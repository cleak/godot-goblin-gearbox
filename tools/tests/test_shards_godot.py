"""What tools/test_shards.py needs from Godot before a shard starts:
tools/ci/godot_binary.py (which binary) and tools/ci/class_cache.py (a fresh class cache)."""

from __future__ import annotations

import inspect
import json
import os
import sys
import threading
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import _gate_fixtures  # noqa: E402
import class_cache  # noqa: E402
import godot_binary  # noqa: E402
import kit_config  # noqa: E402

STANDARD = "4.7.2.stable.official.abc123"
MONO = "4.7.2.stable.mono.official.abc123"
OLDER = "4.6.1.stable.official.def456"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch):
    """A project at <tmp>/work/game and a way to plant fake Godot binaries.
    `plant(path, reported)` creates the file and makes `--version` answer `reported`."""
    root = tmp_path / "work" / "game"
    root.mkdir(parents=True)
    versions: dict[Path, str] = {}

    def plant(path: Path, reported: str = STANDARD) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
        versions[path] = reported
        return path

    def fake_version(binary: Path) -> str:
        if binary not in versions:
            raise OSError(f"'{binary}' is not a planted binary")
        return versions[binary]

    monkeypatch.setattr(godot_binary, "version_text", fake_version)
    return root, plant


def _config(root: Path, **godot) -> kit_config.KitConfig:
    (root / "kit.json").write_text(json.dumps({"godot": {"version": "4.7.2", **godot}}), "utf-8")
    return kit_config.load(root)


def _resolve(root: Path, env: dict[str, str] | None = None, **godot):
    return Path(godot_binary.resolve(_config(root, **godot), {"PATH": "", **(env or {})}))


def test_the_order_is_env_then_toolchain_then_search_roots_then_path(
    machine, tmp_path: Path
) -> None:
    root, plant = machine
    on_path = plant(tmp_path / "bin" / "godot.exe")
    env = {"PATH": str(on_path.parent), "PATHEXT": ".EXE"}
    assert _resolve(root, env) == on_path
    # A release beside the project is not found by its name: only a folder kit.json names.
    beside = plant(root.parent / "Godot_v4.7.2-stable_win64.exe")
    assert _resolve(root, env) == on_path
    assert _resolve(root, env, search_roots=[".."]) == beside
    listed = plant(tmp_path / "engines" / "Godot_v4.7.2-stable_win64.exe")
    roots = [str(tmp_path / "engines")]
    assert _resolve(root, env, search_roots=roots) == listed
    local = plant(root / ".tools" / "godot" / "Godot_v4.7.2-stable_win64.exe")
    assert _resolve(root, env, search_roots=roots) == local
    second = plant(tmp_path / "second" / "godot.exe")
    env["GODOT4_EXE"] = str(second)
    assert _resolve(root, env, search_roots=roots) == second
    first = plant(tmp_path / "first" / "godot.exe")
    env["GODOT_BIN"] = str(first)
    assert _resolve(root, env, search_roots=roots) == first


def test_no_argument_names_the_binary() -> None:
    """A tool that took the binary as an argument would run any program its command line
    names: the environment and kit.json are the only ways in."""
    assert list(inspect.signature(godot_binary.resolve).parameters) == ["config", "env"]


def test_an_explicit_binary_is_never_skipped(machine, tmp_path: Path) -> None:
    root, plant = machine
    plant(root / ".tools" / "godot" / "Godot_v4.7.2-stable_win64.exe")
    with pytest.raises(godot_binary.GodotNotFoundError, match="GODOT_BIN does not name"):
        _resolve(root, {"GODOT_BIN": str(tmp_path / "nope.exe")})
    old = plant(tmp_path / "old" / "godot.exe", OLDER)
    with pytest.raises(godot_binary.GodotNotFoundError, match="needs Godot 4.7 stable"):
        _resolve(root, {"GODOT_BIN": str(old)})
    mono = plant(tmp_path / "mono" / "godot.exe", MONO)
    with pytest.raises(godot_binary.GodotNotFoundError, match="GODOT4_EXE.*Mono"):
        _resolve(root, {"GODOT4_EXE": str(mono)})
    beta = plant(tmp_path / "beta" / "godot.exe", "4.7.beta2.official.123abc")
    with pytest.raises(godot_binary.GodotNotFoundError, match="GODOT_BIN.*needs Godot 4.7 stable"):
        _resolve(root, {"GODOT_BIN": str(beta)})


def test_a_folder_gives_the_newest_patch_and_the_console_launcher_first(machine) -> None:
    root, plant = machine
    folder = root / ".tools" / "godot"
    for name in (
        "Godot_v4.7.2-stable_win64.exe",
        "Godot_v4.7.2-stable_win64_console.exe",
        "Godot_v4.7.10-stable_win64.exe",
        "Godot_v4.7.10-stable_win64_console.exe",
        "Godot_v4.7-stable_win64.exe",
        "Godot_v4.6.3-stable_win64.exe",
        "Godot_v4.7.2-stable_mono_win64.exe",
        "readme.txt",
    ):
        plant(folder / name)
    names = [path.name for path in godot_binary.folder_candidates(folder, "4.7")]
    assert names == [
        "Godot_v4.7.10-stable_win64_console.exe",
        "Godot_v4.7.2-stable_win64_console.exe",
        "Godot_v4.7.10-stable_win64.exe",
        "Godot_v4.7.2-stable_win64.exe",
        "Godot_v4.7-stable_win64.exe",
    ]
    # The release kit.json pins goes ahead of a newer patch.
    pinned = [path.name for path in godot_binary.folder_candidates(folder, "4.7", "4.7.2")]
    assert pinned[:2] == [
        "Godot_v4.7.2-stable_win64_console.exe",
        "Godot_v4.7.10-stable_win64_console.exe",
    ]
    assert godot_binary.folder_candidates(folder, "4.6") == [
        folder / "Godot_v4.6.3-stable_win64.exe"
    ]
    later = plant(folder / "Godot_v4.10.1-stable_win64.exe")
    assert godot_binary.folder_candidates(folder, "4.10") == [later]
    assert godot_binary.folder_candidates(folder, "4.1") == []  # 4.10 is not a patch of 4.1


def test_a_release_named_folder_is_searched_one_level_down(machine) -> None:
    root, plant = machine
    # Both ways a release zip gets unpacked: into a folder named like the zip.
    inner = plant(
        root.parent / "Godot_v4.7.2-stable_win64.exe" / "Godot_v4.7.2-stable_win64_console.exe"
    )
    plant(root.parent / "Godot_v4.7.2-stable_win64.exe" / "Godot_v4.7.2-stable_win64.exe")
    plant(root.parent / "Godot_v4.7.2-stable_mono_win64" / "Godot_v4.7.2-stable_mono_win64.exe")
    plant(root.parent / "other" / "Godot_v4.7.2-stable_win64.exe")
    assert _resolve(root, search_roots=[".."]) == inner


def test_a_file_name_is_not_trusted(machine) -> None:
    root, plant = machine
    folder = root / ".tools" / "godot"
    plant(folder / "Godot_v4.7.3-stable_win64.exe", MONO)  # a Mono build, renamed
    plant(folder / "Godot_v4.7.2-stable_win64.exe", OLDER)  # an older build, renamed
    real = plant(folder / "Godot_v4.7.1-stable_win64.exe")
    assert _resolve(root) == real


def test_a_mono_godot_first_on_path_is_passed_over(machine, tmp_path: Path) -> None:
    root, plant = machine
    plant(tmp_path / "a" / "godot.exe", MONO)
    standard = plant(tmp_path / "b" / "godot4.exe")
    path = os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    assert _resolve(root, {"PATH": path, "PATHEXT": ".EXE"}) == standard


def test_a_windowed_binary_gives_way_to_the_console_launcher_beside_it(
    machine, tmp_path: Path
) -> None:
    # Godot for Windows ships X.exe with X_console.exe; tools/godot_resolver.ps1 swaps
    # the first for the second, so both resolvers must.
    root, plant = machine
    windowed = plant(tmp_path / "one" / "Godot_v4.7.2-stable_win64.exe")
    console = plant(tmp_path / "one" / "Godot_v4.7.2-stable_win64_console.exe")
    assert godot_binary.console_twin(windowed) == console
    assert godot_binary.console_twin(console) == console
    assert _resolve(root, {"GODOT_BIN": str(windowed)}) == console
    assert _resolve(root, {"GODOT4_EXE": str(windowed)}) == console
    on_path = plant(tmp_path / "bin" / "godot.exe")
    env = {"PATH": str(on_path.parent), "PATHEXT": ".EXE"}
    assert _resolve(root, env) == on_path  # no twin: used as it is
    twin = plant(tmp_path / "bin" / "godot_console.exe")
    assert _resolve(root, env) == twin
    alone = plant(tmp_path / "two" / "godot")
    assert godot_binary.console_twin(alone) == alone


@pytest.mark.skipif(os.name != "nt", reason="the Windows release names")
def test_on_windows_only_the_64_bit_release_is_a_candidate(machine) -> None:
    root, plant = machine
    folder = root / ".tools" / "godot"
    for name in ("Godot_v4.7.2-stable_win32.exe", "Godot_v4.7.2-stable_windows_arm64.exe"):
        plant(folder / name)
    assert godot_binary.folder_candidates(folder, "4.7") == []
    wanted = plant(folder / "Godot_v4.7.2-stable_win64.exe")
    assert godot_binary.folder_candidates(folder, "4.7") == [wanted]


def test_godot_version_overrides_kit_json(machine) -> None:
    root, plant = machine
    old = plant(root / ".tools" / "godot" / "Godot_v4.6.1-stable_win64.exe", OLDER)
    plant(root / ".tools" / "godot" / "Godot_v4.7.2-stable_win64.exe")
    assert _resolve(root, {"GODOT_VERSION": "4.6"}) == old
    # 4.7 must not accept 4.70 or 4.75.
    assert godot_binary.incompatible(old, "4.6") is None
    fake = plant(root / "x" / "godot.exe", "4.70.0.stable.official.0")
    assert "needs Godot 4.7" in (godot_binary.incompatible(fake, "4.7") or "")
    with pytest.raises(godot_binary.GodotNotFoundError, match="GODOT_VERSION must be"):
        _resolve(root, {"GODOT_VERSION": "latest"})


def test_nothing_found_says_what_was_skipped_and_what_to_do(machine) -> None:
    root, plant = machine
    plant(root / ".tools" / "godot" / "Godot_v4.7.2-stable_win64.exe", OLDER)
    with pytest.raises(godot_binary.GodotNotFoundError) as caught:
        _resolve(root)
    message = str(caught.value)
    assert "Godot 4.7 (standard build) not found" in message
    assert "GODOT_BIN" in message and "install_godot.ps1" in message
    assert f"Searched: {root / '.tools' / 'godot'}; then godot/godot4" in message
    assert "Skipped:" in message and "reported '4.6.1.stable.official.def456'" in message


def test_search_roots_may_be_relative_or_use_environment_variables(
    machine, tmp_path: Path, monkeypatch
) -> None:
    root, plant = machine
    inside = plant(root / "engines" / "Godot_v4.7.2-stable_win64.exe")
    assert _resolve(root, search_roots=["engines"]) == inside
    monkeypatch.setenv("KIT_TEST_ENGINES", str(tmp_path / "shared"))
    shared = plant(tmp_path / "shared" / "godot" / "Godot_v4.7.2-stable_win64.exe")
    variable = "%KIT_TEST_ENGINES%/godot" if os.name == "nt" else "$KIT_TEST_ENGINES/godot"
    assert _resolve(root, search_roots=[variable, "engines"]) == shared


@pytest.mark.parametrize("argument", ["--help", "--bogus", "x"])
def test_the_program_takes_no_argument(argument: str, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["godot_binary.py", argument])
    assert godot_binary.main() == 2
    said = capsys.readouterr()
    assert said.out == "" and said.err.startswith("usage: uv run python tools/ci/godot_binary.py")


@pytest.mark.real_godot
def test_the_real_binary_answers_its_version() -> None:
    # The Godot the PowerShell resolver finds here, from GODOT_BIN, .tools/godot or PATH:
    # the Python one must find the same file.
    real = _gate_fixtures.real_godot()
    if real is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    reported = godot_binary.version_text(Path(real))
    assert reported.split(".")[0].isdigit()
    assert godot_binary.resolve(kit_config.load()) == os.path.abspath(real)


CACHE_ENTRY = """{
"base": &"RefCounted",
"class": &"%s",
"icon": "",
"is_abstract": false,
"is_tool": false,
"language": &"GDScript",
"path": "%s"
}"""


def _cache(root: Path, entries: dict[str, str]) -> None:
    body = ", ".join(CACHE_ENTRY % pair for pair in entries.items())
    path = root / class_cache.CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"list=Array[Dictionary]([{body}])\n", encoding="utf-8")


def _scripts(root: Path, files: dict[str, str]) -> kit_config.KitConfig:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return kit_config.load(root)


def test_class_names_are_read_from_every_folder_godot_scans(tmp_path: Path) -> None:
    config = _scripts(
        tmp_path,
        {
            "scripts/grid.gd": "class_name Grid\nextends RefCounted\n",
            "scripts/tool.gd": '@tool\n@icon("res://i.svg") class_name Painter extends Node\n',
            "scripts/plain.gd": "extends Node\n# class_name NotThis\n",
            "addons/lib/runner.gd": "class_name LibRunner\n",  # excluded as source, still scanned
            "export/.gdignore": "",
            "export/old.gd": "class_name Exported\n",
            ".godot/x.gd": "class_name Hidden\n",
        },
    )
    assert class_cache.classes_on_disk(config) == {
        ("Grid", "res://scripts/grid.gd"),
        ("Painter", "res://scripts/tool.gd"),
        ("LibRunner", "res://addons/lib/runner.gd"),
    }


def test_the_cache_is_stale_when_missing_or_when_a_class_moved(tmp_path: Path) -> None:
    config = _scripts(tmp_path, {"scripts/grid.gd": "class_name Grid\n"})
    assert class_cache.is_stale(config)  # a fresh clone has no cache
    _cache(tmp_path, {"Grid": "res://scripts/grid.gd"})
    assert class_cache.classes_in_cache(tmp_path / class_cache.CACHE) == {
        ("Grid", "res://scripts/grid.gd")
    }
    assert not class_cache.is_stale(config)
    (tmp_path / "scripts" / "grid.gd").rename(tmp_path / "scripts" / "board.gd")
    assert class_cache.is_stale(config)  # moved
    _cache(tmp_path, {"Grid": "res://scripts/board.gd"})
    assert not class_cache.is_stale(config)
    (tmp_path / "scripts" / "new.gd").write_text("class_name Fresh\n", encoding="utf-8")
    assert class_cache.is_stale(config)  # added
    (tmp_path / "scripts" / "new.gd").unlink()
    (tmp_path / "scripts" / "board.gd").write_text("class_name Board\n", encoding="utf-8")
    assert class_cache.is_stale(config)  # renamed


@pytest.fixture(autouse=True)
def private_locks(tmp_path: Path, monkeypatch) -> None:
    """Keep these tests' lock files out of the machine-wide slot folder."""
    monkeypatch.setenv("GODOT_TEST_SLOT_DIR", str(tmp_path / "locks"))


def test_two_runs_in_one_checkout_import_one_at_a_time(tmp_path: Path, monkeypatch) -> None:
    config = _scripts(tmp_path, {"scripts/grid.gd": "class_name Grid\n"})
    imports: list[str] = []

    def fake_refresh(_godot, _config) -> None:
        imports.append("import")
        _cache(tmp_path, {"Grid": "res://scripts/grid.gd"})

    monkeypatch.setattr(class_cache, "refresh", fake_refresh)
    monkeypatch.setattr(class_cache, "LOCK_POLL_SECONDS", 0.05)
    outcome: list[bool] = []
    waiting = threading.Thread(target=lambda: outcome.append(class_cache.ensure("godot", config)))
    with class_cache.import_lock(config):  # another run is importing this checkout
        waiting.start()
        waiting.join(timeout=0.5)
        assert waiting.is_alive() and imports == []
        _cache(tmp_path, {"Grid": "res://scripts/grid.gd"})  # ...and finishes
    waiting.join(timeout=10)
    # The cache is fresh by the time the lock is free: no second import.
    assert outcome == [False] and imports == []
    # A different checkout has its own lock.
    other = _scripts(tmp_path / "other", {"a.gd": "class_name A\n"})
    with class_cache.import_lock(config), class_cache.import_lock(other):
        pass


def test_refresh_imports_and_repeats_once_after_a_noisy_first_pass(
    tmp_path: Path, monkeypatch
) -> None:
    config = _scripts(tmp_path, {"scripts/grid.gd": "class_name Grid\n"})
    outputs = ['SCRIPT ERROR: Parse Error: Could not find type "Grid"', "ok"]
    calls: list[list[str]] = []

    def fake_run(command, timeout, cwd=None, env=None):
        calls.append(command)
        if len(calls) == 2:
            _cache(tmp_path, {"Grid": "res://scripts/grid.gd"})
        return 0, outputs[len(calls) - 1]

    monkeypatch.setattr(class_cache.slot_pool, "run_captured", fake_run)
    assert class_cache.ensure("godot", config) is True
    assert calls == [["godot", "--headless", "--import"]] * 2
    assert class_cache.ensure("godot", config) is False
    assert len(calls) == 2


def test_refresh_fails_loudly_when_the_import_fails_or_hangs(tmp_path: Path, monkeypatch) -> None:
    config = _scripts(tmp_path, {"scripts/grid.gd": "class_name Grid\n"})
    monkeypatch.setattr(class_cache.slot_pool, "run_captured", lambda *_a, **_k: (1, "boom"))
    with pytest.raises(RuntimeError, match="exited 1:\nboom"):
        class_cache.refresh("godot", config)
    monkeypatch.setattr(class_cache.slot_pool, "run_captured", lambda *_a, **_k: (None, "..."))
    with pytest.raises(RuntimeError, match="did not finish"):
        class_cache.refresh("godot", config)


def test_a_cache_still_stale_after_import_warns_and_lets_the_tests_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = _scripts(
        tmp_path,
        {"scripts/grid.gd": "class_name Grid\n", "scripts/broken.gd": "class_name Broken\n"},
    )
    _cache(tmp_path, {"Grid": "res://scripts/grid.gd", "Old": "res://scripts/old.gd"})
    monkeypatch.setattr(class_cache.slot_pool, "run_captured", lambda *_a, **_k: (0, "ok"))
    class_cache.refresh("godot", config)
    warning = capsys.readouterr().err
    assert "still differs from the scripts on disk" in warning
    assert "missing from cache: Broken res://scripts/broken.gd" in warning
    assert "only in cache: Old res://scripts/old.gd" in warning


def test_an_import_that_writes_no_cache_is_followed_by_an_editor_scan(
    tmp_path: Path, monkeypatch
) -> None:
    config = _scripts(tmp_path, {"scripts/grid.gd": "class_name Grid\n"})
    calls: list[list[str]] = []

    def fake_run(command, timeout, cwd=None, env=None):
        calls.append(command[1:])
        if "--editor" in command:
            _cache(tmp_path, {"Grid": "res://scripts/grid.gd"})
        return 0, "ok"

    monkeypatch.setattr(class_cache.slot_pool, "run_captured", fake_run)
    class_cache.refresh("godot", config)
    assert calls == [["--headless", "--import"], ["--headless", "--editor", "--quit-after", "1000"]]
    assert not class_cache.is_stale(config)


def test_cache_entries_for_other_languages_are_not_compared(tmp_path: Path) -> None:
    config = _scripts(tmp_path, {"scripts/grid.gd": "class_name Grid\n"})
    _cache(tmp_path, {"Grid": "res://scripts/grid.gd", "Native": "res://scripts/Native.cs"})
    assert class_cache.differences(config) == []
