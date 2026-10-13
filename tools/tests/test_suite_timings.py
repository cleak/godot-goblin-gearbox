"""tools/test_shards.py: the timings file (--write-timings), and which files it may touch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import kit_config  # noqa: E402
import test_shards as ts  # noqa: E402

A = "res://test/unit/test_a.gd"
B = "res://test/unit/test_b.gd"
GONE = "res://test/unit/test_gone.gd"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    for suite in (A, B):
        path = tmp_path / suite.removeprefix("res://")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("extends GdUnitTestSuite\n", encoding="utf-8")
    monkeypatch.setattr(ts, "CONFIG", kit_config.load(tmp_path))
    return tmp_path


@pytest.mark.parametrize("flag", ["--write-timings", "--timings", "--slow-list", "--always-list"])
def test_a_data_file_outside_the_project_is_refused(
    tmp_path: Path, monkeypatch, capsys, flag: str
) -> None:
    """The flags are pre-approved for agents (.claude/settings.json): none may read or
    replace a file of another project."""
    root = tmp_path / "project"
    (root / "test").mkdir(parents=True)
    victim = tmp_path / "notes.json"
    victim.write_text("three years of notes", encoding="utf-8")
    monkeypatch.setattr(ts, "CONFIG", ts.CONFIG)  # main() replaces it: put it back afterwards
    monkeypatch.setattr(kit_config, "load", lambda *_: kit_config.KitConfig(root=root.resolve()))
    assert ts.main(["res://test", flag, str(victim)]) == 2
    said = capsys.readouterr().err.strip()
    assert said == f"{flag} must name a file inside the project: {victim}"
    assert victim.read_text(encoding="utf-8") == "three years of notes"


@pytest.mark.parametrize("flag", ["--write-timings", "--timings", "--slow-list", "--always-list"])
def test_a_folder_is_not_a_data_file(tmp_path: Path, monkeypatch, capsys, flag: str) -> None:
    """`--write-timings test/unit` reads the test path as the flag's value: one line
    before the run, not a traceback after it."""
    root = (tmp_path / "project").resolve()
    (root / "test" / "unit").mkdir(parents=True)
    monkeypatch.setattr(ts, "CONFIG", ts.CONFIG)
    monkeypatch.setattr(kit_config, "load", lambda *_: kit_config.KitConfig(root=root))
    monkeypatch.setattr(ts, "prepare", lambda _args: pytest.fail("the run was prepared"))
    assert ts.main(["res://test", flag, "test/unit"]) == 2
    hint = " (put test paths first)" if flag == "--write-timings" else ""
    said = capsys.readouterr().err.strip()
    assert said == f"{flag} must name a file, not a folder: test/unit{hint}"


def test_a_relative_data_file_starts_at_the_project_root(tmp_path: Path, monkeypatch) -> None:
    """Like a test path: the working folder does not matter."""
    root = (tmp_path / "project").resolve()
    data = root / "tools" / "ci"
    data.mkdir(parents=True)
    (root / "test").mkdir()
    (data / "test_timings.json").write_text('{"suites": {}}', encoding="utf-8")
    # The same names in the working folder are not the project's files.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "tools" / "ci").mkdir(parents=True)
    (elsewhere / "tools" / "ci" / "test_timings.json").write_text("not json", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(ts, "CONFIG", ts.CONFIG)
    monkeypatch.setattr(kit_config, "load", lambda *_: kit_config.KitConfig(root=root))
    prepared: dict[str, str] = {}

    def prepare(args) -> int:
        prepared.update(vars(args))
        return 0

    monkeypatch.setattr(ts, "prepare", prepare)
    flags = {
        "timings": "tools/ci/test_timings.json",
        "slow_list": "tools\\ci\\slow_suites.txt",
        "always_list": "./tools/ci/always_run_suites.txt",
        "write_timings": "tools/ci/test_timings.json",
    }
    argv = [
        part for flag, value in flags.items() for part in (f"--{flag.replace('_', '-')}", value)
    ]
    assert ts.main(["res://test", *argv]) == 0
    for flag, value in flags.items():
        assert Path(prepared[flag]) == root / value.replace("\\", "/"), flag


@pytest.mark.parametrize("name", ["kit.json", ".claude/settings.json"])
def test_write_timings_replaces_only_a_timings_file(
    tmp_path: Path, monkeypatch, capsys, name: str
) -> None:
    """The flag is pre-approved for agents, and these are JSON objects inside the project
    too: merging the timings into one would drop everything it holds."""
    root = (tmp_path / "project").resolve()
    (root / "test").mkdir(parents=True)
    other = root / name
    other.parent.mkdir(exist_ok=True)
    other.write_text('{"permissions": {"allow": []}}', encoding="utf-8")
    monkeypatch.setattr(ts, "CONFIG", ts.CONFIG)
    monkeypatch.setattr(kit_config, "load", lambda *_: kit_config.KitConfig(root=root))
    monkeypatch.setattr(ts, "prepare", lambda _args: pytest.fail("the run was prepared"))
    assert ts.main(["res://test", "--write-timings", name]) == 2
    said = capsys.readouterr().err.strip()
    assert said == f"--write-timings would replace a file that holds no suite timings: {other}"
    assert other.read_text(encoding="utf-8") == '{"permissions": {"allow": []}}'


def test_a_timings_file_that_cannot_be_merged_is_found_before_the_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = (tmp_path / "project").resolve()
    (root / "test").mkdir(parents=True)
    broken = root / "timings.json"
    broken.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(ts, "CONFIG", ts.CONFIG)
    monkeypatch.setattr(kit_config, "load", lambda *_: kit_config.KitConfig(root=root))
    assert ts.main(["res://test", "--write-timings", str(broken)]) == 2
    said = capsys.readouterr().err.strip().splitlines()
    assert len(said) == 1 and "timings.json is not valid JSON" in said[0]
    assert broken.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize("gone", ["--godot", "--report", "--g", "--write", "--slow"])
def test_no_flag_names_a_program_or_is_guessed_from_a_prefix(gone: str, capsys) -> None:
    """A flag that names a program runs it, and argparse takes a prefix (--g) for the
    flag: this tool has neither."""
    with pytest.raises(SystemExit) as stopped:
        ts.main(["res://test", gone, "x"])
    assert stopped.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_first_write_creates_the_file_the_planner_reads(project: Path) -> None:
    path = project / "tools" / "ci" / "test_timings.json"
    ts.write_timings(path, {A: 6.123, B: 0.4}, {A: {"t1": 4.0, "t2": 2.123}, B: {"t": 0.4}})
    assert ts.load_timings(path) == {A: 6.12, B: 0.4}
    # Case seconds are kept only for suites long enough to be split.
    assert ts.load_case_timings(path) == {A: {"t1": 4.0, "t2": 2.12}}
    assert path.read_bytes().endswith(b"}\n") and b"\r" not in path.read_bytes()


def test_a_later_write_merges_and_drops_suites_that_no_longer_exist(project: Path) -> None:
    path = project / "timings.json"
    old = {"suites": {A: 9.0, B: 2.0, GONE: 7.0}, "cases": {A: {"t": 9.0}, GONE: {"t": 7.0}}}
    path.write_text(json.dumps(old), encoding="utf-8")
    # Only B ran this time: A keeps its value, the deleted suite goes.
    ts.write_timings(path, {B: 3.0}, {B: {"t": 3.0}})
    assert ts.load_timings(path) == {A: 9.0, B: 3.0}
    assert ts.load_case_timings(path) == {A: {"t": 9.0}}


def test_a_suite_that_got_faster_loses_its_case_seconds(project: Path) -> None:
    path = project / "timings.json"
    path.write_text(json.dumps({"suites": {A: 9.0}, "cases": {A: {"t": 9.0}}}), encoding="utf-8")
    ts.write_timings(path, {A: 2.0}, {A: {"t": 2.0}})
    assert ts.load_timings(path) == {A: 2.0}
    assert ts.load_case_timings(path) == {}
