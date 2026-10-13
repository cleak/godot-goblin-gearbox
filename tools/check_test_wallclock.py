"""Flags tests that wait on the wall clock.

The bug: a test that sleeps, awaits a timer or reads the time to decide when something
is done passes on a quiet machine and fails on a busy one. With several test runs
sharing the CPU, "long enough" is not long enough, and the failure cannot be reproduced.
It is also slow on purpose. Wait for the thing itself: a signal, a finished flag, a
frame or tick count, a clock the test controls.

Flagged in every .gd under the test folder (kit.json `tests.root`):
`OS.delay_msec/usec`, `Time.get_ticks_msec/usec`, `Time.get_unix_time*`, `create_timer(`,
and gdUnit4's own sleep, `await_millis(`.

Not flagged: a unique-name stamp, where the time is only a number that differs per run
(`"user://save_%d" % Time.get_ticks_usec()`, `var stamp := Time.get_ticks_usec()`).
Not seen: a `Timer` node the test builds and waits on.

Exceptions: end the line with `# wallclock-ok: reason`, or list the file (or a folder
ending in `/`) in tools/ci/test_wallclock_allowlist.txt (optional; `path | reason` per
line, see tools/ci/gate_support.py). An entry with nothing left to flag fails: remove it.

A second rule covers all authored .gd, tests or not, and has no exception: a summed
sleep. A loop that bounds its wait by adding up what it slept

    while busy() and waited < 500:
        OS.delay_msec(10)
        waited += 10

undercounts real time: every sleep runs long, and the work between sleeps is not
counted, so "500 ms" can take seconds on a loaded machine. Compare against a deadline
(`Time.get_ticks_msec() >= deadline`) or count frames or ticks.

    uv run python tools/check_test_wallclock.py                # the whole project
    uv run python tools/check_test_wallclock.py test/net       # these files or folders

Exit 0 clean; 1 on a finding, a stale allowlist entry, or no test script to scan; 2 on
a bad path, allowlist or kit.json.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))
import gate_support  # noqa: E402
import kit_config  # noqa: E402
import project_refs  # noqa: E402

ALLOWLIST = "tools/ci/test_wallclock_allowlist.txt"
WAIT = re.compile(
    r"OS\.delay_(?:msec|usec)\s*\(|Time\.get_ticks_(?:msec|usec)\s*\(|"
    r"Time\.get_unix_time\w*\s*\(|create_timer\s*\(|\bawait_millis\s*\("
)
STAMP = re.compile(
    r"^(?:(?!Time\.get|OS\.delay|create_timer).)*%\s*\[?"
    r"(?:(?:(?!Time\.get|OS\.delay|create_timer)[^\]])*,\s*)?"
    r"Time\.get_ticks_usec\(\)\s*\]?[\s)]*$"
    r"|^\s*(?:var|const)\s+(?:stamp|tag)\b[^=]*:?=\s*Time\.get_ticks_usec\(\)\s*$"
)
OK_MARK = re.compile(r"#\s*wallclock-ok:\s*\S")
# The sleep's amount: one level of nested calls (`OS.delay_msec(maxi(a, b))`).
DELAY = re.compile(r"OS\.delay_(?:msec|usec)\s*\(\s*((?:[^()]|\([^()]*\))+?)\s*\)")
SUM = re.compile(r"\b(\w+)\s*\+=\s*([^#]+?)\s*$")
SLEEP_NAME = re.compile(r"^(?:waited|slept|elapsed|spent|wait|sleep)", re.IGNORECASE)
SUM_WINDOW = 3


def _code_lines(text: str) -> list[str]:
    """The script line for line with comments removed and strings emptied: a `#` in a
    string starts no comment, and a call named in a string is not a call."""
    return project_refs.code_only(text).split("\n")


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, line) of each flagged wall-clock use."""
    hits: list[tuple[int, str]] = []
    lines = zip(text.split("\n"), _code_lines(text), strict=True)
    for number, (line, code) in enumerate(lines, 1):
        if not WAIT.search(code) or OK_MARK.search(line) or STAMP.search(code):
            continue
        hits.append((number, line.strip()))
    return hits


def summed_sleeps(text: str) -> list[tuple[int, str]]:
    """(line number, line) of each `+=` that adds up a nearby sleep: the same amount as an
    `OS.delay_*` within SUM_WINDOW lines, or into a wait-named counter (`waited += ...`)."""
    raw = text.split("\n")
    lines = _code_lines(text)
    delays: list[tuple[int, str]] = []
    for number, code in enumerate(lines, 1):
        delays += [(number, m.group(1)) for m in DELAY.finditer(code)]
    hits: list[tuple[int, str]] = []
    for number, code in enumerate(lines, 1):
        found = SUM.search(code)
        if found is None:
            continue
        name, amount = found.group(1), found.group(2)
        near = [arg for at, arg in delays if abs(at - number) <= SUM_WINDOW]
        if near and (amount in near or SLEEP_NAME.match(name)):
            hits.append((number, raw[number - 1].strip()))
    return hits


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_files(config: kit_config.KitConfig, paths: list[str] | None = None) -> list[Path]:
    """Every .gd under the test folder, narrowed to `paths` when given."""
    if not config.test_dir.is_dir():
        return []
    tests = kit_config.source_files((".gd",), path=config.test_dir, config=config)
    if not paths:
        return tests
    chosen = set(gate_support.gather(config, paths))
    return [path for path in tests if path in chosen]


def scan(files: list[Path], config: kit_config.KitConfig, allowlist: dict[str, str]) -> list[str]:
    """One `path:line: message` per wall-clock wait in the test scripts `files`."""
    problems: list[str] = []
    for path in files:
        rel = gate_support.relative(path, config)
        if gate_support.allowed(rel, allowlist):
            continue
        for number, line in scan_text(_read(path)):
            problems.append(f"{rel}:{number}: wall-clock wait: {line}")
    return problems


def scan_summed(files: list[Path], config: kit_config.KitConfig) -> list[str]:
    """One `path:line: message` per summed sleep in `files`."""
    problems: list[str] = []
    for path in files:
        rel = gate_support.relative(path, config)
        for number, line in summed_sleeps(_read(path)):
            problems.append(f"{rel}:{number}: summed sleep (use a deadline): {line}")
    return problems


def stale_entries(config: kit_config.KitConfig, allowlist: dict[str, str]) -> list[str]:
    """Allowlist entries that cover no file with a wall-clock use left (remove them)."""
    return [
        key
        for key in allowlist
        if not any(scan_text(_read(path)) for path in gate_support.entry_files(key, config))
    ]


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flag tests that wait on the wall clock.")
    parser.add_argument("paths", nargs="*", help="files or folders (default: the whole project)")
    args = parser.parse_args(argv)
    gate_support.plain_output()
    try:
        config = kit_config.load(root)
        tests = test_files(config, args.paths)
        everything = set(gate_support.gather(config, args.paths))
        allowlist = gate_support.load_allowlist(config.root / ALLOWLIST)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if not tests and not args.paths:
        print(
            f"wall clock: no .gd file under the test folder '{config.test_root}' "
            "(kit.json tests.root)",
            file=sys.stderr,
        )
        return 1
    if not tests and not everything:
        print("wall clock: no .gd file to scan", file=sys.stderr)
        return 1
    problems = scan(tests, config, allowlist)
    problems += scan_summed(sorted(everything | set(tests)), config)
    # A narrowed run cannot tell whether an entry elsewhere is stale.
    stale = [] if args.paths else stale_entries(config, allowlist)
    for line in problems:
        print(line)
    for key in stale:
        print(f"{ALLOWLIST}: stale entry (no wall-clock use left), remove it: {key}")
    if problems:
        print(
            f"{len(problems)} finding(s). The fix is at the top of tools/check_test_wallclock.py."
        )
    else:
        state = "FAIL" if stale else "ok"
        print(f"wall clock: {state} ({len(tests)} test scripts, {len(everything)} scripts in all)")
    return 1 if problems or stale else 0


if __name__ == "__main__":
    sys.exit(main())
