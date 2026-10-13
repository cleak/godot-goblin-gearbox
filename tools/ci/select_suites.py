"""Pick the gdUnit4 suites a change can affect: "run only the tests that matter".

A reference graph over the project's authored scripts, scenes, resources and shaders
(tools/kit_config.py decides what "authored" means, from kit.json). An edge runs from a
file to each file it names: by `res://` path, by `uid://` id, by a script's `class_name`,
or by an autoload's name. A change selects the suites reachable backwards from the
changed files:

- distance 0-1: the suite is the changed file, or names it directly. Always run.
- distance 2: the suite names something that names the changed file (a scene that uses
  the changed script, a helper that wraps it). Run unless the suite is heavy.
- further, or through a hub (a file that more than HUB_FAN_IN others name): not followed.
  This has to catch the likely breakage in seconds, not all of it; `--all` runs the rest.

Around the graph:

- A changed data file (a .json, a .png, a fixture) selects the files that name it, or
  name a folder it sits in (`"res://test/fixtures/"`).
- A changed `x.gd.uid` or `x.png.import` counts as a change to `x.gd` / `x.png`.
- Deleted and renamed files count as changed. A deleted script's `class_name` is read
  from the base commit, so the suites that still name it run (and fail, as they should).
- project.godot, kit.json, or source in a folder kit.json excludes (a third-party addon)
  can break anything: the selection "widens" and the caller runs every suite that is not
  heavy.
- The suites listed in tools/ci/always_run_suites.txt join any change to source.

Not seen: paths built at run time (`load("res://levels/%s.tscn" % name)`).

Run as a program it answers "what changed" for tools/check.ps1 -Changed:

    uv run python tools/ci/select_suites.py --changed-files [BASE]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_config  # noqa: E402
import project_refs  # noqa: E402
import suites as suite_rules  # noqa: E402

SOURCE_SUFFIXES = (".gd", ".tscn", ".tres", ".gdshader", ".gdshaderinc")
SIDECAR_SUFFIXES = (".uid", ".import")
IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")
# A change here can break any suite: project settings, or the pinned Godot and gdUnit4.
WIDENING_FILES = ("project.godot", kit_config.CONFIG_NAME)
# A file named by more than this many others is a hub: reaching it does not pull in
# everything that uses it.
HUB_FAN_IN = 40
MAX_DISTANCE = 2
BASE_BRANCHES = ("main", "master")


@dataclass
class Selection:
    """What a change selects: suites (res:// path -> distance), or everything."""

    suites: dict[str, int] = field(default_factory=dict)
    widened_by: str = ""
    changed: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def is_source(path: str, config: kit_config.KitConfig) -> bool:
    """True for a project-relative path that is (or, once deleted, was) authored source."""
    return path.lower().endswith(SOURCE_SUFFIXES) and project_refs.is_authored(path, config)


class ReferenceGraph:
    """Who names whom. `removed` maps source files the change deleted to their old
    class_name (or None); `watched` are other changed files (data), followed by path."""

    def __init__(
        self,
        repo: Path,
        removed: dict[str, str | None] | None = None,
        watched: set[str] | None = None,
    ) -> None:
        self.config = kit_config.load(repo)
        self.removed = removed or {}
        self.watched = watched or set()
        self.texts: dict[str, str] = {}
        files = set(kit_config.source_files(SOURCE_SUFFIXES, config=self.config))
        if self.config.test_dir.is_dir():
            # Suites count even when kit.json leaves the test folder out of the source set.
            files.update(
                kit_config.source_files(
                    SOURCE_SUFFIXES, path=self.config.test_dir, config=self.config
                )
            )
        for path in sorted(files):
            relative = project_refs.relative(path, self.config)
            self.texts[relative] = path.read_text(encoding="utf-8", errors="replace")
        # The same rule the runner uses (tools/ci/suites.py): a suite by any header, or
        # through a base suite.
        index = suite_rules.SuiteIndex(self.config, self.texts)
        self.suites = {
            path for path in self.texts if path.endswith(".gd") and index.may_be_suite(path)
        }
        uids = project_refs.uid_owners(self.config.root, self.texts)
        names = self._identifier_owners(uids)
        self.dependents: dict[str, set[str]] = defaultdict(set)
        for path, text in self.texts.items():
            for target in self._references(path, text, names, uids):
                self.dependents[target].add(path)
            # `extends "base.gd"` names a script beside this one without a res:// path.
            for base in index.chain(path)[0][1:2] if path.endswith(".gd") else ():
                if base in self.texts:
                    self.dependents[base].add(path)

    def _identifier_owners(self, uids: dict[str, str]) -> dict[str, str]:
        """class_name or autoload name -> the file that owns it."""
        owners = project_refs.class_names(self.texts)
        for path, name in self.removed.items():
            if name:
                owners.setdefault(name, path)
        for name, target in project_refs.autoloads(self.config.root).items():
            owner = uids.get(target) or target.removeprefix("res://")
            owners.setdefault(name, owner)
        return owners

    def _references(
        self, path: str, text: str, owners: dict[str, str], uids: dict[str, str]
    ) -> set[str]:
        named = {found.removeprefix("res://") for found in project_refs.RES_PATH.findall(text)}
        found = set(named)
        found.update(uids[uid] for uid in project_refs.UID.findall(text) if uid in uids)
        if path.endswith(".gd"):
            found.update(owners[name] for name in set(IDENTIFIER.findall(text)) if name in owners)
        # A folder path names every watched file under it.
        folders = tuple(name.rstrip("/") + "/" for name in named if name.strip("/"))
        found.update(target for target in self.watched if target.startswith(folders))
        known = self.texts.keys() | self.removed.keys() | self.watched
        return {target for target in found if target in known and target != path}

    def affected_suites(self, changed: list[str]) -> dict[str, int]:
        """Suites reachable backwards from the changed files, by distance."""
        distance = {path: 0 for path in changed if path in self.texts or path in self.dependents}
        queue = deque(distance)
        while queue:
            node = queue.popleft()
            if distance[node] >= MAX_DISTANCE:
                continue
            if distance[node] > 0 and len(self.dependents[node]) > HUB_FAN_IN:
                continue
            for user in self.dependents[node]:
                if user not in distance:
                    distance[user] = distance[node] + 1
                    queue.append(user)
        return {f"res://{path}": d for path, d in distance.items() if path in self.suites}


def select(
    repo: Path,
    changed: list[str],
    heavy: set[str] | None = None,
    graph: ReferenceGraph | None = None,
    base_rev: str | None = None,
    always: tuple[str, ...] | list[str] = (),
) -> Selection:
    """Suites to run for these changed project-relative paths. `heavy` suites (res://)
    are kept only at distance 0 or 1. `base_rev` lets a deleted script's old `class_name`
    be found. `always` suites (res://) join any change that touches source."""
    config = graph.config if graph else kit_config.load(repo)
    changed = sorted({_owner(path) for path in changed})
    selection = Selection(changed=changed)
    for path in selection.changed:
        untraceable = path.lower().endswith(SOURCE_SUFFIXES) and not is_source(path, config)
        hidden = any(part.startswith(".") for part in Path(path).parts)
        if path in WIDENING_FILES or (untraceable and not hidden):
            selection.widened_by = path
            return selection
    if graph is None:
        watched = {path for path in changed if not is_source(path, config)}
        graph = ReferenceGraph(repo, _removed(config, changed, base_rev), watched)
    for path in selection.changed:
        if path not in graph.texts and path not in graph.dependents:
            selection.ignored.append(path)
    reached = graph.affected_suites(changed)
    skip_heavy = heavy or set()
    selection.suites = {
        suite: distance
        for suite, distance in reached.items()
        if distance <= 1 or suite not in skip_heavy
    }
    if any(is_source(path, config) for path in changed):
        for suite in always:
            if suite.removeprefix("res://") in graph.suites:
                selection.suites[suite] = 0
    selection.suites = dict(sorted(selection.suites.items()))
    return selection


def _owner(path: str) -> str:
    """A change to Godot's `x.gd.uid` or `x.png.import` is a change to `x.gd` / `x.png`:
    the file's id or import settings are part of it."""
    for sidecar in SIDECAR_SUFFIXES:
        if path.endswith(sidecar):
            return path.removesuffix(sidecar)
    return path


def _removed(
    config: kit_config.KitConfig, changed: list[str], base_rev: str | None
) -> dict[str, str | None]:
    """Source files in `changed` that no longer exist, with their old class_name."""
    removed: dict[str, str | None] = {}
    for path in changed:
        if not is_source(path, config) or (config.root / path).exists():
            continue
        old = _git(config.root, "show", f"{base_rev}:./{path}") if base_rev else None
        found = (
            project_refs.CLASS_NAME.search("\n".join(old or [])) if path.endswith(".gd") else None
        )
        removed[path] = found.group(1) if found else None
    return removed


def _git(repo: Path, *args: str) -> list[str] | None:
    """The command's output lines, or None when git is missing or the command fails."""
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return out.stdout.splitlines() if out.returncode == 0 else None


def default_base(repo: Path) -> str | None:
    """The branch a change is measured against: the remote's default branch, else the
    first of main / master that exists, the remote's copy (origin/main) before the local
    one. None outside a git repository, or with neither."""
    head = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if head and head[0].strip():
        return head[0].strip()
    for branch in BASE_BRANCHES:
        # A repository made with `git init` and `git remote add` has no origin/HEAD.
        # origin/main first: after a commit on the local main, main is HEAD itself and
        # nothing committed would count as changed.
        for ref in (f"origin/{branch}", branch):
            if _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
                return ref
    return None


def on_base_branch(repo: Path, base: str) -> bool:
    """True when the checked-out branch is `base` itself (a local branch): the diff
    against it holds no commit, so work committed there cannot count as changed."""
    current = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    return bool(current and current[0].strip() == base)


def base_revision(repo: Path, base: str) -> str | None:
    """The commit this branch forked from `base`, or None when there is none."""
    for ref in (base, f"origin/{base}"):
        found = [line.strip() for line in _git(repo, "merge-base", "HEAD", ref) or []]
        if found and found[0]:
            return found[0]
    return None


def changed_files(repo: Path, base_rev: str) -> list[str] | None:
    """Project-relative paths changed since the commit `base_rev`: committed, staged,
    unstaged and untracked, including deleted files and both sides of a rename. Only
    files under `repo` count, so a project in a subfolder of its repository works.
    None when git cannot answer."""
    tracked = _git(repo, "diff", "--name-only", "--no-renames", "--relative", base_rev)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return sorted({line.strip() for line in tracked + untracked if line.strip()})


@dataclass
class Changes:
    """What differs from the base branch. `files` is None when git cannot say, and
    `why` says why not. `note` is what the caller must tell the user about `files`."""

    files: list[str] | None = None
    base: str = ""
    revision: str = ""
    why: str = ""
    note: str = ""


def working_tree_changes(repo: Path, base: str | None = None) -> Changes:
    """Every file that differs from `base` (default: `default_base`), committed or not.
    The one answer to "what changed" for tools/test_shards.py and tools/check.ps1."""
    base = base or default_base(repo)
    if base is None:
        return Changes(why="not a git repository, or no main/master branch to compare with")
    revision = base_revision(repo, base)
    files = changed_files(repo, revision) if revision else None
    if files is None:
        return Changes(base=base, why=f"no common commit with '{base}', cannot tell what changed")
    note = ""
    if on_base_branch(repo, base):
        note = (
            f"on '{base}', the base branch itself: commits made on it do not count as "
            "changed, only uncommitted and untracked files"
        )
    return Changes(files=files, base=base, revision=revision or "", note=note)


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    """`--changed-files [BASE]`: print the files that differ from the base branch as one
    JSON object, {"base": "main", "files": [...], "note": ""} or {"files": null,
    "why": "..."}. A "note" is for the user: what the list leaves out.
    Exit codes: 0 printed; 2 usage, or a kit.json that cannot be used."""
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[0] != "--changed-files" or len(arguments) > 2:
        usage = "usage: uv run python tools/ci/select_suites.py --changed-files [BASE]"
        print(usage, file=sys.stderr)
        return 2
    try:
        repo = kit_config.load(root).root
    except kit_config.ConfigError as error:
        print(f"select_suites: {error}", file=sys.stderr)
        return 2
    changes = working_tree_changes(repo, arguments[1] if len(arguments) == 2 else None)
    # ASCII only (other characters as \\uXXXX): the reader need not guess a code page.
    answer = {"base": changes.base, "files": changes.files, "why": changes.why}
    print(json.dumps(answer | {"note": changes.note}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
