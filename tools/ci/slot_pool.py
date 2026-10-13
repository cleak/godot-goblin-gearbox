"""A machine-wide pool of test slots, so several test runs share one computer.

Several coding agents (or terminals) may each run `tools/test_shards.py` at once. If
every run started one Godot per core, N runs would mean N x cores busy processes and a
machine too slow to use. So each shard process first takes one slot from this pool: a
lock file per slot in the temp folder, shared by every checkout and worktree of every
project on the machine. When all are taken, the shard waits its turn. The operating
system drops a lock when its holder dies, so a crashed run never leaks a slot. A shard
holds at most one slot and holds nothing while it waits, so runs cannot deadlock.

    GODOT_TEST_SLOTS=6        slots on this machine (default: a quarter of the cores,
                              at least 2, at most 8)
    GODOT_TEST_SLOTS=0        no limit
    CI=true                   slots = cores (a CI runner has the machine to itself)
    GODOT_TEST_SLOT_DIR=...   where the lock files live (default: <tmp>/godot_test_slots);
                              point CI containers that share one host at one mounted folder

Test processes also start at below-normal priority, so an editor, a running game and
the agents' own tools stay responsive while the suites run.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl
    import resource

SLOTS_VARIABLE = "GODOT_TEST_SLOTS"
SLOT_DIR_VARIABLE = "GODOT_TEST_SLOT_DIR"
# Tests get a quarter of the cores. The rest stay free for the editor, the
# game under test and whatever else the other agents are running.
CORE_FRACTION = 4
MAX_DEFAULT_SLOTS = 8
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_SUSPENDED = 0x00000004
POSIX_NICE = 10


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


def slot_directory(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get(SLOT_DIR_VARIABLE) or Path(tempfile.gettempdir()) / "godot_test_slots")


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
    """`count` lock-file slots under `directory`; count 0 never limits."""

    def __init__(self, count: int, directory: Path) -> None:
        self.count = count
        self.directory = directory
        if count:
            directory.mkdir(parents=True, exist_ok=True)

    def try_acquire(self) -> Slot | None:
        """A free slot, or None when all are held. Never blocks."""
        if not self.count:
            return Slot(-1, _NullHandle())
        for index in range(self.count):
            handle = (self.directory / f"slot_{index}.lock").open("a+b")
            if _try_lock(handle):
                return Slot(index, handle)
            handle.close()
        return None

    def held(self) -> int:
        """How many slots are taken right now, by anyone (for status lines)."""
        taken = 0
        for index in range(self.count):
            handle = (self.directory / f"slot_{index}.lock").open("a+b")
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


def _try_lock(handle: IO[bytes]) -> bool:
    try:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if isinstance(handle, _NullHandle):
        return
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ProcessGroup:
    """A child and everything it spawns, counted and killed as one.

    On Windows the Godot launcher (`..._console.exe`) starts the real engine as
    its own child, so a plain Popen handle sees neither the CPU nor the process
    to kill. A job object covers the tree; elsewhere the child is the process.
    Use `launch`: it starts the child suspended, puts it in the job, and only then
    lets it run, so it cannot spawn a child that escapes the job.

    The job is set to kill its processes when its last handle closes. This process
    holds the only handle, so if the test runner itself is killed (Ctrl+C, an agent's
    timeout), Windows ends the Godot processes it started instead of orphaning them.
    """

    def __init__(self, process: subprocess.Popen[bytes], job: Any = None) -> None:
        self.process = process
        self._job: Any = job

    @classmethod
    def launch(
        cls, command: list[str], low_priority: bool = False, **popen_options: Any
    ) -> ProcessGroup:
        """Start `command` (Popen keyword arguments in `popen_options`) as a group."""
        if os.name != "nt":
            if low_priority:
                popen_options["preexec_fn"] = lambda: os.nice(POSIX_NICE)
            return cls(subprocess.Popen(command, **popen_options))
        flags = popen_options.pop("creationflags", 0) | CREATE_SUSPENDED
        if low_priority:
            flags |= BELOW_NORMAL_PRIORITY_CLASS
        process = subprocess.Popen(command, creationflags=flags, **popen_options)
        try:
            return cls(process, _job_for_suspended(process.pid))
        except BaseException:
            process.kill()  # never leave it suspended
            raise

    def cpu_seconds(self) -> float | None:
        """CPU seconds of the whole tree so far (Windows; None elsewhere)."""
        if self._job is None:
            return None
        info = _JobAccounting()
        ok = _KERNEL32.QueryInformationJobObject(
            self._job, JOB_BASIC_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
        )
        if not ok:
            return None
        return (info.TotalUserTime + info.TotalKernelTime) / 1e7  # 100 ns ticks

    def kill(self) -> None:
        if self._job is not None:
            _KERNEL32.TerminateJobObject(self._job, 1)
        self.process.kill()
        self.process.wait()

    def close(self) -> None:
        if self._job is not None:
            _KERNEL32.CloseHandle(self._job)
            self._job = None


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
    """CPU seconds used so far by every reaped child of this process (POSIX only)."""
    if os.name == "nt":
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime
