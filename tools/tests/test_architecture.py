"""tools/check_architecture.py: a clean tree passes; each planted defect fails."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import check_architecture as arch  # noqa: E402

MANIFEST = {
    "_readme": ["keys that start with _ are comments"],
    "modules": {
        "core": {"globs": ["scripts/core/**"], "may_depend": [], "pure": True},
        "sim": {
            "_note": "rules",
            "globs": ["scripts/sim/**"],
            "may_depend": ["core"],
            "pure": True,
        },
        "net_server": {
            "globs": ["scripts/net/server.gd", {"glob": "scripts/server/**", "optional": True}],
            "may_depend": ["core", "sim"],
        },
        "net": {"globs": ["scripts/net/**"], "may_depend": ["core"]},
        "ui": {"globs": ["ui/**", {"glob": "*.gd", "optional": True}], "may_depend": ["core"]},
        "tests": {"globs": ["test/**"], "may_depend": ["*"]},
    },
    "ignore": ["tools/**"],
}
BASE = {
    "scripts/core/grid.gd": "class_name Grid\nextends RefCounted\n",
    "scripts/sim/world.gd": "class_name World\nextends RefCounted\nvar g := Grid.new()\n",
    "scripts/net/server.gd": "class_name Server\nvar w := World.new()\n",
    "scripts/net/wire.gd": "class_name Wire\nextends RefCounted\n",
    "ui/hud.gd": "class_name Hud\nextends Control\nvar g := Grid.new()\n",
    "test/unit/test_all.gd": "extends GdUnitTestSuite\nvar h := Hud.new()\nvar s := Server.new()\n",
    "tools/helper.gd": "extends SceneTree\nvar h := Hud.new()\n",
    "addons/lib/thing.gd": "class_name LibNode\nextends Node\nvar h := Hud.new()\n",
}
PROJECT = '[application]\n\n[autoload]\n\nDevTools="*res://addons/dev/dev_tools.gd"\n\n[debug]\n'


def _tree(
    root: Path, files: dict[str, str | None], allow: str | None = None, manifest: dict | None = None
) -> Path:
    for name, text in {**BASE, **files}.items():
        path = root / name
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    ci = root / "tools" / "ci"
    ci.mkdir(parents=True, exist_ok=True)
    (root / arch.MANIFEST).write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")
    shutil.copy2(TOOLS / "ci" / "node_classes.txt", ci / "node_classes.txt")
    if allow is not None:
        (root / arch.ALLOWLIST).write_text(allow, encoding="utf-8")
    (root / "project.godot").write_text(PROJECT, encoding="utf-8")
    return root


def _has(problems: list[str], needle: str) -> bool:
    return any(needle in problem for problem in problems)


def test_no_manifest_means_not_configured_and_passes(tmp_path: Path, capsys) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.gd").write_text("extends Node\n", encoding="utf-8")
    assert arch.main([], root=tmp_path) == 0
    out = capsys.readouterr().out
    assert out.strip() == "architecture: not configured (no tools/ci/architecture_modules.json)"


def test_a_clean_tree_passes(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path, {})
    assert arch.check(root) == []
    assert arch.main([], root=root) == 0
    assert capsys.readouterr().out.strip() == "architecture: ok"


def test_the_kits_own_manifest_is_well_formed() -> None:
    manifest = arch.load_manifest(TOOLS.parent / arch.MANIFEST)
    assert manifest["modules"]["core"] == {
        "globs": [("scripts/core/**", False)],
        "may_depend": [],
        "pure": True,
    }
    assert manifest["modules"]["game"]["may_depend"] == ["core"]
    assert manifest["modules"]["tests"]["may_depend"] == ["*"]
    made_for, names = arch.load_node_classes(TOOLS.parent / arch.NODE_CLASSES)
    assert made_for == arch.kit_config.load().godot_minor
    assert {"Node", "Node2D", "Control", "Marker3D", "TileMapLayer", "Window"} <= names
    assert not {"RefCounted", "Resource", "Object", "Tween"} & names


def test_a_lower_layer_naming_a_higher_one_fails(tmp_path: Path, capsys) -> None:
    files = {"scripts/sim/world.gd": "class_name World\nvar s := Server.new()\n"}
    root = _tree(tmp_path, files)
    assert arch.check(root) == ["not allowed: scripts/sim/world.gd -> scripts/net/server.gd"]
    assert arch.main([], root=root) == 1
    assert "architecture: FAIL (1)" in capsys.readouterr().out


def test_siblings_that_may_not_name_each_other_fail(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"ui/hud.gd": "class_name Hud\nvar w := Wire.new()\n"})
    assert arch.check(root) == ["not allowed: ui/hud.gd -> scripts/net/wire.gd"]


def test_the_first_matching_module_owns_a_script(tmp_path: Path) -> None:
    # server.gd is in net_server (may use sim) though scripts/net/** also matches it;
    # wire.gd falls through to net (may not).
    root = _tree(tmp_path, {"scripts/net/wire.gd": "class_name Wire\nvar w := World.new()\n"})
    assert arch.check(root) == ["not allowed: scripts/net/wire.gd -> scripts/sim/world.gd"]


@pytest.mark.parametrize(
    "reference",
    [
        'const Hud := preload("res://ui/hud.gd")',
        "var hud = load('res://ui/hud.gd')",
        'const Hud := preload("uid://hud1234")',
        'extends "res://ui/hud.gd"',
        "func f() -> void:\n\tDevTools.ping()",
    ],
)
def test_every_way_of_naming_a_script_counts(tmp_path: Path, reference: str) -> None:
    files = {
        "scripts/sim/world.gd": f"{reference}\n",
        "ui/hud.gd.uid": "uid://hud1234\n",
        "ui/hud.gd": "extends RefCounted\n",
        "ui/dev_tools.gd": "extends RefCounted\n",
    }
    root = _tree(tmp_path, files)
    (root / "project.godot").write_text(
        '[autoload]\n\nDevTools="*res://ui/dev_tools.gd"\n', encoding="utf-8"
    )
    target = "ui/dev_tools.gd" if "DevTools" in reference else "ui/hud.gd"
    assert _has(arch.check(root), f"not allowed: scripts/sim/world.gd -> {target}")


def test_names_in_comments_and_strings_do_not_count(tmp_path: Path) -> None:
    text = (
        "class_name World\n"
        '# Hud draws this; see preload("res://ui/hud.gd")\n'
        'var note := "Hud and Server"  # Server\n'
        'var scene := "res://ui/hud.tscn"\n'  # a scene, not a script: not followed
    )
    assert arch.check(_tree(tmp_path, {"scripts/sim/world.gd": text})) == []


@pytest.mark.parametrize(
    "text",
    [
        'const PATH := "res://ui/hud.gd"\n\n\nfunc make() -> Variant:\n\treturn load(PATH)\n',
        "var scripts := ['res://ui/hud.gd', 'res://scripts/core/grid.gd']\n",
        'const ID := "uid://hud1234"\n',
    ],
)
def test_a_script_path_in_any_string_counts(tmp_path: Path, text: str) -> None:
    # `load(PATH)` with the path in a constant reached the script unseen.
    files = {"scripts/sim/world.gd": text, "ui/hud.gd.uid": "uid://hud1234\n"}
    assert arch.check(_tree(tmp_path, files)) == ["not allowed: scripts/sim/world.gd -> ui/hud.gd"]


def test_ignored_and_unscanned_scripts_are_left_alone(tmp_path: Path) -> None:
    # tools/** is ignored by the manifest, addons/ is not authored source: both name Hud.
    root = _tree(tmp_path, {})
    tree_problems = arch.check(root)
    assert tree_problems == []
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["ignore"] = []
    problems = arch.check(_tree(tmp_path, {}, manifest=manifest))
    assert problems == [
        "no module: tools/helper.gd (add it to a module or to ignore in "
        "tools/ci/architecture_modules.json)"
    ]


def test_an_allowlisted_edge_passes_then_goes_stale(tmp_path: Path) -> None:
    files = {"scripts/sim/world.gd": "class_name World\nvar w := Wire.new()\n"}
    line = "scripts/sim/world.gd -> scripts/net/wire.gd"
    root = _tree(tmp_path, files, f"# moving in the next release\n{line}  # why\n")
    assert arch.check(root) == []
    _tree(tmp_path, {"scripts/sim/world.gd": "class_name World\n"})
    assert arch.check(root) == [f"stale allowlist line (remove it, or run --shrink): {line}"]


def test_shrink_removes_stale_lines_only(tmp_path: Path) -> None:
    files = {"scripts/sim/world.gd": "class_name World\nvar w := Wire.new()\n"}
    keep = "scripts/sim/world.gd -> scripts/net/wire.gd"
    root = _tree(tmp_path, files, f"# keep me\n{keep}\nscripts/sim/a.gd -> ui/b.gd\n")
    assert arch.main(["--shrink"], root=root) == 0
    assert (root / arch.ALLOWLIST).read_text(encoding="utf-8") == f"# keep me\n{keep}\n"


def test_baseline_records_todays_violations_and_new_ones_still_fail(tmp_path: Path) -> None:
    files = {"scripts/sim/world.gd": "class_name World\nvar w := Wire.new()\n"}
    root = _tree(tmp_path, files)
    assert arch.main([], root=root) == 1
    assert not (root / arch.ALLOWLIST).exists()  # a plain run writes nothing
    assert arch.main(["--baseline"], root=root) == 0
    listed = arch.parse_allowlist((root / arch.ALLOWLIST).read_text(encoding="utf-8"))
    assert listed == ["scripts/sim/world.gd -> scripts/net/wire.gd"]
    assert arch.main([], root=root) == 0
    files["scripts/core/grid.gd"] = "class_name Grid\nvar w := World.new()\n"
    _tree(tmp_path, files)
    assert arch.check(root) == ["not allowed: scripts/core/grid.gd -> scripts/sim/world.gd"]


def test_allowlist_lines_parse() -> None:
    text = "# c\n  a.gd  ->  b.gd \n\nc.gd -> d.gd  # until the move\n"
    assert arch.parse_allowlist(text) == ["a.gd -> b.gd", "c.gd -> d.gd"]


def test_signal_in_a_pure_module_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"scripts/sim/world.gd": "class_name World\nsignal done(x: int)\n"})
    assert arch.check(root) == ["scripts/sim/world.gd: signal in pure module sim"]
    # Not pure: fine.
    assert arch.check(_tree(tmp_path / "b", {"ui/hud.gd": "class_name Hud\nsignal done\n"})) == []


@pytest.mark.parametrize(
    "header",
    [
        "extends Node3D",
        "extends Marker2D",  # not caught by a guess at Node names: it is in the engine's list
        "class_name World extends CanvasLayer",
        "@tool\nextends TileMapLayer",
        'extends "res://ui/hud.gd"',  # Hud extends Control
    ],
)
def test_a_node_in_a_pure_module_fails(tmp_path: Path, header: str) -> None:
    root = _tree(tmp_path, {"scripts/core/grid.gd": f"{header}\n"})
    assert _has(arch.check(root), "a Node, in pure module core")


def test_a_node_through_other_scripts_or_an_inner_class_fails(tmp_path: Path) -> None:
    files = {
        "scripts/sim/base.gd": "class_name Base\nextends Node\n",
        "scripts/sim/middle.gd": "class_name Middle\nextends Base\n",
        "scripts/sim/world.gd": "class_name World\nextends Middle\n",
        "scripts/core/grid.gd": "class_name Grid\n\n\nclass Helper extends Timer:\n\tpass\n",
        # LibNode is declared in addons/ (not authored source) and extends Node.
        "scripts/core/uses_lib.gd": "extends LibNode\n",
    }
    problems = arch.check(_tree(tmp_path, files))
    for path, base in (
        ("scripts/sim/base.gd", "Node"),
        ("scripts/sim/middle.gd", "Base"),
        ("scripts/sim/world.gd", "Middle"),
        ("scripts/core/grid.gd", "Timer"),
        ("scripts/core/uses_lib.gd", "LibNode"),
    ):
        assert _has(problems, f"{path}: extends {base}, a Node, in pure module"), path


def test_plain_objects_in_a_pure_module_pass(tmp_path: Path) -> None:
    files = {
        "scripts/core/a.gd": "extends Resource\n",
        "scripts/core/b.gd": "class_name B\n",  # no extends: a RefCounted
        "scripts/core/c.gd": "extends B\n\n\nclass Inner extends Object:\n\tpass\n",
        "scripts/core/loop_a.gd": "class_name LoopA\nextends LoopB\n",
        "scripts/core/loop_b.gd": "class_name LoopB\nextends LoopA\n",
    }
    assert arch.check(_tree(tmp_path, files)) == []


def test_an_autoload_in_a_pure_module_fails(tmp_path: Path) -> None:
    text = "class_name World\nfunc f() -> void:\n\tDevTools.log()\n"
    root = _tree(tmp_path, {"scripts/sim/world.gd": text})
    assert arch.check(root) == ["scripts/sim/world.gd: autoload DevTools used in pure module sim"]


def test_a_node_class_list_for_another_godot_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {})
    listing = root / arch.NODE_CLASSES
    listing.write_text("# Godot 4.2: old\nNode\n", encoding="utf-8")
    assert _has(arch.check(root), "lists Godot 4.2 classes but kit.json pins 4.7")
    listing.unlink()
    assert arch.main([], root=root) == 2
    # No pure module: the list is not needed.
    manifest = json.loads(json.dumps(MANIFEST))
    for module in manifest["modules"].values():
        module.pop("pure", None)
    assert arch.check(_tree(tmp_path / "b", {}, manifest=manifest)) == []
    (tmp_path / "b" / arch.NODE_CLASSES).unlink()
    assert arch.check(tmp_path / "b") == []


def test_update_node_classes_runs_the_godot_the_resolver_finds(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """No option names the binary: `--godot <exe>` would run any program."""
    root = _tree(tmp_path, {})
    with pytest.raises(SystemExit) as stopped:
        arch.main(["--update-node-classes", "--godot", "calc.exe"], root=root)
    assert stopped.value.code == 2
    assert "unrecognized arguments: --godot calc.exe" in capsys.readouterr().err

    asked: list[tuple] = []
    started: list[list[str]] = []
    api = {
        "header": {"version_major": 4, "version_minor": 7},
        "classes": [
            {"name": "Object"},
            {"name": "Node", "inherits": "Object"},
            {"name": "Label", "inherits": "Node"},
            {"name": "Resource", "inherits": "Object"},
        ],
    }

    def resolve(*given) -> str:
        asked.append(given)
        return "the resolver's godot"

    def run_captured(command: list[str], _timeout: float, cwd: Path) -> tuple[int, str]:
        started.append(command)
        (cwd / "extension_api.json").write_text(json.dumps(api), encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(arch.godot_binary, "resolve", resolve)
    monkeypatch.setattr(arch.slot_pool, "run_captured", run_captured)
    assert arch.main(["--update-node-classes"], root=root) == 0
    assert asked == [(arch.kit_config.load(root),)]
    assert started == [["the resolver's godot", "--headless", "--dump-extension-api"]]
    assert arch.load_node_classes(root / arch.NODE_CLASSES) == ("4.7", frozenset({"Node", "Label"}))


def test_preload_cycle_fails(tmp_path: Path) -> None:
    files = {
        "scripts/core/a.gd": 'const B := preload("res://scripts/core/b.gd")\n',
        "scripts/core/b.gd": 'const C := preload("res://scripts/core/c.gd")\n',
        "scripts/core/c.gd": 'const A := preload("res://scripts/core/a.gd")\n',
        "scripts/core/d.gd": 'const A := preload("res://scripts/core/a.gd")\n',
    }
    assert arch.check(_tree(tmp_path, files)) == [
        "preload cycle: scripts/core/a.gd <-> scripts/core/b.gd <-> scripts/core/c.gd"
    ]


RING = {
    "scripts/core/a.gd": 'const B := preload("res://scripts/core/b.gd")\n',
    "scripts/core/b.gd": 'const A := preload("res://scripts/core/a.gd")\n',
}
RING_LINE = "scripts/core/a.gd <-> scripts/core/b.gd"


def test_the_cycle_rule_can_be_switched_off(tmp_path: Path, capsys) -> None:
    # Godot loads a ring of scripts, so a project may decide to live with them.
    manifest = json.loads(json.dumps(MANIFEST)) | {"cycles": False}
    assert arch.check(_tree(tmp_path, RING, manifest=manifest)) == []
    manifest["cycles"] = "no"
    assert arch.main([], root=_tree(tmp_path, RING, manifest=manifest)) == 2
    assert '"cycles" must be true or false' in capsys.readouterr().err


def test_baseline_adopts_a_ring_and_the_line_goes_stale_when_the_ring_is_gone(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, RING)
    assert arch.check(root) == [f"preload cycle: {RING_LINE}"]
    assert arch.main(["--baseline"], root=root) == 0
    assert arch.parse_allowlist((root / arch.ALLOWLIST).read_text("utf-8")) == [RING_LINE]
    assert arch.check(root) == []
    # A ring that grows is a new ring: the old line no longer covers it.
    _tree(tmp_path, {"scripts/core/b.gd": 'const C := preload("res://scripts/core/c.gd")\n'})
    _tree(tmp_path, {"scripts/core/c.gd": 'const A := preload("res://scripts/core/a.gd")\n'})
    assert arch.check(root) == [
        f"stale allowlist line (remove it, or run --shrink): {RING_LINE}",
        "preload cycle: scripts/core/a.gd <-> scripts/core/b.gd <-> scripts/core/c.gd",
    ]
    _tree(tmp_path, {"scripts/core/b.gd": "extends RefCounted\n", "scripts/core/c.gd": None})
    assert arch.check(root) == [f"stale allowlist line (remove it, or run --shrink): {RING_LINE}"]
    assert arch.main(["--shrink"], root=root) == 0


def test_extending_each_other_through_preloads_is_a_ring_too(tmp_path: Path) -> None:
    files = {
        "scripts/core/a.gd": 'extends "res://scripts/core/b.gd"\n',
        "scripts/core/b.gd": 'const A := preload("res://scripts/core/a.gd")\n',
    }
    assert arch.check(_tree(tmp_path, files)) == [f"preload cycle: {RING_LINE}"]


def test_load_and_class_names_may_be_circular(tmp_path: Path) -> None:
    files = {
        "scripts/core/a.gd": 'class_name A\nvar b := load("res://scripts/core/b.gd")\nvar x: B2\n',
        "scripts/core/b.gd": 'class_name B2\nconst A1 := preload("res://scripts/core/a.gd")\n',
    }
    assert arch.check(_tree(tmp_path, files)) == []


def test_autoloads_are_read_past_a_value_that_spans_lines(tmp_path: Path) -> None:
    """A description may hold line breaks and a line that looks like a section header:
    read as one, it would hide every autoload after it, and the purity rule with them."""
    (tmp_path / "project.godot").write_text(
        "config_version=5\n\n[application]\n\n"
        'config/description="Line one\n[autoload]\nline three"\n\n'
        '[autoload]\n\nGame="*res://scripts/game.gd"\nById="*uid://abc123"\n\n[rendering]\n',
        encoding="utf-8",
    )
    assert arch.project_refs.autoloads(tmp_path) == {
        "Game": "res://scripts/game.gd",
        "ById": "uid://abc123",
    }


def test_unassigned_script_and_dead_glob_fail(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"util/orphan.gd": "extends RefCounted\n"})
    # Both name the file that holds the fix.
    where = "tools/ci/architecture_modules.json"
    assert arch.check(root) == [
        f"no module: util/orphan.gd (add it to a module or to ignore in {where})"
    ]
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["modules"]["ui"]["globs"].append("ui/missing/**")
    manifest["ignore"].append("util/**")
    problems = arch.check(_tree(tmp_path, {}, manifest=manifest))
    assert problems == [f"glob matches no script: module ui: ui/missing/** (fix it in {where})"]


def test_optional_globs_may_match_nothing(tmp_path: Path) -> None:
    # MANIFEST has two optional globs with no match: scripts/server/** and *.gd.
    assert arch.check(_tree(tmp_path, {})) == []
    root = _tree(tmp_path, {"main.gd": "extends Node\nvar w := Wire.new()\n"})
    assert arch.check(root) == ["not allowed: main.gd -> scripts/net/wire.gd"]


def test_globs() -> None:
    assert arch.glob_regex("scripts/core/**").match("scripts/core/a/b.gd")
    assert not arch.glob_regex("scripts/core/**").match("scripts/core2/a.gd")
    assert arch.glob_regex("*.gd").match("main.gd")
    assert not arch.glob_regex("*.gd").match("scripts/main.gd")
    assert arch.glob_regex("scripts/*/api.gd").match("scripts/net/api.gd")
    assert not arch.glob_regex("scripts/*/api.gd").match("scripts/net/v2/api.gd")
    assert not arch.glob_regex("a.gd").match("aXgd")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: m.pop("modules"), '"modules" must be an object'),
        (lambda m: m["modules"]["ui"].pop("globs"), "module ui needs a non-empty"),
        (lambda m: m["modules"]["ui"].update(may_depend=["cor"]), "unknown module 'cor'"),
        (lambda m: m["modules"]["ui"].update(may_depends=["core"]), "unknown key(s) may_depends"),
        (lambda m: m.update(ignored=["x/**"]), "unknown key(s) ignored"),
        (lambda m: m["modules"]["ui"].update(globs=[{"path": "ui/**"}]), "bad glob entry"),
        (lambda m: m.update(ignore="tools/**"), '"ignore" must be a list'),
    ],
)
def test_a_wrong_manifest_is_an_error_not_a_pass(tmp_path: Path, capsys, change, message) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    change(manifest)
    root = _tree(tmp_path, {}, manifest=manifest)
    assert arch.main([], root=root) == 2
    assert message in capsys.readouterr().err


def test_a_manifest_that_is_not_json_is_an_error(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path, {})
    (root / arch.MANIFEST).write_text("{ modules: }", encoding="utf-8")
    assert arch.main([], root=root) == 2
    assert "architecture_modules.json" in capsys.readouterr().err


def test_node_classes_come_from_the_engines_own_class_list() -> None:
    api = {
        "classes": [
            {"name": "Object"},
            {"name": "RefCounted", "inherits": "Object"},
            {"name": "Node", "inherits": "Object"},
            {"name": "Node2D", "inherits": "CanvasItem"},
            {"name": "CanvasItem", "inherits": "Node"},
            {"name": "Resource", "inherits": "RefCounted"},
        ]
    }
    assert arch.node_classes_from_api(api) == ["CanvasItem", "Node", "Node2D"]
