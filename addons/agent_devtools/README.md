# Agent DevTools

Lets a coding agent drive a running Godot debug build: look, press, assert, measure.
Godot 4.7, GDScript. MIT: `LICENSE` in this folder.

The client is `tools/devtools.py`, or the MCP server in `tools/mcp/`. Every command, sequence
step, JSON form, launch flag and mailbox file: `docs/devtools.md`.

## Install

1. Copy `addons/agent_devtools/` into the project.
2. Project Settings > Plugins > enable **Agent DevTools**. This adds the `DevTools` autoload
   and the setting `devtools/allow_mutation=false`.

Active in debug builds only. Never in a headless `--script` run.

## Use

```powershell
$env:GODOT_AGENT_SESSION = "agent-1"   # one name per running game; same for game and client
pwsh ./tools/godot.ps1                  # start the game: in another terminal, it blocks
uv run python tools/devtools.py wait    # poll until it answers
uv run python tools/devtools.py input tap <action>
uv run python tools/devtools.py screenshot --filename after.png
uv run python tools/devtools.py quit
```

| Command | Does |
|---|---|
| `wait`, `ping` | Is the game answering, which one, how many errors it has logged |
| `screenshot` | The viewport as a PNG in the game's user data folder. Prints the path |
| `scene-tree`, `get-state` | Nodes; one node's properties as JSON |
| `input list / press / release / tap / clear` | Simulated input actions |
| `input sequence <file>` | Scripted steps with asserts; exit 1 when one fails |
| `performance` | FPS, frame times, memory, draw calls, node counts |
| `validate`, `validate-all` | Load and instantiate scenes; `--strict` fails on warnings too |
| `call <name> [key=value ...]` | A command the game registered with `DevTools.register_handler` |
| `logs`, `quit` | The DevTools log; close the game |
| `set-state`, `run-method` | Refused unless `devtools/allow_mutation=true` |

Exit codes: 0 ok, 1 the command failed, 2 bad usage, 3 no answer.

## Safety

`devtools/allow_mutation=false` is a guard rail against accidents, not a security boundary.
Anything that can write files in the game's user data folder can send commands, and a
command your game registers does whatever you wrote it to do. With
`devtools/allow_mutation=true`, `run-method` calls any method of any node: whoever can write
that folder runs code in the game. It refuses `DevTools` as the target, which is not a
boundary either: a method of another node calls `DevTools` all the same. DevTools is off in
release builds; do not ship a debug build with it to people you do not trust.
