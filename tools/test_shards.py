"""Run the gdUnit4 suites in parallel Godot processes (shards).

One gdUnit4 process runs suites one after another on one core. This splits them across
N processes and gives every process its own user data folder, so `user://` files never
collide. Nothing needs configuring: a fresh project just runs.

    uv run python tools/test_shards.py                   # suites your change can affect
    uv run python tools/test_shards.py --changed develop # the same, against another branch
    uv run python tools/test_shards.py res://test/unit/test_grid.gd res://test/net
    uv run python tools/test_shards.py --dev             # every suite but the heavy ones
    uv run python tools/test_shards.py --all             # every suite
    uv run python tools/test_shards.py --all --skip-slow # every suite not on the slow list
    uv run python tools/test_shards.py --all --write-timings

- No arguments is the cheapest mode, `--changed`: the files that differ from the base
  branch (committed or not) select suites through a reference graph
  (tools/ci/select_suites.py). The base is the remote's default branch, else origin/main,
  main, origin/master, master. Outside a git repository, or with no base branch, it says
  so and runs every suite. On the base branch itself (commits on a main that has no
  remote copy) only uncommitted files count, and a note says so.
- Paths (files or folders) run exactly those suites. `res://test/unit`, `test/unit` and
  `test\\unit` are the same folder on every system; an absolute path is read as this
  system writes it.
- "Heavy" is a suite recorded at `--heavy-seconds` or more, or on the slow list.

What a suite is (tools/ci/suites.py): a .gd file whose bases reach GdUnitTestSuite, by
any header gdUnit4 accepts (`extends GdUnitTestSuite`, `class_name X extends ...`,
`extends "res://..."`, `extends MyBaseSuite`). A base suite with no test function, which
other suites extend, is not run by itself. A script under the test folder that has a
`func test_...` and is not a suite stops the run: those tests would never run.

Optional data files, all under tools/ci/ (a missing file means "none"):

- test_timings.json: seconds per suite, written by `--write-timings` (with no value;
  give a file to write elsewhere). Shards are balanced by it (longest suite first onto
  the least loaded shard), and a suite longer than a shard's fair share is split by test
  case across shards. Commit it.
- slow_suites.txt: one res:// suite per line, `#` starts a comment (say why it is slow
  there). `--skip-slow` leaves these out; `--dev` and `--changed` do too, unless the
  suite file itself changed.
- always_run_suites.txt: same format. These join every `--changed` run that touches
  source, however far the reference graph puts them (guards for project-wide rules).

Several runs share one machine politely: every shard takes a slot from a machine-wide
pool (tools/ci/slot_pool.py; GODOT_TEST_SLOTS, default a quarter of the cores) and
starts at below-normal priority. Shards beyond the free slots wait their turn. With
CI=true every core is a slot.

Before the first shard starts, a missing or stale class cache is rebuilt
(tools/ci/class_cache.py). The Godot binary is found by tools/ci/godot_binary.py.

Every test case runs: a failure does not stop the cases after it. The run ends with
one line per failure (file, line, case, what was expected). A shard that broke some
other way (a script error, a crash, a timeout) has its log printed, each Godot error
once and without its GDScript backtrace; `--verbose` prints the whole log of every
failed shard. gdUnit4's HTML and XML reports go to
artifacts/gdunit-shards/run_<pid>/ and are deleted with the shard logs unless `--keep`
is given.

Exit 0 when every shard passed and every suite reported. 1 on a test failure, a suite
that never reported, a script error in the output (gdUnit4 exits 0 on a suite that
fails to parse, so the output is scanned), a test script that is not a suite, or a list
file that names a missing suite. 124 on a timeout. 2 on a usage or setup error (bad
argument or path, unreadable kit.json or timings file, no Godot, no gdUnit4).

Stopping a run (Ctrl+C, SIGTERM, SIGHUP, SIGQUIT) kills its shards, frees its slots and
removes its folders; the exit code is 128 + the signal's number (130, 143, 129, 131), and
a signal that comes during that cleanup is ignored. Ctrl+C ends with one line.
A run that is killed outright cleans up nothing, but its Godot processes still end with
it (tools/ci/slot_pool.py).

A suite must end what it starts. A process a shard left running: Linux ends it with the
shard and names it in a line of its own; on Windows the shard does not end while it
runs, so it times out; macOS leaves one that OS.create_process started.

File names that are not ASCII need Python to read names as UTF-8, which it does unless
its UTF-8 mode is switched off under a locale that is not UTF-8; then a suite with such
a name is a setup error.

The Godot binary comes from GODOT_BIN or the resolver, never from an argument, and the
data files (`--timings`, `--slow-list`, `--always-list`, `--write-timings`) must lie
inside the project: the command line cannot make this tool run another program or
write another project's file. A relative data file starts at the project root, like a
test path, whatever the working folder. `--write-timings` replaces only a timings file.
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import class_cache  # noqa: E402
import gate_support  # noqa: E402
import godot_binary  # noqa: E402
import kit_config  # noqa: E402
import select_suites  # noqa: E402
import slot_pool  # noqa: E402
import suites as suite_rules  # noqa: E402

# No function here is named test_*, no class Test*: pytest takes this file for a test
# module by its name and runs what it finds (wherever tools/tests is gone, a bare `pytest`
# searches the project).

# Defaults until main() reads kit.json: an unreadable kit.json must be a message and
# exit 2 there, not a traceback at import.
CONFIG = kit_config.KitConfig(root=kit_config.REPO)
TIMINGS = "tools/ci/test_timings.json"
SLOW_SUITES = "tools/ci/slow_suites.txt"
ALWAYS_RUN_SUITES = "tools/ci/always_run_suites.txt"
ARTIFACTS = "artifacts"
REPORTS = f"{ARTIFACTS}/gdunit-shards"
# Floor, in seconds, for a suite with no recorded timing. The assumed value is
# pessimistic (see default_seconds): an unmeasured suite may be heavy, and
# underestimating it stacks it with other heavy suites into a shard timeout.
DEFAULT_SECONDS = 2.0
UNMEASURED_PERCENTILE = 0.9
# A shard should have at least this much work, or its process start-up dominates.
MIN_SHARD_SECONDS = 8.0
# Case timings are kept only for suites this long: the ones that may split.
CASE_TIMINGS_FROM = 5.0
# gdUnit4 exits 0 after these, so the output has to be read.
FATAL_OUTPUT = re.compile(r"No test cases found|SCRIPT ERROR|Parse Error|Failed to load script")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
GDUNIT_TOOL = "res://addons/gdUnit4/bin/GdUnitCmdTool.gd"
# gdUnit4's exit codes: 0 pass, 100 failures, 101 passed with warnings (orphan nodes).
GDUNIT_PASS = (0, 101)
GDUNIT_FAILURES = 100
FAILURE_DETAIL_CHARS = 300
EXIT_TIMEOUT = 124
# The variable that moves Godot's user data folder, where it is not XDG_DATA_HOME (Linux).
USER_DATA_VARIABLE = {"windows": "APPDATA", "macos": "HOME"}
# Signals that end a run; the exit code is 128 + the signal's number. SIGINT is Ctrl+C.
STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
INTERRUPTED = "Stopped by Ctrl+C."


@dataclass
class Item:
    """A suite, or one part of a split suite (it skips every case in `skip`)."""

    suite: str
    estimate: float
    skip: list[str] = field(default_factory=list)


@dataclass
class Shard:
    index: int
    items: list[Item] = field(default_factory=list)
    estimate: float = 0.0


@dataclass
class SuiteResult:
    path: str
    tests: int
    failures: int
    errors: int
    seconds: float
    # Seconds per test function (parameterized cases summed under it).
    cases: dict[str, float] = field(default_factory=dict)
    # "test name: first line of the message" per failed or errored case.
    failed: list[str] = field(default_factory=list)


def local_path(suite: str) -> Path:
    return CONFIG.root / suite.removeprefix("res://")


def typed_path(text: str) -> Path:
    """Where a path from the command line points: `res://test/unit`, `test/unit`,
    `test\\unit` and `test/unit/` are one folder of the project on every system; an
    absolute path is read as this system writes it."""
    if text.startswith("res://"):
        text = text.removeprefix("res://").strip("/") or "."
    return kit_config.project_path(text or ".", CONFIG.root)


def shown(path: str | Path) -> str:
    """A path for a message: project-relative when it is inside the project."""
    full = Path(path).resolve()
    return full.relative_to(CONFIG.root).as_posix() if CONFIG.root in full.parents else str(path)


@dataclass
class Found:
    """What a search for suites turned up."""

    # res:// paths gdUnit4 will run, sorted.
    suites: list[str] = field(default_factory=list)
    # Suite -> the res:// path gdUnit4 reports it under (its own, with rare exceptions).
    reports: dict[str, str] = field(default_factory=dict)
    # One line per script in the test folder that has test functions and is no suite.
    not_suites: list[str] = field(default_factory=list)


def find_suites(roots: list[str]) -> Found:
    """The gdUnit4 suites under the given files and folders: `res://test/unit`,
    `test/unit` and `test/unit/` are one path, as for tools/test.ps1. Folders Godot does
    not scan (dot-folders, .gdignore, nested projects) are skipped. Raises ValueError
    for a path that does not exist or lies outside the project, and for a suite whose
    name this Python holds escaped (see `names_are_utf8`)."""
    files: set[Path] = set()
    for root in roots:
        files.update(kit_config.source_files((".gd",), path=typed_path(root), config=CONFIG))
    # Who extends whom is read over the whole test folder: a base suite and the suites
    # built on it need not both be under the paths asked for.
    known = set(files)
    if CONFIG.test_dir.is_dir():
        known.update(kit_config.source_files((".gd",), path=CONFIG.test_dir, config=CONFIG))
    index = suite_rules.SuiteIndex(CONFIG)
    script_of = {path: path.relative_to(CONFIG.root).as_posix() for path in known}
    # The rule itself lives in tools/ci/suites.py, which tools/lint_tests.ps1 asks too.
    kinds = index.kinds((script_of[path] for path in files), script_of.values())
    found = Found()
    for path in sorted(files):
        script = script_of[path]
        if kinds[script] == suite_rules.SUITE:
            found.suites.append(f"res://{script}")
            found.reports[f"res://{script}"] = f"res://{index.reported_as(script)}"
        elif kinds[script] == suite_rules.NOT_A_SUITE and CONFIG.test_dir in path.parents:
            found.not_suites.append(f"{script} ({index.describe_base(script)})")
    escaped = [] if names_are_utf8() else [suite for suite in found.suites if not suite.isascii()]
    if escaped:
        raise ValueError(
            f"{escaped[0]}: a file name that is not ASCII needs Python to read names as "
            f"UTF-8, and here it reads them as {sys.getfilesystemencoding()}. Fix: unset "
            "PYTHONUTF8 and PYTHONCOERCECLOCALE, or use a UTF-8 locale (LC_ALL=C.UTF-8)"
        )
    return found


def names_are_utf8() -> bool:
    """False when Python's UTF-8 mode is off under a locale that is not UTF-8. A file
    name that is not ASCII is then held as escaped bytes: not the name gdUnit4 reports,
    a list file spells or git prints, so its suite would count as never reported."""
    return codecs.lookup(sys.getfilesystemencoding()).name == "utf-8"


def discover(roots: list[str]) -> list[str]:
    """Every runnable gdUnit4 suite under `roots`, as sorted res:// paths."""
    return find_suites(roots).suites


def _timings_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data


def load_timings(path: Path) -> dict[str, float]:
    return {str(key): float(value) for key, value in _timings_file(path).get("suites", {}).items()}


def load_case_timings(path: Path) -> dict[str, dict[str, float]]:
    return {
        str(suite): {str(name): float(value) for name, value in cases.items()}
        for suite, cases in _timings_file(path).get("cases", {}).items()
    }


def load_suite_list(path: Path) -> list[str]:
    """The res:// suites a list file names, one per line; text after # is a comment.
    A missing file is an empty list."""
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = (line.split("#", 1)[0].strip() for line in lines)
    return [entry for entry in entries if entry]


def missing_suites(listed: list[str]) -> list[str]:
    """Listed suites that are not files: a list that rots must fail, not shrink quietly."""
    return [suite for suite in listed if not local_path(suite).is_file()]


def case_names(suite: str) -> list[str]:
    """The test functions a suite can be split by: its own. None when it also runs
    inherited ones: gdUnit4 reports those under the base's name, so leaving one out by
    name (`-i`) is not relied on and such a suite stays whole."""
    index = suite_rules.SuiteIndex(CONFIG)
    script = suite.removeprefix("res://")
    return [] if index.inherits_tests(script) else index.own_tests(script)


def split(
    suite: str,
    seconds: float,
    share: float,
    names: list[str],
    case_seconds: dict[str, float] | None = None,
) -> list[Item]:
    """Cut a suite longer than `share` into parts of whole test cases. With
    measured case seconds the parts are balanced by them (longest case first
    into the lightest part); without, cases are dealt out evenly."""
    parts = min(len(names), max(1, math.ceil(seconds / share)))
    if parts <= 1:
        return [Item(suite, seconds)]
    known = case_seconds or {}
    measured = sum(known.get(name, 0.0) for name in names)
    # Unmeasured cases (new ones) get the suite's average; the suite's own
    # setup time is spread in proportion.
    average = seconds / len(names)
    cost = {name: known.get(name, average) if measured > 0 else average for name in names}
    scale = seconds / sum(cost.values())
    groups: list[list[str]] = [[] for _ in range(parts)]
    loads = [0.0] * parts
    for name in sorted(names, key=lambda name: (-cost[name], name)):
        lightest = loads.index(min(loads))
        groups[lightest].append(name)
        loads[lightest] += cost[name]
    return [
        Item(suite, load * scale, [name for name in names if name not in group])
        for group, load in zip(groups, loads, strict=True)
    ]


def default_seconds(timings: dict[str, float]) -> float:
    """Seconds assumed for an unmeasured suite: the p90 of the measured ones, at
    least DEFAULT_SECONDS, so new suites spread across shards like heavy ones."""
    values = sorted(timings.values())
    if not values:
        return DEFAULT_SECONDS
    p90 = values[min(len(values) - 1, int(len(values) * UNMEASURED_PERCENTILE))]
    return max(DEFAULT_SECONDS, p90)


def auto_shard_count(suites: list[str], timings: dict[str, float], limit: int) -> int:
    """Processes for a run: enough work each to beat start-up, at most `limit`."""
    fallback = default_seconds(timings)
    serial = sum(timings.get(suite, fallback) for suite in suites)
    return max(1, min(limit, round(serial / MIN_SHARD_SECONDS)))


def plan(
    suites: list[str],
    timings: dict[str, float],
    shard_count: int,
    case_timings: dict[str, dict[str, float]] | None = None,
) -> list[Shard]:
    """Split suites above a fair share, then place the longest first on the least
    loaded shard (longest-processing-time-first)."""
    count = max(1, shard_count)
    cases = case_timings or {}
    fallback = default_seconds(timings)
    seconds = {suite: timings.get(suite, fallback) for suite in suites}
    share = max(sum(seconds.values()) / count, DEFAULT_SECONDS)
    items: list[Item] = []
    for suite in suites:
        if seconds[suite] > share:
            items += split(suite, seconds[suite], share, case_names(suite), cases.get(suite))
        else:
            items.append(Item(suite, seconds[suite]))
    shards = [Shard(index) for index in range(min(count, len(items)))]
    for item in sorted(items, key=lambda item: (-item.estimate, item.suite, item.skip)):
        # Two parts of one suite must not share a shard: gdUnit4 runs a suite
        # once per process, skipping the union of both parts' cases. A suite
        # has at most `count` parts (its seconds are at most count shares), so
        # a free shard always exists.
        free = [shard for shard in shards if all(i.suite != item.suite for i in shard.items)]
        target = min(free, key=lambda shard: (shard.estimate, shard.index))
        target.items.append(item)
        target.estimate += item.estimate
    return shards


def shard_failure(code: int, output: str) -> str | None:
    """Why a finished shard failed, or None when it passed."""
    fatal = FATAL_OUTPUT.search(output)
    if fatal:
        return f"'{fatal.group(0)}' in output"
    if code not in GDUNIT_PASS:
        return f"exit {code}"
    return None


BACKTRACE = re.compile(r"^\s*GDScript backtrace")
BACKTRACE_FRAME = re.compile(r"^\s*\[\d+\]\s")
GODOT_ERROR = re.compile(r"^\s*(SCRIPT )?ERROR:")
ERROR_PLACE = re.compile(r"^\s+at:")


def without_repeats(output: str) -> str:
    """A failed shard's log without what only repeats: Godot prints a GDScript backtrace
    under every script error, and a script that does not compile is reported again by
    everything that loads it. Each error (its line and the "at:" line under it) is kept
    once. tools/check.ps1 Get-GateFindings shows a failed gate the same way."""
    kept: list[str] = []
    seen: set[tuple[str, str]] = set()
    lines = output.splitlines()
    in_backtrace = False
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if BACKTRACE.match(line):
            in_backtrace = True
            continue
        if in_backtrace and BACKTRACE_FRAME.match(line):
            continue
        in_backtrace = False
        if GODOT_ERROR.match(line) and index < len(lines) and ERROR_PLACE.match(lines[index]):
            error = (line.strip(), lines[index].strip())
            if error in seen:
                index += 1
                continue
            seen.add(error)
        kept.append(line)
    return "\n".join(kept)


def failure_line(suite: str, case: str, problem: ET.Element) -> str:
    """One line for a failed case: where, which case, and what gdUnit4 expected.
    gdUnit4 writes `message="FAILED: res://path.gd:70"` and the expectation as text.
    Its lines are joined; the spaces inside one are kept, since they may be part of a
    string the test compared."""
    where = ANSI.sub("", problem.get("message") or "").removeprefix("FAILED:").strip()
    lines = (line.strip() for line in ANSI.sub("", problem.text or "").splitlines())
    detail = " ".join(line for line in lines if line)
    if len(detail) > FAILURE_DETAIL_CHARS:
        detail = detail[: FAILURE_DETAIL_CHARS - 3] + "..."
    if not where.startswith("res://"):
        where, detail = suite, f"{where} {detail}".strip()
    return f"{where}  {case}: {detail}".rstrip(": ")


def parse_results(report_dir: Path) -> list[SuiteResult]:
    """Suite results from every gdUnit4 JUnit results.xml under report_dir."""
    results: list[SuiteResult] = []
    for xml_path in sorted(report_dir.rglob("results.xml")):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as error:
            # A killed shard can leave half a file. Its suites then count as never reported.
            print(f"unreadable gdUnit4 report {xml_path}: {error}", file=sys.stderr)
            continue
        for suite in root.iter("testsuite"):
            # package is the suite's folder relative to res:// ("test/unit"),
            # name its file stem.
            package = suite.get("package", "").removeprefix("res://").strip("/")
            path = f"res://{package}/{suite.get('name')}.gd".replace("res:///", "res://")
            cases: dict[str, float] = {}
            failed: list[str] = []
            for case in suite.iter("testcase"):
                # A parameterized case is named "<function>:<parameters>".
                name = re.split(r"[:\s(]", case.get("name", ""), maxsplit=1)[0]
                cases[name] = cases.get(name, 0.0) + float(case.get("time", "0"))
                for problem in [*case.iter("failure"), *case.iter("error")]:
                    failed.append(failure_line(path, case.get("name", name), problem))
            results.append(
                SuiteResult(
                    path=path,
                    tests=int(suite.get("tests", "0")),
                    failures=int(suite.get("failures", "0")),
                    errors=int(suite.get("errors", "0")),
                    seconds=float(suite.get("time", "0")),
                    cases=cases,
                    failed=failed,
                )
            )
    return results


def shard_env(
    user_dir: Path, platform: str | None = None, env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """`env` (default: this process's) for one shard on `platform` (default: this one),
    with the folder Godot keeps user:// under moved to `user_dir`. Godot 4.7 reads
    %APPDATA% on Windows, $XDG_DATA_HOME on Linux and, on macOS, $HOME alone: there the
    shard, and whatever a test starts from it, sees `user_dir` as the home folder.

    `user_dir` must be an absolute path: Godot ignores a relative XDG_DATA_HOME and would
    use the developer's folder. tools/godot_resolver.ps1 Get-KitUserDataEnvironment is the
    same rule for tools/test.ps1 (tools/tests/test_resolver_parity.py compares the two)."""
    if not Path(user_dir).is_absolute():
        raise ValueError(f"shard_env: user_dir must be an absolute path; it is '{user_dir}'.")
    shard = dict(os.environ if env is None else env)
    variable = USER_DATA_VARIABLE.get(platform or kit_config.host_platform(), "XDG_DATA_HOME")
    shard[variable] = str(user_dir)
    return shard


def reports_dir() -> Path:
    """This run's own report folder: two runs in one checkout must not read, or delete,
    each other's reports."""
    return CONFIG.root / REPORTS / f"run_{os.getpid()}"


def report_dir(index: int) -> Path:
    return reports_dir() / f"shard_{index}"


def prepare_reports() -> None:
    """An empty report folder (a stale report must never count as this run's result),
    under an artifacts/ that Godot does not scan (gdUnit4 writes HTML and images)."""
    shutil.rmtree(reports_dir(), ignore_errors=True)
    gate_support.ensure_gdignore(CONFIG.root / ARTIFACTS)
    reports_dir().mkdir(parents=True, exist_ok=True)


def discard_reports() -> None:
    shutil.rmtree(reports_dir(), ignore_errors=True)
    with contextlib.suppress(OSError):
        (CONFIG.root / REPORTS).rmdir()  # only when no other run has a folder in it


def shard_expired(launched: float, now: float, timeout: float) -> bool:
    """True once a shard has run longer than `timeout`; `launched` is when it got its slot."""
    return now - launched > timeout


def shard_command(godot: str, shard: Shard) -> list[str]:
    # -c: run every case. gdUnit4 otherwise stops the whole process at the first failed
    # case, and the suites behind it would be reported as "never reported".
    command = [godot, "--headless", "-s", GDUNIT_TOOL, "--ignoreHeadlessMode", "-c"]
    # gdUnit4 resolves -rd against the project on Linux even when absolute, so
    # pass it project-relative (under the ignored artifacts/).
    command += ["-rd", report_dir(shard.index).relative_to(CONFIG.root).as_posix()]
    for item in shard.items:
        command += ["-a", item.suite]
        for name in item.skip:
            command += ["-i", f"{item.suite}:{name}"]
    return command


def start_shard(
    godot: str, shard: Shard, work: Path, low_priority: bool = False
) -> tuple[slot_pool.ProcessGroup, Path]:
    user_dir = work / f"user_{shard.index}"
    user_dir.mkdir(parents=True, exist_ok=True)
    log_path = work / f"shard_{shard.index}.log"
    with log_path.open("wb") as log:
        group = slot_pool.ProcessGroup.launch(
            shard_command(godot, shard),
            low_priority=low_priority,
            cwd=CONFIG.root,
            env=shard_env(user_dir),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return group, log_path


def run_everything(args: argparse.Namespace, reason: str) -> None:
    print(f"warning: {reason}; running every suite", file=sys.stderr)
    args.changed = None
    args.all = True


def select_changed(
    args: argparse.Namespace, heavy: set[str], always: list[str]
) -> select_suites.Selection | None:
    """The suites the working tree's changes select, or None after switching the run
    to "every suite" because git cannot say what changed."""
    changes = select_suites.working_tree_changes(CONFIG.root, args.changed or None)
    if changes.files is None:
        run_everything(args, changes.why)
        return None
    if changes.note:
        print(f"note: {changes.note}. --all runs every suite.")
    return select_suites.select(
        CONFIG.root, changes.files, heavy, base_rev=changes.revision, always=always
    )


def choose_suites(args: argparse.Namespace, suites: list[str]) -> list[str] | None:
    """Narrow the discovered suites by --skip-slow, --dev and --changed.
    None means the run is already decided (an error code in args.exit)."""
    timings = load_timings(Path(args.timings))
    slow = load_suite_list(Path(args.slow_list))
    always = load_suite_list(Path(args.always_list)) if args.changed is not None else []
    for label, listed in ((args.slow_list, slow), (args.always_list, always)):
        stale = missing_suites(listed)
        if stale:
            print(f"{shown(label)} lists missing suites: {', '.join(stale)}", file=sys.stderr)
            args.exit = 1
            return None
    if args.skip_slow:
        suites = [suite for suite in suites if suite not in slow]
        print(f"Skipping {len(slow)} slow suite(s) listed in {shown(args.slow_list)}")
    heavy = {suite for suite in suites if timings.get(suite, 0.0) >= args.heavy_seconds}
    heavy.update(slow)
    selection = select_changed(args, heavy, always) if args.changed is not None else None
    if selection is not None and selection.widened_by:
        print(f"{selection.widened_by} changed: running every suite that is not heavy")
        args.dev = True
    elif selection is not None:
        # A slow suite runs here only when its own file changed (distance 0) or it is
        # on the always-run list; name it by path to run it otherwise.
        suites = [
            suite
            for suite in suites
            if suite in selection.suites and (suite not in slow or selection.suites[suite] == 0)
        ]
        near = sum(1 for suite in suites if selection.suites[suite] <= 1)
        print(
            f"{len(selection.changed)} changed file(s) select {len(suites)} suite(s) "
            f"({near} direct, {len(suites) - near} one step away). "
            f"{len(selection.ignored)} changed file(s) are named by no script or scene. "
            "--all runs every suite."
        )
    if args.dev:
        # After a widening change the always-run suites still run, heavy or not.
        heavy -= set(always)
        skipped = [suite for suite in suites if suite in heavy]
        suites = [suite for suite in suites if suite not in heavy]
        print(
            f"Skipping {len(skipped)} heavy suite(s) (>= {args.heavy_seconds:g}s or on the "
            f"slow list; {sum(timings.get(suite, 0.0) for suite in skipped):.0f}s serial). "
            "--all runs them."
        )
    return suites


def resolve_mode(args: argparse.Namespace) -> None:
    """Default to the cheapest mode so nobody runs the whole suite by accident:
    --changed, unless --all/--dev/--skip-slow or explicit paths say otherwise.
    `args.changed` is None (off), "" (on, base branch found by git) or a branch name."""
    if args.all or args.dev or args.changed is not None:
        return
    if args.skip_slow:
        args.all = True
    elif not args.paths:
        args.changed = ""


def default_roots() -> list[str]:
    if not CONFIG.test_dir.is_dir():
        raise ValueError(
            f"no test folder: {CONFIG.test_dir} (kit.json tests.root is '{CONFIG.test_root}')"
        )
    return [f"res://{CONFIG.test_root}"]


@dataclass
class Job:
    """A run that is ready to start: everything `prepare` decided."""

    suites: list[str]
    shards: list[Shard]
    godot: str
    slot_count: int
    # Suite -> the res:// path gdUnit4 reports it under (see suites.reported_as).
    reports: dict[str, str]
    pool: slot_pool.SlotPool


def prepare(args: argparse.Namespace) -> Job | int:
    """Decide what runs and make the project ready for it. An int is the exit code of a
    run that is already over (nothing to run, or a finding that fails it). Raises
    FileNotFoundError, ValueError or RuntimeError for a usage or setup error."""
    resolve_mode(args)
    root = CONFIG.root
    args.timings = args.timings or str(root / TIMINGS)
    args.slow_list = args.slow_list or str(root / SLOW_SUITES)
    args.always_list = args.always_list or str(root / ALWAYS_RUN_SUITES)
    roots = args.paths or default_roots()
    found = find_suites(roots)
    if found.not_suites:
        print(
            f"{len(found.not_suites)} script(s) in the test folder have test functions but "
            "do not extend GdUnitTestSuite, so gdUnit4 would never run them. Extend "
            "GdUnitTestSuite (or a suite), or rename the functions:",
            file=sys.stderr,
        )
        for line in found.not_suites:
            print(f"  {line}", file=sys.stderr)
        return 1
    suites = found.suites
    if not suites:
        print(f"No gdUnit4 suites found under {', '.join(roots)}", file=sys.stderr)
        return 1
    args.exit = 0
    chosen = choose_suites(args, suites)
    if chosen is None:
        return args.exit
    suites = chosen
    if not suites:
        print("No suites to run.")
        return 0
    slot_count = slot_pool.default_slot_count() if args.slots is None else args.slots
    shard_count = args.shards or slot_count or os.cpu_count() or 4
    timings = load_timings(Path(args.timings))
    if not args.shards:
        # Every Godot process costs seconds to start: a small run needs few.
        shard_count = auto_shard_count(suites, timings, shard_count)
    shards = plan(suites, timings, shard_count, load_case_timings(Path(args.timings)))
    if not local_path(GDUNIT_TOOL).is_file():
        raise FileNotFoundError(
            f"gdUnit4 is not installed ({GDUNIT_TOOL.removeprefix('res://')} is missing). "
            "Restore it with: uv run python tools/install_gdunit4.py"
        )
    godot = godot_binary.resolve(CONFIG)
    if not args.no_import and class_cache.is_stale(CONFIG):
        print("Class cache missing or out of date: importing the project...", flush=True)
        class_cache.ensure(godot, CONFIG)
    # A slot folder that cannot be used is a setup error too (slot_pool.SlotError).
    pool = slot_pool.SlotPool(slot_count, slot_pool.slot_directory())
    return Job(suites, shards, godot, slot_count, found.reports, pool)


def claim_reports(shard: Shard, results: list[SuiteResult], reports: dict[str, str]) -> None:
    """Give each result the path of the suite it belongs to. gdUnit4 reports a suite
    whose tests are all inherited under its base's file name; `reports` says which."""
    waiting = [item.suite for item in shard.items]
    unclaimed: list[SuiteResult] = []
    for result in results:
        if result.path in waiting:
            waiting.remove(result.path)
        else:
            unclaimed.append(result)
    for result in unclaimed:
        owner = next((suite for suite in waiting if reports.get(suite) == result.path), None)
        if owner is not None:
            waiting.remove(owner)
            result.path = owner


def execute(args: argparse.Namespace, job: Job, work: Path) -> int:
    """Run the shards and report. `work` holds the shard logs and user data folders."""
    suites, shards, godot, slot_count = job.suites, job.shards, job.godot, job.slot_count
    pool = job.pool
    parts = sum(len(shard.items) for shard in shards)
    print(
        f"Running {len(suites)} suites ({parts} parts) in {len(shards)} shards "
        f"(estimated {max(shard.estimate for shard in shards):.0f}s wall, "
        f"{sum(shard.estimate for shard in shards):.0f}s serial; "
        f"{slot_count or 'no'} slot limit)",
        flush=True,
    )

    prepare_reports()
    started = time.monotonic()
    pending = list(shards)
    running: dict[int, tuple[slot_pool.ProcessGroup, slot_pool.Slot, float]] = {}
    codes: dict[int, int] = {}
    cpu_seconds = 0.0
    children_before = slot_pool.children_cpu_seconds()
    timed_out = False
    next_notice = started + 30.0
    try:
        while pending or running:
            # Start shards while slots are free; a shard holds no slot while it waits.
            while pending:
                slot = pool.try_acquire()
                if slot is None:
                    break
                shard = pending.pop(0)
                try:
                    group, _ = start_shard(godot, shard, work, low_priority=bool(slot_count))
                except BaseException:
                    slot.release()
                    raise
                running[shard.index] = (group, slot, time.monotonic())
            for index, (group, slot, launched) in list(running.items()):
                code = group.process.poll()
                if code is not None:
                    codes[index] = code
                    cpu_seconds += group.cpu_seconds() or 0.0
                    left = group.close()
                    slot.release()
                    print(
                        f"  shard {index}: exit {code} after {time.monotonic() - started:.0f}s "
                        f"({time.monotonic() - launched:.0f}s running)",
                        flush=True,
                    )
                    if left:
                        print(
                            f"  shard {index} left {len(left)} process(es) running, now "
                            f"ended: {'; '.join(left)}",
                            flush=True,
                        )
                    del running[index]
                    continue
                if shard_expired(launched, time.monotonic(), args.timeout):
                    timed_out = True
                    cpu_seconds += group.cpu_seconds() or 0.0
                    group.kill()
                    group.close()
                    slot.release()
                    codes[index] = EXIT_TIMEOUT
                    print(f"  shard {index}: TIMEOUT after {args.timeout:g}s", flush=True)
                    del running[index]
            if pending and time.monotonic() > next_notice:
                next_notice = time.monotonic() + 30.0
                print(
                    f"  {len(pending)} shard(s) waiting for a test slot "
                    f"({pool.held()}/{slot_count} in use on this machine)",
                    flush=True,
                )
            if pending and time.monotonic() - started > args.queue_timeout:
                timed_out = True
                for shard in pending:
                    codes[shard.index] = EXIT_TIMEOUT
                    print(
                        f"  shard {shard.index}: no slot after {args.queue_timeout:g}s", flush=True
                    )
                pending.clear()
            time.sleep(0.2)
    finally:
        # Interrupted (Ctrl+C, an error): leave no Godot process and no slot behind.
        for group, slot, _ in running.values():
            group.kill()
            group.close()
            slot.release()
    wall = time.monotonic() - started
    if children_before is not None:
        cpu_seconds = (slot_pool.children_cpu_seconds() or 0.0) - children_before

    failed = False
    results: list[SuiteResult] = []
    for shard in shards:
        log_path = work / f"shard_{shard.index}.log"
        # A shard that never got a slot has no log.
        output = (
            log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        )
        shard_results = parse_results(report_dir(shard.index))
        claim_reports(shard, shard_results, job.reports)
        results += shard_results
        reason = shard_failure(codes[shard.index], output)
        if not reason:
            continue
        failed = True
        # Ordinary test failures are listed one by one below: the log would add only bulk.
        # That holds only when every failure gdUnit4 counted is in that list.
        counted = sum(result.failures + result.errors for result in shard_results)
        listed = sum(len(result.failed) for result in shard_results)
        ordinary = codes[shard.index] == GDUNIT_FAILURES and reason.startswith("exit")
        if ordinary and 0 < counted <= listed and not args.verbose:
            print(f"  shard {shard.index} FAILED ({reason}): failed cases are listed below")
        elif args.verbose:
            print(f"\n===== shard {shard.index} FAILED ({reason}); log follows =====")
            print(ANSI.sub("", output))
        else:
            print(
                f"\n===== shard {shard.index} FAILED ({reason}); log follows, each error "
                "once (--verbose: all of it) ====="
            )
            print(without_repeats(ANSI.sub("", output)))

    # Every part of every suite must report, or a suite silently failed to load.
    expected = Counter(item.suite for shard in shards for item in shard.items)
    reported = Counter(result.path for result in results)
    missing = sorted((expected - reported).elements())
    if missing:
        failed = True
        print(
            f"\n{len(missing)} suite part(s) never reported "
            "(failed to load, not a gdUnit4 suite, or no tests?):"
        )
        for suite in missing:
            print(f"  {suite}")

    broken = [line for result in results for line in result.failed]
    if broken:
        print(f"\n{len(broken)} failure(s) (--verbose prints the whole log of a failed shard):")
        for line in broken:
            print(f"  {line}")

    total_tests = sum(result.tests for result in results)
    total_bad = sum(result.failures + result.errors for result in results)
    if not total_tests:
        failed = True
        print("\nNo test case ran.")
    print(
        f"\n{len(set(reported))}/{len(suites)} suites, {total_tests} test cases, "
        f"{total_bad} failures/errors in {wall:.1f}s wall"
        + (f", {cpu_seconds:.0f}s CPU" if cpu_seconds else "")
    )
    # Split parts add up to their suite's serial seconds.
    suite_seconds: dict[str, float] = {}
    case_seconds: dict[str, dict[str, float]] = {}
    for result in results:
        suite_seconds[result.path] = suite_seconds.get(result.path, 0.0) + result.seconds
        case_seconds.setdefault(result.path, {}).update(result.cases)
    slowest = sorted(suite_seconds.items(), key=lambda pair: -pair[1])[: args.slowest]
    if slowest:
        print("Slowest suites (serial seconds):")
        for path, seconds in slowest:
            print(f"  {seconds:7.2f}s  {path}")

    lost = {
        item.suite for shard in shards if codes[shard.index] == EXIT_TIMEOUT for item in shard.items
    }
    if args.write_timings:
        # A suite cut short by a timeout has only part of its seconds: keep the old value.
        measured = {suite: seconds for suite, seconds in suite_seconds.items() if suite not in lost}
        write_timings(Path(args.write_timings), measured, case_seconds)
    if timed_out:
        return EXIT_TIMEOUT
    return 1 if failed or total_bad else 0


def run(args: argparse.Namespace, job: Job) -> int:
    work = Path(tempfile.mkdtemp(prefix="godot_shards_"))
    try:
        return execute(args, job, work)
    finally:
        # Also after Ctrl+C or a crash in here: a run leaves nothing behind unless asked.
        if args.keep:
            print(f"Kept: shard logs in {work}; gdUnit4 reports in {reports_dir()}")
        else:
            shutil.rmtree(work, ignore_errors=True)
            discard_reports()


def write_timings(
    path: Path, suite_seconds: dict[str, float], case_seconds: dict[str, dict[str, float]]
) -> None:
    """Merge this run's seconds into the timings file. Suites that did not run keep
    their old value; suites that no longer exist are dropped."""
    merged = load_timings(path)
    merged.update({suite: round(seconds, 2) for suite, seconds in suite_seconds.items()})
    merged = {suite: seconds for suite, seconds in merged.items() if local_path(suite).is_file()}
    cases = {suite: value for suite, value in load_case_timings(path).items() if suite in merged}
    for suite, seconds in suite_seconds.items():
        cases.pop(suite, None)
        if seconds >= CASE_TIMINGS_FROM and case_seconds.get(suite):
            cases[suite] = {
                name: round(value, 2) for name, value in sorted(case_seconds[suite].items())
            }
    payload = {
        "_comment": (
            "gdUnit4 seconds per suite, and per test function for suites over "
            f"{CASE_TIMINGS_FROM:g} s. Written by tools/test_shards.py --write-timings, "
            "which balances and splits its shards with them."
        ),
        "suites": dict(sorted(merged.items())),
        "cases": dict(sorted(cases.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(merged)} suite timings to {path.resolve()}")


def project_files(args: argparse.Namespace) -> None:
    """Hold the data-file flags to the project: this tool reads and writes its own
    files and nobody else's. A relative path starts at the project root, like a test
    path, whatever the working folder. Raises ValueError for a path outside the project,
    for a folder, and for a file --write-timings cannot merge into or that is not a
    timings file (found now, not after the run)."""
    for flag in ("timings", "slow_list", "always_list", "write_timings"):
        value = getattr(args, flag)
        if not value:
            continue
        name = flag.replace("_", "-")
        full = kit_config.project_path(value, CONFIG.root).resolve()
        if CONFIG.root not in full.parents:
            raise ValueError(f"--{name} must name a file inside the project: {value}")
        if full.is_dir():
            # --write-timings takes the test path after it for its value.
            hint = " (put test paths first)" if flag == "write_timings" else ""
            raise ValueError(f"--{name} must name a file, not a folder: {value}{hint}")
        setattr(args, flag, str(full))
    if args.write_timings:
        held = _timings_file(Path(args.write_timings))
        if held and "suites" not in held:
            # kit.json and an agent's settings file are JSON objects of the project too.
            raise ValueError(
                "--write-timings would replace a file that holds no suite timings: "
                f"{args.write_timings}"
            )


def _at_least(kind: type[int] | type[float], least: float) -> Callable[[str], float]:
    """An argparse type: a number of `kind` that is `least` or more. A bad value is a
    usage error at once: a slot count below 0 can never be met."""

    def parse(text: str) -> float:
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{text}' is not a number") from None
        if not value >= least:
            raise argparse.ArgumentTypeError(f"must be {least:g} or more, got {text}")
        return value

    return parse


def main(argv: list[str] | None = None) -> int:
    global CONFIG
    gate_support.plain_output()
    # No abbreviations: a flag is what it is spelled as, for a reader and for an allow rule.
    parser = argparse.ArgumentParser(
        description="Run the gdUnit4 suites in parallel Godot processes (shards).",
        epilog="Modes, data files and exit codes: see the top of tools/test_shards.py.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="suites or folders to run exactly, res:// or project-relative (default: --changed)",
    )
    parser.add_argument(
        "--shards",
        type=_at_least(int, 1),
        help="Godot processes (default: sized to the work, one per slot)",
    )
    parser.add_argument(
        "--slots",
        type=_at_least(int, 0),
        help="machine-wide test slots, 0 = no limit (default: GODOT_TEST_SLOTS, else a "
        "quarter of the cores; every core when CI is set)",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--changed",
        nargs="?",
        const="",
        metavar="BASE",
        help="only suites a change since branch BASE can affect (default BASE: the "
        "remote's default branch, else main, else master); the default mode",
    )
    modes.add_argument("--dev", action="store_true", help="every suite but the heavy ones")
    modes.add_argument("--all", action="store_true", help="every suite")
    parser.add_argument(
        "--heavy-seconds",
        type=_at_least(float, 0),
        default=3.0,
        help="a suite recorded at this many seconds or more is heavy (default 3)",
    )
    parser.add_argument(
        "--timeout",
        type=_at_least(float, 1),
        default=900.0,
        help="seconds one shard may run once started (default 900)",
    )
    parser.add_argument(
        "--queue-timeout",
        type=_at_least(float, 0),
        default=3600.0,
        help="seconds to wait for free slots (default 3600)",
    )
    parser.add_argument("--timings", help=f"timings file (default {TIMINGS})")
    parser.add_argument(
        "--skip-slow", action="store_true", help="leave out the slow list (implies --all)"
    )
    parser.add_argument("--slow-list", help=f"slow suites file (default {SLOW_SUITES})")
    parser.add_argument(
        "--always-list", help=f"always-run suites file (default {ALWAYS_RUN_SUITES})"
    )
    parser.add_argument(
        "--write-timings",
        nargs="?",
        const="",
        metavar="FILE",
        help=f"merge measured suite seconds into FILE (default: the project's {TIMINGS})",
    )
    parser.add_argument(
        "--no-import", action="store_true", help="do not rebuild a stale class cache first"
    )
    parser.add_argument(
        "--slowest", type=_at_least(int, 0), default=10, help="list the N slowest suites"
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the shard logs and gdUnit4's HTML/XML reports"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print the whole log of every failed shard"
    )
    args = parser.parse_args(argv)
    # Both flags take an optional value, so a test path right after one is read as it.
    for flag, value in (("--write-timings", args.write_timings), ("--changed", args.changed)):
        if (value or "").startswith("res://") or (value or "").endswith(".gd"):
            parser.error(f"{flag} took the test path {value} as its value: put test paths first")
    # Only what happens before the first shard starts is a usage or setup error (exit 2).
    try:
        CONFIG = kit_config.load()
        if args.write_timings == "":
            args.write_timings = str(CONFIG.root / TIMINGS)
        project_files(args)
        job = prepare(args)
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 2
    if isinstance(job, int):
        return job
    try:
        return run(args, job)
    except slot_pool.SlotError as error:
        # The slot folder stopped working under the run (removed, its disk gone): the
        # shards were killed on the way here, and the machine needs fixing, not a test.
        print(error, file=sys.stderr)
        return 2


def stop_on_signals() -> None:
    """Make Ctrl+C, SIGTERM, SIGHUP and SIGQUIT (an agent's timeout, a closed terminal)
    end the run one way: on the way out the shards are killed and the run's folders
    removed, and the exit code is 128 + the signal. Once: a signal that comes during
    that cleanup must not cut it short."""
    numbers = [getattr(signal, name) for name in STOP_SIGNALS if hasattr(signal, name)]

    def stop(number: int, _frame: object) -> None:
        for other in numbers:
            signal.signal(other, signal.SIG_IGN)
        raise SystemExit(128 + number)

    for number in numbers:
        signal.signal(number, stop)


def run_as_program() -> int:
    """main() as the program's exit code. A run stopped by Ctrl+C says so in one line
    where Python would print a traceback."""
    stop_on_signals()
    try:
        return main()
    except SystemExit as stopped:
        if stopped.code == 128 + signal.SIGINT:
            print(INTERRUPTED, file=sys.stderr)
        raise


if __name__ == "__main__":
    sys.exit(run_as_program())
