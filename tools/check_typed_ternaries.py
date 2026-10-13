"""Flags an array or dictionary literal in a ternary that feeds a typed container.

The bug: GDScript gives a literal (`[]`, `[a, b]`, `{}`) the element type of its
destination only where the two meet directly. Inside a ternary the literal stays a
plain, untyped Array or Dictionary. So this line parses, passes the editor's checks,
and works until `ok` is false:

    var names: Array[String] = load_names() if ok else []

On that branch it raises "Trying to assign an array of type "Array" to a variable of
type "Array[String]"" at run time and the function stops there (the process carries on
and still exits 0). Likewise `return rows if ok else []` in a `-> Array[int]` function
hands its caller an untyped array, and the caller's typed assignment is what fails.

Flagged, with the ternary at the top level of the right-hand side:

- `var x: Array[...] = ...` / `var x: Dictionary[...] = ...`
- `x = ...` where the `x` in scope (local, parameter, then member) is typed that way
- `return ...` inside a `func ... -> Array[...]:` / `-> Dictionary[...]:` (lambdas too)

The fix is a typed empty value: `var names: Array[String] = []`, then
`if ok: names = load_names()`.

Not seen: `var x := ...` (the inferred type is the first branch's) and an argument
passed to a typed parameter. Both fail the same way.

Exceptions: end the line with `# typed-ternary-ok: reason`, or list the file in
tools/ci/typed_ternary_allowlist.txt (optional; `path | reason` per line, see
tools/ci/gate_support.py). An entry with nothing left to flag fails: remove it.

    uv run python tools/check_typed_ternaries.py                # all authored .gd
    uv run python tools/check_typed_ternaries.py scripts/ui     # these files or folders

Exit 0 clean; 1 on a finding, a stale allowlist entry, or no .gd file to scan; 2 on a
bad path, allowlist or kit.json.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))
import gate_support  # noqa: E402
import kit_config  # noqa: E402

ALLOWLIST = "tools/ci/typed_ternary_allowlist.txt"
OK_MARK = re.compile(r"#\s*typed-ternary-ok:\s*\S")

STRING = re.compile(
    r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''
)
TYPED = r"(?:Array|Dictionary)\s*\["
ANNOTATIONS = r"(?:@\w+(?:\([^)]*\))?\s+)*"
TYPED_DECL = re.compile(
    rf"^\s*{ANNOTATIONS}(?:static\s+)?var\s+(\w+)\s*:\s*{TYPED}[^=]*=(?!=)(.*)$"
)
VAR_DECL = re.compile(rf"^\s*{ANNOTATIONS}(?:static\s+)?var\s+(\w+)\s*(:\s*{TYPED})?")
TYPED_PARAM = re.compile(r"^\s*(\w+)\s*(:\s*(?:Array|Dictionary)\s*X)?")
ASSIGN = re.compile(r"^\s*(self\.)?(\w+)\s*=(?!=)(.*)$")
RETURN = re.compile(r"^\s*return\b(.*)$")
FUNC = re.compile(r"\bfunc\b[^(]*\((?P<args>.*?)\)\s*(?:->\s*(?P<ret>[^:]+))?:")
EMPTY_IF = re.compile(r"(?:^|\s)(?:\[\]|\{\})\s+if\s")
ELSE_EMPTY = re.compile(r"\selse\s+(?:\[\]|\{\})\s*$")
WORD_END = re.compile(r"\w+$")
KEYWORDS = frozenset({"if", "else", "return", "and", "or", "not", "in", "await"})
OPEN = "([{"
CLOSE = ")]}"


def _blank_strings(text: str) -> str:
    """String literals become `""`; newlines inside them are kept so lines still count."""
    return STRING.sub(lambda m: '""' + "\n" * m.group(0).count("\n"), text)


def _logical_lines(text: str) -> list[tuple[int, int, str]]:
    """(first line, last line, code) per statement: comments dropped, bracket and
    backslash continuations joined."""
    out: list[tuple[int, int, str]] = []
    parts: list[str] = []
    start = 0
    depth = 0
    for number, raw in enumerate(_blank_strings(text).splitlines(), 1):
        code = raw.split("#", 1)[0].rstrip()
        if not parts:
            start = number
        for ch in code:
            if ch in OPEN:
                depth += 1
            elif ch in CLOSE:
                depth = max(0, depth - 1)
        cont = code.endswith("\\")
        parts.append(code[:-1] if cont else code)
        if depth > 0 or cont:
            continue
        out.append((start, number, " ".join(p.strip() if i else p for i, p in enumerate(parts))))
        parts = []
    if parts:
        out.append((start, start + len(parts) - 1, " ".join(parts)))
    return out


def _unwrap(expr: str) -> str:
    """`expr` without parentheses that wrap all of it."""
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        for i, ch in enumerate(expr):
            depth += 1 if ch in OPEN else -1 if ch in CLOSE else 0
            if depth == 0 and i < len(expr) - 1:
                return expr
        expr = expr[1:-1].strip()
    return expr


def _subscripts(before: str) -> bool:
    """A `[` / `{` after `before` subscripts or types a value (`a[0]`, `f()[1]`,
    `Array[int]`), rather than opening a literal."""
    word = WORD_END.search(before.rstrip())
    if word:
        return word.group(0) not in KEYWORDS
    return before.rstrip()[-1:] in (")", "]", '"')


def _top_level(expr: str) -> str:
    """`expr` with each top-level bracket group folded: an array or dictionary literal
    to `[]` / `{}`, a call, subscript or parenthesised group to `X`."""
    out: list[str] = []
    depth = 0
    group: list[str] = []
    for ch in expr:
        if depth == 0 and ch not in OPEN:
            out.append(ch)
            continue
        group.append(ch)
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth -= 1
            if depth == 0:
                literal = group[0] != "(" and not _subscripts("".join(out))
                out.append(group[0] + group[-1] if literal else "X")
                group = []
    return "".join(out)


def has_empty_ternary(expr: str) -> bool:
    """True when `expr` is a ternary with an array or dictionary literal operand."""
    flat = " " + _top_level(_unwrap(expr)) + " "
    if " if " not in flat or " else " not in flat:
        return False
    return bool(EMPTY_IF.search(flat) or ELSE_EMPTY.search(flat.rstrip()))


def _params(args: str) -> dict[str, bool]:
    """Parameter name -> declared Array[...] / Dictionary[...]."""
    out: dict[str, bool] = {}
    for part in _top_level(args).split(","):
        param = TYPED_PARAM.match(part)
        if param:
            out[param.group(1)] = param.group(2) is not None
    return out


def _members(lines: list[tuple[int, int, str]]) -> set[str]:
    """Typed vars declared outside every function (class members)."""
    stack: list[int] = []
    out: set[str] = set()
    for _, _, code in lines:
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip())
        while stack and stack[-1] >= indent:
            stack.pop()
        decl = VAR_DECL.match(code)
        if decl and decl.group(2) and not stack:
            out.add(decl.group(1))
        if FUNC.search(code):
            stack.append(indent)
    return out


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, reason) of each untyped empty literal on a typed path."""
    raw_lines = text.splitlines()
    lines = _logical_lines(text)
    members = _members(lines)
    # (indent, returns a typed container, local or param name -> typed), innermost last
    funcs: list[tuple[int, bool, dict[str, bool]]] = []
    hits: list[tuple[int, str]] = []

    def typed_var(name: str, member: bool) -> bool:
        """Locals shadow members, innermost scope first; `self.x` is always the member."""
        for _, _, names in () if member else reversed(funcs):
            if name in names:
                return names[name]
        return name in members

    for first, last, code in lines:
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip())
        while funcs and funcs[-1][0] >= indent:
            funcs.pop()
        marked = any(OK_MARK.search(raw_lines[n - 1]) for n in range(first, last + 1))
        reason = ""
        decl = TYPED_DECL.match(code)
        assign = ASSIGN.match(code)
        ret = RETURN.match(code)
        if decl and has_empty_ternary(decl.group(2)):
            reason = f"typed var `{decl.group(1)}`"
        elif (
            assign
            and typed_var(assign.group(2), assign.group(1) is not None)
            and has_empty_ternary(assign.group(3))
        ):
            reason = f"typed var `{assign.group(2)}`"
        elif ret and funcs and funcs[-1][1] and has_empty_ternary(ret.group(1)):
            reason = "typed return"
        local = VAR_DECL.match(code)
        if local and funcs:
            funcs[-1][2][local.group(1)] = local.group(2) is not None
        func = FUNC.search(code)
        if func:
            typed = re.match(TYPED, (func.group("ret") or "").strip()) is not None
            funcs.append((indent, typed, _params(func.group("args"))))
            body = RETURN.match(code[func.end() :])  # one-line `func f() -> T: return ...`
            if not reason and typed and body and has_empty_ternary(body.group(1)):
                reason = "typed return"
        if reason and not marked:  # a mark exempts its statement, not the scope it opens
            hits.append((first, reason))
    return hits


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def scan(files: list[Path], config: kit_config.KitConfig, allowlist: dict[str, str]) -> list[str]:
    """One `path:line: message` per flagged site in `files`, allowlisted files left out."""
    problems: list[str] = []
    for path in files:
        rel = gate_support.relative(path, config)
        if gate_support.allowed(rel, allowlist):
            continue
        for number, reason in scan_text(_read(path)):
            problems.append(f"{rel}:{number}: untyped [] / {{}} in a ternary on a {reason}")
    return problems


def stale_entries(config: kit_config.KitConfig, allowlist: dict[str, str]) -> list[str]:
    """Allowlist entries that cover no file with a flagged site (remove them)."""
    return [
        key
        for key in allowlist
        if not any(scan_text(_read(path)) for path in gate_support.entry_files(key, config))
    ]


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flag [] / {} literals in ternaries that feed a typed Array or Dictionary."
    )
    parser.add_argument("paths", nargs="*", help="files or folders (default: all authored .gd)")
    args = parser.parse_args(argv)
    gate_support.plain_output()
    try:
        config = kit_config.load(root)
        files = gate_support.gather(config, args.paths)
        allowlist = gate_support.load_allowlist(config.root / ALLOWLIST)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if not files:
        print("typed ternaries: no .gd file to scan", file=sys.stderr)
        return 1
    problems = scan(files, config, allowlist)
    # A narrowed run cannot tell whether an entry elsewhere is stale.
    stale = [] if args.paths else stale_entries(config, allowlist)
    for line in problems:
        print(line)
    for key in stale:
        print(f"{ALLOWLIST}: stale entry (nothing left to flag), remove it: {key}")
    if problems:
        print(f"{len(problems)} site(s). The fix is at the top of tools/check_typed_ternaries.py.")
    else:
        print(f"typed ternaries: {'FAIL' if stale else 'ok'} ({len(files)} files)")
    return 1 if problems or stale else 0


if __name__ == "__main__":
    sys.exit(main())
