"""Shared by the kit's Python command-line tools: safe printing, which files a gate
scans, allowlist files, and the artifacts/ folder.

An allowlist is optional (a missing file is empty). Each line is `path | reason`:
a project-relative file, or a folder ending in `/` for everything under it. The reason
is required. `#` starts a comment line.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_config  # noqa: E402


def plain_output() -> None:
    """Make printing unable to fail. Piped output on Windows is encoded with a legacy
    code page, so a character outside it (a path, a comment or a test message in
    another script) is written as an escape (`\\u2713`). Call first in every `main`."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def gather(
    config: kit_config.KitConfig, paths: Iterable[str] = (), suffixes: Iterable[str] = (".gd",)
) -> list[Path]:
    """The files a gate scans: the project's authored source, or just `paths` (files or
    folders, which win over kit.json's `source.exclude`). Raises ValueError for a path
    that does not exist or lies outside the project."""
    wanted = tuple(suffixes)
    chosen = list(paths)
    if not chosen:
        return kit_config.source_files(wanted, config=config)
    found: set[Path] = set()
    for path in chosen:
        found.update(kit_config.source_files(wanted, path=path, config=config))
    return sorted(found)


def relative(path: Path, config: kit_config.KitConfig) -> str:
    return path.resolve().relative_to(config.root).as_posix()


def load_allowlist(path: Path) -> dict[str, str]:
    """`path | reason` lines as {path: reason}. Raises ValueError for a line with no reason."""
    entries: dict[str, str] = {}
    if not path.is_file():
        return entries
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, reason = (part.strip() for part in line.partition("|"))
        if not reason:
            raise ValueError(f"{path.name}: entry needs `| reason`: {raw!r}")
        entries[name.replace("\\", "/")] = reason
    return entries


def allowed(relative_path: str, allowlist: dict[str, str]) -> bool:
    return any(
        relative_path == key or (key.endswith("/") and relative_path.startswith(key))
        for key in allowlist
    )


def entry_files(key: str, config: kit_config.KitConfig, suffix: str = ".gd") -> list[Path]:
    """The existing files one allowlist entry covers. `b.GD` is a script on every system,
    as it is to the scan (tools/kit_config.py): the suffix is matched without letter case."""
    target = config.root / key
    if key.endswith("/"):
        if not target.is_dir():
            return []
        return sorted(target.rglob(f"*{suffix}", case_sensitive=False))
    return [target] if target.is_file() else []


def ensure_gdignore(folder: Path) -> None:
    """Create `folder` with a .gdignore in it, so Godot does not import what a tool
    writes there (it would turn every PNG and HTML report into a project resource)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gdignore").touch()
