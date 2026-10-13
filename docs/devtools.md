# DevTools

How an agent drives the running game: start it, press actions, assert state, look, quit.

`DevTools` is an autoload from `addons/agent_devtools/`. The client is `tools/devtools.py`.
They talk through files in the game's `user://` folder: no socket, no server. Debug builds
only; never in a headless `--script` run.

## The loop

```powershell
$env:GODOT_AGENT_SESSION = "fix-lamp-1"       # your own name: letters, digits, _ - .
$game = Start-Process pwsh "-NoProfile -File tools/godot.ps1" -PassThru
$game.Id                                      # write it down: the one process you may stop
uv run python tools/devtools.py wait          # polls until the game answers (60 s)
uv run python tools/devtools.py input sequence <tests>/sequences/<name>.json
uv run python tools/devtools.py screenshot --filename lamp_lit.png
uv run python tools/devtools.py performance
uv run python tools/devtools.py input clear   # always, also after a failure
uv run python tools/devtools.py quit          # always
```

- `pwsh ./tools/godot.ps1` blocks until the game exits. `Start-Process` returns at once; an
  agent's own background mode works too.
- The game reads `GODOT_AGENT_SESSION` when it starts, so set it before the launch. A client
  takes it from the variable or from `--session <name>`. A new shell has neither: set one.
- One session, one game. Two games on one session answer each other's commands.
- Open every screenshot. Each capture prints `Screenshot saved: <path>`: the `screenshot`
  command, and every `screenshot` step of a sequence. Check: the right scene and state,
  nothing clipped or off screen, text readable, the change you made visible.
- A game with a session writes to `<user data>/screenshots/<session>/`: two sessions never
  overwrite each other's `smoke.png`. Without a session it is `<user data>/screenshots/`.
- A look worth keeping: copy the PNG to a baseline and compare later captures with
  `uv run python tools/visual_diff/compare.py <baseline> <capture> --output-dir <dir>`
  (exit 1 when they differ).
- `<tests>` is `tests.root` in `kit.json` (`test`). A sequence file may live anywhere; the
  kit keeps them in `<tests>/sequences/`.
- Scripts load at launch. After an edit: `quit`, start again.
- `quit` got no answer: `taskkill /T /F /PID <the id you wrote down>`. It takes the game with
  it. Never stop Godot by name.

One capture without the client (starts, captures, quits; exit 0 or 1):

```powershell
pwsh ./tools/godot.ps1 --user-args --devtools-screenshot --devtools-screenshot-name=boot.png
```

## Commands

`uv run python tools/devtools.py <command>`. `--help` on any of them.

| Command | Does |
|---|---|
| `wait [--timeout <s>]` | Polls until the game answers (60 s). Use after every launch |
| `ping` | One try: which game answers, its pid, session, Godot version, mutation on or off, user data folder, screenshot folder |
| `screenshot [--filename <name.png>]` | The viewport to the session's screenshot folder. Prints the path and the size. A bare file name only |
| `scene-tree [--depth <n>]` | The current scene's nodes as JSON (10 levels) |
| `get-state [--node <path>]` | A node's properties as JSON. No path: the scene root |
| `performance` | FPS, frame times, draw calls, memory, object, node and orphan counts |
| `validate --scene res://<scene>.tscn [--strict]` | Loads and instantiates one scene. Warns when the game has logged errors |
| `validate-all [--strict]` | The same for every authored scene |
| `logs [--tail <n>] [--category <name>]` | The session's DevTools log. Works without the game. Category `error`: what the engine logged. No row to show: one line on stderr says so, exit 0 |
| `input list [--all]` | The project's actions and their keys; which are held. `--all` adds `ui_*` |
| `input press <action> [--strength <0-1>]` | Holds an action until released |
| `input release <action>` | |
| `input tap <action> [--hold <s>] [--strength <0-1>]` | Press and release. At least one process frame and one physics tick |
| `input clear` | Releases everything simulated |
| `input sequence <file> [--timeout <s>]` | Runs a sequence file and waits for the verdict (60 s of real time). Prints the path of each screenshot it took |
| `call <name> [key=value ...] [--timeout <s>]` | Any command, such as one the game registered. Values are JSON when they parse |
| `quit [--exit-code <n>]` | Closes the game |
| `set-state --node <path> --property <name> --value <json>` | Refused unless `devtools/allow_mutation=true`. Prints the value read back; fails when the property kept its old one (read-only, or its setter refused) |
| `run-method --node <path> --method <name> [--args <json array>]` | Refused unless `devtools/allow_mutation=true`. Then it calls any method of any node; `DevTools` as the target is refused, which stops a slip and nothing else (Safety) |

Options for every command, before or after it: `--session <name>`, `--json` (the reply as
one JSON object). Before the command only: `--project <folder>`, the game of another project.
`.claude/settings.json` pre-approves `devtools.py <command> ...`, which cannot hold it.

Exit codes: 0 ok. 1 the game answered and the command failed (an assert, a missing node, a
refusal, a scene that does not validate). 2 bad usage. 3 no answer in time. Results go to
stdout, diagnostics to stderr.

Node paths are relative to the current scene (`.` is its root, `Hud/Status` a child) or
absolute (`/root/Main`). Prefer relative: Git Bash rewrites an argument that starts with `/`.
A property may be indexed: `position:x`.

Several clients can use one session at once (two shells, a shell and the MCP server): every
command and every reply is a file of its own.

## Sequences

A JSON file of steps. Prefer one to a string of single commands: it repeats, it asserts, and a
failed step releases every held action. For the sample game:

```json
{
  "description": "Reset, walk to a lamp, light it",
  "steps": [
    {"type": "call", "action": "reset"},
    {"type": "tap", "action": "move_right"},
    {"type": "tap", "action": "move_right"},
    {"type": "hold", "action": "move_up", "seconds": 1.0,
     "until": {"node": ".", "property": "player_cell", "equals": {"x": 5, "y": 1}}},
    {"type": "tap", "action": "interact"},
    {"type": "assert", "node": ".", "property": "lit_count", "equals": 1},
    {"type": "wait", "seconds": 2.0,
     "until": {"node": "Hud/Status", "property": "text", "equals": "Lamps 1/3   Moves 3"}},
    {"type": "screenshot", "filename": "lamp_lit.png"}
  ]
}
```

| `type` | Fields | Does |
|---|---|---|
| `press` | `action`, `strength`? | Holds the action |
| `release` | `action` | |
| `tap` | `action`, `seconds`?, `strength`? | Press, wait, release. At least one process frame and one physics tick |
| `hold` | `action`, `seconds`, `strength`?, `until`? | Holds for `seconds`, or until the condition is true |
| `wait` | `seconds`, `until`? | Waits. With `until`: fails when it never came true |
| `assert` | `node`, `property`, one of `equals`, `ge`, `le` | Fails the sequence when false |
| `screenshot` | `filename`? | |
| `clear` | | Releases everything simulated |
| `call` | `action`, `args`? | Runs a DevTools command; fails the sequence when it fails |

- `until` is `{"node", "property", "equals" | "ge" | "le"}`. `equals` takes any JSON value,
  read as the property's type. `ge` and `le` take numbers.
- `seconds` is game time. `--timeout` is real time.
- Start with a `call` to a command of your game that resets it. The sequence then passes
  however the game was left. The sample: `test/sequences/smoke.json`.
- Assert state, not pixels. A screenshot is for how it looks.
- A `call` step cannot start another sequence.
- A failure is one line and exit 1. Steps are counted from 0:
  `Failed: Sequence a5164a2d failed at step 1 of 3 (counted from 0): Assertion failed:
  .:lit_count is 0, expected 5`.
- There is no repeat: thirty moves are thirty `tap` lines.

## JSON values

`equals`, `--value`, `--args` and `key=value` take JSON. A value becomes the type of the
property or parameter it is headed for.

| Godot | JSON |
|---|---|
| `Vector2`, `Vector2i` | `{"x": 1, "y": 2}` or `[1, 2]` |
| `Vector3`, `Vector3i` | `{"x": 1, "y": 2, "z": 3}` or `[1, 2, 3]` |
| `Color` | `{"r": 1, "g": 0, "b": 0, "a": 1}`, `[1, 0, 0, 1]`, `"red"`, `"#ff0000"` |
| `Rect2`, `Transform2D`, `Basis` and other math types | `{"type": "Rect2", "args": [0, 0, 4, 4]}` |
| `StringName`, `NodePath` | A string |
| `Array[int]`, `Array[Vector2i]` | A list; each item is read as the element type |
| `Dictionary[String, bool]` | An object; keys may be `String`, `StringName`, `int`, `float` |
| Objects, callables | Shown as text; cannot be sent |

An untyped target gets floats: JSON has one number type. A collection of objects can only be
sent empty, and an `Array[YourClass]` parameter not at all.

## Make the game drivable

- Keep the state worth asserting as plain properties on named nodes. The sample's scene root
  has `player_cell`, `lit_count` and `paused`.
- Register a `reset` command, so a sequence starts from a known state.
- Input arrives as `InputEventAction`: code that polls (`Input.is_action_just_pressed`) and
  code that handles events (`_unhandled_input`) both see it. Code that tests
  `event is InputEventKey` does not: test the action.
- Every screen needs an action that moves on from it, or a menu stops the sequence.

## Your own commands

```gdscript
func _ready() -> void:
	DevTools.register_handler("spawn", _on_devtools_spawn)


func _exit_tree() -> void:
	DevTools.unregister_handler("spawn", _on_devtools_spawn)


func _on_devtools_spawn(args: Dictionary) -> Dictionary:
	var kind: String = str(args.get("kind", "slime"))
	return {"success": true, "message": "Spawned %s" % kind, "data": {"at": position}}
```

`uv run python tools/devtools.py call spawn kind=slime count=2`

- The handler takes the arguments and returns `success`, `message` and `data`. It may `await`.
  Reply data is converted to JSON (a `Vector2` becomes `{"x", "y"}`).
- `register_handler` returns `false` in a release build and for a built-in name, so the call
  can stay in.
- A script may name the `DevTools` autoload; `tools/typecheck.ps1` checks it like any other.
  The sample game's `scripts/game/dev_commands.gd` looks it up
  (`get_node_or_null("/root/DevTools")`), so the game also runs with the plugin off.
- Also there: `DevTools.log_event(category, message, data)`, `DevTools.is_enabled()`,
  `await DevTools.execute(action, args)`.

## Errors

A windowed game prints nowhere an agent reads. DevTools records what the engine logs
(script errors, `push_error`, scripts that do not compile):

- `wait`, `ping`, `validate` and `validate-all` warn on stderr with the count since the game
  started.
- `logs --category error` lists them. One error is logged three times at most, counted always.
- A reply lists the ones logged while its command ran, and the client prints them to stderr.
  That is a time window: one may belong to the game, not to the command.
- `run-method` fails when the method raised a script error.

## Scene validation

| Severity | Fails | Codes |
|---|---|---|
| error | Always | `file_not_found`, `load_failed`, `instantiate_failed`, `missing_script`, `script_not_compiled`, `script_not_attached`, `invalid_connection`, `engine_error` |
| warning | With `--strict` | `missing_texture`, `missing_mesh`, `missing_shader`, `missing_collision_shape`, `missing_resource`, `invalid_animation_path` |
| info | Never | `relative_nodepath`, `missing_audio` |

A warning is something a game may fill in from code, such as a sprite's texture. Validating a
scene runs the `_init` of its scripts, so `validate` takes `res://` scenes only.

A scene is valid when its own scripts attach. A script it only uses by `class_name` may not
compile and the scene still passes: that shows in the error count and in
`logs --category error`.

## Launch flags

After `--user-args` with `tools/godot.ps1`.

| Flag | Does |
|---|---|
| `--devtools-screenshot` | Captures after a delay, prints the path, quits. Exit 0 or 1. The file goes to the session's folder, as every capture does |
| `--devtools-screenshot-delay=<seconds>` | That delay (0.5) |
| `--devtools-screenshot-name=<name.png>` | That file's name |
| `--devtools-validate` | Validates every scene, prints each issue, quits. Exit 0 or 1 |
| `--devtools-validate-strict` | The same; warnings fail too |

`--devtools-validate` works headless, and needs the project folder: in an exported build it
finds no scene. A screenshot does not work headless: that run renders nothing and the flag
exits 1 saying so.

## MCP

Optional. The CLI is enough for any agent that runs shell commands.

`tools/mcp/server.py` offers the same commands as MCP tools over stdio, standard library
only. Copy `tools/mcp/.mcp.json.example` to `.mcp.json` at the project root and set
`GODOT_AGENT_SESSION` in its `env` to the session of the game you start. Tools: `wait`,
`ping`, `screenshot` (returns the image), `scene_tree`, `get_state`, `set_state`, `run_method`,
`performance`, `validate_scene`, `validate_all_scenes`, `input_actions`, `input_press`,
`input_release`, `input_tap`, `input_clear`, `input_sequence` (steps inline), `call`, `logs`,
`quit`. A failed command and a silent game come back as a tool result with `isError`.

## Files

`<user data>` is `%APPDATA%\Godot\app_userdata\<config/name>\` on Windows; `ping` prints it.

| Path | Holds |
|---|---|
| `<user data>/devtools/`, or `<user data>/devtools_<session>/` with a session | The mailbox |
| `.../commands/<id>.json` | A command, `{"action", "args"}`, written by the client. The game removes it once read |
| `.../results/<id>.json` | The reply, `{"action", "id", "success", "message", "data", "errors"?}`. The client removes it once read |
| `.../log.jsonl` | The DevTools log, one JSON row per line. Past 5 MB it is set aside as `log.1.jsonl` at the next start |
| `<user data>/screenshots/`, or `<user data>/screenshots/<session>/` with a session | The captures |
| `<user data>/logs/godot.log` | Godot's own log |

A client of your own: `<id>` is any name of letters, digits, `_` and `-`, at most 64, that no
other client uses. Write the command under another name and rename it into place. The game
removes leftovers when it starts, and replies nobody collected after a minute.

## Safety

- Off in release builds. Do not ship a debug build that carries the addon to people you do
  not trust.
- The mailbox has no authentication. Anything that can write to the user data folder can drive
  a running debug game.
- `set-state` and `run-method` are refused unless `devtools/allow_mutation=true` in
  `project.godot`. That is a guard against accidents, not a security boundary: a command your
  game registers does whatever you wrote. The committed value stays `false`;
  `pwsh ./tools/doctor.ps1` warns when it is `true`. An agent never sets it.
- With it `true`, whoever can write the user data folder can run code in the game:
  `run-method` calls any method of any node. It refuses `DevTools` as the target, and that is
  not a boundary: a method of another node (`propagate_call` on the root) calls `DevTools`
  all the same, past the two checks of the next line.
- `validate` refuses a path outside `res://`. `screenshot` refuses a name with a folder in it.
- Exporting: put `addons/gdUnit4/*, test/*, tools/*, kit.json` in the preset's exclude filter
  (your test folder for `test`), or the build ships them. `addons/agent_devtools` stays:
  `project.godot` names its autoload, which does nothing in a release build.

## No answer

| Symptom | Look at |
|---|---|
| `wait` or `ping` exits 3 | Is the game running? Same session on both sides? A debug build? Not a headless `--script` run? |
| `input tap` changes nothing | Code that reads keys, not actions. A wrong action name fails with `Unknown action: <name>. List them: devtools.py input list` |
| Old behaviour after an edit | The game was not restarted |
| `Nothing to capture` | A headless run renders no frames. Start the game without `--headless` |
| `--node /root/Main` is not found from Git Bash | The shell rewrote the path. Use a relative path, or PowerShell |
| The game cannot be reached at all | Run every other gate. Report the runtime check as not run, with the command and its output |
