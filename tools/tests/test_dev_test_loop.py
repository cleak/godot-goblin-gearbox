"""The parts under tools/test_shards.py: slot_pool.py (shared test slots, process
groups) and select_suites.py (which suites a change can affect)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
    pool = slot_pool.SlotPool(1, slots)
    try:
        deadline = time.monotonic() + 20
        while not said.read_text().strip() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert said.read_text().strip() == "held"
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


def test_the_slot_folder_is_shared_by_every_project_on_the_machine(tmp_path: Path) -> None:
    assert slot_pool.slot_directory({}).name == "godot_test_slots"
    assert slot_pool.slot_directory({"GODOT_TEST_SLOT_DIR": str(tmp_path)}) == tmp_path


BURN = "sum(range(2*10**7)); import time; time.sleep(60)"
SPAWN_AND_WAIT = (
    "import subprocess, sys, time;"
    f"child = subprocess.Popen([sys.executable, '-c', {BURN!r}]);"
    "print(child.pid, flush=True);"
    "time.sleep(60)"
)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        )
        return str(pid) in listing.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_gone(pid: int, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return False


def test_a_process_group_counts_and_kills_the_whole_tree(tmp_path: Path) -> None:
    log = tmp_path / "out.txt"
    with log.open("wb") as out:
        group = slot_pool.ProcessGroup.launch(
            [sys.executable, "-c", SPAWN_AND_WAIT], stdout=out, stderr=subprocess.DEVNULL
        )
    grandchild = 0
    try:
        deadline = time.monotonic() + 20
        while not log.read_text().strip() and time.monotonic() < deadline:
            time.sleep(0.1)
        grandchild = int(log.read_text().split()[0])
        if os.name == "nt":
            while (group.cpu_seconds() or 0.0) < 0.05 and time.monotonic() < deadline:
                time.sleep(0.2)
            # The grandchild's CPU is counted: it was in the job from its first instruction.
            assert (group.cpu_seconds() or 0.0) >= 0.05
        assert group.process.poll() is None
    finally:
        group.kill()
        group.close()
    assert group.process.poll() is not None
    if os.name == "nt":
        assert _wait_gone(grandchild), "the grandchild outlived its group"


@pytest.mark.skipif(os.name != "nt", reason="job objects are a Windows mechanism")
def test_killing_the_runner_takes_its_godot_processes_with_it(tmp_path: Path) -> None:
    # A runner that starts a group and is then killed outright (an agent's timeout).
    runner = (
        "import sys, time; sys.path.insert(0, sys.argv[1]); import slot_pool;"
        f"group = slot_pool.ProcessGroup.launch([sys.executable, '-c', {SPAWN_AND_WAIT!r}],"
        " stdout=open(sys.argv[2], 'wb'));"
        "print(group.process.pid, flush=True); time.sleep(60)"
    )
    log = tmp_path / "grandchild.txt"
    # The real interpreter, not a virtual environment's launcher: the launcher has a job
    # of its own that would do the killing and hide a regression here.
    python = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [python, "-c", runner, str(TOOLS / "ci"), str(log)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        child = int(process.stdout.readline())
        deadline = time.monotonic() + 20
        while not (log.is_file() and log.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.1)
        grandchild = int(log.read_text().split()[0])
        assert _alive(child) and _alive(grandchild)
    finally:
        process.kill()
        process.wait()
    assert _wait_gone(child) and _wait_gone(grandchild)


def test_run_captured_returns_output_and_kills_on_timeout() -> None:
    code, output = slot_pool.run_captured(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], 30
    )
    assert code == 0 and "out" in output and "err" in output
    code, _ = slot_pool.run_captured([sys.executable, "-c", "raise SystemExit(7)"], 30)
    assert code == 7
    started = time.monotonic()
    code, output = slot_pool.run_captured([sys.executable, "-u", "-c", SPAWN_AND_WAIT], 3)
    assert code is None and time.monotonic() - started < 30
    if os.name == "nt":
        assert _wait_gone(int(output.split()[0]))
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
