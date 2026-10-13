"""The class-cache check scans the folders Godot scans, and no others."""

import os
import subprocess
from pathlib import Path

import pytest
from _gate_fixtures import NEEDS_PWSH, REPO, clean_env, run_powershell

CLASS_CACHE = REPO / "tools" / "class_cache.ps1"

pytestmark = NEEDS_PWSH


def _script(path: Path, class_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"class_name {class_name}\nextends RefCounted\n", encoding="ascii")


def _run(root: Path, call: str) -> str:
    env = clean_env(KIT_CLASS_CACHE=str(CLASS_CACHE), KIT_CLASS_CACHE_ROOT=str(root))
    result = run_powershell(
        f". $env:KIT_CLASS_CACHE; {call} -ProjectRoot $env:KIT_CLASS_CACHE_ROOT", env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _entries(root: Path) -> list[str]:
    lines = _run(root, "Get-GodotClassScriptEntries").splitlines()
    return [line.strip() for line in lines if line.strip()]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """One real script, plus scripts in every kind of folder Godot never scans."""
    root = tmp_path / "checkout"
    _script(root / "scripts" / "real.gd", "Real")
    _script(root / ".agent" / "worktrees" / "feature" / "scripts" / "real.gd", "Real")
    _script(root / ".agent" / "worktrees" / "feature" / "scripts" / "new.gd", "OnlyThere")
    _script(root / "docs" / "sample.gd", "Ignored")
    (root / "docs" / ".gdignore").write_text("", encoding="ascii")
    _script(root / "experiments" / "other" / "nested.gd", "Nested")
    (root / "experiments" / "other" / "project.godot").write_text("", encoding="ascii")
    (root / "project.godot").write_text("config_version=5\n", encoding="ascii")
    (root / ".godot").mkdir()
    (root / ".godot" / "global_script_class_cache.cfg").write_text(
        'list=[{\n"base": &"RefCounted",\n"class": &"Real",\n"path": "res://scripts/real.gd"\n}]\n',
        encoding="ascii",
    )
    return root


def test_scan_skips_folders_godot_skips(project: Path) -> None:
    """Hidden folders (agent worktrees), .gdignore folders and nested projects."""
    assert _entries(project) == ["Real res://scripts/real.gd"]


def test_scan_keeps_ordinary_nested_folders_and_addons(project: Path) -> None:
    _script(project / "scripts" / "deep" / "deeper" / "leaf.gd", "Leaf")
    _script(project / "addons" / "plugin" / "tool.gd", "PluginTool")
    entries = _entries(project)
    assert "Leaf res://scripts/deep/deeper/leaf.gd" in entries
    # Godot registers addon classes too, whatever kit.json source.exclude says.
    assert "PluginTool res://addons/plugin/tool.gd" in entries


def test_scan_reads_the_extension_in_any_letter_case_and_the_keyword_in_one(project: Path) -> None:
    """Godot registers the class of Loud.GD, on Linux too (measured with 4.7.2): left out
    of the scan it would be "only in cache" for ever, and every run would import again.
    `Class_Name` is no keyword. tools/ci/class_cache.py reads both the same way."""
    _script(project / "scripts" / "Loud.GD", "Loud")
    shouted = "Class_Name Shouted\nextends RefCounted\n"
    (project / "scripts" / "shouted.gd").write_text(shouted, encoding="ascii")
    found = sorted(_entries(project))
    assert found == ["Loud res://scripts/Loud.GD", "Real res://scripts/real.gd"]


def test_scan_does_not_follow_a_link_to_an_ancestor(project: Path) -> None:
    target = project / "scripts"
    link = target / "loop"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    assert _entries(project) == ["Real res://scripts/real.gd"]


def test_hidden_checkouts_do_not_make_the_cache_stale(project: Path) -> None:
    assert _run(project, "Test-GodotClassCacheStale").strip() == "False"


def test_new_class_script_makes_the_cache_stale(project: Path) -> None:
    _script(project / "scripts" / "added.gd", "Added")
    assert _run(project, "Test-GodotClassCacheStale").strip() == "True"
    diff = _run(project, "Get-GodotClassCacheDiff").strip()
    assert diff == "missing from cache: Added res://scripts/added.gd"


def test_moved_and_deleted_class_scripts_make_the_cache_stale(project: Path) -> None:
    (project / "scripts" / "real.gd").rename(project / "scripts" / "moved.gd")
    diff = _run(project, "Get-GodotClassCacheDiff").splitlines()
    assert [line.strip() for line in diff if line.strip()] == [
        "missing from cache: Real res://scripts/moved.gd",
        "only in cache: Real res://scripts/real.gd",
    ]


def test_missing_cache_is_stale(project: Path) -> None:
    (project / ".godot" / "global_script_class_cache.cfg").unlink()
    assert _run(project, "Test-GodotClassCacheStale").strip() == "True"


def test_project_without_class_names_and_an_empty_cache_is_fresh(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "plain.gd").write_text("extends Node\n", encoding="ascii")
    (root / ".godot").mkdir()
    (root / ".godot" / "global_script_class_cache.cfg").write_text("list=[]\n", encoding="ascii")
    assert _run(root, "Test-GodotClassCacheStale").strip() == "False"


def test_other_languages_in_the_cache_are_ignored(project: Path) -> None:
    """The disk scan reads .gd files, so only GDScript cache entries are compared."""
    cache = project / ".godot" / "global_script_class_cache.cfg"
    cache.write_text(
        'list=[{\n"class": &"Real",\n"path": "res://scripts/real.gd"\n}, '
        '{\n"class": &"Sharp",\n"path": "res://scripts/Sharp.cs"\n}]\n',
        encoding="ascii",
    )
    assert _run(project, "Test-GodotClassCacheStale").strip() == "False"
