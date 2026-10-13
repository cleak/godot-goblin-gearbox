"""One real-Godot test at a time, also when the tests run in parallel.

tools/check.ps1 runs this folder on several workers (pytest-xdist, `-n <workers> --dist
loadgroup`). Most tests start PowerShell and a stand-in Godot, which is cheap. A real Godot
is not, and other people's editors and games share the machine: every test that starts one
carries the mark `real_godot`, and `--dist loadgroup` sends all of them to one worker, one
after another. Without `-n` pytest runs one test at a time anyway. `-n` with any other
`--dist` spreads them over the workers.

A test gets the mark by asking for it (`@pytest.mark.real_godot`), or by using a fixture
that hands out the binary. The mark is enforced, not trusted: without it
_gate_fixtures.real_godot() fails the test, and the variables that name a Godot are taken
out of its environment.

No PowerShell a test starts may write the developer's PowerShell startup profile
(_gate_fixtures.NO_STARTUP_PROFILE says why): the variable that stops it is put into this
process's environment, which every program a test starts inherits.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import _gate_fixtures
import pytest

MARK = "real_godot"
# Fixtures whose value comes from a real Godot (test_gd_tools.py, test_kit_config_parity.py).
REAL_GODOT_FIXTURES = frozenset({"godot", "godot_records"})

# Here and not in a fixture: before the first test, on every worker.
os.environ.update(_gate_fixtures.NO_STARTUP_PROFILE)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", f"{MARK}: starts a real Godot; --dist loadgroup keeps these on one worker"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """tryfirst: pytest-xdist reads the group in its own hook of this name, on each worker,
    and writes it into the test's id (`...::test_x@real_godot`). The scheduler keeps the
    ids with one suffix on one worker. A group added after that hook ran is never read."""
    for item in items:
        if REAL_GODOT_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(getattr(pytest.mark, MARK))
        if item.get_closest_marker(MARK) is not None:
            item.add_marker(pytest.mark.xdist_group(MARK))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    """Around the whole test, fixtures of every scope included."""
    marked = item.get_closest_marker(MARK) is not None
    hidden: dict[str, str] = {}
    if not marked:
        for name in _gate_fixtures.GODOT_VARIABLES:
            if name in os.environ:
                hidden[name] = os.environ.pop(name)
    _gate_fixtures.allow_real_godot(marked)
    try:
        return (yield)
    finally:
        _gate_fixtures.allow_real_godot(False)
        os.environ.update(hidden)
