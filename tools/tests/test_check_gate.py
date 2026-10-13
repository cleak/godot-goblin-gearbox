"""tools/check.ps1: it runs every gate, keeps going, and cannot report a pass it did not see.

The gates themselves are stubs here (their own tests cover them) and Godot is a stand-in,
so these tests pin the orchestration: order, exit code, what is printed, what is skipped.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _gate_fixtures import (
    DEVTOOLS_AUTOLOAD,
    LINUX,
    NEEDS_PWSH,
    NO_STARTUP_PROFILE,
    QUIET_DOTNET,
    REPO,
    STARTUP_PROFILE_VARIABLE,
    WINDOWS,
    bare_path,
    clean_env,
    copy_python_tools,
    fake_godot,
    fake_uv,
    forget,
    powershell_version,
    project_godot,
    run_tool,
    startup_profiles,
    write,
    write_kit_json,
)

pytestmark = NEEDS_PWSH

STUB_GATES = {
    "ascii": "test_scripts_ascii.ps1",
    "gdlint": "lint_gdscript.ps1",
    "typecheck": "typecheck.ps1",
    "test-lint": "lint_tests.ps1",
    "tests": "test.ps1",
}
ALL_GATES = [
    "ascii",
    "gdlint",
    "typecheck",
    "project-lint",
    "shader-lint",
    "validate-all",
    "scenes",
    "test-lint",
    "ruff",
    "pytest",
    "architecture",
    "typed-ternary",
    "wall-clock",
    "agents-md",
    "tests",
]


def _stub(gate: str, code: int = 0) -> str:
    """A gate that records its arguments, prints a line, and exits with `code`."""
    return (
        f"Add-Content -LiteralPath (Join-Path $PSScriptRoot '../calls.txt') "
        f"-Value ('{gate} ' + ($args -join ' '))\n"
        f"Write-Host 'OUTPUT_OF_{gate}'\n"
        f"Write-Host '{gate} summary line'\n"
        f"exit {code}\n"
    )


def _project(
    tmp_path: Path, *, failing: dict[str, int] | None = None, name: str = "project"
) -> tuple[Path, dict]:
    root = tmp_path / name
    tools = root / "tools"
    tools.mkdir(parents=True)
    for name in ("check.ps1", "kit_config.ps1", "godot_resolver.ps1"):
        shutil.copyfile(REPO / "tools" / name, tools / name)
    for gate, script in STUB_GATES.items():
        write(tools / script, _stub(gate, (failing or {}).get(gate, 0)))
    for script in ("lint_project.gd", "lint_shaders.gd", "validate_all.gd"):
        write(tools / script, "extends SceneTree\n")
    write_kit_json(root)
    write(root / "project.godot", project_godot())
    write(root / "scripts" / "player.gd", "extends Node\n")
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")))
    return root, env


def _rows(stdout: str) -> dict[str, tuple[str, str]]:
    """gate -> (result, detail) from the summary table."""
    table = stdout.split("Gate ", 1)[1]
    rows: dict[str, tuple[str, str]] = {}
    for line in table.splitlines()[2:]:
        match = re.match(r"^(\S+)\s+(pass|FAIL|skipped)\s+(\S+)\s*(.*)$", line)
        if match:
            rows[match.group(1)] = (match.group(2), match.group(4).strip())
    return rows


def _calls(root: Path) -> list[str]:
    return (root / "calls.txt").read_text().split("\n")[:-1]


def test_all_gates_pass_quietly_and_in_order(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    result = run_tool(root, "check.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = _rows(result.stdout)
    assert list(rows) == ALL_GATES
    assert {gate for gate, row in rows.items() if row[0] == "pass"} == {
        "ascii",
        "gdlint",
        "typecheck",
        "project-lint",
        "validate-all",
        "test-lint",
        "tests",
    }
    # A gate whose tool is not in the project is skipped, with the reason.
    assert rows["shader-lint"] == ("skipped", "no .gdshader files")
    assert rows["ruff"] == ("skipped", "no Python files under tools/")
    assert rows["pytest"] == ("skipped", "tools/tests not present")
    assert rows["wall-clock"] == ("skipped", "tools/check_test_wallclock.py not present")
    assert rows["scenes"] == ("skipped", "addons/agent_devtools not present")
    assert rows["agents-md"] == ("skipped", "AGENTS.md not present")
    assert rows["gdlint"][1] == "gdlint summary line"
    # A Godot gate shows the tool's verdict line.
    assert rows["project-lint"][1] == "LINT_PROJECT_OK errors=0 warnings=0"
    assert rows["validate-all"][1] == "VALIDATE_ALL_OK loaded=1"
    assert "check: PASSED: 7 passed, 0 failed, 8 skipped" in result.stdout
    # Passing gates stay quiet; their output is in the log.
    assert "OUTPUT_OF_" not in result.stdout
    assert "OUTPUT_OF_gdlint" in (root / "artifacts" / "check" / "gdlint.log").read_text()
    assert (root / "artifacts" / ".gdignore").is_file()
    assert [call.split()[0] for call in _calls(root)] == list(STUB_GATES)


def test_project_folder_with_brackets_and_quotes_runs_every_gate(tmp_path: Path) -> None:
    """Start-Process reads its working directory as a wildcard pattern: in "[wip] game"
    a gate must still start."""
    root, env = _project(tmp_path, name="it's a [wip] game")
    result = run_tool(root, "check.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "could not start" not in result.stdout
    assert "check: PASSED: 7 passed, 0 failed, 8 skipped" in result.stdout
    assert "FAKE_GODOT" in (root / "artifacts" / "check" / "project-lint.log").read_text()


def test_python_gates_and_the_first_uv_sync(tmp_path: Path) -> None:
    """Without a .venv the lint gate has no gdtoolkit; `uv run`, two gates later, would
    create the .venv too late. One `uv sync` comes first."""
    root, env = _project(tmp_path)
    write(root / "pyproject.toml", '[project]\nname = "fixture"\nversion = "0"\n')
    write(root / "tools" / "helper.py", "VALUE = 1\n")
    (root / "tools" / "tests").mkdir()
    log = tmp_path / "uv.log"
    fake_uv(tmp_path / "bin")
    env = env | {
        "PATH": str(tmp_path / "bin") + os.pathsep + env["PATH"],
        "FAKE_UV_LOG": str(log),
    }

    result = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No .venv yet: uv sync" in result.stdout
    asked = log.read_text().splitlines()
    # pytest on half the cores, four at most; alone on a machine with fewer than four.
    workers = min(4, (os.cpu_count() or 1) // 2)
    parallel = f" -n {workers} --dist loadgroup" if workers >= 2 else ""
    assert asked[:3] == ["sync", "run ruff check tools", f"run pytest tools/tests{parallel}"]
    assert result.stdout.index("No .venv yet") < result.stdout.index(
        "ascii ..."
    )  # before the gates
    rows = _rows(result.stdout)
    # The table shows the tool's verdict (stdout), not uv's progress line (stderr).
    assert rows["ruff"] == ("pass", "FAKE_UV run ruff check tools")
    assert rows["pytest"][0] == "pass"

    # With a .venv there is nothing to sync.
    (root / ".venv").mkdir()
    log.unlink()
    again = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "sync" not in log.read_text().splitlines()

    # A failed sync is shown, and the gates still run and give their own verdicts.
    (root / ".venv").rmdir()
    failed = run_tool(root, "check.ps1", "-SkipTests", env=env | {"FAKE_UV_SYNC_EXIT": "2"})
    assert "uv sync failed: exit 2" in failed.stdout
    assert _rows(failed.stdout)["gdlint"][0] == "pass"  # the stub gate needs no .venv


def test_without_uv_the_python_gates_are_skipped_and_say_why(tmp_path: Path) -> None:
    """On a PATH with nothing on it, on every system: no uv, no git, no Godot but the one
    GODOT_BIN names. The other gates still run."""
    root, env = _project(tmp_path)
    write(root / "pyproject.toml", '[project]\nname = "fixture"\nversion = "0"\n')
    write(root / "tools" / "helper.py", "VALUE = 1\n")
    (root / "tools" / "tests").mkdir()
    result = run_tool(root, "check.ps1", "-SkipTests", env=env | {"PATH": bare_path()})
    assert result.returncode == 0, result.stdout + result.stderr
    rows = _rows(result.stdout)
    no_uv = ("skipped", "uv not found (https://docs.astral.sh/uv/)")
    assert rows["ruff"] == no_uv and rows["pytest"] == no_uv
    assert rows["gdlint"][0] == "pass" and rows["validate-all"][0] == "pass"
    assert "uv sync" not in result.stdout  # nothing to run it with

    # -Changed asks git, through uv, what changed: that cannot be skipped.
    write(root / "tools" / "test_shards.py", "print('No suites to run.')\n")
    changed = run_tool(root, "check.ps1", "-Changed", env=env | {"PATH": bare_path()})
    assert changed.returncode == 1, changed.stdout + changed.stderr
    needs_uv = ("FAIL", "-Changed needs uv (https://docs.astral.sh/uv/)")
    assert _rows(changed.stdout)["tests"] == needs_uv


def test_ruff_gate_leaves_the_kits_installed_files_to_the_kits_rules(tmp_path: Path) -> None:
    """In a project install.ps1 put the kit into, `ruff check tools` held the kit's Python
    files to that project's own ruff rules."""
    root, env = _project(tmp_path)
    write(root / ".venv" / "pyvenv.cfg", "")
    write(root / "tools" / "devtools.py", "VALUE = 1\n")
    write(root / "tools" / "ci" / "suites.py", "VALUE = 2\n")
    log = tmp_path / "uv.log"
    fake_uv(tmp_path / "bin")
    env = env | {"PATH": str(tmp_path / "bin") + os.pathsep + env["PATH"], "FAKE_UV_LOG": str(log)}

    def ruff_row() -> tuple[tuple[str, str], list[str]]:
        log.write_text("")
        result = run_tool(root, "check.ps1", "-SkipTests", env=env)
        asked = [line for line in log.read_text().splitlines() if line.startswith("run ruff")]
        return _rows(result.stdout)["ruff"], asked

    # The kit's own repository: no manifest, everything is checked.
    assert ruff_row() == (("pass", "FAKE_UV run ruff check tools"), ["run ruff check tools"])

    # Windows ignores the letter case of a name; elsewhere tools/CI is another folder.
    spelled = "tools/CI/suites.py" if WINDOWS else "tools/ci/suites.py"
    manifest = {"files": {"tools/devtools.py": "0", spelled: "0", "tools/x.ps1": "0"}}
    write(root / "tools" / "kit_manifest.json", json.dumps(manifest))
    reason = "tools/ holds only the kit's Python files (tools/kit_manifest.json lists them)"
    assert ruff_row() == (("skipped", reason), [])

    write(root / "tools" / "build" / "my_export.py", "VALUE = 3\n")
    mine = "run ruff check tools/build/my_export.py"
    assert ruff_row() == (("pass", f"FAKE_UV {mine}"), [mine])

    if not WINDOWS:
        # The kit's tools/CI/suites.py is not this project's tools/ci/suites.py.
        manifest["files"] = {"tools/devtools.py": "0", "tools/CI/suites.py": "0"}
        write(root / "tools" / "kit_manifest.json", json.dumps(manifest))
        row, asked = ruff_row()
        assert row[0] == "pass" and len(asked) == 1
        assert sorted(asked[0].split()[3:]) == ["tools/build/my_export.py", "tools/ci/suites.py"]

    write(root / "tools" / "kit_manifest.json", "{ not json")
    row, asked = ruff_row()
    assert row[0] == "FAIL" and row[1].startswith("tools/kit_manifest.json is not valid JSON")
    assert asked == []


def test_a_failing_gate_fails_the_run_but_not_the_gates_after_it(tmp_path: Path) -> None:
    root, env = _project(tmp_path, failing={"gdlint": 1, "test-lint": 2})
    result = run_tool(root, "check.ps1", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    rows = _rows(result.stdout)
    # The table says why: the exit code and the gate's own last line.
    assert rows["gdlint"] == ("FAIL", "exit 1: gdlint summary line")
    assert rows["test-lint"] == ("FAIL", "exit 2: test-lint summary line")
    assert rows["typecheck"][0] == "pass" and rows["tests"][0] == "pass"
    assert [call.split()[0] for call in _calls(root)] == list(STUB_GATES)
    # Only the failing gates' output is shown.
    assert "OUTPUT_OF_gdlint" in result.stdout and "OUTPUT_OF_test-lint" in result.stdout
    assert "OUTPUT_OF_typecheck" not in result.stdout
    assert "check: FAILED (gdlint, test-lint): 5 passed, 2 failed, 8 skipped" in result.stdout


def _tool_run(script: str) -> str:
    """The arguments of a tool's own run; the compile check before it has --check-only."""
    return f"--headless --script res://tools/{script}"


def test_godot_gate_failures_are_caught_even_with_exit_0(tmp_path: Path) -> None:
    """A Godot script that hits an error can still exit 0: the output decides."""
    root, env = _project(tmp_path)
    quiet_error = env | {
        "FAKE_GODOT_BREAK": _tool_run("lint_project.gd"),
        "FAKE_GODOT_SAYS": "SCRIPT ERROR: Parse Error: seeded",
    }
    result = run_tool(root, "check.ps1", env=quiet_error)
    assert result.returncode == 1, result.stdout + result.stderr
    rows = _rows(result.stdout)
    assert rows["project-lint"][0] == "FAIL" and "SCRIPT ERROR" in rows["project-lint"][1]
    assert rows["validate-all"][0] == "pass"
    assert "SCRIPT ERROR: Parse Error: seeded" in result.stdout

    exit_code = env | {"FAKE_GODOT_BREAK": _tool_run("validate_all.gd"), "FAKE_GODOT_EXIT": "3"}
    result = run_tool(root, "check.ps1", env=exit_code)
    assert result.returncode == 1
    # Exit 3 is a tool built on tools/headless_tool.gd saying its own code broke.
    broke = _rows(result.stdout)["validate-all"]
    assert broke[0] == "FAIL" and broke[1].startswith("exit 3: the tool itself broke (TOOL_ERROR)")

    usage = env | {"FAKE_GODOT_BREAK": _tool_run("lint_project.gd"), "FAKE_GODOT_EXIT": "2"}
    cannot_run = _rows(run_tool(root, "check.ps1", env=usage).stdout)["project-lint"]
    assert cannot_run[0] == "FAIL" and cannot_run[1].startswith("exit 2: the tool could not run")

    found = env | {
        "FAKE_GODOT_BREAK": _tool_run("validate_all.gd"),
        "FAKE_GODOT_EXIT": "1",
        "FAKE_GODOT_SAYS": "VALIDATE_ALL_FAILED failures=2 loaded=9",
    }
    problems = _rows(run_tool(root, "check.ps1", env=found).stdout)["validate-all"]
    assert problems == ("FAIL", "exit 1: VALIDATE_ALL_FAILED failures=2 loaded=9")


def test_a_godot_gate_passes_on_the_tools_verdict_not_on_exit_0(tmp_path: Path) -> None:
    """The project's autoloads run in a `--script` process too. One that calls quit() ends
    the run with exit 0, after the tool said FAILED or before it said anything: both were
    a pass."""
    root, env = _project(tmp_path)
    write(root / "shaders" / "glow.gdshader", "shader_type canvas_item;\n")
    verdicts = {
        "project-lint": "LINT_PROJECT",
        "shader-lint": "LINT_SHADERS",
        "validate-all": "VALIDATE_ALL",
    }
    # Every tool's own run (not the compile check before it) exits 0 with these lines.
    said = tmp_path / "said.txt"
    env = env | {"FAKE_GODOT_BREAK": _tool_run(""), "FAKE_GODOT_SAYS_FILE": str(said)}

    def rows_when_godot_says(*lines: str) -> dict[str, tuple[str, str]]:
        said.write_text("".join(f"{line}\n" for line in lines), encoding="ascii")
        result = run_tool(root, "check.ps1", "-SkipTests", env=env)
        assert result.returncode == 1, result.stdout + result.stderr
        rows = _rows(result.stdout)
        assert [gate for gate, row in rows.items() if row[0] == "FAIL"] == list(verdicts)
        return rows

    rows = rows_when_godot_says(
        *(f"{verdict}_FAILED errors=1 warnings=0" for verdict in verdicts.values())
    )
    for gate, verdict in verdicts.items():
        expected = f"exit 0, but '{verdict}_FAILED errors=1 warnings=0' in the output"
        assert rows[gate] == ("FAIL", expected)

    rows = rows_when_godot_says("Godot Engine v4.7.2.stable")
    for gate, verdict in verdicts.items():
        assert rows[gate][1].startswith(f"exit 0, but no '{verdict}_OK' line: Godot quit"), rows

    # The word inside a longer line is no verdict.
    rows_when_godot_says(
        *(f"note: X{verdict}_OK, NOT_{verdict}_OK_AT_ALL" for verdict in verdicts.values())
    )


def test_a_tool_script_that_does_not_compile_is_reported_at_once(tmp_path: Path) -> None:
    """Godot does not quit when the `--script` it was given fails to compile: it idles.
    Unasked, each tool gate would wait out its whole timeout (15 minutes by default)."""
    root, env = _project(tmp_path)
    log = tmp_path / "godot.log"
    env = env | {
        "FAKE_GODOT_LOG": str(log),
        "FAKE_GODOT_BREAK": "--check-only --script res://tools/validate_all.gd",
        "FAKE_GODOT_SAYS": 'ERROR: Failed to load script "res://tools/validate_all.gd"',
    }
    result = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    rows = _rows(result.stdout)
    assert rows["validate-all"] == (
        "FAIL",
        "tools/validate_all.gd (or a script it is built on) does not compile: it was not run",
    )
    assert 'Failed to load script "res://tools/validate_all.gd"' in result.stdout
    asked = log.read_text().splitlines()
    assert "--headless --check-only --script res://tools/validate_all.gd" in asked
    assert _tool_run("validate_all.gd") not in asked
    # The other tool compiles: asked first, then run.
    assert rows["project-lint"][0] == "pass"
    check, run = (
        asked.index("--headless --check-only --script res://tools/lint_project.gd"),
        asked.index(_tool_run("lint_project.gd")),
    )
    assert check < run


def test_shader_gate_runs_when_the_project_has_shaders(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    write(root / "shaders" / "glow.gdshader", "shader_type canvas_item;\n")
    result = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rows(result.stdout)["shader-lint"][0] == "pass"


def test_switches_reach_the_gates(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    result = run_tool(root, "check.ps1", "-Fix", "-Path", "scripts", "-SkipTests", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(root)
    assert "gdlint -Fix -Path scripts" in calls
    assert "typecheck -TimeoutSeconds 900 -Path scripts" in calls
    assert not any(call.startswith("tests") for call in calls)
    assert _rows(result.stdout)["tests"] == ("skipped", "-SkipTests")

    (root / "calls.txt").unlink()
    timed = run_tool(root, "check.ps1", "-TestTimeoutSeconds", "42", env=env)
    assert timed.returncode == 0, timed.stdout + timed.stderr
    assert "tests -TimeoutSeconds 42" in _calls(root)
    assert "gdlint " in _calls(root)  # no -Fix, no -Path


def test_bad_path_is_a_usage_error(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    result = run_tool(root, "check.ps1", "-Path", "no/such/file.gd", env=env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not exist" in result.stdout
    assert not (root / "calls.txt").exists()


def test_changed_needs_the_shard_runner(tmp_path: Path) -> None:
    """-Changed never falls back to another way of running tests."""
    root, env = _project(tmp_path)
    result = run_tool(root, "check.ps1", "-Changed", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert _rows(result.stdout)["tests"] == ("FAIL", "-Changed needs tools/test_shards.py")
    assert not any(call.startswith("tests") for call in _calls(root))


def test_missing_godot_fails_the_godot_gates(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    result = run_tool(root, "check.ps1", env=env | {"GODOT_BIN": str(tmp_path / "gone.exe")})
    assert result.returncode == 1, result.stdout + result.stderr
    rows = _rows(result.stdout)
    assert rows["project-lint"] == ("FAIL", "Godot not found")
    assert rows["validate-all"] == ("FAIL", "Godot not found")
    assert "GODOT_BIN does not name an executable file" in result.stdout


HEARTBEAT = """import sys
import time

for _ in range(600):
    with open(sys.argv[1], "a") as beat:
        beat.write(".")
    time.sleep(0.2)
"""


def test_a_hung_gate_is_killed_with_what_it_started_and_reported(tmp_path: Path) -> None:
    """The gate is a PowerShell that waits for a program of its own: both must go, or the
    program (a Godot, in a real run) keeps the machine busy after the check has ended."""
    root, env = _project(tmp_path)
    beat = tmp_path / "beat.txt"
    temp = tmp_path / "temp"
    temp.mkdir()
    env = env | {
        "KIT_PYTHON": sys.executable,
        "KIT_HEARTBEAT": str(write(tmp_path / "heartbeat.py", HEARTBEAT)),
        "KIT_BEAT": str(beat),
        "KIT_TOLD": str(tmp_path / "told.txt"),
        "TMPDIR": str(temp),
    }
    # As a developer's shell is: the check itself must ask its gates to keep no files and
    # to write no startup profile. (This check's own PowerShell then writes one, as any
    # does: into a folder of the test's.)
    forget(env, *QUIET_DOTNET, *NO_STARTUP_PROFILE)
    (tmp_path / "own").mkdir()
    env[STARTUP_PROFILE_VARIABLE] = str(tmp_path / "own")
    write(
        root / "tools" / "lint_gdscript.ps1",
        "Set-Content -LiteralPath $env:KIT_TOLD "
        '-Value "gather=$env:DOTNET_MultiCoreJitNoProfileGather"\n'
        "Write-Host 'about to hang'\n& $env:KIT_PYTHON $env:KIT_HEARTBEAT $env:KIT_BEAT\n",
    )
    # Long enough for the other gates to start on a busy machine.
    started = time.monotonic()
    result = run_tool(root, "check.ps1", "-GateTimeoutSeconds", "10", "-SkipTests", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    rows = _rows(result.stdout)
    assert rows["gdlint"] == ("FAIL", "timed out after 10 s")
    assert "about to hang" in result.stdout
    assert rows["typecheck"][0] == "pass"
    # The program ran, and nothing of it is left: its heartbeat has stopped. (Left alive
    # on Windows it keeps the check's output open, and whoever reads that waits for it.)
    beats = beat.stat().st_size
    assert beats > 0
    assert time.monotonic() - started < 90
    time.sleep(1.5)
    assert beat.stat().st_size == beats
    # Linux and macOS: a killed PowerShell leaves .NET's pipes in the temp folder unless it
    # was started with Get-KitQuietPowerShellEnvironment. (Windows keeps no such file, and
    # TMPDIR names no folder there.)
    left = sorted(path.name for path in temp.iterdir())
    assert [
        name for name in left if name.startswith(("clr-debug-pipe-", "dotnet-diagnostic-"))
    ] == []
    # Every system: the gate's PowerShell was told to write no startup profile.
    assert (tmp_path / "told.txt").read_text().strip() == "gather=1"
    # And this check's own went where the test put it (7.0 and 7.1 keep it elsewhere).
    if powershell_version() >= (7, 2):
        assert startup_profiles(tmp_path / "own"), "the check's own profile went elsewhere"


def test_scenes_gate_runs_devtools_validation_when_the_autoload_is_there(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    log = tmp_path / "godot.log"
    sessions = tmp_path / "sessions.log"
    env = env | {
        "FAKE_GODOT_LOG": str(log),
        "FAKE_GODOT_SESSION_LOG": str(sessions),
        "GODOT_AGENT_SESSION": "my-running-game",
    }
    validate = f"--headless --path {root} -- --devtools-validate"

    # The addon without its autoload: nothing would answer, so nothing is started.
    write(root / "addons" / "agent_devtools" / "dev_tools.gd", "extends Node\n")
    unregistered = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert unregistered.returncode == 0, unregistered.stdout + unregistered.stderr
    assert _rows(unregistered.stdout)["scenes"] == (
        "skipped",
        "the DevTools autoload is not registered in project.godot",
    )
    assert validate not in log.read_text()

    # Registered, and no main scene: no game starts. Godot would say so in a dialog and
    # wait for a click, with --headless too; the gate would sit there until its timeout.
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS_AUTOLOAD))
    sceneless = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert sceneless.returncode == 0, sceneless.stdout + sceneless.stderr
    assert _rows(sceneless.stdout)["scenes"] == (
        "skipped",
        "no main scene: project.godot sets no application/run/main_scene, so no game starts",
    )
    assert validate not in log.read_text()

    # Registered, by path or by the uid the editor writes.
    write(root / "addons" / "agent_devtools" / "dev_tools.gd.uid", "uid://qba8jpa33rfr\n")
    for entry in (DEVTOOLS_AUTOLOAD["DevTools"], "*uid://qba8jpa33rfr"):
        log.unlink()
        project = project_godot(autoloads={"DevTools": entry}, main_scene="res://main.tscn")
        write(root / "project.godot", project)
        said = env | {
            "FAKE_GODOT_BREAK": "--devtools-validate",
            "FAKE_GODOT_SAYS": "DevTools: Scenes valid: 3 checked, 0 errors, 1 warnings",
        }
        passed = run_tool(root, "check.ps1", "-SkipTests", env=said)
        assert passed.returncode == 0, passed.stdout + passed.stderr
        assert _rows(passed.stdout)["scenes"] == (
            "pass",
            "DevTools: Scenes valid: 3 checked, 0 errors, 1 warnings",
        )
        asked = log.read_text().splitlines()
        assert validate in asked
        # After validate-all, before the test lint.
        assert asked.index(validate) == len(asked) - 1
    # Its own DevTools mailbox, not the one of a game the developer has running; every
    # other Godot run keeps the caller's session.
    seen = sessions.read_text().splitlines()
    assert seen.count("session=check") == 2 and seen[-1] == "session=check"
    assert set(seen) == {"session=check", "session=my-running-game"}

    failing = env | {
        "FAKE_GODOT_BREAK": "--devtools-validate",
        "FAKE_GODOT_SAYS": "DevTools: validation failed: 1 of 3 scenes fail: 2 errors, 0 warnings",
        "FAKE_GODOT_EXIT": "1",
    }
    failed = run_tool(root, "check.ps1", "-SkipTests", env=failing)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert _rows(failed.stdout)["scenes"] == (
        "FAIL",
        "exit 1: DevTools: validation failed: 1 of 3 scenes fail: 2 errors, 0 warnings",
    )
    assert "check: FAILED (scenes)" in failed.stdout

    # A game that quits by itself before DevTools has looked: exit 0 and no verdict.
    quit_first = env | {
        "FAKE_GODOT_BREAK": "--devtools-validate",
        "FAKE_GODOT_SAYS": "[system] DevTools initialized",
    }
    unseen = run_tool(root, "check.ps1", "-SkipTests", env=quit_first)
    assert unseen.returncode == 1, unseen.stdout + unseen.stderr
    no_verdict = "exit 0, but no 'DevTools: Scenes valid' line: Godot quit before the verdict"
    assert _rows(unseen.stdout)["scenes"] == ("FAIL", f"{no_verdict} (an autoload?)")


AGENTS_LINT = """import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "AGENTS.md")
if "TODO" in path.read_text(encoding="utf-8"):
    print(f"{path}:3: a TODO is left in the file")
    sys.exit(1)
print("AGENTS.md: ok")
"""


def _python_env(tmp_path: Path, env: dict[str, str]) -> dict[str, str]:
    """A stand-in uv that really runs `uv run python <script>` and only echoes the rest
    (ruff, pytest): nothing is installed and nothing leaves this machine."""
    fake_uv(tmp_path / "bin")
    return env | {
        "PATH": str(tmp_path / "bin") + os.pathsep + env["PATH"],
        "FAKE_UV_PYTHON": sys.executable,
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
    }


def test_agents_md_gate_needs_the_file_and_its_lint(tmp_path: Path) -> None:
    root, env = _project(tmp_path)
    env = _python_env(tmp_path, env)
    write(root / ".venv" / "pyvenv.cfg", "")  # no first `uv sync`

    write(root / "tools" / "check_agents_md.py", AGENTS_LINT)
    no_file = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert _rows(no_file.stdout)["agents-md"] == ("skipped", "AGENTS.md not present")

    write(root / "AGENTS.md", "# AGENTS.md\n\nRules.\n")
    clean = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert _rows(clean.stdout)["agents-md"] == ("pass", "AGENTS.md: ok")

    write(root / "AGENTS.md", "# AGENTS.md\n\nTODO\n")
    finding = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert finding.returncode == 1, finding.stdout + finding.stderr
    assert _rows(finding.stdout)["agents-md"] == (
        "FAIL",
        "exit 1: AGENTS.md:3: a TODO is left in the file",
    )

    (root / "tools" / "check_agents_md.py").unlink()
    no_lint = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert no_lint.returncode == 0, no_lint.stdout + no_lint.stderr
    assert _rows(no_lint.stdout)["agents-md"] == ("skipped", "tools/check_agents_md.py not present")


def test_architecture_gate_is_skipped_when_no_layers_are_declared(tmp_path: Path) -> None:
    """Without its manifest tools/check_architecture.py prints "not configured" and exits
    0. That was a green "pass" row for a check that looked at nothing."""
    root, env = _project(tmp_path)
    env = _python_env(tmp_path, env)
    write(root / ".venv" / "pyvenv.cfg", "")
    copy_python_tools(root)
    shutil.copyfile(
        REPO / "tools" / "check_architecture.py", root / "tools" / "check_architecture.py"
    )

    bare = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert _rows(bare.stdout)["architecture"] == (
        "skipped",
        "not configured: no tools/ci/architecture_modules.json (docs/architecture.md)",
    )
    assert "check_architecture.py" not in (tmp_path / "uv.log").read_text()

    layers = (
        '{"modules": {"game": {"globs": ["scripts/**"], "may_depend": []}}, "ignore": ["tools/**"]}'
    )
    write(root / "tools" / "ci" / "architecture_modules.json", layers)
    declared = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert declared.returncode == 0, declared.stdout + declared.stderr
    assert _rows(declared.stdout)["architecture"] == ("pass", "architecture: ok")

    write(root / "elsewhere" / "stray.gd", "extends Node\n")
    broken = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert _rows(broken.stdout)["architecture"] == ("FAIL", "exit 1: architecture: FAIL (1)")
    assert "no module: elsewhere/stray.gd" in broken.stdout


def test_the_changed_tests_row_shows_the_runners_verdict_not_its_last_line(
    tmp_path: Path,
) -> None:
    """tools/test_shards.py ends with a list of slow suites, which is no verdict: a failed
    run shows the runner's summary line, and a run with no suite at all is `skipped`."""
    root, env = _project(tmp_path)
    for script in (*STUB_GATES.values(), "lint_project.gd", "lint_shaders.gd", "validate_all.gd"):
        (root / "tools" / script).unlink()
    env = _python_env(tmp_path, env)
    write(root / ".venv" / "pyvenv.cfg", "")
    slowest = "Slowest suites (serial seconds):\\n   0.60s  res://test/unit/test_ok.gd"

    def tests_row(summary: str, code: int) -> tuple[str, str]:
        runner = f"import sys\nprint('{summary}\\n{slowest}')\nsys.exit({code})\n"
        write(root / "tools" / "test_shards.py", runner)
        result = run_tool(root, "check.ps1", "-Changed", env=env)
        assert result.returncode == code, result.stdout + result.stderr
        return _rows(result.stdout)["tests"]

    failed = "2/2 suites, 23 test cases, 14 failures/errors in 4.8s wall"
    assert tests_row(failed, 1) == ("FAIL", f"exit 1: {failed}")
    passed = "2/2 suites, 23 test cases, 0 failures/errors in 4.8s wall"
    assert tests_row(passed, 0) == ("pass", passed)
    assert tests_row("No suites to run.", 0) == (
        "skipped",
        "-Changed: the change selects no suite",
    )


GODOT_NOISE = """SCRIPT ERROR: Parse Error: Variable "target" has no static type.
          at: GDScript::reload (res://scripts/core/board.gd:29)
          GDScript backtrace (most recent call first):
              [0] _collect (res://tools/lint_project.gd:633)
              [1] _main (res://tools/lint_project.gd:170)
ERROR: Failed to load script "res://scripts/core/board.gd" with error "Parse error".
   at: load (modules/gdscript/gdscript.cpp:3041)
SCRIPT ERROR: Parse Error: Variable "target" has no static type.
          at: GDScript::reload (res://scripts/core/board.gd:29)
          GDScript backtrace (most recent call first):
              [0] _load (res://tools/validate_all.gd:75)
ERROR: Failed to load script "res://scripts/core/board.gd" with error "Parse error".
   at: load (modules/gdscript/gdscript.cpp:3041)
FAIL res://scripts/core/board.gd
"""


def test_a_failed_godot_gate_prints_each_error_once_and_no_backtrace(tmp_path: Path) -> None:
    """One script that does not compile is reported again by everything that loads it,
    each time with a backtrace into the kit's own tool: the same two lines, many times."""
    root, env = _project(tmp_path)
    said = tmp_path / "said.txt"
    said.write_text(GODOT_NOISE, encoding="ascii")
    env = env | {
        "FAKE_GODOT_BREAK": _tool_run("validate_all.gd"),
        "FAKE_GODOT_SAYS_FILE": str(said),
        "FAKE_GODOT_EXIT": "1",
    }
    result = run_tool(root, "check.ps1", "-SkipTests", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    shown = result.stdout.split("----- validate-all:", 1)[1].split("----- end validate-all", 1)[0]
    assert shown.count('Variable "target" has no static type') == 1
    assert shown.count("(res://scripts/core/board.gd:29)") == 1
    assert shown.count("Failed to load script") == 1
    assert "GDScript backtrace" not in shown and "tools/validate_all.gd:75" not in shown
    assert "FAIL res://scripts/core/board.gd" in shown  # the tool's own finding stays
    # Nothing is lost: the log has every line.
    log = (root / "artifacts" / "check" / "validate-all.log").read_text()
    assert log.count("GDScript backtrace") == 2 and "tools/validate_all.gd:75" in log


def _git(root: Path, *args: str) -> None:
    identity = ["-c", "user.name=Kit Test", "-c", "user.email=kit@example.invalid"]
    done = subprocess.run(
        ["git", *identity, *args], cwd=root, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr


PYTEST_SKIPPED = (
    "pytest ... skipped: -Changed: kit files unchanged vs main "
    "(tools/, addons/agent_devtools/, kit.json, pyproject.toml, uv.lock)"
)
ON_BASE_BRANCH = (
    "-Changed: on 'main', the base branch itself: commits made on it do not count as changed, "
    "only uncommitted and untracked files. A full check.ps1 covers the rest."
)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_changed_runs_pytest_only_when_the_kit_itself_changed(tmp_path: Path) -> None:
    """pytest tools/tests checks the kit's tools and takes minutes; a change to the game
    cannot break it. -Changed asks git (the base rule of tools/test_shards.py)."""
    root, env = _project(tmp_path)
    # Only the pytest row and the last lines are read here. Without the other gates' tools
    # (each is then "skipped") a run starts far fewer processes, and there are 14 runs.
    for script in (*STUB_GATES.values(), "lint_project.gd", "lint_shaders.gd", "validate_all.gd"):
        (root / "tools" / script).unlink()
    env = _python_env(tmp_path, env)
    copy_python_tools(root)
    write(root / ".venv" / "pyvenv.cfg", "")
    write(root / "tools" / "test_shards.py", "print('No suites to run.')\n")
    write(root / "tools" / "tests" / "test_nothing.py", "def test_nothing() -> None:\n    pass\n")
    write(root / ".gitignore", ".venv/\nartifacts/\ncalls.txt\n__pycache__/\n")

    outputs: list[str] = []

    def pytest_row(*switches: str) -> tuple[str, str]:
        """("pass", ...) when pytest ran; ("skipped", the whole reason) when it did not."""
        result = run_tool(root, "check.ps1", *switches, env=env)
        assert "could not ask git" not in result.stdout, result.stdout
        outputs.append(result.stdout)
        status, detail = _rows(result.stdout)["pytest"]
        if status == "skipped":
            # The table cuts a long detail short; the line printed as the gate ends does not.
            detail = next(line for line in result.stdout.splitlines() if line.startswith("pytest "))
        return status, detail

    # No git repository: nobody can say what changed, so it runs.
    assert pytest_row("-Changed")[0] == "pass"

    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "start")
    assert pytest_row("-Changed") == ("skipped", PYTEST_SKIPPED)
    # The runner had no suite to run: nothing was tested, so the row is not a pass.
    assert _rows(outputs[-1])["tests"] == ("skipped", "-Changed: the change selects no suite")
    # On main there is no commit to compare with: the run ends by saying so.
    assert outputs[-1].splitlines()[-2].startswith(ON_BASE_BRANCH), outputs[-1]
    # A full check always runs it.
    assert pytest_row()[0] == "pass"
    assert "base branch itself" not in outputs[-1]

    # A change to the game, committed or not, tracked or not: still skipped.
    write(root / "scripts" / "player.gd", "extends Node2D\n")
    write(root / "scripts" / "new_enemy.gd", "extends Node\n")
    write(root / "test" / "unit" / "test_enemy.gd", "extends GdUnitTestSuite\n")
    write(root / "addons" / "third_party" / "plugin.gd", "extends Node\n")
    write(root / "tooling" / "notes.md", "not the tools folder\n")
    if LINUX:
        # Another letter case is another folder there, and another file.
        write(root / "Tools" / "notes.md", "not the tools folder either\n")
        write(root / "Kit.json", "{}\n")
    assert pytest_row("-Changed") == ("skipped", PYTEST_SKIPPED)
    _git(root, "checkout", "--quiet", "-b", "work")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "game work")
    assert pytest_row("-Changed") == ("skipped", PYTEST_SKIPPED)
    assert "base branch itself" not in outputs[-1]

    # Each of these belongs to the kit: one changed path, however it changed, and
    # pytest is back.
    def touch_kit_json() -> None:
        write_kit_json(root, tests={"root": "test"}, gdscript={"strict": True})

    def edit_a_tool() -> None:
        with (root / "tools" / "kit_config.py").open("a", encoding="utf-8") as tool:
            tool.write("\n# edited\n")

    kit_changes = {
        "an untracked tool": lambda: write(root / "tools" / "new_tool.py", "VALUE = 2\n"),
        "an edited tool": edit_a_tool,
        "the addon": lambda: write(root / "addons" / "agent_devtools" / "x.gd", "extends Node\n"),
        "kit.json": touch_kit_json,
        "pyproject.toml": lambda: write(root / "pyproject.toml", "[project]\n"),
        "uv.lock": lambda: write(root / "uv.lock", "version = 1\n"),
    }
    for name, change in kit_changes.items():
        change()
        assert pytest_row("-Changed")[0] == "pass", name
        # The slowest gate says which file made it run, and against which base.
        why = r"^pytest: -Changed: \S+ differs from main; running it\.$"
        assert re.search(why, outputs[-1], re.M), outputs[-1]
        # Undo it, so the next change is the only one.
        _git(root, "add", "-A")
        _git(root, "stash", "--quiet")
        _git(root, "stash", "drop", "--quiet")
    assert pytest_row("-Changed") == ("skipped", PYTEST_SKIPPED)

    # Committed on the branch counts too: the diff is against the base branch.
    write(root / "tools" / "helper.py", "VALUE = 3\n")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "tool work")
    assert pytest_row("-Changed")[0] == "pass"

    # Merged into a main that has no remote copy, the same commit is HEAD and the base at
    # once. git cannot tell it from the past: pytest is skipped, and the run says why.
    _git(root, "checkout", "--quiet", "main")
    _git(root, "merge", "--quiet", "work")
    assert pytest_row("-Changed") == ("skipped", PYTEST_SKIPPED)
    assert outputs[-1].splitlines()[-2].startswith(ON_BASE_BRANCH), outputs[-1]
