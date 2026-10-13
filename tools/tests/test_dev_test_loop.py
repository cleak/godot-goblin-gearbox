"""The parts under tools/test_shards.py: slot_pool.py (shared test slots, process
groups) and select_suites.py (which suites a change can affect)."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _processes
import pytest
from _gate_fixtures import LINUX, linux_only, posix_only, windows_only
from _processes import alive, wait_gone

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import select_suites  # noqa: E402
import slot_pool  # noqa: E402


def test_slots_are_shared_and_released(tmp_path: Path) -> None:
    pool = slot_pool.SlotPool(2, tmp_path)
    first = pool.try_acquire()
    second = pool.try_acquire()
    assert first is not None and second is not None
    assert {first.index, second.index} == {0, 1}
    assert pool.try_acquire() is None  # all taken: a third shard waits
    # A second pool on the same directory is another run on this machine.
    other = slot_pool.SlotPool(2, tmp_path)
    assert other.try_acquire() is None
    assert other.held() == 2
    first.release()
    freed = other.try_acquire()
    assert freed is not None and freed.index == first.index
    for slot in (second, freed):
        slot.release()
    assert pool.held() == 0


def test_a_slot_held_by_another_process_is_freed_when_it_dies(tmp_path: Path) -> None:
    holder = (
        "import sys, time; sys.path.insert(0, sys.argv[1]); import slot_pool; "
        "from pathlib import Path; "
        "slot = slot_pool.SlotPool(1, Path(sys.argv[2])).try_acquire(); "
        "print('held' if slot else 'none', flush=True); time.sleep(60)"
    )
    said = tmp_path / "said.txt"
    slots = tmp_path / "slots"
    # A group, because a virtual environment's python.exe is a launcher on Windows: the
    # interpreter that holds the lock is its child.
    with said.open("wb") as out:
        group = slot_pool.ProcessGroup.launch(
            [sys.executable, "-c", holder, str(TOOLS / "ci"), str(slots)], stdout=out
        )
    try:
        deadline = time.monotonic() + 20
        while not said.read_text().strip() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert said.read_text().strip() == "held"
        pool = slot_pool.SlotPool(1, slots)
        assert pool.try_acquire() is None and pool.held() == 1
    finally:
        group.kill()
        group.close()
    # Killed without releasing: the operating system drops its lock.
    slot = None
    deadline = time.monotonic() + 20
    while slot is None and time.monotonic() < deadline:
        slot = pool.try_acquire()
        time.sleep(0.1)
    assert slot is not None
    slot.release()


def test_zero_slots_never_limits(tmp_path: Path) -> None:
    pool = slot_pool.SlotPool(0, tmp_path / "unused")
    slots = [pool.try_acquire() for _ in range(50)]
    assert all(slot is not None for slot in slots)
    assert not (tmp_path / "unused").exists()
    for slot in slots:
        slot.release()


def test_default_slots_leave_cores_free_and_ci_gets_every_core() -> None:
    assert slot_pool.default_slot_count({"GODOT_TEST_SLOTS": "3"}) == 3
    assert slot_pool.default_slot_count({"GODOT_TEST_SLOTS": "0", "CI": "true"}) == 0
    assert slot_pool.default_slot_count({"GODOT_TEST_SLOTS": "5", "CI": "true"}) == 5
    assert slot_pool.default_slot_count({"CI": "true"}) == max(1, os.cpu_count() or 4)
    local = slot_pool.default_slot_count({})
    assert 2 <= local <= slot_pool.MAX_DEFAULT_SLOTS


@pytest.mark.parametrize("value", ["many", "-1", "2.5"])
def test_a_bad_slot_count_is_an_error_not_a_guess(value: str) -> None:
    with pytest.raises(ValueError, match="GODOT_TEST_SLOTS"):
        slot_pool.default_slot_count({"GODOT_TEST_SLOTS": value})


def test_the_slot_folder_is_one_for_every_run_on_the_machine(tmp_path: Path, monkeypatch) -> None:
    named = {"GODOT_TEST_SLOT_DIR": str(tmp_path / "named")}
    for platform in ("windows", "linux", "macos"):
        assert slot_pool.slot_directory(named, platform) == tmp_path / "named"
    # A session with a temp folder of its own: an agent's sandbox, or any user of a Mac.
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)  # what tempfile remembers of the old one
    assert Path(tempfile.gettempdir()) == tmp_path
    # Windows has no temp folder that every user shares: the pool is this user's.
    assert slot_pool.slot_directory({}, "windows") == tmp_path / "godot_test_slots"
    # Linux and macOS have one, and with two folders two runs would each think the
    # machine is theirs.
    for platform in ("linux", "macos"):
        assert slot_pool.slot_directory({}, platform) == Path("/tmp") / "godot_test_slots"
    here = "windows" if os.name == "nt" else "linux"
    assert slot_pool.slot_directory({}) == slot_pool.slot_directory({}, here)


@pytest.mark.parametrize("named", ["slots", "./slots", "../slots"])
def test_a_relative_slot_folder_is_refused(named: str) -> None:
    """It would be one folder per working folder: two runs would not share it."""
    for platform in ("windows", "linux", "macos"):
        with pytest.raises(slot_pool.SlotError) as said:
            slot_pool.slot_directory({"GODOT_TEST_SLOT_DIR": named}, platform)
        assert str(said.value) == (
            "GODOT_TEST_SLOT_DIR must be an absolute path, so that every test run on this "
            f"machine finds the same folder; it is '{named}'"
        )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@posix_only("permission bits, which decide whether another user's run can take a slot")
def test_a_folder_the_pool_makes_is_open_to_every_user(tmp_path: Path) -> None:
    made = tmp_path / "shared" / "slots"
    private = os.umask(0o077)  # a user whose files are closed to everyone else
    try:
        slot = slot_pool.SlotPool(1, made).try_acquire()
        # The lock of a project's import lives in a folder of the pool's folder, and may
        # be the first thing a run makes there.
        slot_pool.SlotPool(1, tmp_path / "pool" / "import_0123")
    finally:
        os.umask(private)
    assert slot is not None
    # Like /tmp: anyone may add a lock file, nobody may delete another user's.
    assert _mode(made) == 0o1777
    assert _mode(made / "slot_0.lock") == 0o644
    slot.release()
    # Every folder made on the way is as open: another user's run adds its files there.
    for folder in (made.parent, tmp_path / "pool", tmp_path / "pool" / "import_0123"):
        assert _mode(folder) == 0o1777, folder
    # A folder that is already there is its owner's: its mode stays.
    mine = tmp_path / "mine"
    mine.mkdir(mode=0o700)
    slot_pool.SlotPool(1, mine)
    assert _mode(mine) == 0o700
    assert _mode(tmp_path) != 0o1777


def test_every_slot_is_tried_when_the_pool_is_made(tmp_path: Path) -> None:
    """A slot that cannot be opened is found before the first shard starts (exit 2 in
    tools/test_shards.py), not when the second shard asks for it."""
    (tmp_path / "slot_1.lock").mkdir()
    slot_pool.SlotPool(1, tmp_path)
    with pytest.raises(slot_pool.SlotError, match="cannot use the test slot folder"):
        slot_pool.SlotPool(2, tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["slot_0.lock", "slot_1.lock"]


@posix_only("permission bits, which decide whether another user's run can take a slot")
def test_a_lock_file_this_user_may_only_read_can_be_taken(tmp_path: Path) -> None:
    """What another user's lock file looks like from here: readable, in a folder where
    this run may still create its own."""
    if os.geteuid() == 0:
        pytest.skip("root may write every file")
    slot_pool.SlotPool(1, tmp_path)
    lock = tmp_path / "slot_0.lock"
    lock.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        slot = slot_pool.SlotPool(1, tmp_path).try_acquire()
        assert slot is not None
        assert slot_pool.SlotPool(1, tmp_path).try_acquire() is None
        slot.release()
        # A slot whose file is missing cannot be added to a folder that is closed.
        with pytest.raises(slot_pool.SlotError, match="cannot use the test slot folder"):
            slot_pool.SlotPool(2, tmp_path).held()
    finally:
        tmp_path.chmod(0o755)


def test_a_slot_folder_that_cannot_be_used_is_one_line_with_the_fix(tmp_path: Path) -> None:
    blocked = tmp_path / "a_file"
    blocked.write_text("", encoding="utf-8")
    with pytest.raises(slot_pool.SlotError) as said:
        slot_pool.SlotPool(1, blocked)
    message = str(said.value)
    assert message.startswith(f"cannot use the test slot folder {blocked}: ")
    assert "Fix: make it writable, or set GODOT_TEST_SLOT_DIR to a folder" in message
    assert "\n" not in message
    # An error of the run's setup (exit 2 in tools/test_shards.py), not a crash.
    assert isinstance(said.value, RuntimeError)


@posix_only("a folder every user may write to, where a lock file may be somebody's link")
def test_a_lock_file_is_never_opened_through_a_link(tmp_path: Path) -> None:
    slots = tmp_path / "slots"
    slots.mkdir()
    for target in (tmp_path / "missing", tmp_path / "theirs"):
        (slots / "slot_0.lock").symlink_to(target)
        with pytest.raises(slot_pool.SlotError, match="cannot use the test slot folder"):
            slot_pool.SlotPool(1, slots)
        assert not (tmp_path / "missing").exists()
        (slots / "slot_0.lock").unlink()
        (tmp_path / "theirs").write_text("kept", encoding="utf-8")
    assert (tmp_path / "theirs").read_text(encoding="utf-8") == "kept"


@posix_only("a folder that cannot lock, such as some network shares")
def test_a_folder_that_cannot_lock_is_an_error_not_a_slot_that_looks_taken(
    tmp_path: Path, monkeypatch
) -> None:
    """Read as "taken", the run would wait an hour for a slot nobody holds."""
    pool = slot_pool.SlotPool(1, tmp_path)

    def refuse(_handle: int, _operation: int) -> None:
        raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr(slot_pool.fcntl, "flock", refuse)
    with pytest.raises(slot_pool.SlotError) as said:
        pool.try_acquire()
    lock = tmp_path / "slot_0.lock"
    assert str(said.value) == (
        f"cannot lock {lock}: Bad file descriptor. Fix: set GODOT_TEST_SLOT_DIR to a folder "
        "on a local disk"
    )
    # Found when the pool is made: before the first shard starts (exit 2), not after.
    with pytest.raises(slot_pool.SlotError, match="cannot lock"):
        slot_pool.SlotPool(1, tmp_path)


IDLE = "import time; time.sleep(60)"
BURN = "sum(range(2*10**7)); import time; time.sleep(60)"
SPAWN = (
    "import subprocess, sys, time;"
    "child = subprocess.Popen([sys.executable, '-c', {program!r}]{how});"
    "print(child.pid, flush=True);"
)
# A program that starts another and waits: a launcher and its engine.
SPAWN_AND_WAIT = SPAWN.format(program=BURN, how="") + "time.sleep(60)"
# The same, but what it starts leads a session of its own, as every program does that
# Godot's OS.create_process starts on Linux and macOS: no process group holds both.
# (Windows has no sessions: there this is SPAWN_AND_WAIT.)
SPAWN_APART_AND_WAIT = SPAWN.format(program=BURN, how=", start_new_session=True") + "time.sleep(60)"
# A program that starts another and ends: what it started is left running.
SPAWN_AND_LEAVE = SPAWN.format(program=IDLE, how="")
# The same, in a session of its own: what a suite leaves that called OS.create_process.
APART = ", start_new_session=True"
SPAWN_APART_AND_LEAVE = SPAWN.format(program=IDLE, how=APART)
# A program that waits, and whose child started another and ended: nobody is the parent
# of that one any more, and no process group holds it.
SPAWN_AN_ORPHAN_AND_WAIT = (
    "import subprocess, sys, time;"
    f"subprocess.run([sys.executable, '-c', {SPAWN_APART_AND_LEAVE!r}]);"
    "time.sleep(60)"
)
TREES = pytest.mark.parametrize(
    "program",
    [
        SPAWN_AND_WAIT,
        SPAWN_APART_AND_WAIT,
        pytest.param(
            SPAWN_AN_ORPHAN_AND_WAIT,
            marks=linux_only("/proc/<id>/environ, which says what a process was started with"),
        ),
    ],
    ids=["in its group", "in its own session", "after its parent ended"],
)


def _first_number(log: Path, seconds: float = 20.0) -> int:
    """The process id a test program printed into `log` as its first word."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        words = log.read_text(encoding="utf-8", errors="replace").split() if log.is_file() else []
        if words:
            return int(words[0])
        time.sleep(0.1)
    raise AssertionError(f"nothing was written to {log} in {seconds:g} s")


def _launch(program: str, log: Path, **options: object) -> slot_pool.ProcessGroup:
    with log.open("wb") as out:
        return slot_pool.ProcessGroup.launch(
            [sys.executable, "-c", program], stdout=out, stderr=subprocess.DEVNULL, **options
        )


@TREES
def test_a_process_group_kills_the_whole_tree(tmp_path: Path, program: str) -> None:
    group = _launch(program, tmp_path / "out.txt")
    try:
        grandchild = _first_number(tmp_path / "out.txt")
        assert group.process.poll() is None and alive(grandchild)
    finally:
        group.kill()
        group.close()
    assert group.process.poll() is not None
    assert wait_gone(grandchild), "the grandchild outlived its group"


@pytest.mark.parametrize(
    "program",
    [
        SPAWN_AND_LEAVE,
        pytest.param(
            SPAWN_APART_AND_LEAVE,
            marks=linux_only("/proc/<id>/environ, which says what a process was started with"),
        ),
    ],
    ids=["in its group", "in its own session"],
)
def test_closing_a_group_ends_what_its_program_left_running(tmp_path: Path, program: str) -> None:
    """A program that starts a server and forgets it: when it has ended, the server must
    too. In its own session is where Godot's OS.create_process puts what a suite starts;
    there macOS cannot find it once its parent has ended."""
    group = _launch(program, tmp_path / "out.txt")
    named: list[str] = []
    try:
        assert group.process.wait(timeout=30) == 0
        left = _first_number(tmp_path / "out.txt")
        assert alive(left)
    finally:
        named = group.close()
    assert wait_gone(left), "what the program started outlived its group"
    # Linux can say what was left: the runner prints it.
    assert [line.split()[0] for line in named] == ([str(left)] if LINUX else [])
    assert all("time.sleep(60)" in line for line in named)
    assert group.close() == []


@posix_only("sessions, which put a process outside its parent's process group")
def test_a_tree_is_ended_through_its_parents_alone(tmp_path: Path) -> None:
    """All macOS has: no mark is read there. So this tree is started without one."""
    with (tmp_path / "out.txt").open("wb") as out:
        leader = subprocess.Popen(
            [sys.executable, "-c", SPAWN_APART_AND_WAIT], stdout=out, start_new_session=True
        )
    apart = 0
    try:
        apart = _first_number(tmp_path / "out.txt")
        assert alive(apart) and os.getsid(apart) != os.getsid(leader.pid)
        slot_pool.end_tree(leader.pid, running=True)
        assert leader.wait(timeout=20) == -signal.SIGKILL
        assert wait_gone(apart), "what left the group outlived the tree"
    finally:
        leader.kill()
        leader.wait()
        if apart and alive(apart):
            with contextlib.suppress(ProcessLookupError):
                os.kill(apart, signal.SIGKILL)


@linux_only("/proc/<id>/stat, which says that a process has ended")
def test_a_process_that_ends_while_it_is_looked_at_is_gone(monkeypatch) -> None:
    """Between the signal that finds it and the read of its state, its parent may have
    waited for it: then nothing is there to read, and it is not running."""
    monkeypatch.setattr(_processes.os, "kill", lambda _process, _signal: None)
    assert not alive(2**22 + 7)  # above the largest process id Linux gives out


@posix_only("sessions, which put a process outside its parent's process group")
def test_a_killed_group_takes_what_left_it_where_no_mark_is_read(
    tmp_path: Path, monkeypatch
) -> None:
    """As on macOS: with nothing found by mark, a kill still ends the whole tree."""
    monkeypatch.setattr(slot_pool, "marked", lambda *_: {})
    group = _launch(SPAWN_APART_AND_WAIT, tmp_path / "out.txt")
    try:
        apart = _first_number(tmp_path / "out.txt")
        assert alive(apart)
    finally:
        group.kill()
        assert group.close() == []
    assert wait_gone(apart), "what left the group outlived it"


@posix_only("the watchdog process, which a job object makes needless on Windows")
def test_the_watchdog_ends_the_trees_it_was_told_of_and_no_other(tmp_path: Path) -> None:
    """What a run writes to its watchdog, and what that does when the run is gone. The
    trees carry no mark, as on macOS: each is found through its parents alone."""
    started = []
    for name in ("told", "ended", "never_told"):
        with (tmp_path / f"{name}.txt").open("wb") as out:
            started.append(
                subprocess.Popen(
                    [sys.executable, "-c", SPAWN_APART_AND_WAIT], stdout=out, start_new_session=True
                )
            )
    told, ended, never_told = started
    watchdog = subprocess.Popen(
        [sys.executable, "-I", "-S", str(TOOLS / "ci" / "slot_pool.py"), "--watch"],
        stdin=subprocess.PIPE,
    )
    try:
        apart = _first_number(tmp_path / "told.txt")
        assert watchdog.stdin is not None
        watchdog.stdin.write(b"+%d 0123abcd\n+%d 4567ef\n-%d\n" % (told.pid, ended.pid, ended.pid))
        watchdog.stdin.flush()
        assert not wait_gone(told.pid, seconds=0.5), "killed while its run was alive"
        watchdog.stdin.close()  # the run is gone
        assert watchdog.wait(timeout=30) == 0
        assert told.wait(timeout=20) == -signal.SIGKILL
        assert wait_gone(apart), "what left the group outlived its run"
        assert ended.poll() is None and never_told.poll() is None
    finally:
        watchdog.kill()
        watchdog.wait()
        for process in started:
            if process.poll() is None:
                slot_pool.end_tree(process.pid, running=True)
            process.wait()


def test_a_program_that_leaves_nothing_is_closed_without_a_word(tmp_path: Path) -> None:
    for program in ("pass", IDLE):
        group = _launch(program, tmp_path / "out.txt")
        if program == "pass":
            assert group.process.wait(timeout=30) == 0
        else:
            group.kill()  # what a kill ends was not left by a program that ended
        assert group.close() == []


def test_a_process_is_found_by_the_mark_it_was_started_with(tmp_path: Path) -> None:
    """macOS is never run here: it has no /proc, and nothing is found there."""
    mark = "0123456789abcdef"
    env = os.environ | {slot_pool.MARK_VARIABLE: mark}
    ours = subprocess.Popen([sys.executable, "-c", IDLE], env=env)
    other = subprocess.Popen([sys.executable, "-c", IDLE], env=env | {"UNRELATED": mark})
    stranger = subprocess.Popen(
        [sys.executable, "-c", IDLE], env=env | {slot_pool.MARK_VARIABLE: mark + "0"}
    )
    try:
        deadline = time.monotonic() + 20
        found = slot_pool.marked(mark)
        while LINUX and len(found) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)  # between fork and exec a child still shows its parent's
            found = slot_pool.marked(mark)
        assert set(found) == ({ours.pid, other.pid} if LINUX else set())
        assert all("time.sleep(60)" in command for command in found.values())
        assert slot_pool.marked(mark, "macos") == {} and slot_pool.marked(mark, "windows") == {}
        slot_pool.end_marked(mark)
        if LINUX:
            assert ours.wait(timeout=20) != 0 and other.wait(timeout=20) != 0
        assert stranger.poll() is None
    finally:
        for process in (ours, other, stranger):
            process.kill()
            process.wait()


@windows_only("job objects, which count the CPU of a tree while it runs")
def test_a_job_counts_the_cpu_of_the_whole_tree(tmp_path: Path) -> None:
    group = _launch(SPAWN_AND_WAIT, tmp_path / "out.txt")
    try:
        _first_number(tmp_path / "out.txt")
        deadline = time.monotonic() + 20
        while (group.cpu_seconds() or 0.0) < 0.05 and time.monotonic() < deadline:
            time.sleep(0.2)
        # The grandchild's CPU is counted: it was in the job from its first instruction.
        assert (group.cpu_seconds() or 0.0) >= 0.05
    finally:
        group.kill()
        group.close()


@posix_only("getrusage, which counts the CPU of children once they have ended")
def test_the_cpu_of_an_ended_group_is_counted(tmp_path: Path) -> None:
    before = slot_pool.children_cpu_seconds()
    assert before is not None
    group = _launch("sum(range(2*10**7))", tmp_path / "out.txt")
    try:
        assert group.cpu_seconds() is None  # nothing counts a running tree here
        assert group.process.wait(timeout=60) == 0
    finally:
        group.close()
    assert (slot_pool.children_cpu_seconds() or 0.0) - before >= 0.05


@posix_only("nice values")
def test_a_low_priority_group_is_ten_steps_nicer_than_the_run(tmp_path: Path) -> None:
    own = os.getpriority(os.PRIO_PROCESS, 0)
    low = _launch(SPAWN_AND_WAIT, tmp_path / "low.txt", low_priority=True)
    plain = _launch(IDLE, tmp_path / "plain.txt")
    try:
        grandchild = _first_number(tmp_path / "low.txt")
        wanted = min(19, own + slot_pool.POSIX_NICE)
        assert os.getpriority(os.PRIO_PROCESS, low.process.pid) == wanted
        # What it starts is as nice as it is.
        assert os.getpriority(os.PRIO_PROCESS, grandchild) == wanted
        assert os.getpriority(os.PRIO_PROCESS, plain.process.pid) == own
    finally:
        for group in (low, plain):
            group.kill()
            group.close()


@posix_only("a list of every process and its parent, which finds what left a group")
def test_the_list_of_processes_is_read_the_same_on_linux_and_macos() -> None:
    """Linux reads /proc and macOS asks ps; where both are there they must agree."""
    child = subprocess.Popen([sys.executable, "-c", IDLE])
    try:
        for platform in ("linux", "macos"):
            parents = slot_pool.process_parents(platform)
            assert parents[os.getpid()] == os.getppid(), platform
            assert parents[child.pid] == os.getpid(), platform
            assert child.pid in slot_pool.descendants(os.getpid(), parents), platform
    finally:
        child.kill()
        child.wait()


def test_macos_is_asked_for_its_processes_in_the_form_its_ps_reads(monkeypatch) -> None:
    """macOS is never run here: what its `ps` is asked and what it answers are pinned.
    POSIX gives each column an -o of its own once a title is set: after "=" the rest of
    the argument, commas too, is that title (BSD `ps` holds to it, Linux's does not)."""
    asked: list[list[str]] = []

    def ps(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        asked.append(command)
        listing = "    1     0\n  345     1\n 6789   345\n\nnot a row\n"
        return subprocess.CompletedProcess(command, 0, listing, "")

    monkeypatch.setattr(slot_pool.subprocess, "run", ps)
    monkeypatch.setattr(slot_pool, "ps_program", lambda: "/bin/ps")
    assert slot_pool.process_parents("macos") == {1: 0, 345: 1, 6789: 345}
    assert asked == [["/bin/ps", "-A", "-o", "pid=", "-o", "ppid="]]


def test_macos_ps_is_found_without_path_and_its_absence_cuts_no_kill_short(
    monkeypatch, capsys
) -> None:
    """A run started with a PATH of its own (no /bin) found no `ps`: the kill of a timed-out
    shard ended in FileNotFoundError and a traceback. /bin/ps is asked where it is, and a
    `ps` that cannot be started is one line: the group is killed all the same."""
    assert slot_pool.ps_program(lambda path: path == "/bin/ps") == "/bin/ps"
    assert slot_pool.ps_program(lambda _path: False) == "ps"

    def no_ps(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", command[0])

    signals: list[tuple[int, int, bool]] = []
    monkeypatch.setattr(slot_pool.subprocess, "run", no_ps)
    monkeypatch.setattr(slot_pool, "ps_program", lambda: "ps")
    monkeypatch.setattr(
        slot_pool,
        "_signal",
        lambda process, number, group=False: signals.append((process, number, group)),
    )
    monkeypatch.setattr(slot_pool.signal, "SIGSTOP", 19, raising=False)
    monkeypatch.setattr(slot_pool.signal, "SIGKILL", 9, raising=False)
    assert slot_pool.process_parents("macos") == {}
    slot_pool.end_tree(4321, True, "macos")
    assert signals == [(4321, 19, True), (4321, 9, True)]
    said = capsys.readouterr().err.splitlines()
    assert len(said) == 2 and said[0] == said[1]
    assert said[0] == (
        "cannot list this machine's processes (ps: No such file or directory): a process "
        "that left its group is not found, and may still run"
    )


def test_what_a_process_started_is_found_through_its_parents() -> None:
    parents = {0: 0, 1: 0, 20: 1, 30: 20, 31: 20, 40: 31, 50: 1, 60: 50}
    assert slot_pool.descendants(20, parents) == [30, 31, 40]
    assert slot_pool.descendants(50, parents) == [60]
    assert slot_pool.descendants(40, parents) == []
    assert slot_pool.descendants(99, parents) == []
    # Process 0 is its own parent on some systems: the search still ends.
    assert slot_pool.descendants(0, parents) == [1, 20, 50, 30, 31, 60, 40]


# A run that starts a group, says so, and waits for its input to end.
RUNNER = (
    "import subprocess, sys; sys.path.insert(0, sys.argv[1]); import slot_pool;"
    "group = slot_pool.ProcessGroup.launch([sys.executable, '-c', sys.argv[3]],"
    " stdin=subprocess.DEVNULL, stdout=open(sys.argv[2], 'wb'));"
    "print(group.process.pid, flush=True); sys.stdin.read()"
)


def _runner(tmp_path: Path, program: str) -> tuple[subprocess.Popen[str], int, int]:
    """Start RUNNER with `program` as its group; returns it with the ids of the program
    and of what that started, both running."""
    log = tmp_path / "grandchild.txt"
    # The real interpreter, not a virtual environment's launcher: on Windows the launcher
    # has a job of its own that would do the killing and hide a regression here.
    python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [python, "-c", RUNNER, str(TOOLS / "ci"), str(log), program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert process.stdout is not None
        child = int(process.stdout.readline())
        process.stdout.close()
        grandchild = _first_number(log)
        assert alive(child) and alive(grandchild)
    except BaseException:
        process.kill()
        process.wait()
        raise
    return process, child, grandchild


@TREES
def test_killing_the_runner_takes_its_godot_processes_with_it(tmp_path: Path, program: str) -> None:
    """A run that is killed outright (an agent's timeout) runs no cleanup of its own."""
    process, child, grandchild = _runner(tmp_path, program)
    process.kill()
    process.wait()
    assert wait_gone(child) and wait_gone(grandchild)


@TREES
def test_a_run_that_ends_without_closing_its_groups_takes_them_along(
    tmp_path: Path, program: str
) -> None:
    process, child, grandchild = _runner(tmp_path, program)
    assert process.stdin is not None
    process.stdin.close()
    assert process.wait(timeout=30) == 0
    assert wait_gone(child) and wait_gone(grandchild)


def _watchdog_of(runner: int, program: int) -> int:
    """The watchdog a run started: its one child that is not the group's program."""
    parents = slot_pool.process_parents()
    [watchdog] = [pid for pid, parent in parents.items() if parent == runner and pid != program]
    return watchdog


@posix_only("signals that end a process")
def test_the_watchdog_outlives_the_signal_that_ends_its_run(tmp_path: Path) -> None:
    """A timeout that signals a run and each of its children: the watchdog must still be
    there when the run is gone, or nothing ends what the run started."""
    process, child, grandchild = _runner(tmp_path, SPAWN_APART_AND_WAIT)
    try:
        watchdog = _watchdog_of(process.pid, child)
        os.kill(watchdog, signal.SIGTERM)
        assert not wait_gone(watchdog, seconds=1.0), "the watchdog died of SIGTERM"
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
    assert wait_gone(child) and wait_gone(grandchild) and wait_gone(watchdog)


# A run that starts a group, loses its watchdog to a kill, and starts a second group.
RUNNER_LOSING_ITS_WATCHDOG = (
    "import subprocess, sys; sys.path.insert(0, sys.argv[1]); import slot_pool;"
    "idle = [sys.executable, '-c', 'import time; time.sleep(60)'];"
    "first = slot_pool.ProcessGroup.launch(idle, stdin=subprocess.DEVNULL);"
    "slot_pool._watchdog._process.kill(); slot_pool._watchdog._process.wait();"
    "second = slot_pool.ProcessGroup.launch(idle, stdin=subprocess.DEVNULL);"
    "print(first.process.pid, second.process.pid, flush=True); sys.stdin.read()"
)


@posix_only("the watchdog process, which a job object makes needless on Windows")
def test_a_watchdog_that_was_killed_is_replaced_and_knows_every_group() -> None:
    python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [python, "-c", RUNNER_LOSING_ITS_WATCHDOG, str(TOOLS / "ci")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert process.stdout is not None
        first, second = (int(word) for word in process.stdout.readline().split())
        process.stdout.close()
        assert alive(first) and alive(second)
    finally:
        process.kill()
        process.wait()
    assert wait_gone(first), "the group started before the watchdog was killed is still there"
    assert wait_gone(second)


@posix_only("signals that end a process")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_a_signal_that_ends_the_runner_ends_its_godot_processes(tmp_path: Path, name: str) -> None:
    process, child, grandchild = _runner(tmp_path, SPAWN_APART_AND_WAIT)
    process.send_signal(getattr(signal, name))
    assert process.wait(timeout=30) != 0
    assert wait_gone(child) and wait_gone(grandchild)


def test_run_captured_returns_output_and_kills_on_timeout() -> None:
    code, output = slot_pool.run_captured(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], 30
    )
    assert code == 0 and "out" in output and "err" in output
    code, _ = slot_pool.run_captured([sys.executable, "-c", "raise SystemExit(7)"], 30)
    assert code == 7
    started = time.monotonic()
    for program in (SPAWN_AND_WAIT, SPAWN_APART_AND_WAIT):
        code, output = slot_pool.run_captured([sys.executable, "-u", "-c", program], 3)
        assert code is None and time.monotonic() - started < 60
        assert wait_gone(int(output.split()[0]))
    with pytest.raises(OSError):
        slot_pool.run_captured(["no-such-program-anywhere"], 5)


def _project(root: Path, files: dict[str, str], kit: dict | None = None) -> None:
    (root / "project.godot").write_text(
        '[application]\nconfig/name="x"\n\n[autoload]\n\nBus="*res://game/bus.gd"\n\n[debug]\n',
        encoding="utf-8",
    )
    if kit is not None:
        (root / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


SUITE = "extends GdUnitTestSuite\n"


def test_a_change_selects_direct_and_one_step_suites(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "game/bus.gd": "extends Node\n",
            "scripts/core/grid.gd": "class_name Grid\nextends RefCounted\n",
            "scripts/sim/rules.gd": "class_name Rules\nextends RefCounted\nvar g := Grid.new()\n",
            "scripts/sim/other.gd": "class_name Other\nextends RefCounted\n",
            "levels/world.tscn": '[ext_resource path="res://scripts/sim/rules.gd"]\n',
            "test/unit/test_grid.gd": SUITE + "var g := Grid.new()\n",
            "test/unit/test_rules.gd": SUITE + "var r := Rules.new()\n",
            "test/integration/test_world.gd": SUITE + 'var w := load("res://levels/world.tscn")\n',
            "test/unit/test_other.gd": SUITE + "var o := Other.new()\n",
        },
    )
    graph = select_suites.ReferenceGraph(tmp_path)
    picked = select_suites.select(tmp_path, ["scripts/core/grid.gd"], graph=graph).suites
    assert picked == {
        "res://test/unit/test_grid.gd": 1,
        "res://test/unit/test_rules.gd": 2,
    }
    # The scene is three steps from the grid: out of range, --all covers it.
    assert "res://test/integration/test_world.gd" not in picked


def test_a_suite_is_one_by_any_header_and_follows_its_base_suite(tmp_path: Path) -> None:
    # The same rule as the runner (tools/ci/suites.py): a suite the graph does not
    # recognise is never selected, so a change that breaks it would pass.
    _project(
        tmp_path,
        {
            "scripts/core/grid.gd": "class_name Grid\nextends RefCounted\n",
            "test/support/base_suite.gd": "class_name BaseSuite\nextends GdUnitTestSuite\n",
            "test/support/plain_base.gd": "extends GdUnitTestSuite\n",
            "test/unit/test_one_line.gd": "class_name OneLine extends GdUnitTestSuite\n"
            "var g := Grid.new()\n",
            "test/unit/test_by_path.gd": 'extends "res://addons/gdUnit4/src/GdUnitTestSuite.gd"\n'
            "var g := Grid.new()\n",
            "test/unit/test_derived.gd": "extends BaseSuite\nvar g := Grid.new()\n",
            "test/unit/test_beside.gd": 'extends "../support/plain_base.gd"\n',
            "test/unit/helper.gd": "extends RefCounted\nvar g := Grid.new()\n",
        },
    )
    assert set(select_suites.select(tmp_path, ["scripts/core/grid.gd"]).suites) == {
        "res://test/unit/test_by_path.gd",
        "res://test/unit/test_derived.gd",
        "res://test/unit/test_one_line.gd",
    }
    # A change to a base suite selects the suites built on it, however they name it.
    base = select_suites.select(tmp_path, ["test/support/base_suite.gd"]).suites
    assert base["res://test/unit/test_derived.gd"] == 1
    beside = select_suites.select(tmp_path, ["test/support/plain_base.gd"]).suites
    assert beside["res://test/unit/test_beside.gd"] == 1


def test_scripts_without_class_names_are_followed_by_preload_path(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "scripts/core/counter.gd": "extends RefCounted\n",
            "scripts/game/player.gd": 'const Counter := preload("res://scripts/core/counter.gd")\n',
            "main.tscn": '[ext_resource type="Script" path="res://scripts/game/player.gd"]\n',
            "test/unit/test_counter.gd": SUITE
            + 'const Counter := preload("res://scripts/core/counter.gd")\n',
            "test/unit/test_player.gd": SUITE
            + 'const Player := preload("res://scripts/game/player.gd")\n',
            "test/unit/test_main.gd": SUITE + 'const Main := preload("res://main.tscn")\n',
        },
    )
    assert select_suites.select(tmp_path, ["scripts/core/counter.gd"]).suites == {
        "res://test/unit/test_counter.gd": 1,
        "res://test/unit/test_player.gd": 2,
    }
    assert select_suites.select(tmp_path, ["scripts/game/player.gd"]).suites == {
        "res://test/unit/test_main.gd": 2,
        "res://test/unit/test_player.gd": 1,
    }


def test_uid_references_are_followed(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "scripts/core/counter.gd": "extends RefCounted\n",
            "scripts/core/counter.gd.uid": "uid://c0unter1\n",
            "levels/world.tscn": '[gd_scene load_steps=2 format=3 uid="uid://w0rld77"]\n',
            "test/unit/test_counter.gd": SUITE + 'const Counter := preload("uid://c0unter1")\n',
            "test/unit/test_world.gd": SUITE + 'var scene := load("uid://w0rld77")\n',
            "test/unit/test_other.gd": SUITE + 'var scene := load("uid://unknown1")\n',
        },
    )
    assert set(select_suites.select(tmp_path, ["scripts/core/counter.gd"]).suites) == {
        "res://test/unit/test_counter.gd"
    }
    assert set(select_suites.select(tmp_path, ["levels/world.tscn"]).suites) == {
        "res://test/unit/test_world.gd"
    }


def test_heavy_suites_need_a_direct_reference(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "scripts/a.gd": "class_name A\nextends RefCounted\n",
            "scripts/b.gd": "class_name B\nextends RefCounted\nvar a := A.new()\n",
            "test/unit/test_direct.gd": SUITE + "var a := A.new()\n",
            "test/unit/test_indirect.gd": SUITE + "var b := B.new()\n",
        },
    )
    heavy = {"res://test/unit/test_direct.gd", "res://test/unit/test_indirect.gd"}
    picked = select_suites.select(tmp_path, ["scripts/a.gd"], heavy).suites
    assert picked == {"res://test/unit/test_direct.gd": 1}


def test_a_changed_suite_runs_even_when_heavy_and_unnamed_files_select_nothing(
    tmp_path: Path,
) -> None:
    _project(tmp_path, {"test/unit/test_x.gd": SUITE})
    heavy = {"res://test/unit/test_x.gd"}
    picked = select_suites.select(tmp_path, ["test/unit/test_x.gd", "docs/a.md"], heavy)
    assert picked.suites == {"res://test/unit/test_x.gd": 0}
    assert picked.ignored == ["docs/a.md"]


def test_a_changed_data_file_selects_what_names_it_or_its_folder(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "data/levels.json": "{}",
            "test/fixtures/saves/old.bin": "",
            "scripts/loader.gd": 'class_name Loader\nconst PATH := "res://data/levels.json"\n',
            "test/unit/test_loader.gd": SUITE + "var l := Loader.new()\n",
            "test/unit/test_saves.gd": SUITE + 'const DIR := "res://test/fixtures/saves/"\n',
            "test/unit/test_one_save.gd": SUITE + 'var f := "res://test/fixtures/saves/old.bin"\n',
            "test/unit/test_other.gd": SUITE + 'const DIR := "res://test/fixtures/sav"\n',
        },
    )
    assert select_suites.select(tmp_path, ["data/levels.json"]).suites == {
        "res://test/unit/test_loader.gd": 2
    }
    saves = select_suites.select(tmp_path, ["test/fixtures/saves/old.bin"])
    assert saves.suites == {
        "res://test/unit/test_one_save.gd": 1,
        "res://test/unit/test_saves.gd": 1,
    }
    assert saves.ignored == []
    # A new fixture nobody names by file is still under the folder a suite names.
    added = select_suites.select(tmp_path, ["test/fixtures/saves/new.bin"]).suites
    assert added == {"res://test/unit/test_saves.gd": 1}


def test_godots_sidecar_files_count_as_the_file_they_belong_to(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "scripts/a.gd": "class_name A\nextends RefCounted\n",
            "art/icon.png": "",
            "ui/panel.tscn": '[ext_resource type="Texture2D" path="res://art/icon.png" id="1"]\n',
            "test/unit/test_a.gd": SUITE + "var a := A.new()\n",
            "test/unit/test_panel.gd": SUITE + 'var p := load("res://ui/panel.tscn")\n',
        },
    )
    by_uid = select_suites.select(tmp_path, ["scripts/a.gd.uid", "scripts/a.gd"])
    assert by_uid.changed == ["scripts/a.gd"]
    assert by_uid.suites == {"res://test/unit/test_a.gd": 1}
    by_import = select_suites.select(tmp_path, ["art/icon.png.import"])
    assert by_import.suites == {"res://test/unit/test_panel.gd": 2}


def test_hubs_do_not_pull_in_their_users(tmp_path: Path) -> None:
    files = {
        "scripts/leaf.gd": "class_name Leaf\nextends RefCounted\n",
        "scripts/hub.gd": "class_name Hub\nextends RefCounted\nvar l := Leaf.new()\n",
    }
    for index in range(select_suites.HUB_FAN_IN + 1):
        files[f"test/unit/test_user_{index}.gd"] = SUITE + "var h := Hub.new()\n"
    _project(tmp_path, files)
    picked = select_suites.select(tmp_path, ["scripts/leaf.gd"]).suites
    assert picked == {}  # Leaf -> Hub is a hub: its 41 suites are not all run
    assert len(select_suites.select(tmp_path, ["scripts/hub.gd"]).suites) == 41


def test_project_settings_widen_the_selection(tmp_path: Path) -> None:
    _project(tmp_path, {"test/unit/test_x.gd": SUITE})
    selection = select_suites.select(tmp_path, ["project.godot"])
    assert selection.widened_by == "project.godot"
    # kit.json pins the engine and gdUnit4 and says what counts as source.
    assert select_suites.select(tmp_path, ["docs/a.md", "kit.json"]).widened_by == "kit.json"


def test_source_outside_the_authored_set_widens_and_included_source_does_not(
    tmp_path: Path,
) -> None:
    kit = {"source": {"exclude": ["addons", "vendor"], "include": ["addons/mine"]}}
    _project(
        tmp_path,
        {
            "addons/mine/tool.gd": "class_name MineTool\nextends RefCounted\n",
            "test/unit/test_tool.gd": SUITE + "var t := MineTool.new()\n",
            "test/unit/test_x.gd": SUITE,
        },
        kit,
    )
    # A third-party addon cannot be traced: anything may use it.
    for changed in ("addons/gdUnit4/src/GdUnitAssert.gd", "vendor/lib/thing.tscn"):
        assert select_suites.select(tmp_path, [changed]).widened_by == changed
    # Not source at all, or a tool's own dot-folder: no effect on suites.
    for changed in ("addons/gdUnit4/plugin.cfg", ".venv/lib/x.gd", "README.md"):
        selection = select_suites.select(tmp_path, [changed])
        assert selection.widened_by == "" and selection.suites == {}
    # The project's own addon is authored source: traced like any script.
    mine = select_suites.select(tmp_path, ["addons/mine/tool.gd"])
    assert mine.widened_by == "" and mine.suites == {"res://test/unit/test_tool.gd": 1}


def test_suites_are_found_even_when_kit_json_excludes_the_test_folder(tmp_path: Path) -> None:
    kit = {"source": {"exclude": ["specs"]}, "tests": {"root": "specs"}}
    _project(
        tmp_path,
        {
            "scripts/a.gd": "class_name A\nextends RefCounted\n",
            "specs/test_a.gd": SUITE + "var a := A.new()\n",
        },
        kit,
    )
    assert select_suites.select(tmp_path, ["scripts/a.gd"]).suites == {"res://specs/test_a.gd": 1}
    assert select_suites.select(tmp_path, ["specs/test_a.gd"]).suites == {
        "res://specs/test_a.gd": 0
    }


def test_autoload_names_resolve_to_their_script(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "game/bus.gd": "extends Node\n",
            "test/unit/test_bus.gd": SUITE + "func test_x() -> void:\n\tBus.emit()\n",
        },
    )
    assert set(select_suites.select(tmp_path, ["game/bus.gd"]).suites) == {
        "res://test/unit/test_bus.gd"
    }


def test_a_deleted_script_still_selects_the_suites_that_name_it(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "scripts/user.gd": "class_name User\nextends RefCounted\nvar g := Gone.new()\n",
            "test/unit/test_user.gd": SUITE + "var u := User.new()\n",
            "test/unit/test_gone.gd": SUITE + "var g := Gone.new()\n",
        },
    )
    graph = select_suites.ReferenceGraph(tmp_path, removed={"scripts/gone.gd": "Gone"})
    picked = select_suites.select(tmp_path, ["scripts/gone.gd"], graph=graph)
    assert picked.ignored == []
    assert set(picked.suites) == {
        "res://test/unit/test_gone.gd",  # names it directly
        "res://test/unit/test_user.gd",  # names User, which names it
    }


def test_always_run_suites_join_any_source_change(tmp_path: Path) -> None:
    always = ["res://test/unit/test_rules_hold.gd", "res://test/unit/test_not_there.gd"]
    _project(
        tmp_path,
        {
            "scripts/a.gd": "class_name A\nextends RefCounted\n",
            "test/unit/test_rules_hold.gd": SUITE,
        },
    )
    heavy = set(always)
    for changed in ("scripts/a.gd", "levels/x.tscn", "x.tres", "shaders/x.gdshader"):
        picked = select_suites.select(tmp_path, [changed], heavy, always=always).suites
        assert picked == {"res://test/unit/test_rules_hold.gd": 0}, changed
    assert select_suites.select(tmp_path, ["docs/a.md"], always=always).suites == {}
    assert select_suites.select(tmp_path, ["scripts/a.gd"]).suites == {}


def _git(root: Path, *args: str) -> None:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", *identity, *args], cwd=root, check=True, capture_output=True)


def _repository(root: Path, branch: str = "main") -> None:
    _git(root, "init", "-q", "-b", branch)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")


def test_changed_files_and_selection_see_deletions_and_renames(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "scripts/old_name.gd": "class_name Doomed\nextends RefCounted\n",
            "scripts/stays.gd": "class_name Stays\nextends RefCounted\n",
            "test/unit/test_doomed.gd": SUITE + "var d := Doomed.new()\n",
            "test/unit/test_stays.gd": SUITE + "var s := Stays.new()\n",
        },
    )
    _repository(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "scripts" / "old_name.gd").rename(tmp_path / "scripts" / "new_name.gd")
    assert select_suites.default_base(tmp_path) == "main"
    base = select_suites.base_revision(tmp_path, "main")
    assert base
    changed = select_suites.changed_files(tmp_path, base)
    assert changed == ["scripts/new_name.gd", "scripts/old_name.gd"]
    picked = select_suites.select(tmp_path, changed, base_rev=base)
    # The renamed script's old class_name is read from the base commit, so the suite
    # that names it runs; the untouched one does not.
    assert "res://test/unit/test_doomed.gd" in picked.suites
    assert "res://test/unit/test_stays.gd" not in picked.suites


def test_changes_are_committed_staged_unstaged_and_untracked(tmp_path: Path) -> None:
    _project(tmp_path, {name: "" for name in ("a.gd", "b.gd", "c.gd", "kept.gd")})
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _repository(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "a.gd").write_text("# committed\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "work")
    (tmp_path / "b.gd").write_text("# staged\n", encoding="utf-8")
    _git(tmp_path, "add", "b.gd")
    (tmp_path / "c.gd").write_text("# unstaged\n", encoding="utf-8")
    (tmp_path / "new.gd").write_text("# untracked\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "x.gd").write_text("", encoding="utf-8")
    base = select_suites.base_revision(tmp_path, "main")
    assert select_suites.changed_files(tmp_path, base) == ["a.gd", "b.gd", "c.gd", "new.gd"]


def test_a_project_in_a_subfolder_of_its_repository_sees_only_its_own_paths(
    tmp_path: Path,
) -> None:
    game = tmp_path / "game"
    game.mkdir()
    _project(
        game,
        {
            "scripts/a.gd": "class_name A\nextends RefCounted\n",
            "scripts/gone.gd": "class_name Gone\nextends RefCounted\n",
            "test/unit/test_a.gd": SUITE + "var a := A.new()\n",
            "test/unit/test_gone.gd": SUITE + "var g := Gone.new()\n",
        },
    )
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "main.py").write_text("", encoding="utf-8")
    _repository(tmp_path)
    (game / "scripts" / "a.gd").write_text("class_name A\nextends RefCounted\n# x\n", "utf-8")
    (game / "scripts" / "gone.gd").unlink()
    (tmp_path / "server" / "main.py").write_text("# changed\n", encoding="utf-8")
    base = select_suites.base_revision(game, select_suites.default_base(game))
    changed = select_suites.changed_files(game, base)
    assert changed == ["scripts/a.gd", "scripts/gone.gd"]
    picked = select_suites.select(game, changed, base_rev=base).suites
    assert set(picked) == {"res://test/unit/test_a.gd", "res://test/unit/test_gone.gd"}


def test_the_base_branch_is_found_or_reported_missing(tmp_path: Path) -> None:
    assert select_suites.default_base(tmp_path) is None  # not a git repository
    assert select_suites.changed_files(tmp_path, "HEAD") is None
    _project(tmp_path, {"a.gd": ""})
    _repository(tmp_path, branch="master")
    assert select_suites.default_base(tmp_path) == "master"
    _git(tmp_path, "branch", "-m", "trunk")
    assert select_suites.default_base(tmp_path) is None
    assert select_suites.base_revision(tmp_path, "main") is None
    assert select_suites.base_revision(tmp_path, "trunk")


def test_the_remote_copy_of_the_base_branch_comes_before_the_local_one(tmp_path: Path) -> None:
    """`git init`, `git remote add`, `git push` leave no origin/HEAD. With the local main as
    the base, a commit on main is HEAD and the base at once: nothing counts as changed."""
    work = tmp_path / "work"
    work.mkdir()
    _project(work, {"a.gd": "", "b.gd": ""})
    _repository(work)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "origin", "main")
    assert select_suites.default_base(work) == "origin/main"
    (work / "a.gd").write_text("# committed on main\n", encoding="utf-8")
    _git(work, "commit", "-q", "-am", "work")
    changes = select_suites.working_tree_changes(work)
    assert (changes.base, changes.files, changes.note) == ("origin/main", ["a.gd"], "")


def test_on_the_base_branch_itself_a_note_says_commits_do_not_count(tmp_path: Path) -> None:
    _project(tmp_path, {"a.gd": "", "b.gd": ""})
    _repository(tmp_path)
    (tmp_path / "a.gd").write_text("# committed on main\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "work")
    (tmp_path / "b.gd").write_text("# not committed\n", encoding="utf-8")
    changes = select_suites.working_tree_changes(tmp_path)
    assert (changes.base, changes.files) == ("main", ["b.gd"])
    assert changes.note.startswith("on 'main', the base branch itself: commits made on it do not")
    # On a branch of it the commits count, and there is nothing to say.
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _git(tmp_path, "commit", "-q", "-am", "more")
    changes = select_suites.working_tree_changes(tmp_path)
    assert (changes.files, changes.note) == (["b.gd"], "")
