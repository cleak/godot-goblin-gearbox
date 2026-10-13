"""How one project file names another: shared by select_suites.py and check_architecture.py.

A file can name another by `res://` path, by `uid://` id, by a script's `class_name`,
or by an autoload's name. These helpers read those names from the project's text.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kit_config  # noqa: E402

_ANNOTATIONS = r"(?:@\w+(?:\([^)\n]*\))?[ \t]+)*"
# `class_name X`, with any annotations before it on the line (`@tool class_name X`).
CLASS_NAME = re.compile(rf"^[ \t]*{_ANNOTATIONS}class_name[ \t]+(\w+)", re.MULTILINE)
# What the script itself extends: `extends Base`, `extends "res://base.gd"`, or either
# after `class_name X` on the same line. At the left margin, so never an inner class's.
OWN_BASE = re.compile(
    rf"^{_ANNOTATIONS}(?:class_name[ \t]+\w+[ \t]+)?extends[ \t]+"
    r"(?:([\w.]+)|[\"']([^\"'\n]+)[\"'])",
    re.MULTILINE,
)
AUTOLOAD_LINE = re.compile(r'^(\w+)="\*?(res://[^"]+|uid://[^"]+)"', re.MULTILINE)
RES_PATH = re.compile(r"res://[^\"'\s)\],]+")
UID = re.compile(r"uid://[a-z0-9]+")
SCENE_UID = re.compile(r'^\[gd_(?:scene|resource)[^\]\n]*\buid="(uid://[a-z0-9]+)"', re.MULTILINE)
_STRING = r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''
_STRING_OR_COMMENT = re.compile(rf"({_STRING})|#[^\n]*")


def _keep_lines(text: str) -> str:
    return "\n" * text.count("\n")


def without_comments(gdscript: str) -> str:
    """GDScript with comments removed and strings kept; line numbers do not move."""
    return _STRING_OR_COMMENT.sub(lambda found: found.group(1) or "", gdscript)


def code_only(gdscript: str) -> str:
    """GDScript with comments removed and every string emptied; line numbers do not move."""
    return _STRING_OR_COMMENT.sub(
        lambda found: '""' + _keep_lines(found.group(1)) if found.group(1) else "", gdscript
    )


def own_base(gdscript: str) -> tuple[str, str]:
    """What a script extends, as (class name, script path): one is "", both when the
    script has no `extends`. A commented-out line or one inside a multi-line string is
    not read as code."""

    def code(found: re.Match[str]) -> str:
        text = found.group(1)
        if text is None:
            return ""
        return text if "\n" not in text else '""' + _keep_lines(text)

    # A byte order mark (some Windows editors write one) is not part of the first line.
    base = OWN_BASE.search(_STRING_OR_COMMENT.sub(code, gdscript.removeprefix("﻿")))
    return (base.group(1) or "", base.group(2) or "") if base else ("", "")


def relative(path: Path, config: kit_config.KitConfig) -> str:
    """A file inside the project as a project-relative path with forward slashes."""
    return path.resolve().relative_to(config.root).as_posix()


def is_authored(relative_path: str, config: kit_config.KitConfig) -> bool:
    """Whether a project-relative path sits where authored source lives, judged by its
    name alone, so it also answers for a file that has been deleted."""
    folders = PurePosixPath(relative_path).parts[:-1]
    if any(folder.startswith(".") for folder in folders):
        return False
    folder = "/".join(folders).lower()

    def under(roots: Iterable[str]) -> bool:
        return any(
            folder == root.lower() or folder.startswith(root.lower() + "/") for root in roots
        )

    if under(config.source_include) or under([config.test_root]):
        return True
    return not under(config.source_exclude)


def top_level_lines(config_text: str) -> list[str]:
    """The lines of a Godot config file that start outside a string. A quoted value may
    run over several lines (a description), and a line inside it that looks like a
    section header or an entry is neither."""
    lines: list[str] = []
    inside = False
    for line in config_text.splitlines():
        if not inside:
            lines.append(line)
        if len(re.findall(r'(?<!\\)"', line)) % 2:
            inside = not inside
    return lines


def autoloads(root: Path) -> dict[str, str]:
    """Autoload name -> target (`res://...` or `uid://...`) from project.godot's [autoload]."""
    project = root / "project.godot"
    if not project.is_file():
        return {}
    text = "\n".join(top_level_lines(project.read_text(encoding="utf-8", errors="replace")))
    section = re.search(r"^\[autoload\][ \t]*\n(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    return dict(AUTOLOAD_LINE.findall(section.group(1))) if section else {}


def class_names(texts: dict[str, str]) -> dict[str, str]:
    """class_name -> the script that declares it, over `texts` (relative path -> text)."""
    owners: dict[str, str] = {}
    for path, text in texts.items():
        if path.endswith(".gd"):
            found = CLASS_NAME.search(without_comments(text))
            if found:
                owners.setdefault(found.group(1), path)
    return owners


def uid_owners(root: Path, texts: dict[str, str]) -> dict[str, str]:
    """`uid://...` -> relative path, for every file in `texts`: a scene or resource
    carries its id in its header, a script or shader in a `.uid` file beside it."""
    owners: dict[str, str] = {}
    for path, text in texts.items():
        if path.endswith((".tscn", ".tres")):
            found = SCENE_UID.search(text)
            if found:
                owners.setdefault(found.group(1), path)
            continue
        sidecar = root / (path + ".uid")
        if sidecar.is_file():
            uid = sidecar.read_text(encoding="utf-8", errors="replace").strip()
            if UID.fullmatch(uid):
                owners.setdefault(uid, path)
    return owners
