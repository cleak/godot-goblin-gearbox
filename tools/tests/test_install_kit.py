"""install.ps1, run for real: from a temp copy of the kit into temp Godot projects.

Both folders have a space, an apostrophe and square brackets in their names, and the
script is started from a third folder. The last test needs a Godot binary and is skipped
without one.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from _gate_fixtures import PWSH, REPO, STRICT_WARNINGS, WINDOWS, clean_env, real_godot, run_tool

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7")

NOT_COPIED = {".git", ".godot", ".venv", ".tools", "artifacts", ".pytest_cache", ".ruff_cache"}
SEEDS = (
    ".gdlintrc",
    ".editorconfig",
    ".gitattributes",
    ".pre-commit-config.yaml",
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "CLAUDE.md",
    ".github/workflows/ci.yml",
    ".github/copilot-instructions.md",
    ".claude/settings.json",
    "AGENTS.md",
)
PROJECT_DATA = "tools/ci/architecture_modules.json"
HOOKS = ".pre-commit-config.yaml"

# The description holds a line that looks like a section header: it is inside a string.
PROJECT = """; Engine configuration file.

config_version=5

[application]

config/name="Target"
config/description="Line one
[autoload]
line three"
config/features=PackedStringArray("4.7", "Forward Plus")
{debug}
[rendering]

textures/canvas_textures/default_texture_filter=0
"""
AUTOLOAD = '[autoload]\n\nDevTools="*res://addons/agent_devtools/dev_tools.gd"\n\n'
DEVTOOLS = "[devtools]\n\nallow_mutation=false\n\n"
PLUGINS = (
    "[editor_plugins]\n\nenabled=PackedStringArray("
    '"res://addons/agent_devtools/plugin.cfg", "res://addons/gdUnit4/plugin.cfg")\n\n'
)
GITIGNORE = "# godot-goblin-gearbox\n.godot/\n/artifacts/\n/.tools/\n.venv/\n__pycache__/\n"
SCENE = (
    "[gd_scene load_steps=2 format=3]\n\n"
    '[ext_resource type="Script" path="res://player.gd" id="1"]\n\n'
    '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n'
)
TYPED_SCRIPT = "extends Node2D\n\nvar speed: float = 200.0\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _copy_kit(destination: Path, *, whole_gdunit: bool = False) -> Path:
    """The kit as a stranger has it: no caches, no local toolchain.

    gdUnit4 is about a thousand files; unless a test runs it, plugin.cfg stands for it.
    """
    gdunit = REPO / "addons" / "gdUnit4"

    def skipped(folder: str, names: list[str]) -> set[str]:
        drop = {name for name in names if name in NOT_COPIED or name == "__pycache__"}
        if not whole_gdunit and Path(folder) == gdunit:
            drop |= {name for name in names if name not in ("plugin.cfg", "LICENSE")}
        return drop

    shutil.copytree(REPO, destination, ignore=skipped)
    return destination


def _project(*, debug: str = "") -> str:
    return PROJECT.format(debug=f"\n{debug}" if debug else "")


def _target(root: Path, project: str | None = None) -> Path:
    _write(root / "project.godot", _project() if project is None else project)
    _write(root / "main.tscn", SCENE)
    _write(root / "player.gd", TYPED_SCRIPT)
    return root


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    return _copy_kit(tmp_path / "kit's [copy]")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return _target(tmp_path / "it's a [wip] game")


def _install(kit: Path, target: Path | str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run <kit>/install.ps1 from a folder that is neither the kit nor the target."""
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(kit / "install.ps1")]
        + ["-Target", str(target), *args],
        cwd=kit.parent,
        env=clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def _installed(kit: Path, target: Path, *args: str) -> str:
    result = _install(kit, target, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.strip() == ""
    return result.stdout


def _refused(kit: Path, target: Path | str, *args: str) -> str:
    """Exit 1 with one `install:` line and nothing on stderr; returns stdout."""
    result = _install(kit, target, *args)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stderr.strip() == ""
    assert "install: " in result.stdout
    return result.stdout


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every file (its bytes) and folder (None) under root."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _stamps(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file()
    }


def _kit_files(kit: Path) -> set[str]:
    """What the kit owns in a target: tools/ and the DevTools addon, minus what stays behind."""
    found = set()
    for tree in ("tools", "addons/agent_devtools"):
        for path in (kit / tree).rglob("*"):
            relative = path.relative_to(kit).as_posix()
            if path.is_file() and not relative.startswith("tools/tests/"):
                found.add(relative)
    return found - {PROJECT_DATA}


def _manifest(target: Path) -> dict:
    return json.loads(_read(target / "tools" / "kit_manifest.json"))


def _lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _section_keys(text: str, section: str) -> list[str]:
    body = re.search(rf"^\[{section}\]\n(.*?)(?=^\[\w+\]\n|\Z)", text, re.M | re.S)
    assert body is not None, f"no [{section}] in:\n{text}"
    return re.findall(r"^([\w/]+)=", body[1], re.M)


def _without_kit_entries(text: str, record: list[str]) -> str:
    """project.godot with `wrote.project_godot` undone as README "Remove it" says, entry
    by entry. The kit's sections come after [application], whose description holds a line
    that looks like a header: the last match is the section."""
    lines = text.split("\n")

    def span(section: str) -> tuple[int, int]:
        start = max(index for index, line in enumerate(lines) if line == f"[{section}]")
        later = (i for i in range(start + 1, len(lines)) if re.fullmatch(r"\[\w+\]", lines[i]))
        return start, next(later, len(lines))

    def entry_line(section: str, key: str) -> int:
        start, end = span(section)
        (found,) = [i for i in range(start, end) if lines[i].startswith(f"{key}=")]
        return found

    made = []
    for entry in record:
        section, what = re.fullmatch(r"\[(\w+)\] (.+)", entry).groups()
        replaced = re.fullmatch(r"(\w+) (\S+) -> 2", what)
        if what == "new section":
            made.append(section)
        elif section == "autoload":
            assert what == "DevTools"
            del lines[entry_line(section, "DevTools")]
        elif section == "devtools":
            assert what == "allow_mutation=false"
            del lines[entry_line(section, "allow_mutation")]
        elif section == "editor_plugins":
            at = entry_line(section, "enabled")
            for plugin in what.split(", "):
                path = f'"res://addons/{plugin}/plugin.cfg"'
                assert path in lines[at]
                lines[at] = lines[at].replace(f", {path}", "").replace(f"{path}, ", "")
                lines[at] = lines[at].replace(path, "")
        elif replaced:
            at = entry_line(section, f"gdscript/warnings/{replaced[1]}")
            lines[at] = f"gdscript/warnings/{replaced[1]}={replaced[2]}"
        else:
            assert section == "debug" and what.endswith(" added"), entry
            at = entry_line(section, f"gdscript/warnings/{what.removesuffix(' added')}")
            assert lines[at].endswith("=2")
            del lines[at]
    for section in made:
        start, end = span(section)
        assert set(lines[start + 1 : end]) <= {"", "enabled=PackedStringArray()"}, section
        del lines[start:end]
    return "\n".join(lines)


def test_blank_project_gets_the_kit(kit: Path, target: Path) -> None:
    assert (kit / "tools" / "tests").is_dir() and (kit / PROJECT_DATA).is_file()
    out = _installed(kit, target)

    # The kit's files, byte for byte, .uid sidecars included; not its tests or project data.
    owned = _kit_files(kit)
    assert "tools/check.ps1" in owned and "tools/headless_tool.gd.uid" in owned
    assert "addons/agent_devtools/dev_tools.gd.uid" in owned
    for relative in owned:
        assert (target / relative).read_bytes() == (kit / relative).read_bytes(), relative
    assert not (target / "tools" / "tests").exists()
    assert not (target / "test" / "kit").exists()
    assert not (target / PROJECT_DATA).exists()
    assert not list(target.rglob("__pycache__"))
    assert (target / "addons" / "gdUnit4" / "plugin.cfg").is_file()

    manifest = _manifest(target)
    assert manifest["kit_version"] == _read(kit / "VERSION").strip()
    assert set(manifest["files"]) == owned
    assert manifest["files"]["tools/check.ps1"] == _lf_sha256(kit / "tools" / "check.ps1")
    # The kit's licence travels with its tools.
    assert _read(target / "tools" / "LICENSE") == _read(kit / "LICENSE")
    assert _read(kit / "addons" / "agent_devtools" / "LICENSE") == _read(kit / "LICENSE")

    docs = [
        path.relative_to(kit).as_posix() for path in (kit / "docs").rglob("*") if path.is_file()
    ]
    # The README's pictures stay behind: the kit's README is not installed.
    pictures = [relative for relative in docs if relative.startswith("docs/images/")]
    assert pictures and not (target / "docs" / "images").exists()
    docs = [relative for relative in docs if relative not in pictures]
    for relative in (*SEEDS, *docs):
        if relative != HOOKS:  # written for a project the kit is a guest in: its own test
            assert (target / relative).read_bytes() == (kit / relative).read_bytes(), relative
    # Every starter file is recorded with the hash it was written with.
    assert set(manifest["seeded"]) == {*SEEDS, *docs}
    assert manifest["seeded"]["CLAUDE.md"] == _lf_sha256(kit / "CLAUDE.md")
    assert manifest["seeded"][HOOKS] == _lf_sha256(target / HOOKS)
    starter = _read(target / "test" / "unit" / "test_kit_smoke.gd")
    assert starter.startswith("extends GdUnitTestSuite\n") and "\r" not in starter
    assert re.fullmatch(r"uid://[a-y0-8]+\n", _read(target / "test/unit/test_kit_smoke.gd.uid"))
    # What removing the kit has to undo.
    assert manifest["wrote"] == {
        "kit_json": True,
        "gdunit4": True,
        "starter_test": "test/unit/test_kit_smoke.gd",
        "project_godot": [
            "[autoload] new section",
            "[autoload] DevTools",
            "[editor_plugins] new section",
            "[editor_plugins] agent_devtools, gdUnit4",
            "[devtools] new section",
            "[devtools] allow_mutation=false",
        ],
        "gitignore": [".godot/", "/artifacts/", "/.tools/", ".venv/", "__pycache__/"],
    }
    assert "unfinished" not in manifest  # only while a run is under way
    assert list(manifest) == ["kit_version", "hash", "seeded", "wrote", "files"]

    config = json.loads(_read(target / "kit.json"))
    pins = json.loads(_read(kit / "kit.json"))
    assert config["godot"] == pins["godot"] | {"search_roots": []}
    assert config["gdunit4"] == pins["gdunit4"]
    assert config["source"] == {"exclude": ["addons", "artifacts", "docs"], "include": []}
    assert config["tests"] == {"root": "test"}
    assert config["gdscript"] == {"strict": False}

    # project.godot: three entries in new sections, every other byte as it was.
    before = _project()
    cut = before.index("[rendering]")
    after = _read(target / "project.godot")
    assert after == before[:cut] + AUTOLOAD + DEVTOOLS + PLUGINS + before[cut:]
    assert _without_kit_entries(after, manifest["wrote"]["project_godot"]) == before
    assert _read(target / ".gitignore") == GITIGNORE  # none before: Godot's cache folder too

    assert f"{len(owned)} new, 0 updated, 0 unchanged" in out
    assert "note:" not in out
    assert "pwsh ./tools/setup.ps1 -InstallGodot" in out
    assert "pwsh ./tools/check.ps1" in out
    assert "gdscript.strict=false" in out and "-StrictWarnings" in out


def test_second_run_writes_nothing(kit: Path, target: Path) -> None:
    _installed(kit, target)
    files, stamps = _tree(target), _stamps(target)
    out = _installed(kit, target)
    assert "Up to date: nothing to write." in out
    assert f"0 new, 0 updated, {len(_kit_files(kit))} unchanged" in out
    assert _tree(target) == files
    assert _stamps(target) == stamps  # not even rewritten with the same bytes

    # A checkout that turned LF into CRLF is still the kit's file, and is left as it is.
    for relative in ("tools/check.ps1", "tools/headless_tool.gd", "tools/kit_manifest.json"):
        path = target / relative
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    files = _tree(target)
    assert "Up to date: nothing to write." in _installed(kit, target)
    assert _tree(target) == files


def test_a_newer_kit_updates_its_files_and_leaves_yours(kit: Path, target: Path) -> None:
    # The project already has a tools/ folder with a script of its own.
    mine = _write(target / "tools" / "build.ps1", "Write-Host 'my build'\n")
    _installed(kit, target)
    assert _read(mine) == "Write-Host 'my build'\n"
    assert "tools/build.ps1" not in _manifest(target)["files"]

    # The next kit: one file changed, one added, two gone; one of the gone was edited here.
    changed = kit / "tools" / "check.ps1"
    changed.write_bytes(changed.read_bytes() + b"# a newer check\n")
    _write(kit / "tools" / "brand_new.ps1", "Write-Host 'new tool'\n")
    for gone in ("validate_all.gd", "validate_all.gd.uid", "lint_shaders.gd"):
        (kit / "tools" / gone).unlink()
    edited = target / "tools" / "lint_shaders.gd"
    edited.write_bytes(edited.read_bytes() + b"# my change\n")
    # And a starter file gone: the project's copy is the project's.
    (kit / "docs" / "gotchas.md").unlink()
    page = (target / "docs" / "gotchas.md").read_bytes()
    _write(kit / "VERSION", "9.9.9\n")

    out = _installed(kit, target)
    assert "1 new, 1 updated" in out and "2 removed" in out
    assert "~ tools/check.ps1" in out and "- tools/validate_all.gd" in out
    assert "! tools/lint_shaders.gd" in out and "you changed it" in out
    assert (target / "tools" / "check.ps1").read_bytes() == changed.read_bytes()
    assert (target / "tools" / "brand_new.ps1").is_file()
    assert not (target / "tools" / "validate_all.gd").exists()
    assert not (target / "tools" / "validate_all.gd.uid").exists()
    assert edited.read_bytes().endswith(b"# my change\n")  # kept, and no longer the kit's
    manifest = _manifest(target)
    assert manifest["kit_version"] == "9.9.9"
    assert set(manifest["files"]) == _kit_files(kit)
    assert _read(mine) == "Write-Host 'my build'\n"
    # The dropped starter file stays, on record (for whoever removes the kit) with no hash.
    assert "! docs/gotchas.md   (no longer part of the kit; yours to keep or delete)" in out
    assert (target / "docs" / "gotchas.md").read_bytes() == page
    assert manifest["seeded"]["docs/gotchas.md"] == ""
    assert manifest["seeded"]["docs/tools.md"] == _lf_sha256(kit / "docs" / "tools.md")
    # Said once. The record goes when the file does.
    out = _installed(kit, target)
    assert "Up to date" in out and "docs/gotchas.md" not in out
    assert "docs/gotchas.md" in _manifest(target)["seeded"]
    (target / "docs" / "gotchas.md").unlink()
    _installed(kit, target)
    assert "docs/gotchas.md" not in _manifest(target)["seeded"]
    assert not (target / "docs" / "gotchas.md").exists()


def test_a_newer_kit_updates_the_starter_files_you_left_alone(kit: Path, target: Path) -> None:
    mine = _write(target / ".editorconfig", "root = true\n# mine from the start\n")
    _installed(kit, target)
    edited = target / "docs" / "devtools.md"
    edited.write_bytes(edited.read_bytes() + b"\nMy own note.\n")
    edited_text = _read(edited)

    for name in ("docs/tools.md", "docs/devtools.md", "CLAUDE.md", ".editorconfig"):
        (kit / name).write_bytes((kit / name).read_bytes() + b"\nNewer kit text.\n")
    before = _tree(target)
    out = _installed(kit, target, "-DryRun")
    assert _tree(target) == before
    for line in (
        "~ docs/tools.md",
        "~ CLAUDE.md",
        "! docs/devtools.md   (yours kept; the kit's changed since you got it)",
        "= .editorconfig   (yours kept; the kit's copy is not installed)",
        "starter files  0 new, 2 updated, ",
        ", 2 yours kept",
    ):
        assert line in out, line

    out = _installed(kit, target)
    assert _read(target / "docs" / "tools.md") == _read(kit / "docs" / "tools.md")
    assert _read(target / "CLAUDE.md") == _read(kit / "CLAUDE.md")
    assert _read(edited) == edited_text and _read(mine) == "root = true\n# mine from the start\n"
    assert "! docs/devtools.md" in out
    assert ".editorconfig" not in _manifest(target)["seeded"]
    # Said once; the edited file is compared with the kit's next change from here on.
    # -Force is for the kit's files: it overwrites no starter file you changed.
    out = _installed(kit, target, "-Force")
    assert "Up to date" in out and "docs/devtools.md" not in out
    assert _read(edited) == edited_text and _read(mine) == "root = true\n# mine from the start\n"

    # A pyproject.toml the project changed keeps its own lock file.
    project = target / "pyproject.toml"
    project.write_bytes(project.read_bytes() + b"# uv add was here\n")
    lock = (target / "uv.lock").read_bytes()
    (kit / "uv.lock").write_bytes((kit / "uv.lock").read_bytes() + b"# newer\n")
    assert "Up to date" in _installed(kit, target)
    assert (target / "uv.lock").read_bytes() == lock


def test_a_file_with_the_kits_text_that_was_yours_from_the_start_stays_yours(
    kit: Path, target: Path
) -> None:
    # The same bytes as the kit's starter file, written by the project's owner: no run
    # may take it for the kit's and bring it "up to date".
    mine = _write(target / ".python-version", _read(kit / ".python-version"))
    _write(target / "pyproject.toml", '[project]\nname = "mine"\nversion = "1"\n')
    out = _installed(kit, target)
    assert "~ .python-version" not in out
    seeded = _manifest(target)["seeded"]
    assert ".python-version" not in seeded and "pyproject.toml" not in seeded
    assert "CLAUDE.md" in seeded

    text = _read(mine)
    _write(kit / ".python-version", "3.99\n")
    _write(kit / "VERSION", "9.9.9\n")
    out = _installed(kit, target, "-Force")
    assert ".python-version" not in out
    assert _read(mine) == text
    assert ".python-version" not in _manifest(target)["seeded"]


def test_a_starter_file_you_deleted_stays_deleted(kit: Path, target: Path) -> None:
    _installed(kit, target)
    gone = ("CLAUDE.md", "AGENTS.md", "docs/gotchas.md", "uv.lock", ".github/workflows/ci.yml")
    starter = ("test/unit/test_kit_smoke.gd", "test/unit/test_kit_smoke.gd.uid")
    for relative in (*gone, *starter):
        (target / relative).unlink()
    files = _tree(target)
    out = _installed(kit, target)
    assert "Up to date: nothing to write." in out and "starter test" not in out
    assert _tree(target) == files

    # Also when the kit's copy changes. The record stays: put the file back and it follows.
    for relative in ("CLAUDE.md", "AGENTS.md", "docs/gotchas.md"):
        (kit / relative).write_bytes((kit / relative).read_bytes() + b"\nNewer kit text.\n")
    out = _installed(kit, target)
    for relative in (*gone, *starter, "AGENTS.kit.md"):
        assert not (target / relative).exists(), relative
        assert relative not in out, relative
    manifest = _manifest(target)
    assert set(gone) <= set(manifest["seeded"])
    assert manifest["wrote"]["starter_test"] == "test/unit/test_kit_smoke.gd"


def test_an_older_kit_does_not_replace_a_newer_one(kit: Path, target: Path) -> None:
    _installed(kit, target)
    manifest = _manifest(target)
    version = _read(kit / "VERSION").strip()
    refusals = {
        "99.0.0": f"install: the project has kit 99.0.0, this kit is {version}. Pull the kit",
        # Not a version this script can compare: it does not guess which is newer.
        "0.9.0-rc1": f"install: the project has kit 0.9.0-rc1, this kit is {version}: "
        "which is newer cannot be told. Run again with -Force",
    }
    for recorded, message in refusals.items():
        manifest["kit_version"] = recorded
        _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
        before = _tree(target)
        for args in ((), ("-DryRun",)):
            out = _refused(kit, target, *args)
            assert message in out
            assert len(out.strip().splitlines()) == 1, out
            assert _tree(target) == before
    _installed(kit, target, "-Force")
    assert _manifest(target)["kit_version"] == version


def test_a_kit_file_you_changed_is_a_conflict(kit: Path, target: Path) -> None:
    _installed(kit, target)
    edited = target / "tools" / "check.ps1"
    edited.write_bytes(edited.read_bytes() + b"# my local tweak\n")
    other = kit / "tools" / "doctor.ps1"
    other.write_bytes(other.read_bytes() + b"# newer doctor\n")
    before = _tree(target)

    out = _refused(kit, target)
    assert "1 kit file(s) in the target differ" in out
    assert "\n  tools/check.ps1\n" in out and "-Force" in out
    assert "tools/doctor.ps1" not in out
    assert _tree(target) == before  # nothing written: not even the update that had no conflict

    out = _installed(kit, target, "-Force")
    assert "~ tools/check.ps1" in out
    assert edited.read_bytes() == (kit / "tools" / "check.ps1").read_bytes()
    assert (target / "tools" / "doctor.ps1").read_bytes() == other.read_bytes()


def test_a_file_of_yours_with_a_kit_name_is_never_overwritten(kit: Path, target: Path) -> None:
    _write(target / "tools" / "check.ps1", "Write-Host 'my own check'\n")
    before = _tree(target)
    for args in ((), ("-Force",), ("-DryRun",), ("-Force", "-DryRun")):
        out = _refused(kit, target, *args)
        assert (
            "install: 1 file(s) of yours have the name of a kit file:\n  tools/check.ps1\n" in out
        )
        assert "Rename or move them, then run again." in out
        assert "-Force" not in out and "template" not in out
        assert _tree(target) == before

    # The same on an upgrade, when a newer kit ships a name the project already uses.
    (target / "tools" / "check.ps1").unlink()
    _installed(kit, target)
    mine = _write(target / "tools" / "my_build.ps1", "Write-Host 'mine'\n")
    _write(kit / "tools" / "my_build.ps1", "Write-Host 'the kit grew this name'\n")
    before = _tree(target)
    out = _refused(kit, target, "-Force")
    assert "\n  tools/my_build.ps1\n" in out and "of yours" in out
    assert _tree(target) == before and _read(mine) == "Write-Host 'mine'\n"


def test_a_game_made_from_the_template_is_told_how_to_upgrade(kit: Path, tmp_path: Path) -> None:
    """It has the kit's files and no manifest: a file the newer kit changed cannot be told
    from one the owner wrote, so it is left alone and the way out is printed."""
    game = _copy_kit(tmp_path / "template game")
    shutil.rmtree(game / "tools" / "tests")
    version = _read(game / "VERSION").strip()
    # The same version first. A kit tool the owner edited stops it like any file of theirs,
    # without the advice to do what they are doing.
    tool = game / "tools" / "check.ps1"
    shipped = tool.read_bytes()
    tool.write_bytes(shipped + b"# my gate\n")
    out = _refused(kit, game)
    assert "1 file(s) of yours have the name of a kit file:\n  tools/check.ps1\n" in out
    assert "the kit's template" not in out
    tool.write_bytes(shipped)
    # Every kit file is recorded, nothing is rewritten; the starter files are the kit's.
    out = _installed(kit, game)
    assert "0 new, 0 updated" in out and "tools/kit_manifest.json   (commit it)" in out
    assert {"CLAUDE.md", ".python-version", "docs/tools.md"} <= set(_manifest(game)["seeded"])
    assert "Up to date" in _installed(kit, game)
    (game / "tools" / "kit_manifest.json").unlink()

    # A newer kit that changes none of the game's kit files (one dropped, one new, a page
    # edited) would install, drop nothing and leave the game on no version at all.
    _write(kit / "VERSION", "9.9.9\n")
    (kit / "tools" / "lint_shaders.gd").unlink()
    _write(kit / "tools" / "brand_new.ps1", "Write-Host 'new tool'\n")
    (kit / "CLAUDE.md").write_bytes((kit / "CLAUDE.md").read_bytes() + b"\nNewer kit text.\n")
    before = _tree(game)
    for args in (("-DryRun",), ("-Force",)):
        out = _refused(kit, game, *args)
        assert "install: the game has the kit's files and no tools/kit_manifest.json" in out
        assert f"at kit {version} by its VERSION file" in out
        assert f"run the install.ps1 of kit {version} on it and commit the manifest" in out
        assert len(out.strip().splitlines()) == 1, out
        assert _tree(game) == before

    # Without VERSION nothing says where the game started: said when a kit file differs.
    (game / "VERSION").unlink()
    changed = kit / "tools" / "doctor.ps1"
    changed.write_bytes(changed.read_bytes() + b"# a newer doctor\n")
    before = _tree(game)
    out = _refused(kit, game, "-Force")
    assert "1 file(s) of yours have the name of a kit file:\n  tools/doctor.ps1\n" in out
    assert "A game made from the kit's template has no tools/kit_manifest.json" in out
    assert "run the install.ps1 of the kit version the game started from" in out
    assert _tree(game) == before


@pytest.mark.parametrize(
    "key", ["tools/..\\..\\victim.txt", "tools/../../victim.txt", "tools\\..\\..\\victim.txt"]
)
def test_a_manifest_that_names_a_file_outside_the_project_is_refused(
    kit: Path, target: Path, key: str
) -> None:
    # A manifest from a cloned repository is someone else's text: it must not pick what
    # gets deleted. The entry carries the outside file's own hash, as "unmodified" needs.
    _installed(kit, target)
    victim = _write(target.parent / "victim.txt", "not the kit's\n")
    manifest = _manifest(target)
    manifest["files"][key] = _lf_sha256(victim)
    _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
    before = _tree(target)
    for args in ((), ("-DryRun",), ("-Force",)):
        out = _refused(kit, target, *args)
        assert f"install: the target's tools/kit_manifest.json lists '{key}'" in out
        assert "not a file inside the project" in out
        assert len(out.strip().splitlines()) == 1, out
        assert _read(victim) == "not the kit's\n"
        assert _tree(target) == before


def test_a_manifest_entry_removes_nothing_the_kit_does_not_install(kit: Path, target: Path) -> None:
    # Only the kit's own files are ever removed. An entry with the file's true hash is what
    # "the kit installed it and you did not change it" looks like: it must not be enough.
    mine = {
        "tools/tests/test_mine.py": "def test_mine():\n    assert True\n",
        PROJECT_DATA: '{"modules": {}}\n',
        "tools/ci/slow_suites.txt": "res://test/unit/test_slow.gd\n",
        "README.md": "# My game\n",
        "src/game.gd": "extends Node\n",
    }
    for relative, text in mine.items():
        _write(target / relative, text)
    _installed(kit, target)
    manifest = _manifest(target)
    for relative in mine:
        # The kit's tests folder under another spelling: the same folder to Windows.
        listed = relative.replace("tools/tests/", "tools/Tests/")
        manifest["files"][listed] = _lf_sha256(target / relative)
    assert "tools/Tests/test_mine.py" in manifest["files"]
    _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
    for args in (("-DryRun",), ("-Force",)):
        out = _installed(kit, target, *args)
        assert "removed" not in out
        for relative, text in mine.items():
            assert _read(target / relative) == text, relative
            assert relative not in out, relative
    assert set(_manifest(target)["files"]) == _kit_files(kit)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is not a JSON object"),
        ("[]\n", "is not a JSON object"),
        ('"x"\n', "is not a JSON object"),
        ("null\n", "is not a JSON object"),
        ("{}\n", "has no kit_version"),
        ('{"kit_version": "0.1.0", "seeded": {}, "wrote": {}}\n', 'has no "files" object'),
    ],
)
def test_a_manifest_that_is_not_one_is_refused(
    kit: Path, target: Path, text: str, message: str
) -> None:
    # Read as "nothing on record", it would make every kit file there the project's own.
    _write(target / "tools" / "kit_manifest.json", text)
    before = _tree(target)
    out = _refused(kit, target, "-Force")
    assert f"install: the target's tools/kit_manifest.json {message}. Restore it from git" in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(target) == before


def test_a_gdunit4_copy_that_stops_halfway_is_finished_by_the_next_run(
    kit: Path, target: Path
) -> None:
    import msvcrt

    # plugin.cfg is copied before src/: a folder cut short still names the pinned version.
    last = _write(kit / "addons" / "gdUnit4" / "src" / "zz_last.gd", "extends RefCounted\n")
    with last.open("rb") as held:
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)  # another process cannot read it
        try:
            out = _refused(kit, target)
        finally:
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
    assert "install: stopped halfway. Fix that, then run the same command again." in out
    assert not (target / "addons" / "gdUnit4").exists()  # never a folder that is half there
    # The manifest already says what the run set out to write; no file is the kit's yet.
    manifest = _manifest(target)
    assert manifest["files"] == {} and manifest["seeded"] == {}
    assert manifest["wrote"]["gdunit4"] is True and manifest["wrote"]["kit_json"] is True
    assert {"CLAUDE.md", "AGENTS.md", "test/unit/test_kit_smoke.gd"} <= set(manifest["unfinished"])
    assert not (target / "CLAUDE.md").exists() and not (target / "kit.json").exists()

    out = _installed(kit, target)
    assert "gdUnit4        new: " in out and "3 files" in out
    assert "starter test   test/unit/test_kit_smoke.gd" in out
    manifest = _manifest(target)
    assert "unfinished" not in manifest and set(manifest["files"]) == _kit_files(kit)
    assert (target / "test" / "unit" / "test_kit_smoke.gd").is_file()
    for relative in ("LICENSE", "plugin.cfg", "src/zz_last.gd"):
        copied = target / "addons" / "gdUnit4" / relative
        assert copied.read_bytes() == (kit / "addons" / "gdUnit4" / relative).read_bytes()
    assert sorted(path.name for path in (target / "addons").iterdir()) == [
        "agent_devtools",
        "gdUnit4",
    ]
    assert "Up to date" in _installed(kit, target)

    # What a killed run leaves behind is cleared, also beside a gdUnit4 of the project's own.
    _write(target / "addons" / ".gdUnit4.kit-partial" / "plugin.cfg", "half\n")
    assert "gdUnit4.kit-partial" in _installed(kit, target, "-DryRun")
    assert (target / "addons" / ".gdUnit4.kit-partial").is_dir()
    assert "Up to date" not in _installed(kit, target)
    assert not (target / "addons" / ".gdUnit4.kit-partial").exists()
    assert "Up to date" in _installed(kit, target)


def test_a_run_that_stops_halfway_loses_no_record(kit: Path, target: Path) -> None:
    """The manifest is what removing the kit goes by, and what tells the kit's files from
    yours: a run that stops after it wrote files must leave both true for the next run."""
    debug = "[debug]\n\ngdscript/warnings/untyped_declaration=1\n"
    project = _write(target / "project.godot", _project(debug=debug))
    ignore = _write(target / ".gitignore", ".godot/\n")
    # Yours from the start, with the kit's text: not the kit's after a stopped run either.
    mine = _write(target / ".python-version", _read(kit / ".python-version"))
    wrote = {
        "kit_json": True,
        "gdunit4": True,
        "starter_test": "test/unit/test_kit_smoke.gd",
        "project_godot": [
            "[autoload] new section",
            "[autoload] DevTools",
            "[editor_plugins] new section",
            "[editor_plugins] agent_devtools, gdUnit4",
            "[devtools] new section",
            "[devtools] allow_mutation=false",
        ],
        "gitignore": ["/artifacts/", "/.tools/", ".venv/", "__pycache__/"],
    }

    # A first install that cannot write .gitignore: its last step but the manifest.
    ignore.chmod(stat.S_IREAD)
    try:
        out = _refused(kit, target)
    finally:
        ignore.chmod(stat.S_IWRITE | stat.S_IREAD)
    assert "install: stopped halfway. Fix that, then run the same command again." in out
    assert (target / "kit.json").is_file() and "DevTools=" in _read(project)
    manifest = _manifest(target)
    assert manifest["wrote"] == wrote
    assert manifest["files"] == {} and manifest["seeded"] == {}
    assert "CLAUDE.md" in manifest["unfinished"] and ".python-version" not in manifest["unfinished"]

    _installed(kit, target)
    manifest = _manifest(target)
    assert manifest["wrote"] == wrote and "unfinished" not in manifest
    assert set(manifest["files"]) == _kit_files(kit)
    # The starter files the stopped run wrote are the kit's; the one that was there is not.
    assert {"CLAUDE.md", "AGENTS.md", "docs/tools.md", "uv.lock"} <= set(manifest["seeded"])
    assert ".python-version" not in manifest["seeded"]
    assert "Up to date" in _installed(kit, target)

    # An upgrade that cannot write project.godot: kit and starter files are written by then.
    tool = kit / "tools" / "check.ps1"
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    (kit / "CLAUDE.md").write_bytes((kit / "CLAUDE.md").read_bytes() + b"\nNewer kit text.\n")
    _write(kit / "docs" / "new_page.md", "# New in this kit\n")
    _write(kit / ".python-version", "3.99\n")
    _write(kit / "VERSION", "9.9.9\n")
    old = manifest["files"]["tools/check.ps1"]
    project.chmod(stat.S_IREAD)
    try:
        out = _refused(kit, target, "-StrictWarnings")
    finally:
        project.chmod(stat.S_IWRITE | stat.S_IREAD)
    assert "install: stopped halfway." in out
    assert (target / "tools" / "check.ps1").read_bytes() == tool.read_bytes()
    manifest = _manifest(target)
    # Still the old kit on record, so the files not yet updated are not taken for edits.
    assert manifest["kit_version"] == _read(REPO / "VERSION").strip()
    assert manifest["files"]["tools/check.ps1"] == old
    assert "docs/new_page.md" not in manifest["seeded"]
    assert set(manifest["unfinished"]) == {"CLAUDE.md", "docs/new_page.md"}
    strict = ["[debug] untyped_declaration 1 -> 2"]
    strict += [f"[debug] {name} added" for name in _strict_names(kit)[1:]]
    wrote["project_godot"] += strict
    assert manifest["wrote"] == wrote  # though project.godot does not have them yet
    assert "untyped_declaration=1\n" in _read(project)

    # Finished by a kit that moved on meanwhile: what the stopped run wrote is the kit's
    # to update, though "files" and "seeded" never recorded it.
    for relative in ("CLAUDE.md", "docs/new_page.md", "tools/doctor.ps1"):
        (kit / relative).write_bytes((kit / relative).read_bytes() + b"\n# Newer still.\n")
    _write(kit / "VERSION", "9.9.10\n")
    out = _installed(kit, target, "-StrictWarnings")
    assert "install:" not in out and "[debug] untyped_declaration 1 -> 2" in out
    for line in ("~ tools/doctor.ps1", "~ CLAUDE.md", "~ docs/new_page.md"):
        assert line in out, line
    manifest = _manifest(target)
    assert manifest["kit_version"] == "9.9.10" and "unfinished" not in manifest
    assert manifest["wrote"] == wrote
    assert manifest["files"]["tools/check.ps1"] == _lf_sha256(tool)
    for relative in ("CLAUDE.md", "docs/new_page.md"):
        assert (target / relative).read_bytes() == (kit / relative).read_bytes()
        assert manifest["seeded"][relative] == _lf_sha256(kit / relative)
    assert ".python-version" not in manifest["seeded"] and _read(mine) != "3.99\n"
    assert _without_kit_entries(_read(project), wrote["project_godot"]) == _project(debug=debug)
    assert "Up to date" in _installed(kit, target, "-StrictWarnings")


def test_a_gitignore_keeps_its_own_bytes(kit: Path, target: Path) -> None:
    # Appended to, never rewritten: bytes that are not UTF-8 and a BOM both survive.
    mine = b"\xef\xbb\xbf/artifacts/\n# f\xfcr mich\n.godot/"
    (target / ".gitignore").write_bytes(mine)
    out = _installed(kit, target)
    assert ".gitignore     + /.tools/ .venv/ __pycache__/" in out
    assert (target / ".gitignore").read_bytes() == (
        mine + b"\n\n# godot-goblin-gearbox\n/.tools/\n.venv/\n__pycache__/\n"
    )
    assert "Up to date" in _installed(kit, target)


def test_a_utf16_gitignore_is_refused(kit: Path, target: Path) -> None:
    # What `echo .godot/ > .gitignore` writes in Windows PowerShell 5; git cannot read it.
    (target / ".gitignore").write_bytes(".godot/\r\n".encode("utf-16"))
    before = _tree(target)
    out = _refused(kit, target)
    assert "install: .gitignore is not UTF-8 text" in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(target) == before


def test_your_own_gdlintrc_judges_your_scripts_and_not_the_kits(kit: Path, target: Path) -> None:
    """max-line-length: 80 would fail the style gate on 104 lines of the kit's
    tools/*.gd, and `check.ps1 -Fix` would rewrite them into conflicts for the next upgrade."""
    gdtoolkit = Path(sys.executable).parent
    assert (gdtoolkit / "gdlint.exe").is_file(), "run uv sync"
    _write(target / ".gdlintrc", "max-line-length: 80\n")
    out = _installed(kit, target)
    assert "note:" not in out
    assert "= .gdlintrc   (yours kept; the kit's copy is not installed)" in out
    assert _read(target / ".gdlintrc") == "max-line-length: 80\n"
    listed = [name for name in _manifest(target)["files"] if name.endswith(".gd")]
    kit_scripts = [name for name in listed if name.startswith("tools/")]
    assert len(kit_scripts) >= 8, kit_scripts
    # At least one of them is longer than this project allows.
    assert any(len(line) > 80 for name in kit_scripts for line in _read(target / name).splitlines())

    env = clean_env(PATH=str(gdtoolkit) + os.pathsep + os.environ.get("PATH", ""))
    before = _tree(target)
    for args in ((), ("-Fix",)):
        lint = run_tool(target, "lint_gdscript.ps1", *args, env=env)
        assert lint.returncode == 0, lint.stdout + lint.stderr
        # player.gd and the starter test; addons/ is excluded by the kit.json it got.
        assert "Linting 2 GDScript file(s)" in lint.stdout
        assert f"{len(kit_scripts)} kit file(s) left out: tools/kit_manifest.json" in lint.stdout
    assert _tree(target) == before  # -Fix rewrote nothing
    assert "Up to date" in _installed(kit, target)

    # The project's own script is still held to its own rule.
    _write(target / "player.gd", TYPED_SCRIPT + f'const NAME: String = "{"x" * 70}"\n')
    own = run_tool(target, "lint_gdscript.ps1", env=env)
    assert own.returncode == 1 and "player.gd:4: Error: Max allowed line length (80)" in own.stdout


def test_the_seeded_hooks_leave_the_kits_files_alone(kit: Path, target: Path) -> None:
    """pre-commit runs gdlint and gdformat on the files of a commit with the project's
    .gdlintrc: in an installed project that must not reach what the kit owns."""
    _installed(kit, target)
    seeded = _read(target / HOOKS)
    assert "\r" not in seeded and seeded.isascii()
    ids = r"^\s+- id: gd(?:lint|format)$"
    kit_text = _read(kit / HOOKS)
    for name, text in (("rev", r"^\s+rev: (\S+)$"), ("repo", r"^\s+- repo: (\S+)$"), ("ids", ids)):
        assert re.findall(text, seeded, re.M) == re.findall(text, kit_text, re.M) != [], name
    assert 'args: ["--line-length", "100"]' in seeded
    excludes = re.findall(r"^\s+exclude: (\S+)$", seeded, re.M)
    assert len(excludes) == 2 == len(re.findall(ids, seeded, re.M))
    owned = [name for name in _manifest(target)["files"] if name.endswith(".gd")]
    assert "tools/typecheck.gd" in owned and "addons/agent_devtools/dev_tools.gd" in owned
    for pattern in excludes:
        # pre-commit searches each file's path (forward slashes) for the pattern.
        assert all(re.search(pattern, name) for name in owned)
        assert re.search(pattern, "addons/gdUnit4/src/GdUnitTestSuite.gd")
        for mine in ("player.gd", "scripts/tools/x.gd", "test/unit/test_kit_smoke.gd"):
            assert not re.search(pattern, mine), mine

    # A project with hooks of its own keeps them.
    other = _target(target.parent / "second game")
    _write(other / HOOKS, "repos: []\n")
    _installed(kit, other)
    assert _read(other / HOOKS) == "repos: []\n"


def test_keys_and_sections_are_matched_as_godot_matches_them(kit: Path, target: Path) -> None:
    # Case matters to Godot, and a header may carry a comment.
    project = _project().replace(
        "[rendering]",
        '[autoload] ; my singletons\n\ndevtools="*res://game.gd"\n\n'
        "[Devtools]\n\nallow_mutation=true\n\n[rendering]",
    )
    _write(target / "project.godot", project)
    _installed(kit, target)
    after = _read(target / "project.godot")
    assert (
        '[autoload] ; my singletons\n\ndevtools="*res://game.gd"\n'
        'DevTools="*res://addons/agent_devtools/dev_tools.gd"\n\n'
        "[Devtools]\n\nallow_mutation=true\n\n" + DEVTOOLS + PLUGINS + "[rendering]"
    ) in after
    assert len(re.findall(r"^\[autoload\]", after, re.M)) == 2  # one is in the description
    assert "Up to date" in _installed(kit, target)


def test_your_agents_md_is_kept_and_the_kits_lands_beside_it(kit: Path, target: Path) -> None:
    mine = _write(target / "AGENTS.md", "# My rules\n")
    out = _installed(kit, target)
    assert "AGENTS.md      yours kept; the kit's is AGENTS.kit.md" in out
    assert _read(mine) == "# My rules\n"
    beside = target / "AGENTS.kit.md"
    assert _read(beside) == _read(kit / "AGENTS.md")
    assert "AGENTS.kit.md" in _manifest(target)["files"]
    assert "Up to date" in _installed(kit, target)

    # Merged and deleted: it stays away until the kit's AGENTS.md changes, and no line
    # points at a file that is not there.
    beside.unlink()
    out = _installed(kit, target)
    assert "Up to date" in out and "AGENTS" not in out
    assert not beside.exists()
    _write(kit / "AGENTS.md", "# AGENTS.md\n\nNewer rules.\n")
    _installed(kit, target)
    assert _read(beside) == "# AGENTS.md\n\nNewer rules.\n"
    assert _read(mine) == "# My rules\n"


def test_an_agents_md_from_the_kit_is_yours_after_the_first_run(kit: Path, target: Path) -> None:
    _installed(kit, target)
    agents = target / "AGENTS.md"
    assert _read(agents) == _read(kit / "AGENTS.md")
    _write(agents, "# AGENTS.md\n\nFilled in for my game.\n")
    assert "Up to date" in _installed(kit, target)
    assert not (target / "AGENTS.kit.md").exists()

    _write(kit / "AGENTS.md", "# AGENTS.md\n\nNewer rules.\n")
    out = _installed(kit, target)
    assert "the kit's changed since you got it: see AGENTS.kit.md" in out
    assert _read(target / "AGENTS.kit.md") == "# AGENTS.md\n\nNewer rules.\n"
    assert _read(agents) == "# AGENTS.md\n\nFilled in for my game.\n"
    out = _installed(kit, target)
    assert "Up to date" in out and "see AGENTS.kit.md" in out
    (target / "AGENTS.kit.md").unlink()
    out = _installed(kit, target)
    assert "Up to date" in out and "AGENTS" not in out


def test_crlf_project_godot_keeps_its_line_endings(kit: Path, target: Path) -> None:
    before = _project().replace("\n", "\r\n")
    _write(target / "project.godot", before)
    _write(target / ".gitignore", ".godot/\r\nartifacts\r\n")
    _installed(kit, target, "-StrictWarnings")
    after = _read(target / "project.godot")
    assert re.search(r"(?<!\r)\n", after) is None  # no bare LF crept in
    strict = "[debug]\n\n" + "".join(f"gdscript/warnings/{name}=2\n" for name in _strict_names(kit))
    lf = _project()
    cut = lf.index("[rendering]")
    expected = lf[:cut] + AUTOLOAD + strict + "\n" + DEVTOOLS + PLUGINS + lf[cut:]
    assert after == expected.replace("\n", "\r\n")
    # .gitignore: only what was missing, in the file's own line endings.
    assert _read(target / ".gitignore") == (
        ".godot/\r\nartifacts\r\n\r\n# godot-goblin-gearbox\r\n"
        "/.tools/\r\n.venv/\r\n__pycache__/\r\n"
    )


def _strict_names(kit: Path) -> list[str]:
    """The strict warnings in the order install.ps1 writes them: the one list the gates
    read too (Get-KitStrictWarnings), which is what the kit's own project.godot sets."""
    names = _kit_strict_warnings()
    assert len(names) == 16 and tuple(names) == STRICT_WARNINGS
    written = {
        key.removeprefix("gdscript/warnings/")
        for key in _section_keys(_read(kit / "project.godot"), "debug")
        if key != "gdscript/warnings/directory_rules"
    }
    assert written == set(names)
    return names


@functools.cache
def _kit_strict_warnings() -> list[str]:
    command = f". '{REPO / 'tools' / 'kit_config.ps1'}'; Get-KitStrictWarnings"
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    return asked.stdout.split()


def test_the_uv_add_line_names_the_packages_of_the_kits_own_pyproject() -> None:
    """install.ps1, doctor.ps1 and lint_gdscript.ps1 print it for a project whose
    pyproject.toml is its own. One list (Get-KitPythonTools), held to the kit's file."""
    import tomllib

    wanted = tomllib.loads(_read(REPO / "pyproject.toml"))["dependency-groups"]["dev"]
    command = f". '{REPO / 'tools' / 'kit_config.ps1'}'; Get-KitPythonToolsFix"
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    assert asked.stdout.strip() == "uv add --dev " + " ".join(f'"{name}"' for name in wanted)


def test_strict_warnings_sets_the_sixteen_and_says_what_it_replaced(
    kit: Path, target: Path
) -> None:
    debug = (
        "[debug]\n\n"
        'gdscript/warnings/directory_rules={\n"res://addons": 0,\n"res://addons/mine": 1\n}\n'
        "gdscript/warnings/untyped_declaration=1\n"
        "gdscript/warnings/unused_signal=2\n"
        "settings/stdout/verbose_stdout=true\n"
    )
    before = _project(debug=debug)
    _write(target / "project.godot", before)
    out = _installed(kit, target, "-StrictWarnings")
    assert "[debug] untyped_declaration 1 -> 2" in out
    assert "[debug] 14 strict warning(s) set to error" in out
    assert json.loads(_read(target / "kit.json"))["gdscript"] == {"strict": True}
    assert "gdscript.strict" not in out.split("Installed.")[1]  # no "turn it on" hint

    after = _read(target / "project.godot")
    values = dict(re.findall(r"^gdscript/warnings/(\w+)=(\d+)$", after, re.M))
    assert values == dict.fromkeys(_strict_names(kit), "2")
    # The project's own [debug] lines stay where they were; the new ones follow them.
    kept = debug.replace("untyped_declaration=1", "untyped_declaration=2")
    assert kept in after
    added = [name for name in _strict_names(kit) if f"warnings/{name}=" not in debug]
    cut = before.index("[rendering]")
    assert after == (
        before[: before.index("[debug]")]
        + AUTOLOAD
        + kept
        + "".join(f"gdscript/warnings/{name}=2\n" for name in added)
        + "\n"
        + DEVTOOLS
        + PLUGINS
        + before[cut:]
    )
    # The record names every line: the 14 it added, the one it replaced, and not the
    # project's own unused_signal=2. Undone entry by entry, the file is the project's again.
    record = _manifest(target)["wrote"]["project_godot"]
    assert record[6:] == ["[debug] untyped_declaration 1 -> 2"] + [
        f"[debug] {name} added" for name in added
    ]
    assert len(added) == 14 and "unused_signal" not in added
    assert "[debug] new section" not in record
    assert _without_kit_entries(after, record) == before
    assert "Up to date" in _installed(kit, target, "-StrictWarnings")
    assert _read(target / "project.godot") == after


def test_strict_warnings_later_patches_the_project_and_points_at_kit_json(
    kit: Path, target: Path
) -> None:
    _installed(kit, target)
    assert "[debug]" not in _read(target / "project.godot")
    kit_json = (target / "kit.json").read_bytes()
    out = _installed(kit, target, "-StrictWarnings")
    assert "[debug] 16 strict warning(s) set to error" in out
    assert 'kit.json is yours and has gdscript.strict false. Set it:  "gdscript"' in out
    assert (target / "kit.json").read_bytes() == kit_json  # never rewritten
    project = target / "project.godot"
    assert len(re.findall(r"^gdscript/warnings/\w+=2$", _read(project), re.M)) == 16
    record = _manifest(target)["wrote"]["project_godot"]
    names = _strict_names(kit)
    assert record[6:] == ["[debug] new section"] + [f"[debug] {name} added" for name in names]

    # Loosened by hand and set again: the record keeps one entry per warning, the first,
    # which is what the project had before the kit.
    text = _read(project).replace("warnings/unused_variable=2", "warnings/unused_variable=1")
    _write(project, text.replace("gdscript/warnings/unsafe_cast=2\n", ""))
    out = _installed(kit, target, "-StrictWarnings")
    assert "[debug] unused_variable 1 -> 2; [debug] 1 strict warning(s) set to error" in out
    assert _manifest(target)["wrote"]["project_godot"] == record
    assert _without_kit_entries(_read(project), record) == _project()


def test_dry_run_prints_the_plan_and_writes_nothing(kit: Path, target: Path) -> None:
    before = _tree(target)
    out = _installed(kit, target, "-DryRun", "-StrictWarnings")
    assert _tree(target) == before  # no file, no folder
    assert "(dry run: nothing is written)" in out and "Dry run: nothing was written." in out
    for line in (
        "+ tools/check.ps1",
        "+ addons/agent_devtools/dev_tools.gd.uid",
        "+ AGENTS.md",
        "+ .github/workflows/ci.yml",
        "kit.json       new: tests.root=test, gdscript.strict=true",
        "starter test   test/unit/test_kit_smoke.gd",
        "[autoload] DevTools; [editor_plugins] agent_devtools, gdUnit4; [devtools] allow_mutation",
        "[debug] 16 strict warning(s) set to error",
        ".gitignore     + .godot/ /artifacts/ /.tools/ .venv/ __pycache__/",
        "tools/kit_manifest.json   (commit it)",
    ):
        assert line in out, line
    assert "tools/tests/" not in out and PROJECT_DATA not in out

    # Also a dry run of an upgrade with a conflict: reported, exit 1, nothing written.
    _installed(kit, target)
    edited = target / "tools" / "check.ps1"
    edited.write_bytes(edited.read_bytes() + b"# mine\n")
    before = _tree(target)
    assert "tools/check.ps1" in _refused(kit, target, "-DryRun")
    assert _tree(target) == before


def test_what_the_project_already_has_is_kept(kit: Path, tmp_path: Path) -> None:
    uid = _read(kit / "addons" / "agent_devtools" / "dev_tools.gd.uid").strip()
    project = _project().replace(
        "[rendering]",
        f'[autoload]\n\nGame="*res://game.gd"\nDevTools="*{uid}"\n\n'
        '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/mine/plugin.cfg")\n\n'
        "[rendering]",
    )
    target = _target(tmp_path / "it's a [wip] game", project)
    own = {
        "addons/gdUnit4/plugin.cfg": '[plugin]\n\nname="gdUnit4"\nversion="5.0.3"\n',
        "pyproject.toml": '[project]\nname = "mine"\nversion = "1"\n',
        ".gdlintrc": "max-line-length: 120\n",
        "kit.json": '{"tests": {"root": "res://spec/"}, "gdscript": {"strict": false}}\n',
        "spec/suites/board_spec.gd": "extends GdUnitTestSuite\n",
        ".github/workflows/ci.yml": "name: mine\n",
        "docs/tools.md": "# My tools\n",
    }
    for relative, text in own.items():
        _write(target / relative, text)
    out = _installed(kit, target)
    for relative, text in own.items():
        assert _read(target / relative) == text, relative
    assert not (target / "addons" / "gdUnit4" / "LICENSE").exists()
    assert not (target / "uv.lock").exists()  # the kit's lock belongs to the kit's pyproject
    pinned = json.loads(_read(kit / "kit.json"))["gdunit4"]["version"]
    assert f"gdUnit4        yours kept: 5.0.3; the kit pins {pinned}" in out
    assert "uv run python tools/install_gdunit4.py --force" in out
    assert "pyproject.toml is yours and has no gdtoolkit" in out
    assert 'uv add --dev "gdtoolkit==' in out and '"ruff' in out
    assert "kit.json       yours kept" in out
    # tests.root is read from the project's kit.json; its suite needs no starter beside it.
    assert not (target / "spec" / "unit").exists() and "starter test" not in out
    assert "= docs/tools.md   (yours kept; the kit's copy is not installed)" in out
    # What it did not write is not on the list of what to undo.
    wrote = _manifest(target)["wrote"]
    assert wrote["gdunit4"] is False and wrote["kit_json"] is False and wrote["starter_test"] == ""
    assert wrote["project_godot"] == [
        "[editor_plugins] agent_devtools, gdUnit4",
        "[devtools] new section",
        "[devtools] allow_mutation=false",
    ]
    assert _without_kit_entries(_read(target / "project.godot"), wrote["project_godot"]) == project
    assert "gdscript.strict" not in out.split("Installed.")[0]  # its kit.json says: no note
    after = _read(target / "project.godot")
    assert f'DevTools="*{uid}"\n' in after and after.count("DevTools=") == 1
    assert (
        'enabled=PackedStringArray("res://addons/mine/plugin.cfg", '
        '"res://addons/agent_devtools/plugin.cfg", "res://addons/gdUnit4/plugin.cfg")\n' in after
    )
    assert after.count("[autoload]") == 2  # the one in the description, and the section
    assert "Up to date" in _installed(kit, target)


def test_a_kit_json_of_yours_that_does_not_say_strict_gets_a_note(kit: Path, target: Path) -> None:
    # No gdscript.strict means strict: the first check would fail on 16 warnings.
    _write(target / "kit.json", '{"tests": {"root": "test"}}\n')
    out = _installed(kit, target, "-DryRun")
    assert (
        "note: kit.json is yours and has no gdscript.strict, so the strict type check is on: "
        "16 of 16 strict warnings are not errors yet. To start with it off, add:  "
        '"gdscript": {"strict": false}'
    ) in out
    # Not when the run sets them, and not when they are set.
    assert "gdscript.strict" not in _installed(kit, target, "-DryRun", "-StrictWarnings")
    strict = "[debug]\n\n" + "".join(f"gdscript/warnings/{name}=2\n" for name in STRICT_WARNINGS)
    _write(target / "project.godot", _project(debug=strict))
    assert "gdscript.strict" not in _installed(kit, target, "-DryRun")


def test_the_manifest_lists_everything_removing_the_kit_has_to_undo(
    kit: Path, target: Path
) -> None:
    """README "Remove it", followed to the letter after an install and an upgrade: nothing
    of the kit's is left, nothing of the project's is lost."""
    debug = "[debug]\n\ngdscript/warnings/untyped_declaration=1\ngdscript/warnings/unsafe_cast=2\n"
    plugins = '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/mine/plugin.cfg")\n\n'
    project = _write(
        target / "project.godot",
        _project(debug=debug).replace("[rendering]", plugins + "[rendering]"),
    )
    ignore = _write(target / ".gitignore", ".godot/\n*.tmp\n")
    _write(target / "tools" / "build.ps1", "Write-Host 'my build'\n")
    before = _tree(target)

    _installed(kit, target)
    _write(target / "enemy.gd", "extends Node\n")  # the project goes on
    # The next kit: a tool and a starter file gone, one of each new; now with strict warnings.
    for gone in ("tools/lint_shaders.gd", "docs/gotchas.md"):
        (kit / gone).unlink()
    _write(kit / "tools" / "brand_new.ps1", "Write-Host 'new tool'\n")
    _write(kit / "docs" / "new_page.md", "# New in this kit\n")
    _write(kit / "VERSION", "9.9.9\n")
    _installed(kit, target, "-StrictWarnings")

    manifest = _manifest(target)
    wrote = manifest["wrote"]
    assert "unfinished" not in manifest  # else: run the installer again first
    # files: the kit's. Then the manifest.
    for relative in manifest["files"]:
        (target / relative).unlink()
    (target / "tools" / "kit_manifest.json").unlink()
    # wrote: kit.json, gdUnit4, the starter test with its .uid.
    assert wrote["kit_json"] is True and wrote["gdunit4"] is True
    (target / "kit.json").unlink()
    shutil.rmtree(target / "addons" / "gdUnit4")
    (target / wrote["starter_test"]).unlink()
    (target / (wrote["starter_test"] + ".uid")).unlink()
    # wrote.gitignore: those lines, the "# godot-goblin-gearbox" line and the blank line above it.
    lines = _read(ignore).split("\n")
    at = lines.index("# godot-goblin-gearbox")
    end = at + 1 + len(wrote["gitignore"])
    assert lines[at - 1] == "" and lines[at + 1 : end] == wrote["gitignore"]
    del lines[at - 1 : end]
    _write(ignore, "\n".join(lines))
    # wrote.project_godot: entry by entry.
    _write(project, _without_kit_entries(_read(project), wrote["project_godot"]))
    # seeded: the starter files, the one the newer kit dropped among them.
    assert "docs/gotchas.md" in manifest["seeded"] and "docs/new_page.md" in manifest["seeded"]
    for relative in manifest["seeded"]:
        (target / relative).unlink()
    # Folders left empty.
    for folder in sorted((path for path in target.rglob("*") if path.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()

    assert _tree(target) == before | {"enemy.gd": b"extends Node\n"}


@pytest.mark.parametrize(
    ("folder", "suite", "root", "starter"),
    [
        ("test", "test/unit/test_board.gd", "test", False),
        ("tests", "tests/test_board.gd", "tests", False),
        # A gdUnit4 suite under another name is a suite: its folder is the root.
        ("spec", "spec/BoardTest.gd", "spec", False),
        ("tests", "tests/helpers/fixtures.gd", "test", True),  # no suite there
    ],
)
def test_tests_root_is_where_the_suites_are(
    kit: Path, target: Path, folder: str, suite: str, root: str, starter: bool
) -> None:
    text = "extends GdUnitTestSuite\n" if "ixtures" not in suite else "extends RefCounted\n"
    _write(target / suite, text)
    out = _installed(kit, target)
    assert json.loads(_read(target / "kit.json"))["tests"] == {"root": root}
    assert (target / root / "unit" / "test_kit_smoke.gd").exists() == starter
    assert ("starter test" in out) == starter


REFUSALS = {
    "no project.godot": (
        lambda target: (target / "project.godot").unlink(),
        "no project.godot in '",
    ),
    "a Godot 3 project": (
        lambda target: _write(target / "project.godot", "config_version=4\n\n[application]\n"),
        "project.godot has config_version=4; a Godot 4 project has config_version=5",
    ),
    "another DevTools autoload": (
        lambda target: _write(
            target / "project.godot", _project() + '\n[autoload]\n\nDevTools="*res://mine.gd"\n'
        ),
        'project.godot already has an autoload named DevTools ("*res://mine.gd"). Rename it',
    ),
    "plugins not a list": (
        lambda target: _write(
            target / "project.godot", _project() + '\n[editor_plugins]\n\nenabled="all"\n'
        ),
        "project.godot [editor_plugins] enabled is not a PackedStringArray(...)",
    ),
    "a broken manifest": (
        lambda target: _write(target / "tools" / "kit_manifest.json", "{ not json"),
        "the target's tools/kit_manifest.json is not valid JSON",
    ),
    "a broken kit.json": (
        lambda target: _write(target / "kit.json", "{ not json"),
        "the target's kit.json is not valid JSON",
    ),
    "a test root outside the project": (
        lambda target: _write(target / "kit.json", '{"tests": {"root": "../elsewhere"}}\n'),
        "the target's kit.json tests.root must be a folder inside the project, not '../elsewhere'",
    ),
    "project.godot is not UTF-8": (
        lambda target: (target / "project.godot").write_bytes(b"config_version=5\n\xff\xfe\n"),
        "project.godot is not UTF-8 text",
    ),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_a_target_it_cannot_install_into_is_refused_in_one_line(
    kit: Path, target: Path, case: str
) -> None:
    arrange, message = REFUSALS[case]
    arrange(target)
    before = _tree(target)
    for args in ((), ("-DryRun",), ("-Force", "-StrictWarnings")):
        out = _refused(kit, target, *args)
        assert f"install: {message}" in out
        assert len(out.strip().splitlines()) == 1, out
        assert _tree(target) == before


def test_the_kit_itself_and_a_missing_target_are_refused(kit: Path, tmp_path: Path) -> None:
    before = _tree(kit)
    assert "install: -Target is the kit itself, or a folder inside it." in _refused(kit, kit)
    inside = _target(kit / "sample")
    assert "install: -Target is the kit itself, or a folder inside it." in _refused(kit, inside)
    shutil.rmtree(inside)
    assert _tree(kit) == before
    assert "install: no project.godot in '" in _refused(kit, tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists()
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(kit / "install.ps1")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1 and "install: name the game's folder" in result.stdout
    unknown = _install(kit, tmp_path / "nowhere", "-NoSuchSwitch")
    assert unknown.returncode != 0 and "NoSuchSwitch" in unknown.stderr
    assert unknown.stdout.strip() == ""


def test_get_help_shows_the_description() -> None:
    # A line of the help block that starts with a dot is read as a help keyword, and one
    # unknown keyword hides the whole block.
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", f"Get-Help '{REPO / 'install.ps1'}'"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    for shown in ("SYNOPSIS", "DESCRIPTION", "-DryRun", "Starter files", "Removing it", "Stopped"):
        assert shown in asked.stdout, shown


@pytest.mark.parametrize("missing", ["AGENTS.md", "CLAUDE.md", "docs", "addons/gdUnit4", "uv.lock"])
def test_an_incomplete_kit_copy_is_refused_up_front(kit: Path, target: Path, missing: str) -> None:
    path = kit / missing
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    before = _tree(target)
    out = _refused(kit, target)
    assert "install: this kit copy is incomplete" in out
    assert f"no {missing}" in out and "Clone or download the kit again." in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(target) == before


def test_a_relative_target_is_read_from_the_callers_folder(kit: Path, target: Path) -> None:
    # _install runs from the folder that holds both; the script lives elsewhere.
    out = _installed(kit, f".\\{target.name}", "-DryRun")
    assert f"-> {target}" in out


def test_other_godot_versions_and_csharp_are_warned_about(kit: Path, target: Path) -> None:
    project = _project().replace('"4.7", "Forward Plus"', '"4.6", "C#", "Forward Plus"')
    _write(target / "project.godot", project)
    out = _installed(kit, target)
    assert "note: project.godot is a Godot 4.6 project; the kit is built and tested for 4.7" in out
    assert "note: this is a C# project" in out and "GDScript only" in out
    assert json.loads(_read(target / "kit.json"))["godot"]["version"].startswith("4.7.")


def test_the_ci_workflow_runs_scripts_and_switches_that_exist() -> None:
    # The workflow is copied into projects and cannot be run here: pin what it calls.
    text = (REPO / ".github" / "workflows" / "ci.yml").read_bytes().decode("ascii")
    assert "\t" not in text and "\r" not in text.replace("\r\n", "\n")
    runs = re.findall(r"^ +run: \./(tools/\w+\.ps1)((?: -\w+(?: \d+)?)*)$", text, re.M)
    assert [script for script, _ in runs] == ["tools/setup.ps1", "tools/check.ps1"]
    assert text.count("run:") == len(runs) + 1  # the two steps and `defaults: run:`
    for script, arguments in runs:
        source = _read(REPO / script)
        for name, value in re.findall(r"-(\w+)(?: (\d+))?", arguments):
            kind = "int" if value else "switch"
            assert re.search(rf"\[{kind}\]\${name}\b", source), f"{script} -{name}"
    uses = re.findall(r"uses: ([\w./-]+)@(\S+)(.*)", text)
    assert [name for name, _, _ in uses] == [
        "actions/checkout",
        "astral-sh/setup-uv",
        "actions/cache",
        "actions/upload-artifact",
    ]
    # Pinned by commit (a tag can be moved), with the release it is in a comment.
    for name, ref, comment in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{name}@{ref}"
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), f"{name}: {comment!r}"
    for needed in (
        "runs-on: windows-latest",
        "shell: pwsh",
        "path: .tools/godot",
        "hashFiles('kit.json')",
        "if: failure()",
        "path: artifacts/",
    ):
        assert needed in text, needed


@pytest.mark.real_godot
def test_the_installed_project_loads_and_runs_its_starter_test(tmp_path: Path) -> None:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot binary: set GODOT_BIN, or run pwsh ./tools/setup.ps1 -InstallGodot")
    kit = _copy_kit(tmp_path / "kit's [copy]", whole_gdunit=True)
    target = _target(tmp_path / "it's a [wip] game")
    _installed(kit, target, "-StrictWarnings")
    # Godot's user data (logs, caches) goes to the temp folder, not the real profile.
    env = clean_env(GODOT_BIN=godot, APPDATA=str(tmp_path / "userdata"))

    imported = subprocess.run(
        [godot, "--headless", "--path", str(target), "--import"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    log = imported.stdout + imported.stderr
    assert imported.returncode == 0, log
    assert not re.search(r"^\s*(SCRIPT )?ERROR", log, re.M), log
    assert (target / ".godot" / "global_script_class_cache.cfg").is_file()

    for script, verdict in (
        ("typecheck.ps1", "Type-check: CLEAN"),
        ("test.ps1", "1 test cases | 0 errors | 0 failures"),
    ):
        result = run_tool(target, script, env=env, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        assert verdict in result.stdout, result.stdout
    listed = run_tool(
        target, "godot.ps1", "--headless", "--script", "res://tools/add_input_action.gd",
        "--user-args", "--list", env=env,
    )  # fmt: skip
    assert listed.returncode == 0 and "INPUT_ACTIONS 0" in listed.stdout, listed.stdout

    # Godot wrote .uid files for the project's own scripts; the kit's files are untouched.
    assert "Up to date" in _installed(kit, target, "-StrictWarnings")
    assert (target / "player.gd.uid").is_file()
