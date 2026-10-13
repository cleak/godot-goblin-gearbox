"""Find the Godot binary this project is pinned to, for the Python tools.

The Python mirror of tools/godot_resolver.ps1: same order, same rules. Change the two
together. The first compatible hit wins:

1. GODOT_BIN, then GODOT4_EXE. Explicit: a missing or incompatible one is an error,
   never skipped.
2. .tools/godot/ in the project (where tools/install_godot.ps1 puts a download).
3. In a linked git worktree, the same folder of the main worktree: one download serves
   every worktree.
4. The folders in kit.json `godot.search_roots` (absolute or project-relative;
   %VAR%, $VAR, ${VAR} and a leading ~ expand). A Godot unzipped next to your projects:
   `"search_roots": [".."]`.
5. `godot`, then `godot4`, on PATH. A Mono build there is passed over, not an error.

No folder is searched that you did not name: a file called like a Godot release in the
folder above the project (a Downloads folder, a shared drive) is not run.

In a folder (2-4) a release is found by the name of the official download for this
system (RELEASE_TAGS), as a file or one level down in a folder named like it:

    Godot_v4.7.2-stable_win64.exe
    Godot_v4.7.2-stable_win64/Godot_v4.7.2-stable_win64_console.exe
    Godot_v4.7.2-stable_linux.x86_64/Godot_v4.7.2-stable_linux.x86_64
    Godot_v4.7.2-stable_macos.universal/Godot.app      (a folder's own Godot.app: last)

The release kit.json pins comes first, then the newest patch. On Windows the `_console`
launcher comes before the windowed binary: it waits for the engine and forwards its
output. For the same reason a named or PATH binary `X.exe` resolves to `X_console.exe`
when that sits beside it. On macOS GODOT_BIN may name Godot.app: the program is
Godot.app/Contents/MacOS/Godot.

Every rule that differs by system takes the system as an argument (`build`, `platform`),
so each branch is tested on any machine.

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
# What the file names of each official download end in. tools/godot_resolver.ps1
# Get-KitGodotRelease is the same table.
RELEASE_TAGS = {
    "windows_x86_64": "win64",
    "linux_x86_64": "linux.x86_64",
    "linux_arm64": "linux.arm64",
    "macos_universal": "macos.universal",
}
CONSOLE_SUFFIX = "_console"
# Where the program is inside Godot.app, which macOS shows as one file.
BUNDLE = "Godot.app"
BUNDLE_PROGRAM = ("Contents", "MacOS", "Godot")
VARIABLE = re.compile(r"%([^%\\/]+)%|\$\{(\w+)\}|\$(\w+)")
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
    if not is_executable(binary):
        return f"'{binary}' is not executable. Fix: chmod +x '{binary}'"
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


def is_executable(binary: Path) -> bool:
    """False on Linux and macOS for a file this user may not execute (a download unpacked
    by a tool that drops the permission). Windows has no such permission.
    tools/godot_resolver.ps1 Test-KitNotExecutable is the same question."""
    return os.name == "nt" or os.access(binary, os.X_OK)


def release_name(build: str) -> re.Pattern[str] | None:
    """What the names of the `build` download start with; None for an unknown build."""
    tag = RELEASE_TAGS.get(build)
    if tag is None:
        return None
    return re.compile(r"Godot_v(\d+(?:\.\d+)+)-stable_" + re.escape(tag), re.IGNORECASE)


def _release(name: str, minor: str, pattern: re.Pattern[str]) -> tuple[int, ...] | None:
    """The version in a release name, as numbers, when it is `minor` or a patch of it."""
    found = pattern.match(name)
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


def bundle_program(path: Path, platform: str | None = None) -> Path:
    """macOS: Godot.app is a folder. Given one, the program inside it; otherwise `path`."""
    platform = platform or kit_config.host_platform()
    if platform == "macos" and path.name.lower().endswith(".app"):
        return path.joinpath(*BUNDLE_PROGRAM)
    return path


def _entries(folder: Path) -> list[Path]:
    """What `folder` holds; nothing when it may not be read (it holds no candidate)."""
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _name(path: Path) -> str:
    return path.name


def folder_candidates(
    folder: Path, minor: str, pinned: str = "", build: str | None = None
) -> list[Path]:
    """The Godot releases of `minor` in one folder for one build (default: this
    machine's), best first (not yet asked for their version): the `pinned` release, then
    the newest patch; on Windows every `_console` launcher before any windowed binary."""
    build = kit_config.godot_build() if build is None else build
    pattern = release_name(build)
    # os.path.isdir / isfile: False for what may not be looked at, where Path.is_dir()
    # and Path.is_file() raise.
    if pattern is None or not os.path.isdir(folder):
        return []
    pin = tuple(int(part) for part in pinned.split(".")) if VERSION.fullmatch(pinned) else ()
    releases: list[tuple[tuple[int, ...], Path]] = []
    for entry in _entries(folder):
        version = _release(entry.name, minor, pattern)
        if version is not None:
            releases.append((version, entry))
    # (4, 7) pads to (4, 7, 0, 0) so that it sorts below (4, 7, 1).
    releases.sort(
        key=lambda item: (item[0] == pin, (*item[0], 0, 0, 0)[:4], item[1].name), reverse=True
    )
    consoles: list[Path] = []
    plain: list[Path] = []
    for _, entry in releases:
        if build == "macos_universal":
            program = entry.joinpath(BUNDLE, *BUNDLE_PROGRAM)
            if os.path.isdir(entry) and os.path.isfile(program):
                plain.append(program)
            continue
        # By name as text: two paths compare without letter case on Windows.
        inner = [entry]
        if os.path.isdir(entry):
            inner = sorted(_entries(entry), key=_name, reverse=True)
        for path in inner:
            if not os.path.isfile(path) or MONO_NAME.search(path.name):
                continue
            if build == "windows_x86_64":
                if pattern.match(path.name) and path.suffix.lower() == ".exe":
                    is_console = path.stem.lower().endswith(CONSOLE_SUFFIX)
                    (consoles if is_console else plain).append(path)
            elif pattern.fullmatch(path.name):
                # The whole name: the archive beside it (....zip) is not the program.
                plain.append(path)
    if build == "macos_universal":
        program = folder.joinpath(BUNDLE, *BUNDLE_PROGRAM)
        if os.path.isfile(program):
            plain.append(program)
    return consoles + plain


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


def expand_search_root(entry: str) -> str:
    """One kit.json `godot.search_roots` entry with %NAME%, ${NAME} and $NAME replaced by
    that environment variable (left as written when it is not set) and a leading ~ by
    the home folder, on every system. tools/godot_resolver.ps1 Expand-KitSearchRoot is
    the same rule."""

    def value(found: re.Match[str]) -> str:
        name = found.group(1) or found.group(2) or found.group(3)
        return os.environ.get(name) or found.group(0)

    expanded = VARIABLE.sub(value, entry)
    if expanded == "~" or expanded.startswith(("~/", "~\\")):
        expanded = str(Path.home()) + expanded[1:]
    return expanded


def search_folders(config: kit_config.KitConfig) -> list[Path]:
    """Steps 2-4 of the order, as folders."""
    folders = [config.root / LOCAL_TOOLCHAIN]
    main_project = main_worktree_project(config.root)
    if main_project is not None:
        folders.append(main_project / LOCAL_TOOLCHAIN)
    for entry in config.godot_search_roots:
        expanded = expand_search_root(entry)
        if expanded:
            path = Path(expanded)
            folders.append(path if path.is_absolute() else config.root / path)
    return list(dict.fromkeys(Path(os.path.abspath(folder)) for folder in folders))


def path_candidates(env: Mapping[str, str], platform: str | None = None) -> list[Path]:
    """Every `godot` / `godot4` on PATH, in PATH order (a later one may be the standard
    build when the first is Mono)."""
    extensions = [""]
    if (platform or kit_config.host_platform()) == "windows":
        extensions = [ext.lower() for ext in env.get("PATHEXT", ".EXE").split(os.pathsep) if ext]
    found: list[Path] = []
    for name in PATH_NAMES:
        for folder in env.get("PATH", "").split(os.pathsep):
            for extension in extensions if folder else []:
                # Absolute: a PATH entry such as "." would name it by the working directory.
                candidate = Path(os.path.abspath(Path(folder) / f"{name}{extension}"))
                if os.path.isfile(candidate) and console_twin(candidate) not in found:
                    found.append(console_twin(candidate))
    return found


def not_found_message(minor: str, pinned: str, platform: str, architecture: str) -> str:
    """What `resolve` says first when nothing fits: the fix for `platform`."""
    tag = RELEASE_TAGS.get(kit_config.godot_build(platform, architecture))
    if tag is None:
        return (
            f"Godot {minor} (standard build) not found, and Godot publishes no build for "
            f"{platform} {architecture}. Build or install Godot {minor} yourself and set "
            "GODOT_BIN to its executable."
        )
    named = ""
    if platform == "linux":
        # The file to look for: the pinned release, or a patch of another `minor`.
        version = pinned
        if not f"{pinned}.".startswith(f"{minor}."):
            version = minor if minor.count(".") >= 2 else f"{minor}.x"
        named = f" (Godot_v{version}-stable_{tag})"
    elif platform == "macos":
        named = " (Godot.app, or Godot.app/Contents/MacOS/Godot)"
    return (
        f"Godot {minor} (standard build) not found. Install it with "
        f"'pwsh ./tools/install_godot.ps1', or set GODOT_BIN to a Godot {minor} "
        f"executable{named}."
    )


def resolve(
    config: kit_config.KitConfig | None = None,
    env: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    architecture: str | None = None,
) -> str:
    """The Godot binary to run. Raises GodotNotFoundError.

    `platform` and `architecture`: the system to resolve for (default: this one).

    No caller hands in a binary: a tool that took one as an argument would run any
    program its command line names."""
    cfg = config or kit_config.load()
    env = os.environ if env is None else env
    platform = platform or kit_config.host_platform()
    architecture = architecture or kit_config.host_architecture()
    build = kit_config.godot_build(platform, architecture)
    minor = wanted_minor(cfg, env)
    named = [(name, env.get(name, "").strip()) for name in EXPLICIT_VARIABLES]
    for source, value in named:
        if not value:
            continue
        program = bundle_program(Path(value), platform)
        if not program.is_file():
            raise GodotNotFoundError(f"{source} does not name an executable file: '{value}'")
        binary = console_twin(Path(os.path.abspath(program)))
        reason = incompatible(binary, minor)
        if reason:
            raise GodotNotFoundError(f"{source}: {reason}")
        return str(binary)
    rejected: list[str] = []
    folders = search_folders(cfg)
    candidates = [
        path
        for folder in folders
        for path in folder_candidates(folder, minor, cfg.godot_version, build)
    ]
    for binary in candidates + path_candidates(env, platform):
        reason = incompatible(binary, minor)
        if reason is None:
            return str(binary)
        rejected.append(reason)
    lines = [not_found_message(minor, cfg.godot_version, platform, architecture)]
    if env.get("GODOT_VERSION", "").strip() and not f"{cfg.godot_version}.".startswith(f"{minor}."):
        lines.append(
            f"GODOT_VERSION asks for {minor}; kit.json pins {cfg.godot_version}, which is what "
            "install_godot.ps1 installs."
        )
    searched = "; ".join(str(folder) for folder in folders)
    lines.append(f"Searched: {searched}; then godot/godot4 on PATH.")
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
