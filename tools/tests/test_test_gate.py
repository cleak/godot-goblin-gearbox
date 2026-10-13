"""tools/test.ps1: what it asks gdUnit4 to run, and that only a real pass is a pass.

gdUnit4 exits 0 after a parse error and after finding no tests, so the wrapper reads the
output as well as the exit code. Godot is a stand-in in most cases: they pin the wrapper's
verdicts. One case runs the real engine and the real gdUnit4.
"""

import re
import shutil
import time
from pathlib import Path

import pytest
from _gate_fixtures import (
    EMPTY_CLASS_CACHE,
    MACOS,
    NEEDS_PWSH,
    REPO,
    USER_DATA_VARIABLE,
    clean_env,
    copy_tools,
    fake_godot,
    link_folder,
    posix_only,
    project_godot,
    real_godot,
    run_tool,
    user_data_env,
    write,
    write_kit_json,
)

pytestmark = NEEDS_PWSH

SUMMARY = "Overall Summary: 3 test cases | 0 errors | 0 failures | 0 flaky | 0 skipped | 0 orphans"
RUNNER = "GdUnitCmdTool.gd"


def _project(tmp_path: Path, test_root: str = "test") -> tuple[Path, dict[str, str], Path]:
    """A project test.ps1 accepts, a fake Godot, and the log of what that Godot was asked."""
    root = tmp_path / "project"
    copy_tools(root, "test.ps1")
    write_kit_json(root, tests={"root": test_root})
    write(root / "project.godot", project_godot())
    write(root / "addons" / "gdUnit4" / "bin" / RUNNER, "extends SceneTree\n")
    write(root / ".godot" / "global_script_class_cache.cfg", EMPTY_CLASS_CACHE)
    write(root / "test" / "unit" / "test_x.gd", "extends GdUnitTestSuite\n")
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))
    return root, env, log


def _gdunit(tmp_path: Path, env: dict[str, str], output: str, exit_code: int = 0) -> dict[str, str]:
    """The environment in which the fake Godot's test run prints `output` and exits `exit_code`."""
    says = tmp_path / "says.txt"
    says.write_text(output, encoding="ascii")
    return env | {
        "FAKE_GODOT_BREAK": RUNNER,
        "FAKE_GODOT_SAYS_FILE": str(says),
        "FAKE_GODOT_EXIT": str(exit_code),
    }


FAILED_SUMMARY = SUMMARY.replace("0 failures", "1 failures")
COLOURED_ERROR = "\x1b[38;2;255;0;0mSCRIPT ERROR\x1b[0m: x\n" + SUMMARY
COLOURED_SUMMARY = "\x1b[1mOverall Summary:\x1b[0m 3 test cases | 0 errors | 0 failures"
VERDICTS = {
    # name: (what gdUnit4 printed, its exit code, the wrapper's exit code, its last line)
    "pass": (SUMMARY, 0, 0, "Tests: PASSED. 3 test cases | 0 errors"),
    "warnings": (SUMMARY, 101, 0, "Tests: PASSED with warnings. 3 test cases"),
    "failures": (FAILED_SUMMARY, 100, 1, "Tests: FAILED. 3 test cases | 0 errors | 1 failures"),
    # Exit 0, but the suites never ran or did not load.
    "no_summary": ("Run tests ...\n", 0, 1, "gdUnit4 printed no 'Overall Summary'"),
    "parse_error": ("SCRIPT ERROR: Parse Error: x\n" + SUMMARY, 0, 1, "'SCRIPT ERROR' in the"),
    "load_error": ('Failed to load script "res://a.gd"\n' + SUMMARY, 0, 1, "'Failed to load scr"),
    "no_tests": ("No test cases found, abort!\n" + SUMMARY, 0, 1, "'No test cases found' in the"),
    # The colour codes gdUnit4 prints must not hide any of it.
    "coloured_error": (COLOURED_ERROR, 0, 1, "'SCRIPT ERROR' in the"),
    "coloured_summary": (COLOURED_SUMMARY, 0, 0, "Tests: PASSED. 3 test cases"),
    # A crash, or a script that quit on its own: Godot's code, never 0.
    "crash": (SUMMARY, 3, 3, "Tests: FAILED (Godot exited 3)."),
}


@pytest.mark.parametrize(
    ("output", "godot_exit", "expected", "last_line"), VERDICTS.values(), ids=VERDICTS.keys()
)
def test_verdict(
    tmp_path: Path, output: str, godot_exit: int, expected: int, last_line: str
) -> None:
    root, env, _ = _project(tmp_path)
    result = run_tool(root, "test.ps1", env=_gdunit(tmp_path, env, output, godot_exit))
    assert result.returncode == expected, result.stdout + result.stderr
    assert last_line in result.stdout.strip().splitlines()[-1]


def test_what_gdunit_is_asked_to_run(tmp_path: Path) -> None:
    root, env, log = _project(tmp_path)
    env = _gdunit(tmp_path, env, SUMMARY)

    default = run_tool(root, "test.ps1", env=env)
    assert default.returncode == 0, default.stdout + default.stderr
    assert SUMMARY in default.stdout  # gdUnit4's own output is shown
    # -c: every test case runs (gdUnit4 alone stops at the first failure), as with
    # tools/test_shards.py.
    assert log.read_text().splitlines() == [
        "--headless -s res://addons/gdUnit4/bin/GdUnitCmdTool.gd --ignoreHeadlessMode "
        "-rd res://artifacts/gdunit-reports -a res://test -c"
    ]
    # Godot must not import the reports it writes under artifacts/.
    assert (root / "artifacts" / ".gdignore").is_file()

    # A folder or a suite, written any of three ways, reaches gdUnit4 as a res:// path.
    suite = root / "test" / "unit" / "test_x.gd"
    for given in ("res://test/unit/test_x.gd", "test/unit/test_x.gd", str(suite)):
        log.unlink()
        one = run_tool(root, "test.ps1", "-Test", given, "-FailFast", env=env)
        assert one.returncode == 0, one.stdout + one.stderr
        assert log.read_text().strip().endswith("-a res://test/unit/test_x.gd")
        assert "Mode: stop at the first failure" in one.stdout

    # The path alone, as tools/test_shards.py takes it.
    log.unlink()
    bare = run_tool(root, "test.ps1", "test/unit/test_x.gd", env=env)
    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert log.read_text().strip().endswith("-a res://test/unit/test_x.gd -c")
    # A second path is refused; it is not read as the timeout.
    two = run_tool(root, "test.ps1", "test/unit", "test/other", env=env)
    assert two.returncode == 1 and "test/other" in two.stdout + two.stderr

    # A switch it does not have is refused, not silently ignored.
    unknown = run_tool(root, "test.ps1", "-Continue", env=env)
    assert unknown.returncode == 1 and "Continue" in unknown.stdout + unknown.stderr


def test_the_run_gets_a_user_folder_of_its_own(tmp_path: Path) -> None:
    """A suite that writes a save must not find, or replace, the saves of the game its
    developer plays: tools/test_shards.py isolates every shard, and the full check runs
    this script. The variable that moves user:// is this system's (USER_DATA_VARIABLE),
    and only Godot gets it."""
    root, env, _ = _project(tmp_path)
    seen = tmp_path / "appdata.log"
    mine = tmp_path / "my real user data"
    mine.mkdir()
    env = _gdunit(tmp_path, env, SUMMARY) | {
        "FAKE_GODOT_APPDATA_LOG": str(seen),
        USER_DATA_VARIABLE: str(mine),
    }
    for _ in range(2):
        result = run_tool(root, "test.ps1", env=env)
        assert result.returncode == 0, result.stdout + result.stderr
    used = [Path(line.removeprefix("appdata=").strip()) for line in seen.read_text().splitlines()]
    assert len(used) == 2 and used[0] != used[1]
    for folder in used:
        # The folder itself, or on macOS Library/Application Support inside it.
        own = [part for part in folder.parts if part.startswith("godot_test_user_")]
        assert len(own) == 1 and not folder.is_relative_to(mine), folder
        assert not folder.exists()  # removed after the run
    # On macOS the variable is HOME, where the PowerShell of this run keeps caches too.
    assert MACOS or list(mine.iterdir()) == []


@posix_only("a temp folder named by a relative path (Windows makes its own absolute)")
def test_a_relative_temp_folder_still_gives_the_run_its_own_user_folder(tmp_path: Path) -> None:
    """Godot ignores a relative XDG_DATA_HOME: handed one, the run would use the folder of
    the game its developer plays, and pass."""
    root, env, _ = _project(tmp_path)
    seen = tmp_path / "appdata.log"
    (tmp_path / "tmp").mkdir()  # run_tool starts the script in tmp_path
    env = _gdunit(tmp_path, env, SUMMARY) | {"FAKE_GODOT_APPDATA_LOG": str(seen), "TMPDIR": "tmp"}
    result = run_tool(root, "test.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    used = Path(seen.read_text().strip().removeprefix("appdata="))
    own = [part for part in used.parts if part.startswith("godot_test_user_")]
    assert len(own) == 1 and used.is_relative_to(tmp_path / "tmp"), used
    assert list((tmp_path / "tmp").iterdir()) == []  # removed after the run


def test_the_project_may_be_reached_through_a_link(tmp_path: Path) -> None:
    """A checkout under a linked folder has two spellings, and a shell on Linux starts the
    script by the real one whatever the user typed. An absolute -Test in either spelling
    names the same suite."""
    root, env, log = _project(tmp_path)
    env = _gdunit(tmp_path, env, SUMMARY)
    link = tmp_path / "link to the project"
    link_folder(link, root)
    suite = Path("test") / "unit" / "test_x.gd"
    for started, given in ((root, link / suite), (link, root / suite), (link, link / suite)):
        log.unlink(missing_ok=True)
        result = run_tool(started, "test.ps1", "-Test", str(given), env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.read_text().strip().endswith("-a res://test/unit/test_x.gd -c"), given
    # What is not there is "not found", in either spelling; what is outside stays outside.
    missing = run_tool(root, "test.ps1", "-Test", str(link / "test" / "nope"), env=env)
    assert missing.returncode == 1 and "Test path not found" in missing.stdout, missing.stdout
    outside = run_tool(link, "test.ps1", "-Test", str(tmp_path / "bin"), env=env)
    assert outside.returncode == 1, outside.stdout + outside.stderr
    assert "is not a path inside the project" in outside.stdout


@pytest.mark.parametrize("test_root", ["specs", "res://specs", "specs/"])
def test_test_root_comes_from_kit_json(tmp_path: Path, test_root: str) -> None:
    root, env, log = _project(tmp_path, test_root=test_root)
    write(root / "specs" / "test_y.gd", "extends GdUnitTestSuite\n")
    result = run_tool(root, "test.ps1", env=_gdunit(tmp_path, env, SUMMARY))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Test path: res://specs" in result.stdout
    assert log.read_text().strip().endswith("-a res://specs -c")


def test_nothing_runs_when_the_request_is_wrong(tmp_path: Path) -> None:
    """Pointed at nothing gdUnit4 exits 0: the wrapper says what is wrong and Godot never starts."""
    root, env, log = _project(tmp_path)
    env = _gdunit(tmp_path, env, SUMMARY)

    missing = run_tool(root, "test.ps1", "-Test", "res://test/nope", env=env)
    assert missing.returncode == 1 and "Test path not found" in missing.stdout

    outside = run_tool(root, "test.ps1", "-Test", str(tmp_path), env=env)
    assert outside.returncode == 1 and "is not a path inside the project" in outside.stdout

    write_kit_json(root, tests={"root": "../elsewhere"})
    wrong_root = run_tool(root, "test.ps1", env=env)
    assert wrong_root.returncode == 1
    assert (
        'Tests: kit.json: "tests.root" must be a folder inside the project, not "../elsewhere"'
        in wrong_root.stdout
    )

    write(root / "kit.json", "{ not json")
    broken = run_tool(root, "test.ps1", env=env)
    assert broken.returncode == 1 and "Tests: kit.json is not valid JSON" in broken.stdout
    assert broken.stderr.strip() == ""  # one line, not a PowerShell error record

    write_kit_json(root)
    (root / "addons" / "gdUnit4" / "bin" / RUNNER).unlink()
    no_gdunit = run_tool(root, "test.ps1", env=env)
    assert no_gdunit.returncode == 1 and "gdUnit4 is not installed" in no_gdunit.stdout

    no_godot = run_tool(root, "test.ps1", env=env | {"GODOT_BIN": str(tmp_path / "gone.exe")})
    assert no_godot.returncode == 2, no_godot.stdout + no_godot.stderr
    assert not log.exists()


def test_missing_or_stale_class_cache_is_rebuilt_first(tmp_path: Path) -> None:
    """gdUnit4's runner names its own classes; without the cache it dies on a parse error."""
    root, env, log = _project(tmp_path)
    cache = root / ".godot" / "global_script_class_cache.cfg"
    cache.unlink()
    env = _gdunit(tmp_path, env, SUMMARY) | {"FAKE_GODOT_CACHE": str(cache)}

    result = run_tool(root, "test.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No class cache yet" in result.stdout
    asked = log.read_text().splitlines()
    assert asked[0] == "--headless --import"
    assert RUNNER in asked[-1] and len(asked) == 2

    log.unlink()
    again = run_tool(root, "test.ps1", env=env)
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(log.read_text().splitlines()) == 1  # fresh now: straight to the tests


PASSING_SUITE = (
    "extends GdUnitTestSuite\n\n\nfunc test_one() -> void:\n\tassert_int(1).is_equal(1)\n"
)
FAILING_SUITE = (
    "extends GdUnitTestSuite\n\n\n"
    "func test_first() -> void:\n\tassert_int(1).is_equal(2)\n\n\n"
    "func test_second() -> void:\n\tassert_int(3).is_equal(4)\n"
)


def _failures(stdout: str) -> int:
    counted = re.search(r"^Tests: FAILED\. .*?(\d+) failures", stdout, re.MULTILINE)
    assert counted is not None, stdout
    return int(counted.group(1))


@pytest.mark.real_godot
def test_real_gdunit4_in_a_folder_with_spaces_and_brackets(tmp_path: Path) -> None:
    """The real engine and the real gdUnit4, in a project folder named the way people
    name them. A path with a space must reach gdUnit4 as one argument, and brackets
    must not stop the start."""
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    root = tmp_path / "it's a [wip] game"
    copy_tools(root, "test.ps1")
    write_kit_json(root)
    write(root / "project.godot", project_godot())
    shutil.copytree(REPO / "addons" / "gdUnit4", root / "addons" / "gdUnit4")
    write(root / "test" / "unit" / "test_ok.gd", PASSING_SUITE)
    # The run's own user:// is test.ps1's doing; the import before it is this test's.
    env = clean_env(GODOT_BIN=godot, **user_data_env(tmp_path / "user data"))

    passed = run_tool(root, "test.ps1", env=env, timeout=400)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert "Tests: PASSED. 1 test cases | 0 errors | 0 failures" in passed.stdout
    assert "Unknown '" not in passed.stdout
    assert (root / "artifacts" / "gdunit-reports").is_dir()  # inside the project, not beside it

    # Every test case runs by default: the second failure is reported too.
    write(root / "test" / "unit" / "test_bad.gd", FAILING_SUITE)
    failed = run_tool(root, "test.ps1", env=env, timeout=400)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert _failures(failed.stdout) == 2
    assert "test_second" in failed.stdout

    # -FailFast is gdUnit4's own default: it stops at the first.
    fail_fast = run_tool(root, "test.ps1", "-FailFast", env=env, timeout=400)
    assert fail_fast.returncode == 1, fail_fast.stdout + fail_fast.stderr
    assert _failures(fail_fast.stdout) == 1

    one = run_tool(root, "test.ps1", "-Test", "test/unit/test_ok.gd", env=env, timeout=400)
    assert one.returncode == 0, one.stdout + one.stderr


def test_timeout_kills_the_run(tmp_path: Path) -> None:
    root, env, _ = _project(tmp_path)
    env = _gdunit(tmp_path, env, "Run tests ...\n") | {"FAKE_GODOT_SLEEP": "60"}
    started = time.monotonic()
    result = run_tool(root, "test.ps1", "-TimeoutSeconds", "3", env=env)
    assert result.returncode == 124, result.stdout + result.stderr
    assert "TIMEOUT: tests exceeded 3s" in result.stdout
    assert "Run tests ..." in result.stdout  # what it printed before it hung is kept
    assert time.monotonic() - started < 45
