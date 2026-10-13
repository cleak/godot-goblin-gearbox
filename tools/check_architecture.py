"""Architecture check: which scripts may name which, kept true by a failing command.

A project that wants this writes tools/ci/architecture_modules.json. Without that file
the check prints "not configured" and exits 0.

    {
      "modules": {
        "core":  {"globs": ["scripts/core/**"], "may_depend": [], "pure": true},
        "game":  {"globs": ["scripts/game/**"], "may_depend": ["core"]},
        "tests": {"globs": ["test/**"], "may_depend": ["*"]}
      },
      "ignore": ["addons/**", "tools/**"],
      "cycles": true
    }

Every authored .gd (tools/kit_config.py; plus the test folder) belongs to the first
module whose globs match it, in file order, so put a narrow glob before a wide one.
`*` stays inside one folder, `**` crosses folders. Keys that start with `_` are comments.

Rules:

- Coverage. A script in no module and under no `ignore` glob fails. A glob that matches
  nothing fails, unless written `{"glob": "...", "optional": true}`.
- Layering. A script names another by `class_name`, by autoload name, by `extends`, or
  by a string that holds its `res://` path or `uid://` (in `preload`, in `load`, or in a
  constant handed to them). Naming a script in another module fails unless that module
  is in `may_depend` (`"*"` allows any).
- Purity, for a module with `"pure": true` (logic that must run headless, without a
  scene tree): no `signal`, no autoload name, and no class that extends a Node, directly
  or through other scripts. tools/ci/node_classes.txt lists the engine's Node classes;
  `--update-node-classes` rewrites it from the Godot the kit's tools use (GODOT_BIN, else
  the resolver: tools/ci/godot_binary.py).
- Cycles. Scripts that `preload` or `extends` each other in a ring fail. Godot loads a
  ring of scripts (checked on 4.7.2), so this is a design rule, not a crash guard: no
  script in a ring can be loaded, tested or reused without all the others, and a ring
  cannot be split into layers. `"cycles": false` in the manifest turns the rule off.
- Allowlist (optional). tools/ci/architecture_allowlist.txt holds exceptions, one per
  line, `#` for comments: `source.gd -> target.gd` for layering, `a.gd <-> b.gd` for a
  ring. It may only shrink: a line the tree no longer needs fails until removed
  (`--shrink` removes them).

    uv run python tools/check_architecture.py
    uv run python tools/check_architecture.py --shrink      # drop stale allowlist lines
    uv run python tools/check_architecture.py --baseline    # adopt: allowlist today's violations
    uv run python tools/check_architecture.py --update-node-classes

Not seen: scenes (a script that loads a .tscn is not followed into it), paths built at
run time (`"res://levels/%s.gd" % name`), and Node classes from a GDExtension.

Exit 0 when the rules hold or the check is not configured, 1 on a violation, 2 when the
manifest or kit.json is wrong.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "ci"))
import class_cache  # noqa: E402
import gate_support  # noqa: E402
import godot_binary  # noqa: E402
import kit_config  # noqa: E402
import project_refs  # noqa: E402
import slot_pool  # noqa: E402

MANIFEST = "tools/ci/architecture_modules.json"
ALLOWLIST = "tools/ci/architecture_allowlist.txt"
NODE_CLASSES = "tools/ci/node_classes.txt"
ALLOWLIST_HEADER = (
    "# Exceptions for tools/check_architecture.py: `source.gd -> target.gd` (layering)\n"
    "# or `a.gd <-> b.gd` (a ring). Shrink-only: delete a line when its reference is\n"
    "# gone. Do not add lines.\n"
)
RING = " <-> "
MODULE_KEYS = {"globs", "may_depend", "pure"}
TOP_KEYS = {"modules", "ignore", "cycles"}

SIGNAL = re.compile(r"^\s*signal\s+\w+", re.MULTILINE)
_BASE = r"extends[ \t]+(?:(\w+)|[\"'](res://[^\"']+|uid://\w+)[\"'])"
# `extends Base` or `extends "res://base.gd"`: on its own line, after `class_name Name`,
# or after an inner `class Name`.
EXTENDS = re.compile(rf"^[ \t]*(?:class(?:_name)?[ \t]+\w+[ \t]+)?{_BASE}", re.MULTILINE)
PRELOADED = re.compile(r"\bpreload\(\s*[\"'](res://[^\"']+|uid://\w+)[\"']")
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
NODE_CLASSES_VERSION = re.compile(r"^# Godot (\d+\.\d+)")


class ManifestError(ValueError):
    """The manifest (or a file it needs) is missing a piece or malformed."""


def glob_regex(glob: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _notes_dropped(mapping: dict, allowed: set[str], where: str) -> dict:
    kept = {key: value for key, value in mapping.items() if not key.startswith("_")}
    unknown = sorted(set(kept) - allowed)
    if unknown:
        raise ManifestError(f"{where}: unknown key(s) {', '.join(unknown)}")
    return kept


def load_manifest(path: Path) -> dict:
    """The manifest as {"modules": {name: {"globs": [(glob, optional)], "may_depend": [...],
    "pure": bool}}, "ignore": [glob], "cycles": bool}. Raises ManifestError."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{path.name}: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError(f"{path.name}: expected a JSON object")
    top = _notes_dropped(raw, TOP_KEYS, path.name)
    declared = top.get("modules")
    if not isinstance(declared, dict) or not declared:
        raise ManifestError(f'{path.name}: "modules" must be an object with at least one module')
    modules: dict[str, dict] = {}
    for name, body in declared.items():
        if name.startswith("_"):
            continue
        if not isinstance(body, dict):
            raise ManifestError(f"{path.name}: module {name} must be an object")
        module = _notes_dropped(body, MODULE_KEYS, f"{path.name}: module {name}")
        globs: list[tuple[str, bool]] = []
        for entry in module.get("globs") or []:
            if isinstance(entry, str):
                globs.append((entry, False))
            elif isinstance(entry, dict) and isinstance(entry.get("glob"), str):
                globs.append((entry["glob"], bool(entry.get("optional"))))
            else:
                raise ManifestError(f"{path.name}: module {name}: bad glob entry {entry!r}")
        if not globs:
            raise ManifestError(f'{path.name}: module {name} needs a non-empty "globs" list')
        may_depend = module.get("may_depend", [])
        if not isinstance(may_depend, list) or not all(isinstance(m, str) for m in may_depend):
            raise ManifestError(f'{path.name}: module {name}: "may_depend" must be a list of names')
        modules[name] = {
            "globs": globs,
            "may_depend": may_depend,
            "pure": bool(module.get("pure", False)),
        }
    for name, module in modules.items():
        for target in module["may_depend"]:
            if target != "*" and target not in modules:
                raise ManifestError(
                    f"{path.name}: module {name} may_depend names unknown module '{target}'"
                )
    ignore = top.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(glob, str) for glob in ignore):
        raise ManifestError(f'{path.name}: "ignore" must be a list of globs')
    cycles = top.get("cycles", True)
    if not isinstance(cycles, bool):
        raise ManifestError(f'{path.name}: "cycles" must be true or false')
    return {"modules": modules, "ignore": ignore, "cycles": cycles}


def load_node_classes(path: Path) -> tuple[str, frozenset[str]]:
    """(the Godot major.minor the list was made from, the Node class names)."""
    if not path.is_file():
        raise ManifestError(
            f"{NODE_CLASSES} is missing; a pure module needs it. "
            "Create it with: uv run python tools/check_architecture.py --update-node-classes"
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    version = NODE_CLASSES_VERSION.match(lines[0]) if lines else None
    names = frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))
    return (version.group(1) if version else ""), names


class Tree:
    """The scanned project: scripts, the module each belongs to, and who names whom."""

    def __init__(
        self, config: kit_config.KitConfig, manifest: dict, node_classes: frozenset[str]
    ) -> None:
        self.config = config
        self.modules: dict[str, dict] = manifest["modules"]
        self.node_classes = node_classes
        self.problems: list[str] = []
        files = set(gate_support.gather(config))
        if config.test_dir.is_dir():
            files.update(kit_config.source_files((".gd",), path=config.test_dir, config=config))
        ignored = [glob_regex(glob) for glob in manifest["ignore"]]
        self.text: dict[str, str] = {}
        for path in sorted(files):
            relative = gate_support.relative(path, config)
            if not any(pattern.match(relative) for pattern in ignored):
                self.text[relative] = path.read_text(encoding="utf-8", errors="replace")
        self.code = {path: project_refs.code_only(text) for path, text in self.text.items()}
        self.quoted = {
            path: project_refs.without_comments(text) for path, text in self.text.items()
        }
        self.classes = project_refs.class_names(self.text)
        self.uids = project_refs.uid_owners(config.root, self.text)
        self.autoloads = project_refs.autoloads(config.root)
        self._all_classes: dict[str, str] | None = None
        self.module_of = self._assign()
        self.edges: dict[str, set[str]] = {}
        self.preloads: dict[str, set[str]] = {}
        self._link()

    def _assign(self) -> dict[str, str]:
        owner: dict[str, str] = {}
        for name, module in self.modules.items():
            for glob, optional in module["globs"]:
                pattern = glob_regex(glob)
                hits = [path for path in self.text if pattern.match(path)]
                if not hits and not optional:
                    self.problems.append(
                        f"glob matches no script: module {name}: {glob} (fix it in {MANIFEST})"
                    )
                for path in hits:
                    owner.setdefault(path, name)
        for path in self.text:
            if path not in owner:
                self.problems.append(
                    f"no module: {path} (add it to a module or to ignore in {MANIFEST})"
                )
        if self.text and not owner:
            self.problems.append("no script belongs to any module")
        return owner

    def _target(self, reference: str) -> str | None:
        """The scanned script a `res://` or `uid://` reference names, if any."""
        path = self.uids.get(reference) or reference.removeprefix("res://")
        return path if path in self.text else None

    def _link(self) -> None:
        named_by: dict[str, str] = dict(self.classes)
        for name, reference in self.autoloads.items():
            target = self._target(reference)
            if target:
                named_by.setdefault(name, target)
        for path in self.text:
            words = set(IDENT.findall(self.code[path]))
            deps = {named_by[word] for word in words if word in named_by}
            quoted = self.quoted[path]
            # Any string that holds a script's path or id names it: `load(PATH)` reaches
            # the script through the constant just as `load("res://...")` does.
            literals = project_refs.RES_PATH.findall(quoted) + project_refs.UID.findall(quoted)
            deps.update(filter(None, map(self._target, literals)))
            hard = set(filter(None, map(self._target, PRELOADED.findall(quoted))))
            for _, reference in EXTENDS.findall(quoted):
                target = self._target(reference) if reference else None
                if target:
                    deps.add(target)
                    hard.add(target)
            deps.discard(path)
            hard.discard(path)
            self.edges[path] = deps
            self.preloads[path] = hard

    def allowed(self, source: str, target: str) -> bool:
        a, b = self.module_of.get(source), self.module_of.get(target)
        if a is None or b is None or a == b:
            return True
        may = self.modules[a]["may_depend"]
        return "*" in may or b in may

    def violations(self) -> list[str]:
        return sorted(
            f"{src} -> {dst}"
            for src, deps in self.edges.items()
            for dst in deps
            if not self.allowed(src, dst)
        )

    def _class_file(self, name: str) -> Path | None:
        """The script that declares `class_name name`, scanned or not (an addon's too)."""
        if name in self.classes:
            return self.config.root / self.classes[name]
        if self._all_classes is None:
            self._all_classes = {
                declared: path.removeprefix("res://")
                for declared, path in class_cache.classes_on_disk(self.config)
            }
        found = self._all_classes.get(name)
        return self.config.root / found if found else None

    def _is_node(self, base: str, reference: str, seen: frozenset[Path]) -> bool:
        """Whether a class that extends `base` (a name) or `reference` (a path) is a Node."""
        if reference:
            relative = self.uids.get(reference) or reference.removeprefix("res://")
            script = self.config.root / relative
        else:
            if base in self.node_classes:
                return True
            script = self._class_file(base) or Path()
        if not script.is_file() or script in seen:
            return False
        name, path = project_refs.own_base(script.read_text(encoding="utf-8", errors="replace"))
        if path and "://" not in path:  # `extends "base.gd"`: a file beside the script
            beside = (script.parent / path).resolve()
            inside = self.config.root in beside.parents
            path = "res://" + beside.relative_to(self.config.root).as_posix() if inside else ""
        return bool(name or path) and self._is_node(name, path, seen | {script})

    def purity(self) -> list[str]:
        problems: list[str] = []
        for path, module in sorted(self.module_of.items()):
            if not self.modules[module]["pure"]:
                continue
            code = self.code[path]
            if SIGNAL.search(code):
                problems.append(f"{path}: signal in pure module {module}")
            for base, reference in EXTENDS.findall(self.quoted[path]):
                if self._is_node(base, reference, frozenset()):
                    problems.append(
                        f"{path}: extends {base or reference}, a Node, in pure module {module}"
                    )
            for name in sorted(set(IDENT.findall(code)) & set(self.autoloads)):
                problems.append(f"{path}: autoload {name} used in pure module {module}")
        return problems

    def cycles(self) -> list[str]:
        """Rings in the preload graph (its strongly connected components of two or more),
        each as `a.gd <-> b.gd`, members sorted."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        found: list[list[str]] = []
        counter = 0
        for start in sorted(self.preloads):
            if start in index:
                continue
            work = [(start, iter(sorted(self.preloads[start])))]
            index[start] = low[start] = counter
            counter += 1
            stack.append(start)
            on_stack.add(start)
            while work:
                node, children = work[-1]
                advanced = False
                for child in children:
                    if child not in index:
                        index[child] = low[child] = counter
                        counter += 1
                        stack.append(child)
                        on_stack.add(child)
                        work.append((child, iter(sorted(self.preloads.get(child, ())))))
                        advanced = True
                        break
                    if child in on_stack:
                        low[node] = min(low[node], index[child])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        found.append(sorted(component))
        return [RING.join(component) for component in sorted(found)]


def _normal(line: str) -> str:
    return re.sub(r"\s+", " ", line.split("#", 1)[0].strip())


def parse_allowlist(text: str) -> list[str]:
    return [line for line in map(_normal, text.splitlines()) if line]


def check(root: Path | None = None, shrink: bool = False, baseline: bool = False) -> list[str]:
    """Every problem under `root` (default: this project); [] when the rules hold.
    Raises ManifestError. Call only when the manifest exists (see `configured`)."""
    config = kit_config.load(root)
    manifest = load_manifest(config.root / MANIFEST)
    node_classes: frozenset[str] = frozenset()
    problems: list[str] = []
    if any(module["pure"] for module in manifest["modules"].values()):
        made_for, node_classes = load_node_classes(config.root / NODE_CLASSES)
        if made_for != config.godot_minor:
            problems.append(
                f"{NODE_CLASSES} lists Godot {made_for or '?'} classes but kit.json pins "
                f"{config.godot_minor}: run with --update-node-classes"
            )
    tree = Tree(config, manifest, node_classes)
    problems += tree.problems
    # What the allowlist may hold: layering lines (`a -> b`) and rings (`a <-> b`).
    needed = tree.violations() + (tree.cycles() if manifest["cycles"] else [])
    allow_path = config.root / ALLOWLIST
    if baseline:
        allow_path.parent.mkdir(parents=True, exist_ok=True)
        body = ALLOWLIST_HEADER + "".join(f"{line}\n" for line in needed)
        allow_path.write_text(body, encoding="utf-8", newline="\n")
        print(f"wrote {ALLOWLIST} ({len(needed)} line(s))", file=sys.stderr)
    raw = allow_path.read_text(encoding="utf-8") if allow_path.is_file() else ""
    listed = set(parse_allowlist(raw))
    stale = sorted(listed - set(needed))
    if shrink and stale:
        kept = [line for line in raw.splitlines() if _normal(line) not in stale]
        allow_path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
        print(f"removed {len(stale)} stale line(s) from {ALLOWLIST}", file=sys.stderr)
        stale = []
    problems += [f"stale allowlist line (remove it, or run --shrink): {line}" for line in stale]
    for line in needed:
        if line not in listed:
            problems.append(f"{'preload cycle' if RING in line else 'not allowed'}: {line}")
    problems += tree.purity()
    return problems


def configured(root: Path | None = None) -> bool:
    return (kit_config.load(root).root / MANIFEST).is_file()


def node_classes_from_api(api: dict) -> list[str]:
    """Node and every class that inherits from it, from Godot's extension API dump."""
    parent = {entry["name"]: entry.get("inherits", "") for entry in api["classes"]}

    def is_node(name: str) -> bool:
        while name and name != "Node":
            name = parent.get(name, "")
        return name == "Node"

    return sorted(name for name in parent if is_node(name))


def update_node_classes(root: Path | None = None) -> int:
    """Rewrite tools/ci/node_classes.txt from the class list of the Godot the kit's
    resolver finds (tools/ci/godot_binary.py; GODOT_BIN names another)."""
    config = kit_config.load(root)
    binary = godot_binary.resolve(config)
    with tempfile.TemporaryDirectory(prefix="godot_api_") as folder:
        code, output = slot_pool.run_captured(
            [binary, "--headless", "--dump-extension-api"], 300, cwd=Path(folder)
        )
        dump = Path(folder) / "extension_api.json"
        if code != 0 or not dump.is_file():
            print(f"godot --dump-extension-api failed (exit {code}):\n{output}", file=sys.stderr)
            return 1
        api = json.loads(dump.read_text(encoding="utf-8"))
    names = node_classes_from_api(api)
    version = api.get("header", {})
    label = f"{version.get('version_major')}.{version.get('version_minor')}"
    header = (
        f"# Godot {label}: Node and every engine class that inherits from it.\n"
        "# tools/check_architecture.py reads this for its purity rule. Do not edit;\n"
        "# rewrite with: uv run python tools/check_architecture.py --update-node-classes\n"
    )
    target = config.root / NODE_CLASSES
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + "\n".join(names) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {NODE_CLASSES}: {len(names)} classes from Godot {label}")
    return 0


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check module layering, purity and preload cycles against "
        f"{MANIFEST}. Rules and manifest format: the top of tools/check_architecture.py."
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--shrink", action="store_true", help="drop stale allowlist lines")
    actions.add_argument(
        "--baseline",
        action="store_true",
        help="write the allowlist from today's layering violations and rings (to adopt the check)",
    )
    actions.add_argument(
        "--update-node-classes",
        action="store_true",
        help=f"rewrite {NODE_CLASSES} from the Godot the kit's tools use (GODOT_BIN, "
        "else the resolver)",
    )
    args = parser.parse_args(argv)
    gate_support.plain_output()
    try:
        if args.update_node_classes:
            return update_node_classes(root)
        if not configured(root):
            print(f"architecture: not configured (no {MANIFEST})")
            return 0
        problems = check(root, shrink=args.shrink, baseline=args.baseline)
    except (ManifestError, kit_config.ConfigError, godot_binary.GodotNotFoundError) as error:
        print(error, file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    print(f"architecture: {f'FAIL ({len(problems)})' if problems else 'ok'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
