"""Helpers for the PowerShell gate tests: throwaway projects, a fake Godot, the real one.

Every test builds its own project in a temp folder and copies in the tools under test, so
the gates are exercised exactly as a stranger's project would run them and nothing in this
repository is touched.

The gates run on Windows, Linux and macOS, and so do these helpers. A test file that
starts PowerShell carries `pytestmark = NEEDS_PWSH`. A test of something only one system
has carries `@windows_only("<what>")` or `@posix_only("<what>")`: the reason names it.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
# As tools/kit_config.py host_platform(): "windows", "macos" or "linux".
PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
WINDOWS = PLATFORM == "windows"
LINUX = PLATFORM == "linux"
MACOS = PLATFORM == "macos"
POSIX = not WINDOWS

NEEDS_PWSH = pytest.mark.skipif(PWSH is None, reason="needs PowerShell 7 (pwsh) on PATH")
NEEDS_UV = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")


def windows_only(what: str) -> pytest.MarkDecorator:
    """Skip elsewhere. `what`: the thing only Windows has ("cmd files", "code page 1252")."""
    return pytest.mark.skipif(not WINDOWS, reason=f"Windows only: {what}")


def posix_only(what: str) -> pytest.MarkDecorator:
    """Skip on Windows. `what`: the thing only Linux and macOS have ("the executable bit")."""
    return pytest.mark.skipif(WINDOWS, reason=f"Linux and macOS only: {what}")


def linux_only(what: str) -> pytest.MarkDecorator:
    """Skip elsewhere. `what`: the thing only Linux has ("DISPLAY")."""
    return pytest.mark.skipif(not LINUX, reason=f"Linux only: {what}")


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

# Shared by the stand-in programs: one line for a list of arguments, an argument with white
# space or a quote in double quotes, as a Windows command line shows it.
SHOWN = """
def shown(arguments):
    parts = []
    for argument in arguments:
        if argument and not any(letter in argument for letter in ' \\t\\r\\n"'):
            parts.append(argument)
        else:
            parts.append('"' + argument.replace('"', '\\\\"') + '"')
    return " ".join(parts)


def note(variable, text):
    path = os.environ.get(variable)
    if path:
        with open(path, "a", encoding="utf-8", newline="\\n") as log:
            log.write(text + "\\n")
"""

FAKE_GODOT = (
    '''"""Stands in for Godot: reports a version, echoes its arguments, and fails on request.

  FAKE_GODOT_VERSION      the --version line (default 4.7.2.stable.fake)
  FAKE_GODOT_LOG          a file that gets one line of arguments per run
  FAKE_GODOT_SESSION_LOG  a file that gets "session=" + GODOT_AGENT_SESSION, one line per run
  FAKE_GODOT_APPDATA_LOG  a file that gets "appdata=" + the folder Godot keeps user:// data
                          under (_gate_fixtures.godot_data_root), one line per run
  FAKE_GODOT_CACHE        a class cache file written (empty) by every run, as an import would
  FAKE_GODOT_BREAK        a run whose line of arguments holds this text prints FAKE_GODOT_SAYS
                          and the file FAKE_GODOT_SAYS_FILE, waits FAKE_GODOT_SLEEP seconds,
                          and exits FAKE_GODOT_EXIT
Any other `--headless --script res://tools/<tool>.gd` run ends with that tool's OK line.
A `--version` run does nothing else.
On Windows cmd.exe starts it: no argument with % ^ & | < > (python_program lists why).
"""
import os
import sys
import time
'''
    + SHOWN
    + """

def data_root():
    if sys.platform == "win32":
        return os.environ.get("APPDATA", "")
    home = os.environ.get("HOME", "")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support")
    data = os.environ.get("XDG_DATA_HOME", "")
    return data if os.path.isabs(data) else os.path.join(home, ".local", "share")


OK_LINES = {
    "res://tools/lint_project.gd": "LINT_PROJECT_OK errors=0 warnings=0",
    "res://tools/lint_shaders.gd": "LINT_SHADERS_OK checked=1",
    "res://tools/validate_all.gd": "VALIDATE_ALL_OK loaded=1",
}
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
arguments = sys.argv[1:]
if arguments[:1] == ["--version"]:
    print(os.environ.get("FAKE_GODOT_VERSION") or "4.7.2.stable.fake")
    sys.exit(0)
line = shown(arguments)
note("FAKE_GODOT_LOG", line)
note("FAKE_GODOT_SESSION_LOG", "session=" + os.environ.get("GODOT_AGENT_SESSION", ""))
note("FAKE_GODOT_APPDATA_LOG", "appdata=" + data_root())
if os.environ.get("FAKE_GODOT_CACHE"):
    with open(os.environ["FAKE_GODOT_CACHE"], "w", encoding="utf-8", newline="\\n") as cache:
        cache.write("list=[]\\n")
print("FAKE_GODOT " + line, flush=True)
broken = os.environ.get("FAKE_GODOT_BREAK")
if broken and broken in line:
    if os.environ.get("FAKE_GODOT_SAYS"):
        print(os.environ["FAKE_GODOT_SAYS"], flush=True)
    if os.environ.get("FAKE_GODOT_SAYS_FILE"):
        with open(os.environ["FAKE_GODOT_SAYS_FILE"], "rb") as said:
            sys.stdout.buffer.write(said.read())
        sys.stdout.buffer.flush()
    if os.environ.get("FAKE_GODOT_SLEEP"):
        time.sleep(float(os.environ["FAKE_GODOT_SLEEP"]))
    sys.exit(int(os.environ.get("FAKE_GODOT_EXIT") or 0))
if arguments[1:2] == ["--script"] and arguments[2:3] and arguments[2] in OK_LINES:
    print(OK_LINES[arguments[2]])
sys.exit(0)
"""
)

FAKE_UV = (
    '''"""Stands in for uv: logs its arguments (FAKE_UV_LOG), installs nothing, runs nothing.

  FAKE_UV_SYNC_EXIT  the exit code of `uv sync` (default 0)
  FAKE_UV_PYTHON     a Python that really runs `uv run python <script> ...` and
                     `uv run --project <dir> python <script> ...`
Any other `uv run ...` prints a progress line to stderr and its arguments to stdout, like
the real one.
"""
import os
import subprocess
import sys
'''
    + SHOWN
    + """

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
arguments = sys.argv[1:]
line = shown(arguments)
note("FAKE_UV_LOG", line)
if arguments[:1] == ["--version"]:
    print("uv 0.0.0 (fake)")
if arguments[:1] == ["sync"] and os.environ.get("FAKE_UV_SYNC_EXIT"):
    sys.exit(int(os.environ["FAKE_UV_SYNC_EXIT"]))
if arguments[:1] != ["run"]:
    sys.exit(0)
python = os.environ.get("FAKE_UV_PYTHON")
if python and arguments[1:2] == ["python"]:
    sys.exit(subprocess.call([python, *arguments[2:]]))
if python and arguments[1:2] == ["--project"] and arguments[3:4] == ["python"]:
    sys.exit(subprocess.call([python, *arguments[4:]]))
print("Installed 3 packages in 1ms", file=sys.stderr, flush=True)
print("FAKE_UV " + line)
sys.exit(0)
"""
)


def write(path: Path, contents: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="ascii", newline="\n")
    return path


def copy_tools(root: Path, *names: str) -> None:
    """Copy the named tools, and the files they all dot-source, into root/tools."""
    (root / "tools").mkdir(parents=True, exist_ok=True)
    for name in (*SHARED_TOOLS, *names):
        shutil.copyfile(REPO / "tools" / name, root / "tools" / name)


def link_folder(link: Path, target: Path) -> None:
    """A directory link without admin rights: a junction on Windows, a symlink elsewhere."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def is_folder_link(path: Path) -> bool:
    """True for what link_folder made, also once its target is gone."""
    return path.is_junction() if WINDOWS else path.is_symlink()


def unlink_folder(link: Path) -> None:
    """Remove what link_folder made; the target stays."""
    if WINDOWS:
        os.rmdir(link)
    else:
        link.unlink()


def not_as_root() -> None:
    """Skip a test that takes a permission away where the user has every permission."""
    if POSIX and os.geteuid() == 0:
        pytest.skip("root reads and writes every file")


EVERYONE = "*S-1-1-0"  # Windows: the group every user is in


def icacls(folder: Path, *arguments: str) -> None:
    """Windows: change the access rules of `folder`."""
    changed = subprocess.run(
        ["icacls", str(folder), *arguments], capture_output=True, text=True, check=False
    )
    assert changed.returncode == 0, changed.stdout + changed.stderr


@contextlib.contextmanager
def takes_no_new_file(folder: Path) -> Iterator[None]:
    """Nothing can be created in `folder` meanwhile; what it holds stays writable: an
    access rule on Windows, no write permission elsewhere. Ask not_as_root() first."""
    if WINDOWS:
        icacls(folder, "/deny", f"{EVERYONE}:(WD,AD)")
        try:
            yield
        finally:
            icacls(folder, "/remove:d", EVERYONE)
        return
    mode = stat.S_IMODE(folder.stat().st_mode)
    folder.chmod(0o555)
    try:
        yield
    finally:
        folder.chmod(mode)


@contextlib.contextmanager
def cannot_be_listed(*folders: Path) -> Iterator[None]:
    """What `folders` hold cannot be listed meanwhile: an access rule on Windows, no
    permission at all elsewhere. Ask not_as_root() first."""
    modes = [stat.S_IMODE(folder.stat().st_mode) for folder in folders]
    for folder in folders:
        if WINDOWS:
            icacls(folder, "/deny", f"{EVERYONE}:(RD)")
        else:
            folder.chmod(0)
    try:
        yield
    finally:
        for folder, mode in zip(folders, modes, strict=True):
            if WINDOWS:
                icacls(folder, "/remove:d", EVERYONE)
            else:
                folder.chmod(mode)


def old_powershell(root: Path, problem: str) -> None:
    """Make the tools copied into `root` take the PowerShell that runs them for one that
    cannot (`problem`: why, as Get-KitPowerShellProblem would say it). No machine of the
    test run has such a PowerShell; the rule itself is tested with the version passed in."""
    with (root / "tools" / "kit_config.ps1").open("a", encoding="ascii", newline="\n") as tools:
        tools.write(
            "\nfunction Get-KitPowerShellProblem {\n"
            '    param([version]$Version, [string]$Platform = "")\n'
            f"    return '{problem}'\n"
            "}\n"
        )


def copy_python_tools(root: Path) -> None:
    """Copy tools/kit_config.py and tools/ci/*.py, which a PowerShell gate may ask."""
    (root / "tools" / "ci").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "tools" / "kit_config.py", root / "tools" / "kit_config.py")
    for module in (REPO / "tools" / "ci").glob("*.py"):
        shutil.copyfile(module, root / "tools" / "ci" / module.name)


def write_kit_json(root: Path, **sections: dict) -> None:
    """A kit.json with the kit's defaults; keyword sections replace whole sections."""
    config: dict = {
        "godot": {"version": "4.7.2", "search_roots": []},
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
    main_scene: str = "",
) -> str:
    """A minimal project.godot, by default with the kit's strict warnings.

    `directory_rules`: where Godot applies warnings (1) or none (0); it applies none under
    res://addons unless a rule includes the folder.
    `main_scene`: application/run/main_scene. Without one the project has no game to run.
    """
    lines = ["config_version=5", "", "[application]", "", 'config/name="Gate fixture"']
    if main_scene:
        lines += [f'run/main_scene="{main_scene}"']
    lines += [""]
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


def _plain_python() -> str:
    """The interpreter behind this one: in a virtual environment on Windows python.exe is
    a launcher that starts it, one process more for every stand-in run."""
    base = getattr(sys, "_base_executable", "") or ""
    return base if base and Path(base).is_file() else sys.executable


def python_program(folder: Path, name: str, source: str) -> Path:
    """A program in `folder` that runs the Python `source` with its arguments, and exits
    with its exit code: `<name>.cmd` on Windows, an executable `<name>` elsewhere, each a
    two-line launcher of `<name>.py`. One source, so the systems cannot drift apart.

    `source` runs isolated (`-I -S`): the standard library only. Returns the launcher.

    Arguments a test must not pass, because cmd.exe starts the Windows launcher and reads
    them as its own (test_gate_fixtures.py pins each): `%NAME%` is replaced by that
    variable, `^` is dropped, and `&`, `|`, `<`, `>` end the argument and run what follows
    as a command. From PowerShell (`& $program ...`) also an argument that holds a double
    quote (the quote is dropped) and an empty one (it is dropped); Invoke-KitProcess and a
    direct start pass both. Linux and macOS pass every one of these unchanged.
    """
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / f"{name}.py"
    script.write_text(source, encoding="utf-8", newline="\n")
    python = _plain_python()
    if WINDOWS:
        launcher = folder / f"{name}.cmd"
        lines = ["@echo off", f'"{python}" -I -S "%~dp0{name}.py" %*', "exit /b %errorlevel%"]
        # cmd.exe reads a batch file in the OEM code page, with CRLF line ends.
        launcher.write_text("\r\n".join(lines) + "\r\n", encoding="oem", newline="")
        return launcher
    launcher = folder / name
    command = f'exec {shlex.quote(python)} -I -S {shlex.quote(str(script))} "$@"'
    launcher.write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8", newline="\n")
    launcher.chmod(0o755)
    return launcher


def fake_godot(folder: Path) -> Path:
    """A stand-in Godot the resolvers accept as GODOT_BIN; see FAKE_GODOT for its switches."""
    return python_program(folder, "fake_godot", FAKE_GODOT)


def fake_uv(folder: Path) -> Path:
    """A stand-in uv; put `folder` first on PATH. See FAKE_UV for its switches."""
    return python_program(folder, "uv", FAKE_UV)


def bare_path(*folders: Path) -> str:
    """A PATH of `folders` and what the system itself cannot do without: no git, no uv,
    no Godot unless a folder holds one."""
    system = [Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"] if WINDOWS else []
    return os.pathsep.join(str(folder) for folder in (*folders, *system))


def venv_tool(root: Path, name: str) -> Path:
    """Where `uv sync` puts a command of the project's virtual environment."""
    if WINDOWS:
        return root / ".venv" / "Scripts" / f"{name}.exe"
    return root / ".venv" / "bin" / name


# The variable that moves the folder Godot keeps user:// data under.
USER_DATA_VARIABLE = {"windows": "APPDATA", "macos": "HOME"}.get(PLATFORM, "XDG_DATA_HOME")


def godot_data_root(env: Mapping[str, str], platform: str = PLATFORM) -> str:
    """The folder Godot 4.7 keeps `app_userdata/<project name>` under, below `Godot/`
    (`godot/` on Linux): %APPDATA% on Windows; $XDG_DATA_HOME when it is an absolute path,
    else ~/.local/share, on Linux; ~/Library/Application Support on macOS, which reads no
    XDG variable. FAKE_GODOT_APPDATA_LOG logs this folder."""
    if platform == "windows":
        return env.get("APPDATA", "")
    home = env.get("HOME", "")
    if platform == "macos":
        return os.path.join(home, "Library", "Application Support")
    data = env.get("XDG_DATA_HOME", "")
    return data if os.path.isabs(data) else os.path.join(home, ".local", "share")


def user_data_env(folder: Path) -> dict[str, str]:
    """The variable that makes a Godot started with it keep user:// data (saves, logs)
    below `folder`, not among the developer's own: merge it into the environment of a
    real Godot. On macOS that variable is HOME, and whatever else starts with it (uv, git,
    PowerShell) looks for its own settings and caches there too."""
    folder.mkdir(parents=True, exist_ok=True)
    return {USER_DATA_VARIABLE: str(folder)}


DEVTOOLS_AUTOLOAD = {"DevTools": "*res://addons/agent_devtools/dev_tools.gd"}
EMPTY_CLASS_CACHE = "list=[]\n"


def ready_project(root: Path) -> None:
    """Everything tools/doctor.ps1 looks for, as placeholders: no check runs the files."""
    write_kit_json(root)
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS_AUTOLOAD))
    write(root / "addons" / "gdUnit4" / "plugin.cfg", '[plugin]\n\nversion="6.2.2"\n')
    write(root / "addons" / "agent_devtools" / "dev_tools.gd", "extends Node\n")
    write(venv_tool(root, "gdlint"), "")
    write(venv_tool(root, "gdformat"), "")
    write(root / ".godot" / "global_script_class_cache.cfg", EMPTY_CLASS_CACHE)


# The variables that name a Godot binary to every tool.
GODOT_VARIABLES = ("GODOT_BIN", "GODOT4_EXE")
# What a developer's shell may hold that would change what a gate does.
LEAKING_VARIABLES = (*GODOT_VARIABLES, "GODOT_VERSION", "GODOT_AGENT_SESSION")


# On Linux and macOS a running PowerShell keeps files in the temp folder: .NET's two pipes
# and a socket for a debugger (clr-debug-pipe-<pid>-*, dotnet-diagnostic-<pid>-*) and its own
# pipe for Enter-PSHostProcess (CoreFxPipe_PSHost.*). One that is killed, as a gate's timeout
# kills it, leaves them there. With these it makes none; PowerShell 7.2 does not know the
# second and still makes its own pipe.
QUIET_DOTNET = {"DOTNET_EnableDiagnostics": "0", "POWERSHELL_DIAGNOSTICS_OPTOUT": "1"}

# A PowerShell rewrites its startup profile when it ends, in place, in the user's own cache
# folder: StartupProfileData-NonInteractive under ~/.cache/powershell (Linux, macOS) or
# %LOCALAPPDATA%\Microsoft\PowerShell. On Linux two that end at the same moment can leave
# it mixed, and the next PowerShell of that user prints "An error has occurred that was
# not properly handled" and exits 0 without running anything. The tests start PowerShell
# on four workers at once. With this variable one reads the profile and writes none
# (PowerShell 7.2 and newer; 7.0 and 7.1 do not know it): conftest.py puts it into this
# process's environment, so every program a test starts has it, and
# tools/godot_resolver.ps1 Get-KitQuietPowerShellEnvironment names it too.
NO_STARTUP_PROFILE = {"DOTNET_MultiCoreJitNoProfileGather": "1"}
# Where a PowerShell started with `{STARTUP_PROFILE_VARIABLE: folder}` keeps that profile.
STARTUP_PROFILE_VARIABLE = "USERPROFILE" if WINDOWS else "XDG_CACHE_HOME"
STARTUP_PROFILE_FOLDER = "AppData/Local/Microsoft/PowerShell" if WINDOWS else "powershell"


@functools.cache
def powershell_version() -> tuple[int, int]:
    """(major, minor) of the PowerShell the tests start."""
    said = '$v = $PSVersionTable.PSVersion; "$($v.Major).$($v.Minor)"'
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", said],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    major, minor = asked.stdout.strip().split(".")
    return int(major), int(minor)


def startup_profiles(folder: Path) -> list[str]:
    """The startup profiles a PowerShell started with STARTUP_PROFILE_VARIABLE = `folder`
    has written."""
    kept = folder / STARTUP_PROFILE_FOLDER
    return sorted(path.name for path in kept.glob("StartupProfileData-*"))


def forget(env: dict[str, str], *names: str) -> None:
    """Take `names` out of `env`. Windows names a variable without letter case, and
    Python holds this process's own there in capitals: every spelling goes."""
    wanted = {name.upper() for name in names} if WINDOWS else set(names)
    for held in list(env):
        if (held.upper() if WINDOWS else held) in wanted:
            del env[held]


def clean_env(**overrides: str) -> dict[str, str]:
    """This process's environment (PATH, HOME, the XDG folders, the locale: what a
    program needs to start) without LEAKING_VARIABLES, with QUIET_DOTNET and
    NO_STARTUP_PROFILE, then `overrides`."""
    env = os.environ.copy()
    forget(env, *LEAKING_VARIABLES, *QUIET_DOTNET, *NO_STARTUP_PROFILE)
    env.update(QUIET_DOTNET)
    env.update(NO_STARTUP_PROFILE)
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
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ". $env:KIT_RESOLVER; Resolve-GodotPath",
        ],
        env=os.environ | {"KIT_RESOLVER": str(REPO / "tools" / "godot_resolver.ps1")},
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
    when no real Godot resolves here or it is not the .exe + _console.exe pair of the
    Windows download.
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
