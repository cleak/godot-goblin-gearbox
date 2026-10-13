#!/usr/bin/env python3
"""Install a pinned third-party Godot addon from its release archive.

A vendored addon is a pinned dependency: kit.json records its version and the SHA-256
of the exact upstream source archive. This module downloads that archive, checks the
hash before reading a byte of it, refuses an archive that could write outside its
folder (path traversal, symlinks, encrypted or oversized members), and swaps the new
tree in whole: on any failure the old addon is put back. It replaces only an empty
folder or the addon's own (a plugin.cfg with its name), never some other folder.

tools/install_gdunit4.py is a thin wrapper over this. To pin another addon, add a
kit.json section with `version` and `source_archive_sha256`, and a wrapper with its
own AddonSpec.

Godot writes `.uid` and `.import` files beside an addon's files; upstream archives do
not ship them. A reinstall carries the existing ones over, so ids stay stable and the
upgrade diff shows only what upstream changed.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_config  # noqa: E402

ROOT = kit_config.REPO
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?")
SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
# Files Godot generates beside the addon's own; not part of the upstream archive.
GENERATED_SUFFIXES = (".uid", ".import")


@dataclass(frozen=True)
class AddonSpec:
    """Everything that differs between one pinned addon and another."""

    key: str  # the kit.json section, e.g. "gdunit4"
    url_template: str  # "...archive/refs/tags/v{version}.zip"
    inner_glob: str  # path within the archive, e.g. "gdUnit4-{version}/addons/gdUnit4"
    destination: Path  # where the addon lives, e.g. ROOT/addons/gdUnit4
    required_files: tuple[str, ...]  # files that must exist for the install to be valid
    plugin_name: str  # `name=` in the addon's plugin.cfg: how its own folder is recognised


def load_pin(spec: AddonSpec, config: kit_config.KitConfig | None = None) -> tuple[str, str]:
    """The pinned version and source-archive SHA-256 from kit.json. Raises RuntimeError."""
    cfg = config or kit_config.load()
    section = cfg.raw.get(spec.key)
    if not isinstance(section, dict):
        raise RuntimeError(f'kit.json has no "{spec.key}" section (looked in {cfg.root})')
    version = section.get("version")
    digest = section.get("source_archive_sha256")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise RuntimeError(f"kit.json {spec.key}.version is missing or not a version: {version!r}")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise RuntimeError(
            f"kit.json {spec.key}.source_archive_sha256 is missing or not 64 hex characters"
        )
    return version, digest.lower()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _occupied(path: Path) -> bool:
    """True when something is there to check or replace: not missing, not an empty folder."""
    return path.exists() and not (path.is_dir() and not _is_link(path) and not any(path.iterdir()))


def check_replaceable(spec: AddonSpec, path: Path) -> None:
    """Refuse to replace anything but this addon's own folder. `--destination addons
    --force` would otherwise swap the whole addons folder, every other addon in it
    included, for this one."""
    if not _occupied(path):
        return
    if _is_link(path):
        raise RuntimeError(f"{spec.key} install path may not be a link or junction: {path}")
    plugin = path / "plugin.cfg"
    named = None
    if path.is_dir() and plugin.is_file():
        text = plugin.read_text(encoding="utf-8", errors="replace")
        named = re.search(r'^name\s*=\s*"([^"]*)"\s*$', text, re.MULTILINE)
    if named is None or named.group(1) != spec.plugin_name:
        raise RuntimeError(
            f"refusing to replace {path}: it is not a {spec.plugin_name} folder (no plugin.cfg "
            f'with name="{spec.plugin_name}"). The destination is the addon\'s own folder, such '
            f"as addons/{spec.plugin_name}; if this is a broken install, delete it first."
        )


def verify_install(spec: AddonSpec, path: Path, version: str) -> None:
    if _is_link(path):
        raise RuntimeError(f"{spec.key} install path may not be a link or junction: {path}")
    missing = [name for name in spec.required_files if not (path / name).is_file()]
    if missing:
        raise RuntimeError(f"{spec.key} install is incomplete; missing: " + ", ".join(missing))
    plugin = (path / "plugin.cfg").read_text(encoding="utf-8")
    reported = re.search(r'^version\s*=\s*"([^"]+)"\s*$', plugin, re.MULTILINE)
    if reported is None or reported.group(1) != version:
        found = reported.group(1) if reported else "no version"
        raise RuntimeError(f"{spec.key} at {path} reports {found}, expected {version}")


def _safe_members(spec: AddonSpec, archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    names: set[str] = set()
    total_size = 0
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if (
            not name
            # On Windows zipfile has already turned "\" into "/" in `filename`.
            or "\\" in member.orig_filename
            or ":" in name
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != name.rstrip("/")
        ):
            raise RuntimeError(f"unsafe {spec.key} archive member: {name!r}")
        if name.casefold() in names:
            raise RuntimeError(f"duplicate {spec.key} archive member: {name}")
        names.add(name.casefold())
        mode = (member.external_attr >> 16) & 0xFFFF
        if member.create_system == 3 and mode & 0o170000 == 0o120000:
            raise RuntimeError(f"{spec.key} archive contains a symbolic link: {name}")
        if member.flag_bits & 0x1:
            raise RuntimeError(f"{spec.key} archive contains an encrypted member: {name}")
        total_size += member.file_size
        if total_size > MAX_ARCHIVE_BYTES:
            raise RuntimeError(f"{spec.key} archive exceeds the uncompressed size limit")
    return members


def _extract_verified(spec: AddonSpec, payload: bytes, destination: Path, version: str) -> Path:
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        members = _safe_members(spec, archive)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.filename).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    glob = spec.inner_glob.format(version=version)
    candidates = list(destination.glob(glob))
    if len(candidates) != 1:
        raise RuntimeError(f"upstream archive did not contain exactly one {glob}")
    return candidates[0]


def _check_arguments(spec: AddonSpec, payload: bytes, expected_hash: str, version: str) -> None:
    if not SHA256_PATTERN.fullmatch(expected_hash):
        raise ValueError(f"expected {spec.key} SHA-256 must contain 64 hexadecimal characters")
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            f"{spec.key} version must be a dotted numeric version with an optional suffix"
        )
    actual_hash = hashlib.sha256(payload).hexdigest()
    if actual_hash != expected_hash.lower():
        raise RuntimeError(
            f"{spec.key} archive hash mismatch: expected {expected_hash}, got {actual_hash}"
        )


def _carry_generated(old: Path, new: Path) -> None:
    """Copy Godot's .uid / .import files from the old tree for files the new tree still has."""
    for sidecar in old.rglob("*"):
        if sidecar.suffix not in GENERATED_SUFFIXES or not sidecar.is_file():
            continue
        relative = sidecar.relative_to(old)
        target = new / relative
        if (new / relative.with_suffix("")).is_file() and not target.exists():
            shutil.copy2(sidecar, target)


def install_payload(
    spec: AddonSpec,
    payload: bytes,
    *,
    expected_hash: str,
    version: str,
    force: bool,
) -> None:
    _check_arguments(spec, payload, expected_hash, version)
    destination = spec.destination
    if _occupied(destination) and not force:
        verify_install(spec, destination, version)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Dot-names: Godot skips them, so a scan mid-install never imports half an addon.
    backup = destination.parent / f".{spec.key}-backup-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(
        prefix=f".{spec.key}-stage-", dir=destination.parent
    ) as temporary:
        temporary_path = Path(temporary)
        candidate = _extract_verified(spec, payload, temporary_path / "archive", version)
        staged = temporary_path / spec.key
        shutil.copytree(candidate, staged)
        verify_install(spec, staged, version)

        had_existing = destination.exists()
        if had_existing:
            check_replaceable(spec, destination)
            # Said before it happens: the folder is replaced whole, with no copy kept.
            print(f"Replacing {destination}: local edits in it are lost.")
            _carry_generated(destination, staged)
            destination.replace(backup)
        try:
            staged.replace(destination)
            verify_install(spec, destination, version)
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            if had_existing and backup.exists():
                backup.replace(destination)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)


def _same(left: bytes, right: bytes) -> bool:
    """Equal, treating CRLF and LF alike in text (git may have normalised line ends)."""
    if left == right:
        return True
    if b"\0" in left or b"\0" in right:
        return False
    return left.replace(b"\r\n", b"\n") == right.replace(b"\r\n", b"\n")


def _files(tree: Path) -> dict[str, Path]:
    return {
        path.relative_to(tree).as_posix(): path
        for path in tree.rglob("*")
        if path.is_file() and path.suffix not in GENERATED_SUFFIXES
    }


def compare_payload(
    spec: AddonSpec, payload: bytes, *, expected_hash: str, version: str
) -> list[str]:
    """How the installed addon differs from the verified archive: one line per file that
    is missing, extra or modified. [] when the install is exactly upstream's."""
    _check_arguments(spec, payload, expected_hash, version)
    if not spec.destination.is_dir():
        return [f"not installed: {spec.destination}"]
    with tempfile.TemporaryDirectory(prefix=f"{spec.key}-verify-") as temporary:
        upstream = _files(_extract_verified(spec, payload, Path(temporary), version))
        installed = _files(spec.destination)
        differences = [f"missing: {name}" for name in sorted(upstream.keys() - installed.keys())]
        differences += [f"extra: {name}" for name in sorted(installed.keys() - upstream.keys())]
        differences += [
            f"modified: {name}"
            for name in sorted(upstream.keys() & installed.keys())
            if not _same(upstream[name].read_bytes(), installed[name].read_bytes())
        ]
    return differences


def download(spec: AddonSpec, version: str) -> bytes:
    url = spec.url_template.format(version=version)
    print(f"Downloading {spec.key} {version} from {url}")
    with urlopen(url, timeout=60) as response:  # noqa: S310 - https URL fixed by the spec
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(f"{spec.key} download exceeds the compressed size limit")
    return payload


def install(spec: AddonSpec, *, version: str, expected_hash: str, force: bool) -> int:
    """Download, verify, and install the addon. Returns a process exit code."""
    if _occupied(spec.destination) and not force:
        verify_install(spec, spec.destination, version)
        print(f"{spec.key} {version} is already at {spec.destination} (--force reinstalls)")
        return 0
    check_replaceable(spec, spec.destination)  # before the download, not after it
    payload = download(spec, version)
    install_payload(spec, payload, expected_hash=expected_hash, version=version, force=force)
    print(f"Installed verified {spec.key} {version} at {spec.destination}")
    return 0


def verify(spec: AddonSpec, *, version: str, expected_hash: str) -> int:
    """Download the archive and compare the installed addon with it. Exit code 0 or 1."""
    payload = download(spec, version)
    differences = compare_payload(spec, payload, expected_hash=expected_hash, version=version)
    for line in differences:
        print(line)
    if differences:
        print(
            f"{spec.key} at {spec.destination} differs from {version}: {len(differences)} file(s)"
        )
        return 1
    print(f"{spec.key} at {spec.destination} matches the verified {version} archive")
    return 0
