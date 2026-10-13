"""_gate_fixtures.py: the stand-in programs behave the same on Windows, Linux and macOS.

Every gate test is built on them. One that ran differently on one system would make that
system's tests pass, or fail, for a reason no gate has.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import _gate_fixtures
import pytest
from _gate_fixtures import (
    NEEDS_PWSH,
    PLATFORM,
    POSIX,
    USER_DATA_VARIABLE,
    WINDOWS,
    bare_path,
    clean_env,
    fake_godot,
    fake_uv,
    godot_data_root,
    posix_only,
    python_program,
    run_powershell,
    user_data_env,
    venv_tool,
    windows_only,
)

sys.path.insert(0, str(_gate_fixtures.REPO / "tools"))
import kit_config  # noqa: E402


def _run(program: Path, *arguments: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(program), *arguments],
        env=clean_env(**env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_the_fixtures_name_this_system_as_the_tools_do() -> None:
    assert kit_config.host_platform() == PLATFORM
    flags = (WINDOWS, _gate_fixtures.LINUX, _gate_fixtures.MACOS)
    assert flags == tuple(name == PLATFORM for name in ("windows", "linux", "macos"))
    assert sum(flags) == 1 and POSIX is not WINDOWS
    for name in _gate_fixtures.SHARED_TOOLS:
        assert (_gate_fixtures.REPO / "tools" / name).is_file(), name


def test_a_python_program_gets_its_arguments_and_gives_its_exit_code(tmp_path: Path) -> None:
    source = "import json, sys\n\nprint(json.dumps(sys.argv[1:]))\nsys.exit(int(sys.argv[1]))\n"
    program = python_program(tmp_path / "it's [a] bin", "echo_args", source)
    # A name the system starts by itself: .cmd on Windows, the executable bit elsewhere.
    assert program.name == ("echo_args.cmd" if WINDOWS else "echo_args")
    assert WINDOWS or program.stat().st_mode & 0o111 == 0o111
    done = _run(program, "7", "a b", "plain", "--flag=x")
    assert done.returncode == 7, done.stdout + done.stderr
    assert done.stdout.strip() == '["7", "a b", "plain", "--flag=x"]'


ECHO = "import json, sys\n\nprint(json.dumps(sys.argv[1:]))\n"
ORDINARY = ["--path", "a b", "it's", 'say "hi"', "", "x=y;z", "a,b", "(paren)", "!bang", "*.gd"]
ORDINARY += ["50%", "tab\there", "back\\slash\\", "ünï", "--", "-x"]
# What cmd.exe, which starts a Windows stand-in, reads as its own. No `>`: it writes a file.
CMD_OWN = (["%PATH%"], ["^caret"], ["a&kit_no_such_program"], ["a|kit_no_such_program"])
CMD_OWN += (["<kit_no_such_file"],)


def _echoed(done: subprocess.CompletedProcess[str]) -> list[str] | None:
    """The arguments an ECHO program got; None when it did not run to its end."""
    try:
        return json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        return None


def test_a_stand_in_gets_its_arguments_as_they_are(tmp_path: Path) -> None:
    program = python_program(tmp_path, "echo_args", ECHO)
    assert _echoed(_run(program, *ORDINARY)) == ORDINARY
    if POSIX:
        for arguments in CMD_OWN:
            assert _echoed(_run(program, *arguments)) == arguments


@windows_only("cmd.exe, which starts a stand-in program there")
@pytest.mark.parametrize("arguments", CMD_OWN)
def test_cmd_keeps_its_own_characters_from_a_stand_in(tmp_path: Path, arguments: list[str]) -> None:
    """The limit python_program() states. A test that passes one of these to a stand-in
    tests cmd.exe; when this fails the limit is gone and the docstring can lose it."""
    program = python_program(tmp_path, "echo_args", ECHO)
    assert _echoed(_run(program, *arguments)) != arguments


@NEEDS_PWSH
def test_powershell_hands_a_stand_in_its_arguments(tmp_path: Path) -> None:
    program = python_program(tmp_path, "echo_args", ECHO)
    plain = [argument for argument in ORDINARY if argument and '"' not in argument]
    env = clean_env(
        KIT_PROGRAM=str(program),
        KIT_ARGUMENTS=json.dumps(ORDINARY),
        KIT_RESOLVER=str(_gate_fixtures.REPO / "tools" / "godot_resolver.ps1"),
    )
    asked = "$all = @($env:KIT_ARGUMENTS | ConvertFrom-Json); "
    called = run_powershell(
        asked + "$plain = @($all | Where-Object { $_ -and $_ -notmatch '\"' }); "
        "& $env:KIT_PROGRAM @plain; exit $LASTEXITCODE",
        env=env,
    )
    assert _echoed(called) == plain, called.stdout + called.stderr
    # Invoke-KitProcess, which every gate captures a program with: all of them.
    captured = run_powershell(
        ". $env:KIT_RESOLVER; " + asked + "$run = Invoke-KitProcess -FilePath $env:KIT_PROGRAM "
        "-Arguments $all -TimeoutSeconds 60; Write-Output $run.Stdout; exit $run.Code",
        env=env,
    )
    assert _echoed(captured) == ORDINARY, captured.stdout + captured.stderr


def test_the_fake_godot_reports_a_version_and_nothing_else(tmp_path: Path) -> None:
    log = tmp_path / "godot.log"
    fake = fake_godot(tmp_path / "bin")
    assert fake.name == ("fake_godot.cmd" if WINDOWS else "fake_godot")
    default = _run(fake, "--version", FAKE_GODOT_LOG=str(log))
    assert (default.returncode, default.stdout.strip()) == (0, "4.7.2.stable.fake")
    other = _run(fake, "--version", FAKE_GODOT_VERSION="4.6.1.stable.mono", FAKE_GODOT_BREAK="--v")
    assert (other.returncode, other.stdout.strip()) == (0, "4.6.1.stable.mono")
    assert _lines(log) == []  # a --version run logs nothing and breaks nothing


def test_the_fake_godot_logs_what_it_was_asked(tmp_path: Path) -> None:
    fake = fake_godot(tmp_path / "bin")
    log, sessions, data, cache = (tmp_path / name for name in ("log", "sessions", "data", "cache"))
    env = {
        "FAKE_GODOT_LOG": str(log),
        "FAKE_GODOT_SESSION_LOG": str(sessions),
        "FAKE_GODOT_APPDATA_LOG": str(data),
        "FAKE_GODOT_CACHE": str(cache),
    } | user_data_env(tmp_path / "user data")
    first = _run(fake, "--headless", "--import", **env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert first.stdout.strip() == "FAKE_GODOT --headless --import"
    second = _run(fake, "--path", "a b", "", 'say "hi"', "--", "x", GODOT_AGENT_SESSION="s1", **env)
    assert second.returncode == 0, second.stdout + second.stderr
    # One line a run; an argument with white space or a quote, or an empty one, in quotes.
    assert _lines(log) == ["--headless --import", '--path "a b" "" "say \\"hi\\"" -- x']
    assert _lines(sessions) == ["session=", "session=s1"]
    # Where this system's Godot would keep user:// data, given that environment.
    root = godot_data_root(clean_env(**env))
    assert _lines(data) == [f"appdata={root}"] * 2
    assert Path(root).is_relative_to(tmp_path / "user data")
    assert cache.read_text(encoding="utf-8") == "list=[]\n"


@pytest.mark.parametrize(
    ("tool", "line"),
    [
        ("lint_project.gd", "LINT_PROJECT_OK errors=0 warnings=0"),
        ("lint_shaders.gd", "LINT_SHADERS_OK checked=1"),
        ("validate_all.gd", "VALIDATE_ALL_OK loaded=1"),
        ("typecheck.gd", ""),
    ],
)
def test_the_fake_godot_ends_a_tool_run_with_the_tools_ok_line(
    tmp_path: Path, tool: str, line: str
) -> None:
    fake = fake_godot(tmp_path / "bin")
    done = _run(fake, "--headless", "--script", f"res://tools/{tool}", "--", "--all")
    assert done.returncode == 0, done.stdout + done.stderr
    said = done.stdout.splitlines()
    assert said[0] == f"FAKE_GODOT --headless --script res://tools/{tool} -- --all"
    assert said[1:] == ([line] if line else [])
    # Only as the script of a headless run.
    plain = _run(fake, "--script", f"res://tools/{tool}")
    assert plain.stdout.splitlines() == [f"FAKE_GODOT --script res://tools/{tool}"]


def test_the_fake_godot_breaks_the_run_it_is_told_to(tmp_path: Path) -> None:
    fake = fake_godot(tmp_path / "bin")
    said = tmp_path / "said.txt"
    said.write_bytes(b"\x1b[1mfrom a file\x1b[0m\nsecond line\n")
    env = {
        "FAKE_GODOT_BREAK": "--script res://tools/lint_project.gd",
        "FAKE_GODOT_SAYS": "SCRIPT ERROR: seeded",
        "FAKE_GODOT_SAYS_FILE": str(said),
        "FAKE_GODOT_EXIT": "3",
    }
    broken = _run(fake, "--headless", "--script", "res://tools/lint_project.gd", **env)
    assert broken.returncode == 3, broken.stdout + broken.stderr
    assert broken.stdout.splitlines() == [
        "FAKE_GODOT --headless --script res://tools/lint_project.gd",
        "SCRIPT ERROR: seeded",
        "\x1b[1mfrom a file\x1b[0m",
        "second line",
    ]
    # Another run is not broken; a broken one exits 0 unless told otherwise.
    other = _run(fake, "--headless", "--script", "res://tools/validate_all.gd", **env)
    assert other.returncode == 0 and "VALIDATE_ALL_OK loaded=1" in other.stdout
    del env["FAKE_GODOT_EXIT"]
    quiet = _run(fake, "--headless", "--script", "res://tools/lint_project.gd", **env)
    assert quiet.returncode == 0 and "LINT_PROJECT_OK" not in quiet.stdout
    # FAKE_GODOT_SLEEP holds the broken run back, for a gate to time out on.
    slow = subprocess.Popen(
        [str(fake), "--headless", "--quit"],
        env=clean_env(FAKE_GODOT_BREAK="--quit", FAKE_GODOT_SLEEP="60"),
        stdout=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            slow.wait(timeout=2)
    finally:
        if WINDOWS:
            # The launcher started the interpreter: the tree, as a gate's timeout does it.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(slow.pid)], capture_output=True)
        else:
            slow.kill()
        slow.wait(timeout=30)


def test_the_fake_uv_logs_and_runs_only_python(tmp_path: Path) -> None:
    uv = fake_uv(tmp_path / "bin")
    assert uv.name == ("uv.cmd" if WINDOWS else "uv")
    log = tmp_path / "uv.log"
    env = {"FAKE_UV_LOG": str(log)}
    version = _run(uv, "--version", **env)
    assert (version.returncode, version.stdout.strip()) == (0, "uv 0.0.0 (fake)")
    assert _run(uv, "sync", **env).returncode == 0
    assert _run(uv, "sync", FAKE_UV_SYNC_EXIT="2", **env).returncode == 2
    ran = _run(uv, "run", "ruff", "check", "a b", **env)
    assert ran.returncode == 0 and ran.stdout.strip() == 'FAKE_UV run ruff check "a b"'
    assert ran.stderr.strip() == "Installed 3 packages in 1ms"
    # With FAKE_UV_PYTHON a Python script really runs, with or without --project.
    script = tmp_path / "it runs.py"
    script.write_text("import sys\n\nprint(sys.argv[1:])\nsys.exit(5)\n", encoding="utf-8")
    env["FAKE_UV_PYTHON"] = sys.executable
    for prefix in (("run", "python"), ("run", "--project", str(tmp_path), "python")):
        real = _run(uv, *prefix, str(script), "x y", "z", **env)
        assert real.returncode == 5, real.stdout + real.stderr
        assert real.stdout.strip() == "['x y', 'z']"
    still = _run(uv, "run", "pytest", "-q", **env)
    assert still.stdout.strip() == "FAKE_UV run pytest -q"
    assert _lines(log)[:4] == ["--version", "sync", "sync", 'run ruff check "a b"']
    assert len(_lines(log)) == 7


def test_clean_env_keeps_what_a_program_needs_and_drops_what_would_leak(monkeypatch) -> None:
    # Named here one by one: a name lost from LEAKING_VARIABLES must fail this test.
    leaking = ("GODOT_BIN", "GODOT4_EXE", "GODOT_VERSION", "GODOT_AGENT_SESSION")
    for name in leaking:
        monkeypatch.setenv(name, "from the developer's shell")
    monkeypatch.setenv("KIT_TEST_KEPT", "kept")
    assert not set(leaking) & set(clean_env())
    env = clean_env(GODOT_BIN="named by the test", EXTRA="1")
    assert env["GODOT_BIN"] == "named by the test" and env["EXTRA"] == "1"
    assert env["KIT_TEST_KEPT"] == "kept" and env["PATH"] == os.environ["PATH"]
    for name in leaking[1:]:
        assert name not in env
    assert set(_gate_fixtures.LEAKING_VARIABLES) == set(leaking)
    kept = ("HOME", "LANG", "XDG_DATA_HOME", "SYSTEMROOT", "APPDATA", "TEMP", "TMPDIR")
    for name in kept:
        assert env.get(name) == os.environ.get(name), name
    assert set(_gate_fixtures.GODOT_VARIABLES) < set(_gate_fixtures.LEAKING_VARIABLES)
    # A PowerShell started with it leaves nothing in the temp folder when it is killed.
    assert env["DOTNET_EnableDiagnostics"] == "0" and env["POWERSHELL_DIAGNOSTICS_OPTOUT"] == "1"
    # And writes no startup profile: test_a_powershell_the_tests_start_writes_no_startup_profile.
    assert env["DOTNET_MultiCoreJitNoProfileGather"] == "1"


@NEEDS_PWSH
@posix_only("the files .NET keeps in the temp folder for a debugger")
def test_a_killed_powershell_leaves_nothing_in_the_temp_folder(tmp_path: Path) -> None:
    """The gate tests kill a hung PowerShell on purpose, many times a run."""
    temp = tmp_path / "temp"
    temp.mkdir()
    started = tmp_path / "started.txt"
    hung = subprocess.Popen(
        [
            _gate_fixtures.PWSH,
            "-NoProfile",
            "-Command",
            "Set-Content -LiteralPath $env:KIT_STARTED -Value up; Start-Sleep 120",
        ],
        env=clean_env(TMPDIR=str(temp), KIT_STARTED=str(started)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(600):
            if started.exists() or hung.poll() is not None:
                break
            time.sleep(0.1)
        assert started.exists(), "PowerShell did not start"
    finally:
        hung.kill()
        hung.wait(timeout=30)
    left = sorted(path.name for path in temp.iterdir())
    # Measured: PowerShell 7.6 makes no pipe of its own when asked; 7.2 has no such switch.
    asked = run_powershell('$v = $PSVersionTable.PSVersion; "$($v.Major).$($v.Minor)"')
    quiet = tuple(int(part) for part in asked.stdout.strip().split(".")) >= (7, 6)
    assert [name for name in left if quiet or not name.startswith("CoreFxPipe_PSHost.")] == []


@NEEDS_PWSH
def test_a_powershell_the_tests_start_writes_no_startup_profile(tmp_path: Path) -> None:
    """The profile is the developer's own file, rewritten in place by every PowerShell that
    ends; two at once can leave it mixed, and then no PowerShell of that user runs."""
    [(told, value)] = _gate_fixtures.NO_STARTUP_PROFILE.items()
    assert os.environ[told] == value  # conftest.py: for every program a test starts
    assert clean_env()[told] == value
    if _gate_fixtures.powershell_version() < (7, 2):
        # Measured with 7.0.13: it writes the profile all the same. 7.0 and 7.1 run the
        # kit on Windows only, where two that end together did not mix the file.
        pytest.skip("PowerShell before 7.2 (.NET before 6) does not know the variable")
    own = tmp_path / "own"
    own.mkdir()  # a folder that is not there moves nothing
    moved = {_gate_fixtures.STARTUP_PROFILE_VARIABLE: str(own)}
    for start in (run_powershell, lambda command, env: _file_run(tmp_path, command, env)):
        ran = start('"RAN-" + (6 * 7)', env=clean_env(**moved))
        assert ran.returncode == 0 and "RAN-42" in ran.stdout, ran.stdout + ran.stderr
    assert _gate_fixtures.startup_profiles(own) == []
    # And one that is not told writes it there: the check above can fail.
    plain = clean_env(**moved)
    _gate_fixtures.forget(plain, told)
    assert "RAN-42" in run_powershell('"RAN-" + (6 * 7)', env=plain).stdout
    if not _gate_fixtures.startup_profiles(own):
        # Seen on no machine yet: a Windows whose profile folder USERPROFILE does not move.
        pytest.skip(f"this PowerShell keeps no startup profile under {moved}")
    assert _gate_fixtures.startup_profiles(own) == ["StartupProfileData-NonInteractive"]


def _file_run(tmp_path: Path, command: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    """`pwsh -File`, as run_tool starts every gate."""
    script = tmp_path / "ran.ps1"
    script.write_text(command + "\n", encoding="ascii")
    return subprocess.run(
        [_gate_fixtures.PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_where_godot_keeps_user_data_on_each_system() -> None:
    windows = {"APPDATA": r"C:\Users\me\AppData\Roaming", "HOME": "/ignored"}
    assert godot_data_root(windows, "windows") == r"C:\Users\me\AppData\Roaming"
    home = os.path.join("/home/me", ".local", "share")
    assert godot_data_root({"HOME": "/home/me"}, "linux") == home
    absolute = os.path.abspath(os.sep + "data")
    assert godot_data_root({"HOME": "/home/me", "XDG_DATA_HOME": absolute}, "linux") == absolute
    # A relative XDG_DATA_HOME is ignored, as Godot ignores it.
    assert godot_data_root({"HOME": "/home/me", "XDG_DATA_HOME": "data"}, "linux") == home
    # macOS reads no XDG variable: only HOME moves it.
    mac = os.path.join("/Users/me", "Library", "Application Support")
    assert godot_data_root({"HOME": "/Users/me", "XDG_DATA_HOME": absolute}, "macos") == mac
    expected = {"windows": "APPDATA", "linux": "XDG_DATA_HOME", "macos": "HOME"}
    assert expected[PLATFORM] == USER_DATA_VARIABLE


def test_a_passing_test_leaves_no_temp_folder_behind(pytestconfig: pytest.Config) -> None:
    """Most tests copy the kit into their temp folder: about half a gigabyte a run, and
    pytest keeps the last three runs unless told otherwise."""
    if pytestconfig.inipath is None or pytestconfig.inipath.parent != _gate_fixtures.REPO:
        pytest.skip("run with another pytest configuration than the kit's pyproject.toml")
    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
    assert int(pytestconfig.getini("tmp_path_retention_count")) == 1


def test_the_small_helpers_follow_the_system(tmp_path: Path) -> None:
    here = user_data_env(tmp_path / "mine")
    assert here == {USER_DATA_VARIABLE: str(tmp_path / "mine")} and (tmp_path / "mine").is_dir()
    tool = venv_tool(tmp_path, "gdlint").relative_to(tmp_path).as_posix()
    assert tool == (".venv/Scripts/gdlint.exe" if WINDOWS else ".venv/bin/gdlint")
    parts = bare_path(tmp_path / "a", tmp_path / "b").split(os.pathsep)
    assert parts[:2] == [str(tmp_path / "a"), str(tmp_path / "b")]
    # Windows cannot start a program without System32; Linux and macOS need nothing.
    assert [Path(part).name for part in parts[2:]] == (["System32"] if WINDOWS else [])
