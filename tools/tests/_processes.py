"""Is a process still there? For the tests that kill process trees. Not a test module."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def alive(pid: int) -> bool:
    """True while `pid` runs. On Linux a process that has ended and that nobody has
    waited for yet (state Z) counts as gone: it runs nothing and holds nothing."""
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        )
        return str(pid) in listing.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False  # the number is another user's process now
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        # No /proc (macOS): the signal was accepted. With one, the process was waited
        # for between the signal and this read: gone.
        return not os.path.isdir("/proc/self")
    # "<pid> (<name>) <state> ...": the name may hold blanks and brackets.
    return stat.rpartition(")")[2].split()[:1] not in (["Z"], ["X"])


def wait_gone(pid: int, seconds: float = 20.0) -> bool:
    """True once `pid` is gone; False when it still runs after `seconds`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False
