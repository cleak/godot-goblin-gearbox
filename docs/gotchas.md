# Gotchas

Look up a message or a symptom; get the cause and the fix. Godot 4.7, GDScript, gdUnit4, Windows.

Every row was reproduced on Godot 4.7.2 with the kit's strict warnings. Proof:

| Mark | How |
|---|---|
| `4.7.2` | A script run on Godot 4.7.2 stable, headless |
| `kit` | The kit's own command, run on the sample game with the defect seeded |
| `shell` | PowerShell 7.6 and Git Bash on Windows 11 |

A new trap goes in as a row with its proof. One that bites every session also earns a line in
`AGENTS.md`.

## The type check fails

Messages of `pwsh ./tools/typecheck.ps1`. Each ends with `(Warning treated as error.)` unless
it is a plain compile error.

| Message contains | Cause | Fix | Proof |
|---|---|---|---|
| `inferred from a Variant value` | `var y := abs(x)`. `abs` `clamp` `min` `max` `lerp` `floor` `ceil` `round` `sign` `snapped` `wrap` return `Variant` | The typed twin: `absf` `clampf` `minf` `maxf` `lerpf` `floorf` `ceilf` `roundf` `signf` `snappedf` `wrapf` (`floori` for an `int`) | `4.7.2` |
| `requires the subtype "float" but the supertype "Variant" was provided` | The same call passed as an argument: `g(abs(x))` | The typed twin | `4.7.2` |
| `The method "size()" is not present on the inferred type "Variant"` | A method call on a `Dictionary` value or another untyped result: `d["items"].size()` | Copy into a typed local first: `var items: Array = d["items"]` | `4.7.2` |
| `The property "position" is not present on the inferred type "Node"` | The declared type is too wide for the member | Check with `is`, then `var body: Node2D = node` | `4.7.2` |
| `Cannot assign a value of type Array[Node2D] to variable ... Array[Node]` | Typed arrays do not convert to each other | `supers.assign(subs)` | `4.7.2` |
| `Nested typed collections are not supported` | `Array[Array[int]]` | `Array[Array]`, or a small class | `4.7.2` |
| `Integer division. Decimal part will be discarded` | `int / int` | `a / float(b)`. Truncation meant: `@warning_ignore("integer_division")` on the line above | `4.7.2` |
| `"@onready" will set the default value after "@export"` | Both on one variable | `@export var target_path: NodePath`, then `get_node_or_null(target_path)` in `_ready()` | `4.7.2` |
| `The local variable "name" is shadowing an already-declared property in the base class "Node"` | A local or parameter named like a member of the base class | Rename it | `4.7.2` |
| `The function parameter "seed" has the same name as a built-in function` | A parameter or local named like a global function | Rename it: `seed_value` | `4.7.2` |
| `"for" iterator variable "x" has no static type` | A loop over an untyped `Array` | `for x: int in xs:`. A loop over an `Array[int]` or over a number needs nothing | `4.7.2` |
| `Casting "Variant" to "Node" is unsafe` | `script.new() as Node` | `var made: Object = script.new()`, check with `is`, assign to a typed local | `4.7.2` |
| `The signal "changed" is declared but never explicitly used in the class` | It is emitted or connected only from another script | `@warning_ignore("unused_signal")` on the line above the declaration | `4.7.2` |
| The warning stays after `@warning_ignore` | The annotation covers the one statement below it | Put it directly above the statement that warns | `4.7.2` |
| `Identifier not found: <Autoload>` in a `--script` tool | The entry script compiles before autoloads exist. The node is missing in its `_init()` too | `root.get_node("<Autoload>")` from `_initialize()` on. Scripts loaded later may name the autoload | `4.7.2` |

## It compiles, then goes wrong

No gate reports these unless the row says so.

| Symptom | Cause | Fix | Proof |
|---|---|---|---|
| `Trying to assign an array of type "Array" to a variable of type "Array[int]"` | `map()` returns an untyped array. `filter()`, `slice()` and `duplicate()` keep the type | `out.assign(xs.map(f))` | `4.7.2` |
| The same error, only on one branch | `var out: Array[int] = xs if ok else []`: a `[]` or `{}` in a ternary is untyped | `var out: Array[int] = []`, then `if ok: out = xs`. The `typed-ternary` gate flags it | `4.7.2`, `kit` |
| The same error from `d.keys()` | `keys()` and `values()` of an untyped `Dictionary` are untyped. A `Dictionary[String, int]` gives typed ones | `names.assign(d.keys())`, or type the dictionary | `4.7.2` |
| `Invalid access to property or key 'x' on a base object of type 'int'` | A property read on a `Variant` (`d["pos"].x`) is not checked at all, though a method call is | Copy into a typed local first | `4.7.2` |
| A value is `null` where an object was expected | A failed `as` returns `null` and logs nothing | `if thing is Node:` then `var node: Node = thing`. A wrong type assigned to a typed local is a loud error | `4.7.2` |
| A function stops halfway; the game keeps running; the process exits 0 | A script error ends that one function. Its callers carry on with `null` or `0` | Read the errors: `uv run python tools/devtools.py logs --category error`. The kit's `.gd` tools exit 3 on one | `4.7.2` |
| A lambda sees an old value | A lambda captures a local by value when it is created | Pass the value in, or capture an object and read its field | `4.7.2` |
| A sorted list of `StringName` is not alphabetical | `StringName` does not sort by its text | Sort `String`s; convert after | `4.7.2` |
| An id read from JSON is `1.0` | Every JSON number is a `float` | `int(value)` where the data comes in | `4.7.2` |
| `-5 / 2` is `-2` | Integer division truncates toward zero | `floori(a / float(b))` for `-3` | `4.7.2` |

## Nodes and scenes

| Symptom | Cause | Fix | Proof |
|---|---|---|---|
| A node appears at the wrong place | `global_position` was set before `add_child()`. On a `Node2D` that is silent: the value becomes the local position. A `Node3D` logs `Condition "!is_inside_tree()" is true` | `add_child()` first | `4.7.2` |
| `await node.ready` never returns | Added to a running tree, the node is ready before `add_child()` returns: the signal already fired | `await get_tree().process_frame` | `4.7.2` |
| `the header has no uid`, `ext_resource has no uid` from `lint_project.gd` | A hand-written `.tscn` or `.tres` | Write `[gd_scene format=3]`, reference by `path=`, then run `lint_project.gd --user-args --fix`: it writes both uids | `kit` |
| `has no <name>.gd.uid sidecar` from `lint_project.gd`, also after `--fix` | A new script or shader that Godot has not imported. `--fix` repairs scenes; it does not write this file | `pwsh ./tools/godot.ps1 --headless --import`, then commit the `.uid` | `kit` |
| A `uid://` you typed is not the one Godot reads | Text that is not a real id is re-encoded: `uid://myinventeduid01` reads back as `uid://b5uju7omloqwm` | Never type one. `lint_project.gd --user-args --fix` | `4.7.2` |
| `ext_resource, invalid UID ... using text path instead` | A stale uid beside a good path: the scene loads, and breaks at the next move | `lint_project.gd --user-args --fix` | `4.7.2` |
| `class cache is stale` from `doctor.ps1`, or a new `class_name` is not found | A script with `class_name` was added, moved or pulled since the last import | `pwsh ./tools/godot.ps1 --headless --import`. `tools/godot.ps1` does it by itself before any run; it also writes the new `.uid` files | `kit` |
| `--headless --import` exits 0, the scripts are broken | Import registers files. It does not compile them: three scripts with a parse error, exit 0, no error line | The proof is `typecheck.ps1`, the tests and a launch | `4.7.2` |
| Lines vanish from `project.godot` | `ProjectSettings.save()` and an editor save write only keys that differ from the engine default: `gdscript/warnings/inferred_declaration=0` and `unused_parameter=1` went, every `=2` line stayed. Behaviour is unchanged | `git diff project.godot`; keep what you did not mean out of the commit. Edit one key as text | `4.7.2` |
| A settings file reads back as defaults | A UTF-8 byte order mark. `ConfigFile.load` returns `OK` and drops the first section, nothing else. Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes one | Write without a mark: PowerShell 7, or your editing tool | `4.7.2` |

## Tests

| Symptom | Cause | Fix | Proof |
|---|---|---|---|
| `The local variable "before" is shadowing an already-declared function ... in the base class "GdUnitTestSuite"` | `before`, `after`, `before_test` and `after_test` are the suite's hooks. The suite does not parse | Rename the local: `count_before` | `kit` |
| gdUnit4 says `No test cases found` and exits 0 | The path held no suite with a test: a wrong path, a suite without `test_` functions, a script that does not extend `GdUnitTestSuite`. (A suite that does not parse exits 105) | Run tests through `tools/test.ps1` or `tools/test_shards.py`: both exit 1 on either | `kit` |
| `Expecting: 'Lamps 0/3   Moves 1/1' but was 'LPampus 0/3   Moves 1/1d'` | gdUnit4 prints a character diff of the two strings, its colours lost: the `but was` text is not the value (here `Paused`) | Print the value, or assert a shorter fact (a bool, an int) beside the string | `kit` |
| `a gdUnit4 suite outside tests.root 'test' never runs` from `lint_tests.ps1` | The runners start gdUnit4 on `tests.root` alone | Move the suite there, or set `tests.root` in `kit.json` | `kit` |
| A simulated press never reaches `_input()` | `Input.action_press()` sets the action's state. It sends no event: polling sees it, event handlers do not | Send an `InputEventAction` through `Input.parse_input_event()`: both see it | `4.7.2` |
| `wall-clock wait: await await_millis(50)` | A test waits on the clock. Also flagged: `OS.delay_msec`, `Time.get_ticks_msec`, `create_timer` | Wait for a signal, a flag, or a number of frames. A real exception: `# wallclock-ok: <reason>` on the line | `kit` |
| `test 'test_hollow' has no assertion` | `lint_tests.ps1` counts `assert_*(`, `verify*(` and `fail(` | Assert. Name a shared helper `assert_<something>` | `kit` |

## DevTools

| Symptom | Cause | Fix | Proof |
|---|---|---|---|
| `No response: no reply to 'ping' after 5s` (exit 3) | No game is running on that `GODOT_AGENT_SESSION` | Start the game with the same session; `uv run python tools/devtools.py wait` | `kit` |
| `Refused: set_state needs the project setting devtools/allow_mutation=true` | Mutation is off, as shipped | Drive the game through input and its own commands. The owner turns mutation on, not an agent | `kit` |
| `Nothing to capture: this run renders no frames (headless)` | A screenshot from a headless run | Start the game with a window | `kit` |
| `Sequence <id> failed at step 1 of 3 (counted from 0): Assertion failed: .:lit_count is 0, expected 5` and exit 1 | A sequence step failed: the second of three. Held actions are released for you | Fix the game or the expectation; the message names node and property | `kit` |
| `Unknown action: <name>. List them: devtools.py input list` | No such input action in `project.godot` | `uv run python tools/devtools.py input list` | `kit` |
| `[OK] Scenes valid`, yet the game is broken | A scene is valid when its own scripts attach. A script it uses by `class_name` did not compile | Read the line printed with it, `Warning: the game has logged 6 error(s) since it started`, then `uv run python tools/devtools.py logs --category error` | `kit` |
| `pwsh ./tools/godot.ps1` never returns | It waits for the game to exit | Start it as its own process: [devtools.md](devtools.md) | `kit` |

## Shells and setup

| Symptom | Cause | Fix | Proof |
|---|---|---|---|
| `--node /root/Main` arrives as `C:/Program Files/Git/root/Main` | Git Bash rewrites an argument that starts with `/` | A relative node path (`Player`, `.`), or PowerShell | `shell` |
| A tool script gets no arguments | PowerShell drops a bare `--` when a script is called from a session (`& ./tools/godot.ps1 ... -- --fix`). `pwsh ./tools/godot.ps1 ... -- --fix` keeps it | Always `--user-args`: `tools/godot.ps1` turns it into `--` | `shell` |
| `Godot 4.7 (standard build) not found`, with `Skipped: ... is a Mono build` | The `godot` on `PATH` is a Mono build | `pwsh ./tools/setup.ps1 -InstallGodot`, or set `GODOT_BIN` | `kit` |
| The same in a new worktree, though the main checkout runs | The main checkout found its Godot through `GODOT_BIN` or `PATH`, not in its own `.tools/godot/`, which is the one place a worktree shares. `Searched:` lists every folder tried | `pwsh ./tools/setup.ps1 -InstallGodot` in the main checkout, or set `GODOT_BIN` | `kit` |
| The session is not set in the next command | Each shell an agent opens starts clean | Set `$env:GODOT_AGENT_SESSION` again, or pass `--session <name>` | `shell` |
| `check: needs PowerShell 7, this is 5.1...` | Started with `powershell`, which is Windows PowerShell 5.1 | `pwsh ./tools/check.ps1` (`winget install Microsoft.PowerShell`) | `shell` |
| `-Path` gets `A positional parameter cannot be found that accepts argument ...` | Two paths. `lint_gdscript.ps1` and `typecheck.ps1` take one | Run once per path, or name their folder | `kit` |
