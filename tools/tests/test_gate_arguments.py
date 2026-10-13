"""Every gate refuses arguments it does not know, and reports a broken kit.json in one line.

A script with a plain param() block drops unknown arguments: `lint_gdscript.ps1 -Paths x`
linted everything and `check.ps1 -Fixx` ran as if nothing had been passed.
"""

from pathlib import Path

import pytest
from _gate_fixtures import (
    PWSH,
    REPO,
    WINDOWS,
    clean_env,
    copy_tools,
    fake_godot,
    project_godot,
    run_tool,
    write,
    write_kit_json,
)

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 gates")

# tools/godot.ps1 is not here: it forwards whatever it is given to Godot.
GATES = [
    "check.ps1",
    "doctor.ps1",
    "install_godot.ps1",
    "lint_gdscript.ps1",
    "lint_tests.ps1",
    "new_game.ps1",
    "setup.ps1",
    "test.ps1",
    "test_scripts_ascii.ps1",
    "typecheck.ps1",
]


def _project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "project"
    copy_tools(root, *GATES, "typecheck.gd")
    write_kit_json(root)
    write(root / "project.godot", project_godot())
    write(root / "scripts" / "player.gd", "extends Node\n")
    return root, clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")))


def test_gate_list_is_every_script_with_parameters() -> None:
    """A new tool must be added to GATES (or forward its arguments on purpose)."""
    libraries = {"kit_config.ps1", "godot_resolver.ps1", "class_cache.ps1", "export_paths.ps1"}
    scripts = {path.name for path in (REPO / "tools").glob("*.ps1")}
    assert scripts - libraries - {"godot.ps1"} == set(GATES)


@pytest.mark.parametrize("gate", GATES)
def test_unknown_switch_is_an_error(tmp_path: Path, gate: str) -> None:
    root, env = _project(tmp_path)
    extra = ("-Title", "Fine") if gate == "new_game.ps1" else ()
    result = run_tool(root, gate, *extra, "-NoSuchSwitch", env=env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "NoSuchSwitch" in result.stderr
    # Nothing ran: not one line of the tool's own output.
    assert result.stdout.strip() == ""
    assert 'config/name="Gate fixture"' in (root / "project.godot").read_text()


@pytest.mark.parametrize("gate", ["lint_gdscript.ps1", "typecheck.ps1", "check.ps1"])
def test_misspelled_path_switch_does_not_widen_the_run(tmp_path: Path, gate: str) -> None:
    root, env = _project(tmp_path)
    result = run_tool(root, gate, "-Paths", "no/such/folder", env=env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Paths" in result.stderr and result.stdout.strip() == ""


# doctor.ps1 and new_game.ps1 have their own cases; new_game.ps1 does not read kit.json.
KIT_JSON_READERS = [
    ("check.ps1", 2),
    ("install_godot.ps1", 1),
    ("lint_gdscript.ps1", 1),
    ("lint_tests.ps1", 2),
    ("setup.ps1", 1),
    ("test.ps1", 1),
    ("test_scripts_ascii.ps1", 1),
    ("typecheck.ps1", 1),
]


@pytest.mark.parametrize(("gate", "code"), KIT_JSON_READERS)
def test_broken_kit_json_is_one_line(tmp_path: Path, gate: str, code: int) -> None:
    root, env = _project(tmp_path)
    write(root / "kit.json", "{ this is not json")
    result = run_tool(root, gate, env=env)
    assert result.returncode == code, result.stdout + result.stderr
    assert "kit.json is not valid JSON" in result.stdout
    # A message, not a PowerShell error record with the throwing line and a caret.
    assert result.stderr.strip() == ""
    assert "kit_config.ps1" not in result.stdout
