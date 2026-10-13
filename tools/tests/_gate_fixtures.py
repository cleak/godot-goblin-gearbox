"""Helpers for the PowerShell gate tests: throwaway projects, a fake Godot, the real one.

Every test builds its own project in a temp folder and copies in the tools under test, so
the gates are exercised exactly as a stranger's project would run them and nothing in this
repository is touched.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
WINDOWS = os.name == "nt"

# What every tool needs beside itself.
SHARED_TOOLS = ("kit_config.ps1", "godot_resolver.ps1", "class_cache.ps1")

# The warnings tools/doctor.ps1 expects as errors.
STRICT_WARNINGS = (
    "untyped_declaration",
    "unsafe_property_access",
    "unsafe_method_access",
    "unsafe_cast",
    "unsafe_call_argument",
    "integer_division",
    "narrowing_conversion",
    "unused_variable",
    "unused_local_constant",
    "unused_private_class_variable",
    "unused_signal",
    "shadowed_variable",
    "shadowed_variable_base_class",
    "shadowed_global_identifier",
    "standalone_expression",
    "standalone_ternary",
)

FAKE_GODOT = r"""@echo off
rem Stands in for Godot: reports a version, echoes its arguments, and fails on request.
rem   FAKE_GODOT_VERSION  the --version line (default 4.7.2.stable.fake)
rem   FAKE_GODOT_LOG      a file that gets one line of arguments per run
rem   FAKE_GODOT_SESSION_LOG  a file that gets GODOT_AGENT_SESSION, one line per run
rem   FAKE_GODOT_APPDATA_LOG  a file that gets APPDATA (where user:// lives), one line per run
rem   FAKE_GODOT_CACHE    a class cache file written (empty) by every run, as an import would
rem   FAKE_GODOT_BREAK    a run whose arguments hold this text prints FAKE_GODOT_SAYS and the
rem                       file FAKE_GODOT_SAYS_FILE, waits FAKE_GODOT_SLEEP seconds, and exits
rem                       FAKE_GODOT_EXIT
rem Any other `--headless --script res://tools/<tool>.gd` run ends with that tool's OK line.
if "%~1"=="--version" goto version
if defined FAKE_GODOT_LOG >>"%FAKE_GODOT_LOG%" echo %*
if defined FAKE_GODOT_SESSION_LOG >>"%FAKE_GODOT_SESSION_LOG%" echo session=%GODOT_AGENT_SESSION%
if defined FAKE_GODOT_APPDATA_LOG >>"%FAKE_GODOT_APPDATA_LOG%" echo appdata=%APPDATA%
if defined FAKE_GODOT_CACHE >"%FAKE_GODOT_CACHE%" echo list=[]
echo FAKE_GODOT %*
if not defined FAKE_GODOT_BREAK goto verdict
echo %* | "%SystemRoot%\System32\findstr.exe" /C:"%FAKE_GODOT_BREAK%" >nul
if errorlevel 1 goto verdict
if defined FAKE_GODOT_SAYS echo %FAKE_GODOT_SAYS%
if defined FAKE_GODOT_SAYS_FILE type "%FAKE_GODOT_SAYS_FILE%"
if defined FAKE_GODOT_SLEEP "%SystemRoot%\System32\ping.exe" -n %FAKE_GODOT_SLEEP% 127.0.0.1 >nul
if defined FAKE_GODOT_EXIT exit /b %FAKE_GODOT_EXIT%
exit /b 0
:verdict
if not "%~2"=="--script" exit /b 0
if "%~3"=="res://tools/lint_project.gd" echo LINT_PROJECT_OK errors=0 warnings=0
if "%~3"=="res://tools/lint_shaders.gd" echo LINT_SHADERS_OK checked=1
if "%~3"=="res://tools/validate_all.gd" echo VALIDATE_ALL_OK loaded=1
exit /b 0
:version
if defined FAKE_GODOT_VERSION (echo %FAKE_GODOT_VERSION%) else (echo 4.7.2.stable.fake)
exit /b 0
"""

FAKE_UV = r"""@echo off
rem Stands in for uv: logs its arguments (FAKE_UV_LOG), installs nothing, runs nothing.
rem   FAKE_UV_SYNC_EXIT  the exit code of `uv sync` (default 0)
rem   FAKE_UV_PYTHON     a Python that really runs `uv run python <script> ...` and
rem                      `uv run --project <dir> python <script> ...`
rem Any other `uv run ...` prints a progress line to stderr and its arguments to stdout,
rem like the real one.
if defined FAKE_UV_LOG >>"%FAKE_UV_LOG%" echo %*
if "%~1"=="--version" echo uv 0.0.0 (fake)
if "%~1"=="sync" if defined FAKE_UV_SYNC_EXIT exit /b %FAKE_UV_SYNC_EXIT%
if not "%~1"=="run" exit /b 0
if defined FAKE_UV_PYTHON if "%~2"=="python" goto python
if defined FAKE_UV_PYTHON if "%~2"=="--project" if "%~4"=="python" goto project_python
echo Installed 3 packages in 1ms 1>&2
echo FAKE_UV %*
exit /b 0
:python
"%FAKE_UV_PYTHON%" %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%
:project_python
"%FAKE_UV_PYTHON%" %5 %6 %7 %8 %9
exit /b %errorlevel%
"""


def write(path: Path, contents: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="ascii", newline="\n")
    return path


def copy_tools(root: Path, *names: str) -> None:
    """Copy the named tools, and the files they all dot-source, into root/tools."""
    (root / "tools").mkdir(parents=True, exist_ok=True)
    for name in (*SHARED_TOOLS, *names):
        shutil.copyfile(REPO / "tools" / name, root / "tools" / name)


def copy_python_tools(root: Path) -> None:
    """Copy tools/kit_config.py and tools/ci/*.py, which a PowerShell gate may ask."""
    (root / "tools" / "ci").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "tools" / "kit_config.py", root / "tools" / "kit_config.py")
    for module in (REPO / "tools" / "ci").glob("*.py"):
        shutil.copyfile(module, root / "tools" / "ci" / module.name)


def write_kit_json(root: Path, **sections: dict) -> None:
    """A kit.json with the kit's defaults; keyword sections replace whole sections."""
    config: dict = {
        "godot": {"version": "4.7.2", "windows_x86_64_sha512": "", "search_roots": []},
        "gdunit4": {"version": "6.2.2"},
        "source": {"exclude": ["addons", "artifacts", "docs"], "include": []},
        "tests": {"root": "test"},
    }
    config.update(sections)
    write(root / "kit.json", json.dumps(config, indent=2) + "\n")


def project_godot(
    *,
    autoloads: dict[str, str] | None = None,
    strict: bool = True,
    directory_rules: dict[str, int] | None = None,
) -> str:
    """A minimal project.godot, by default with the kit's strict warnings.

    `directory_rules`: where Godot applies warnings (1) or none (0); it applies none under
    res://addons unless a rule includes the folder.
    """
    lines = ["config_version=5", "", "[application]", "", 'config/name="Gate fixture"', ""]
    if autoloads:
        lines += ["[autoload]", ""]
        lines += [f'{name}="{path}"' for name, path in autoloads.items()]
        lines += [""]
    if strict or directory_rules:
        lines += ["[debug]", ""]
    if strict:
        lines += [f"gdscript/warnings/{name}=2" for name in STRICT_WARNINGS]
    if directory_rules:
        # One entry per line, as Godot itself writes a dictionary.
        entries = ",\n".join(f'"{folder}": {rule}' for folder, rule in directory_rules.items())
        lines += ["gdscript/warnings/directory_rules={\n" + entries + "\n}"]
    lines += [""]
    return "\n".join(lines)


def _batch_file(path: Path, contents: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents.replace("\n", "\r\n"), encoding="ascii", newline="")
    return path


def fake_godot(folder: Path) -> Path:
    """A stand-in Godot the resolver accepts; see FAKE_GODOT for its switches."""
    return _batch_file(folder / "fake_godot.cmd", FAKE_GODOT)


def fake_uv(folder: Path) -> Path:
    """A stand-in uv; put `folder` first on PATH. See FAKE_UV for its switches."""
    return _batch_file(folder / "uv.cmd", FAKE_UV)


DEVTOOLS_AUTOLOAD = {"DevTools": "*res://addons/agent_devtools/dev_tools.gd"}
EMPTY_CLASS_CACHE = "list=[]\n"


def ready_project(root: Path) -> None:
    """Everything tools/doctor.ps1 looks for, as placeholders: no check runs the files."""
    write_kit_json(root)
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS_AUTOLOAD))
    write(root / "addons" / "gdUnit4" / "plugin.cfg", '[plugin]\n\nversion="6.2.2"\n')
    write(root / "addons" / "agent_devtools" / "dev_tools.gd", "extends Node\n")
    write(root / ".venv" / "Scripts" / "gdlint.exe", "")
    write(root / ".venv" / "Scripts" / "gdformat.exe", "")
    write(root / ".godot" / "global_script_class_cache.cfg", EMPTY_CLASS_CACHE)


def clean_env(**overrides: str) -> dict[str, str]:
    """The environment without the Godot overrides a developer may have set."""
    env = os.environ.copy()
    for name in ("GODOT_BIN", "GODOT4_EXE", "GODOT_VERSION"):
        env.pop(name, None)
    env.update(overrides)
    return env


def run_tool(
    root: Path,
    script: str,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = 180,
) -> subprocess.CompletedProcess[str]:
    """Run root/tools/<script> from outside the project: no tool may depend on the cwd."""
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(root / "tools" / script), *args],
        cwd=root.parent,
        env=env if env is not None else clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def run_powershell(
    command: str, *, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=cwd,
        env=env if env is not None else clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def run_godot_script(
    root: Path, script: str, *, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a res:// script headless in `root` with $env:GODOT_BIN, through Invoke-KitProcess."""
    env = env | {"KIT_PROBE_ROOT": str(root), "KIT_PROBE_SCRIPT": script}
    return run_powershell(
        ". (Join-Path $env:KIT_PROBE_ROOT 'tools/godot_resolver.ps1'); "
        "$run = Invoke-KitProcess -FilePath $env:GODOT_BIN "
        "-Arguments @('--headless', '--script', $env:KIT_PROBE_SCRIPT) "
        "-WorkingDirectory $env:KIT_PROBE_ROOT -TimeoutSeconds 120; "
        "Write-Output $run.Text; exit $run.Code",
        env=env,
    )


# The variables that name a Godot binary to every tool.
GODOT_VARIABLES = ("GODOT_BIN", "GODOT4_EXE")
_real_godot_allowed = False


def allow_real_godot(allowed: bool) -> None:
    """conftest.py says, test by test, whether the running one may start a real Godot."""
    global _real_godot_allowed
    _real_godot_allowed = allowed


def real_godot() -> str | None:
    """The Godot this repository's resolver finds, or None when there is none.

    Only for a test marked `@pytest.mark.real_godot`: `--dist loadgroup` keeps those on one
    worker (conftest.py).
    """
    if not _real_godot_allowed:
        pytest.fail(
            "this test starts a real Godot without the mark that keeps such tests from "
            "running side by side: add @pytest.mark.real_godot (tools/tests/conftest.py)",
            pytrace=False,
        )
    return _resolved_godot()


@functools.cache
def _resolved_godot() -> str | None:
    if PWSH is None:
        return None
    resolver = REPO / "tools" / "godot_resolver.ps1"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", f". '{resolver}'; Resolve-GodotPath"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = result.stdout.strip().splitlines()
    return lines[-1].strip() if result.returncode == 0 and lines else None


def lone_gui_godot(folder: Path) -> Path | None:
    """The real Godot's windowed .exe in `folder`, with no _console.exe beside it.

    A hard link when the volume allows one (the binary is large), a copy otherwise. None
    when no real Godot resolves here or it is not the usual .exe + _console.exe pair.
    """
    console = real_godot()
    if console is None or not console.lower().endswith("_console.exe"):
        return None
    gui = Path(console[: -len("_console.exe")] + ".exe")
    if not gui.is_file():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    alone = folder / gui.name
    try:
        os.link(gui, alone)
    except OSError:
        shutil.copyfile(gui, alone)
    return alone
