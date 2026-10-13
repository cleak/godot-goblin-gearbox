# AGENTS.md

Godot 4.7 Standard (non-Mono), typed GDScript, Windows, PowerShell 7, Python 3.12 via `uv`.
Project is the owner's and overrides Kit.
A `(sample)` section describes the kit's sample game: while one remains the game is undecided.
Ask the owner before inventing its design. No answer: the smallest reading, each choice reported.
A `(default)` section is a starting rule the owner has not confirmed.

# Project

## Mission (sample)

A sample game: light three lamps on a 7x5 grid. It exists to exercise the tools: keep it small.

## Fixed decisions (sample)

- 2D, Forward+, single-player, keyboard: `move_left/right/up/down`, `interact`, `pause`.
- Ask first: C#, GDExtension, a new addon, a new autoload.

## Layout (sample)

| Path | Holds | May name |
|---|---|---|
| `scripts/core/` | Rules: pure `RefCounted`, no nodes | nothing |
| `scripts/game/`, `main.tscn` | Nodes: input, drawing, DevTools commands | core |
| `test/unit/`, `test/sequences/` | gdUnit4 suites; DevTools sequences (JSON) | any |
| `tools/`, `addons/`, `kit.json` | The kit and its config. `addons/gdUnit4/`: never edit | |

## Architecture (default)

- Rules are pure `RefCounted`: no nodes, signals or autoloads. Nodes read state, call rules.
- Randomness and time arrive as arguments: a seeded `RandomNumberGenerator`, a tick count.
- State worth asserting is a plain property of a named node: tests and sequences read it.
- Ids are explicit, never built from names or paths. Saved enums: append only.
- `assert` invariants, `push_error` bad setup. No silent fallback.

## Quality (default)

- Whatever the player sees move gets the twelve principles of animation and juice:
  `docs/feel.md`. Feel never changes game state, and the player can turn it down.
- Nothing ships as a placeholder; dull or generic is a defect. Asked to judge the game:
  `docs/critique.md`.
- Models, textures, sound, a look that is off: `docs/assets.md`. Each asset's source goes in
  `docs/provenance.md`.

## Git (default)

- Commit and push only when asked. Never force-push.
- Keep changes that are not yours. No `git stash`: worktrees share it.

## Done (default)

1. A test added or tightened. A bug fix began with a failing test.
2. `pwsh ./tools/check.ps1` green: no `-Changed`, no `-SkipTests`.
3. Player-visible: runtime loop run, screenshot opened and described, `docs/feel.md` checklist.
4. Docs the change made false are fixed, this file included.
5. Report: files, each command with its outcome and test counts, what did not run and why.

# Kit

Ships with the kit; the owner updates it from there. A project rule goes in Project.

## Every change

1. New clone or worktree: `pwsh ./tools/setup.ps1`, then `pwsh ./tools/check.ps1`. Red before
   you changed anything: report it, fix it only if asked.
2. Read nearby code and tests. Changing an enum, signal, saved field or action: find every user.
3. Test first. Bug: write the failing test and watch it fail.
4. Iterate on the narrowest check. Player-visible: the runtime loop. Then Done.

## Commands

```powershell
pwsh ./tools/check.ps1 -Changed  # iterating: the gates; tests only for what the diff can affect
pwsh ./tools/check.ps1           # handoff: every gate, every suite. -Fix formats first
pwsh ./tools/lint_gdscript.ps1 -Path <file or folder>   # style; -Fix formats
pwsh ./tools/typecheck.ps1 -Path <file or folder>       # the strict type check
uv run python tools/test_shards.py <tests>/unit/test_<name>.gd   # <tests>: tests.root in kit.json
pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd --user-args --fix
pwsh ./tools/godot.ps1 --headless --import   # after a new script or shader: writes its .uid
pwsh ./tools/doctor.ps1          # a tool is missing or odd: prints the fix
```

- `skipped` in the table is not a pass: report it. Full output: `artifacts/check/<gate>.log`.
- `pytest` tests the kit, not the game: minutes. The owner's call: `new_game.ps1 -RemoveKitTests`.
- Arguments of a `tools/*.gd` script go after `--user-args`, never a bare `--`.
- Moved or renamed a file: move its `.uid` and `.import` too. `lint_project.gd --fix` (above)
  repairs scene uids and moved paths: never type a `uid://`. Commit every `*.uid`.
- Exit 0 from `--import` proves nothing: scripts that do not compile pass it. Nor does a green
  `check.ps1` prove the game looks or feels right (runtime loop) or a shader runs on a GPU.

## Runtime loop

```powershell
$env:GODOT_AGENT_SESSION = "<unique>"   # in every shell of the loop, or --session <unique>
$game = Start-Process pwsh "-NoProfile -File tools/godot.ps1" -PassThru   # note $game.Id
uv run python tools/devtools.py wait             # until the game answers
uv run python tools/devtools.py input sequence <tests>/sequences/<name>.json   # steps, asserts
uv run python tools/devtools.py screenshot --filename <feature>_<state>.png
uv run python tools/devtools.py input clear      # always, also after a failure
uv run python tools/devtools.py quit             # always
```

- Open the PNG and say what you see: a written file is no proof. Every capture prints
  `Screenshot saved: <path>`, a sequence's screenshot steps included.
- A player-visible feature ships `<tests>/sequences/<name>.json`: assert state, screenshot looks.
- `wait` warns when the game logged errors: `devtools.py logs --category error`.
- Scripts load at launch: quit and relaunch after an edit.
- No answer: same session as the game? `quit` unanswered: `taskkill /T /F /PID <that id>`.

## GDScript

- `[debug]` warnings are errors: type every variable, parameter and return. A real exception:
  `@warning_ignore("<name>")` on the line above that one statement, with the reason.
- A method call on a `Variant` fails the gate. A property read (`d["pos"].x`) passes unchecked
  and fails at run time. Copy into a typed local first.
- Only `map()` returns an untyped array (`filter()` keeps the type): `out.assign(xs.map(f))`.
- A failed `as` is a silent `null`: check with `is`, then assign to a typed local.
- `add_child()` before `global_position`: the other order is silent on a `Node2D`.
- An editor save or `ProjectSettings.save()` drops default-valued keys: `git diff project.godot`.

## Tests

- Every test asserts; loops are bounded; no clock waits. A rerun never turns red into green.
- After `add_child()`, `await node.ready` hangs (it already fired): await a frame.
- A local named `before`, `after`, `before_test` or `after_test` shadows a hook: a type error.
- Run tests only through `test_shards.py` or `tools/test.ps1`: gdUnit4 itself exits 0 when it
  finds no test case. Read the executed counts, not only the failures.
- A failed string compare prints both strings interleaved: its `but was` text is not the value.
- `Input.action_press()` sets state only, `_input()` never runs: use `Input.parse_input_event()`.

## Shared machine

- Other agents and the owner's editor share this PC. One Godot at a time per agent: quit your
  game before you run tests or `check.ps1`.
- Stop only a process you started, by its id. Never by name (`Stop-Process -Name godot*`).
- Tests wait for a machine-wide slot pool (`GODOT_TEST_SLOTS`): never raise it, no `--slots 0`.
- One worktree per agent, `setup.ps1` run in it; one `GODOT_AGENT_SESSION` per game. A session
  has its own mailbox and its own screenshot folder: with none set, both are shared.

## Safety

- Never weaken a gate to get green: no edits to `[debug]`, `.gdlintrc`, `kit.json`, CI, timeouts
  or allowlists; no deleted or skipped test; no `git commit --no-verify`. Fix the code or ask.
- A gate that was skipped or did not run is not a pass. Never report a check you did not run.
- `devtools/allow_mutation` stays `false`: never set it yourself. `set-state` and `run-method`
  need it; use input and `devtools.py call`.
- Never run downloaded scripts. Never commit secrets, `.env*`, `artifacts/`, `.godot/`, `.tools/`.

## Reference (open only what the task needs)

| When | Open |
|---|---|
| One gate alone, flags, exit codes; `kit.json`; a new input action | `docs/tools.md` |
| Driving the game: commands, sequence steps, your own command, no answer | `docs/devtools.md` |
| A message you do not recognise; scenes and uids; shells | `docs/gotchas.md` |
| A new folder or layer; what `check_architecture.py` enforces | `docs/architecture.md` |
| Changing this file (owner) | `docs/customizing-agents-md.md` |
