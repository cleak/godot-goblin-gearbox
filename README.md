# Godot Goblin Gearbox

A Godot project template for working with coding agents: one command that checks everything,
and a way for the agent to drive the running game and look at it.

**Godot 4.7, GDScript. Windows and Linux.** PowerShell 7, Python 3.12 through uv. MIT.

| System | |
|---|---|
| Windows x86_64 | Run and checked: Windows 11 |
| Linux x86_64 | Run and checked: Ubuntu 24.04, the game under Xvfb |
| Linux arm64, macOS | Written and unit-tested, never run |

Never run: the code for that system is there, tested on the other two with its name passed
in. Nobody has started it on such a machine: [docs/platforms.md](docs/platforms.md) lists
what rests on reading, and a ten-minute check for a Mac.

Every gate in one table. Then, with the game running: play a sequence with its asserts,
and look.

```sh
pwsh ./tools/check.ps1
uv run python tools/devtools.py input sequence test/sequences/smoke.json
uv run python tools/devtools.py screenshot --filename smoke.png
```

Every command of the kit starts with `pwsh` or `uv`: type it the same in pwsh and in bash.

![The sample game after the sequence: one lamp lit, the player on it](docs/images/smoke.png)

```text
Gate           Result   Seconds  Detail
----           ------   -------  ------
ascii          pass         0.4  ASCII guard: CLEAN (16 PowerShell script(s))
gdlint         pass         2.9  GDScript lint + format: CLEAN
typecheck      pass         4.7  Type-check: CLEAN (30 of 30 files)
project-lint   pass         0.4  LINT_PROJECT_OK errors=0 warnings=0
shader-lint    skipped        -  no .gdshader files
validate-all   pass         0.9  VALIDATE_ALL_OK loaded=29
scenes         pass         0.4  DevTools: Scenes valid: 1 checked, 0 errors, 0 warnings
test-lint      pass         0.8  Test lint: CLEAN (10 file(s))
ruff           pass         0.1  All checks passed!
pytest         pass       192.8  1168 passed, 64 skipped in 192.38s (0:03:12)
architecture   pass         0.2  architecture: ok
typed-ternary  pass         0.1  typed ternaries: ok (30 files)
wall-clock     pass         0.1  wall clock: ok (10 test scripts, 30 scripts in all)
agents-md      pass         0.1  agents-md: ok (AGENTS.md: 159 lines, 8,519 bytes)
tests          pass         8.1  Tests: PASSED. 126 test cases | 0 errors | 0 failures | 0 flaky | 0 skipped | 0 orphans

check: PASSED: 14 passed, 0 failed, 1 skipped in 212.5s
```

That is Windows. Linux: `1216 passed, 16 skipped` in the `pytest` row and
`check: PASSED: 14 passed, 0 failed, 1 skipped in 218.9s`. A check passed when its last line
says `check: PASSED`: exit 0 without that line is not a pass.

One untyped function added to a rules script:

```text
typecheck      FAIL         4.6  exit 1: Type-check FAILED: 1 file(s) violate the strict GDScript warnings.

  FAIL  res://scripts/core/board.gd
        Parse Error: Variable "twice" has no static type. (Warning treated as error.) (res://scripts/core/board.gd:62)
```

The tools are tested the same way: the `pytest` row is 1,232 cases for the scripts under
`tools/`, 64 of them skipped on Windows and 16 on Linux as another system's, and 111 of the
126 cases in the `tests` row are for the `DevTools` addon.

## What you get

| | |
|---|---|
| **One check** | `tools/check.ps1`: gdformat and gdlint, the engine's strict type check, uid and scene lint, shader lint, load-every-resource, instantiate-every-scene, test lint, a layering check, gdUnit4. One table, `skipped` shown as skipped, then the verdict as the last line: `check: PASSED` or `check: FAILED`, and a non-zero exit on any failure. |
| **Eyes and hands** | The `DevTools` autoload and `tools/devtools.py`: screenshot, scene tree, node state, performance, input press and tap, sequences with asserts, your own commands. Plain files and a CLI: no server to run. |
| **Fast tests** | `tools/test_shards.py`: gdUnit4 in parallel processes, by default the suites your diff can affect. A machine-wide slot pool lets several agents share one machine. |
| **Strict GDScript** | `project.godot` turns untyped declarations, unsafe access, integer division, shadowing and unused code into errors. `tools/typecheck.ps1` enforces them from the command line. |
| **Agent instructions** | `AGENTS.md`, about 160 lines: your project on top, the kit's workflow, commands, runtime loop and verified traps below. `CLAUDE.md` imports it. A lint keeps it short and its paths real. |
| **A quality bar** | Starting rules for how the game moves and what may ship, with the method behind them: the twelve principles of animation and juice ([docs/feel.md](docs/feel.md)), making and reviewing assets ([docs/assets.md](docs/assets.md)), read-only critics who hunt for what is broken, unclear or dull ([docs/critique.md](docs/critique.md)). |
| **Setup that explains itself** | `tools/setup.ps1 -InstallGodot` fetches the pinned Godot for your system and checks its SHA-512. gdUnit4 is vendored. `tools/doctor.ps1` names what is missing and the command that fixes it. |
| **CI** | `.github/workflows/ci.yml` runs the same `check.ps1` on `ubuntu-latest` and `windows-latest`. |

## Start

| Needs | Windows | Linux | macOS (not run) |
|---|---|---|---|
| PowerShell 7 | 7.0 or newer: `winget install Microsoft.PowerShell` | 7.2 or newer: [install](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux) | 7.2 or newer: [install](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-macos) |
| [uv](https://docs.astral.sh/uv/) | `winget install astral-sh.uv` | [install](https://docs.astral.sh/uv/getting-started/installation/) | `brew install uv` |
| git | `winget install Git.Git` | `apt install git`, `dnf install git` | `xcode-select --install` |

The check needs no display. To see the game on a Linux machine without one (ssh, a
container), it runs under Xvfb with software rendering. Debian and Ubuntu, in bash:

```bash
sudo apt install xvfb xauth mesa-vulkan-drivers libvulkan1 libgl1-mesa-dri libxcursor1 libxi6 libxinerama1 libxrandr2
```

That line was not run: the test machine had the packages. The last four are the X11
libraries Godot loads as it opens a window; without the first two of them it opens none
([docs/gotchas.md](docs/gotchas.md), "Linux"). Other systems:
[docs/platforms.md](docs/platforms.md).

**A new game.** Create a repository from this template (GitHub: *Use this template*), clone it,
then set up (Python tools, Godot 4.7.2, first import, doctor), name the game and check:

```sh
pwsh ./tools/setup.ps1 -InstallGodot
pwsh ./tools/new_game.ps1 -Title "My Game" -Blurb "One line about it." -RemoveKitTests
pwsh ./tools/check.ps1
```

**An existing game.** Commit it first: without git there is no undo. Clone the kit anywhere,
then point the installer at your project. `-DryRun` prints the plan and writes nothing:

```sh
pwsh ./install.ps1 -Target ../my-game -DryRun
pwsh ./install.ps1 -Target ../my-game
```

Then, in your project:

```sh
pwsh ./tools/setup.ps1 -InstallGodot
pwsh ./tools/check.ps1
```

The installer copies `tools/` and `addons/agent_devtools/`, adds gdUnit4 when you have none
(whole or not at all), and edits `project.godot` as text: the `DevTools` autoload, the two
editor plugins, `devtools/allow_mutation=false`. Every other byte stays. Where you have no
such file it writes starter files: `kit.json`, `AGENTS.md`, `CLAUDE.md`, `docs/`, lint and
Python config, a CI workflow, a placeholder test. `-DryRun` lists every file. A game in a
folder below its repository's root is told so in a note: GitHub and pre-commit read the
workflow, the Copilot pointer and `.pre-commit-config.yaml` at the root only.

It never overwrites a file of yours. An `AGENTS.md` you already have is kept, and the kit's
lands beside it as `AGENTS.kit.md`. `.gitignore` gains the kit's lines at its end. A file of
yours with the name of a kit file stops the run: nothing is written until you rename it. Your
`.gdlintrc` stays and judges your scripts, not the kit's. A test folder keeps your spelling:
`Tests/` becomes `tests.root` `Tests`.

No file is written in place. Each is written beside its place as `.<name>.kit-new` and
renamed when complete, so a run that is killed, stopped with Ctrl+C or out of disk leaves
every file whole: as it was, or as the kit writes it. Run the same command again and it
finishes. Ctrl+C while it writes is one line and exit 1; before the first write it ends the
run with nothing written, no line and exit 0.

On Linux a folder or file of yours that differs from the kit's in letter case only (`Tools/`,
`Docs/`, `agents.md`) is refused, with the names: rename it (`git mv Tools tools`). Windows
takes the two for one name: the kit goes into your folder, and a note says to rename it
there too, in two steps (`git mv Tools tools-x`, then `git mv tools-x tools`). Until then
the `agents-md` gate fails on the paths `AGENTS.md` names (`is spelled Tools/check.ps1 in
the project`) and Godot warns of a case mismatch.

The first `check.ps1` reports what your code does not pass yet, each finding with its fix.
Expect `gdlint` red until `pwsh ./tools/check.ps1 -Fix` has formatted your scripts (gdformat,
100 columns) and you have renamed what its naming rules reject, or relaxed the rule in your
`.gdlintrc`; and `project-lint` red on a scene without uids: its output ends with the
command that writes them. The strict type gate starts off (`gdscript.strict` is `false` in
the `kit.json` it writes): untyped code passes, and the table says so. When your code is
typed, run the installer again with `-StrictWarnings` and set `gdscript.strict` to `true` in
`kit.json`. The `architecture` gate is `skipped` until you declare your layers
([docs/architecture.md](docs/architecture.md)), and `scenes` while the project has no main
scene.

Downloaded as a zip on Windows? It blocks the scripts until you run, in pwsh,
`Get-ChildItem -Recurse -Filter *.ps1 | Unblock-File` once. A git clone needs nothing.

## The loop

While working. `-Changed`: the gates, and tests for the suites the diff can affect. `-Fix`:
format in place, then check.

```sh
pwsh ./tools/check.ps1 -Changed
pwsh ./tools/check.ps1 -Fix
pwsh ./tools/lint_gdscript.ps1 -Path scripts/core/board.gd
pwsh ./tools/typecheck.ps1 -Path scripts/core
uv run python tools/test_shards.py test/unit/test_board.gd
```

Seeing and driving the game. Start it in the background under a name of your own, one per
running game. Windows, in pwsh:

```powershell
$env:GODOT_AGENT_SESSION = "agent-1"
$game = Start-Process pwsh "-NoProfile -File tools/godot.ps1" -PassThru   # the game, debug build
```

Linux, in bash (macOS: the same lines, not run):

```bash
export GODOT_AGENT_SESSION=agent-1
mkdir -p artifacts
pwsh ./tools/godot.ps1 > artifacts/game.log 2>&1 < /dev/null &
```

On a machine without a display the last line gets `xvfb-run -a` in front:

```bash
xvfb-run -a pwsh ./tools/godot.ps1 > artifacts/game.log 2>&1 < /dev/null &
```

Then, on every system:

```sh
uv run python tools/devtools.py wait
uv run python tools/devtools.py input sequence test/sequences/smoke.json
uv run python tools/devtools.py screenshot --filename smoke.png
uv run python tools/devtools.py performance
uv run python tools/devtools.py quit
```

`wait` prints the game's pid and `screenshot` the file it wrote. The launch in pwsh on Linux,
and how to stop a game that does not quit: [docs/devtools.md](docs/devtools.md).

A sequence is JSON: press, tap, hold, wait, assert, screenshot, call. From the sample:

```json
{
  "steps": [
    {"type": "call", "action": "reset"},
    {"type": "tap", "action": "move_right"},
    {"type": "tap", "action": "move_right"},
    {"type": "tap", "action": "move_up"},
    {"type": "assert", "node": ".", "property": "player_cell", "equals": {"x": 5, "y": 1}},
    {"type": "tap", "action": "interact"},
    {"type": "assert", "node": ".", "property": "lit_count", "equals": 1},
    {"type": "screenshot", "filename": "smoke.png"}
  ]
}
```

A failed assert exits 1 with one line:
`Failed: Sequence a5164a2d failed at step 1 of 3 (counted from 0): Assertion failed: .:lit_count is 0, expected 5`.

Reference: [docs/tools.md](docs/tools.md), [docs/devtools.md](docs/devtools.md),
[docs/gotchas.md](docs/gotchas.md), [docs/platforms.md](docs/platforms.md).

## Make it yours

- [ ] Name it with `tools/new_game.ps1`, as in Start. `-RemoveKitTests` deletes the kit's own
      tests (`tools/tests/`, `test/kit/`): most of a full check's time, and needed only to
      change the kit itself.
- [ ] Edit the sections of `AGENTS.md` marked `(sample)`; read the ones marked `(default)`.
      `tools/doctor.ps1` warns while the text `(sample)` is anywhere in the file.
      Guide: [docs/customizing-agents-md.md](docs/customizing-agents-md.md).
- [ ] Replace the sample game: `main.tscn`, `scripts/`, `test/unit/`, `test/sequences/`, its
      actions under `[input]` in `project.godot`. The full list is in the guide, section 3.
- [ ] Make `tools/ci/architecture_modules.json` match your folders, or delete it:
      [docs/architecture.md](docs/architecture.md).
- [ ] Set `kit.json`: folders that are not your code, where tests live.
- [ ] CI runs on two systems. Building for one? Delete the other from `matrix.os` in
      `.github/workflows/ci.yml`.
- [ ] Write your own `README.md`. Delete `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`,
      `install.ps1` and `docs/images/`. Keep `VERSION`: it is the kit version you started
      from, and the first upgrade asks for it.
- [ ] `LICENSE` is the kit's. Put your game's licence there; the kit's notice stays in
      `tools/LICENSE` and `addons/agent_devtools/LICENSE`.
- [ ] Leave the pages in `docs/` as shipped: they describe the kit, with the sample game as
      their example, and an upgrade keeps them current only while you have not changed them.
- [ ] `pwsh ./tools/check.ps1`.

## Update

Pull the kit, then run its installer on your game again:

```sh
pwsh ./install.ps1 -Target ../my-game -DryRun
pwsh ./install.ps1 -Target ../my-game
```

| Mark | File | What happens |
|---|---|---|
| `~` | A kit file (`tools/`, `addons/agent_devtools/`) | Updated. One you edited is a conflict: listed, nothing written (`-Force` overwrites it) |
| `~` | A starter file you left alone (`docs/`, `CLAUDE.md`, lint, Python and CI config) | Updated |
| `!` | A starter file you changed | Kept. Named once, when the kit's copy changed |
| `!` | A starter file the kit no longer ships | Kept, and yours. Named once |
| | A starter file you deleted | Stays deleted |
| | `AGENTS.md`, `kit.json` | Never touched. The kit's current `AGENTS.md` lands as `AGENTS.kit.md`: [the guide](docs/customizing-agents-md.md#kit-updates-and-agentskitmd) |
| `-` | `left over`: a `.kit-new` file of a run that was killed | Listed, on a dry run too, and deleted by the next run |

`tools/kit_manifest.json` is how the installer tells its files from yours: commit it, and
let only the installer change it. An older kit refuses to replace a newer one. The manifest
is the same on every system: a game installed into on Windows is updated from Linux, and the
other way round.

A run that could not write a file stops with two lines, the first naming the file
(`install: 'docs/tools.md' was not written: Permission denied`), then
`install: stopped halfway. Fix that, then run the same command again.` Every file is still
whole. On Windows a file another program holds open stops it the same way: close the program.

A game made from the template has no manifest yet. Once, before its first upgrade: check out
the kit at the version in your game's `VERSION` (its tag is `v` and that number:
`git checkout v0.1.0`), run that `install.ps1` on the game, commit the manifest. Then run
the newer one. Until then the newer one refuses, and says this.

## Remove it

There is no uninstaller. If the install is one commit, revert it. Otherwise
`tools/kit_manifest.json` lists what to undo:

- `unfinished`, when it is there: a run stopped halfway. Run the installer again first.
- `files`: the kit's files. Delete them, then the manifest.
- `wrote.kit_json`, `wrote.gdunit4`: where `true`, delete `kit.json`, `addons/gdUnit4/`.
- `wrote.starter_test`: delete that file and its `.uid`.
- `wrote.gitignore`: delete those lines from `.gitignore`, with the `# godot-goblin-gearbox` line
  and the blank line above it. A `.gitignore` left empty goes too; one that ended without a
  line break got one.
- `wrote.project_godot`: undo each entry in `project.godot`.

  | Entry | Undo |
  |---|---|
  | `[autoload] DevTools` | Delete the `DevTools=` line |
  | `[editor_plugins] agent_devtools, gdUnit4` | Take the paths of the plugins it names out of `enabled` |
  | `[devtools] allow_mutation=false` | Delete the line |
  | `[debug] name added` | Delete the `gdscript/warnings/name=2` line |
  | `[debug] name 1 -> 2` | Set `gdscript/warnings/name` back to `1` |
  | `[debug] new section`, and so for each section | Last: delete the header when nothing is left under it. An `enabled=PackedStringArray()` left empty goes with it |

- `seeded`: the starter files. Yours to keep or delete.
- Then delete the folders this left empty.
- Not listed: `.venv/`, `.tools/`, `artifacts/`, `__pycache__/` folders under `tools/`, a
  `.kit-new` file a killed run left, and the `devtools*` and `screenshots` folders in the
  game's user data ([where that is](docs/platforms.md#where-things-live)). `.godot/` is
  Godot's own cache, not the kit's.

## Configure

`kit.json` is the one configuration file.

| Key | Meaning |
|---|---|
| `godot.version` | The Godot the tools accept (by major.minor), and the release `tools/install_godot.ps1` downloads |
| `godot.windows_x86_64_sha512`, `godot.linux_x86_64_sha512`, `godot.linux_arm64_sha512`, `godot.macos_universal_sha512` | The SHA-512 of that release's download for each system. The installer verifies the one for this machine |
| `godot.search_roots` | Folders to look for a Godot install in: none unless you name one. `GODOT_BIN` overrides everything |
| `gdunit4.version` | The gdUnit4 that `tools/doctor.ps1` expects in `addons/gdUnit4/` |
| `source.exclude` | Folders that are not your code (`addons`, `artifacts`, `docs`). Dot-folders and folders with a `.gdignore` are always left out |
| `source.include` | Folders inside an excluded one that are checked anyway |
| `gdscript.strict` | `true` (the template): the strict warnings are required. `false`: the type gate reports compile errors and says strict typing is off |
| `tests.root` | Where gdUnit4 suites live (`test`) |

No folder layout is assumed: the gates check every `.gd` file that is not excluded.

## Compatibility

| | |
|---|---|
| Godot 4.7, standard build (4.7.2 pinned) | Yes. Everything is run on it |
| Godot 4.6 and older | No. The tools refuse a Godot that is not 4.7 |
| C#, the Mono build | No. The tools skip a Mono build and check no C# |
| Windows 11 x86_64, PowerShell 7.0 or newer | Yes. Everything is run on it |
| Linux x86_64, PowerShell 7.2 or newer | Yes. Everything is run on Ubuntu 24.04, the game under Xvfb |
| Linux arm64, macOS | Written and unit-tested, never run: [docs/platforms.md](docs/platforms.md) |
| Windows 10 | Untested |
| gdUnit4 | 6.2.2, vendored unmodified, pinned by hash |
| gdtoolkit (gdlint, gdformat) | 4.5.0 |

## Works with

Any agent that reads `AGENTS.md` and can run a shell command: Codex, Cursor, Claude Code and
others. `.github/copilot-instructions.md` points GitHub Copilot at it.

Extras for Claude Code: `CLAUDE.md` imports `AGENTS.md`; `tools/mcp/server.py` offers the
DevTools commands as MCP tools (optional: copy `tools/mcp/.mcp.json.example` to `.mcp.json`);
`.claude/settings.json` pre-approves the kit's check and DevTools commands, so they run your
project's scripts and tests without a prompt.

The kit checks and drives a game. It does not author scenes in the editor.

## Safety

- `DevTools` runs in debug builds and does nothing in a release build.
- It takes commands from files in the game's user data folder. There is no authentication:
  anything that can write to that folder can drive a running debug game. Do not hand a debug
  build with the addon to people you do not trust.
- `set-state` and `run-method` are refused until `devtools/allow_mutation=true` is set in
  `project.godot`. It ships `false`; `tools/doctor.ps1` warns when it is `true`. With it
  `true`, whoever can write that folder can call any method of any node.
- The Godot download is refused when its SHA-512 does not match `kit.json`. A Godot found
  another way (`GODOT_BIN`, `godot.search_roots`, `PATH`) is run as it is.
- Exporting: put `addons/gdUnit4/*, test/*, tools/*, kit.json` in the preset's exclude filter,
  or the build ships them. `addons/agent_devtools` stays: `project.godot` names its autoload.
- A hole: [SECURITY.md](SECURITY.md).

## Limits

- A green check does not show that the game looks right. Someone has to open the screenshot.
- The shader lint parses and type-checks. It does not compile for a GPU.
- Never run: macOS, Linux arm64, a Linux desktop session (a window manager, a GPU), a
  Wayland session without X11. The last one failed where it was tried, on two headless
  compositors: put `xvfb-run -a` in front. [docs/platforms.md](docs/platforms.md).
- A full `check.ps1` takes about three and a half minutes while the kit's own tests are in
  the project: 210 to 230 s on Windows (32 threads), about 220 s on Linux (16 cores), all
  but 13 to 21 s of it those tests; four to seven minutes on a machine busy with other work.
  `check.ps1 -Changed` skips them unless you touched the kit (13 to 17 s on Windows, 10 to
  13 s on Linux); `new_game.ps1 -RemoveKitTests` deletes them (11 to 15 s for a full check).
- On GitHub's hosted Windows runner the check took about 17 minutes while the kit's own
  tests were in the project: 1,026 s measured, 934 s of it those tests, when there were 924
  of them. There are more now: expect longer. The hosted Linux runner was not measured.

## What is in the box

```text
AGENTS.md  CLAUDE.md      agent instructions; edit the (sample) sections
kit.json                  tool configuration
install.ps1               puts the kit into an existing project, and updates it there
tools/                    gates, runners, CLIs (tools/tests/: their own tests)
addons/agent_devtools/    the DevTools autoload (test/kit/: its own tests)
addons/gdUnit4/           test framework, vendored (MIT)
main.tscn  scripts/  test/  a small sample game with tests and an input sequence
docs/                     tools, DevTools, gotchas, platforms, architecture, customizing
                          AGENTS.md, feel, assets, critique
.github/  .claude/        CI, the Copilot pointer, Claude Code permissions
```

## License

MIT: [LICENSE](LICENSE). The same text travels with the kit's files as `tools/LICENSE` and
`addons/agent_devtools/LICENSE`. gdUnit4 is MIT, by Mike Schulze:
[THIRD_PARTY.md](THIRD_PARTY.md). Changes: [CONTRIBUTING.md](CONTRIBUTING.md).
