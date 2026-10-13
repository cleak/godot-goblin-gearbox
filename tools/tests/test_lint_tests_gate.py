"""tools/lint_tests.ps1: what it accepts, what it flags, and that it cannot pass on nothing.

Which script is a suite is tools/ci/suites.py's answer (the test runner's rule), so every
case here starts one real `uv run python`.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest
from _gate_fixtures import (
    PWSH,
    REPO,
    clean_env,
    copy_python_tools,
    copy_tools,
    run_tool,
    write,
    write_kit_json,
)

sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "ci"))
import kit_config  # noqa: E402
import suites  # noqa: E402

pytestmark = pytest.mark.skipif(
    PWSH is None or shutil.which("uv") is None, reason="needs PowerShell 7 (pwsh) and uv"
)

# Everything a well-formed suite may look like: blank lines and comments inside a body, a
# signature that spans lines, a one-line test, a shared assertion helper, mock verification.
CLEAN_SUITE = """extends GdUnitTestSuite


func test_blank_line_before_the_assertion() -> void:
	var value: int = 2

	# the assertion comes after a gap
	assert_int(value).is_equal(2)


func test_parameterized(
	value: int, expected: int, test_parameters := [[1, 1], [2, 2]]
) -> void:
	assert_int(value).is_equal(expected)


func test_one_line() -> void: assert_bool(true).is_true()


func test_uses_a_helper() -> void:
	_assert_roundtrip("a")


func test_verifies_a_mock() -> void:
	var node: Node = mock(Node)
	verify(node, 0).queue_free()


func test_bounded_loop() -> void:
	var turns: int = 0
	while turns < 3:
		turns += 1
	assert_int(turns).is_equal(3)


func _assert_roundtrip(text: String) -> void:
	assert_str(text).is_equal(text)
"""


def _project(tmp_path: Path, test_root: str = "test") -> Path:
    root = tmp_path / "project"
    copy_tools(root, "lint_tests.ps1")
    copy_python_tools(root)
    write_kit_json(root, tests={"root": test_root})
    return root


def test_clean_suite_passes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "test" / "unit" / "test_clean.gd", CLEAN_SUITE)
    write(root / "test" / "unit" / "helper.gd", "extends RefCounted\n")  # no suite: not linted
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Test lint: CLEAN (1 file(s))" in result.stdout


def test_a_suite_is_what_the_runner_runs_whatever_its_file_name(tmp_path: Path) -> None:
    """gdUnit4 goes by what a script extends, not by `test_*.gd`. A suite under another
    name is run, so it is linted too."""
    root = _project(tmp_path)
    write(
        root / "test" / "board_checks.gd",
        "extends GdUnitTestSuite\n\n\nfunc test_asserts_nothing() -> void:\n\tpass\n",
    )
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "test/board_checks.gd:4: test 'test_asserts_nothing' has no assertion" in result.stdout


def test_the_lint_and_the_runner_agree_on_every_script(tmp_path: Path) -> None:
    """One rule, asked twice: the lint reports exactly the scripts tools/ci/suites.py
    calls a suite or a stray, for every header form in one folder."""
    root = _project(tmp_path)
    weak = "\n\n\nfunc test_x() -> void:\n\tpass\n"
    scripts = {
        "test/test_plain.gd": "extends GdUnitTestSuite" + weak,
        "test/named.gd": "class_name NamedSuite extends GdUnitTestSuite" + weak,
        "test/test_tool.gd": "@tool\nextends GdUnitTestSuite" + weak,
        "test/test_by_path.gd": 'extends "res://addons/gdUnit4/src/GdUnitTestSuite.gd"' + weak,
        "test/base/base_suite.gd": "class_name BaseSuite\nextends GdUnitTestSuite\n",
        "test/test_derived.gd": "extends BaseSuite" + weak,
        "test/test_beside.gd": 'extends "base/base_suite.gd"' + weak,
        "test/test_outer.gd": "extends OuterSuite" + weak,
        "test/test_inherits_only.gd": 'extends "test_plain.gd"\n',
        "test/test_stray.gd": "extends Node" + weak,
        "test/test_no_extends.gd": "func test_x() -> void:\n\tpass\n",
        "test/helper.gd": "extends RefCounted\n\n\nfunc make() -> int:\n\treturn 1\n",
        "test/test_named_like_one.gd": "extends Node\n",
        "scripts/outer_suite.gd": "class_name OuterSuite\nextends GdUnitTestSuite\n",
    }
    for name, text in scripts.items():
        write(root / name, text)
    described = suites.describe(kit_config.load(root))
    kinds = {entry["path"]: entry["kind"] for entry in described["scripts"]}
    assert kinds == {
        "test/base/base_suite.gd": "base",
        "test/helper.gd": "other",
        "test/named.gd": "suite",
        "test/test_beside.gd": "suite",
        "test/test_by_path.gd": "suite",
        "test/test_derived.gd": "suite",
        "test/test_inherits_only.gd": "suite",
        "test/test_named_like_one.gd": "other",
        "test/test_no_extends.gd": "not_a_suite",
        "test/test_outer.gd": "suite",
        "test/test_plain.gd": "suite",
        "test/test_stray.gd": "not_a_suite",
        "test/test_tool.gd": "suite",
    }

    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 2, result.stdout + result.stderr
    flagged = {
        line.strip().split(":")[0]
        for line in result.stdout.splitlines()
        if line.startswith("  test/")
    }
    # Every suite with a test of its own is linted (the weak test), every stray is an
    # error, and nothing else is mentioned.
    linted = {path for path, kind in kinds.items() if kind in ("suite", "not_a_suite")}
    assert flagged == linted - {"test/test_inherits_only.gd"}
    assert "test/test_stray.gd:1: has test functions, so it must 'extends GdUnitTestSuite'" in (
        result.stdout
    )
    assert "(it has: extends Node)" in result.stdout and "(it has: no extends)" in result.stdout
    assert "test_inherits_only.gd" not in result.stdout  # it runs test_plain's test


def test_named_suite_on_one_line_is_a_suite(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(
        root / "test" / "test_named.gd",
        "class_name NamedSuite extends GdUnitTestSuite\n\n\n"
        "func test_x() -> void:\n\tassert_bool(true).is_true()\n",
    )
    write(
        root / "test" / "test_commented.gd",
        "# extends GdUnitTestSuite\nextends Node\n\n\n"
        "func test_x() -> void:\n\tassert_bool(true).is_true()\n",
    )
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "test_named.gd" not in result.stdout
    assert "test/test_commented.gd:1: has test functions, so it must 'extends" in result.stdout


def test_no_test_files_is_an_error(tmp_path: Path) -> None:
    """A wrong tests.root or an empty folder must not read as a pass."""
    root = _project(tmp_path)
    missing = run_tool(root, "lint_tests.ps1")
    assert missing.returncode == 2, missing.stdout + missing.stderr
    assert "No gdUnit4 suites found under test/" in missing.stdout

    write(root / "test" / "notes.gd", "extends RefCounted\n")
    empty = run_tool(root, "lint_tests.ps1")
    assert empty.returncode == 2 and "No gdUnit4 suites found" in empty.stdout


def test_it_says_what_is_missing_when_it_cannot_ask(tmp_path: Path) -> None:
    """No uv, or no tools/ci/suites.py: one line each, never a pass and never a guess."""
    root = _project(tmp_path)
    write(root / "test" / "test_clean.gd", CLEAN_SUITE)
    system32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    no_uv = run_tool(root, "lint_tests.ps1", env=clean_env(PATH=str(system32)))
    assert no_uv.returncode == 2, no_uv.stdout + no_uv.stderr
    assert "Test lint: uv not found" in no_uv.stdout

    (root / "tools" / "ci" / "suites.py").unlink()
    no_rules = run_tool(root, "lint_tests.ps1")
    assert no_rules.returncode == 2, no_rules.stdout + no_rules.stderr
    assert "Test lint: tools/ci/suites.py is missing" in no_rules.stdout


def test_uv_runs_in_the_project_wherever_the_caller_stands(tmp_path: Path) -> None:
    """`uv run` in the caller's folder would read that folder's pyproject.toml and build
    a .venv there: uv is given the project."""
    root = _project(tmp_path)
    write(root / "test" / "test_clean.gd", CLEAN_SUITE)
    write(root / "pyproject.toml", '[project]\nname = "mine"\nversion = "0"\n')
    # run_tool starts the gate from the folder above the project: someone else's project.
    write(tmp_path / "pyproject.toml", '[project]\nname = "theirs"\nversion = "0"\n')
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / ".venv").exists() and not (tmp_path / "uv.lock").exists()


def test_test_root_comes_from_kit_json(tmp_path: Path) -> None:
    root = _project(tmp_path, test_root="specs")
    write(root / "specs" / "test_clean.gd", CLEAN_SUITE)
    write(root / "test" / "test_broken.gd", "extends Node\n")  # the default root is not read
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "under specs/" in result.stdout


def test_errors_exit_2(tmp_path: Path) -> None:
    root = _project(tmp_path)
    not_a_suite = "extends Node\n\n\nfunc test_x() -> void:\n\tassert_bool(true).is_true()\n"
    write(root / "test" / "test_not_a_suite.gd", not_a_suite)
    write(
        root / "test" / "deep" / "test_hangs.gd",
        "extends GdUnitTestSuite\n\n\nfunc test_spin() -> void:\n"
        "\twhile true:\n\t\tbreak\n\tassert_bool(true).is_true()\n",
    )
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "test/test_not_a_suite.gd:1: has test functions, so it must 'extends" in result.stdout
    assert "test/deep/test_hangs.gd:5: 'while true:'" in result.stdout


def test_warnings_exit_1(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(
        root / "test" / "test_weak.gd",
        "extends GdUnitTestSuite\n\n\nfunc test_asserts_nothing() -> void:\n"
        "\tvar value: int = 1\n\n\t# assert_int(value).is_equal(1)\n\tprint(value)\n\n\n"
        "func test_fine() -> void:\n\tassert_bool(true).is_true()\n",
    )
    write(root / "test" / "test_empty.gd", "extends GdUnitTestSuite\n")
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "test/test_weak.gd:4: test 'test_asserts_nothing' has no assertion" in result.stdout
    assert "test_fine" not in result.stdout
    assert "test/test_empty.gd:1: no test functions" in result.stdout


# Valid suites (gdUnit4 runs and passes them) that a line-by-line reading gets wrong: text
# at column 0 inside a test, and "#" or brackets inside strings.
TRICKY_SUITE = '''extends GdUnitTestSuite

var _checks: RefCounted = RefCounted.new()


func test_commented_out_line_at_column_0() -> void:
	var a: int = 1
#	print(a)
	assert_int(a).is_equal(1)


func test_multi_line_string_at_column_0() -> void:
	var text: String = """first
second # not a comment
func not_a_function():
"""
	assert_str(text).contains("second")


func test_hash_in_a_parameter(value: String, test_parameters := [["#fff"], ["#000"]]) -> void:
	assert_str(value).has_length(4)


func test_right_after_the_parameterized_one() -> void:
	self.assert_int(1).is_equal(1)


func test_assertion_on_a_helper_object() -> void:
	_checks.assert_valid(1)


func test_bracket_in_a_string() -> void:
	var open: String = "(["
	assert_str(open).has_length(2)


func test_continued_line_at_column_0() -> void:
	var total: int = 1 + \\
2
	assert_int(total).is_equal(3)
'''

# Text that looks like an assertion but is not code.
HOLLOW_SUITE = '''extends GdUnitTestSuite


func test_assertion_in_a_string() -> void:
	print("assert_int(1).is_equal(1)")


func test_assertion_in_a_long_string(value: String, test_parameters := [["#fff"]]) -> void:
	var text: String = """
	assert_int(1).is_equal(1)
"""
	print(text + value)


func test_asserts() -> void:
	assert_int(1).is_equal(1)


func test_last_one_is_still_read() -> void:
	pass
'''


def test_comments_and_strings_are_not_read_as_code(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "test" / "test_tricky.gd", TRICKY_SUITE)
    clean = run_tool(root, "lint_tests.ps1")
    assert clean.returncode == 0, clean.stdout + clean.stderr

    write(root / "test" / "test_hollow.gd", HOLLOW_SUITE)
    hollow = run_tool(root, "lint_tests.ps1")
    assert hollow.returncode == 1, hollow.stdout + hollow.stderr
    flagged = [
        line.split("test '")[1].split("'")[0]
        for line in hollow.stdout.splitlines()
        if "has no assertion" in line
    ]
    assert flagged == [
        "test_assertion_in_a_string",
        "test_assertion_in_a_long_string",
        "test_last_one_is_still_read",
    ]
    assert "test/test_hollow.gd:19: test 'test_last_one_is_still_read'" in hollow.stdout
    assert "test_tricky.gd" not in hollow.stdout


BASE_SUITE = "class_name BaseSuite\nextends GdUnitTestSuite\n"
DERIVED = "extends {base}\n\n\nfunc test_x() -> void:\n\tassert_bool(true).is_true()\n"


def test_suite_may_extend_gdunit_through_a_base_class(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "test" / "support" / "base_suite.gd", BASE_SUITE)
    write(
        root / "test" / "support" / "deeper_suite.gd", "class_name DeeperSuite extends BaseSuite\n"
    )
    write(root / "test" / "test_by_name.gd", DERIVED.format(base="DeeperSuite"))
    write(
        root / "test" / "test_by_path.gd", DERIVED.format(base='"res://test/support/base_suite.gd"')
    )
    write(
        root / "test" / "unit" / "test_by_relative_path.gd",
        DERIVED.format(base='"../support/base_suite.gd"'),
    )
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr

    # A base class outside the test root is read from its script: no import is needed
    # first (Godot's class cache is not asked).
    write(root / "scripts" / "outer_suite.gd", "class_name OuterSuite\nextends GdUnitTestSuite\n")
    write(root / "test" / "test_outer.gd", DERIVED.format(base="OuterSuite"))
    write(root / "test" / "test_node.gd", DERIVED.format(base="Node"))
    assert not (root / ".godot").exists()
    known = run_tool(root, "lint_tests.ps1")
    assert known.returncode == 2, known.stdout + known.stderr
    assert "test_outer.gd" not in known.stdout
    assert (
        "test/test_node.gd:1: has test functions, so it must 'extends GdUnitTestSuite' "
        "(it has: extends Node)" in known.stdout
    )


def test_loop_forms_and_unreadable_config(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(
        root / "test" / "test_hangs.gd",
        "extends GdUnitTestSuite\n\n\nfunc test_spin() -> void:\n"
        '\tprint("while true:")  # while true: in a comment\n'
        "\twhile (true):\n\t\tbreak\n\tassert_bool(true).is_true()\n",
    )
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout.count("'while true:' can hang") == 1
    assert "test/test_hangs.gd:6: 'while true:'" in result.stdout

    # A broken kit.json or test root is one line, not a PowerShell error record.
    write_kit_json(root, tests={"root": "../elsewhere"})
    (root.parent / "elsewhere").mkdir()
    outside = run_tool(root, "lint_tests.ps1")
    assert outside.returncode == 2, outside.stdout + outside.stderr
    assert (
        'Test lint: kit.json: "tests.root" must be a folder inside the project, not "../elsewhere"'
        in outside.stdout
    )
    write(root / "kit.json", "{ not json")
    broken = run_tool(root, "lint_tests.ps1")
    assert broken.returncode == 2, broken.stdout + broken.stderr
    assert "Test lint: kit.json is not valid JSON" in broken.stdout
    assert outside.stderr.strip() == "" and broken.stderr.strip() == ""


def test_test_root_may_be_written_as_a_res_path(tmp_path: Path) -> None:
    root = _project(tmp_path, test_root="res://specs/")
    write(root / "specs" / "test_clean.gd", CLEAN_SUITE)
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "under specs/" in result.stdout


FAILING_SUITE = "extends GdUnitTestSuite\n\n\nfunc test_x() -> void:\n\tassert_int(1).is_equal(2)\n"


def test_a_suite_outside_the_test_root_is_an_error(tmp_path: Path) -> None:
    """The runners start gdUnit4 on kit.json tests.root alone. A suite anywhere else never
    runs: every gate would be green with a failing test in the project."""
    root = _project(tmp_path, test_root="tests")
    write(root / "tests" / "unit" / "test_clean.gd", CLEAN_SUITE)
    write(root / "test" / "unit" / "test_left_behind.gd", FAILING_SUITE)
    # A base suite kept beside the code, and a suite in a folder that is not authored
    # source, are nobody's lost tests.
    write(root / "scripts" / "base_suite.gd", "class_name BaseSuite\nextends GdUnitTestSuite\n")
    write(root / "addons" / "theirs" / "test_theirs.gd", FAILING_SUITE)
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout.count("test_left_behind.gd") == 1
    assert (
        "test/unit/test_left_behind.gd:1: a gdUnit4 suite outside tests.root 'tests' never "
        "runs: move it there, or set tests.root in kit.json"
    ) in result.stdout
    assert "base_suite.gd" not in result.stdout and "test_theirs.gd" not in result.stdout

    (root / "test" / "unit" / "test_left_behind.gd").rename(root / "tests" / "test_found.gd")
    moved = run_tool(root, "lint_tests.ps1")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert "Test lint: CLEAN (2 file(s))" in moved.stdout

    # No suite under the root at all: the one that was left behind is named too.
    write(root / "old" / "test_only.gd", FAILING_SUITE)
    shutil.rmtree(root / "tests")
    (root / "tests").mkdir()
    none = run_tool(root, "lint_tests.ps1")
    assert none.returncode == 2, none.stdout + none.stderr
    assert "old/test_only.gd: a gdUnit4 suite outside tests.root 'tests' never runs" in none.stdout
    assert "No gdUnit4 suites found under tests/" in none.stdout


def test_a_base_suite_outside_the_test_root_runs_in_the_suites_that_extend_it(
    tmp_path: Path,
) -> None:
    """gdUnit4 runs a base's tests in every suite that extends it. A suite outside the test
    root is lost only when no suite under the root extends it."""
    root = _project(tmp_path)
    shared = (
        "class_name {name} extends {base}\n\n\n"
        "func test_{test}() -> void:\n\tassert_bool(true).is_true()\n"
    )
    testing = root / "scripts" / "testing"
    write(
        testing / "shared_suite.gd", shared.format(name="Shared", base="GdUnitTestSuite", test="a")
    )
    write(testing / "deeper_suite.gd", shared.format(name="Deeper", base="Shared", test="b"))
    write(root / "test" / "unit" / "test_uses_shared.gd", DERIVED.format(base="Deeper"))
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Test lint: CLEAN (1 file(s))" in result.stdout

    # The same suite under a name nothing extends.
    write(testing / "stray_suite.gd", shared.format(name="Stray", base="GdUnitTestSuite", test="c"))
    stray = run_tool(root, "lint_tests.ps1")
    assert stray.returncode == 2, stray.stdout + stray.stderr
    assert (
        "scripts/testing/stray_suite.gd:1: a gdUnit4 suite outside tests.root 'test' never runs"
        in stray.stdout
    )
    assert "shared_suite.gd" not in stray.stdout and "deeper_suite.gd" not in stray.stdout

    # Without the suite that extends them, the bases are lost too.
    write(root / "test" / "unit" / "test_uses_shared.gd", CLEAN_SUITE)
    lost = run_tool(root, "lint_tests.ps1")
    assert lost.returncode == 2, lost.stdout + lost.stderr
    for name in ("shared_suite.gd", "deeper_suite.gd", "stray_suite.gd"):
        assert f"scripts/testing/{name}:1: a gdUnit4 suite outside tests.root" in lost.stdout


def test_wall_clock_waits_are_another_checks_business(tmp_path: Path) -> None:
    # tools/check_test_wallclock.py is a gate of its own in check.ps1: not run twice.
    root = _project(tmp_path)
    write(root / "test" / "test_clean.gd", CLEAN_SUITE)
    write(root / "tools" / "check_test_wallclock.py", "raise SystemExit('must not be started')\n")
    result = run_tool(root, "lint_tests.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "wall" not in result.stdout.lower()
    gone = run_tool(root, "lint_tests.ps1", "-SkipWallClock")
    assert gone.returncode != 0 and "SkipWallClock" in gone.stdout + gone.stderr
