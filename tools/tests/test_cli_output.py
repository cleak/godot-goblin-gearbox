"""Every gate prints its findings whatever the console's code page.

Coding agents read a tool's output through a pipe. On Windows that pipe is cp1252, and a
finding with any other character in it (a path, a comment) raised UnicodeEncodeError in
the middle of the report. tools/test_shards.py has the same test beside its fixtures.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import check_architecture  # noqa: E402
import check_test_wallclock  # noqa: E402
import check_typed_ternaries  # noqa: E402
import gate_support  # noqa: E402
from _legacy_console import LegacyConsole  # noqa: E402


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_stand_in_console_fails_the_way_a_windows_pipe_does(monkeypatch) -> None:
    LegacyConsole(monkeypatch)
    try:
        print("日本語")
    except UnicodeEncodeError:
        pass
    else:
        raise AssertionError("the stand-in console accepted text outside cp1252")
    console = LegacyConsole(monkeypatch)
    gate_support.plain_output()
    print("日本語 → ✓ and plain")
    assert console.text().strip() == "\\u65e5\\u672c\\u8a9e \\u2192 \\u2713 and plain"


def test_typed_ternaries_names_a_file_with_a_name_outside_the_code_page(
    tmp_path: Path, monkeypatch
) -> None:
    finding = "var names: Array[String] = load_names() if ok else []\n"
    root = _write(tmp_path, {"scripts/game/ゲーム.gd": finding})
    console = LegacyConsole(monkeypatch)
    assert check_typed_ternaries.main([], root=root) == 1
    assert "scripts/game/\\u30b2\\u30fc\\u30e0.gd:1: untyped [] / {}" in console.text()


def test_wall_clock_prints_a_line_with_a_comment_outside_the_code_page(
    tmp_path: Path, monkeypatch
) -> None:
    wait = "func test_x() -> void:\n\tOS.delay_msec(100)  # 待つ → slow\n"
    root = _write(tmp_path, {"test/unit/test_x.gd": wait})
    console = LegacyConsole(monkeypatch)
    assert check_test_wallclock.main([], root=root) == 1
    said = console.text()
    assert "test/unit/test_x.gd:2: wall-clock wait: OS.delay_msec(100)  # \\u5f85\\u3064" in said
    assert "1 finding(s)." in said


def test_architecture_names_a_file_with_a_name_outside_the_code_page(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = {"modules": {"core": {"globs": ["scripts/core/**"], "pure": True}}}
    root = _write(
        tmp_path,
        {
            "scripts/core/格子.gd": "extends Node\n",
            check_architecture.MANIFEST: json.dumps(manifest),
        },
    )
    shutil.copy2(TOOLS / "ci" / "node_classes.txt", root / check_architecture.NODE_CLASSES)
    console = LegacyConsole(monkeypatch)
    assert check_architecture.main([], root=root) == 1
    said = console.text()
    assert "scripts/core/\\u683c\\u5b50.gd: extends Node, a Node, in pure module core" in said


def test_a_broken_kit_json_is_one_line_and_exit_2_for_every_gate(tmp_path: Path, capsys) -> None:
    root = _write(tmp_path, {"kit.json": "{broken", "test/unit/test_x.gd": "extends Node\n"})
    _write(root, {check_architecture.MANIFEST: json.dumps({"modules": {}})})
    for gate in (check_typed_ternaries, check_test_wallclock, check_architecture):
        assert gate.main([], root=root) == 2, gate.__name__
        error = capsys.readouterr().err
        assert error.count("\n") == 1 and "kit.json is not valid JSON" in error, gate.__name__
