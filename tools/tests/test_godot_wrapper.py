"""tools/godot.ps1: Godot's exit code comes back, whichever of its two binaries runs."""

from pathlib import Path

import pytest
from _gate_fixtures import (
    EMPTY_CLASS_CACHE,
    PWSH,
    WINDOWS,
    clean_env,
    copy_tools,
    fake_godot,
    lone_gui_godot,
    project_godot,
    real_godot,
    run_tool,
    write,
    write_kit_json,
)

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 tools")

QUIT_7 = 'extends SceneTree\n\n\nfunc _init() -> void:\n\tprint("QUIT_7_RAN")\n\tquit(7)\n'


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    copy_tools(root, "godot.ps1", "export_paths.ps1")
    write_kit_json(root)
    write(root / "project.godot", project_godot())
    write(root / "quit_7.gd", QUIT_7)
    write(root / ".godot" / "global_script_class_cache.cfg", EMPTY_CLASS_CACHE)
    return root


def _log(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def test_exit_code_and_arguments_are_forwarded(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))

    ok = run_tool(root, "godot.ps1", "--headless", "--quit", env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "FAKE_GODOT --headless --quit" in ok.stdout  # Godot's output is shown

    failing = env | {"FAKE_GODOT_BREAK": "lint.gd", "FAKE_GODOT_EXIT": "7"}
    args = ("--headless", "--script", "res://lint.gd", "--user-args", "--all", "a b")
    failed = run_tool(root, "godot.ps1", *args, env=failing)
    assert failed.returncode == 7, failed.stdout + failed.stderr
    # --user-args became the bare -- that PowerShell would have swallowed.
    assert _log(log)[-1].split() == [
        "--headless",
        "--script",
        "res://lint.gd",
        "--",
        "--all",
        '"a',
        'b"',
    ]

    twice = run_tool(root, "godot.ps1", "--user-args", "a", "--user-args", "b", env=env)
    assert twice.returncode == 1 and "--user-args may be supplied only once" in twice.stdout


@pytest.mark.parametrize("switch", ["-?", "-h", "/?"])
def test_a_lone_help_switch_prints_usage_and_starts_no_game(tmp_path: Path, switch: str) -> None:
    """Every argument is Godot's, and Godot given `-?` opens the game window and stays."""
    root = _project(tmp_path)
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))
    asked = run_tool(root, "godot.ps1", switch, env=env)
    assert asked.returncode == 0, asked.stdout + asked.stderr
    assert asked.stdout.startswith("usage: pwsh ./tools/godot.ps1 [godot arguments]")
    assert "--help: Godot's own options" in asked.stdout
    assert _log(log) == []


def test_stale_class_cache_is_refreshed_first(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "scripts" / "added.gd", "class_name Added\nextends RefCounted\n")
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))

    result = run_tool(root, "godot.ps1", "--headless", "--quit", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Class cache is stale" in result.stdout
    assert _log(log)[0] == "--headless --import"
    assert _log(log)[-1] == "--headless --quit"

    # An explicit import is the refresh: no second one in front of it.
    log.unlink()
    imported = run_tool(root, "godot.ps1", "--headless", "--import", env=env)
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert _log(log) == ["--headless --import"]


def test_wrapper_problems_are_one_line_and_exit_1(tmp_path: Path) -> None:
    root = _project(tmp_path)
    missing = run_tool(root, "godot.ps1", env=clean_env(GODOT_BIN=str(tmp_path / "gone.exe")))
    assert missing.returncode == 1, missing.stdout + missing.stderr
    assert "godot.ps1: GODOT_BIN does not name an executable file" in missing.stdout
    assert missing.stderr.strip() == ""  # no PowerShell error record around it

    (root / "project.godot").unlink()
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")))
    no_project = run_tool(root, "godot.ps1", env=env)
    assert no_project.returncode == 1 and "Godot project not found" in no_project.stdout


@pytest.mark.real_godot
def test_real_godot_exit_code_through_the_console_wrapper(tmp_path: Path) -> None:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    root = _project(tmp_path)
    args = ("--headless", "--script", "res://quit_7.gd")
    result = run_tool(root, "godot.ps1", *args, env=clean_env(GODOT_BIN=godot))
    assert result.returncode == 7, result.stdout + result.stderr
    assert "QUIT_7_RAN" in result.stdout

    # Naming the windowed .exe of the same install is the same thing.
    if godot.lower().endswith("_console.exe"):
        windowed = godot[: -len("_console.exe")] + ".exe"
        result = run_tool(root, "godot.ps1", *args, env=clean_env(GODOT_BIN=windowed))
        assert result.returncode == 7, result.stdout + result.stderr
        assert "QUIT_7_RAN" in result.stdout


@pytest.mark.real_godot
def test_real_windowed_godot_alone_is_waited_for(tmp_path: Path) -> None:
    """The windowed .exe is a GUI-subsystem program: `& godot.exe` returns at once, exit code
    unset. The wrapper waits for it and passes its exit code on."""
    windowed = lone_gui_godot(tmp_path / "flat")
    if windowed is None:
        pytest.skip("no real Godot .exe + _console.exe pair resolves here")
    root = _project(tmp_path)
    result = run_tool(
        root,
        "godot.ps1",
        "--headless",
        "--script",
        "res://quit_7.gd",
        env=clean_env(GODOT_BIN=str(windowed)),
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert "QUIT_7_RAN" in result.stdout  # it ran to the end before the wrapper returned
