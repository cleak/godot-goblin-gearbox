"""The headless GDScript tools, run for real against small projects in a temp folder.

Each tool must pass a clean project (exit 0) and fail a seeded defect (non-zero, with a
message that names the file and the cause). Needs the Godot 4.7 binary the kit's resolver
finds (tools/godot_resolver.ps1); without one the module is skipped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import _gate_fixtures
import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL_FILES = (
    "kit_config.gd",
    "error_capture.gd",
    "headless_tool.gd",
    "lint_project.gd",
    "lint_shaders.gd",
    "validate_all.gd",
    "add_input_action.gd",
)


def _kit_debug_section() -> str:
    """The strict [debug] warning block of the kit's own project.godot."""
    text = (REPO / "project.godot").read_text(encoding="utf-8")
    section = re.search(r"^\[debug\]\n.*?(?=^\[|\Z)", text, re.M | re.S)
    assert section is not None, "project.godot has no [debug] section"
    return section[0].rstrip("\n") + "\n"


# The fixture projects run under the warnings the kit ships, so a tool that only
# compiles under looser ones fails here.
DEBUG_SECTION = _kit_debug_section()

PROJECT_GODOT = f"""; Engine configuration file.
; It's best edited using the editor UI and not directly,
; since the parameters that go here are not all obvious.
;
; Format:
;   [section] ; section goes between []
;   param=value ; assign values to parameters

config_version=5

[application]

config/name="Kit tool fixture"
config/description="Line one
[not a section]
line three"
config/features=PackedStringArray("4.7", "Forward Plus")

[autoload]

Game="*res://scripts/game.gd"

{DEBUG_SECTION}
[rendering]

textures/canvas_textures/default_texture_filter=0
"""

UID_MAIN = "uid://bmainscene01"
UID_CHILD = "uid://bchildscene1"
UID_BOSS = "uid://bbossscene01"
UID_PLAYER = "uid://bplayerscr01"
UID_HUD = "uid://bhudscript01"
UID_GAME = "uid://bgamescript1"
UID_CONFIG = "uid://bconfigres01"

PLAYER_GD = """extends Node2D

@export var target_path: NodePath
@export var speed: float = 120.0


func heading() -> Vector2:
\treturn Vector2.RIGHT * speed * float(Game.score)
"""

HUD_GD = """extends Node

@export var label_path: NodePath
@export var unset_path: NodePath
@export var watch: Array[NodePath] = []
@export var save_path: String = ""
@export var data_path: String = ""
"""

MAIN_TSCN = f"""[gd_scene load_steps=4 format=3 uid="{UID_MAIN}"]

[ext_resource type="Script" uid="{UID_PLAYER}" path="res://scripts/player.gd" id="1_player"]
[ext_resource type="PackedScene" uid="{UID_CHILD}" path="res://scenes/child.tscn" id="2_child"]
[ext_resource type="Script" uid="{UID_HUD}" path="res://scripts/hud.gd" id="3_hud"]

[node name="Main" type="Node2D"]

[node name="Player" type="Node2D" parent="."]
script = ExtResource("1_player")
target_path = NodePath("../Enemy/Sprite")

[node name="Enemy" parent="." instance=ExtResource("2_child")]

[node name="Sprite" parent="Enemy" index="0"]
position = Vector2(3, 4)

[node name="Label" type="Label" parent="."]
unique_name_in_owner = true

[node name="Lazy" parent="." instance_placeholder="res://scenes/child.tscn"]

[node name="Mesh" type="MeshInstance3D" parent="."]
skeleton = NodePath("")

[node name="Hud" type="Node" parent="."]
script = ExtResource("3_hud")
label_path = NodePath("%Label")
unset_path = NodePath("")
watch = Array[NodePath]([NodePath("../Player"), NodePath("../Enemy/Sprite:position"), \
NodePath("../Enemy/%Muzzle"), NodePath("../Enemy/Sprite/%Muzzle"), NodePath("../Lazy/Sprite")])
save_path = "saves/slot1"
data_path = "levels/one.json"

[node name="Dialog" type="FileDialog" parent="."]
current_path = "saves/slot1"
"""

CHILD_TSCN = f"""[gd_scene format=3 uid="{UID_CHILD}"]

[node name="Child" type="Node2D"]

[node name="Sprite" type="Node2D" parent="."]

[node name="Muzzle" type="Node2D" parent="Sprite"]
unique_name_in_owner = true
"""

# Inherits child.tscn: paths into the base scene's nodes must resolve, also by unique name.
BOSS_TSCN = f"""[gd_scene load_steps=2 format=3 uid="{UID_BOSS}"]

[ext_resource type="PackedScene" uid="{UID_CHILD}" path="child.tscn" id="1_base"]

[node name="Boss" instance=ExtResource("1_base")]

[node name="Aim" type="RemoteTransform2D" parent="."]
remote_path = NodePath("../Sprite")

[node name="Fire" type="RemoteTransform2D" parent="."]
remote_path = NodePath("%Muzzle")
"""

CONFIG_TRES = f"""[gd_resource type="Resource" format=3 uid="{UID_CONFIG}"]

[resource]
"""

# A scene written by hand: valid for Godot, but its header carries no uid.
HAND_WRITTEN_TSCN = '[gd_scene format=3]\n\n[node name="A" type="Node"]\n'

# `@export var x: Node` is stored as a NodePath too.
AIMER_GD = "extends Node\n\n@export var target: Node\n@export var others: Array[Node] = []\n"
AIM_TSCN = """[gd_scene load_steps=2 format=3 uid="uid://baimscene001"]

[ext_resource type="Script" uid="uid://baimerscr001" path="res://scripts/aimer.gd" id="1"]

[node name="Root" type="Node"]

[node name="Good" type="Node" parent="." node_paths=PackedStringArray("target", "others")]
script = ExtResource("1")
target = NodePath("../Bad")
others = [NodePath(".."), NodePath("../Bad")]

[node name="Bad" type="Node" parent="." node_paths=PackedStringArray("target", "others")]
script = ExtResource("1")
target = NodePath("../Gone")
others = [NodePath("../Good"), NodePath("../AlsoGone")]
"""

COMMON_INCLUDE = "float lib_half(float lib_value) {\n\treturn lib_value * 0.5;\n}\n"
TINT_SHADER = """shader_type canvas_item;
#include "lib/common.gdshaderinc"

uniform vec4 tint : source_color = vec4(1.0);

void fragment() {
\tCOLOR = texture(TEXTURE, UV) * tint * lib_half(2.0);
}
"""
SPATIAL_SHADER = """shader_type spatial;
#include "res://shaders/lib/common.gdshaderinc"

void fragment() {
\tALBEDO = vec3(lib_half(1.0));
}
"""


@pytest.fixture(scope="session")
def godot() -> Path:
    """The Godot the kit's own resolver finds: one rule for the tools and their tests, so
    these run wherever `pwsh ./tools/setup.ps1` reports a Godot."""
    binary = _gate_fixtures.real_godot()
    if binary is None:
        pytest.skip(
            "no Godot resolves here: set GODOT_BIN, or pwsh ./tools/setup.ps1 -InstallGodot"
        )
    return Path(binary)


class Project:
    """A throwaway Godot project holding copies of the kit's tools."""

    def __init__(self, root: Path, godot: Path) -> None:
        self.root = root / "project"
        self.godot = godot
        # Godot's user data (logs, caches) goes to the temp folder, not the real profile:
        # by the variable this system's Godot reads, and on Linux its two other folders.
        self.env = os.environ | _gate_fixtures.user_data_env(root / "userdata")
        for variable in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            self.env[variable] = str(root / "userdata")
        self.write("project.godot", PROJECT_GODOT)
        self.write("scripts/game.gd", "extends Node\n\nvar score: int = 3\n", UID_GAME)
        for name in TOOL_FILES:
            for suffix in ("", ".uid"):
                source = REPO / "tools" / (name + suffix)
                target = self.root / "tools" / (name + suffix)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

    def path(self, relative: str) -> Path:
        return self.root / relative

    def write(self, relative: str, text: str, uid: str = "") -> None:
        """Write a file with LF endings, plus its .uid sidecar when `uid` is given."""
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        if uid:
            target.with_name(target.name + ".uid").write_bytes(f"{uid}\n".encode())

    def read(self, relative: str) -> str:
        return self.path(relative).read_bytes().decode("utf-8")

    def seed(self, relative: str, after: str, code: str) -> None:
        """Insert `code` into a script, after the one line that matches the regex `after`."""
        # gdformat writes CRLF on Windows; a working copy may hold either ending.
        source = self.read(relative).replace("\r\n", "\n")
        found = re.findall(rf"^{after}\n", source, re.M)
        assert len(found) == 1, f"{relative}: {after!r} matched {len(found)} times"
        self.write(relative, source.replace(found[0], found[0] + code))

    def run(
        self, script: str, *args: str, engine_args: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess[str]:
        command = [str(self.godot), "--headless", *engine_args, "--path", str(self.root)]
        command += ["--script", f"res://{script}"]
        if args:
            command += ["--", *args]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            timeout=120,
            check=False,
        )

    def tool(self, name: str, *args: str) -> tuple[int, str]:
        """Run tools/<name>.gd; returns the exit code and stdout + stderr."""
        result = self.run(f"tools/{name}.gd", *args)
        return result.returncode, result.stdout + result.stderr

    def import_project(self) -> None:
        """What `godot.ps1 --headless --import` does: build the .godot caches."""
        result = subprocess.run(
            [str(self.godot), "--headless", "--path", str(self.root), "--import"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def add_game(self) -> None:
        """Scenes, scripts and a resource that every tool accepts."""
        self.write("scripts/player.gd", PLAYER_GD, UID_PLAYER)
        self.write("scripts/hud.gd", HUD_GD, UID_HUD)
        self.write("scenes/main.tscn", MAIN_TSCN)
        self.write("scenes/child.tscn", CHILD_TSCN)
        self.write("scenes/boss.tscn", BOSS_TSCN)
        self.write("data/config.tres", CONFIG_TRES)

    def add_shaders(self) -> None:
        self.write("shaders/lib/common.gdshaderinc", COMMON_INCLUDE, "uid://bcommoninc01")
        self.write("shaders/tint.gdshader", TINT_SHADER, "uid://btintshader1")
        self.write("shaders/solid.gdshader", SPATIAL_SHADER, "uid://bsolidshader")


@pytest.fixture
def project(tmp_path: Path, godot: Path) -> Project:
    return Project(tmp_path, godot)


def test_godot_keeps_its_user_data_in_the_temp_folder(project: Project, tmp_path: Path) -> None:
    """Every Godot these tests start writes its logs below the test's own folder, never
    among the developer's: the variable that moves them is this system's."""
    project.add_game()
    code, out = project.tool("validate_all")
    assert code == 0, out
    base = Path(_gate_fixtures.godot_data_root(project.env))
    assert tmp_path / "userdata" in (base, *base.parents)
    # "Godot" on Windows and macOS, "godot" on Linux.
    kept = [folder.name for folder in base.glob("[Gg]odot/app_userdata/*")]
    assert kept == ["Kit tool fixture"], kept


# --- lint_project.gd ---


def test_lint_project_passes_a_clean_project(project: Project) -> None:
    project.add_game()
    project.add_shaders()
    # Third-party folders are not authored source: their defects are not this gate's.
    project.write("addons/vendor/loose.tscn", HAND_WRITTEN_TSCN)
    code, out = project.tool("lint_project")
    assert code == 0, out
    assert "UIDs: OK (4 scenes and resources, 13 scripts and shaders)" in out
    # Not warned about: an empty NodePath, a String that holds a folder, a path into
    # an InstancePlaceholder; resolved: unique names of instanced and inherited scenes.
    assert "Scene NodePaths: OK (3 scenes)" in out
    assert "LINT_PROJECT_OK errors=0 warnings=0" in out
    # The verdict comes from the files; a missing .godot cache is only a note.
    assert "note: Godot's uid cache lacks 17 file(s)" in out

    project.import_project()
    code, out = project.tool("lint_project")
    assert code == 0, out
    assert "LINT_PROJECT_OK errors=0 warnings=0" in out
    assert "note:" not in out and "invalid UID" not in out


def test_lint_project_reports_each_uid_defect_with_its_fix(project: Project) -> None:
    project.add_game()
    project.write("scenes/no_uid.tscn", HAND_WRITTEN_TSCN)
    project.write("scenes/main.tscn", MAIN_TSCN.replace(UID_PLAYER, "uid://bstale000001"))
    project.write(
        "scenes/missing_ref.tscn",
        '[gd_scene load_steps=2 format=3 uid="uid://bmissingref1"]\n\n'
        '[ext_resource type="Script" path="res://scripts/gone.gd" id="1"]\n\n'
        '[node name="A" type="Node"]\n',
    )
    project.write(
        "scenes/moved_ref.tscn",
        '[gd_scene load_steps=2 format=3 uid="uid://bmovedref001"]\n\n'
        f'[ext_resource type="Script" uid="{UID_HUD}" path="res://old/hud.gd" id="1"]\n\n'
        '[node name="A" type="Node"]\nscript = ExtResource("1")\n',
    )
    project.write("scenes/copy.tscn", CHILD_TSCN)
    project.write("scripts/no_sidecar.gd", "extends RefCounted\n")
    project.write("scripts/bad_sidecar.gd", "extends RefCounted\n", "not a uid")
    project.write("scripts/orphan.gd.uid", "uid://borphan00001\n")

    code, out = project.tool("lint_project", "--uids-only")
    assert code == 1, out
    for expected in (
        "res://scenes/no_uid.tscn:1: the header has no uid",
        "Fix: run this tool with --fix, or save the file from the editor.",
        "res://scenes/main.tscn:3: ext_resource has uid uid://bstale000001, "
        f"but res://scripts/player.gd is {UID_PLAYER}.",
        f'Fix: set uid="{UID_PLAYER}", or run with --fix.',
        "res://scenes/missing_ref.tscn:3: ext_resource res://scripts/gone.gd does not exist.",
        "res://scenes/moved_ref.tscn:3: ext_resource res://old/hud.gd does not exist; "
        f"{UID_HUD} is now res://scripts/hud.gd.",
        f"res://scenes/copy.tscn:1: shares {UID_CHILD} with res://scenes/child.tscn.",
        "res://scripts/no_sidecar.gd: has no no_sidecar.gd.uid sidecar. "
        "Fix: run: pwsh ./tools/godot.ps1 --headless --import",
        'res://scripts/bad_sidecar.gd.uid: holds "not a uid", which is not a valid uid.',
        "res://scripts/orphan.gd.uid: its source file orphan.gd is gone.",
        "UIDs: 8 problem(s)",
        # What "--fix" in the lines above means, as the whole command.
        "--fix:  pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd "
        "--user-args --fix\n",
        "LINT_PROJECT_FAILED errors=8 warnings=0",
    ):
        assert expected in out, f"missing: {expected}\n{out}"
    # Findings come sorted by file, whichever check found them.
    reported = re.findall(r"^(res://\S+?)(?::\d+)?: ", out, re.M)
    assert reported == sorted(reported) and len(reported) == 8

    # The scene pass reports a scene Godot cannot load, with Godot's reason.
    scene = "scenes/missing_ref.tscn"
    code, out = project.tool("lint_project", "--warnings-only", "--scene", scene)
    assert code == 1, out
    assert re.search(r"missing_ref\.tscn: does not load: .*res://scripts/gone\.gd", out), out
    assert "LINT_PROJECT_FAILED errors=1 warnings=0" in out
    # A project without a .godot folder: the likely cause is named, with the command.
    assert "note: this project was never imported" in out
    assert "Run: pwsh ./tools/godot.ps1 --headless --import" in out


def _png(width: int = 1, height: int = 1) -> bytes:
    """A valid white RGB PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + b"\xff" * 3 * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def test_lint_project_reads_the_uid_of_an_imported_asset(project: Project) -> None:
    # A texture keeps its uid in the .import file that an import writes next to it.
    project.path("art").mkdir()
    project.path("art/dot.png").write_bytes(_png())
    scene = (
        '[gd_scene load_steps=2 format=3 uid="uid://bspritescn01"]\n\n'
        '[ext_resource type="Texture2D" uid="uid://bstale000001" path="res://art/dot.png" id="1"]'
        "\n\n"
        '[node name="Dot" type="Sprite2D"]\ntexture = ExtResource("1")\n'
    )
    project.write("scenes/sprite.tscn", scene)
    # Never imported: the texture has no uid yet and Godot cannot load it.
    code, out = project.tool("lint_project")
    assert code == 1, out
    assert "ext_resource has uid uid://bstale000001, but res://art/dot.png has no uid." in out
    assert "res://scenes/sprite.tscn: does not load:" in out
    assert "note: this project was never imported" in out

    project.import_project()
    texture_uid = re.search(r'uid="(uid://\w+)"', project.read("art/dot.png.import"))
    assert texture_uid is not None
    code, out = project.tool("lint_project")
    assert code == 1, out
    stale = f"ext_resource has uid uid://bstale000001, but res://art/dot.png is {texture_uid[1]}."
    assert f"res://scenes/sprite.tscn:3: {stale}" in out
    assert "never imported" not in out

    project.write("scenes/sprite.tscn", scene.replace("uid://bstale000001", texture_uid[1]))
    code, out = project.tool("lint_project")
    assert code == 0, out
    assert "LINT_PROJECT_OK errors=0 warnings=0" in out

    # The uid comes from the .import file, not from Godot's cache.
    shutil.rmtree(project.path(".godot"))
    code, out = project.tool("lint_project", "--uids-only")
    assert code == 0, out


def test_lint_project_fix_repairs_uids_and_touches_nothing_else(project: Project) -> None:
    project.add_game()
    body = '\r\n\r\n[node name="A" type="Node"]\r\n\r\n[node name="B" type="Node" parent="."]\r\n'
    project.write("scenes/hand_written.tscn", "[gd_scene format=3]" + body)
    stale = MAIN_TSCN.replace(f' uid="{UID_PLAYER}"', "").replace(UID_CHILD, "uid://bstale000001")
    stale = stale.replace("res://scripts/hud.gd", "res://old/hud.gd")
    project.write("scenes/main.tscn", stale)
    # A scene that points at the scene that gets its uid in the same run.
    project.write(
        "scenes/user.tscn",
        '[gd_scene load_steps=2 format=3 uid="uid://buserscene01"]\n\n'
        '[ext_resource type="PackedScene" path="res://scenes/hand_written.tscn" id="1"]\n\n'
        '[node name="Root" type="Node"]\n\n'
        '[node name="A" parent="." instance=ExtResource("1")]\n',
    )
    # Needs both repairs: a header uid, and the uid of the scene it instances.
    project.write(
        "scenes/both.tscn",
        "[gd_scene load_steps=2 format=3]\n\n"
        '[ext_resource type="PackedScene" path="res://scenes/child.tscn" id="1"]\n\n'
        '[node name="Root" type="Node"]\n\n'
        '[node name="A" parent="." instance=ExtResource("1")]\n',
    )
    assert project.tool("lint_project", "--uids-only")[0] == 1

    code, out = project.tool("lint_project", "--fix")
    assert code == 0, out
    for fixed in ("both", "hand_written", "main", "user"):
        assert out.count(f"fixed res://scenes/{fixed}.tscn\n") == 1, out
    assert "LINT_PROJECT_OK errors=0 warnings=0" in out
    assert f'uid="{UID_CHILD}" path="res://scenes/child.tscn"' in project.read("scenes/both.tscn")

    hand_written = project.read("scenes/hand_written.tscn")
    header = re.fullmatch(r'\[gd_scene format=3 uid="(uid://[a-y0-8]+)"\](.*)', hand_written, re.S)
    assert header is not None, hand_written
    assert header[2] == body  # CRLF endings and every other byte survive
    assert project.read("scenes/main.tscn") == MAIN_TSCN
    assert f'[ext_resource type="PackedScene" uid="{header[1]}" path=' in project.read(
        "scenes/user.tscn"
    )

    code, out = project.tool("lint_project")
    assert code == 0, out
    assert "fixed" not in out


def test_lint_project_fix_leaves_files_it_cannot_rewrite_exactly(project: Project) -> None:
    # Reading text and writing it back drops a byte order mark and replaces invalid
    # bytes: such a file is reported, not rewritten.
    latin1 = b'[gd_scene format=3]\n\n[node name="A" type="Label"]\ntext = "caf\xe9"\n'
    bom = b'\xef\xbb\xbf[gd_scene format=3]\n\n[node name="A" type="Node"]\n'
    project.path("scenes").mkdir()
    project.path("scenes/latin1.tscn").write_bytes(latin1)
    project.path("scenes/bom.tscn").write_bytes(bom)
    project.write("scenes/plain.tscn", HAND_WRITTEN_TSCN)

    code, out = project.tool("lint_project", "--fix", "--uids-only")
    assert code == 1, out
    assert project.path("scenes/latin1.tscn").read_bytes() == latin1
    assert project.path("scenes/bom.tscn").read_bytes() == bom
    for name in ("latin1", "bom"):
        assert f"res://scenes/{name}.tscn: is not plain UTF-8" in out
        assert f"fixed res://scenes/{name}.tscn" not in out
    assert "--fix leaves it alone. Fix: re-save it as UTF-8." in out
    assert "fixed res://scenes/plain.tscn" in out

    # With a uid in its header, Godot and this tool both accept the file with the mark.
    project.path("scenes/latin1.tscn").unlink()
    project.path("scenes/bom.tscn").write_bytes(bom.replace(b"]", b' uid="uid://bbomscene001"]', 1))
    code, out = project.tool("lint_project")
    assert code == 0, out


def test_lint_project_node_path_warnings_fail_only_on_request(project: Project) -> None:
    project.add_game()
    project.write(
        "scenes/dangling.tscn",
        '[gd_scene load_steps=3 format=3 uid="uid://bdangling001"]\n\n'
        f'[ext_resource type="Script" uid="{UID_HUD}" path="res://scripts/hud.gd" id="1"]\n'
        f'[ext_resource type="PackedScene" uid="{UID_CHILD}" path="res://scenes/child.tscn"'
        ' id="2"]\n\n'
        '[node name="Root" type="Node2D"]\n\n'
        '[node name="Enemy" parent="." instance=ExtResource("2")]\n\n'
        '[node name="Aim" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("../Nope")\n\n'
        '[node name="Outside" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("../../Sibling")\n\n'
        '[node name="Absolute" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("/root/Elsewhere")\n\n'
        '[node name="Unset" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("")\n\n'
        '[node name="Named" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("%Missing")\n\n'
        '[node name="Inner" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("%Muzzle")\n\n'
        '[node name="Wrong" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("../Enemy/%Barrel")\n\n'
        '[node name="Spawned" type="RemoteTransform2D" parent="."]\n'
        'remote_path = NodePath("../MadeAtRunTime")\n'
        "metadata/lint_ignore_node_paths = true\n\n"
        '[node name="Texty" type="Node" parent="."]\n'
        'script = ExtResource("1")\n'
        'save_path = "user://saves"\n'
        'data_path = "../Lost"\n',
    )
    code, out = project.tool("lint_project")
    assert code == 0, out
    aim = "res://scenes/dangling.tscn: warning: node Aim: remote_path = ../Nope: no node at Nope"
    assert aim in out
    assert "node Named: remote_path = %Missing: no node of this scene has that unique name" in out
    # A unique name belongs to the scene that owns the node: Muzzle is Enemy's, not Root's.
    assert "node Inner: remote_path = %Muzzle: no node of this scene has that unique name" in out
    assert "node Wrong: remote_path = ../Enemy/%Barrel: no node of this scene has" in out
    # Not judged: a path that leaves the scene root, an absolute one, one that is not
    # set, a String (whatever its property is called), and a node that opts out.
    for silent in ("Outside", "Absolute", "Unset", "Texty", "Spawned"):
        assert f"node {silent}" not in out
    assert "Scene NodePaths: 0 error(s), 4 warning(s) in 4 scenes" in out
    assert "LINT_PROJECT_OK errors=0 warnings=4" in out

    code, out = project.tool("lint_project", "--fail-on-warn")
    assert code == 1, out
    assert "LINT_PROJECT_FAILED errors=0 warnings=4" in out

    code, out = project.tool("lint_project", "--scene", "scenes/main.tscn", "--fail-on-warn")
    assert code == 0, out
    assert "Scene NodePaths: OK (1 scenes)" in out


def test_lint_project_compares_uids_by_value(project: Project) -> None:
    project.add_game()
    # A leading "a" is a leading zero: the same id as UID_HUD, spelled differently.
    project.write("scripts/hud.gd.uid", UID_HUD.replace("uid://", "uid://a") + "\n")
    code, out = project.tool("lint_project", "--uids-only")
    assert code == 0, out


def test_lint_project_checks_node_typed_exports(project: Project) -> None:
    project.write("scripts/aimer.gd", AIMER_GD, "uid://baimerscr001")
    project.write("scenes/aim.tscn", AIM_TSCN)
    code, out = project.tool("lint_project", "--fail-on-warn")
    assert code == 1, out
    assert "node Bad: target = ../Gone: no node at Gone in this scene." in out
    assert "node Bad: others = ../AlsoGone: no node at AlsoGone in this scene." in out
    assert "node Good" not in out
    assert "LINT_PROJECT_FAILED errors=0 warnings=2" in out


def test_gates_fail_when_nothing_is_authored(project: Project) -> None:
    project.write("kit.json", '{"source": {"exclude": ["tools", "scripts"]}}')
    code, out = project.tool("lint_project")
    assert code == 1, out
    assert "no authored scenes, resources or scripts found" in out
    code, out = project.tool("validate_all")
    assert code == 1, out
    assert "no authored gd/tscn/tres/scn/res files found" in out


def test_lint_project_json_and_scope(project: Project) -> None:
    project.add_game()
    project.write("addons/mine/loose.tscn", HAND_WRITTEN_TSCN)
    project.write("kit.json", '{"source": {"exclude": ["addons"], "include": ["addons/mine"]}}')
    # An autoload that prints: the report must still be found on its own marked line.
    chatty = (
        'extends Node\n\nvar score: int = 3\n\n\nfunc _init() -> void:\n\tprint("Game booting {")\n'
    )
    project.write("scripts/game.gd", chatty, UID_GAME)
    result = project.run("tools/lint_project.gd", "--json")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Game booting {" in result.stdout
    marked = [line for line in result.stdout.splitlines() if line.startswith("LINT_PROJECT_JSON ")]
    assert len(marked) == 1, result.stdout
    report = json.loads(marked[0].removeprefix("LINT_PROJECT_JSON "))
    assert report["ok"] is False
    assert [error["file"] for error in report["uid_errors"]] == ["res://addons/mine/loose.tscn"]
    assert report["uid_errors"][0]["line"] == 1
    assert report["scene_errors"] == [] and report["scene_warnings"] == []
    assert report["fixed"] == [] and len(report["not_in_uid_cache"]) == 14
    assert any("uid cache lacks 14 file(s)" in note for note in report["notes"])
    assert "LINT_PROJECT_FAILED" not in result.stdout


# --- every tool: a broken tool or a broken kit.json is never a pass ---

# The texts are tools/kit_config.py's, which tools/kit_config.gd repeats word for word
# (tools/tests/test_kit_config_parity.py holds the three loaders to that).
BROKEN_KIT_JSON = {
    "cut short": (
        '{"source": {"exclude": ["addons"], "include": ["addons/mine"]}',
        "kit.json is not valid JSON: ",
    ),
    "empty": ("", "kit.json is not valid JSON: "),
    "a trailing comma": ('{"source": {"exclude": ["addons"],}}', "kit.json is not valid JSON: "),
    "a list": ('["not", "an", "object"]', "kit.json must hold a JSON object ("),
    "source is a string": ('{"source": "scripts"}', 'kit.json: "source" must be an object'),
    "strings for lists": (
        '{"source": {"exclude": "addons", "include": "addons/mine"}}',
        'kit.json: "source.exclude" must be a list of strings',
    ),
    "a number in a list": (
        '{"source": {"exclude": ["addons", 3]}}',
        'kit.json: "source.exclude" must be a list of strings',
    ),
    "include typo": (
        '{"source": {"exclude": ["addons"], "include": ["addons/typo"]}}',
        'kit.json: "source.include" names a folder that does not exist: addons/typo',
    ),
}


def test_gates_stop_on_a_broken_kit_json(project: Project) -> None:
    # Falling back to the default scope would silently drop source.include from the
    # check: here, the only broken files of the project.
    project.add_game()
    project.write("addons/mine/bad.gd", 'extends RefCounted\n\nvar count: int = "three"\n')
    project.write("addons/mine/bad.gdshader", "shader_type spatial;\nvoid fragment() { x }\n")
    project.write("addons/mine/bad.tscn", HAND_WRITTEN_TSCN)
    project.write("kit.json", '{"source": {"exclude": ["addons"], "include": ["addons/mine"]}}')
    gates = ("validate_all", "lint_shaders", "lint_project")
    for gate in gates:
        code, out = project.tool(gate)
        assert code == 1, out
        assert "res://addons/mine/bad." in out

    for name, (text, message) in BROKEN_KIT_JSON.items():
        project.write("kit.json", text)
        # Every gate on the first case, one gate on the rest: they share the check.
        for gate in gates if name == "cut short" else ("validate_all",):
            code, out = project.tool(gate)
            assert code == 2, f"{name}: {out}"
            assert f"{gate}: {message}" in out, f"{name}: {out}"
            assert "_OK" not in out, f"{name}: {out}"


TOOLS_WITH_A_VERDICT = (
    "lint_project",
    "lint_shaders",
    "validate_all",
    "add_input_action",
)
SCRIPT_ERROR = '\tvar kit_seeded: Dictionary = {}\n\tprint(kit_seeded["missing"])\n'


@pytest.mark.parametrize("name", TOOLS_WITH_A_VERDICT)
def test_a_script_error_in_a_tool_is_never_a_pass(project: Project, name: str) -> None:
    # GDScript carries on after a script error; the tool must still end non-zero.
    project.add_game()
    tool = f"tools/{name}.gd"
    project.seed(tool, r"func _main\(.*\) -> Verdict:", SCRIPT_ERROR)
    before = project.read("project.godot")
    code, out = project.tool(name)
    assert code == 3, out
    assert f"TOOL_ERROR {name}: 1 script error(s) in the tool itself" in out
    assert f"(res://{tool}:" in out
    assert "_OK" not in out
    assert project.read("project.godot") == before


def test_a_script_error_deep_inside_a_tool_voids_its_ok_line(project: Project) -> None:
    # The failing function returns early and its callers carry on: without the guard
    # this run prints LINT_PROJECT_OK and exits 0 with the UID check skipped.
    project.write("scenes/no_uid.tscn", HAND_WRITTEN_TSCN)
    tool = "tools/lint_project.gd"
    intact = project.read(tool)
    project.seed(tool, r"\t_uid_by_path\.clear\(\)", SCRIPT_ERROR)  # the top of _index_uids
    result = project.run(tool, "--uids-only")
    assert result.returncode == 3, result.stdout + result.stderr
    verdict = "TOOL_ERROR lint_project: 1 script error(s) in the tool itself; ignore its verdict."
    assert verdict in result.stdout
    # It is the last word: after any OK line the broken run printed.
    assert result.stdout.index(verdict) > result.stdout.rfind("LINT_PROJECT_")
    assert "_index_uids" in result.stderr  # Godot's own report names the function

    # An error in a helper the tool preloads counts as the tool's own too.
    project.write(tool, intact)
    project.seed("tools/kit_config.gd", r"static func source_files\(.*\) -> .*:", SCRIPT_ERROR)
    code, out = project.tool("lint_project", "--uids-only")
    assert code == 3, out
    assert "(res://tools/kit_config.gd:" in out


def test_a_tool_that_breaks_before_its_verdict_still_exits(project: Project) -> None:
    # An error in _initialize leaves nothing to call quit: the run must not idle forever.
    project.seed("tools/headless_tool.gd", r"func _initialize\(\) -> void:", SCRIPT_ERROR)
    code, out = project.tool("validate_all")
    assert code == 3, out
    assert "TOOL_ERROR validate_all: stopped before it could report." in out


def test_an_autoload_that_quits_does_not_replace_the_tools_exit_code(project: Project) -> None:
    # The project's autoloads start after the tool's _initialize. One that called quit()
    # set the exit code last: 0, after the tool had printed FAILED and asked for 1.
    project.write(
        "scripts/game.gd", "extends Node\n\n\nfunc _ready() -> void:\n\tget_tree().quit()\n"
    )
    code, out = project.tool("lint_project", "--uids-only")
    assert code == 0 and "LINT_PROJECT_OK" in out, out
    project.write("scenes/no_uid.tscn", HAND_WRITTEN_TSCN)
    code, out = project.tool("lint_project", "--uids-only")
    assert code == 1, out
    assert "LINT_PROJECT_FAILED" in out
    project.seed("tools/lint_project.gd", r"func _main\(.*\) -> Verdict:", SCRIPT_ERROR)
    code, out = project.tool("lint_project", "--uids-only")
    assert code == 3 and "TOOL_ERROR lint_project" in out, out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--scene",), "--scene needs a scene path"),
        (("--scene", "scenes/absent.tscn"), "scene not found: res://scenes/absent.tscn"),
        (("--uids-only", "--warnings-only"), "--uids-only and --warnings-only cannot be combined"),
        (("--fix", "--warnings-only"), "--fix and --warnings-only cannot be combined"),
        (("--everything",), "unknown argument: --everything"),
    ],
)
def test_lint_project_rejects_bad_arguments(
    project: Project, args: tuple[str, ...], message: str
) -> None:
    code, out = project.tool("lint_project", *args)
    assert code == 2, out
    assert message in out
    assert "usage:" in out


# --- a path argument never leaves the project ---

# Ways to name a file that sits in the folder above the project. `{root}` is the project
# folder and `{above}` the one that holds it, both absolute.
OUTSIDE_FORMS = (
    "../{name}",
    "..\\{name}",
    "res://../{name}",
    "scripts/../../{name}",
    "res://scripts\\..\\../{name}",
    "{root}/../{name}",
    "{above}/{name}",
)


def _outside(project: Project, form: str, name: str) -> str:
    return form.format(name=name, root=project.root, above=project.root.parent)


def _marking_scene(marker: Path) -> str:
    """A scene whose built-in script writes `marker` as soon as Godot loads the scene."""
    target = marker.as_posix()
    return (
        '[gd_scene load_steps=2 format=3]\n\n[sub_resource type="GDScript" id="1"]\n'
        'script/source="extends Node\n\n\nstatic func _static_init() -> void:\n'
        f'\tvar file: FileAccess = FileAccess.open(\\"{target}\\", FileAccess.WRITE)\n'
        '\tfile.store_string(\\"loaded\\")\n"\n\n'
        '[node name="Marks" type="Node"]\nscript = SubResource("1")\n'
    )


@pytest.mark.parametrize("form", OUTSIDE_FORMS)
def test_lint_project_loads_no_scene_from_outside_the_project(project: Project, form: str) -> None:
    """`--user-args *` is pre-approved for agents: the scene after it must not be able to
    run a script the project does not hold."""
    marker = project.root.parent / "loaded.txt"
    (project.root.parent / "outside.tscn").write_bytes(_marking_scene(marker).encode())
    arg = _outside(project, form, "outside.tscn")
    code, out = project.tool("lint_project", "--warnings-only", "--scene", arg)
    assert code == 2, out
    assert f"lint_project: scene is outside the project: {arg}" in out
    assert "Scene NodePaths" not in out
    assert not marker.exists()


def test_lint_project_loads_a_scene_inside_the_project_however_it_is_written(
    project: Project,
) -> None:
    # Also the control of the test above: loading this scene does leave the marker.
    marker = project.root.parent / "loaded.txt"
    project.write("scenes/inside.tscn", _marking_scene(marker))
    forms = (
        "scenes/inside.tscn",
        "scripts/../scenes/inside.tscn",
        "res://scenes\\inside.tscn",
        str(project.path("scenes/inside.tscn")),
        f"{project.root}/../project/scenes/inside.tscn",
    )
    scenes = [part for form in forms for part in ("--scene", form)]
    code, out = project.tool("lint_project", "--warnings-only", *scenes)
    assert code == 0, out
    assert f"Scene NodePaths: OK ({len(forms)} scenes)" in out
    assert marker.exists()


# --- lint_shaders.gd ---


def test_lint_shaders_passes_clean_shaders_and_lists_unchecked_includes(project: Project) -> None:
    project.add_shaders()
    project.write("shaders/lib/unused.gdshaderinc", COMMON_INCLUDE, "uid://bunusedinc01")
    code, out = project.tool("lint_shaders")
    assert code == 0, out
    assert "not compiled for a GPU" in out
    assert "ok   res://shaders/solid.gdshader" in out
    assert "ok   res://shaders/tint.gdshader" in out
    assert "note res://shaders/lib/unused.gdshaderinc: no checked shader includes it" in out
    assert "common.gdshaderinc: no checked" not in out
    assert "LINT_SHADERS_OK checked=2" in out


def test_lint_shaders_passes_what_godot_never_reads_and_known_globals(project: Project) -> None:
    # An #include in a comment or in an inactive #ifdef block names no file Godot
    # opens; a missing one there is not a defect.
    project.add_shaders()
    project.write(
        "shaders/quiet.gdshader",
        "shader_type canvas_item;\n"
        '// #include "lib/old_line.gdshaderinc"\n'
        '/* shader_type spatial;\n#include "lib/old_block.gdshaderinc"\n*/\n'
        '#ifdef NEVER_DEFINED\n#include "lib/never.gdshaderinc"\n#endif\n'
        '#include "res://shaders/lib/common.gdshaderinc" // a comment after it\n'
        "global uniform vec4 wind_tint;\n"
        "global uniform float gust;\n"
        "// global uniform float not_declared;\n"
        "void fragment() {\n\tCOLOR = wind_tint * lib_half(2.0) * gust;\n}\n",
    )
    # A global uniform comes from project.godot, or from a script that adds it.
    globals_section = (
        '\n[shader_globals]\n\nwind_tint={\n"type": "vec4",\n"value": Vector4(1, 1, 1, 1)\n}\n'
    )
    project.write("project.godot", PROJECT_GODOT + globals_section)
    project.write(
        "scripts/weather.gd",
        "extends Node\n\n\nfunc _ready() -> void:\n"
        '\tRenderingServer.global_shader_parameter_add(\n\t\t&"gust", '
        "RenderingServer.GLOBAL_VAR_TYPE_FLOAT, 0.0\n\t)\n",
    )
    code, out = project.tool("lint_shaders")
    assert code == 0, out
    assert "ok   res://shaders/quiet.gdshader" in out
    assert "LINT_SHADERS_OK checked=3" in out


def test_lint_shaders_fails_each_broken_shader(project: Project) -> None:
    project.add_shaders()
    project.write("shaders/syntax.gdshader", TINT_SHADER.replace("lib_half(2.0);", "lib_half(2.0)"))
    project.write("shaders/types.gdshader", SPATIAL_SHADER.replace("vec3(lib_half(1.0))", "1.0"))
    project.write("shaders/include.gdshader", TINT_SHADER.replace("common", "absent"))
    project.write("shaders/untyped.gdshader", "void fragment() {\n}\n")
    # A broken include file fails the shader that pulls it in.
    project.write("shaders/lib/broken.gdshaderinc", "float lib_bad() {\n\treturn 1.0\n}\n")
    project.write(
        "shaders/uses_broken.gdshader",
        'shader_type canvas_item;\n#include "lib/broken.gdshaderinc"\n',
    )
    # An include that includes a file that is not there.
    project.write("shaders/lib/chain.gdshaderinc", '#include "absent_too.gdshaderinc"\n')
    project.write(
        "shaders/nested.gdshader", 'shader_type canvas_item;\n#include "lib/chain.gdshaderinc"\n'
    )
    # A global uniform that project.godot [shader_globals] does not declare.
    project.write(
        "shaders/global.gdshader",
        "shader_type canvas_item;\nglobal uniform vec4 no_such_global;\n"
        "void fragment() {\n\tCOLOR = no_such_global;\n}\n",
    )
    code, out = project.tool("lint_shaders")
    assert code == 1, out
    # Each FAIL line is followed by Godot's reason; its wording is Godot's to change.
    for expected in (
        r"FAIL res://shaders/syntax\.gdshader\n    .+ \(line 8\)\n",
        r"FAIL res://shaders/types\.gdshader\n    .*'vec3, float'",
        r"FAIL res://shaders/include\.gdshader\n"
        r"    #include not found: res://shaders/lib/absent\.gdshaderinc",
        r"FAIL res://shaders/untyped\.gdshader\n    no shader_type line",
        r"FAIL res://shaders/uses_broken\.gdshader\n"
        r"    .+ \(res://shaders/lib/broken\.gdshaderinc:3\)\n",
        r"FAIL res://shaders/nested\.gdshader\n"
        r"    #include not found: res://shaders/lib/absent_too\.gdshaderinc "
        r"\(in res://shaders/lib/chain\.gdshaderinc\)",
        r"FAIL res://shaders/global\.gdshader\n"
        r"    global uniform no_such_global is not declared; add it in Project Settings",
        r"ok   res://shaders/tint\.gdshader\n",
        r"LINT_SHADERS_FAILED failed=7 checked=9\n",
    ):
        assert re.search(expected, out), f"missing: {expected}\n{out}"


def test_lint_shaders_explicit_paths_and_arguments(project: Project) -> None:
    code, out = project.tool("lint_shaders")
    assert code == 0, out
    assert "LINT_SHADERS_OK checked=0 (no .gdshader files in the authored source)" in out

    project.add_shaders()
    project.write("addons/vendor/broken.gdshader", "shader_type spatial;\nvoid fragment() { x }\n")
    assert project.tool("lint_shaders")[0] == 0  # excluded folder
    code, out = project.tool("lint_shaders", "shaders/tint.gdshader")
    assert code == 0, out
    assert "LINT_SHADERS_OK checked=1" in out
    code, out = project.tool("lint_shaders", "res://addons/vendor/broken.gdshader")
    assert code == 1, out  # a path given by name is always checked
    assert "FAIL res://addons/vendor/broken.gdshader" in out

    for arg, message in (
        ("shaders/lib/common.gdshaderinc", "is an include file"),
        ("shaders/absent.gdshader", "file not found: res://shaders/absent.gdshader"),
        ("--strict", "unknown option: --strict"),
    ):
        code, out = project.tool("lint_shaders", arg)
        assert code == 2, out
        assert message in out


@pytest.mark.parametrize("form", OUTSIDE_FORMS)
def test_lint_shaders_reads_no_shader_from_outside_the_project(project: Project, form: str) -> None:
    (project.root.parent / "outside.gdshader").write_bytes(b"shader_type canvas_item;\n")
    arg = _outside(project, form, "outside.gdshader")
    code, out = project.tool("lint_shaders", arg)
    assert code == 2, out
    assert f"lint_shaders: path is outside the project: {arg}" in out
    assert "ok   " not in out and "LINT_SHADERS" not in out


def test_lint_shaders_checks_a_shader_inside_the_project_however_it_is_written(
    project: Project,
) -> None:
    project.add_shaders()
    forms = (
        "scripts/../shaders/tint.gdshader",
        "res://shaders\\tint.gdshader",
        str(project.path("shaders/tint.gdshader")),
        f"{project.root}/../project/shaders/tint.gdshader",
    )
    code, out = project.tool("lint_shaders", *forms)
    assert code == 0, out
    assert out.count("ok   res://shaders/tint.gdshader\n") == len(forms)


# --- validate_all.gd ---


def test_validate_all_loads_a_clean_project(project: Project) -> None:
    project.add_game()
    project.write("addons/vendor/broken.gd", "this is not gdscript\n")
    code, out = project.tool("validate_all")
    assert code == 0, out
    # 6 tool scripts (not the running one, nor the base it extends), 3 game scripts,
    # 3 scenes, 1 resource.
    assert "VALIDATE_ALL_OK loaded=12" in out


def test_validate_all_fails_on_script_and_scene_errors(project: Project) -> None:
    project.add_game()
    project.write("scripts/type_error.gd", 'extends RefCounted\n\nvar count: int = "three"\n')
    project.write("scripts/untyped.gd", "extends RefCounted\n\nvar count = 3\n")
    project.write(
        "scenes/missing_dep.tscn",
        '[gd_scene load_steps=2 format=3 uid="uid://bmissingdep1"]\n\n'
        '[ext_resource type="Script" path="res://scripts/gone.gd" id="1"]\n\n'
        '[node name="A" type="Node"]\nscript = ExtResource("1")\n',
    )
    # Loads before the script it uses (scenes/ sorts first): the script must still
    # fail when its own turn comes.
    project.write(
        "scenes/uses_bad.tscn",
        '[gd_scene load_steps=2 format=3 uid="uid://busesbad0001"]\n\n'
        '[ext_resource type="Script" path="res://scripts/type_error.gd" id="1"]\n\n'
        '[node name="A" type="Node"]\nscript = ExtResource("1")\n',
    )
    # Godot loads a scene with a misspelt node type and only fails on instantiate.
    project.write(
        "scenes/bad_type.tscn",
        '[gd_scene format=3 uid="uid://bbadtype0001"]\n\n'
        '[node name="Root" type="Node"]\n\n'
        '[node name="Hero" type="Bodee2D" parent="."]\n',
    )
    code, out = project.tool("validate_all")
    assert code == 1, out
    # Each FAIL line is followed by Godot's reasons; their wording is Godot's to change.
    for expected in (
        r"FAIL res://scripts/type_error\.gd\n    .+ \(res://scripts/type_error\.gd:3\)\n",
        r'FAIL res://scripts/untyped\.gd\n    .*"count".* \(res://scripts/untyped\.gd:3\)\n',
        r"FAIL res://scenes/missing_dep\.tscn\n(    .*\n)*?    .*res://scripts/gone\.gd",
        r"FAIL res://scenes/uses_bad\.tscn\n    .+ \(res://scripts/type_error\.gd:3\)\n",
        r"FAIL res://scenes/bad_type\.tscn\n    node Hero: there is no node type Bodee2D\n",
        r"VALIDATE_ALL_FAILED failures=5 loaded=12\n",
        r"this project was never imported",
    ):
        assert re.search(expected, out), f"missing: {expected}\n{out}"
    assert out.count("FAIL ") == 5

    code, out = project.tool("validate_all", "scripts")
    assert code == 2, out
    assert "unexpected argument: scripts" in out


# --- add_input_action.gd ---

# Six actions with two keys each, and the physical keycode Godot stores for each key.
# (They are the sample game's, written out here: this test must not depend on which
# actions a game made from the template has in its project.godot.)
SAMPLE_ACTIONS = (
    ("move_left", ("A", 65), ("Left", 4194319)),
    ("move_right", ("D", 68), ("Right", 4194321)),
    ("move_up", ("W", 87), ("Up", 4194320)),
    ("move_down", ("S", 83), ("Down", 4194322)),
    ("interact", ("E", 69), ("Space", 32)),
    ("pause", ("Escape", 4194305), ("P", 80)),
)

# Prints what Godot itself reads from project.godot, and saves its own copy of the settings.
INPUT_PROBE_GD = """extends SceneTree


func _initialize() -> void:
\tprint("EVENTS jump %d" % InputMap.action_get_events("jump").size())
\tProjectSettings.save_custom("res://saved_by_godot.godot")
\tquit(0)
"""


def _key_event(physical_keycode: int, unicode: int = 0) -> str:
    """One key binding, spelled the way the editor writes it into project.godot."""
    return (
        'Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,'
        '"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,'
        '"meta_pressed":false,"pressed":false,"keycode":0,'
        f'"physical_keycode":{physical_keycode},"key_label":0,"unicode":{unicode},'
        '"location":0,"echo":false,"script":null)'
    )


KEY_SPACE, KEY_P, KEY_W, KEY_S, KEY_F10 = 32, 80, 87, 83, 4194341


def _existing_input(opening: str, closing: str) -> str:
    """An [input] section with two of the project's own actions.

    Godot writes the event list as `[...]` (the editor) or `Array[InputEvent]([...])`
    (a script that saved InputMap events); `opening` and `closing` pick the form.
    """
    return f"""[input]

jump={{
"deadzone": 0.5,
"events": {opening}{_key_event(KEY_SPACE, unicode=32)}
{closing}
}}
pause={{
"deadzone": 0.5,
"events": {opening}{_key_event(KEY_F10)}
, {_key_event(KEY_P, unicode=112)}
{closing}
}}
"""


def _section(text: str, name: str) -> str:
    """The text of one project.godot section, from its header to the next header."""
    match = re.search(rf"^\[{name}\]\n.*?(?=^\[\w+\]\n|\Z)", text, re.M | re.S)
    assert match is not None, f"no [{name}] section in:\n{text}"
    return match[0]


def test_input_tool_writes_the_block_the_editor_writes(project: Project) -> None:
    # One run per action gives the [input] section the Godot editor would save, byte for
    # byte, and touches nothing else: a block can always be made again.
    before = project.read("project.godot")
    assert "[input]" not in before
    for action, (first, _), (second, _) in SAMPLE_ACTIONS:
        code, out = project.tool("add_input_action", action, "--key", first, "--key", second)
        assert code == 0, out
        assert f"add     {action} (deadzone 0.2)\n  + key {first}\n  + key {second}\n" in out
        assert f"INPUT_ACTION_OK {action} added=2 kept=0" in out
    written = project.read("project.godot")
    section = _section(written, "input")
    blocks = [
        f'{action}={{\n"deadzone": 0.2,\n"events": [{_key_event(first)}\n'
        f", {_key_event(second)}\n]\n}}\n"
        for action, (_, first), (_, second) in SAMPLE_ACTIONS
    ]
    assert section == "[input]\n\n" + "".join(blocks) + "\n"
    assert written.replace(section, "") == before

    code, out = project.tool("add_input_action", "--list")
    assert code == 0, out
    for action, (first, _), (second, _) in SAMPLE_ACTIONS:
        assert f"{action} --deadzone 0.2 --key {first} --key {second}\n" in out
    assert "INPUT_ACTIONS 6" in out
    assert project.read("project.godot") == written


def test_input_tool_adds_every_kind_of_event_and_only_adds(project: Project) -> None:
    before = project.read("project.godot")
    command = ("jump", "--key", "Space", "--key", "w", "--joy-button", "a")
    command += ("--joy-axis", "left_x:-1", "--mouse-button", "left", "--deadzone", "0.25")
    code, out = project.tool("add_input_action", *command)
    assert code == 0, out
    assert (
        "add     jump (deadzone 0.25)\n  + key Space\n  + key W\n  + joy-button a\n"
        "  + joy-axis left_x:-1\n  + mouse-button left\nINPUT_ACTION_OK jump added=5 kept=0\n"
    ) in out
    assert "Wrote res://project.godot." in out

    after = project.read("project.godot")
    added = _section(after, "input")
    assert after.replace(added, "") == before  # only a new [input] section, nothing else
    assert DEBUG_SECTION in after
    assert after.index("[debug]") < after.index("[input]") < after.index("[rendering]")
    assert added.startswith(
        '[input]\n\njump={\n"deadzone": 0.25,\n"events": [Object(InputEventKey,'
    )
    assert [int(key) for key in re.findall(r'"physical_keycode":(\d+)', added)] == [
        KEY_SPACE,
        KEY_W,
    ]
    assert re.search(r'Object\(InputEventJoypadButton,[^)]*"button_index":0,', added)
    assert "Object(InputEventJoypadMotion," in added and '"axis":0,"axis_value":-1.0,' in added
    assert re.search(r'Object\(InputEventMouseButton,.*"button_index":1,', added)
    # Every event matches any device, as the editor's Input Map writes it; the
    # engine's own default device id differs between Godot versions.
    assert added.count('"device":-1,') == added.count("Object(InputEvent") == 5

    # Idempotent: the same command again changes nothing.
    code, out = project.tool("add_input_action", *command)
    assert code == 0, out
    assert "extend  jump\n  = key Space (already bound)\n  = key W (already bound)\n" in out
    assert "INPUT_ACTION_OK jump added=0 kept=5" in out and "Wrote" not in out
    assert project.read("project.godot") == after

    # Godot reads the section back, and writes the very same text itself.
    project.write("input_probe.gd", INPUT_PROBE_GD)
    result = project.run("input_probe.gd")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EVENTS jump 5" in result.stdout
    assert _section(project.read("saved_by_godot.godot"), "input") == added

    # A built-in action keeps its events when it gains one.
    code, out = project.tool("add_input_action", "ui_cancel", "--key", "Q")
    assert code == 0, out
    assert "extend  ui_cancel\n  + key Q\n" in out
    stored = _section(project.read("project.godot"), "input")
    assert stored.startswith(added.rstrip("\n") + "\nui_cancel={\n")
    built_in = stored[stored.index("ui_cancel={") :]
    assert built_in.count("Object(InputEvent") >= 2 and '"physical_keycode":81,' in built_in


@pytest.mark.parametrize(("opening", "closing"), [("[", "]"), ("Array[InputEvent]([", "])")])
def test_input_tool_keeps_existing_events_and_other_actions(
    project: Project, opening: str, closing: str
) -> None:
    existing = _existing_input(opening, closing)
    before = PROJECT_GODOT.replace("[rendering]", existing + "\n[rendering]")
    project.write("project.godot", before)
    command = ("pause", "--key", "Ctrl+S", "--key", "p", "--joy-button", "Sony Circle")
    command += ("--joy-axis", "Right Trigger:1", "--mouse-button", "Mouse Wheel Up")
    code, out = project.tool("add_input_action", *command, "--joy-button", "30")
    assert code == 0, out
    assert (
        "extend  pause\n  + key Ctrl+S\n  = key P (already bound)\n  + joy-button b\n"
        "  + joy-axis trigger_right:1\n  + mouse-button wheel_up\n  + joy-button 30\n"
        "INPUT_ACTION_OK pause added=5 kept=1\n"
    ) in out

    after = project.read("project.godot")
    section = _section(after, "input")
    assert after.replace(section, "") == before.replace(_section(before, "input"), "")
    jump = existing[existing.index("jump={") : existing.index("pause={")]
    assert section.startswith("[input]\n\n" + jump)  # untouched, still first
    pause = section[section.index("pause={") :].rstrip("\n") + "\n"
    # The project's own deadzone and events, in the list form the file already used.
    kept = existing[existing.index("pause={") : existing.rindex(f"\n{closing}\n")]
    assert pause.startswith(kept + "\n, Object(InputEventKey,")
    assert pause.endswith(f"\n{closing}\n}}\n")
    keys = [int(key) for key in re.findall(r'"physical_keycode":(\d+)', pause)]
    assert keys == [KEY_F10, KEY_P, KEY_S]
    assert pause.count('"ctrl_pressed":true') == 1
    assert '"button_index":1,' in pause and '"button_index":30,' in pause
    assert '"axis":5,"axis_value":1.0,' in pause and '"button_index":4,' in pause

    # --deadzone belongs to a new action; asking for another one on this is refused.
    code, out = project.tool("add_input_action", "pause", "--key", "F1", "--deadzone", "0.2")
    assert code == 1, out
    assert "add_input_action: pause exists with deadzone 0.5; --deadzone only sets a new" in out
    assert "nothing was written" in out and "INPUT_ACTION_OK" not in out
    assert project.read("project.godot") == after

    # A new action goes to the end of the section. Names ignore case, spaces, - and _.
    fire = ("fire", "--mouse-button", "left", "--joy-button", "D-PAD_up")
    code, out = project.tool("add_input_action", *fire)
    assert code == 0, out
    section = _section(project.read("project.godot"), "input")
    assert re.findall(r"^([\w.-]+)=\{$", section, re.M) == ["jump", "pause", "fire"]

    code, out = project.tool("add_input_action", "--list")
    assert code == 0, out
    assert "jump --deadzone 0.5 --key Space\n" in out
    assert (
        "pause --deadzone 0.5 --key F10 --key P --key Ctrl+S --joy-button b "
        "--joy-axis trigger_right:1 --mouse-button wheel_up --joy-button 30\n"
    ) in out
    assert "fire --deadzone 0.2 --mouse-button left --joy-button dpad_up\n" in out
    assert "INPUT_ACTIONS 3" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "name the action to add"),
        (("jump",), "jump binds nothing; give it at least one of --key, --joy-button, --joy-axis,"),
        (("jump", "--key"), "--key needs a value"),
        (("jump", "--key", "Spce"), '--key "Spce" is not a Godot key name (W, Space,'),
        # The engine reads "Hyper+A" as plain A; the tool must not.
        (("jump", "--key", "Hyper+A"), "--key Hyper+A: Hyper is not a modifier (Shift, Alt,"),
        (("jump", "--key", "+"), '--key "+": write the plus key as Plus (Ctrl+Plus)'),
        (("jump", "--key", "Ctrl++"), '--key "Ctrl++": write the plus key as Plus (Ctrl+Plus)'),
        (("jump", "--joy-button", "nope"), '--joy-button "nope" is not one of: a, b, x, y, back,'),
        (("jump", "--joy-button", "128"), '--joy-button "128" is not one of: a, b,'),
        (("jump", "--joy-axis", "left_x"), '--joy-axis "left_x": write the axis and its direction'),
        (("jump", "--joy-axis", "left_x:0.5"), '--joy-axis "left_x:0.5": write the axis and its'),
        (("jump", "--joy-axis", "wheel:1"), '--joy-axis "wheel" is not one of: left_x, left_y,'),
        (("jump", "--mouse-button", "0"), '--mouse-button "0" is not one of: left, right, middle,'),
        (
            ("jump", "--key", "W", "--deadzone", "1.5"),
            '--deadzone "1.5" is not a number from 0 to 1',
        ),
        (("jump", "--key", "W", "--force"), "unknown argument: --force"),
        (("jump", "run", "--key", "W"), "unexpected argument: run (one action per run)"),
        (
            ("fire now", "--key", "F"),
            'an action name is letters, digits, _ . and -, not "fire now"',
        ),
        (("--list", "jump"), "--list takes no other argument"),
    ],
)
def test_input_tool_stops_on_bad_arguments(
    project: Project, args: tuple[str, ...], message: str
) -> None:
    before = project.read("project.godot")
    code, out = project.tool("add_input_action", *args)
    assert code == 2, out
    assert f"add_input_action: {message}" in out
    assert "nothing was written" in out
    # The command as a person types it: `--user-args`, never the bare `--` PowerShell eats.
    usage = "usage: pwsh ./tools/godot.ps1 --headless --script res://tools/add_input_action.gd"
    assert f"{usage} --user-args <action>" in out and " -- " not in out
    assert "INPUT_ACTION" not in out
    assert project.read("project.godot") == before


def test_input_tool_stops_on_an_entry_that_is_not_an_action(project: Project) -> None:
    broken = '[input]\n\npause={\n"deadzone": "high",\n"events": []\n}\ninteract="E"\n'
    before = PROJECT_GODOT.replace("[rendering]", broken + "\n[rendering]")
    project.write("project.godot", before)
    for action, problem in (
        ("pause", 'has the deadzone "high", which is not a number.'),
        ("interact", "is not a {deadzone, events} block."),
    ):
        code, out = project.tool("add_input_action", action, "--key", "E")
        assert code == 1, out
        assert f"add_input_action: res://project.godot [input] {action} {problem}" in out
        assert "Fix or remove it by hand; nothing was written." in out
        assert "INPUT_ACTION_OK" not in out
        assert project.read("project.godot") == before

    code, out = project.tool("add_input_action", "--list")
    assert code == 0, out
    assert 'pause  # has the deadzone "high", which is not a number.\n' in out
    assert "interact  # is not a {deadzone, events} block.\n" in out


def test_input_tool_keeps_crlf_line_endings(project: Project) -> None:
    before = PROJECT_GODOT.replace("\n", "\r\n")
    project.write("project.godot", before)
    code, out = project.tool("add_input_action", "jump", "--key", "Space", "--joy-button", "a")
    assert code == 0, out
    after = project.read("project.godot")
    assert '[input]\r\n\r\njump={\r\n"deadzone": 0.2,\r\n' in after
    assert re.search(r"(?<!\r)\n", after) is None
    start = after.index("[input]")
    end = after.index("[rendering]")
    assert after[:start] + after[end:] == before


def test_input_tool_finds_a_header_that_carries_a_comment(project: Project) -> None:
    # Godot reads "[input] ; mine" as the section; a second [input] must not appear.
    existing = _existing_input("[", "]").replace("[input]\n", "[input] ; mine\n")
    project.write("project.godot", PROJECT_GODOT.replace("[rendering]", existing + "\n[rendering]"))
    code, out = project.tool("add_input_action", "fire", "--key", "Ctrl+Plus")
    assert code == 0, out
    assert "add     fire (deadzone 0.2)\n  + key Ctrl+Plus\n" in out
    after = project.read("project.godot")
    assert re.findall(r"^\[input\].*$", after, re.M) == ["[input] ; mine"]
    section = after[after.index("[input] ; mine") : after.index("[rendering]")]
    assert re.findall(r"^([\w.-]+)=\{$", section, re.M) == ["jump", "pause", "fire"]

    code, out = project.tool("add_input_action", "--list")
    assert code == 0, out
    assert "fire --deadzone 0.2 --key Ctrl+Plus\n" in out and "INPUT_ACTIONS 3" in out


@pytest.mark.parametrize(
    "raw",
    [
        PROJECT_GODOT.encode("utf-8").replace(b'="Kit tool', b'="Caf\xe9 tool'),  # cp1252
        b"\xef\xbb\xbf" + PROJECT_GODOT.encode("utf-8"),
    ],
    ids=["a byte that is not UTF-8", "a byte-order mark"],
)
def test_input_tool_stops_on_a_project_file_it_cannot_write_back_exactly(
    project: Project, raw: bytes
) -> None:
    # Decoding and encoding again would change bytes outside [input].
    project.path("project.godot").write_bytes(raw)
    code, out = project.tool("add_input_action", "run", "--key", "R")
    assert code == 1, out
    assert "add_input_action: res://project.godot is not plain UTF-8 text" in out
    assert "nothing was written" in out and "INPUT_ACTION_OK" not in out
    assert project.path("project.godot").read_bytes() == raw
