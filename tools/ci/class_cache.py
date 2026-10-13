"""Keep Godot's class cache in step with the scripts on disk, before tests run.

Godot records every `class_name` in .godot/global_script_class_cache.cfg. That folder
is not committed, so a fresh clone has no cache, and a pull that adds, moves, renames
or deletes a `class_name` script leaves it stale. Every script that names such a class
then fails to parse, gdUnit4's own runner included ("Could not find type
"GdUnitTestCIRunner""), and no test runs. `godot --headless --import` rewrites the cache.

The Python mirror of tools/class_cache.ps1; change the two together.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_config  # noqa: E402
import project_refs  # noqa: E402
import slot_pool  # noqa: E402

CACHE = ".godot/global_script_class_cache.cfg"
CACHED_CLASS = re.compile(r'^"class": &"(\w+)"')
CACHED_PATH = re.compile(r'^"path": "(res://[^"]+)"')
IMPORT_SECONDS = 1800
SCAN_SECONDS = 120
LOCK_POLL_SECONDS = 0.5


def classes_on_disk(config: kit_config.KitConfig) -> set[tuple[str, str]]:
    """(class name, res:// path) of every `class_name` script Godot scans: all of them,
    addons included, so kit.json's `source.exclude` does not apply here."""
    scanned = dataclasses.replace(config, source_exclude=(), source_include=())
    found: set[tuple[str, str]] = set()
    for path in kit_config.source_files((".gd",), config=scanned):
        text = path.read_text(encoding="utf-8", errors="replace")
        declared = project_refs.CLASS_NAME.search(text)
        if declared:
            found.add((declared.group(1), kit_config.to_res(path, config)))
    return found


def classes_in_cache(cache: Path) -> set[tuple[str, str]]:
    """The (class name, res:// path) pairs the cache lists for GDScript files. In each
    entry "class" comes before "path"."""
    found: set[tuple[str, str]] = set()
    name: str | None = None
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        declared = CACHED_CLASS.match(line)
        located = CACHED_PATH.match(line)
        if declared:
            name = declared.group(1)
        elif located and name is not None:
            if located.group(1).lower().endswith(".gd"):
                found.add((name, located.group(1)))
            name = None
    return found


def differences(config: kit_config.KitConfig) -> list[str]:
    """One line per way the cache differs from the scripts on disk; [] when it is fresh."""
    cache = config.root / CACHE
    if not cache.is_file():
        return [f"no class cache yet ({CACHE})"]
    cached = classes_in_cache(cache)
    on_disk = classes_on_disk(config)
    lines = [f"missing from cache: {name} {path}" for name, path in sorted(on_disk - cached)]
    lines += [f"only in cache: {name} {path}" for name, path in sorted(cached - on_disk)]
    return lines


def is_stale(config: kit_config.KitConfig) -> bool:
    return bool(differences(config))


def _godot(godot: str, arguments: list[str], root: Path, seconds: int) -> tuple[int, str]:
    code, output = slot_pool.run_captured([godot, *arguments], seconds, cwd=root)
    if code is None:
        raise RuntimeError(f"godot {' '.join(arguments)} did not finish in {seconds} s:\n{output}")
    return code, output


def refresh(godot: str, config: kit_config.KitConfig) -> None:
    """Re-import the project. Raises RuntimeError with Godot's output when Godot fails."""
    arguments = ["--headless", "--import"]
    code, output = _godot(godot, arguments, config.root, IMPORT_SECONDS)
    # With no cache at all, the first pass can parse scripts before their classes are
    # registered, print "SCRIPT ERROR" for each and still exit 0. A second pass is clean.
    if code == 0 and "SCRIPT ERROR" in output:
        code, output = _godot(godot, arguments, config.root, IMPORT_SECONDS)
    if code != 0:
        raise RuntimeError(f"godot --headless --import exited {code}:\n{output}")
    if not (config.root / CACHE).is_file():
        # Seen on older Godot 4 releases: --import leaves no cache, an editor scan writes it.
        _godot(godot, ["--headless", "--editor", "--quit-after", "1000"], config.root, SCAN_SECONDS)
    left = differences(config)
    if left:
        # Not fatal: usually a class_name script that does not parse, and the tests that
        # follow name it better than this can.
        print(
            "warning: the class cache still differs from the scripts on disk after "
            "godot --headless --import:\n  " + "\n  ".join(left),
            file=sys.stderr,
        )


@contextlib.contextmanager
def import_lock(config: kit_config.KitConfig) -> Iterator[None]:
    """One import per checkout at a time: two Godot processes importing the same
    project write the same .godot files. A lock file per project, beside the test slots."""
    name = hashlib.sha256(str(config.root).lower().encode()).hexdigest()[:16]
    pool = slot_pool.SlotPool(1, slot_pool.slot_directory() / f"import_{name}")
    deadline = time.monotonic() + IMPORT_SECONDS
    slot = pool.try_acquire()
    while slot is None:
        if time.monotonic() > deadline:
            raise RuntimeError(f"another run has been importing {config.root} for too long")
        time.sleep(LOCK_POLL_SECONDS)
        slot = pool.try_acquire()
    try:
        yield
    finally:
        slot.release()


def ensure(godot: str, config: kit_config.KitConfig) -> bool:
    """Refresh the cache when it is missing or stale. True when this call imported."""
    if not is_stale(config):
        return False
    with import_lock(config):
        if not is_stale(config):
            return False  # another run imported while this one waited
        refresh(godot, config)
    return True
