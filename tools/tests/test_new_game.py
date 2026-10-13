"""tools/new_game.ps1 writes the title and blurb so that Godot reads back what was typed."""

from pathlib import Path

import pytest
from _gate_fixtures import PWSH, copy_tools, run_tool

pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 (pwsh) is not installed")

PROJECT = (
    "config_version=5\n\n[application]\n\n"
    'config/name="Template"\nrun/main_scene="res://main.tscn"\n\n[debug]\n\nx=1\n'
)
TITLE = 'Cost $5 "Quoted" \\ Game $1'


def _project(tmp_path: Path, contents: str = PROJECT) -> Path:
    root = tmp_path / "project"
    copy_tools(root, "new_game.ps1")
    (root / "project.godot").write_bytes(contents.encode("ascii"))
    return root


def _text(root: Path) -> str:
    return (root / "project.godot").read_bytes().decode("utf-8")


def test_title_and_blurb_are_escaped_not_interpreted(tmp_path: Path) -> None:
    """A "$", a quote or a backslash is text: not a regex group, not the end of the string."""
    root = _project(tmp_path)
    result = run_tool(root, "new_game.ps1", "-Title", TITLE, "-Blurb", 'Say "hi" for $0')
    assert result.returncode == 0, result.stdout + result.stderr
    assert _text(root) == PROJECT.replace(
        'config/name="Template"\n',
        'config/name="Cost $5 \\"Quoted\\" \\\\ Game $1"\n'
        'config/description="Say \\"hi\\" for $0"\n',
    )

    # Again, with other values: the lines are replaced, not added twice.
    again = run_tool(root, "new_game.ps1", "-Title", "Second", "-Blurb", "Two")
    assert again.returncode == 0, again.stdout + again.stderr
    text = _text(root)
    assert 'config/name="Second"\nconfig/description="Two"\n' in text
    assert text.count("config/name=") == 1 and text.count("config/description=") == 1

    # Without a blurb the description is left as it is, and the run says what it still is:
    # in a copy of the template it describes the template.
    assert "config/description" not in again.stdout
    titled = run_tool(root, "new_game.ps1", "-Title", "Third")
    assert titled.returncode == 0 and 'config/name="Third"\nconfig/description="Two"\n' in _text(
        root
    )
    assert 'config/description still says "Two"; -Blurb "..." replaces it.' in titled.stdout


def test_crlf_project_file_keeps_its_line_endings(tmp_path: Path) -> None:
    root = _project(tmp_path, PROJECT.replace("\n", "\r\n"))
    result = run_tool(root, "new_game.ps1", "-Title", "Windows", "-Blurb", "Line endings stay")
    assert result.returncode == 0, result.stdout + result.stderr
    text = _text(root)
    assert 'config/name="Windows"\r\nconfig/description="Line endings stay"\r\n' in text
    assert text.replace("\r\n", "").count("\n") == 0  # no bare LF crept in
    assert not (root / "project.godot").read_bytes().startswith(b"\xef\xbb\xbf")


def _with_tests(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    """A project that holds the kit's own tests beside the game's; returns what must stay."""
    root = _project(tmp_path)
    kit_tests = (
        "tools/tests/test_check_gate.py",
        "tools/tests/__pycache__/test_check_gate.cpython-312.pyc",
        "test/kit/devtools/test_devtools_input.gd",
        "test/kit/devtools/test_devtools_input.gd.uid",
    )
    game = (
        "test/unit/test_board.gd",
        "test/sequences/smoke.json",
        "tools/ci/suites.py",
        "tools/tests.md",
        "scripts/kit/tests/notes.gd",
    )
    for relative in (*kit_tests, *game):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"# {relative}\n".encode("ascii"))
    kept = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "project.godot"
        and path.relative_to(root).as_posix() not in kit_tests
    }
    return root, kept


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != "project.godot"
    }


def test_remove_kit_tests_deletes_the_kits_own_tests_and_nothing_else(tmp_path: Path) -> None:
    root, kept = _with_tests(tmp_path)
    result = run_tool(root, "new_game.ps1", "-Title", "Mine", "-RemoveKitTests")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "removed tools/tests/ (2 files)" in result.stdout
    assert "removed test/kit/ (2 files)" in result.stdout
    assert not (root / "tools" / "tests").exists() and not (root / "test" / "kit").exists()
    assert _files(root) == kept
    assert 'config/name="Mine"' in _text(root)

    # Again: they are gone already, which is fine.
    again = run_tool(root, "new_game.ps1", "-Title", "Mine", "-RemoveKitTests")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "tools/tests/ was already gone" in again.stdout
    assert "test/kit/ was already gone" in again.stdout
    assert _files(root) == kept


def test_kit_tests_stay_without_the_switch_or_after_a_refusal(tmp_path: Path) -> None:
    root, _ = _with_tests(tmp_path)
    everything = _files(root)
    plain = run_tool(root, "new_game.ps1", "-Title", "Mine")
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "still here (tools/tests/, test/kit/); -RemoveKitTests deletes them" in plain.stdout
    assert _files(root) == everything

    refused = run_tool(root, "new_game.ps1", "-Title", "   ", "-RemoveKitTests")
    assert refused.returncode == 1 and "removed" not in refused.stdout
    assert _files(root) == everything


def test_bad_input_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    root = _project(tmp_path)
    for args, message in (
        (("-Title", "Two\nlines"), "Title must be a single line"),
        (("-Title", "   "), "Title must contain"),
        (("-Title", "Fine", "-Blurb", "Two\nlines"), "Blurb must be a single line"),
    ):
        result = run_tool(root, "new_game.ps1", *args)
        assert result.returncode == 1, result.stdout + result.stderr
        assert f"new_game: {message}" in result.stdout
        assert result.stderr.strip() == ""
    assert _text(root) == PROJECT

    (root / "project.godot").write_text("config_version=5\n", encoding="ascii")
    nameless = run_tool(root, "new_game.ps1", "-Title", "Fine")
    assert nameless.returncode == 1 and "exactly one config/name entry" in nameless.stdout
