"""Which scripts are gdUnit4 suites: one rule for tools/test_shards.py and select_suites.py.

gdUnit4 loads a script and walks what it extends; a script whose bases reach
GdUnitTestSuite is a suite. This reads the same answer from the text, so no Godot has
to start. Every header gdUnit4 accepts is followed:

    extends GdUnitTestSuite
    class_name MySuite extends GdUnitTestSuite
    extends "res://addons/gdUnit4/src/GdUnitTestSuite.gd"
    extends BaseSuite                  # a class_name script that is itself a suite
    extends "res://test/base_suite.gd" # or "base_suite.gd", beside this file

A suite also runs the `test_` functions of the suites it extends. gdUnit4 names a
suite's report after the script that holds its first test function, so a suite with
none of its own reports under its base's name: see `reported_as`.

Not followed: a base that is an inner class (`extends Outer.Inner`). Such a script is
handed to gdUnit4 anyway when it has test functions: if it is no suite gdUnit4 reports
nothing for it, and the runner fails on a suite that does not report.

Run as a program it prints the answer for every script under the test folder, as one
JSON object, so tools/lint_tests.ps1 lints exactly what the runner runs:

    uv run python tools/ci/suites.py

    {"test_root": "test", "scripts": [{"path": "test/unit/test_x.gd", "kind": "suite",
      "extends": "extends GdUnitTestSuite", "tests": ["test_a"], "inherits_tests": false}],
     "outside": ["old_tests/test_y.gd"]}

`outside` lists the authored suites with tests that are not under the test folder and
that no suite there extends: no runner starts them, so tools/lint_tests.ps1 fails on each.

Exit codes: 0 printed; 2 kit.json cannot be used.
"""

from __future__ import annotations

import dataclasses
import json
import posixpath
import re
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import class_cache  # noqa: E402
import gate_support  # noqa: E402
import kit_config  # noqa: E402
import project_refs  # noqa: E402

SUITE_CLASS = "GdUnitTestSuite"
TEST_FUNC = re.compile(r"^(?:static[ \t]+)?func[ \t]+(test_\w+)[ \t]*\(", re.MULTILINE)

# What a script under the test folder is to the runner.
SUITE = "suite"  # gdUnit4 is asked to run it
BASE = "base"  # a suite with no test that other suites extend: not run alone
NOT_A_SUITE = "not_a_suite"  # has test functions, extends no suite: the run fails on it
OTHER = "other"  # a helper: no test function, no suite


class SuiteIndex:
    """Answers "is this script a suite, and which tests does it run" for one project.
    Paths are project-relative with forward slashes. `texts` saves reading files the
    caller already holds; any other script is read from disk when a suite extends it."""

    def __init__(self, config: kit_config.KitConfig, texts: dict[str, str] | None = None) -> None:
        self.config = config
        self._texts: dict[str, str | None] = dict(texts or {})
        self._classes: dict[str, str] | None = None
        self._uids: dict[str, str] | None = None
        self._chains: dict[str, tuple[list[str], bool]] = {}

    def text(self, script: str) -> str | None:
        if script not in self._texts:
            path = self.config.root / script
            self._texts[script] = (
                path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
            )
        return self._texts[script]

    def _class_script(self, name: str) -> str | None:
        """The script that declares `class_name name`, wherever Godot scans (addons too)."""
        if self._classes is None:
            self._classes = {
                declared: path.removeprefix("res://")
                for declared, path in sorted(class_cache.classes_on_disk(self.config))
            }
        return self._classes.get(name)

    def _uid_script(self, uid: str) -> str | None:
        if self._uids is None:
            scanned = dataclasses.replace(self.config, source_exclude=(), source_include=())
            self._uids = {}
            for sidecar in kit_config.source_files((".uid",), config=scanned):
                owner = project_refs.relative(sidecar, self.config).removesuffix(".uid")
                self._uids[sidecar.read_text(encoding="utf-8", errors="replace").strip()] = owner
        return self._uids.get(uid)

    def base(self, script: str) -> str:
        """What `script` extends: SUITE_CLASS, a project-relative script path, or ""
        (an engine class, nothing, or a base this cannot follow)."""
        name, path = project_refs.own_base(self.text(script) or "")
        if name == SUITE_CLASS or posixpath.basename(path) == f"{SUITE_CLASS}.gd":
            return SUITE_CLASS
        if name:
            return self._class_script(name) or ""
        if path.startswith("res://"):
            return path.removeprefix("res://")
        if path.startswith("uid://"):
            return self._uid_script(path) or ""
        # A bare file name is looked up beside the script.
        return posixpath.normpath(posixpath.join(posixpath.dirname(script), path)) if path else ""

    def chain(self, script: str) -> tuple[list[str], bool]:
        """`script` and the scripts it extends, nearest first, and whether they end at
        GdUnitTestSuite."""
        if script not in self._chains:
            seen: list[str] = []
            current = script
            reached = False
            while current and current not in seen and self.text(current) is not None:
                seen.append(current)
                current = self.base(current)
                if current == SUITE_CLASS:
                    reached = True
                    break
            self._chains[script] = (seen, reached)
        return self._chains[script]

    def is_suite(self, script: str) -> bool:
        return self.chain(script)[1]

    def unfollowed(self, script: str) -> bool:
        """True when the chain stops at a base this cannot read (an inner class), so
        whether `script` is a suite is not known."""
        seen, reached = self.chain(script)
        if reached or not seen:
            return False
        return "." in project_refs.own_base(self.text(seen[-1]) or "")[0]

    def may_be_suite(self, script: str) -> bool:
        return self.is_suite(script) or self.unfollowed(script)

    def own_tests(self, script: str) -> list[str]:
        """The `test_` functions written in this script, in file order."""
        return TEST_FUNC.findall(project_refs.code_only(self.text(script) or ""))

    def inherits_tests(self, script: str) -> bool:
        return any(self.own_tests(base) for base in self.chain(script)[0][1:])

    def has_tests(self, script: str) -> bool:
        return bool(self.own_tests(script)) or self.inherits_tests(script)

    def reported_as(self, script: str) -> str:
        """The path gdUnit4's report gives this suite: its own, unless every test it runs
        is inherited. Then it is this suite's folder with the file name of the nearest
        base that has a test function."""
        for holder in self.chain(script)[0]:
            if self.own_tests(holder):
                return posixpath.join(posixpath.dirname(script), posixpath.basename(holder))
        return script

    def describe_base(self, script: str) -> str:
        """For a message: what the script says it extends."""
        name, path = project_refs.own_base(self.text(script) or "")
        return f"extends {name or path}" if name or path else "no extends"

    def kinds(self, scripts: Iterable[str], known: Iterable[str]) -> dict[str, str]:
        """SUITE, BASE, NOT_A_SUITE or OTHER for each of `scripts`. `known` is every
        script that may extend one of them (the whole test folder): a suite without a
        test is a BASE only when another script extends it."""
        extended = {base for script in known for base in self.chain(script)[0][1:2]}
        kinds: dict[str, str] = {}
        for script in scripts:
            if self.is_suite(script):
                # A suite with no test of its own or inherited is run all the same
                # (gdUnit4 then reports nothing for it and the run fails), unless it
                # is a base.
                runs = self.has_tests(script) or script not in extended
                kinds[script] = SUITE if runs else BASE
            elif not self.own_tests(script):
                kinds[script] = OTHER
            else:
                # A base that cannot be read from the text: gdUnit4 decides. A script
                # it does not take for a suite never reports, which fails the run.
                kinds[script] = SUITE if self.unfollowed(script) else NOT_A_SUITE
        return kinds


def outside(config: kit_config.KitConfig, index: SuiteIndex, inside: set[str]) -> list[str]:
    """Authored suites with tests that are not under the test folder (`inside` is every
    script there) and that no suite there extends. The runners start gdUnit4 on that
    folder alone, so such a suite never runs, and a test that never runs passes. A base's
    tests run in each suite that extends it."""
    authored = kit_config.source_files((".gd",), config=config)
    scripts = (project_refs.relative(path, config) for path in authored)
    run_by_a_suite_inside = {base for script in inside for base in index.chain(script)[0][1:]}
    return [
        script
        for script in scripts
        if script not in inside
        and script not in run_by_a_suite_inside
        and index.is_suite(script)
        and index.has_tests(script)
    ]


def describe(config: kit_config.KitConfig) -> dict:
    """Every script under the test folder and what it is to the runner."""
    files = []
    if config.test_dir.is_dir():
        files = kit_config.source_files((".gd",), path=config.test_dir, config=config)
    scripts = [project_refs.relative(path, config) for path in files]
    index = SuiteIndex(config)
    kinds = index.kinds(scripts, scripts)
    return {
        "test_root": config.test_root,
        "outside": outside(config, index, set(scripts)),
        "scripts": [
            {
                "path": script,
                "kind": kinds[script],
                "extends": index.describe_base(script),
                "tests": index.own_tests(script),
                "inherits_tests": index.inherits_tests(script),
            }
            for script in scripts
        ],
    }


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    gate_support.plain_output()
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("usage: uv run python tools/ci/suites.py", file=sys.stderr)
        return 2
    try:
        config = kit_config.load(root)
    except kit_config.ConfigError as error:
        print(f"suites: {error}", file=sys.stderr)
        return 2
    # ASCII only (other characters as \\uXXXX): the reader need not guess a code page.
    print(json.dumps(describe(config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
