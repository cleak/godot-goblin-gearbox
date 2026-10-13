"""tools/test_shards.py run as a program, start to end, with a stand-in for Godot: what a
shard is given, what is left when a run passes, hangs, is stopped or is killed, and that
two runs share the slots. The same tests on every system."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _gate_fixtures import (
    LINUX,
    MACOS,
    REPO,
    USER_DATA_VARIABLE,
    clean_env,
    copy_python_tools,
    linux_only,
    posix_only,
    project_godot,
    python_program,
    write,
    write_kit_json,
)
from _processes import wait_gone

sys.path.insert(0, str(REPO / "tools" / "ci"))
import slot_pool  # noqa: E402

SUITE = "extends GdUnitTestSuite\n\n\nfunc test_x() -> void:\n\tpass\n"
SUITES = ("res://test/unit/test_a.gd", "res://test/unit/test_b.gd")

STAND_IN = '''"""Stands in for Godot under tools/test_shards.py.

  STAND_IN_NOTES    a folder that gets one JSON file per run: its process id, the variables
                    that move Godot's user data, and when it started and ended
  STAND_IN_DOES     pass (default): writes the gdUnit4 report of every suite it was given
                    hang: starts two processes, one of them in a session of its own
                    as Godot's OS.create_process does, says so, and never ends
                    leak: starts a process in a session of its own, then passes
  STAND_IN_ON       hang and leak only in the run that was given this suite (default: all)
  STAND_IN_SECONDS  how long a passing run takes (default 0)
"""
import json
import os
import subprocess
import sys
import time

arguments = sys.argv[1:]
if arguments[:1] == ["--version"]:
    print("4.7.2.stable.fake")
    sys.exit(0)
note = {"pid": os.getpid(), "started": time.time()}
for name in ("APPDATA", "XDG_DATA_HOME", "HOME"):
    note[name] = os.environ.get(name, "")
suites = [arguments[at + 1] for at, word in enumerate(arguments) if word == "-a"]
does = os.environ.get("STAND_IN_DOES") or "pass"
if os.environ.get("STAND_IN_ON", suites[0]) not in suites:
    does = "pass"
idle = [sys.executable, "-I", "-S", "-c", "import time; time.sleep(300)"]


def write_note():
    path = os.path.join(os.environ["STAND_IN_NOTES"], "%d.json" % os.getpid())
    with open(path + ".part", "w", encoding="utf-8") as out:
        json.dump(note, out)
    os.replace(path + ".part", path)


if does == "hang":
    note["child"] = subprocess.Popen(idle).pid
    note["apart"] = subprocess.Popen(idle, start_new_session=True).pid
    write_note()
    print("STAND_IN hangs", flush=True)
    time.sleep(300)
if does == "leak":
    quiet = dict.fromkeys(("stdin", "stdout", "stderr"), subprocess.DEVNULL)
    note["apart"] = subprocess.Popen(idle, start_new_session=True, **quiet).pid
time.sleep(float(os.environ.get("STAND_IN_SECONDS") or 0))
report = os.path.join(arguments[arguments.index("-rd") + 1], "report_1")
os.makedirs(report)
with open(os.path.join(report, "results.xml"), "w", encoding="utf-8") as out:
    out.write("<testsuites>")
    for suite in suites:
        folder, _, name = suite.rpartition("/")
        out.write(
            '<testsuite name="%s" package="%s" tests="1" failures="0" errors="0" time="0.1">'
            '<testcase name="test_x" time="0.1"/></testsuite>' % (name[:-3], folder)
        )
    out.write("</testsuites>")
note["ended"] = time.time()
write_note()
print("STAND_IN ran " + " ".join(suites))
'''


class Game:
    """A project with two suites, a copy of the runner, and the stand-in as its Godot."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "game"
        self.notes = tmp_path / "notes"
        self.temp = tmp_path / "temp"
        self.locks = tmp_path / "locks"
        copy_python_tools(self.root)
        shutil.copyfile(REPO / "tools" / "test_shards.py", self.root / "tools" / "test_shards.py")
        write_kit_json(self.root)
        write(self.root / "project.godot", project_godot())
        write(self.root / "addons" / "gdUnit4" / "bin" / "GdUnitCmdTool.gd", "extends SceneTree\n")
        for suite in SUITES:
            write(self.root / suite.removeprefix("res://"), SUITE)
        for folder in (self.notes, self.temp):
            folder.mkdir()
        godot = python_program(tmp_path / "bin", "fake_godot", STAND_IN)
        temp = dict.fromkeys(("TMPDIR", "TEMP", "TMP"), str(self.temp))
        self.env = clean_env(
            GODOT_BIN=str(godot),
            GODOT_TEST_SLOT_DIR=str(self.locks),
            STAND_IN_NOTES=str(self.notes),
            **temp,
        )
        for name in ("CI", "GODOT_TEST_SLOTS"):
            self.env.pop(name, None)

    def start(self, *args: str, **env: str) -> subprocess.Popen[str]:
        """The runner, started from another folder: nothing may depend on the working one.
        The real interpreter, not a virtual environment's launcher, which on Windows has a
        job of its own and would end the tree whatever the runner does."""
        python = getattr(sys, "_base_executable", sys.executable)
        return subprocess.Popen(
            [python, str(self.root / "tools" / "test_shards.py"), "--no-import", *args],
            cwd=self.root.parent,
            env=self.env | env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def run(self, *args: str, **env: str) -> tuple[int, str, str]:
        process = self.start(*args, **env)
        try:
            out, error = process.communicate(timeout=120)
        except BaseException:
            process.kill()
            raise
        return process.returncode, out, error

    def read_notes(self, count: int, seconds: float = 60.0) -> list[dict]:
        """The notes of `count` stand-in runs, oldest first, once that many are written."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            files = sorted(self.notes.glob("*.json"))
            if len(files) >= count:
                notes = [json.loads(path.read_text(encoding="utf-8")) for path in files]
                return sorted(notes, key=lambda note: note["started"])
            time.sleep(0.1)
        raise AssertionError(f"{count} stand-in run(s) did not start in {seconds:g} s")

    def left_behind(self) -> list[str]:
        """What a finished run left in its temp folder and in the project's artifacts."""
        reports = self.root / "artifacts" / "gdunit-shards"
        return sorted(path.name for path in [*self.temp.iterdir(), *reports.glob("*")])

    def slots_held(self) -> int:
        return slot_pool.SlotPool(2, self.locks).held()


@pytest.fixture
def game(tmp_path: Path) -> Game:
    return Game(tmp_path)


def _tree_gone(note: dict) -> bool:
    """A hung stand-in and both processes it started have ended."""
    return all(wait_gone(note[name]) for name in ("pid", "child", "apart"))


def test_a_run_passes_and_every_shard_has_a_user_folder_of_its_own(game: Game) -> None:
    code, out, error = game.run("--all", "--shards", "2", "--slots", "2")
    assert code == 0, out + error
    assert "Running 2 suites (2 parts) in 2 shards" in out and "2 slot limit" in out
    assert "2/2 suites, 2 test cases, 0 failures/errors" in out
    first, second = game.read_notes(2)
    # The variable that moves Godot's user data on this system, and a folder per shard.
    folders = sorted(Path(note[USER_DATA_VARIABLE]) for note in (first, second))
    assert [folder.name for folder in folders] == ["user_0", "user_1"]
    assert folders[0].parent == folders[1].parent and folders[0].parent.parent == game.temp
    assert folders[0].parent.name.startswith("godot_shards_")
    # Nothing stays: the user folders go with the run.
    assert game.left_behind() == [] and game.slots_held() == 0


@pytest.mark.parametrize("spelled", ["test/unit/test_a.gd", "test\\unit\\test_a.gd"])
def test_a_suite_is_named_with_either_slash_on_every_system(game: Game, spelled: str) -> None:
    code, out, error = game.run(spelled)
    assert code == 0, out + error
    assert "Running 1 suites (1 parts) in 1 shards" in out
    assert "1/1 suites, 1 test cases, 0 failures/errors" in out
    assert f"  {SUITES[0]}" in out and SUITES[1] not in out


def test_a_shard_that_hangs_is_killed_with_everything_it_started(game: Game) -> None:
    run = ("--all", "--shards", "1", "--slots", "1", "--timeout", "2")
    code, out, error = game.run(*run, STAND_IN_DOES="hang")
    assert code == 124, out + error
    assert "  shard 0: TIMEOUT after 2s" in out
    assert (
        "===== shard 0 FAILED (exit 124); log follows, each error once (--verbose: all of it) ====="
        in out
    )
    assert "STAND_IN hangs" in out  # the log of the shard that was killed
    assert "2 suite part(s) never reported" in out and "No test case ran." in out
    [note] = game.read_notes(1)
    assert wait_gone(note["pid"]), "the hung Godot is still running"
    assert _tree_gone(note), "what the hung Godot started is still running"
    assert game.left_behind() == [] and game.slots_held() == 0


def test_a_shard_that_timed_out_gives_its_slot_to_the_next(game: Game) -> None:
    """One slot, two shards, the first hangs: the second must not wait for a slot that
    the killed one still holds."""
    run = ("--all", "--shards", "2", "--slots", "1", "--timeout", "2", "--queue-timeout", "30")
    code, out, error = game.run(*run, STAND_IN_DOES="hang", STAND_IN_ON=SUITES[0])
    assert code == 124, out + error
    assert "  shard 0: TIMEOUT after 2s" in out and "  shard 1: exit 0 after" in out
    assert "no slot after" not in out
    assert "1 suite part(s) never reported" in out and f"\n  {SUITES[0]}\n" in out
    assert "1/2 suites, 1 test cases, 0 failures/errors" in out
    [hung] = [note for note in game.read_notes(2) if "child" in note]
    assert _tree_gone(hung)
    assert game.left_behind() == [] and game.slots_held() == 0


def test_a_slot_file_that_cannot_be_opened_is_found_before_any_shard_starts(game: Game) -> None:
    """The second slot's file cannot be made: one line and exit 2 at the start, not a
    traceback when the second shard asks for it and the first is already running."""
    (game.locks / "slot_1.lock").mkdir(parents=True)
    code, out, error = game.run("--all", "--shards", "2", "--slots", "2")
    assert code == 2, out + error
    assert error.startswith(f"cannot use the test slot folder {game.locks}: ")
    assert error.count("\n") == 1 and "Traceback" not in error
    assert "Running" not in out and list(game.notes.iterdir()) == []
    # One slot never asks for the second file.
    code, out, error = game.run("--all", "--shards", "2", "--slots", "1")
    assert code == 0, out + error


def test_a_relative_slot_folder_is_a_setup_error(game: Game) -> None:
    """It would be made in the working folder, and shared with no other run."""
    code, out, error = game.run("--all", GODOT_TEST_SLOT_DIR="slots_here")
    assert code == 2, out + error
    assert error.startswith("GODOT_TEST_SLOT_DIR must be an absolute path")
    assert error.count("\n") == 1 and "Running" not in out
    assert not [path for path in game.root.parent.rglob("slots_here")]


def test_a_process_a_passing_shard_left_running_is_ended_and_named(game: Game) -> None:
    """A suite that starts a server and forgets it. Linux ends it and says so; Windows
    ends it with the shard's job. (A real Godot on Windows does not end while it runs.)"""
    code, out, error = game.run("--all", "--shards", "1", "--slots", "1", STAND_IN_DOES="leak")
    [note] = game.read_notes(1)
    leaked = note["apart"]
    try:
        assert code == 0, out + error
        assert "2/2 suites, 2 test cases, 0 failures/errors" in out
        named = f"  shard 0 left 1 process(es) running, now ended: {leaked} "
        assert (named in out) is LINUX, out
        if not MACOS:  # macOS has no way to find it: its parent is gone
            assert wait_gone(leaked), "what the shard left running is still there"
    finally:
        if MACOS:
            with contextlib.suppress(OSError):
                os.kill(leaked, signal.SIGKILL)
    assert game.left_behind() == [] and game.slots_held() == 0


@linux_only("a locale under which Python reads file names as ASCII")
def test_a_suite_name_python_cannot_read_is_a_setup_error_not_a_false_failure(game: Game) -> None:
    """With UTF-8 mode off under the C locale the name is held as escaped bytes, which
    is not what gdUnit4 reports: the suite would count as never reported."""
    escaped = {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    (game.root / "test" / "unit" / "test_日本.gd").write_text(SUITE, encoding="utf-8")
    code, out, error = game.run("--all", **escaped)
    assert code == 2, out + error
    assert "a file name that is not ASCII needs Python to read names as UTF-8" in error
    assert "Fix: unset PYTHONUTF8 and PYTHONCOERCECLOCALE" in error
    assert error.count("\n") == 1 and "Traceback" not in error and "Running" not in out
    # A suite with an ASCII name runs there, and the C locale alone reads every name.
    code, out, error = game.run("test/unit/test_a.gd", **escaped)
    assert code == 0, out + error
    code, out, error = game.run("--all", LC_ALL="C")
    assert code == 0, out + error
    assert "3/3 suites, 3 test cases, 0 failures/errors" in out


@posix_only("signals that ask a process to end")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP", "SIGQUIT", "SIGINT"])
def test_a_run_that_is_stopped_leaves_nothing_behind(game: Game, name: str) -> None:
    process = game.start("--all", "--shards", "1", "--slots", "1", STAND_IN_DOES="hang")
    try:
        [note] = game.read_notes(1)
        assert game.slots_held() == 1
        number = getattr(signal, name)
        process.send_signal(number)
        _out, error = process.communicate(timeout=60)
    finally:
        process.kill()
    # 128 + the signal, as a shell reports it. Ctrl+C (SIGINT) is one line, no traceback.
    assert process.returncode == 128 + number
    assert error == ("Stopped by Ctrl+C.\n" if name == "SIGINT" else "")
    assert _tree_gone(note)
    assert game.left_behind() == [] and game.slots_held() == 0


@posix_only("signals that ask a process to end")
def test_a_signal_during_the_cleanup_does_not_cut_it_short(game: Game) -> None:
    """A timeout that signals a run, then signals it again while it cleans up."""
    process = game.start("--all", "--shards", "1", "--slots", "1", STAND_IN_DOES="hang")
    try:
        [note] = game.read_notes(1)
        deadline = time.monotonic() + 60
        while process.poll() is None and time.monotonic() < deadline:
            process.send_signal(signal.SIGTERM)
            time.sleep(0.005)
        _out, error = process.communicate(timeout=60)
    finally:
        process.kill()
    assert process.returncode == 128 + signal.SIGTERM
    assert error == "", "the cleanup was interrupted"
    assert _tree_gone(note)
    assert game.left_behind() == [] and game.slots_held() == 0


def test_a_run_that_is_killed_takes_its_godot_processes_with_it(game: Game) -> None:
    """An agent's timeout ends the runner at once: it removes no folder, but no Godot
    may be left running and no slot may stay taken."""
    process = game.start("--all", "--shards", "1", "--slots", "1", STAND_IN_DOES="hang")
    try:
        [note] = game.read_notes(1)
        assert game.slots_held() == 1
    finally:
        process.kill()
        process.communicate(timeout=60)
    assert _tree_gone(note)
    deadline = time.monotonic() + 20
    while game.slots_held() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert game.slots_held() == 0


def test_two_runs_share_the_slots(game: Game) -> None:
    """One slot, two runs at once: the second run's shard starts when the first has ended."""
    run = ("--shards", "1", "--slots", "1")
    runs = [
        game.start(suite, *run, STAND_IN_SECONDS="2")
        for suite in ("test/unit/test_a.gd", "test/unit/test_b.gd")
    ]
    try:
        said = [process.communicate(timeout=120) for process in runs]
    finally:
        for process in runs:
            process.kill()
    assert [process.returncode for process in runs] == [0, 0], said
    first, second = game.read_notes(2)
    assert first["ended"] <= second["started"], "two shards ran at once on one slot"
    assert game.left_behind() == [] and game.slots_held() == 0
