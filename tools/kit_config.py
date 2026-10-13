"""Shared configuration for the kit's Python tools: kit.json and the authored source set.

"Authored source" is every matching file under the project root, skipping:

- folders whose name starts with "." (.godot, .venv, .git, ...)
- folders that hold a .gdignore, or a project.godot (a nested project)
- directory links
- the folders listed in kit.json `source.exclude`

and then adding back the folders listed in `source.include`. tools/kit_config.ps1 and
tools/kit_config.gd read kit.json the same way, with the same error texts, and apply the
same rules; change the three together (tools/tests/test_kit_config_parity.py compares them).

kit.json, every key optional:

    godot.version                   "4.7.2"   the pinned release
    godot.windows_x86_64_sha512     ""        checksum of that release's archive
    godot.search_roots              []        extra folders searched for Godot
    gdunit4.version                 ""        the pinned gdUnit4
    gdunit4.source_archive_sha256   ""        checksum of its source archive
    source.exclude                  ["addons", "artifacts", "docs"]
    source.include                  []        folders added back; each must exist
    tests.root                      "test"    "test", "test/" and "res://test" are the same
    gdscript.strict                 true      false: the strict warnings are advice

A missing file, a missing key, null and "" all mean the default. Anything else of the wrong
type is an error, never a silent default.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG_NAME = "kit.json"


@dataclass(frozen=True)
class KitConfig:
    root: Path
    godot_version: str = "4.7.2"
    godot_windows_sha512: str = ""
    godot_search_roots: tuple[str, ...] = ()
    gdunit4_version: str = ""
    gdunit4_sha256: str = ""
    source_exclude: tuple[str, ...] = ("addons", "artifacts", "docs")
    source_include: tuple[str, ...] = ()
    test_root: str = "test"
    gdscript_strict: bool = True
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def godot_minor(self) -> str:
        """The major.minor a Godot binary must report: 4.7 for version 4.7.2."""
        parts = self.godot_version.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else self.godot_version

    @property
    def test_dir(self) -> Path:
        return self.root / self.test_root


class ConfigError(ValueError):
    """kit.json is not valid JSON, or holds a value that cannot be used."""


def project_folder(text: str) -> str:
    """`text` as a project-relative folder with forward slashes: "res://test", "test/",
    "\\test" and "./test" all give "test". "" when it names the project root itself or a
    place outside the project (too many "..", or a drive letter)."""
    text = text.replace("\\", "/")
    if text.startswith("res://"):
        text = text[len("res://") :]
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
        elif ":" in part:
            return ""
        else:
            parts.append(part)
    return "/".join(parts)


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f'{CONFIG_NAME}: "{name}" must be an object')
    return value


def _text(section: dict, name: str, key: str, default: str = "") -> str:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        # A bare number is refused too: 4.10 would arrive as 4.1.
        raise ConfigError(f'{CONFIG_NAME}: "{name}.{key}" must be a string')
    return value or default


def _texts(section: dict, name: str, key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f'{CONFIG_NAME}: "{name}.{key}" must be a list of strings')
    return tuple(value)


def _flag(section: dict, name: str, key: str, default: bool) -> bool:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f'{CONFIG_NAME}: "{name}.{key}" must be true or false')
    return value


def _folders(names: tuple[str, ...], name: str, key: str) -> tuple[str, ...]:
    folders = []
    for entry in names:
        folder = project_folder(entry)
        if not folder:
            raise ConfigError(
                f'{CONFIG_NAME}: "{name}.{key}" must list folders inside the project, not "{entry}"'
            )
        folders.append(folder)
    return tuple(folders)


def _not_json(constant: str) -> None:
    """json.loads takes NaN, Infinity and -Infinity; the other two loaders do not."""
    raise ValueError(f"{constant} is not JSON")


def load(root: Path | None = None) -> KitConfig:
    """Read kit.json under `root` (default: this repository). A missing file gives
    defaults. Raises ConfigError (a ValueError), one line that names what is wrong."""
    base = (root or REPO).resolve()
    path = base / CONFIG_NAME
    if not path.is_file():
        return KitConfig(root=base)
    try:
        # utf-8-sig: PowerShell 5.1 writes a BOM, which plain utf-8 turns into a JSON error.
        raw = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_not_json)
    except ValueError as error:  # bad JSON, or bytes that are not UTF-8
        raise ConfigError(f"{CONFIG_NAME} is not valid JSON: {error} ({path})") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME} must hold a JSON object ({path})")
    godot = _section(raw, "godot")
    gdunit4 = _section(raw, "gdunit4")
    source = _section(raw, "source")
    tests = _section(raw, "tests")
    gdscript = _section(raw, "gdscript")
    defaults = KitConfig(root=base)
    godot_version = _text(godot, "godot", "version", defaults.godot_version)
    godot_sha512 = _text(godot, "godot", "windows_x86_64_sha512")
    search_roots = _texts(godot, "godot", "search_roots")
    gdunit4_version = _text(gdunit4, "gdunit4", "version")
    gdunit4_sha256 = _text(gdunit4, "gdunit4", "source_archive_sha256")
    exclude = _folders(
        _texts(source, "source", "exclude", defaults.source_exclude), "source", "exclude"
    )
    include = _folders(_texts(source, "source", "include"), "source", "include")
    written_root = _text(tests, "tests", "root", defaults.test_root)
    test_root = project_folder(written_root)
    if not test_root:
        raise ConfigError(
            f'{CONFIG_NAME}: "tests.root" must be a folder inside the project, not "{written_root}"'
        )
    strict = _flag(gdscript, "gdscript", "strict", defaults.gdscript_strict)
    for folder in include:
        # A typo here would silently drop the folder from every check.
        if not (base / folder).is_dir():
            raise ConfigError(
                f'{CONFIG_NAME}: "source.include" names a folder that does not exist: {folder}'
            )
    return KitConfig(
        root=base,
        godot_version=godot_version,
        godot_windows_sha512=godot_sha512,
        godot_search_roots=search_roots,
        gdunit4_version=gdunit4_version,
        gdunit4_sha256=gdunit4_sha256,
        source_exclude=exclude,
        source_include=include,
        test_root=test_root,
        gdscript_strict=strict,
        raw=raw,
    )


def is_skipped_dir(directory: Path, config: KitConfig, *, use_exclude: bool = True) -> bool:
    """True for a folder Godot does not scan, or that `source.exclude` names."""
    # A junction is the directory link Windows makes without admin rights; is_symlink()
    # does not see it.
    if directory.name.startswith(".") or directory.is_symlink() or directory.is_junction():
        return True
    if (directory / ".gdignore").is_file() or (directory / "project.godot").is_file():
        return True
    if not use_exclude:
        return False
    relative = directory.resolve().relative_to(config.root).as_posix().lower()
    return relative in {excluded.lower() for excluded in config.source_exclude}


def _walk(
    start: Path, suffixes: frozenset[str], config: KitConfig, *, use_exclude: bool
) -> Iterator[Path]:
    if not start.is_dir():
        return
    pending = [start]
    while pending:
        directory = pending.pop()
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if not is_skipped_dir(entry, config, use_exclude=use_exclude):
                    pending.append(entry)
            elif entry.suffix.lower() in suffixes:
                yield entry


def _at_or_under(folder: str, parent: str) -> bool:
    """Both project-relative with forward slashes; "" is the project root."""
    folder, parent = folder.lower(), parent.lower()
    return not parent or folder == parent or folder.startswith(parent + "/")


def source_files(
    suffixes: Iterable[str] = (".gd",),
    path: str | Path | None = None,
    config: KitConfig | None = None,
) -> list[Path]:
    """The project's authored files with one of `suffixes`, sorted.

    `path` (a file or folder, absolute or project-relative) narrows the search. A file
    named this way is always returned. A folder gives its authored files: the
    `source.exclude` folders inside it stay out and the `source.include` folders inside it
    come back, unless the folder itself is at or under an excluded one: asking for an
    excluded folder by name checks all of it. Raises ValueError when `path` does not exist
    or lies outside the project.
    """
    cfg = config or load()
    wanted = frozenset(suffix.lower() for suffix in suffixes)
    found: set[Path] = set()
    if path is None:
        named = ""
    else:
        target = Path(path)
        full = (target if target.is_absolute() else cfg.root / target).resolve()
        if not full.exists():
            raise ValueError(f"path does not exist: {full}")
        if full != cfg.root and cfg.root not in full.parents:
            raise ValueError(f"path is outside the project: {full}")
        if full.is_file():
            return [full] if full.suffix.lower() in wanted else []
        named = "" if full == cfg.root else full.relative_to(cfg.root).as_posix()
    if named and any(_at_or_under(named, excluded) for excluded in cfg.source_exclude):
        return sorted(_walk(cfg.root / named, wanted, cfg, use_exclude=False))
    found.update(_walk(cfg.root / named, wanted, cfg, use_exclude=True))
    for included in cfg.source_include:
        if _at_or_under(included, named):
            found.update(_walk(cfg.root / included, wanted, cfg, use_exclude=False))
    return sorted(found)


def to_res(path: Path, config: KitConfig | None = None) -> str:
    """An absolute path inside the project as a res:// path."""
    cfg = config or load()
    return "res://" + path.resolve().relative_to(cfg.root).as_posix()
