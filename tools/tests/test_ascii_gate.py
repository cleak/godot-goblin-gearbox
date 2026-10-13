"""tools/test_scripts_ascii.ps1: only BOM-less ASCII PowerShell passes, judged by the bytes."""

import json
from pathlib import Path

import pytest
from _gate_fixtures import NEEDS_PWSH, copy_tools, linux_only, run_tool, write, write_kit_json

pytestmark = NEEDS_PWSH

SCRIPT = "Write-Host 'hi'\r\n# a second line\r\n"
EM_DASH = "—"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    copy_tools(root, "test_scripts_ascii.ps1")
    write_kit_json(root)
    return root


def _run(root: Path) -> tuple[int, str]:
    result = run_tool(root, "test_scripts_ascii.ps1")
    assert result.stderr.strip() == "", result.stderr
    return result.returncode, result.stdout


def test_ascii_scripts_pass_and_are_counted(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "scripts" / "deploy.psm1", SCRIPT)
    write(root / ".githooks" / "pre-commit.ps1", SCRIPT)  # hook folders are checked too
    code, output = _run(root)
    assert code == 0, output
    # The three shared tools, the guard itself, and the two above.
    assert "ASCII guard: CLEAN (6 PowerShell script(s))" in output


BAD_BYTES = {
    # name: (file contents, what the guard says about it)
    "utf8_em_dash": (SCRIPT.encode() + f"# dash {EM_DASH} here\r\n".encode(), "line 3: U+2014"),
    "windows_1252": (SCRIPT.encode() + b"# dash \x97 here\r\n", "line 3: byte 0x97 (not UTF-8)"),
    "utf8_bom": (b"\xef\xbb\xbf" + SCRIPT.encode(), "UTF-8 BOM present"),
    # What Windows PowerShell 5.1's `>` and Out-File write.
    "utf16_le_bom": (b"\xff\xfe" + SCRIPT.encode("utf-16-le"), "UTF-16 BOM present"),
    "utf16_be_bom": (b"\xfe\xff" + SCRIPT.encode("utf-16-be"), "UTF-16 BOM present"),
    "utf16_no_bom": (SCRIPT.encode("utf-16-le"), "NUL byte present"),
}


@pytest.mark.parametrize(("contents", "message"), BAD_BYTES.values(), ids=BAD_BYTES.keys())
def test_anything_but_bomless_ascii_fails(tmp_path: Path, contents: bytes, message: str) -> None:
    root = _project(tmp_path)
    (root / "tools" / "seeded.ps1").write_bytes(contents)
    code, output = _run(root)
    assert code == 1, output
    assert "FAIL  tools/seeded.ps1" in output
    assert message in output
    assert "1 of 5 script(s) are not BOM-less ASCII" in output


def test_bom_and_characters_are_both_reported(tmp_path: Path) -> None:
    root = _project(tmp_path)
    many = "".join(f"# {EM_DASH} line {index}\r\n" for index in range(14))
    (root / "tools" / "seeded.ps1").write_bytes(b"\xef\xbb\xbf" + many.encode())
    code, output = _run(root)
    assert code == 1, output
    assert "UTF-8 BOM present" in output
    assert "line 1: U+2014" in output and "line 10: U+2014" in output
    assert "line 11: U+2014" not in output and "... and 4 more" in output  # a long list is cut


@linux_only("a folder that holds a.ps1 beside A.ps1")
def test_scripts_that_differ_only_in_letter_case_are_both_checked(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "tools" / "seeded.ps1", SCRIPT)
    (root / "tools" / "SEEDED.ps1").write_bytes(f"# dash {EM_DASH} here\r\n".encode())
    code, output = _run(root)
    assert code == 1, output
    assert "FAIL  tools/SEEDED.ps1" in output and "line 1: U+2014" in output
    assert "1 of 6 script(s) are not BOM-less ASCII" in output


def test_folders_the_project_does_not_author_are_not_checked(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bad = f"# {EM_DASH}\r\n".encode()
    for folder in ("addons/theirs", "artifacts", ".venv/Lib", "ignored"):
        (root / folder).mkdir(parents=True)
        (root / folder / "bad.ps1").write_bytes(bad)
    write(root / "ignored" / ".gdignore", "")
    code, output = _run(root)
    assert code == 0, output
    assert "CLEAN (4 PowerShell script(s))" in output


def test_in_an_installed_project_your_own_script_may_carry_a_bom(tmp_path: Path) -> None:
    """UTF-8 with a BOM is valid PowerShell on 5.1 and 7. The kit keeps its own files
    BOM-less; in someone's project that house rule is for the kit's files only."""
    root = _project(tmp_path)
    kit_files = {f"tools/{path.name}": "x" for path in (root / "tools").glob("*.ps1")}
    write(root / "tools" / "kit_manifest.json", json.dumps({"files": kit_files}))
    mine = root / "tools" / "release.ps1"
    mine.write_bytes(b"\xef\xbb\xbf" + f"# ship it {EM_DASH} now\r\n".encode())
    code, output = _run(root)
    assert code == 0, output
    assert "CLEAN (5 PowerShell script(s); 1 of your own are UTF-8 with a BOM)" in output

    # What does break Windows PowerShell 5.1 still fails: the same text with no BOM.
    mine.write_bytes(f"# ship it {EM_DASH} now\r\n".encode())
    code, output = _run(root)
    assert code == 1 and "FAIL  tools/release.ps1" in output and "line 1: U+2014" in output
    assert "A script of your own may be UTF-8 with a BOM instead." in output
    # A kit file is still held to the kit's rule.
    mine.unlink()
    guard = root / "tools" / "kit_config.ps1"
    guard.write_bytes(b"\xef\xbb\xbf" + guard.read_bytes())
    code, output = _run(root)
    assert code == 1 and "FAIL  tools/kit_config.ps1" in output and "UTF-8 BOM present" in output


def test_no_scripts_and_no_config_are_failures(tmp_path: Path) -> None:
    """Zero files checked must not read as a pass."""
    root = _project(tmp_path)
    write_kit_json(root, source={"exclude": ["tools"], "include": []})
    code, output = _run(root)
    assert code == 1 and "No PowerShell scripts found to check" in output

    write(root / "kit.json", "{ not json")
    code, output = _run(root)
    assert code == 1 and "ASCII guard: kit.json is not valid JSON" in output
