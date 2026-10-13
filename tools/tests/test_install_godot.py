"""tools/install_godot.ps1 installs nothing it could not verify.

These run offline against local archives (-Source), for every system's download
(-Platform, -Architecture) on whatever machine this is. The real download is exercised by
running the script itself.
"""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest
from _gate_fixtures import (
    NEEDS_PWSH,
    REPO,
    clean_env,
    copy_tools,
    fake_godot,
    old_powershell,
    posix_only,
    run_powershell,
    run_tool,
    write_kit_json,
)

sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "ci"))
import godot_binary  # noqa: E402
import kit_config  # noqa: E402

pytestmark = NEEDS_PWSH

# (platform, architecture): one for each official download.
SYSTEMS = [("windows", "x86_64"), ("linux", "x86_64"), ("linux", "arm64"), ("macos", "arm64")]
every_system = pytest.mark.parametrize("system", SYSTEMS, ids=["-".join(pair) for pair in SYSTEMS])


class Release:
    """The official download of Godot 4.7.2 for one system, as the installer knows it."""

    def __init__(self, system: tuple[str, str]) -> None:
        self.platform, self.architecture = system
        self.build = kit_config.godot_build(*system)
        self.name = f"Godot_v4.7.2-stable_{godot_binary.RELEASE_TAGS[self.build]}"
        self.archive = f"{self.name}.zip"
        self.files = [self.name]
        if self.platform == "windows":
            self.archive = f"{self.name}.exe.zip"
            self.files = [f"{self.name}.exe", f"{self.name}_console.exe"]
        elif self.platform == "macos":
            self.files = ["Godot.app/Contents/MacOS/Godot"]
        self.program = self.files[-1]
        self.key = f"{self.build}_sha512"

    @property
    def switches(self) -> tuple[str, ...]:
        return ("-Platform", self.platform, "-Architecture", self.architecture)


def _project(tmp_path: Path, release: Release, sha512: str = "") -> Path:
    root = tmp_path / "project"
    copy_tools(root, "install_godot.ps1")
    write_kit_json(root, godot={"version": "4.7.2", release.key: sha512, "search_roots": []})
    return root


def _archive(tmp_path: Path, release: Release, entries: dict[str, str]) -> Path:
    path = tmp_path / release.archive
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    return path


def _sha512(path: Path) -> str:
    return hashlib.sha512(path.read_bytes()).hexdigest()


def _installed(root: Path) -> list[str]:
    folder = root / ".tools" / "godot"
    return sorted(entry.name for entry in folder.iterdir()) if folder.is_dir() else []


def _install(root: Path, release: Release, *switches: str, **env: str):
    return run_tool(root, "install_godot.ps1", *release.switches, *switches, env=clean_env(**env))


@every_system
def test_empty_pin_is_refused_before_anything_is_fetched(tmp_path: Path, system) -> None:
    release = Release(system)
    root = _project(tmp_path, release)
    result = _install(root, release, "-Source", str(tmp_path / "never_read.zip"))
    assert result.returncode == 1, result.stdout + result.stderr
    said = result.stdout.strip().splitlines()
    assert len(said) == 1, result.stdout  # one line, and it names the fix
    assert said[0].startswith(f"install_godot: kit.json godot.{release.key} is empty")
    assert f"Add the SHA-512 of {release.archive}" in said[0] and "-SkipChecksum" in said[0]
    assert _installed(root) == []


@every_system
def test_a_pin_for_another_system_does_not_verify_this_one(tmp_path: Path, system) -> None:
    """A kit.json from before this system had a key: refused, never installed unverified."""
    release = Release(system)
    root = _project(tmp_path, release)
    others = {
        f"{build}_sha512": "ab" * 64 for build in kit_config.GODOT_BUILDS if build != release.build
    }
    write_kit_json(root, godot={"version": "4.7.2", **others})
    archive = _archive(tmp_path, release, dict.fromkeys(release.files, "x"))
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"kit.json godot.{release.key} is empty" in result.stdout
    assert _installed(root) == []


@every_system
def test_malformed_pin_is_refused(tmp_path: Path, system) -> None:
    release = Release(system)
    root = _project(tmp_path, release, sha512="abc123")
    result = _install(root, release)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"kit.json godot.{release.key} is not a SHA-512" in result.stdout


@every_system
def test_checksum_mismatch_installs_nothing(tmp_path: Path, system) -> None:
    release = Release(system)
    archive = _archive(tmp_path, release, dict.fromkeys(release.files, "a"))
    root = _project(tmp_path, release, sha512="0" * 128)
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"SHA-512 mismatch for {release.archive}; nothing was installed." in result.stdout
    assert f"expected (kit.json godot.{release.key}): {'0' * 128}" in result.stdout
    assert _sha512(archive) in result.stdout  # the actual digest, to compare by eye
    assert _installed(root) == []


@every_system
def test_verified_archive_without_the_executables_installs_nothing(tmp_path: Path, system) -> None:
    release = Release(system)
    archive = _archive(tmp_path, release, {"readme.txt": "not godot"})
    root = _project(tmp_path, release, sha512=_sha512(archive))
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SHA-512 matches kit.json" in result.stdout
    assert f"does not hold {' and '.join(release.files)}" in result.stdout
    assert _installed(root) == []


def test_windows_needs_both_programs(tmp_path: Path) -> None:
    release = Release(("windows", "x86_64"))
    archive = _archive(tmp_path, release, {release.files[0]: "the windowed one alone"})
    root = _project(tmp_path, release, sha512=_sha512(archive))
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1 and "does not hold" in result.stdout, result.stdout
    assert _installed(root) == []


@every_system
def test_archive_entry_outside_the_install_folder_is_refused(tmp_path: Path, system) -> None:
    release = Release(system)
    entries = {"../../escaped.txt": "x"} | dict.fromkeys(release.files, "b")
    archive = _archive(tmp_path, release, entries)
    root = _project(tmp_path, release, sha512=_sha512(archive))
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "points outside the install folder" in result.stdout
    assert _installed(root) == []
    assert not list(tmp_path.rglob("escaped.txt"))


@every_system
def test_archive_that_does_not_run_is_rolled_back(tmp_path: Path, system) -> None:
    """The executables are there but are not Godot: no broken install is left behind."""
    release = Release(system)
    archive = _archive(tmp_path, release, dict.fromkeys(release.files, "not a program"))
    root = _project(tmp_path, release, sha512=_sha512(archive))
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"the archive's {release.program} does not run as Godot 4.7.2" in result.stdout
    assert _installed(root) == []


@every_system
def test_failed_reinstall_puts_the_previous_install_back(tmp_path: Path, system) -> None:
    release = Release(system)
    archive = _archive(tmp_path, release, dict.fromkeys(release.files, "new"))
    root = _project(tmp_path, release, sha512=_sha512(archive))
    previous = root / ".tools" / "godot" / release.name
    for name in (*release.files, "marker.txt"):
        (previous / name).parent.mkdir(parents=True, exist_ok=True)
        (previous / name).write_text("old", encoding="ascii")
    result = _install(root, release, "-Source", str(archive), "-Force")
    assert result.returncode == 1, result.stdout + result.stderr
    assert _installed(root) == [release.name]  # no scratch folder left either
    assert (previous / "marker.txt").read_text() == "old"
    assert (previous / release.program).read_text() == "old"


def test_skip_checksum_says_so_and_goes_on(tmp_path: Path) -> None:
    release = Release(("linux", "x86_64"))
    archive = _archive(tmp_path, release, {"readme.txt": "not godot"})
    root = _project(tmp_path, release)  # no pin at all
    result = _install(root, release, "-Source", str(archive), "-SkipChecksum")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "WARNING: -SkipChecksum: the archive will be installed without verification." in (
        result.stdout
    )
    assert "SHA-512 matches" not in result.stdout and "does not hold" in result.stdout


def test_a_system_godot_publishes_no_build_for_is_one_line(tmp_path: Path) -> None:
    release = Release(("linux", "x86_64"))
    root = _project(tmp_path, release, sha512="ab" * 64)
    none = run_tool(root, "install_godot.ps1", "-Platform", "linux", "-Architecture", "riscv64")
    assert none.returncode == 1, none.stdout + none.stderr
    assert none.stdout.strip() == (
        "install_godot: Godot publishes no build for linux riscv64. Build or install Godot 4.7.2 "
        "yourself and set GODOT_BIN to its executable."
    )
    unknown = run_tool(root, "install_godot.ps1", "-Platform", "beos")
    assert unknown.returncode == 1, unknown.stdout + unknown.stderr
    expected = "install_godot: -Platform must be windows, linux or macos; it is 'beos'."
    assert unknown.stdout.strip() == expected
    assert _installed(root) == []


def test_a_missing_source_file_is_one_line(tmp_path: Path) -> None:
    release = Release(("linux", "x86_64"))
    root = _project(tmp_path, release, sha512="ab" * 64)
    result = _install(root, release, "-Source", str(tmp_path / "gone.zip"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "-Source is neither a URL nor an existing file" in result.stdout
    assert _installed(root) == []
    garbage = tmp_path / release.archive
    garbage.write_bytes(b"this is no zip archive")
    root = _project(tmp_path, release, sha512=_sha512(garbage))
    broken = _install(root, release, "-Source", str(garbage))
    assert broken.returncode == 1 and "is not a readable zip archive" in broken.stdout
    assert _installed(root) == []


def test_a_powershell_that_cannot_verify_is_refused_before_anything_is_fetched(
    tmp_path: Path,
) -> None:
    """PowerShell 7.0 on a Linux with OpenSSL 3 aborts where it computes the checksum, and
    leaves the download behind: the installer says so first, in one line."""
    release = Release(("linux", "x86_64"))
    archive = _archive(tmp_path, release, dict.fromkeys(release.files, "x"))
    root = _project(tmp_path, release, sha512=_sha512(archive))
    old_powershell(root, "it cannot compute a checksum")
    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 1, result.stdout + result.stderr
    said = result.stdout.strip().splitlines()
    assert len(said) == 1, result.stdout
    assert said[0].startswith("install_godot: PowerShell 7.")
    assert ": it cannot compute a checksum. Fix: " in said[0]
    assert result.stderr.strip() == ""
    assert not (root / ".tools").exists()  # no folder for the download either


POSIX_SYSTEMS = [system for system in SYSTEMS if system[0] != "windows"]


@posix_only("a stand-in Godot that runs without being a Windows .exe")
@pytest.mark.parametrize("system", POSIX_SYSTEMS, ids=["-".join(pair) for pair in POSIX_SYSTEMS])
def test_a_verified_archive_is_installed_executable_and_found(tmp_path: Path, system) -> None:
    """The whole path, with a stand-in for the program: verified, unpacked, made executable
    (the archive carries no permission), asked for its version, and then resolved."""
    release = Release(system)
    stand_in = fake_godot(tmp_path / "bin").read_text(encoding="utf-8")
    archive = _archive(tmp_path, release, {release.program: stand_in, "extra/readme.txt": "x"})
    root = _project(tmp_path, release, sha512=_sha512(archive))
    program = root / ".tools" / "godot" / release.name / release.program

    result = _install(root, release, "-Source", str(archive))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SHA-512 matches kit.json." in result.stdout
    assert result.stdout.strip().endswith(f"Godot 4.7.2 installed: {program}")
    assert _installed(root) == [release.name]
    assert program.stat().st_mode & 0o111 == 0o111

    # Both resolvers find it, for that system.
    minor = "4.7"
    folder = root / ".tools" / "godot"
    assert godot_binary.folder_candidates(folder, minor, "4.7.2", release.build) == [program]
    resolved = run_powershell(
        f". $env:KIT_RESOLVER; Resolve-GodotPath {' '.join(release.switches)}",
        env=clean_env(KIT_RESOLVER=str(root / "tools" / "godot_resolver.ps1")),
    )
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    assert resolved.stdout.strip() == str(program)

    # A second run changes nothing; -Force installs again; another version is replaced.
    program.with_name("marker.txt").write_text("mine", encoding="ascii")
    again = _install(root, release, "-Source", str(tmp_path / "not_read.zip"))
    assert again.returncode == 0, again.stdout + again.stderr
    assert again.stdout.strip() == f"Godot 4.7.2 is already installed: {program}"
    assert program.with_name("marker.txt").is_file()
    stale = _install(root, release, "-Source", str(archive), FAKE_GODOT_VERSION="4.7.1.stable")
    assert stale.returncode == 1, stale.stdout + stale.stderr
    assert "does not run as 4.7.2; installing it again." in stale.stdout
    assert program.with_name("marker.txt").is_file()  # the failed attempt put it back
    forced = _install(root, release, "-Source", str(archive), "-Force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert not program.with_name("marker.txt").exists()

    # GODOT_BIN wins over the install, and the installer says so.
    noted = _install(root, release, "-Source", str(archive), "-Force", GODOT_BIN="/elsewhere/godot")
    assert noted.returncode == 0, noted.stdout + noted.stderr
    assert "Note: GODOT_BIN is set ('/elsewhere/godot') and wins over this install." in noted.stdout

    # A program that lost its permission is no install: it is put right.
    program.chmod(0o644)
    repaired = _install(root, release, "-Source", str(archive))
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "installing it again" in repaired.stdout
    assert program.stat().st_mode & 0o111 == 0o111
