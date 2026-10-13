"""Lint AGENTS.md: the file an agent reads on every turn stays small, plain and true.

    uv run python tools/check_agents_md.py                    # AGENTS.md at the project root
    uv run python tools/check_agents_md.py --max-lines 150    # another budget
    uv run python tools/check_agents_md.py docs/tools.md --max-bytes 0 --max-lines 0 \\
        --max-columns 0                                       # paths and links of any page

One line per finding, `<file>:<line>: <problem>`:

- Size. More than `--max-bytes` bytes (12288) or `--max-lines` lines (200): every line is
  paid for on every turn of every session.
- ASCII. A byte order mark or a character outside ASCII.
- Columns. A line longer than `--max-columns` (100).
- Imports. `@name` outside a code span or code block: Claude Code reads it as an import
  and loads that file on every turn. Name the file as a plain path in backticks.
- Paths. A project path in a code span or a code block that does not exist: anything
  under `tools/`, `docs/`, `addons/`, `test/` or another top-level folder of the project,
  and any other word with a `/` that ends in `/` or in a file ending (`src/sim/`,
  `src/player.gd`), with or without `./` or `res://` in front (so the script of every
  command shown). Upper and lower case must match: a path that is there in other letters
  (`Docs/` for `docs/`, which Windows and macOS open all the same) is reported as
  `is spelled ... in the project`.
- Files. A bare file name in a code span or a code block (`kit.json`, `check.ps1`) that
  is neither at the project root nor the name of any file in the project. A dot-file
  that is not at the project root: the kit's own (`.gdlintrc`, `.gitignore`, ...) and any
  name with a file ending (`.pre-commit-config.yaml`).
- Links. A Markdown link to a file that does not exist (relative to the page).

A limit of 0 is off. Not checked:

- Text with `<`, `>`, `*`, `{`, `}`, `$`, `%` or `|` in it (a placeholder: write
  `test_<name>.gd`), beyond the folders written out in front of a `<name>`
  (`test/sequences/<name>.json` needs a `test/sequences/`). Absolute paths, `../`
  paths, URLs, `user://` and `uid://`.
- Any other dot-name: `.gd` is an ending, `.position` a property, `.gdignore` sits in
  folders, and none of them can be told from a root file that is gone.
- What is on one machine and not on the next: `artifacts/`, `.godot/`, `.tools/`,
  `.venv/`, `.env`, and every top-level name the project's `.gitignore` lists.
- What a kit tool writes on demand: `AGENTS.kit.md`, `.mcp.json`,
  `tools/kit_manifest.json`, `tools/ci/architecture_allowlist.txt`,
  `tools/ci/test_timings.json`. `--allow GLOB` (repeatable) passes one more.
- Paths under a heading that carries `(sample)`: that section describes the kit's sample
  game and is the owner's to replace. The last line counts what was left out.

Exit 0 clean, 1 findings, 2 usage (unknown option, no such file, unreadable file).
Standard library only; needs nothing else from the kit.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "AGENTS.md"
MAX_BYTES = 12288
MAX_LINES = 200
MAX_COLUMNS = 100
# A section under a heading with this text is the kit's sample: its paths are not findings.
SAMPLE_MARK = "(sample)"

# Checked even when the folder is missing: a page that names tools/x.ps1 means the kit's.
KNOWN_ROOTS = ("tools", "docs", "addons", "test")
# Present on one machine and not on the next: never checked. The .gitignore adds to it.
LOCAL_NAMES = frozenset({"artifacts", ".godot", ".tools", ".venv", ".git", ".env"})
# Right to be absent: a kit tool writes it on demand, or the owner copies it in.
WRITTEN_LATER = (
    "AGENTS.kit.md",  # install.ps1, beside an AGENTS.md the project already had
    ".mcp.json",  # the owner's copy of tools/mcp/.mcp.json.example
    "tools/kit_manifest.json",  # install.ps1
    "tools/ci/architecture_allowlist.txt",  # check_architecture.py --baseline
    "tools/ci/test_timings.json",  # test_shards.py --write-timings
)
# A name with one of these endings is taken for a file of the project.
FILE_SUFFIXES = (".md", ".ps1", ".py", ".gd", ".json", ".toml", ".godot", ".tscn", ".tres")
FILE_SUFFIXES += (".cfg", ".yml", ".yaml", ".gdshader")
# The kit's own root dot-files: a page that names one means that file. Any other dot-name
# is checked only when it ends like a file (`.x.json`): `.gd` and `.position` are not files.
ROOT_DOTFILES = frozenset(
    {".gdlintrc", ".editorconfig", ".gitattributes", ".gitignore", ".python-version"}
)
PLACEHOLDER = re.compile(r"[<>*{}$%|]|\.\.\.")
BOM = b"\xef\xbb\xbf"

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TICKS = re.compile(r"`+")
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?\s*$")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
IMPORT = re.compile(r"(?:^|(?<=[\s(\[]))@[\w./~-]+")
SPLIT = re.compile(r"[\s\"'()\[\],;=`]+")
ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/~])")
LINE_SUFFIX = re.compile(r":\d+(?::\d+)?$")
BARE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")


def _limit(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 (off) or more")
    return value


def exists_exact(root: Path, relative: str) -> bool:
    """True when `relative` names a file or folder under `root`, letter case included
    (Windows would say yes to `Tools/Check.ps1`; a Linux runner would not)."""
    current = root
    for part in (part for part in relative.split("/") if part not in ("", ".")):
        if part == "..":
            current = current.parent
            continue
        try:
            names = os.listdir(current)
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return True


def other_spelling(root: Path, relative: str) -> str:
    """`relative` as the project spells it, when it is there in other letters (`Docs/x.md`
    for `docs/x.md`); "" when it is spelled the same or is not there at all."""
    current = root
    spelled: list[str] = []
    for part in relative.split("/"):
        if part in ("", "."):
            spelled.append(part)
            continue
        if part == "..":
            current = current.parent
            spelled.append(part)
            continue
        try:
            names = os.listdir(current)
        except OSError:
            return ""
        same = [name for name in names if name.lower() == part.lower()]
        if not same:
            return ""
        spelled.append(part if part in same else same[0])
        current = current / spelled[-1]
    found = "/".join(spelled)
    return "" if found == relative else found


def missing(root: Path, relative: str) -> str:
    """What is wrong with a path that exists_exact() does not find."""
    other = other_spelling(root, relative)
    if other:
        # Windows and macOS open either; Linux and git do not.
        return (
            f"is spelled `{other}` in the project: make the two spellings match "
            "(upper and lower case count)"
        )
    return "does not exist in the project"


class Project:
    """What the page's paths are checked against."""

    def __init__(self, root: Path, allow: list[str]) -> None:
        self.root = root
        self.allow = allow
        self._top: frozenset[str] | None = None
        self._names: frozenset[str] | None = None
        self._ignored: tuple[str, ...] | None = None

    def allowed(self, text: str) -> bool:
        if any(text in (later, later.rsplit("/", 1)[-1]) for later in WRITTEN_LATER):
            return True
        return any(fnmatch.fnmatchcase(text, pattern) for pattern in self.allow)

    def local(self, name: str) -> bool:
        """True for a top-level name that is right to be on one machine only."""
        if self._ignored is None:
            try:
                text = (self.root / ".gitignore").read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            patterns: list[str] = []
            for line in text.splitlines():
                pattern = line.strip()
                if not pattern or pattern[0] in "#!":
                    continue
                pattern = pattern.strip("/").removesuffix("/*")
                # A nested pattern is not a top-level name; a bare `*` would hide everything.
                if "/" not in pattern and pattern.strip("*"):
                    patterns.append(pattern)
            self._ignored = tuple(patterns)
        if name in LOCAL_NAMES:
            return True
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self._ignored)

    def top_folders(self) -> frozenset[str]:
        """Lower-cased names of the project's top-level folders, the known ones included."""
        if self._top is None:
            found = {name.lower() for name in KNOWN_ROOTS}
            for entry in os.scandir(self.root):
                if entry.is_dir():
                    found.add(entry.name.lower())
            self._top = frozenset(found)
        return self._top

    def file_names(self) -> frozenset[str]:
        """The name of every file in the project, outside what is on this machine only."""
        if self._names is None:
            names: set[str] = set()
            for _folder, folders, files in os.walk(self.root):
                folders[:] = [name for name in folders if name not in LOCAL_NAMES]
                names.update(files)
            self._names = frozenset(names)
        return self._names

    def problem(self, word: str) -> str:
        """Why `word`, one piece of code text, is a path the project does not have; ""
        when it is fine or not a project path at all."""
        placeholder = PLACEHOLDER.search(word)
        if placeholder:
            # The folders written out in front of a `<name>` are still a claim:
            # `test/sequences/<name>.json` says there is a `test/sequences/`. A glob
            # (`scripts/ui/**`) may describe a folder that is yet to come.
            if placeholder.group() != "<":
                return ""
            literal = word[: placeholder.start()]
            folder = literal[: literal.rfind("/") + 1].strip()
            if folder.startswith("res://"):
                folder = folder[len("res://") :]
            while folder.startswith("./"):
                folder = folder[2:]
            first = folder.split("/", 1)[0]
            if not first or "://" in folder or ABSOLUTE.match(folder) or self.local(first):
                return ""
            if first.lower() not in self.top_folders() or exists_exact(self.root, folder):
                return ""
            return f"`{folder}` (in front of a placeholder) {missing(self.root, folder)}"
        text = word.strip().rstrip(".:,")
        text = LINE_SUFFIX.sub("", text).replace("\\", "/")
        if text.startswith("res://"):
            text = text[len("res://") :]
        elif "://" in text or ABSOLUTE.match(text):
            return ""
        while text.startswith("./"):
            text = text[2:]
        if not text or text.startswith("../") or self.allowed(text):
            return ""
        if "/" in text:
            first = text.split("/", 1)[0]
            if self.local(first):
                return ""
            # `press/tap` and `Hud/Status` are not paths; `src/sim/` and `src/x.gd` are,
            # also when the project has no `src` folder (any more).
            shaped = text.endswith("/") or text.lower().endswith(FILE_SUFFIXES)
            if not shaped and first.lower() not in self.top_folders():
                return ""
            if exists_exact(self.root, text):
                return ""
            return f"`{text}` {missing(self.root, text)}"
        if text.startswith("."):
            low = text.lower()
            named = low.endswith(FILE_SUFFIXES) and low not in FILE_SUFFIXES
            if text not in ROOT_DOTFILES and not named:
                return ""
            if self.local(text) or exists_exact(self.root, text):
                return ""
            return f"`{text}` is not at the project root"
        if not BARE_NAME.match(text) or not text.lower().endswith(FILE_SUFFIXES):
            return ""
        if text in self.file_names():
            return ""
        return (
            f"`{text}` is no file in the project "
            f"(a placeholder goes in angle brackets, like `<name>{Path(text).suffix}`)"
        )


def _link_problem(target: str, page: Path, project: Project) -> str:
    if target.startswith("#") or SCHEME.match(target) or PLACEHOLDER.search(target):
        return ""
    path = unquote(target.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    if not path or project.allowed(path):
        return ""
    if exists_exact(page.parent, path):
        return ""
    other = other_spelling(page.parent, path)
    if other:
        return f"link to `{path}`, which is spelled `{other}` on disk (upper and lower case count)"
    return f"link to `{path}`, which does not exist (links are relative to this page)"


def _closing(line: str, start: int, run: str) -> int:
    """Where the backtick run that closes `run` starts in `line`, from `start`; -1 if none."""
    for match in TICKS.finditer(line, start):
        if match.group() == run:
            return match.start()
    return -1


def _spans(line: str, pending: str) -> tuple[str, list[str], str]:
    """One line outside a code block as (prose, code pieces, backtick run left open).

    `pending` is the run a code span of the line above left open: a span may wrap. Code is
    blanked out of the prose, so a column there is still a column of the line."""
    prose: list[str] = []
    code: list[str] = []
    position = 0
    while True:
        if pending:
            close = _closing(line, position, pending)
            if close < 0:
                code.append(line[position:])
                prose.append(" " * (len(line) - position))
                return "".join(prose), code, pending
            code.append(line[position:close])
            prose.append(" " * (close + len(pending) - position))
            position = close + len(pending)
        opened = TICKS.search(line, position)
        if opened is None:
            prose.append(line[position:])
            return "".join(prose), code, ""
        prose.append(line[position : opened.start()] + " " * len(opened.group()))
        position = opened.end()
        pending = opened.group()


def findings_of(
    raw: bytes,
    page: Path,
    project: Project,
    max_bytes: int = MAX_BYTES,
    max_lines: int = MAX_LINES,
    max_columns: int = MAX_COLUMNS,
) -> tuple[list[tuple[int, str]], int]:
    """Every finding as (line number, problem), line 0 being the page as a whole, and the
    number of missing paths left out because they sit under a `(sample)` heading."""
    found: list[tuple[int, str]] = []
    if raw.startswith(BOM):
        found.append((1, "starts with a byte order mark: save it as UTF-8 without one"))
        raw = raw[len(BOM) :]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return [*found, (0, f"is not UTF-8 text ({error.reason} at byte {error.start})")], 0
    lines = text.splitlines()
    if max_bytes and len(raw) > max_bytes:
        found.append((0, f"{len(raw):,} bytes; the budget is {max_bytes:,} (--max-bytes)"))
    if max_lines and len(lines) > max_lines:
        found.append((0, f"{len(lines)} lines; the budget is {max_lines} (--max-lines)"))

    fenced = False
    pending = ""  # the backtick run of a code span still open from the line above
    sample_level = 0  # heading level of the (sample) section the line is in; 0 outside one
    left_out = 0
    for number, line in enumerate(lines, 1):
        for column, character in enumerate(line, 1):
            if ord(character) > 127:
                point = f"U+{ord(character):04X}"
                found.append((number, f"non-ASCII character {point} at column {column}"))
                break
        if max_columns and len(line) > max_columns:
            found.append((number, f"{len(line)} columns; the limit is {max_columns}"))

        if FENCE.match(line):
            fenced = not fenced
            pending = ""
            continue
        heading = None if fenced else HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if sample_level and level <= sample_level:
                sample_level = 0
            if SAMPLE_MARK in heading.group(2):
                sample_level = level
        problems: list[str] = []
        if fenced:
            code = [line]
        else:
            if not line.strip():
                pending = ""  # a blank line ends the paragraph, and any span with it
            prose, code, pending = _spans(line, pending)
            for match in IMPORT.finditer(prose):
                problems.append(
                    f"{match.group(0).rstrip('.')} outside a code span is an import: Claude Code "
                    "loads that file on every turn. Write a plain path in backticks"
                )
            targets = [m.group(1) for m in LINK.finditer(prose)]
            reference = REFERENCE.match(prose)
            if reference:
                targets.append(reference.group(1))
            problems += [_link_problem(target, page, project) for target in targets]
        paths: list[str] = []
        for piece in code:
            paths += [project.problem(word) for word in SPLIT.split(piece)]
        paths = [problem for problem in dict.fromkeys(paths) if problem]  # once each, in order
        if sample_level:
            left_out += len(paths)
            paths = []
        for problem in dict.fromkeys(paths + problems):
            if problem:
                found.append((number, problem))
    return found, left_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint AGENTS.md: size, ASCII, line length, no imports, real paths.",
        epilog="Rules and what is not checked: the top of tools/check_agents_md.py.",
    )
    parser.add_argument("path", nargs="?", help=f"page to check (default: {DEFAULT_NAME})")
    parser.add_argument("--root", type=Path, default=ROOT, help="project folder")
    parser.add_argument("--max-bytes", type=_limit, default=MAX_BYTES, metavar="N")
    parser.add_argument("--max-lines", type=_limit, default=MAX_LINES, metavar="N")
    parser.add_argument("--max-columns", type=_limit, default=MAX_COLUMNS, metavar="N")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="GLOB",
        help="a path or file name that may be absent (repeatable)",
    )
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # A path the console's code page cannot show must not crash the report.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")

    root = args.root.resolve()
    if not root.is_dir():
        print(f"check_agents_md: --root is not a folder: {args.root}", file=sys.stderr)
        return 2
    page = Path(args.path).resolve() if args.path else root / DEFAULT_NAME
    try:
        raw = page.read_bytes()
    except OSError as error:
        reason = error.strerror or type(error).__name__
        print(f"check_agents_md: cannot read {page}: {reason}", file=sys.stderr)
        return 2
    try:
        shown = page.relative_to(root).as_posix()
    except ValueError:
        shown = str(page)

    found, left_out = findings_of(
        raw, page, Project(root, args.allow), args.max_bytes, args.max_lines, args.max_columns
    )
    for number, problem in found:
        print(f"{shown}:{number}: {problem}" if number else f"{shown}: {problem}")
    if found:
        print(f"agents-md: FAILED: {len(found)} finding(s) in {shown}")
        return 1
    note = f"; {left_out} missing path(s) under {SAMPLE_MARK} headings left out" if left_out else ""
    print(f"agents-md: ok ({shown}: {len(raw.splitlines())} lines, {len(raw):,} bytes{note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
