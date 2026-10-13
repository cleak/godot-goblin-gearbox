# Changelog

## 0.1.0

The first public version. Godot 4.7 (4.7.2 pinned), standard build, GDScript, Windows,
PowerShell 7, Python 3.12 through uv.

- **One gate.** `tools/check.ps1` runs fifteen gates and prints one table: BOM-less ASCII
  scripts, gdformat and gdlint, the engine's strict type check, uid and scene lint, shader
  lint, load-every-resource, instantiate-every-scene, test lint, ruff, the kit's own pytest
  suite, the layering check, typed ternaries, no wall-clock waits in tests, the `AGENTS.md`
  lint, gdUnit4. A gate that checked nothing is `skipped`, never `pass`. `-Changed` runs the
  tests the diff can affect and leaves the kit's own tests out; `-Fix` formats first.
- **Setup.** `tools/setup.ps1`, `tools/doctor.ps1` (each problem with its fix),
  `tools/install_godot.ps1` (the pinned build, SHA-512 checked), `tools/new_game.ps1` (names
  the game; `-RemoveKitTests` deletes the kit's own tests). Godot is looked for only where
  you say: `GODOT_BIN`, `.tools/godot/`, `godot.search_roots`, `PATH`. In a linked git
  worktree the tools find the Godot the main worktree downloaded.
- **Runtime.** The `addons/agent_devtools` autoload and `tools/devtools.py`: screenshots,
  scene tree, node state, performance, input press and tap, sequences with asserts, scene
  validation, the engine's errors, your own commands. Plain files, no server. One mailbox and
  one screenshot folder per `GODOT_AGENT_SESSION`. Mutation is off unless the project turns it
  on. An MCP server is optional.
- **Tests.** gdUnit4 6.2.2, vendored unmodified and pinned by hash. `tools/test_shards.py`:
  parallel processes, the suites a diff can affect, a machine-wide slot pool.
  `tools/test.ps1`: one process. Both run in a `user://` of their own, and both fail a suite
  that does not parse and a run without a test case.
- **Drop-in.** `install.ps1` puts the kit into an existing Godot 4.7 project and updates it
  later. It edits `project.godot` as text and never overwrites a file of yours. Starter files
  (`docs/`, `CLAUDE.md`, lint, Python and CI config) follow the kit until you change them.
  `tools/kit_manifest.json` records what it installed and every edit it made: enough to
  remove the kit by hand. `-StrictWarnings` sets the strict warnings in `project.godot`.
- **A quality bar.** A `Quality (default)` section in `AGENTS.md` and three pages behind it:
  `docs/feel.md` (the twelve principles of animation, juice, starting numbers, how to prove
  it), `docs/assets.md` (batches with review sheets and rules as code, fixing a look at its
  source) with `docs/provenance.md` to fill, `docs/critique.md` (read-only critics, evidence
  for every finding, rounds, design panels).
- **Configuration.** `kit.json`: the Godot and gdUnit4 pins, which folders are your code,
  where tests live, `gdscript.strict`. No folder layout is assumed.
- **Godot script tools.** `lint_project.gd` (`--fix` repairs uids), `lint_shaders.gd`,
  `validate_all.gd`, `add_input_action.gd`, all on `tools/headless_tool.gd`, which exits 3
  when the tool itself broke. A path given to one never leaves the project.
- **For agents.** `AGENTS.md` with a Project part to edit and a Kit part that ships;
  `CLAUDE.md`, a Copilot pointer and Claude Code permissions that name each pre-approved
  command; `docs/`; a sample game with tests and an input sequence; a CI workflow that runs
  `setup.ps1` and `check.ps1`.

Not in it: Linux and macOS, C#, Godot versions other than 4.7, an uninstaller.
