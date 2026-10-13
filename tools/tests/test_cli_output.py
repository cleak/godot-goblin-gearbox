"""Every gate prints its findings whatever the console's code page.

Coding agents read a tool's output through a pipe. On Windows that pipe is cp1252, and a
finding with any other character in it (a path, a comment) raised UnicodeEncodeError in
the middle of the report. tools/test_shards.py has the same test beside its fixtures.

Two kinds of test: the gates called in this process with a stand-in for that pipe
(_legacy_console.py; every system), and one gate run as a program into a real pipe, in
each encoding a system gives one (Windows' code page, the C locale of Linux and macOS).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _gate_fixtures import copy_python_tools, posix_only, windows_only

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


FINDING = "var names: Array[String] = load_names() if ok else []\n"
NAMED = "scripts/game/ゲーム.gd:1: untyped [] / {}"
# The name as six ASCII characters for each of its three: backslash, u, four digits.
ESCAPED = NAMED.encode("ascii", "backslashreplace")
WRITTEN = NAMED.encode()


def _run_gate(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[bytes]:
    """tools/check_typed_ternaries.py as a program, its output in a real pipe, on a project
    with one finding in a file whose name is outside ASCII."""
    root = _write(tmp_path / "game", {"scripts/game/ゲーム.gd": FINDING})
    copy_python_tools(root)
    shutil.copyfile(TOOLS / "check_typed_ternaries.py", root / "tools" / "check_typed_ternaries.py")
    inherited = {
        name: value
        for name, value in os.environ.items()
        if name not in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE", "LC_ALL", "LANG")
    }
    return subprocess.run(
        [sys.executable, str(root / "tools" / "check_typed_ternaries.py")],
        cwd=tmp_path,
        env=inherited | env,
        capture_output=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    ("encoding", "printed"),
    [("ascii", ESCAPED), ("cp1252", ESCAPED), ("cp437", ESCAPED), ("utf-8", WRITTEN)],
)
def test_a_gate_prints_its_findings_into_a_pipe_of_any_encoding(
    tmp_path: Path, encoding: str, printed: bytes
) -> None:
    """The stand-in console above is a model; this is the pipe itself. What the pipe's
    encoding cannot hold arrives as an escape, never as a traceback."""
    result = _run_gate(tmp_path, PYTHONIOENCODING=encoding)
    assert result.returncode == 1, result.stderr
    assert printed in result.stdout and result.stderr == b""


@windows_only("a pipe in the system's legacy code page, which is what Python gives one there")
def test_a_gate_prints_into_the_pipe_windows_gives_it(tmp_path: Path) -> None:
    result = _run_gate(tmp_path)
    assert result.returncode == 1, result.stderr
    assert ESCAPED in result.stdout and result.stderr == b""


@posix_only("the C locale, where Python writes UTF-8 all the same")
@pytest.mark.parametrize("locale", ["C", "POSIX", "C.UTF-8"])
def test_a_gate_prints_utf8_whatever_the_locale(tmp_path: Path, locale: str) -> None:
    result = _run_gate(tmp_path, LC_ALL=locale, LANG=locale)
    assert result.returncode == 1, result.stderr
    assert WRITTEN in result.stdout and result.stderr == b""


@posix_only("a file name that is bytes, which a locale without UTF-8 cannot read as text")
def test_a_gate_survives_a_locale_that_has_no_utf8_at_all(tmp_path: Path) -> None:
    """PYTHONUTF8=0 and PYTHONCOERCECLOCALE=0 take back what Python does for the C locale:
    the name is bytes it cannot decode, and still the finding is printed."""
    off = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    result = _run_gate(tmp_path, **off)
    assert result.returncode == 1, result.stderr
    assert rb".gd:1: untyped [] / {}" in result.stdout and result.stderr == b""
    assert result.stdout.isascii()


def test_a_broken_kit_json_is_one_line_and_exit_2_for_every_gate(tmp_path: Path, capsys) -> None:
    root = _write(tmp_path, {"kit.json": "{broken", "test/unit/test_x.gd": "extends Node\n"})
    _write(root, {check_architecture.MANIFEST: json.dumps({"modules": {}})})
    for gate in (check_typed_ternaries, check_test_wallclock, check_architecture):
        assert gate.main([], root=root) == 2, gate.__name__
        error = capsys.readouterr().err
        assert error.count("\n") == 1 and "kit.json is not valid JSON" in error, gate.__name__
