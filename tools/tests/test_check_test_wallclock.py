"""tools/check_test_wallclock.py: clean trees pass; planted waits are flagged."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import check_test_wallclock as chk  # noqa: E402

WAIT = "func test_x() -> void:\n\tOS.delay_msec(2)\n"
SUMMED = "func f() -> void:\n\twhile busy():\n\t\tOS.delay_msec(10)\n\t\twaited += 10\n"
ALLOWLIST = "tools/ci/test_wallclock_allowlist.txt"


def _project(root: Path, files: dict[str, str], kit: dict | None = None) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if kit is not None:
        (root / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    return root


def test_flags_waits_and_ignores_stamps_marks_and_comments() -> None:
    text = "\n".join(
        [
            "OS.delay_msec(2)",
            "var d := Time.get_ticks_msec() + 5",
            'var p := "user://x-%d" % Time.get_ticks_usec()',
            "var stamp := Time.get_ticks_usec()",
            "OS.delay_msec(1)  # wallclock-ok: real socket",
            "# OS.delay_msec(1) in a comment",
            "await get_tree().create_timer(0.1).timeout",
            "await await_millis(500)",  # gdUnit4's own sleep
            "await await_idle_frame()",  # waits for a frame, not for time
            "await await_signal_on(emitter, 'done', [], 2000)",  # waits for the thing itself
        ]
    )
    assert [n for n, _ in chk.scan_text(text)] == [1, 2, 7, 8]


def test_strings_are_not_read_as_code_or_as_comments() -> None:
    text = "\n".join(
        [
            'print("#1"); OS.delay_msec(500)',  # the "#" is in a string: the wait is code
            'var hint: String = "never call OS.delay_msec(5) in a test"',  # no call here
            'var note: String = """spans',
            "lines: OS.delay_msec(1)",
            '"""',
            "OS.delay_msec(2)\r",  # a CRLF file
        ]
    )
    assert chk.scan_text(text) == [
        (1, 'print("#1"); OS.delay_msec(500)'),
        (6, "OS.delay_msec(2)"),
    ]


def test_a_clean_project_passes(tmp_path: Path, capsys) -> None:
    files = {"test/unit/test_x.gd": "func test_x() -> void:\n\tpass\n", "scripts/a.gd": ""}
    assert chk.main([], root=_project(tmp_path, files)) == 0
    assert "wall clock: ok (1 test scripts, 2 scripts in all)" in capsys.readouterr().out


def test_a_wait_in_a_test_fails_and_the_same_line_outside_tests_does_not(
    tmp_path: Path, capsys
) -> None:
    files = {"test/unit/test_x.gd": WAIT, "test/helpers/wait.gd": WAIT, "scripts/game/a.gd": WAIT}
    assert chk.main([], root=_project(tmp_path, files)) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [
        "test/helpers/wait.gd:2: wall-clock wait: OS.delay_msec(2)",
        "test/unit/test_x.gd:2: wall-clock wait: OS.delay_msec(2)",
    ]
    assert out[2].startswith("2 finding(s).")


def test_the_test_folder_comes_from_kit_json(tmp_path: Path, capsys) -> None:
    kit = {"tests": {"root": "specs"}}
    root = _project(tmp_path, {"specs/test_x.gd": WAIT, "test/old.gd": WAIT}, kit)
    assert chk.main([], root=root) == 1
    out = capsys.readouterr().out
    assert "specs/test_x.gd:2" in out and "test/old.gd" not in out


def test_no_test_script_is_a_failure_not_a_pass(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, {"scripts/a.gd": ""})
    assert chk.main([], root=root) == 1
    assert "no .gd file under the test folder 'test'" in capsys.readouterr().err
    (root / "test").mkdir()
    assert chk.main([], root=root) == 1


def test_paths_narrow_the_run_and_a_bad_path_is_a_usage_error(tmp_path: Path, capsys) -> None:
    files = {"test/unit/test_x.gd": WAIT, "test/net/test_y.gd": WAIT, "scripts/a.gd": SUMMED}
    root = _project(tmp_path, files)
    assert chk.main(["test/net"], root=root) == 1
    out = capsys.readouterr().out
    assert "test/net/test_y.gd:2" in out and "test_x.gd" not in out and "scripts/a.gd" not in out
    assert chk.main(["scripts"], root=root) == 1  # the summed sleep, no test file needed
    assert "scripts/a.gd:4: summed sleep" in capsys.readouterr().out
    assert chk.main(["nowhere"], root=root) == 2
    assert "does not exist" in capsys.readouterr().err


def test_the_allowlist_exempts_files_and_folders_and_must_not_go_stale(
    tmp_path: Path, capsys
) -> None:
    files = {
        "test/unit/test_x.gd": WAIT,
        "test/net/test_y.gd": WAIT,
        ALLOWLIST: "# real sockets\ntest/net/ | talks to a real socket\n"
        "test/unit/test_x.gd | measures the frame timer itself\n",
    }
    root = _project(tmp_path, files)
    assert chk.main([], root=root) == 0
    capsys.readouterr()
    (root / "test/unit/test_x.gd").write_text("func test_x() -> void:\n\tpass\n", "utf-8")
    assert chk.main([], root=root) == 1
    out = capsys.readouterr().out
    assert "stale entry (no wall-clock use left), remove it: test/unit/test_x.gd" in out
    assert "test/net/" not in out
    # A narrowed run cannot judge the rest of the list.
    assert chk.main(["test/net"], root=root) == 0


def test_an_allowlist_entry_without_a_reason_is_an_error(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, {"test/unit/test_x.gd": WAIT, ALLOWLIST: "test/unit/test_x.gd\n"})
    assert chk.main([], root=root) == 2
    assert "needs `| reason`" in capsys.readouterr().err


def test_a_summed_sleep_fails_anywhere_and_has_no_exception(tmp_path: Path, capsys) -> None:
    marked = SUMMED.replace("waited += 10", "waited += 10  # wallclock-ok: no such thing")
    files = {
        "test/unit/test_x.gd": "func test_x() -> void:\n\tpass\n",
        "scripts/game/loader.gd": marked,
        ALLOWLIST: "scripts/game/ | not a test\n",
    }
    assert chk.main([], root=_project(tmp_path, files)) == 1
    out = capsys.readouterr().out
    assert "scripts/game/loader.gd:4: summed sleep (use a deadline): waited += 10" in out


def test_stamp_exempts_only_a_bare_usec_stamp() -> None:
    text = "\n".join(
        [
            "var stamp := Time.get_ticks_usec()",
            "var stamp := Time.get_ticks_msec()",
            "var tag := Time.get_ticks_usec() + 5000",
            "var stamp: int = Time.get_ticks_usec()",
        ]
    )
    assert [n for n, _ in chk.scan_text(text)] == [2, 3]


def test_inline_mark_needs_a_reason() -> None:
    text = "\n".join(
        [
            "OS.delay_msec(1)  # wallclock-ok:",
            "OS.delay_msec(1)  # wallclock-ok:   ",
            "OS.delay_msec(1)  # wallclock-ok: real socket",
        ]
    )
    assert [n for n, _ in chk.scan_text(text)] == [1, 2]


def test_formatted_stamp_does_not_hide_a_second_clock_call() -> None:
    text = "\n".join(
        [
            'var p := "x-%d" % Time.get_ticks_usec()',
            'var p := "x-%d-%d" % [1, Time.get_ticks_usec()]',
            'var p := "x-%d" % Time.get_ticks_usec(); OS.delay_msec(5)',
            'var p := "x-%d" % [Time.get_ticks_usec(), Time.get_ticks_msec()]',
        ]
    )
    assert [n for n, _ in chk.scan_text(text)] == [3, 4]


def test_summed_sleeps_are_flagged_and_deadlines_are_not() -> None:
    text = "\n".join(
        [
            "while busy() and waited < LIMIT:",
            "\tOS.delay_msec(10)",
            "\twaited += 10",
            "while busy():",
            "\tOS.delay_usec(POLL_USEC)  # wallclock-ok: a mark never excuses a sum",
            "\tspent += POLL_USEC",
            "while busy():",
            "\tif Time.get_ticks_msec() >= deadline:",
            "\t\tbreak",
            "\tOS.delay_msec(1)",
            "\tframes += 2",
            "",
            "",
            "",
            "waited += 1  # a tick counter far from any sleep",
        ]
    )
    assert [n for n, _ in chk.summed_sleeps(text)] == [3, 6]


def test_a_wait_named_counter_beside_a_sleep_is_a_sum() -> None:
    text = "\n".join(["for i in 9:", "\tOS.delay_msec(STEP)", "\twaited_ms += step_ms"])
    assert [n for n, _ in chk.summed_sleeps(text)] == [3]


def test_a_sleep_with_a_nested_call_is_summed() -> None:
    text = "\n".join(["while busy():", "\tOS.delay_msec(maxi(a, b))", "\ttotal += maxi(a, b)"])
    assert chk.summed_sleeps(text) == [(3, "total += maxi(a, b)")]
