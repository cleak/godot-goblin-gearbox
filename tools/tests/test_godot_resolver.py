"""tools/godot_resolver.ps1: where Godot is looked for, what is accepted, how it is run."""

import os
import sys
from pathlib import Path

import pytest
from _gate_fixtures import (
    PWSH,
    WINDOWS,
    clean_env,
    copy_tools,
    fake_godot,
    run_powershell,
    write,
    write_kit_json,
)

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 tools")

SYSTEM32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"


def _project(tmp_path: Path, **godot: object) -> Path:
    root = tmp_path / "games" / "project"
    copy_tools(root)
    section = {"version": "4.7.2", "windows_x86_64_sha512": "", "search_roots": []} | godot
    write_kit_json(root, godot=section)
    return root


def _call(root: Path, call: str, **env: str):
    resolver = root / "tools" / "godot_resolver.ps1"
    return run_powershell(
        f". '{resolver}'; try {{ {call} }} "
        "catch { Write-Output ('THROWS: ' + $_.Exception.Message); exit 1 }",
        env=clean_env(**env),
    )


def _install(
    folder: Path, name: str, *, console: bool = True, flat: bool = False, zip_named: bool = False
) -> None:
    """An unzipped Godot (empty placeholder files) in one of the recognised layouts.

    `zip_named`: the folder keeps the archive's name, which ends in .exe.
    """
    if flat:
        write(folder / f"{name}.exe", "")
        return
    inner = folder / (f"{name}.exe" if zip_named else name)
    write(inner / f"{name}.exe", "")
    if console:
        write(inner / f"{name}_console.exe", "")


def test_search_order_is_tools_then_configured_roots_and_no_other_folder(tmp_path: Path) -> None:
    root = _project(tmp_path, search_roots=["extra", "%KIT_TEST_ELSEWHERE%"])
    elsewhere = tmp_path / "elsewhere"
    _install(root / ".tools" / "godot", "Godot_v4.7.2-stable_win64")
    _install(root / "extra", "Godot_v4.7.2-stable_win64", flat=True)
    _install(root / "extra", "Godot_v4.7.10-stable_win64", zip_named=True)
    _install(root / "extra", "Godot_v4.7.2-stable_mono_win64")  # Mono: never a candidate
    _install(root / "extra", "Godot_v4.70.1-stable_win64")  # 4.70 is not 4.7
    _install(root / "extra", "Godot_v4.6.3-stable_win64")  # another minor
    _install(elsewhere, "Godot_v4.7.1-stable_win64", console=False)
    _install(root.parent, "Godot_v4.7-stable_win64")

    result = _call(root, "Get-GodotCandidates", KIT_TEST_ELSEWHERE=str(elsewhere))
    assert result.returncode == 0, result.stdout + result.stderr
    found = [Path(line).relative_to(tmp_path).as_posix() for line in result.stdout.split()]
    assert found == [
        # 1. the project's own install; the console binary first
        "games/project/.tools/godot/Godot_v4.7.2-stable_win64/Godot_v4.7.2-stable_win64_console.exe",
        "games/project/.tools/godot/Godot_v4.7.2-stable_win64/Godot_v4.7.2-stable_win64.exe",
        # 2. kit.json search_roots, in order; console binaries before plain ones
        "games/project/extra/Godot_v4.7.10-stable_win64.exe/Godot_v4.7.10-stable_win64_console.exe",
        "games/project/extra/Godot_v4.7.2-stable_win64.exe",
        "games/project/extra/Godot_v4.7.10-stable_win64.exe/Godot_v4.7.10-stable_win64.exe",
        "elsewhere/Godot_v4.7.1-stable_win64/Godot_v4.7.1-stable_win64.exe",
        # The folder above the project is not searched: whatever lands there (a download,
        # a shared drive) must not run because of its name.
    ]

    # Who keeps Godot beside their projects says so in kit.json.
    write_kit_json(root, godot={"version": "4.7.2", "search_roots": [".."]})
    named = _call(root, "Get-GodotCandidates")
    assert [Path(line).name for line in named.stdout.split()][-2:] == [
        "Godot_v4.7-stable_win64_console.exe",
        "Godot_v4.7-stable_win64.exe",
    ]


def test_godot_version_override_changes_what_is_searched(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _install(root / ".tools" / "godot", "Godot_v4.7.2-stable_win64")
    _install(root / ".tools" / "godot", "Godot_v4.6.3-stable_win64")
    result = _call(root, "Get-GodotCandidates", GODOT_VERSION="4.6")
    assert [Path(line).name for line in result.stdout.split()] == [
        "Godot_v4.6.3-stable_win64_console.exe",
        "Godot_v4.6.3-stable_win64.exe",
    ]
    bad = _call(root, "Get-KitGodotVersion", GODOT_VERSION="latest")
    assert bad.returncode == 1 and "GODOT_VERSION must be a version like 4.7" in bad.stdout


def test_explicit_override_wins_and_must_be_compatible(tmp_path: Path) -> None:
    root = _project(tmp_path)
    fake = fake_godot(tmp_path / "bin")

    accepted = _call(root, "Resolve-GodotPath", GODOT_BIN=str(fake))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert accepted.stdout.strip() == str(fake)

    second = _call(root, "Resolve-GodotPath", GODOT4_EXE=str(fake))
    assert second.stdout.strip() == str(fake)

    # An explicit binary that does not fit is an error, never silently replaced.
    wrong = _call(root, "Resolve-GodotPath", GODOT_BIN=str(fake), GODOT_VERSION="4.6")
    assert wrong.returncode == 1
    assert "GODOT_BIN" in wrong.stdout and "needs Godot 4.6 stable" in wrong.stdout

    unstable = _call(
        root, "Resolve-GodotPath", GODOT_BIN=str(fake), FAKE_GODOT_VERSION="4.7.dev.custom_build"
    )
    assert unstable.returncode == 1 and "needs Godot 4.7 stable" in unstable.stdout

    mono = _call(
        root,
        "Resolve-GodotPath",
        GODOT_BIN=str(fake),
        FAKE_GODOT_VERSION="4.7.2.stable.mono.official",
    )
    assert mono.returncode == 1 and "Mono build" in mono.stdout

    missing = _call(root, "Resolve-GodotPath", GODOT_BIN=str(tmp_path / "nope.exe"))
    assert missing.returncode == 1 and "does not name an executable file" in missing.stdout


def test_not_found_says_how_to_get_godot(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _call(root, "Resolve-GodotPath", PATH=str(SYSTEM32))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Godot 4.7 (standard build) not found" in result.stdout
    assert "tools/install_godot.ps1" in result.stdout
    assert "GODOT_BIN" in result.stdout
    assert str(root / ".tools" / "godot") in result.stdout  # where it looked


def test_explicit_plain_exe_resolves_to_the_console_wrapper_beside_it(tmp_path: Path) -> None:
    """GODOT_BIN may name Godot's windowed .exe, which no shell waits for: the tools get the
    console wrapper that ships beside it."""
    root = _project(tmp_path)
    folder = tmp_path / "unzipped"
    plain = write(folder / "Godot_v4.7.2-stable_win64.exe", "")
    console = write(folder / "Godot_v4.7.2-stable_win64_console.exe", "")
    alone = write(tmp_path / "flat" / "Godot_v4.7.2-stable_win64.exe", "")
    script = write(folder / "godot.cmd", "")
    for given, expected in ((plain, console), (console, console), (alone, alone), (script, script)):
        result = _call(root, "Get-GodotConsoleTwin $env:KIT_GIVEN", KIT_GIVEN=str(given))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == str(expected)

    # The override goes through it: what is probed, and reported, is the wrapper.
    probed = _call(root, "Resolve-GodotPath", GODOT_BIN=str(plain))
    assert probed.returncode == 1
    assert "GODOT_BIN: Could not run" in probed.stdout
    assert str(console) in probed.stdout


def test_gui_subsystem_programs_are_recognised(tmp_path: Path) -> None:
    """The call operator does not wait for a GUI-subsystem .exe: the tools must know one."""
    root = _project(tmp_path)
    home = Path(sys.base_prefix)
    console, windowed = home / "python.exe", home / "pythonw.exe"
    if not console.is_file() or not windowed.is_file():
        pytest.skip("this Python has no python.exe / pythonw.exe pair to tell apart")
    not_a_program = write(tmp_path / "notes.exe", "MZ is how a program starts; this is text\n")
    cases = {
        console: "False",
        windowed: "True",
        not_a_program: "False",
        fake_godot(tmp_path / "bin"): "False",
        tmp_path / "missing.exe": "False",
    }
    for path, expected in cases.items():
        result = _call(root, "Test-KitGuiSubsystem $env:KIT_GIVEN", KIT_GIVEN=str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == expected, path

    # Started attached, such a program is waited for and its exit code comes back.
    done = tmp_path / "it's [odd] dir" / "done.txt"
    script = write(
        done.parent / "late exit.py",
        "import pathlib, sys, time\n\ntime.sleep(1)\n"
        "pathlib.Path(sys.argv[1]).write_text(pathlib.Path.cwd().name)\nsys.exit(7)\n",
    )
    result = _call(
        root,
        "$code = Invoke-KitAttachedProcess -FilePath $env:KIT_PYTHONW "
        "-Arguments @($env:KIT_SCRIPT, $env:KIT_DONE) -WorkingDirectory $env:KIT_WD; "
        "Write-Output ('code=' + $code + ' done=' + (Test-Path -LiteralPath $env:KIT_DONE))",
        KIT_PYTHONW=str(windowed),
        KIT_SCRIPT=str(script),
        KIT_DONE=str(done),
        KIT_WD=str(done.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "code=7 done=True" in result.stdout
    assert done.read_text() == "it's [odd] dir"


def test_process_helper_quotes_arguments_and_reports_exit_code(tmp_path: Path) -> None:
    """A path with a space must arrive as one argument (Start-Process quotes nothing).

    The working directory is taken literally too: Start-Process reads it as a wildcard
    pattern, and a folder with square brackets is then "not found".
    """
    root = _project(tmp_path)
    echo = write(
        tmp_path / "it's [wip] with space" / "echo args.py",
        "import os, sys\n\nprint(sys.argv[1:])\nprint('cwd=' + os.path.basename(os.getcwd()))\n"
        "sys.stderr.write('oops')\nsys.exit(7)\n",
    )
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -TimeoutSeconds 60 "
        "-WorkingDirectory $env:KIT_WD "
        "-Arguments @($env:KIT_ECHO, 'a b', 'plain', 'C:\\dir with space\\', 'say \"hi\"', ''); "
        "Write-Output $run.Stdout.Trim(); Write-Output ('err=' + $run.Stderr.Trim()); "
        "Write-Output ('code=' + $run.Code + ' timedout=' + $run.TimedOut)",
        KIT_ECHO=str(echo),
        KIT_PYTHON=sys.executable,
        KIT_WD=str(echo.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ["a b", "plain", "C:\\dir with space\\", 'say "hi"', ""]
    assert repr(expected) in result.stdout
    assert "cwd=it's [wip] with space" in result.stdout
    assert "err=oops" in result.stdout
    assert "code=7 timedout=False" in result.stdout


def test_below_normal_priority_reaches_the_grandchild(tmp_path: Path) -> None:
    """Godot's console wrapper starts the real Godot: that process must be polite too."""
    root = _project(tmp_path)
    report = write(
        tmp_path / "report.ps1",
        "param([string]$Label = 'child')\n"
        '"$Label=" + (Get-Process -Id $PID).PriorityClass\n'
        "if ($Label -eq 'child') {\n"
        "    & (Get-Process -Id $PID).Path -NoProfile -File $PSCommandPath -Label grandchild\n"
        "}\n",
    )
    result = _call(
        root,
        "$before = (Get-Process -Id $PID).PriorityClass; "
        "$run = Invoke-KitProcess -FilePath (Get-Process -Id $PID).Path -BelowNormal "
        "-TimeoutSeconds 60 -Arguments @('-NoProfile', '-File', $env:KIT_REPORT); "
        "Write-Output $run.Text; "
        "Write-Output ('restored=' + ((Get-Process -Id $PID).PriorityClass -eq $before))",
        KIT_REPORT=str(report),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "child=BelowNormal" in result.stdout
    assert "grandchild=BelowNormal" in result.stdout
    assert "restored=True" in result.stdout  # the caller's own priority is put back


def test_process_helper_kills_a_hung_process(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath (Get-Process -Id $PID).Path "
        "-Arguments @('-NoProfile', '-Command', 'Write-Output started; Start-Sleep 60') "
        "-TimeoutSeconds 3; "
        "Write-Output ('code=' + $run.Code + ' timedout=' + $run.TimedOut); "
        "Write-Output ('seconds=' + $run.Seconds)",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "code=124 timedout=True" in result.stdout
    seconds = float(result.stdout.split("seconds=")[1].split()[0])
    assert seconds < 30
