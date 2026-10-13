# Changelog

## 0.1.0

The first public version. Godot 4.7 (4.7.2 pinned), standard build, GDScript, PowerShell 7,
Python 3.12 through uv.

**Systems.** Run and checked on Windows x86_64 (Windows 11) and Linux x86_64 (Ubuntu 24.04,
the game under Xvfb). Written and unit-tested for Linux arm64 and macOS, never run there.
PowerShell 7.0 or newer on Windows, 7.2 or newer on Linux and macOS. `docs/platforms.md`.
The full check on both: 1,232 pytest cases (64 skipped on Windows, 16 on Linux as another
system's) and 126 gdUnit4 cases, in 210 to 230 s on Windows and about 220 s on Linux.

- **One check.** `tools/check.ps1` runs fifteen gates and prints one table: BOM-less ASCII
  scripts, gdformat and gdlint, the engine's strict type check, uid and scene lint, shader
  lint, load-every-resource, instantiate-every-scene, test lint, ruff, the kit's own pytest
  suite, the layering check, typed ternaries, no wall-clock waits in tests, the `AGENTS.md`
  lint, gdUnit4. A gate that checked nothing is `skipped`, never `pass`. `-Changed` runs the
  tests the diff can affect and leaves the kit's own tests out; `-Fix` formats first. No gate
  needs a display. A gate over its time limit is killed with everything it started. The last
  line is the verdict, `check: PASSED: ...` or `check: FAILED (<gates>): ...`.
- **Setup.** `tools/setup.ps1`, `tools/doctor.ps1` (each problem with its fix, in the
  system's own words: winget, brew, a package manager), `tools/install_godot.ps1` (the pinned
  build for this machine, SHA-512 checked), `tools/new_game.ps1` (names the game;
  `-RemoveKitTests` deletes the kit's own tests). Godot is looked for only where you say:
  `GODOT_BIN`, `.tools/godot/`, `godot.search_roots`, `PATH`. In a linked git worktree the
  tools find the Godot the main worktree downloaded.
- **Runtime.** The `addons/agent_devtools` autoload and `tools/devtools.py`: screenshots,
  scene tree, node state, performance, input press and tap, sequences with asserts, scene
  validation, the engine's errors, your own commands. Plain files, no server. One mailbox and
  one screenshot folder per `GODOT_AGENT_SESSION`. Mutation is off unless the project turns it
  on. An MCP server is optional. On Linux without a display the game runs under
  `xvfb-run -a`; `tools/godot.ps1` says so in one line where a window cannot open.
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
- **Configuration.** `kit.json`: the Godot pin with a SHA-512 per system, the gdUnit4 pin,
  which folders are your code, where tests live, `gdscript.strict`. No folder layout is
  assumed.
- **Godot script tools.** `lint_project.gd` (`--fix` repairs uids), `lint_shaders.gd`,
  `validate_all.gd`, `add_input_action.gd`, all on `tools/headless_tool.gd`, which exits 3
  when the tool itself broke. A path given to one never leaves the project.
- **For agents.** `AGENTS.md` with a Project part to edit and a Kit part that ships;
  `CLAUDE.md`, a Copilot pointer and Claude Code permissions that name each pre-approved
  command; `docs/`; a sample game with tests and an input sequence; a CI workflow that runs
  `setup.ps1` and `check.ps1` on `ubuntu-latest` and `windows-latest`.

Behaviour worth knowing:

- **Installer.** No file of the project is written in place: each is written beside its
  place as `.<name>.kit-new` and renamed when complete, so a killed run leaves every file
  whole and the next run finishes it. A rewritten file keeps its permission bits and is a new
  file: a hard link to the old one keeps the old content. `.gitignore` is written whole,
  your bytes first.
  On Windows a file another program holds open stops the run at that file. Ctrl+C while it
  writes is one line and exit 1; the same command finishes the job. `tests.root` of a
  new `kit.json` keeps your folder's spelling (`Tests`). On Linux a name that differs from
  the kit's in letter case only (`Tools/`) is refused; on Windows the kit goes into that
  folder and a note says to rename it, since the `agents-md` gate fails until then (`...
  is spelled Tools/check.ps1 in the project`). A game in a folder below its repository's
  root gets a note: GitHub and pre-commit read their files at the root only. A
  `project.godot` that cannot be read is refused with the system's reason.
- **DevTools client.** Only the game makes a session's mailbox. A command to a session no
  game has started exits 3 at once and writes nothing: `wait` after every launch.
  `Screenshot saved` is printed only for a file that was written. A mailbox this user may
  not write is one line, exit 2. A command written while a starting game empties its command
  folder is written again. `input clear` waits 5 s for a game that is gone, as `ping` and
  `quit` do. A window that draws nothing says why a capture failed: minimized, or on macOS
  covered or behind a locked screen.
- **No main scene.** `check.ps1` lists `scenes` as `skipped`, and `tools/godot.ps1` refuses
  a run of the game in one line, exit 1: Godot would say so in a dialog and wait for a
  click, with `--headless` too.
- **Test runner.** `GODOT_TEST_SLOT_DIR` must be an absolute path. Every slot file is opened
  when a run starts: a slot folder that cannot take them fails the run then, exit 2. On Linux
  and macOS the slot folder is `/tmp/godot_test_slots`, mode 1777, shared by every user.
  Ctrl+C (one line, exit 130), SIGTERM, SIGHUP and SIGQUIT stop a run cleanly; a second
  signal during the cleanup is ignored. On Linux a process a passing shard left running is
  ended and named; on Windows that shard runs into its timeout. Programs the runner starts
  on Linux and macOS carry `GODOT_PROCESS_GROUP` in their environment. The log of a shard
  that broke (a suite that does not parse) shows each Godot error once, without its
  backtrace; `--verbose` shows all of it.
- **Godot lookup.** Blanks around `GODOT_BIN` and `GODOT4_EXE` are not part of the path. On
  Linux and macOS a Godot this user may not execute is refused with the `chmod +x` line. An
  export preset is matched by its exact name. A main scene is read past a description that
  runs over several lines. Called from a session, `tools/godot.ps1` keeps Godot's output on
  the pipeline; under PowerShell before 7.3 a run with a double quote inside an argument, or
  an empty one, writes to the console only.
- **PowerShell.** `setup.ps1`, `install_godot.ps1` and `install.ps1` refuse 7.0 and 7.1 on
  Linux and macOS in one line. `check.ps1` starts its script gates with
  `DOTNET_MultiCoreJitNoProfileGather=1`: none rewrites PowerShell's startup profile, which
  two PowerShells ending at the same moment can leave mixed on Linux; the next `pwsh` then
  runs nothing and exits 0 (`docs/gotchas.md`). On Linux and macOS also with
  `DOTNET_EnableDiagnostics=0`: no debugger attaches to a gate. `doctor.ps1` looks for gdlint
  and gdformat only where this system's `uv sync` puts them, and on macOS warns about
  Apple's placeholder for git (not run).
- **CI.** The workflow starts `setup.ps1` and `check.ps1` from bash and passes a step on the
  script's last line (`setup: ready`, `check: PASSED`), not on exit 0. It uploads the logs of
  a cancelled job too, and keys the Godot cache by system and processor.
- **The kit's own tests.** A passing test's temp folder is removed when it ends; the
  folders of failed tests stay until a later run fails too.
- **Pages.** No command in a `sh` or `bash` block carries a `# comment`: a zsh somebody
  types into hands it over as arguments.

Not in it: C#, Godot versions other than 4.7, an uninstaller.
