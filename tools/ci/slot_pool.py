"""A machine-wide pool of test slots, so several test runs share one computer.

Several coding agents (or terminals) may each run `tools/test_shards.py` at once. If
every run started one Godot per core, N runs would mean N x cores busy processes and a
machine too slow to use. So each shard process first takes one slot from this pool: a
lock file per slot in one folder, shared by every checkout and worktree of every
project. When all are taken, the shard waits its turn. The operating system drops a
lock when its holder dies, so a crashed run never leaks a slot. A shard holds at most
one slot and holds nothing while it waits, so runs cannot deadlock.

    GODOT_TEST_SLOTS=6        slots on this machine (default: a quarter of the cores,
                              at least 2, at most 8)
    GODOT_TEST_SLOTS=0        no limit
    CI=true                   slots = cores (a CI runner has the machine to itself)
    GODOT_TEST_SLOT_DIR=...   where the lock files live, as an absolute path; point CI
                              containers that share one host at one mounted folder

The folder, when GODOT_TEST_SLOT_DIR does not name one:

    Windows         %TEMP%\\godot_test_slots: the runs of one user
    Linux, macOS    /tmp/godot_test_slots: the runs of every user. Not $TMPDIR, which
                    is one user's (macOS) or one session's and would split the pool.

On Linux and macOS every folder the pool creates is open to every user (mode 1777, as
/tmp is) and a lock is taken on a file opened for reading, so one user's run can take a
slot whose file another user created. A folder that exists keeps its mode.

Test processes also start at below-normal priority (Windows) or 10 steps nicer than the
run (Linux, macOS), so an editor, a running game and the agents' own tools stay
responsive while the suites run.

A started program is a ProcessGroup: it and everything it starts end together, when
the group is killed or closed and when the run that started it dies, however it dies.
On Linux and macOS a watchdog process sees to the last: this file, run with --watch.
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_config  # noqa: E402

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl
    import resource

SLOTS_VARIABLE = "GODOT_TEST_SLOTS"
SLOT_DIR_VARIABLE = "GODOT_TEST_SLOT_DIR"
SLOT_FOLDER = "godot_test_slots"
# The one temp folder every user and every session of a Linux or macOS machine shares.
POSIX_SHARED_TEMP = "/tmp"
# Tests get a quarter of the cores. The rest stay free for the editor, the
# game under test and whatever else the other agents are running.
CORE_FRACTION = 4
MAX_DEFAULT_SLOTS = 8
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_SUSPENDED = 0x00000004
POSIX_NICE = 10
POSIX_NICEST = 19
SHARED_FOLDER_MODE = 0o1777
LOCK_FILE_MODE = 0o644
# A lock file is missing, then made by another run before this one makes it: twice at most.
LOCK_OPEN_TRIES = 5
# Set to a value of its own for each ProcessGroup on Linux and macOS: what the group's
# program starts inherits it, whoever its parent is later (see `marked`).
MARK_VARIABLE = "GODOT_PROCESS_GROUP"
# A killed process is listed until its parent has taken note: looked for this often.
MARK_SWEEPS = 50
MARK_SWEEP_SECONDS = 0.02
SHOWN_COMMAND_CHARS = 80
# The argument that makes this file the watchdog of a run (see `_watch`).
WATCH = "--watch"


class SlotError(RuntimeError):
    """The slot folder cannot be used; the message is one line with the fix."""


def default_slot_count(env: Mapping[str, str] | None = None) -> int:
    """Slots for this machine: GODOT_TEST_SLOTS, else every core in CI, else a core
    fraction. Raises ValueError when GODOT_TEST_SLOTS is not a whole number >= 0."""
    env = os.environ if env is None else env
    explicit = env.get(SLOTS_VARIABLE, "").strip()
    if explicit:
        if not explicit.isdecimal():
            raise ValueError(f"{SLOTS_VARIABLE} must be a whole number >= 0, got '{explicit}'")
        return int(explicit)
    if env.get("CI"):
        return max(1, os.cpu_count() or 4)
    return max(2, min(MAX_DEFAULT_SLOTS, (os.cpu_count() or 8) // CORE_FRACTION))


def slot_directory(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Where the lock files live: GODOT_TEST_SLOT_DIR, else the folder every run on a
    `platform` machine (default: this one) finds without being told. Raises SlotError
    for a relative GODOT_TEST_SLOT_DIR: it would name one folder per working folder."""
    env = os.environ if env is None else env
    named = env.get(SLOT_DIR_VARIABLE)
    if named:
        if not Path(named).is_absolute():
            raise SlotError(
                f"{SLOT_DIR_VARIABLE} must be an absolute path, so that every test run on "
                f"this machine finds the same folder; it is '{named}'"
            )
        return Path(named)
    if (platform or kit_config.host_platform()) == "windows":
        return Path(tempfile.gettempdir()) / SLOT_FOLDER
    return Path(POSIX_SHARED_TEMP) / SLOT_FOLDER


class Slot:
    """One held slot; `release()` frees it (closing the file also does)."""

    def __init__(self, index: int, handle: IO[bytes]) -> None:
        self.index = index
        self._handle: IO[bytes] | None = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()


class SlotPool:
    """`count` lock-file slots under `directory`; count 0 never limits. Raises SlotError
    when the folder cannot be made, or a lock file in it cannot be opened or locked."""

    def __init__(self, count: int, directory: Path) -> None:
        self.count = count
        self.directory = directory
        if count:
            _make_shared(directory)
            # Every slot is opened and tried once: a folder that cannot be used, or that
            # cannot take one more lock file, is found now, not after the run has started.
            self.held()

    def _open(self, index: int) -> IO[bytes]:
        return _open_lock(self.directory / f"slot_{index}.lock")

    def try_acquire(self) -> Slot | None:
        """A free slot, or None when all are held. Never blocks."""
        if not self.count:
            return Slot(-1, _NullHandle())
        for index in range(self.count):
            handle = self._open(index)
            if _try_lock(handle):
                return Slot(index, handle)
            handle.close()
        return None

    def held(self) -> int:
        """How many slots are taken right now, by anyone (for status lines)."""
        taken = 0
        for index in range(self.count):
            handle = self._open(index)
            try:
                if _try_lock(handle):
                    _unlock(handle)
                else:
                    taken += 1
            finally:
                handle.close()
        return taken


class _NullHandle:
    """Stands in for a lock file when the pool is unlimited."""

    def close(self) -> None:
        return None


def _unusable(directory: Path, error: OSError) -> SlotError:
    return SlotError(
        f"cannot use the test slot folder {directory}: {error.strerror or error}. Fix: make "
        f"it writable, or set {SLOT_DIR_VARIABLE} to a folder every test run on this "
        "machine can write to"
    )


def _make_shared(directory: Path) -> None:
    """Create the pool's folder, and each folder above it that is missing. Off Windows
    every folder made here is open to every user, so another user's run can add its lock
    files to it and its own folders under it; the mode is set after a folder is made
    because the umask applies to `mkdir`. A folder that exists keeps its mode."""
    try:
        missing = [folder for folder in (directory, *directory.parents) if not folder.exists()]
        for folder in reversed(missing):
            with contextlib.suppress(FileExistsError):  # another run made it just now
                folder.mkdir()
                if os.name != "nt":
                    folder.chmod(SHARED_FOLDER_MODE)
    except OSError as error:
        raise _unusable(directory, error) from error


def _open_lock(path: Path) -> IO[bytes]:
    """A lock file, created if missing. Off Windows it is opened for reading, which is
    all a lock needs and all another user's file allows, and never through a link: the
    folder may be writable by everyone."""
    try:
        if os.name == "nt":
            return path.open("a+b")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

        def create(name: str, _flags: int) -> int:
            made = os.open(name, flags | os.O_CREAT | os.O_EXCL, LOCK_FILE_MODE)
            os.fchmod(made, LOCK_FILE_MODE)  # readable by every user, whatever the umask
            return made

        for _ in range(LOCK_OPEN_TRIES):
            with contextlib.suppress(FileNotFoundError):
                return open(path, "rb", opener=lambda name, _flags: os.open(name, flags))
            with contextlib.suppress(FileExistsError):  # another run made it just now
                return open(path, "rb", opener=create)
        raise OSError(errno.EAGAIN, f"{path.name} is there and gone by turns")
    except OSError as error:
        raise _unusable(path.parent, error) from error


def _try_lock(handle: IO[bytes]) -> bool:
    if os.name == "nt":
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError as error:
        # Not "taken": this folder cannot lock at all (a network share may need a file
        # opened for writing). Waiting for a slot would wait for ever.
        handle.close()
        raise SlotError(
            f"cannot lock {handle.name}: {error.strerror or error}. Fix: set "
            f"{SLOT_DIR_VARIABLE} to a folder on a local disk"
        ) from error
    return True


def _unlock(handle: IO[bytes]) -> None:
    if isinstance(handle, _NullHandle):
        return
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ps_program(is_file: Callable[[str], bool] = os.path.isfile) -> str:
    """The `ps` to ask: /bin/ps, where macOS keeps it, whatever PATH holds (a run may be
    started with a PATH of its own); the one on PATH where that file is not there."""
    return "/bin/ps" if is_file("/bin/ps") else "ps"


def process_parents(platform: str | None = None) -> dict[int, int]:
    """Every process of the machine -> its parent, on Linux and macOS. Linux reads /proc;
    macOS has none and asks `ps`, which every Mac has. A process that ends while the
    list is read is left out. A `ps` that cannot be started is one line on stderr and an
    empty list: the caller still kills what it knows of."""
    parents: dict[int, int] = {}
    if (platform or kit_config.host_platform()) == "linux" and os.path.isdir("/proc/self"):
        for entry in os.listdir("/proc"):
            if entry.isdecimal():
                with contextlib.suppress(OSError, IndexError, ValueError):
                    stat = Path("/proc", entry, "stat").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    # "<pid> (<name>) <state> <parent> ...": the name may hold ") ".
                    parents[int(entry)] = int(stat.rpartition(")")[2].split()[1])
        return parents
    # One -o for each column: after "=" the rest of the argument is the column's title.
    ps = ps_program()
    try:
        listing = subprocess.run(
            [ps, "-A", "-o", "pid=", "-o", "ppid="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        reason = getattr(error, "strerror", None) or str(error)
        print(
            f"cannot list this machine's processes ({ps}: {reason}): a process that left "
            "its group is not found, and may still run",
            file=sys.stderr,
        )
        return parents
    for line in listing.stdout.splitlines():
        pair = line.split()
        if len(pair) == 2 and pair[0].isdecimal() and pair[1].isdecimal():
            parents[int(pair[0])] = int(pair[1])
    return parents


def descendants(process: int, parents: Mapping[int, int]) -> list[int]:
    """Every process that `process` started, directly or through another, nearest first.
    One whose parent has ended is not found: it belongs to nobody."""
    started: dict[int, list[int]] = {}
    for child, parent in parents.items():
        started.setdefault(parent, []).append(child)
    found: list[int] = []
    waiting = [process]
    while waiting:
        for child in sorted(started.get(waiting.pop(0), ())):
            if child != process and child not in found:
                found.append(child)
                waiting.append(child)
    return found


def _signal(process: int, number: int, group: bool = False) -> None:
    # Gone already; macOS answers "not permitted" for a group of ended processes.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        (os.killpg if group else os.kill)(process, number)


def end_tree(leader: int, running: bool, platform: str | None = None) -> None:
    """Kill the process group that `leader` leads and, when `running`, every process it
    started that left the group. Godot's OS.create_process gives what it starts a session
    of its own, so a server a test launched is in no group of ours and is found by its
    parent alone. `running`: the leader has not been waited for, so the number is still
    its own. Without it only the group is killed, and a process that left it stays.

    The tree is stopped before it is read, then read until nothing new shows: a stopped
    process starts nothing. It is killed leaves and all before the leader, whose number
    its parent keeps until then: no number is free for a stranger while this works."""
    left: list[int] = []
    try:
        if running:
            _signal(leader, signal.SIGSTOP, group=True)
            while True:
                tree = descendants(leader, process_parents(platform))
                new = [process for process in tree if process not in left]
                if not new:
                    break
                for process in new:
                    _signal(process, signal.SIGSTOP)
                left += new
    finally:
        # Also when the tree could not be read: nothing stays stopped.
        for process in left:
            _signal(process, signal.SIGKILL)
        _signal(leader, signal.SIGKILL, group=True)


def marked(mark: str, platform: str | None = None) -> dict[int, str]:
    """Every running process that was started with `mark` in MARK_VARIABLE -> its command
    line (the program's name without its folder), on Linux. A ProcessGroup's program is
    started with a mark of its own, and what that starts inherits it: a process is found
    by it in a session of its own and after its parent has ended. Read from
    /proc/<id>/environ; macOS has none, so nothing is found there. One that was started
    with an environment of its own making is not found."""
    found: dict[int, str] = {}
    if (platform or kit_config.host_platform()) != "linux" or not os.path.isdir("/proc/self"):
        return found
    wanted = f"{MARK_VARIABLE}={mark}".encode()
    for entry in os.listdir("/proc"):
        if entry.isdecimal():
            # Another user's process may not be read; one that has ended has nothing to read.
            with contextlib.suppress(OSError):
                if wanted in Path("/proc", entry, "environ").read_bytes().split(b"\0"):
                    words = Path("/proc", entry, "cmdline").read_bytes().split(b"\0")
                    words[0] = words[0].rpartition(b"/")[2]
                    command = b" ".join(words).decode("utf-8", errors="replace").strip()
                    found[int(entry)] = command[:SHOWN_COMMAND_CHARS]
    return found


def end_marked(mark: str, platform: str | None = None) -> None:
    """Kill every process `marked` finds, until it finds none."""
    for _ in range(MARK_SWEEPS):
        found = marked(mark, platform)
        if not found:
            return
        for process in found:
            _signal(process, signal.SIGKILL)
        time.sleep(MARK_SWEEP_SECONDS)


def _leads_a_session(process: int) -> bool:
    """True while `process` runs as the leader of its session, as every ProcessGroup's
    program does: the number has not gone to a stranger."""
    try:
        return os.getsid(process) == process
    except OSError:
        return False


def _watch() -> None:
    """The watchdog: what a job object does on Windows, for Linux and macOS. It runs
    beside a run and outlives it by a moment. It reads the process groups the run starts
    ("+<id> <mark>") and ends ("-<id>") from its input, which only the run can write to.
    When the input ends the run is gone, however it went, and the trees still listed are
    killed. It ignores the signals that stop a run, so it is there to do that."""
    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"):
        signal.signal(getattr(signal, name), signal.SIG_IGN)
    groups: dict[int, str] = {}
    for line in sys.stdin.buffer:
        words = line[1:].split()
        if line[:1] == b"+":
            groups[int(words[0])] = words[1].decode()
        else:
            groups.pop(int(words[0]), None)
    for group, mark in sorted(groups.items()):
        end_tree(group, _leads_a_session(group))
        end_marked(mark)


class _Watchdog:
    """This run's watchdog process (see `_watch`), started when the first group is."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._groups: dict[int, str] = {}
        self._registered = False

    def _start(self) -> subprocess.Popen[bytes]:
        if not self._registered:
            self._registered = True
            atexit.register(self.stop)
        # A session of its own: Ctrl+C in the run's terminal does not reach it. Nothing
        # of the run is kept open by it: no output, no working folder, no lock file.
        return subprocess.Popen(
            [sys.executable, "-I", "-S", str(Path(__file__).resolve()), WATCH],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            bufsize=0,
            start_new_session=True,
        )

    def _tell(self, group: int, mark: str | None) -> None:
        """`mark`: the group's (see `marked`) when it starts, None when it has ended."""
        with self._lock:
            if mark is None:
                self._groups.pop(group, None)
            else:
                self._groups[group] = mark
            if self._process is None and not self._groups:
                return  # nothing runs: nothing to watch
            news = b"-%d\n" % group if mark is None else b"+%d %s\n" % (group, mark.encode())
            for _ in range(2):
                if self._process is None or self._process.poll() is not None:
                    # None yet, or it was killed: a new one hears of every group that runs.
                    self._process = self._start()
                    news = b"".join(
                        b"+%d %s\n" % (known, its.encode())
                        for known, its in sorted(self._groups.items())
                    )
                assert self._process.stdin is not None
                try:
                    self._process.stdin.write(news)
                    return
                except OSError:
                    self._process = None
            raise OSError("the process that ends this run's process trees does not stay up")

    def add(self, group: int, mark: str) -> None:
        self._tell(group, mark)

    def remove(self, group: int) -> None:
        self._tell(group, None)

    def stop(self) -> None:
        """End it; the trees it still lists are killed."""
        with self._lock:
            process, self._process = self._process, None
        if process is None or process.stdin is None:
            return
        process.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)


_watchdog = _Watchdog()


class ProcessGroup:
    """A child and everything it spawns, counted and killed as one. Use `launch`.

    Windows: a job object. The Godot launcher (`..._console.exe`) starts the real engine
    as its own child, so a plain Popen handle sees neither the CPU nor the process to
    kill. The child starts suspended, is put in the job, and only then runs, so it
    cannot spawn a child that escapes the job. The job kills its processes when its last
    handle closes, and this process holds the only one.

    Linux and macOS: the child leads a session of its own, so its tree is one process
    group that one signal reaches. What leaves that group (Godot's OS.create_process
    starts a session for every program) is found through its parent: see `end_tree`.

    On every system the tree ends when the group is killed, when it is closed, and when
    this process dies (Ctrl+C, an agent's timeout, a kill): no Godot is left behind.

    A program that ended and left a process running, which nobody is the parent of any
    more, is ended when the group is closed: Windows by the job; Linux by the mark every
    process of the group was started with (see `marked`), also in a session of its own.
    macOS ends it only while it is in the program's process group: one that Godot's
    OS.create_process started there stays. (On Windows Godot's console program does not
    end while a process started with OS.create_process runs: that shard times out.)
    """

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        job: Any = None,
        session: bool = False,
        mark: str = "",
    ) -> None:
        self.process = process
        self._job: Any = job
        self._session = session
        self._session_ended = False
        self._mark = mark

    @classmethod
    def launch(
        cls, command: list[str], low_priority: bool = False, **popen_options: Any
    ) -> ProcessGroup:
        """Start `command` (Popen keyword arguments in `popen_options`) as a group."""
        if os.name != "nt":
            return cls._launch_session(command, low_priority, popen_options)
        flags = popen_options.pop("creationflags", 0) | CREATE_SUSPENDED
        if low_priority:
            flags |= BELOW_NORMAL_PRIORITY_CLASS
        process = subprocess.Popen(command, creationflags=flags, **popen_options)
        try:
            return cls(process, _job_for_suspended(process.pid))
        except BaseException:
            process.kill()  # never leave it suspended
            raise

    @classmethod
    def _launch_session(
        cls, command: list[str], low_priority: bool, popen_options: dict[str, Any]
    ) -> ProcessGroup:
        mark = os.urandom(16).hex()
        given = popen_options.pop("env", None)
        env = dict(os.environ if given is None else given) | {MARK_VARIABLE: mark}
        process = subprocess.Popen(command, start_new_session=True, env=env, **popen_options)
        group = cls(process, session=True, mark=mark)
        try:
            _watchdog.add(process.pid, mark)
            if low_priority:
                # Set from here, not in the child before it starts: code between fork and
                # exec can deadlock a process that has threads. What the child starts
                # inherits the value. Relative to this process, so it is always allowed.
                nice = min(POSIX_NICEST, os.getpriority(os.PRIO_PROCESS, 0) + POSIX_NICE)
                with contextlib.suppress(ProcessLookupError):  # it has ended already
                    os.setpriority(os.PRIO_PGRP, process.pid, nice)
        except BaseException:
            group.kill()
            raise
        return group

    def cpu_seconds(self) -> float | None:
        """CPU seconds of the whole tree so far (Windows; None elsewhere, where
        `children_cpu_seconds` counts what has ended)."""
        if self._job is None:
            return None
        info = _JobAccounting()
        ok = _KERNEL32.QueryInformationJobObject(
            self._job, JOB_BASIC_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
        )
        if not ok:
            return None
        return (info.TotalUserTime + info.TotalKernelTime) / 1e7  # 100 ns ticks

    def _end_session(self) -> None:
        """Once: when every process of the group has ended, its number is free again."""
        if self._session and not self._session_ended:
            self._session_ended = True
            # poll() is None: the child runs. Should it end now, it keeps its number until
            # it is waited for, which only this process does.
            end_tree(self.process.pid, running=self.process.poll() is None)

    def kill(self) -> None:
        if self._job is not None:
            _KERNEL32.TerminateJobObject(self._job, 1)
        self._end_session()
        self.process.kill()
        self.process.wait()

    def close(self) -> list[str]:
        """Let go of the group; what is still running in it is killed. Returns what a
        program that ended by itself had left running, one "<process id> <command line>"
        each, where the system can say (Linux)."""
        left: dict[int, str] = {}
        if self._job is not None:
            _KERNEL32.CloseHandle(self._job)
            self._job = None
        if self._session:
            if not self._session_ended and self.process.poll() is not None:
                left = marked(self._mark)
            self._end_session()
            if self.process.poll() is None:
                self.process.wait()
            end_marked(self._mark)
            self._session = False
            _watchdog.remove(self.process.pid)
        return [f"{process} {command}".rstrip() for process, command in sorted(left.items())]


if os.name == "nt":
    JOB_BASIC_ACCOUNTING = 1
    JOB_EXTENDED_LIMITS = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    PROCESS_SUSPEND_RESUME = 0x0800
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateJobObjectW.restype = wintypes.HANDLE
    _KERNEL32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _KERNEL32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _KERNEL32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    _KERNEL32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _KERNEL32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _NTDLL = ctypes.WinDLL("ntdll")
    _NTDLL.NtResumeProcess.argtypes = [wintypes.HANDLE]

    class _JobAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", ctypes.c_ulonglong * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _kill_on_close(job: Any) -> None:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _KERNEL32.SetInformationJobObject(
            job, JOB_EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits)
        )

    def _job_for_suspended(pid: int) -> Any:
        """A job holding the suspended process (None if that failed), then resume it."""
        job = _KERNEL32.CreateJobObjectW(None, None)
        handle = _KERNEL32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME, False, pid
        )
        if not handle:
            if job:
                _KERNEL32.CloseHandle(job)
            raise OSError(f"cannot open process {pid} to put it in a job")
        try:
            assigned = bool(job) and _KERNEL32.AssignProcessToJobObject(job, handle)
            if assigned:
                _kill_on_close(job)
            # Resume even when the job failed: a test run beats a hung one.
            _NTDLL.NtResumeProcess(handle)
        finally:
            _KERNEL32.CloseHandle(handle)
        if job and not assigned:
            _KERNEL32.CloseHandle(job)
            return None
        return job or None


def run_captured(
    command: list[str],
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int | None, str]:
    """Run `command` to its end as a ProcessGroup: (exit code, stdout and stderr).
    The exit code is None when `timeout` seconds passed and the whole tree was killed.

    Output goes to a file, not a pipe: after a timeout a pipe stays open for as long as
    any grandchild lives (Godot's launcher starts the engine as one), and reading it
    would hang.
    """
    with tempfile.TemporaryFile() as log:
        group = ProcessGroup.launch(
            command,
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            code: int | None = group.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            group.kill()
            code = None
        except BaseException:
            group.kill()
            raise
        finally:
            group.close()
        log.seek(0)
        return code, log.read().decode("utf-8", errors="replace")


def children_cpu_seconds() -> float | None:
    """CPU seconds of every child of this process that has ended and been waited for,
    with what those waited for in turn (Linux and macOS; None on Windows, where
    ProcessGroup.cpu_seconds counts a tree while it runs)."""
    if os.name == "nt":
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


if __name__ == "__main__":
    if sys.argv[1:] != [WATCH] or os.name == "nt":
        sys.exit("slot_pool.py is run by the tools that start Godot, never by hand")
    _watch()
