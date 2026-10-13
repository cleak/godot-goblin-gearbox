"""conftest.py enforces the mark, and `--dist loadgroup` keeps the marked tests on one worker."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import _gate_fixtures
import conftest
import pytest

SUFFIX = f"@{conftest.MARK}"


def _run_is_grouped(config: pytest.Config) -> bool:
    """True on a pytest-xdist worker of a `--dist loadgroup` run."""
    return hasattr(config, "workerinput") and config.getoption("dist", "no") == "loadgroup"


def test_the_binary_is_refused_without_the_mark() -> None:
    with pytest.raises(pytest.fail.Exception, match="add @pytest.mark.real_godot"):
        _gate_fixtures.real_godot()
    with pytest.raises(pytest.fail.Exception, match="add @pytest.mark.real_godot"):
        _gate_fixtures.lone_gui_godot(_gate_fixtures.REPO / "artifacts" / "never_made")


def test_the_variables_that_name_a_godot_are_hidden_without_the_mark() -> None:
    assert [name for name in _gate_fixtures.GODOT_VARIABLES if name in os.environ] == []
    assert "GODOT_BIN" in _gate_fixtures.GODOT_VARIABLES


@pytest.mark.real_godot
def test_a_marked_test_may_ask_and_runs_in_the_one_group(request: pytest.FixtureRequest) -> None:
    group = request.node.get_closest_marker("xdist_group")
    assert group is not None and group.args == (conftest.MARK,)
    if _run_is_grouped(request.config):
        # The id is what the scheduler reads; the mark alone moves nothing.
        assert request.node.nodeid.endswith(SUFFIX)
    found = _gate_fixtures.real_godot()  # None on a machine without Godot: not a failure
    assert found is None or os.path.isfile(found)


def test_every_test_given_a_real_godot_by_a_fixture_is_marked(
    request: pytest.FixtureRequest,
) -> None:
    items = request.session.items
    by_fixture = [item for item in items if conftest.REAL_GODOT_FIXTURES & set(item.fixturenames)]
    marked = [item for item in items if item.get_closest_marker(conftest.MARK) is not None]
    grouped = [
        item
        for item in items
        if (group := item.get_closest_marker("xdist_group")) and group.args == (conftest.MARK,)
    ]
    # When this file runs alone no test uses those fixtures; the rule still holds.
    assert set(by_fixture) <= set(marked) and marked == grouped
    assert request.node not in marked
    if _run_is_grouped(request.config):
        assert [item for item in items if item.nodeid.endswith(SUFFIX)] == marked


ROUTING_PROBE = """import pytest


@pytest.fixture
def godot() -> str:
    return "the fixture's name gives the mark"


@pytest.mark.real_godot
@pytest.mark.parametrize("turn", range(8))
def test_marked(turn: int) -> None:
    pass


@pytest.mark.parametrize("turn", range(4))
def test_marked_by_its_fixture(turn: int, godot: str) -> None:
    pass


@pytest.mark.parametrize("turn", range(12))
def test_plain(turn: int) -> None:
    pass
"""


def test_loadgroup_sends_every_marked_test_to_one_worker(tmp_path: Path) -> None:
    """The routing itself, in a pytest of its own: three workers, this conftest.py, and
    tests that start nothing. However this run was started, that one is `--dist loadgroup`."""
    for name in ("conftest.py", "_gate_fixtures.py"):
        shutil.copyfile(Path(__file__).with_name(name), tmp_path / name)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(ROUTING_PROBE, encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTEST_")}
    run = ("test_probe.py", "-v", "-p", "no:cacheprovider", "-n", "3", "--dist", "loadgroup")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *run],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # "[gw1] [ 12%] PASSED test_probe.py::test_marked[3]@real_godot"
    ran = re.findall(r"^\[(gw\d+)\] .*PASSED (test_probe\.py::\S+)", result.stdout, re.M)
    assert len(ran) == 24, result.stdout
    marked = [(worker, test) for worker, test in ran if "::test_marked" in test]
    plain = [(worker, test) for worker, test in ran if "::test_plain" in test]
    assert len(marked) == 12 and len(plain) == 12, result.stdout
    assert all(test.endswith(SUFFIX) for _, test in marked), result.stdout
    assert not any("@" in test for _, test in plain), result.stdout
    assert len({worker for worker, _ in marked}) == 1, result.stdout
