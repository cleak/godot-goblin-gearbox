#!/usr/bin/env python3
"""Re-vendor or upgrade addons/gdUnit4 from its pinned, hash-verified release archive.

gdUnit4 is committed to the repository, so a fresh clone needs nothing from here. Use
this to restore the folder, to check it was not edited, or to move to a new version:

    uv run python tools/install_gdunit4.py              # check the installed version
    uv run python tools/install_gdunit4.py --verify     # compare every file with upstream
    uv run python tools/install_gdunit4.py --force      # reinstall the pinned version
    uv run python tools/install_gdunit4.py --version 6.2.0 --sha256 <hash> --force

`--destination DIR` works on another gdUnit4 folder (DIR is that folder itself). Only an
empty folder or an existing gdUnit4 is replaced: anything else is refused, so a
mistyped `--destination addons --force` cannot delete the other addons.

The pin is kit.json `gdunit4.version` and `gdunit4.source_archive_sha256` (the SHA-256
of https://github.com/MikeSchulze/gdUnit4/archive/refs/tags/v<version>.zip). After an
upgrade, write the new version and hash into kit.json and run
`pwsh ./tools/godot.ps1 --headless --import` so Godot writes .uid files for new scripts.

The archive checks and the swap with rollback live in tools/_addon_install.py.

Exit 0 done or matching, 1 on a difference (--verify) or a failed or refused install,
2 on usage or an unreadable kit.json pin.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))

from _addon_install import (  # noqa: E402
    ROOT,
    SHA256_PATTERN,
    VERSION_PATTERN,
    AddonSpec,
    install,
    load_pin,
    verify,
)
from gate_support import plain_output  # noqa: E402

SPEC = AddonSpec(
    key="gdunit4",
    url_template="https://github.com/MikeSchulze/gdUnit4/archive/refs/tags/v{version}.zip",
    inner_glob="gdUnit4-{version}/addons/gdUnit4",
    destination=ROOT / "addons" / "gdUnit4",
    required_files=("plugin.cfg", "plugin.gd", "bin/GdUnitCmdTool.gd"),
    plugin_name="gdUnit4",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-vendor or upgrade addons/gdUnit4 from its pinned, hash-verified archive."
    )
    parser.add_argument("--version", help="gdUnit4 version (default: kit.json gdunit4.version)")
    parser.add_argument(
        "--sha256", help="SHA-256 of the source archive; required with a non-pinned --version"
    )
    parser.add_argument(
        "--destination",
        type=Path,
        help="the gdUnit4 folder itself, not the addons folder (default: addons/gdUnit4); "
        "only an empty folder or an existing gdUnit4 is ever replaced",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--force", action="store_true", help="replace an existing install")
    action.add_argument(
        "--verify",
        action="store_true",
        help="change nothing; download the archive and list files that differ from it",
    )
    args = parser.parse_args(argv)
    plain_output()
    try:
        pinned_version, pinned_hash = load_pin(SPEC)
    except (RuntimeError, ValueError) as error:  # ValueError: kit.json itself is unreadable
        print(error, file=sys.stderr)
        return 2
    version = args.version or pinned_version
    if not VERSION_PATTERN.fullmatch(version):
        parser.error("--version must be a dotted numeric version with an optional suffix")
    expected_hash = args.sha256 or (pinned_hash if version == pinned_version else None)
    if expected_hash is None:
        parser.error(f"--version {version} is not the pinned {pinned_version}: pass --sha256")
    if not SHA256_PATTERN.fullmatch(expected_hash):
        parser.error("--sha256 must be 64 hexadecimal characters")
    spec = SPEC
    if args.destination:
        spec = dataclasses.replace(SPEC, destination=args.destination.resolve())
    try:
        if args.verify:
            return verify(spec, version=version, expected_hash=expected_hash)
        return install(spec, version=version, expected_hash=expected_hash, force=args.force)
    except (RuntimeError, URLError, OSError) as error:
        print(f"gdUnit4 install failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
