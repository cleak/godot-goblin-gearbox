"""tools/install_godot.ps1 installs nothing it could not verify.

These run offline against local archives (-Source). The real download is exercised by
running the script itself.
"""

import hashlib
import zipfile
from pathlib import Path

import pytest
from _gate_fixtures import PWSH, WINDOWS, copy_tools, run_tool, write_kit_json

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 tools")

NAME = "Godot_v4.7.2-stable_win64"


def _project(tmp_path: Path, sha512: str = "") -> Path:
    root = tmp_path / "project"
    copy_tools(root, "install_godot.ps1")
    write_kit_json(
        root, godot={"version": "4.7.2", "windows_x86_64_sha512": sha512, "search_roots": []}
    )
    return root


def _archive(tmp_path: Path, entries: dict[str, str]) -> Path:
    path = tmp_path / f"{NAME}.exe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    return path


def _sha512(path: Path) -> str:
    return hashlib.sha512(path.read_bytes()).hexdigest()


def _installed(root: Path) -> list[str]:
    folder = root / ".tools" / "godot"
    return sorted(entry.name for entry in folder.iterdir()) if folder.is_dir() else []


def test_empty_pin_is_refused_before_anything_is_fetched(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = run_tool(root, "install_godot.ps1", "-Source", str(tmp_path / "never_read.zip"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "windows_x86_64_sha512 is empty" in result.stdout
    assert "-SkipChecksum" in result.stdout
    assert _installed(root) == []


def test_malformed_pin_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path, sha512="abc123")
    result = run_tool(root, "install_godot.ps1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "is not a SHA-512" in result.stdout


def test_checksum_mismatch_installs_nothing(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {f"{NAME}.exe": "a", f"{NAME}_console.exe": "b"})
    root = _project(tmp_path, sha512="0" * 128)
    result = run_tool(root, "install_godot.ps1", "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SHA-512 mismatch" in result.stdout
    assert _sha512(archive) in result.stdout  # the actual digest, to compare by eye
    assert _installed(root) == []


def test_verified_archive_without_the_executables_installs_nothing(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {"readme.txt": "not godot"})
    root = _project(tmp_path, sha512=_sha512(archive))
    result = run_tool(root, "install_godot.ps1", "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SHA-512 matches kit.json" in result.stdout
    assert "does not hold" in result.stdout
    assert _installed(root) == []


def test_archive_entry_outside_the_install_folder_is_refused(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {"../../escaped.txt": "x", f"{NAME}_console.exe": "b"})
    root = _project(tmp_path, sha512=_sha512(archive))
    result = run_tool(root, "install_godot.ps1", "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "points outside the install folder" in result.stdout
    assert _installed(root) == []
    assert not list(tmp_path.rglob("escaped.txt"))


def test_archive_that_does_not_run_is_rolled_back(tmp_path: Path) -> None:
    """The executables are there but are not Godot: no broken install is left behind."""
    archive = _archive(tmp_path, {f"{NAME}.exe": "a", f"{NAME}_console.exe": "b"})
    root = _project(tmp_path, sha512=_sha512(archive))
    result = run_tool(root, "install_godot.ps1", "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "does not run as Godot 4.7.2" in result.stdout
    assert _installed(root) == []


def test_failed_reinstall_puts_the_previous_install_back(tmp_path: Path) -> None:
    archive = _archive(tmp_path, {f"{NAME}.exe": "new", f"{NAME}_console.exe": "new"})
    root = _project(tmp_path, sha512=_sha512(archive))
    previous = root / ".tools" / "godot" / NAME
    previous.mkdir(parents=True)
    for name in (f"{NAME}.exe", f"{NAME}_console.exe", "marker.txt"):
        (previous / name).write_text("old", encoding="ascii")
    result = run_tool(root, "install_godot.ps1", "-Source", str(archive), "-Force")
    assert result.returncode == 1, result.stdout + result.stderr
    assert _installed(root) == [NAME]  # no scratch folder left either
    assert (previous / "marker.txt").read_text() == "old"
    assert (previous / f"{NAME}_console.exe").read_text() == "old"
