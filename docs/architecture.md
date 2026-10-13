# Architecture

The layering the sample game shows, the check that keeps it true, and how to change or drop it.

It is a default, not a requirement: no other tool in the kit depends on it.

## The default

| Module | Folder | Holds | May name |
|---|---|---|---|
| `core` | `scripts/core/` | Rules and data: `RefCounted` and `Resource` scripts. No node, no signal, no autoload | Nothing outside itself |
| `game` | `scripts/game/`, scripts beside `project.godot` | Nodes: input, drawing, wiring, DevTools commands | `core` |
| `tests` | `test/` | gdUnit4 suites | Anything |

In the sample game (these files go when it goes):

- `scripts/core/board.gd` is the whole game as plain logic: a grid, a player cell, lamps.
  `move()` and `toggle_lamp()` change it and say whether they did.
- `scripts/game/game.gd` is a `Node2D`. It polls input, calls the board, draws from what the
  board holds. It decides nothing. It shows the state worth asserting as plain properties:
  `player_cell`, `lit_count`, `paused`.
- `test/unit/test_board.gd` tests every rule without a scene or a frame, in milliseconds.
  `test/unit/test_game.gd` drives the scene through input actions, as a player does.
- `test/sequences/smoke.json` asserts those properties in the running game.

## Why each line

The `Architecture (default)` lines of `AGENTS.md`, each with what it buys.

| Default | Buys | Drop it when |
|---|---|---|
| Rules are pure `RefCounted` | Rules test in milliseconds, with no window and no scene tree | The game is one scene of physics toys |
| Nodes read state and call rules | One writer per value; drawing never decides an outcome | Rarely |
| Randomness and time are arguments | A failure replays from its seed | Nothing is random and nothing replays |
| State is a plain property of a named node | A test, a sequence `assert` and `get-state` all read it | Never, while agents drive the game |
| Explicit ids; saved enums append only | Saves and data survive a rename | There are no saves and no content data |
| `assert`, `push_error`, no silent fallback | A bug shows where it starts | Never |

A rule nobody enforces teaches agents to skip the file. Delete the line, or back it with a
test or a check.

## The check

```sh
uv run python tools/check_architecture.py
```

It reads `tools/ci/architecture_modules.json` and every authored `.gd` file, the test folder
included. `pwsh ./tools/check.ps1` runs it as the `architecture` gate. Its lines and exit
codes are the same on Windows and Linux.

No manifest, no check: the script prints `architecture: not configured` and `check.ps1`
lists the gate as `skipped`. That is where a project starts after `install.ps1`, which does
not copy the sample game's manifest. Write your own (below) to turn the gate on.

| Rule | Fails when | Message |
|---|---|---|
| Coverage | A script is in no module and under no `ignore` glob. A glob matches nothing | `no module: <script> (add it to a module or to ignore in tools/ci/architecture_modules.json)`, `glob matches no script: module <name>: <glob> (fix it in ...)` |
| Layering | A script names a script of a module that is not in its `may_depend` | `not allowed: scripts/core/<name>.gd -> scripts/game/game.gd` |
| Purity | A script of a `pure` module declares a signal, names an autoload, or extends a `Node` | `scripts/core/<name>.gd: signal in pure module core` |
| Cycles | Scripts `preload` or `extend` each other in a ring | `preload cycle: scripts/game/<a>.gd <-> scripts/game/<b>.gd` |

One script names another by `class_name`, by an autoload's name, by `extends`, or by a string
that holds its `res://` path or `uid://`.

Godot loads a ring of scripts without complaint. The cycle rule is a design rule: nothing in
a ring can be tested or reused without the rest.

Not seen: a reference through a scene, a path built at run time, a `Node` class that comes
from a GDExtension.

Exit codes: 0 the rules hold or the check is not configured, 1 a violation, 2 a wrong manifest
or `kit.json`.

## The manifest

`tools/ci/architecture_modules.json`:

```json
{
  "modules": {
    "core": {"globs": ["scripts/core/**"], "may_depend": [], "pure": true},
    "game": {"globs": ["scripts/game/**", {"glob": "*.gd", "optional": true}], "may_depend": ["core"]},
    "tests": {"globs": ["test/**"], "may_depend": ["*"]}
  },
  "ignore": ["addons/**", "tools/**"],
  "cycles": true
}
```

| Key | Meaning |
|---|---|
| `modules.<name>.globs` | Project-relative paths. `*` stays in one folder, `**` crosses folders. A glob that matches nothing fails, unless written `{"glob": "...", "optional": true}` |
| `modules.<name>.may_depend` | The modules this one may name. `"*"` is any. A module may always name itself |
| `modules.<name>.pure` | `true`: no signal, no autoload name, no class that extends a `Node` |
| `ignore` | Globs the check leaves alone |
| `cycles` | `false` turns the cycle rule off |

A script belongs to the first module whose globs match it, in file order: put a narrow glob
before a wide one. A key that starts with `_` is a comment.

## Change it

| Want | Do |
|---|---|
| A new folder of scripts | Add its glob to a module, in the same change. Then update the Layout table of `AGENTS.md` |
| A new layer | Add a module, then name it in the `may_depend` of each module that may use it: `"ui": {"globs": ["scripts/ui/**"], "may_depend": ["core", "game"]}` |
| The check on a project that already breaks it | `uv run python tools/check_architecture.py --baseline` writes today's violations to `tools/ci/architecture_allowlist.txt`. New ones fail |
| An exception gone from the code | Its allowlist line is now stale and fails: `--shrink` removes it. The list only shrinks |
| No cycle rule | `"cycles": false` |
| No check at all | Delete `tools/ci/architecture_modules.json`. `check.ps1` then lists the gate as `skipped`. Delete the `Architecture` and `Layout` lines of `AGENTS.md` that no longer hold |
| A new Godot version | `uv run python tools/check_architecture.py --update-node-classes` rewrites `tools/ci/node_classes.txt`, the list of engine `Node` classes the purity rule uses |

A reference the check rejects: move the code or turn the dependency around. An agent does not
add an allowlist line to get green.
