"""tools/test_shards.py: discovery, balanced plans, modes, and what counts as a failure."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import kit_config  # noqa: E402
import test_shards as shards  # noqa: E402
from _legacy_console import LegacyConsole  # noqa: E402

SUITE = "extends GdUnitTestSuite\n\n\nfunc test_x() -> void:\n\tpass\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """An empty project the runner treats as its own."""
    monkeypatch.setenv("GODOT_TEST_SLOT_DIR", str(tmp_path / "locks"))
    (tmp_path / "project.godot").write_text("", encoding="utf-8")
    # main() reads kit.json from kit_config.REPO; functions called directly use CONFIG.
    monkeypatch.setattr(kit_config, "REPO", tmp_path)
    monkeypatch.setattr(shards, "CONFIG", kit_config.load(tmp_path))
    return tmp_path


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_every_suite_lands_on_exactly_one_shard_longest_first() -> None:
    timings = {f"res://t/s{index}.gd": float(index) for index in range(1, 9)}
    plan = shards.plan(sorted(timings), timings, 3)
    placed = Counter(item.suite for shard in plan for item in shard.items)
    assert placed == Counter(timings.keys())
    # Longest-first on the least loaded shard: 36 s over 3 shards stays within
    # a couple of seconds of 12 each.
    estimates = [shard.estimate for shard in plan]
    assert sum(estimates) == 36.0
    assert max(estimates) - min(estimates) <= 2.0


def test_a_suite_longer_than_its_share_splits_by_test_case(monkeypatch) -> None:
    names = [f"test_{index}" for index in range(6)]
    monkeypatch.setattr(shards, "case_names", lambda suite: names)
    timings = {"res://t/big.gd": 60.0, "res://t/small.gd": 4.0}
    plan = shards.plan(sorted(timings), timings, 4)
    parts = [item for shard in plan for item in shard.items if item.suite == "res://t/big.gd"]
    assert len(parts) == 4
    # Each part runs its own cases and skips the rest; together they run all once.
    ran = Counter(name for part in parts for name in names if name not in part.skip)
    assert ran == Counter(names)


def test_measured_case_seconds_balance_the_split_parts(monkeypatch) -> None:
    # One 30 s case and five 2 s cases: dealing them evenly would pair the big
    # case with others; by cost it stands alone in its part.
    names = ["test_big"] + [f"test_small_{index}" for index in range(5)]
    cases = {"test_big": 30.0} | {name: 2.0 for name in names[1:]}
    monkeypatch.setattr(shards, "case_names", lambda suite: names)
    timings = {"res://t/big.gd": 40.0, "res://t/other.gd": 40.0}
    plan = shards.plan(sorted(timings), timings, 4, {"res://t/big.gd": cases})
    parts = [item for shard in plan for item in shard.items if item.suite == "res://t/big.gd"]
    alone = [part for part in parts if part.skip == names[1:]]
    assert len(alone) == 1
    # Estimates keep the suite's total (its setup is spread in proportion).
    assert abs(sum(part.estimate for part in parts) - 40.0) < 1e-9


def test_parts_of_one_suite_never_share_a_shard(monkeypatch) -> None:
    # gdUnit4 runs a suite once per process with the union of the parts' skips,
    # so two parts on one shard would silently skip most of the suite.
    names = [f"test_{index}" for index in range(14)]
    monkeypatch.setattr(shards, "case_names", lambda suite: names)
    timings = {"res://t/a.gd": 36.0, "res://t/b.gd": 13.0}
    for count in range(2, 9):
        plan = shards.plan(sorted(timings), timings, count)
        for shard in plan:
            suites = [item.suite for item in shard.items]
            assert len(suites) == len(set(suites)), (count, suites)


def test_more_shards_than_suites_is_capped() -> None:
    plan = shards.plan(["res://t/a.gd"], {}, 8)
    assert len(plan) == 1
    assert plan[0].estimate == shards.DEFAULT_SECONDS


def test_unmeasured_suites_get_the_p90_of_measured_ones() -> None:
    timings = {f"res://t/s{i}.gd": float(i) for i in range(1, 21)}
    assert shards.default_seconds(timings) == 19.0
    assert shards.default_seconds({"res://t/a.gd": 0.1}) == shards.DEFAULT_SECONDS
    plan = shards.plan(["res://t/new_a.gd", "res://t/new_b.gd"], timings, 2)
    assert [shard.estimate for shard in plan] == [19.0, 19.0]


def test_auto_sizing_counts_unmeasured_suites_at_the_p90() -> None:
    timings = {f"res://t/s{i}.gd": float(i) for i in range(1, 21)}
    new = [f"res://t/new{i}.gd" for i in range(4)]
    # 4 x 19 s unmeasured; at the 2 s floor this would size to 1 shard.
    assert shards.auto_shard_count(new, timings, 16) == round(4 * 19.0 / shards.MIN_SHARD_SECONDS)
    assert shards.auto_shard_count(new, {}, 16) == 1


def test_a_fresh_project_plans_without_a_timings_file(project: Path) -> None:
    _write(project, {"test/a.gd": SUITE, "test/b.gd": SUITE})
    assert shards.load_timings(project / "tools/ci/test_timings.json") == {}
    assert shards.load_case_timings(project / "tools/ci/test_timings.json") == {}
    plan = shards.plan(shards.discover(["res://test"]), {}, 4)
    assert sorted(item.suite for shard in plan for item in shard.items) == [
        "res://test/a.gd",
        "res://test/b.gd",
    ]


def test_discovery_finds_suites_and_skips_what_godot_skips(project: Path) -> None:
    _write(
        project,
        {
            "test/unit/test_a.gd": SUITE,
            "test/unit/helper.gd": "extends RefCounted\n",
            "test/unit/commented.gd": "# extends GdUnitTestSuite\nextends Node\n",
            "test/.cache/test_hidden.gd": SUITE,
            "test/out/.gdignore": "",
            "test/out/test_ignored.gd": SUITE,
            "test/integration/test_b.gd": "@tool\nextends   GdUnitTestSuite\n",
            "addons/thing/test/test_addon.gd": SUITE,
        },
    )
    assert shards.discover(["res://test"]) == [
        "res://test/integration/test_b.gd",
        "res://test/unit/test_a.gd",
    ]
    assert shards.discover(["res://test/unit/test_a.gd"]) == ["res://test/unit/test_a.gd"]
    # A path named outright is searched even inside a folder kit.json excludes.
    assert shards.discover(["res://addons/thing"]) == ["res://addons/thing/test/test_addon.gd"]


TEST = "\n\n\nfunc test_x() -> void:\n\tpass\n"
BASE_SUITE = "class_name BaseSuite\nextends GdUnitTestSuite\n"


@pytest.mark.parametrize(
    "header",
    [
        "extends GdUnitTestSuite",
        "class_name OneLine extends GdUnitTestSuite",
        "class_name TwoLines\nextends GdUnitTestSuite",
        "@tool\nextends GdUnitTestSuite",
        "@tool extends GdUnitTestSuite",
        "# what this covers\nextends GdUnitTestSuite  # the base",
        "﻿extends GdUnitTestSuite",  # saved with a byte order mark
        'extends "res://addons/gdUnit4/src/GdUnitTestSuite.gd"',
        "extends BaseSuite",
        "class_name Derived extends BaseSuite",
        "extends MiddleSuite",
        'extends "res://test/support/base_suite.gd"',
        "extends 'res://test/support/base_suite.gd'",
        'extends "../support/base_suite.gd"',
    ],
)
def test_every_header_gdunit4_accepts_is_a_suite(project: Path, header: str) -> None:
    # Each of these runs under gdUnit4. Missing one is a false pass: the suite is never
    # started, so its failures are never seen.
    files = {"test/unit/test_a.gd": header + TEST}
    if "MiddleSuite" in header:  # bases outside the test folder, two steps from gdUnit4
        files["scripts/testing/middle_suite.gd"] = "class_name MiddleSuite\nextends BaseSuite\n"
        files["scripts/testing/base_suite.gd"] = BASE_SUITE
    elif "GdUnitTestSuite" not in header:
        files["test/support/base_suite.gd"] = BASE_SUITE
    _write(project, files)
    assert shards.discover(["res://test"]) == ["res://test/unit/test_a.gd"]
    assert shards.discover(["res://test/unit/test_a.gd"]) == ["res://test/unit/test_a.gd"]


@pytest.mark.parametrize(
    "text",
    [
        "extends Node",
        "# extends GdUnitTestSuite\nextends RefCounted",
        'var note := """\nextends GdUnitTestSuite\n"""',
        "extends RefCounted\n\n\nclass Inner:\n\textends GdUnitTestSuite",
        "extends Helper",  # a class_name script that is not a suite
        "extends Unknown",
        "extends Outer.Inner",
        "extends LoopA",
        "",
    ],
)
def test_a_script_that_does_not_reach_gdunit_test_suite_is_not_a_suite(
    project: Path, text: str
) -> None:
    _write(
        project,
        {
            "test/unit/thing.gd": text + "\n",
            "test/unit/helper.gd": "class_name Helper\nextends RefCounted\n",
            "test/unit/loop_a.gd": "class_name LoopA\nextends LoopB\n",
            "test/unit/loop_b.gd": "class_name LoopB\nextends LoopA\n",
        },
    )
    assert shards.discover(["res://test"]) == []


def test_a_base_that_cannot_be_read_is_left_for_gdunit4_to_judge(project: Path) -> None:
    # An inner class as a base is not followed. With test functions the script goes to
    # gdUnit4 all the same: a real suite reports, anything else fails as "never reported".
    _write(
        project,
        {
            "test/unit/test_inner.gd": "extends Bases.InnerSuite" + TEST,
            "test/unit/bases.gd": "class_name Bases\n\n\nclass InnerSuite:\n\textends Node\n",
        },
    )
    found = shards.find_suites(["res://test"])
    assert found.suites == ["res://test/unit/test_inner.gd"] and found.not_suites == []


def test_a_base_suite_without_tests_is_not_run_by_itself(project: Path) -> None:
    _write(
        project,
        {
            "test/support/base_suite.gd": BASE_SUITE + "\n\nfunc helper() -> int:\n\treturn 1\n",
            "test/unit/test_a.gd": "extends BaseSuite" + TEST,
        },
    )
    # gdUnit4 reports nothing for a suite with no test: running the base would fail the run.
    assert shards.discover(["res://test"]) == ["res://test/unit/test_a.gd"]
    # Asked for alone, it is still known to be a base (who extends it is read test-wide).
    assert shards.discover(["res://test/support"]) == []


def test_a_suite_with_no_test_that_nothing_extends_still_runs_and_so_fails(project: Path) -> None:
    # `func tets_x()`: a typo must not turn into "nothing to run".
    _write(project, {"test/unit/test_typo.gd": "extends GdUnitTestSuite\n\n\nfunc tets_x():\n"})
    assert shards.discover(["res://test"]) == ["res://test/unit/test_typo.gd"]


def test_a_test_script_that_is_not_a_suite_stops_the_run(project: Path, capsys) -> None:
    _write(
        project,
        {
            "test/unit/test_a.gd": SUITE,
            "test/unit/test_orphan.gd": "extends Node" + TEST,
            "test/unit/test_lost_base.gd": "extends DeletedSuite" + TEST,
            "test/unit/helper.gd": "extends RefCounted\n\n\nfunc make() -> int:\n\treturn 1\n",
            # Outside the test folder a `test_` function is just a function.
            "scripts/physics.gd": "extends Node\n\n\nfunc test_move() -> bool:\n\treturn true\n",
        },
    )
    assert shards.main(["--all"]) == 1
    error = capsys.readouterr().err
    assert "2 script(s) in the test folder have test functions" in error
    assert "  test/unit/test_orphan.gd (extends Node)" in error
    assert "  test/unit/test_lost_base.gd (extends DeletedSuite)" in error
    assert "helper.gd" not in error and "physics.gd" not in error


def test_a_suite_is_split_by_its_own_tests_only(project: Path) -> None:
    own = (
        "extends GdUnitTestSuite\n\n\nfunc test_a() -> void:\n\tpass\n\n\n"
        "static func test_b() -> void:\n\tpass\n\n\n# func test_commented() -> void:\n"
        'var text := """\nfunc test_in_a_string() -> void:\n"""\n'
        "func helper_test_c() -> void:\n\tpass\n"
    )
    _write(
        project,
        {
            "test/unit/test_own.gd": own,
            "test/support/base_suite.gd": BASE_SUITE + TEST,
            "test/unit/test_mixed.gd": "extends BaseSuite\n\n\nfunc test_more() -> void:\n\tpass\n",
        },
    )
    assert shards.case_names("res://test/unit/test_own.gd") == ["test_a", "test_b"]
    # It also runs test_x from its base, which gdUnit4 cannot be told to leave out by
    # this suite's name: never split.
    assert shards.case_names("res://test/unit/test_mixed.gd") == []
    plan = shards.plan(["res://test/unit/test_mixed.gd"], {"res://test/unit/test_mixed.gd": 90}, 4)
    assert [len(shard.items) for shard in plan] == [1]


@pytest.mark.parametrize("folder", [".", "tools"])
def test_pytest_finds_no_test_in_the_runner_itself(tmp_path: Path, folder: str) -> None:
    """tools/test_shards.py has the name of a test file. Where tools/tests is gone
    (new_game.ps1 -RemoveKitTests) a bare `pytest` searches the folder it is started in
    and imports the runner: nothing in it may be taken for a test."""
    shutil.copyfile(TOOLS.parent / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(
        TOOLS, tmp_path / "tools", ignore=shutil.ignore_patterns("tests", "__pycache__")
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        cwd=tmp_path / folder,
        env={name: value for name, value in os.environ.items() if not name.startswith("PYTEST_")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    # 5: no test was collected. A test found in the runner is 1 (it fails), or 0.
    assert result.returncode == 5, result.stdout + result.stderr
    assert "ERROR" not in result.stdout


def test_a_suite_with_only_inherited_tests_is_matched_to_its_report(project: Path) -> None:
    _write(
        project,
        {
            "test/support/contract.gd": "class_name Contract\nextends GdUnitTestSuite" + TEST,
            "test/unit/test_first.gd": "extends Contract\n",
            "test/unit/test_second.gd": "extends Contract\n",
            "test/unit/test_own.gd": "extends Contract" + TEST,
        },
    )
    found = shards.find_suites(["res://test/unit"])
    first, second, own = (f"res://test/unit/test_{name}.gd" for name in ("first", "second", "own"))
    assert found.suites == [first, own, second]
    # gdUnit4 names a report after the script that holds the suite's first test function.
    alias = "res://test/unit/contract.gd"
    assert found.reports == {first: alias, second: alias, own: own}
    shard = shards.Shard(0, [shards.Item(suite, 1.0) for suite in found.suites])
    results = [shards.SuiteResult(path, 1, 0, 0, 0.1) for path in (alias, own, alias)]
    shards.claim_reports(shard, results, found.reports)
    assert [result.path for result in results] == [first, own, second]
    # One report short: the suite that has none is the one left unreported.
    results = [shards.SuiteResult(path, 1, 0, 0, 0.1) for path in (alias, own)]
    shards.claim_reports(shard, results, found.reports)
    assert [result.path for result in results] == [first, own]


def test_a_path_is_res_or_project_relative_and_must_exist(project: Path) -> None:
    _write(project, {"test/unit/test_a.gd": SUITE})
    one = ["res://test/unit/test_a.gd"]
    for spelled in ("res://test/unit", "test/unit", "test/unit/", "test/unit/test_a.gd"):
        assert shards.discover([spelled]) == one, spelled
    for missing in ("res://test/missing", "test/missing"):
        with pytest.raises(ValueError, match="does not exist"):
            shards.discover([missing])
    with pytest.raises(ValueError, match="outside the project"):
        shards.discover([str(project.parent)])


def test_the_default_root_is_kit_json_tests_root(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "kit.json").write_text('{"tests": {"root": "specs"}}', encoding="utf-8")
    monkeypatch.setattr(shards, "CONFIG", kit_config.load(tmp_path))
    with pytest.raises(ValueError, match="no test folder"):
        shards.default_roots()
    (tmp_path / "specs").mkdir()
    assert shards.default_roots() == ["res://specs"]


def test_a_project_without_tests_fails_loudly(project: Path, capsys) -> None:
    assert shards.main(["--all"]) == 2
    assert "no test folder" in capsys.readouterr().err
    (project / "test").mkdir()
    assert shards.main(["--all"]) == 1
    assert "No gdUnit4 suites found" in capsys.readouterr().err


def test_results_map_back_to_suite_paths_and_name_failed_cases(tmp_path: Path) -> None:
    report = tmp_path / "report_1"
    report.mkdir()
    (report / "results.xml").write_text(
        '<testsuites><testsuite name="test_a" package="res://test/unit" tests="3" '
        'failures="1" errors="0" time="1.5">'
        '<testcase name="test_one" time="0.5"/>'
        '<testcase name="test_two:[1, 2]" time="0.25"/>'
        '<testcase name="test_two:[3, 4]" time="0.25">'
        '<failure message="FAILED: res://test/unit/test_a.gd:9">\n  line 9: Expecting 1\n'
        "</failure></testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    [result] = shards.parse_results(tmp_path)
    assert result.path == "res://test/unit/test_a.gd"
    assert (result.tests, result.failures, result.seconds) == (3, 1, 1.5)
    # Parameterized cases add up under their function.
    assert result.cases == {"test_one": 0.5, "test_two": 0.5}
    # Where, which case, what was expected: enough to act on without the log.
    assert result.failed == ["res://test/unit/test_a.gd:9  test_two:[3, 4]: line 9: Expecting 1"]


def test_a_failure_line_survives_odd_reports() -> None:
    def line(xml: str, text: str | None = None) -> str:
        problem = shards.ET.fromstring(xml)
        problem.text = text if text is not None else problem.text
        return shards.failure_line("res://test/a.gd", "test_x", problem)

    coloured = line(
        '<failure message="FAILED: res://test/a.gd:3"/>', "\x1b[31mExpecting\n  1\x1b[0m"
    )
    assert coloured == "res://test/a.gd:3  test_x: Expecting 1"
    # The spaces inside a line may be part of a string the test compared: they stay.
    spaced = line(
        '<failure message="FAILED: res://test/a.gd:3"/>',
        "Expecting:\n 'Lamps 0/3   Moves 1'\n but was\n 'Paused'",
    )
    assert spaced == "res://test/a.gd:3  test_x: Expecting: 'Lamps 0/3   Moves 1' but was 'Paused'"
    assert line('<error message="timed out after 5s"/>') == (
        "res://test/a.gd  test_x: timed out after 5s"
    )
    assert line("<failure/>") == "res://test/a.gd  test_x"
    long = line(f'<failure message="FAILED: res://test/a.gd:3">{"word " * 200}</failure>')
    assert long.endswith("...") and len(long) < 340


def test_half_a_report_is_skipped_not_a_crash(tmp_path: Path, capsys) -> None:
    (tmp_path / "report_1").mkdir()
    (tmp_path / "report_1" / "results.xml").write_text("<testsuites><testsuite na", "utf-8")
    assert shards.parse_results(tmp_path) == []
    assert "unreadable gdUnit4 report" in capsys.readouterr().err


def test_suite_lists_ignore_comments_and_a_missing_file_is_empty(project: Path) -> None:
    listing = project / "tools" / "ci" / "slow_suites.txt"
    assert shards.load_suite_list(listing) == []
    _write(
        project,
        {
            "tools/ci/slow_suites.txt": "# why\n\nres://test/a.gd  # 40 s sweep\nres://test/gone.gd\n",
            "test/a.gd": SUITE,
        },
    )
    assert shards.load_suite_list(listing) == ["res://test/a.gd", "res://test/gone.gd"]
    assert shards.missing_suites(shards.load_suite_list(listing)) == ["res://test/gone.gd"]


def test_a_script_error_fails_a_shard_that_exited_zero() -> None:
    clean = "Overall Summary: 3 test cases\nExit code: 0\n"
    assert shards.shard_failure(0, clean) is None
    assert shards.shard_failure(101, clean) is None  # passed with warnings (orphan nodes)
    assert shards.shard_failure(100, clean) == "exit 100"
    assert shards.shard_failure(1, clean) == "exit 1"
    # gdUnit4 exits 0 on each of these.
    for fatal in (
        "SCRIPT ERROR: Parse Error: Unexpected identifier",
        'ERROR: Failed to load script "res://test/unit/test_a.gd" with error "Parse error".',
        "No test cases found, abort test run!",
    ):
        assert shards.shard_failure(0, clean + fatal + "\n"), fatal


def test_every_case_runs_even_after_a_failure() -> None:
    shard = shards.Shard(0, [shards.Item("res://test/a.gd", 1.0, ["test_skipped"])])
    command = shards.shard_command("godot", shard)
    # Without -c gdUnit4 stops the process at the first failed case.
    assert "-c" in command and "--ignoreHeadlessMode" in command
    # Project-relative, and this run's own folder.
    report = f"artifacts/gdunit-shards/run_{os.getpid()}/shard_0"
    assert command[command.index("-rd") + 1] == report
    assert command[-4:] == ["-a", "res://test/a.gd", "-i", "res://test/a.gd:test_skipped"]


def test_reports_start_empty_inside_a_folder_godot_ignores(project: Path) -> None:
    mine = project / "artifacts" / "gdunit-shards" / f"run_{os.getpid()}"
    stale = mine / "shard_0" / "report_1" / "results.xml"
    other = project / "artifacts" / "gdunit-shards" / "run_1" / "shard_0" / "results.xml"
    for path in (stale, other):
        _write(project, {str(path.relative_to(project)): "<testsuites/>"})
    shards.prepare_reports()
    assert mine.is_dir() and not stale.exists()
    assert (project / "artifacts" / ".gdignore").is_file()
    # Another run in this checkout keeps its reports, now and when this one finishes.
    shards.discard_reports()
    assert not mine.exists() and other.is_file()
    other.unlink()
    other.parent.rmdir()
    other.parent.parent.rmdir()
    shards.prepare_reports()
    shards.discard_reports()
    assert not (project / "artifacts" / "gdunit-shards").exists()


def test_each_shard_gets_its_own_user_data_folder(tmp_path: Path) -> None:
    env = shards.shard_env(tmp_path / "user_3")
    assert env["APPDATA"] == env["XDG_DATA_HOME"] == str(tmp_path / "user_3")


def _mode(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--changed", nargs="?", const="")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--skip-slow", action="store_true")
    args = parser.parse_args(list(argv))
    shards.resolve_mode(args)
    return args


def test_a_bare_run_is_the_cheap_changed_mode() -> None:
    args = _mode()
    assert args.changed == "" and not args.all and not args.dev
    assert _mode("--changed", "develop").changed == "develop"


def test_the_whole_suite_is_opt_in() -> None:
    assert _mode("--all").all and _mode("--all").changed is None
    # --skip-slow is a full run minus the slow list, never the diff-only default.
    assert _mode("--skip-slow").all
    assert _mode("--dev").changed is None


def test_explicit_paths_run_exactly_those_suites() -> None:
    args = _mode("res://test/unit/test_x.gd")
    assert args.changed is None and not args.all and not args.dev


def _choose_args(project: Path, **overrides) -> argparse.Namespace:
    values = {
        "skip_slow": False,
        "dev": False,
        "all": False,
        "changed": None,
        "timings": str(project / "tools/ci/test_timings.json"),
        "slow_list": str(project / "tools/ci/slow_suites.txt"),
        "always_list": str(project / "tools/ci/always_run_suites.txt"),
        "heavy_seconds": 3.0,
        "exit": 0,
    }
    return argparse.Namespace(**(values | overrides))


SLOW, FAST, FAR = (f"res://test/unit/test_{name}.gd" for name in ("slow", "fast", "far"))


@pytest.fixture
def three_suites(project: Path) -> Path:
    _write(project, {suite.removeprefix("res://"): SUITE for suite in (SLOW, FAST, FAR)})
    _write(project, {"tools/ci/slow_suites.txt": f"{SLOW}  # reason\n"})
    return project


def _selecting(monkeypatch, suites: dict[str, int], widened_by: str = "") -> None:
    selection = shards.select_suites.Selection(
        suites=suites, changed=["a.gd"], ignored=[], widened_by=widened_by
    )
    monkeypatch.setattr(shards, "select_changed", lambda *_: selection)


def test_changed_mode_leaves_out_slow_suites_it_only_reaches_indirectly(
    three_suites: Path, monkeypatch
) -> None:
    _selecting(monkeypatch, {SLOW: 1, FAST: 1})
    args = _choose_args(three_suites, changed="")
    assert shards.choose_suites(args, [FAR, FAST, SLOW]) == [FAST]
    # Its own file changed: it runs.
    _selecting(monkeypatch, {SLOW: 0, FAST: 1})
    assert shards.choose_suites(args, [FAR, FAST, SLOW]) == [FAST, SLOW]


def test_a_widening_change_runs_every_suite_that_is_not_heavy(
    three_suites: Path, monkeypatch
) -> None:
    _write(three_suites, {"tools/ci/test_timings.json": json.dumps({"suites": {FAR: 9.5}})})
    _selecting(monkeypatch, {}, widened_by="project.godot")
    args = _choose_args(three_suites, changed="")
    assert shards.choose_suites(args, [FAR, FAST, SLOW]) == [FAST]
    # An always-run suite is a guard for exactly this kind of change: it runs though slow.
    _write(three_suites, {"tools/ci/always_run_suites.txt": f"{SLOW}\n"})
    args = _choose_args(three_suites, changed="")
    assert shards.choose_suites(args, [FAR, FAST, SLOW]) == [FAST, SLOW]


def test_dev_skips_heavy_and_slow_suites_and_skip_slow_only_the_slow_list(
    three_suites: Path,
) -> None:
    _write(three_suites, {"tools/ci/test_timings.json": json.dumps({"suites": {FAR: 3.0}})})
    everything = [FAR, FAST, SLOW]
    assert shards.choose_suites(_choose_args(three_suites, dev=True), everything) == [FAST]
    assert shards.choose_suites(_choose_args(three_suites, all=True), everything) == everything
    skip = _choose_args(three_suites, all=True, skip_slow=True)
    assert shards.choose_suites(skip, everything) == [FAR, FAST]


def test_a_list_that_names_a_missing_suite_fails_the_run(three_suites: Path, capsys) -> None:
    _write(three_suites, {"tools/ci/slow_suites.txt": "res://test/unit/test_gone.gd\n"})
    args = _choose_args(three_suites, all=True)
    assert shards.choose_suites(args, [FAST]) is None
    assert args.exit == 1
    assert "lists missing suites: res://test/unit/test_gone.gd" in capsys.readouterr().err


def test_always_run_suites_reach_the_selection(three_suites: Path, monkeypatch) -> None:
    _write(three_suites, {"tools/ci/always_run_suites.txt": f"{FAR}  # project-wide guard\n"})
    seen: dict[str, object] = {}

    def fake_select(_root, changed, heavy, base_rev=None, always=()):
        seen.update(changed=changed, heavy=heavy, base_rev=base_rev, always=list(always))
        return shards.select_suites.Selection(suites={FAR: 0}, changed=changed)

    monkeypatch.setattr(shards.select_suites, "default_base", lambda _root: "main")
    monkeypatch.setattr(shards.select_suites, "base_revision", lambda _root, _base: "abc123")
    monkeypatch.setattr(shards.select_suites, "changed_files", lambda _root, _rev: ["x.gd"])
    monkeypatch.setattr(shards.select_suites, "select", fake_select)
    args = _choose_args(three_suites, changed="")
    assert shards.choose_suites(args, [FAR, FAST, SLOW]) == [FAR]
    assert seen == {"changed": ["x.gd"], "heavy": {SLOW}, "base_rev": "abc123", "always": [FAR]}


def test_on_the_base_branch_the_run_says_that_commits_do_not_count(
    three_suites: Path, monkeypatch, capsys
) -> None:
    """After a commit on a main with no remote copy, "No suites to run." alone reads as
    "nothing to test"."""
    monkeypatch.setattr(shards.select_suites, "default_base", lambda _root: "main")
    monkeypatch.setattr(shards.select_suites, "base_revision", lambda _root, _base: "abc123")
    monkeypatch.setattr(shards.select_suites, "changed_files", lambda _root, _rev: [])
    answers = iter((True, False))
    monkeypatch.setattr(shards.select_suites, "on_base_branch", lambda _root, _base: next(answers))
    note = "note: on 'main', the base branch itself: commits made on it"
    for said in (True, False):
        args = _choose_args(three_suites, changed="")
        assert shards.choose_suites(args, [FAR, FAST, SLOW]) == []
        assert (note in capsys.readouterr().out) is said


@pytest.mark.parametrize(
    ("base", "revision", "changed", "needle"),
    [
        (None, None, None, "not a git repository"),
        ("main", None, None, "no common commit with 'main'"),
        ("main", "abc123", None, "no common commit with 'main'"),
    ],
)
def test_without_git_or_a_base_branch_every_suite_runs_loudly(
    three_suites: Path, monkeypatch, capsys, base, revision, changed, needle
) -> None:
    monkeypatch.setattr(shards.select_suites, "default_base", lambda _root: base)
    monkeypatch.setattr(shards.select_suites, "base_revision", lambda _root, _base: revision)
    monkeypatch.setattr(shards.select_suites, "changed_files", lambda _root, _rev: changed)
    args = _choose_args(three_suites, changed="")
    # Everything: heavy and slow suites too, since nothing says what changed.
    assert shards.choose_suites(args, [FAR, FAST, SLOW]) == [FAR, FAST, SLOW]
    assert args.all and args.changed is None
    error = capsys.readouterr().err
    assert needle in error and "running every suite" in error


def test_shard_timeout_counts_from_slot_not_from_queueing() -> None:
    got_slot, timeout = 400.0, 480.0
    # 400 s queued, then 100 s running: not expired.
    assert not shards.shard_expired(got_slot, 500.0, timeout)
    assert shards.shard_expired(got_slot, got_slot + timeout + 1, timeout)


class FakeSlot:
    def __init__(self, pool) -> None:
        self.pool = pool

    def release(self) -> None:
        self.pool.busy = False


class FakePool:
    busy = False

    def __init__(self, *_args) -> None:
        pass

    def try_acquire(self):
        if self.busy:
            return None
        self.busy = True
        return FakeSlot(self)

    def held(self) -> int:
        return int(self.busy)


class FakeProcess:
    def __init__(self, clock: list[float], ends: float, code: int = 0) -> None:
        self.clock, self.ends, self.code = clock, ends, code

    def poll(self):
        return self.code if self.clock[0] >= self.ends else None


class FakeGroup:
    def __init__(self, clock: list[float], ends: float, code: int = 0) -> None:
        self.process = FakeProcess(clock, ends, code)
        self.killed = False

    def cpu_seconds(self) -> float:
        return 0.0

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        pass


@pytest.fixture
def fake_run(project: Path, monkeypatch):
    """Drive run() with a fake clock, pool and Godot: two suites, one slot.
    Returns (clock, starts, groups, results) for the test to steer and inspect."""
    clock = [1000.0]
    monkeypatch.setattr(shards.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(shards.time, "sleep", lambda _seconds: clock.__setitem__(0, clock[0] + 10))
    suites = ["res://test/unit/test_a.gd", "res://test/unit/test_b.gd"]
    _write(project, {suite.removeprefix("res://"): SUITE for suite in suites})
    _write(project, {"addons/gdUnit4/bin/GdUnitCmdTool.gd": "extends SceneTree\n"})
    starts: list[float] = []
    groups: list[FakeGroup] = []
    results: dict[str, shards.SuiteResult] = {}
    state = {"seconds": 100.0, "code": 0}

    def fake_start(_godot, shard, work, low_priority=False):
        starts.append(clock[0])
        groups.append(FakeGroup(clock, clock[0] + state["seconds"], state["code"]))
        log = work / f"shard_{shard.index}.log"
        log.write_text("Overall Summary\n", encoding="utf-8")
        return groups[-1], log

    def fake_results(report: Path):
        index = int(report.name.removeprefix("shard_"))
        return [results[suites[index]]] if suites[index] in results else []

    monkeypatch.setattr(shards.godot_binary, "resolve", lambda *_: "godot")
    monkeypatch.setattr(shards.class_cache, "is_stale", lambda _config: False)
    monkeypatch.setattr(shards, "start_shard", fake_start)
    monkeypatch.setattr(shards, "parse_results", fake_results)
    monkeypatch.setattr(shards.slot_pool, "SlotPool", FakePool)
    for suite in suites:
        results[suite] = shards.SuiteResult(suite, tests=1, failures=0, errors=0, seconds=1.0)
    return clock, starts, groups, results, state


RUN = ["--all", "--slots", "1", "--shards", "2", "--slowest", "0"]


def test_monitor_starts_shard_clock_at_slot_acquisition(fake_run) -> None:
    """One slot, two 100 s shards, timeout 150 s: the second queues 100 s, then
    runs 100 s. Queue time must not count, so nothing times out."""
    _clock, starts, groups, _results, _state = fake_run
    assert shards.main([*RUN, "--timeout", "150"]) == 0
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 100.0  # the second shard really queued
    assert not any(group.killed for group in groups)


def test_a_shard_over_its_timeout_is_killed_and_the_run_exits_124(fake_run) -> None:
    _clock, _starts, groups, _results, _state = fake_run
    assert shards.main([*RUN, "--timeout", "50"]) == 124
    assert all(group.killed for group in groups)


def test_a_suite_that_never_reports_fails_the_run(fake_run, capsys) -> None:
    _clock, _starts, _groups, results, _state = fake_run
    del results["res://test/unit/test_b.gd"]
    assert shards.main(RUN) == 1
    out = capsys.readouterr().out
    assert "1 suite part(s) never reported" in out and "res://test/unit/test_b.gd" in out


def test_suites_that_report_no_test_case_fail_the_run(fake_run, capsys) -> None:
    _clock, _starts, _groups, results, _state = fake_run
    for suite in list(results):
        results[suite] = shards.SuiteResult(suite, tests=0, failures=0, errors=0, seconds=0.0)
    assert shards.main(RUN) == 1
    assert "No test case ran." in capsys.readouterr().out


def test_a_failed_case_fails_the_run_and_is_named(fake_run, capsys) -> None:
    _clock, _starts, _groups, results, state = fake_run
    state["code"] = 100
    suite = "res://test/unit/test_a.gd"
    failure = f"{suite}:5  test_x: Expecting 3 but was 2"
    for path in results:  # one failed case in each shard
        line = failure.replace(suite, path)
        results[path] = shards.SuiteResult(
            path, tests=1, failures=1, errors=0, seconds=1.0, failed=[line]
        )
    assert shards.main(RUN) == 1
    out = capsys.readouterr().out
    # The failure is itemised, so the shard's log is left out...
    assert "shard 0 FAILED (exit 100)" in out and f"  {failure}" in out
    assert "2 failure(s)" in out and "log follows" not in out
    # ...unless asked for, or unless gdUnit4 counted a failure the list does not show.
    assert shards.main([*RUN, "--verbose"]) == 1
    assert "shard 0 FAILED (exit 100); log follows" in capsys.readouterr().out
    results[suite].failures = 2
    assert shards.main(RUN) == 1
    assert "shard 0 FAILED (exit 100); log follows" in capsys.readouterr().out


def test_a_script_error_beside_a_test_failure_still_prints_the_log(
    fake_run, monkeypatch, capsys
) -> None:
    _clock, _starts, _groups, results, state = fake_run
    state["code"] = 100
    suite = "res://test/unit/test_a.gd"
    results[suite] = shards.SuiteResult(
        suite, tests=1, failures=1, errors=0, seconds=1.0, failed=[f"{suite}:5  test_x: no"]
    )
    real_start = shards.start_shard

    def noisy_start(*args, **kwargs):
        group, log = real_start(*args, **kwargs)
        log.write_text("SCRIPT ERROR: Invalid call\nOverall Summary\n", encoding="utf-8")
        return group, log

    monkeypatch.setattr(shards, "start_shard", noisy_start)
    assert shards.main(RUN) == 1
    assert "FAILED ('SCRIPT ERROR' in output); log follows" in capsys.readouterr().out


def test_an_interrupted_run_kills_what_it_started(fake_run, monkeypatch) -> None:
    clock, _starts, groups, _results, _state = fake_run

    def interrupt(_seconds: float) -> None:
        clock[0] += 10
        if clock[0] > 1030:
            raise KeyboardInterrupt

    monkeypatch.setattr(shards.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        shards.main(RUN)
    assert len(groups) == 1 and groups[0].killed


def test_a_stale_class_cache_is_rebuilt_before_the_first_shard(fake_run, monkeypatch) -> None:
    _clock, starts, _groups, _results, _state = fake_run
    order: list[str] = []
    monkeypatch.setattr(shards.class_cache, "is_stale", lambda _config: True)
    monkeypatch.setattr(
        shards.class_cache, "refresh", lambda godot, _config: order.append(f"import {godot}")
    )
    real_start = shards.start_shard

    def start(*args, **kwargs):
        order.append("shard")
        return real_start(*args, **kwargs)

    monkeypatch.setattr(shards, "start_shard", start)
    assert shards.main(RUN) == 0
    assert order == ["import godot", "shard", "shard"]
    order.clear()
    assert shards.main([*RUN, "--no-import"]) == 0
    assert order == ["shard", "shard"] and len(starts) == 4


def test_no_gdunit4_is_a_setup_error(fake_run, project: Path, capsys) -> None:
    (project / "addons/gdUnit4/bin/GdUnitCmdTool.gd").unlink()
    assert shards.main(RUN) == 2
    error = capsys.readouterr().err
    assert "gdUnit4 is not installed" in error and "install_gdunit4.py" in error


def test_no_godot_is_a_setup_error(fake_run, monkeypatch, capsys) -> None:
    def missing(*_args):
        raise shards.godot_binary.GodotNotFoundError("Godot 4.7 (standard build) not found.")

    monkeypatch.setattr(shards.godot_binary, "resolve", missing)
    assert shards.main(RUN) == 2
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--slots", "-1"],  # a slot count that can never be met
        ["--slots", "many"],
        ["--shards", "0"],
        ["--timeout", "0"],
        ["--queue-timeout", "-5"],
        ["--heavy-seconds", "nan"],
        ["--write-timings", "res://test/unit"],  # the flag swallowed a test path
    ],
)
def test_a_bad_argument_is_a_usage_error_at_once(fake_run, capsys, argv) -> None:
    _clock, starts, _groups, _results, _state = fake_run
    with pytest.raises(SystemExit) as stopped:
        shards.main(["--all", *argv])
    assert stopped.value.code == 2 and starts == []
    assert argv[0] in capsys.readouterr().err


def test_a_test_path_read_as_the_base_branch_is_a_usage_error(fake_run, capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        shards.main(["--changed", "res://test/unit"])
    assert stopped.value.code == 2
    assert "--changed took the test path res://test/unit" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        shards.main(["--changed", "test/unit/test_a.gd"])
    assert "--changed took the test path test/unit/test_a.gd" in capsys.readouterr().err


def test_an_unreadable_kit_json_is_a_setup_error_not_a_traceback(project: Path, capsys) -> None:
    _write(project, {"test/a.gd": SUITE, "kit.json": '{"tests": {"root": "test"}\n "x": 1}'})
    assert shards.main(["--all"]) == 2
    error = capsys.readouterr().err
    assert "kit.json is not valid JSON" in error and str(project / "kit.json") in error
    _write(project, {"kit.json": '{"source": {"exclude": "addons"}}'})
    assert shards.main(["--all"]) == 2
    assert 'kit.json: "source.exclude" must be a list of strings' in capsys.readouterr().err


def test_an_unreadable_timings_file_is_a_setup_error(project: Path, capsys) -> None:
    _write(project, {"test/a.gd": SUITE, "tools/ci/test_timings.json": "{oops"})
    assert shards.main(["--all"]) == 2
    assert "test_timings.json is not valid JSON" in capsys.readouterr().err


def test_write_timings_without_a_file_writes_the_projects_own(
    fake_run, project: Path, monkeypatch, capsys
) -> None:
    elsewhere = project / "some" / "other" / "folder"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)  # a relative default would land here
    assert shards.main([*RUN, "--write-timings"]) == 0
    written = project / "tools" / "ci" / "test_timings.json"
    assert json.loads(written.read_text(encoding="utf-8"))["suites"] == {
        "res://test/unit/test_a.gd": 1.0,
        "res://test/unit/test_b.gd": 1.0,
    }
    assert f"Wrote 2 suite timings to {written.resolve()}" in capsys.readouterr().out
    # A named file starts at the project root too, like a test path.
    assert shards.main([*RUN, "--write-timings", "mine.json"]) == 0
    assert (project / "mine.json").is_file() and not (elsewhere / "mine.json").exists()


@pytest.fixture
def work_folder(project: Path, monkeypatch) -> Path:
    """The folder a run keeps its shard logs in, so a test can see it removed."""
    work = project / "work"

    def make(**_options) -> str:
        work.mkdir()
        return str(work)

    monkeypatch.setattr(shards.tempfile, "mkdtemp", make)
    return work


def test_text_outside_the_console_code_page_cannot_crash_the_report(
    fake_run, project: Path, work_folder: Path, monkeypatch
) -> None:
    """Piped output on Windows is cp1252. A failure message or a log line with other
    characters must not raise UnicodeEncodeError: that reads as a setup error (exit 2),
    with the failure never printed and the run's folders left behind."""
    _clock, _starts, _groups, results, state = fake_run
    state["code"] = 100
    suite = "res://test/unit/test_a.gd"
    line = f"{suite}:5  test_x: Expecting: '日本語 → ✓' but was 'plain'"
    results[suite] = shards.SuiteResult(suite, 1, 1, 0, 1.0, failed=[line])
    console = LegacyConsole(monkeypatch)
    assert shards.main(RUN) == 1
    said = console.text()
    assert "Expecting: '\\u65e5\\u672c\\u8a9e \\u2192 \\u2713' but was 'plain'" in said
    assert "2/2 suites, 2 test cases, 1 failures/errors" in said
    assert not work_folder.exists() and not (project / "artifacts" / "gdunit-shards").exists()

    # The other way text reaches the report: a failed shard's whole log.
    real_start = shards.start_shard

    def noisy_start(*args, **kwargs):
        group, log = real_start(*args, **kwargs)
        log.write_text("✓ loaded\nSCRIPT ERROR: Invalid call\n", encoding="utf-8")
        return group, log

    monkeypatch.setattr(shards, "start_shard", noisy_start)
    console = LegacyConsole(monkeypatch)
    assert shards.main(RUN) == 1
    assert "\\u2713 loaded" in console.text()


def test_a_crash_after_the_shards_started_is_not_a_setup_error_and_leaves_nothing(
    fake_run, project: Path, work_folder: Path, monkeypatch
) -> None:
    def broken(_report: Path):
        raise ValueError("a bug in the runner")

    monkeypatch.setattr(shards, "parse_results", broken)
    # Exit 2 means "fix your setup"; a bug must look like a bug.
    with pytest.raises(ValueError, match="a bug in the runner"):
        shards.main(RUN)
    assert not work_folder.exists() and not (project / "artifacts" / "gdunit-shards").exists()


def test_keep_leaves_the_logs_and_reports_and_says_where(
    fake_run, project: Path, work_folder: Path, capsys
) -> None:
    assert shards.main([*RUN, "--keep"]) == 0
    assert work_folder.is_dir() and (project / "artifacts" / "gdunit-shards").is_dir()
    assert f"Kept: shard logs in {work_folder}" in capsys.readouterr().out
