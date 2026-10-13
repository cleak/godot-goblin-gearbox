"""tools/godot.ps1 creates an export's output folder before Godot starts."""

from pathlib import Path

import pytest
from _gate_fixtures import (
    EMPTY_CLASS_CACHE,
    PWSH,
    WINDOWS,
    clean_env,
    copy_tools,
    run_tool,
    write,
    write_kit_json,
)

PRESET = "Windows Desktop"
PRESET_PATH = "build/windows/game.exe"

# An engine that passes only when the folder is already there.
ENGINE = (
    "@echo off\r\n"
    'if "%~1"=="--version" (echo 4.7.2.stable.fake& exit /b 0)\r\n'
    'if exist "%KIT_EXPORT_PARENT%\\" (exit /b 0) else (exit /b 41)\r\n'
)


@pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 tools")
@pytest.mark.parametrize("output", ["preset", "relative", "absolute"])
def test_export_creates_parent_before_engine(tmp_path: Path, output: str) -> None:
    """The preset's own export_path and an explicit path, run from outside the project."""
    root = tmp_path / "checkout"
    copy_tools(root, "godot.ps1", "export_paths.ps1")
    write_kit_json(root)
    write(root / "project.godot", "config_version=5\n")
    write(root / ".godot" / "global_script_class_cache.cfg", EMPTY_CLASS_CACHE)
    write(
        root / "export_presets.cfg",
        f'[preset.0]\nname="Other"\nexport_path="wrong/place/game.exe"\n\n'
        f'[preset.1]\nname="{PRESET}"\nexport_path="{PRESET_PATH}"\n',
    )
    engine = tmp_path / "bin" / "engine.cmd"
    engine.parent.mkdir()
    engine.write_text(ENGINE, encoding="ascii", newline="")

    target = root / PRESET_PATH
    args = ["--headless", "--export-release", PRESET]
    if output == "relative":
        target = root / "other/output/game.exe"
        args.append("other/output/game.exe")
    elif output == "absolute":
        target = tmp_path / "absolute/output/game.exe"
        args.append(str(target))
    env = clean_env(GODOT_BIN=str(engine), KIT_EXPORT_PARENT=str(target.parent))
    result = run_tool(root, "godot.ps1", *args, env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.parent.is_dir()
    assert not (root / "wrong").exists()

    # Without an export nothing is created, and the engine's verdict comes back.
    plain = run_tool(
        root, "godot.ps1", "--headless", env=env | {"KIT_EXPORT_PARENT": str(root / "nope")}
    )
    assert plain.returncode == 41, plain.stdout + plain.stderr
