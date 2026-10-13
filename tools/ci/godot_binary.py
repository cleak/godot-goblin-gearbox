"""Find the Godot binary this project is pinned to, for the Python tools.

The Python mirror of tools/godot_resolver.ps1: same order, same rules. Change the two
together. The first compatible hit wins:

1. GODOT_BIN, then GODOT4_EXE. Explicit: a missing or incompatible one is an error,
   never skipped.
2. .tools/godot/ in the project (where tools/install_godot.ps1 puts a download).
3. In a linked git worktree, the same folder of the main worktree: one download serves
   every worktree.
4. The folders in kit.json `godot.search_roots` (absolute or project-relative;
   %VARS% expand). A Godot unzipped next to your projects: `"search_roots": [".."]`.
5. `godot`, then `godot4`, on PATH. A Mono build there is passed over, not an error.

No folder is searched that you did not name: a file called like a Godot release in the
folder above the project (a Downloads folder, a shared drive) is not run.

In a folder (2-4) a release is found by its name (on Windows the 64-bit build, the one
the kit targets), as a file or one level down in a folder named like it:

    Godot_v4.7.2-stable_win64.exe
    Godot_v4.7.2-stable_win64/Godot_v4.7.2-stable_win64_console.exe

The release kit.json pins comes first, then the newest patch. The `_console` launcher
comes before the windowed binary: it waits for the engine and forwards its output. For
the same reason a named or PATH binary `X.exe` resolves to `X_console.exe` when that
sits beside it.

Compatible means a stable, standard build (not Mono/.NET: the kit is GDScript only)
that reports the major.minor of kit.json `godot.version`. GODOT_VERSION overrides that
major.minor. Every candidate is asked for its `--version`: a file name is not trusted.

    uv run python tools/ci/godot_binary.py      # print the binary the tools will use

Exit 0 with the path, 1 when no binary fits (with what was tried), 2 when kit.json is
unreadable or an argument was given.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_config  # noqa: E402
import slot_pool  # noqa: E402

LOCAL_TOOLCHAIN = ".tools/godot"
EXPLICIT_VARIABLES = ("GODOT_BIN", "GODOT4_EXE")
PATH_NAMES = ("godot", "godot4")
MONO_NAME = re.compile(r"(^|[_-])mono([_.-]|$)", re.IGNORECASE)
# As tools/godot_resolver.ps1: on Windows only the 64-bit x86 release is a candidate.
RELEASE_NAME = re.compile(
    r"^Godot_v(\d+(?:\.\d+)+)-stable_" + ("win64" if os.name == "nt" else ""), re.IGNORECASE
)
CONSOLE_SUFFIX = "_console"
VERSION = re.compile(r"\d+(\.\d+)+")
PROBE_SECONDS = 30


class GodotNotFoundError(FileNotFoundError):
    """No compatible Godot binary; the message says what was tried and how to fix it."""


def wanted_minor(config: kit_config.KitConfig, env: Mapping[str, str]) -> str:
    """The major.minor a binary must report: GODOT_VERSION, else kit.json's."""
    override = env.get("GODOT_VERSION", "").strip()
    if not override:
        return config.godot_minor
    if not VERSION.fullmatch(override):
        raise GodotNotFoundError(f"GODOT_VERSION must be a version like 4.7; it is '{override}'")
    return override


def is_mono_name(path: Path) -> bool:
    return bool(MONO_NAME.search(path.name))


def version_text(binary: Path) -> str:
    """What `binary --version` prints. Raises OSError when it cannot be run."""
    code, output = slot_pool.run_captured([str(binary), "--version"], PROBE_SECONDS)
    if code is None:
        raise OSError(f"'{binary} --version' did not finish in {PROBE_SECONDS} s")
    if code != 0:
        raise OSError(f"'{binary} --version' exited {code}")
    return output.strip()


def incompatible(binary: Path, minor: str) -> str | None:
    """Why `binary` cannot be used, or None when it can."""
    if is_mono_name(binary):
        return f"'{binary}' is a Mono build; the kit needs the standard build"
    try:
        reported = version_text(binary)
    except OSError as error:
        return str(error)
    wanted = re.match(rf"{re.escape(minor)}(?:\.|[- ])", reported)
    if not wanted or "stable" not in reported.lower():
        return f"'{binary}' reported '{reported}'; the kit needs Godot {minor} stable"
    if "mono" in reported.lower():
        return f"'{binary}' is a Mono build ('{reported}'); the kit needs the standard build"
    return None


def _release(name: str, minor: str) -> tuple[int, ...] | None:
    """The version in a release name, as numbers, when it is `minor` or a patch of it."""
    found = RELEASE_NAME.match(name)
    if not found or MONO_NAME.search(name):
        return None
    version = found.group(1)
    if version != minor and not version.startswith(minor + "."):
        return None
    return tuple(int(part) for part in version.split("."))


def console_twin(binary: Path) -> Path:
    """`X_console.exe` when `binary` is `X.exe` and the two sit side by side (how Godot
    ships for Windows); otherwise `binary` itself."""
    if binary.suffix.lower() != ".exe" or binary.stem.lower().endswith(CONSOLE_SUFFIX):
        return binary
    twin = binary.with_name(f"{binary.stem}{CONSOLE_SUFFIX}{binary.suffix}")
    return twin if twin.is_file() else binary


def _is_program(path: Path) -> bool:
    if not path.is_file():
        return False
    return path.suffix.lower() == ".exe" if os.name == "nt" else os.access(path, os.X_OK)


def folder_candidates(folder: Path, minor: str, pinned: str = "") -> list[Path]:
    """The Godot releases of `minor` in one folder, best first (not yet asked for their
    version): the `pinned` release, then the newest patch; within that, every `_console`
    launcher before any windowed binary."""
    if not folder.is_dir():
        return []
    pin = tuple(int(part) for part in pinned.split(".")) if VERSION.fullmatch(pinned) else ()
    releases: list[tuple[tuple[int, ...], Path]] = []
    for entry in folder.iterdir():
        version = _release(entry.name, minor)
        if version is not None:
            releases.append((version, entry))
    # (4, 7) pads to (4, 7, 0, 0) so that it sorts below (4, 7, 1).
    releases.sort(
        key=lambda item: (item[0] == pin, (*item[0], 0, 0, 0)[:4], item[1].name), reverse=True
    )
    consoles: list[Path] = []
    windowed: list[Path] = []
    for _, entry in releases:
        inner = sorted(entry.iterdir(), reverse=True) if entry.is_dir() else [entry]
        for path in inner:
            if _release(path.name, minor) is None or not _is_program(path):
                continue
            is_console = path.stem.lower().endswith(CONSOLE_SUFFIX)
            (consoles if is_console else windowed).append(path)
    return consoles + windowed


def main_worktree_project(root: Path) -> Path | None:
    """The folder this project has in the main git worktree, when `root` is in a linked
    one (`git worktree add`). None otherwise: no git, no repository, the main worktree
    itself, or a bare repository. Never an error."""
    try:
        asked = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir", "--git-common-dir", "--show-prefix"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Three lines: this worktree's git folder, the repository's, and the project's path
    # below the top of the worktree ("" at the top).
    said = asked.stdout.split("\n")
    if asked.returncode != 0 or len(said) < 2:
        return None
    own, common = (Path(os.path.abspath(root / line.strip())) for line in said[:2])
    prefix = said[2].strip() if len(said) > 2 else ""
    # In the main worktree both are one folder. A bare repository has no main worktree.
    if os.path.normcase(own) == os.path.normcase(common) or common.name.lower() != ".git":
        return None
    return Path(os.path.abspath(common.parent / prefix))


def search_folders(config: kit_config.KitConfig) -> list[Path]:
    """Steps 2-4 of the order, as folders."""
    folders = [config.root / LOCAL_TOOLCHAIN]
    main_project = main_worktree_project(config.root)
    if main_project is not None:
        folders.append(main_project / LOCAL_TOOLCHAIN)
    for entry in config.godot_search_roots:
        expanded = os.path.expandvars(entry)
        if expanded:
            path = Path(expanded)
            folders.append(path if path.is_absolute() else config.root / path)
    return list(dict.fromkeys(Path(os.path.abspath(folder)) for folder in folders))


def path_candidates(env: Mapping[str, str]) -> list[Path]:
    """Every `godot` / `godot4` on PATH, in PATH order (a later one may be the standard
    build when the first is Mono)."""
    extensions = [""]
    if os.name == "nt":
        extensions = [ext.lower() for ext in env.get("PATHEXT", ".EXE").split(os.pathsep) if ext]
    found: list[Path] = []
    for name in PATH_NAMES:
        for folder in env.get("PATH", "").split(os.pathsep):
            for extension in extensions if folder else []:
                candidate = Path(folder) / f"{name}{extension}"
                if candidate.is_file() and console_twin(candidate) not in found:
                    found.append(console_twin(candidate))
    return found


def resolve(
    config: kit_config.KitConfig | None = None, env: Mapping[str, str] | None = None
) -> str:
    """The Godot binary to run. Raises GodotNotFoundError.

    No caller hands in a binary: a tool that took one as an argument would run any
    program its command line names."""
    cfg = config or kit_config.load()
    env = os.environ if env is None else env
    minor = wanted_minor(cfg, env)
    named = [(name, env.get(name, "").strip()) for name in EXPLICIT_VARIABLES]
    for source, value in named:
        if not value:
            continue
        if not Path(value).is_file():
            raise GodotNotFoundError(f"{source} does not name an executable file: '{value}'")
        binary = console_twin(Path(os.path.abspath(value)))
        reason = incompatible(binary, minor)
        if reason:
            raise GodotNotFoundError(f"{source}: {reason}")
        return str(binary)
    rejected: list[str] = []
    folders = search_folders(cfg)
    candidates = [
        path for folder in folders for path in folder_candidates(folder, minor, cfg.godot_version)
    ]
    for binary in candidates + path_candidates(env):
        reason = incompatible(binary, minor)
        if reason is None:
            return str(binary)
        rejected.append(reason)
    lines = [
        f"Godot {minor} (standard build) not found. Install it with "
        f"'pwsh ./tools/install_godot.ps1', or set GODOT_BIN to a Godot {minor} executable.",
        "Searched: "
        + "; ".join(str(folder) for folder in folders)
        + "; then godot/godot4 on PATH.",
    ]
    lines += [f"Skipped: {reason}" for reason in rejected]
    raise GodotNotFoundError("\n".join(lines))


def main() -> int:
    if sys.argv[1:]:
        print("usage: uv run python tools/ci/godot_binary.py   (no arguments)", file=sys.stderr)
        return 2
    try:
        print(resolve())
    except GodotNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    except kit_config.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
