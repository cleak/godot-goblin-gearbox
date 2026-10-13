"""tools/setup.ps1: the bootstrap steps run in order, and a failed step fails the setup.

uv and Godot are stand-ins, so no network and no real install; tools/doctor.ps1 is the real
one. The real download path is exercised by running the script itself.
"""

import os
from pathlib import Path

import pytest
from _gate_fixtures import (
    PWSH,
    WINDOWS,
    clean_env,
    copy_tools,
    fake_godot,
    fake_uv,
    ready_project,
    run_tool,
    write,
)

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 tools")

SYSTEM32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"


def _project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "project"
    copy_tools(root, "setup.ps1", "doctor.ps1")
    ready_project(root)
    bin_folder = tmp_path / "bin"
    fake_uv(bin_folder)
    env = clean_env(
        PATH=str(bin_folder) + os.pathsep + str(SYSTEM32),  # the stand-in uv, and no git
        GODOT_BIN=str(fake_godot(bin_folder)),
        FAKE_UV_LOG=str(tmp_path / "uv.log"),
        FAKE_GODOT_LOG=str(tmp_path / "godot.log"),
    )
    return root, env


def _log(tmp_path: Path, name: str) -> list[str]:
    path = tmp_path / name
    return path.read_text().splitlines() if path.exists() else []


def test_ready_checkout_only_syncs_and_reports(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    result = run_tool(root, "setup.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _log(tmp_path, "uv.log")[0] == "sync"
    assert "Already imported; the class cache is fresh." in result.stdout
    assert _log(tmp_path, "godot.log") == []  # nothing to import
    assert "doctor: READY" in result.stdout
    assert result.stdout.strip().endswith("setup: ready. Next: pwsh ./tools/check.ps1")


def test_fresh_checkout_is_imported(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    cache = root / ".godot" / "global_script_class_cache.cfg"
    cache.unlink()
    result = run_tool(root, "setup.ps1", env=env | {"FAKE_GODOT_CACHE": str(cache)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No class cache yet" in result.stdout and "Imported." in result.stdout
    assert _log(tmp_path, "godot.log") == ["--headless --import"]
    assert "[ok]   class cache is fresh" in result.stdout


def test_missing_godot_says_how_to_get_it(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    del env["GODOT_BIN"]
    result = run_tool(root, "setup.ps1", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "setup.ps1 -InstallGodot" in result.stdout
    assert "Skipped: needs Godot." in result.stdout
    assert "  - Godot not found" in result.stdout
    assert "doctor: NOT READY" in result.stdout  # the verdict is still printed


def test_failed_steps_fail_the_setup(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    sync = run_tool(root, "setup.ps1", env=env | {"FAKE_UV_SYNC_EXIT": "2"})
    assert sync.returncode == 1, sync.stdout + sync.stderr
    assert "  - uv sync failed (exit 2)" in sync.stdout

    # The installer is asked for only when no Godot resolves, and its failure is reported.
    write(root / "tools" / "install_godot.ps1", "Write-Host 'INSTALLER RAN'\nexit 1\n")
    found = run_tool(root, "setup.ps1", "-InstallGodot", env=env)
    assert found.returncode == 0 and "INSTALLER RAN" not in found.stdout
    del env["GODOT_BIN"]
    failed = run_tool(root, "setup.ps1", "-InstallGodot", env=env)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "INSTALLER RAN" in failed.stdout
    assert "  - tools/install_godot.ps1 failed (exit 1)" in failed.stdout

    write(root / "kit.json", "{ not json")
    broken = run_tool(root, "setup.ps1", env=env)
    assert broken.returncode == 1 and "setup: kit.json is not valid JSON" in broken.stdout
    assert "Godot not found" not in broken.stdout  # the real cause, and only that


def test_pre_commit_needs_a_git_repository(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    write(root / ".pre-commit-config.yaml", "repos: []\n")
    result = run_tool(root, "setup.ps1", "-PreCommit", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "is not inside a git repository" in result.stdout
    assert not any("pre-commit" in line for line in _log(tmp_path, "uv.log"))
