# Tools

Every command the kit ships: what it runs, its flags and exit codes, and what a pass proves.

Run from the project root, in PowerShell 7 (`pwsh`). Python tools run through `uv run`.
Driving the running game is a page of its own: [devtools.md](devtools.md).

- Every `.ps1` except `tools/godot.ps1` rejects an argument it does not know: exit 1, nothing runs.
- An unreadable `kit.json` is one line from every tool, never a stack trace.
- The `.ps1` tools work from any working directory and in a project path with spaces or brackets.
- Started with Windows PowerShell 5.1 (`powershell`), `check.ps1`, `setup.ps1`, `doctor.ps1`
  and `install.ps1` say to use `pwsh` and stop.
- `<tests>` on this page is `tests.root` in `kit.json`: `test` unless you changed it.

## check.ps1: every gate

```powershell
pwsh ./tools/check.ps1                # every gate, then every suite
pwsh ./tools/check.ps1 -Changed       # the gates; tests only for suites the diff can affect
pwsh ./tools/check.ps1 -Fix           # gdformat rewrites files first
pwsh ./tools/check.ps1 -SkipTests -Path <file or folder>
```

Fifteen gates run one after another. A failure never stops the run.

| Gate | Runs | A pass proves | It does not prove |
|---|---|---|---|
| `ascii` | `tools/test_scripts_ascii.ps1` | Every PowerShell script is BOM-less ASCII. After `install.ps1`, a script of your own may also be UTF-8 with a byte order mark | |
| `gdlint` | `tools/lint_gdscript.ps1` | Format (gdformat, 100 columns) and style (`.gdlintrc`) | Types. The kit's own files in a project it was installed into: they are left out |
| `typecheck` | `tools/typecheck.ps1` | Every authored `.gd` compiles with the `[debug]` warnings as errors | Run-time type errors: [gotchas.md](gotchas.md) |
| `project-lint` | `tools/lint_project.gd` | Every uid is present and names the right file | A wrong but valid reference. NodePath warnings do not fail it |
| `shader-lint` | `tools/lint_shaders.gd` | Shaders parse and type-check; global uniforms are declared | That a GPU compiles them |
| `validate-all` | `tools/validate_all.gd` | Every script, scene and resource loads; every node type exists | That a scene instantiates and runs |
| `scenes` | Godot, headless, with `--devtools-validate` | Every authored scene instantiates in the project as it starts: scripts compile and attach, signal connections hold. `skipped` without the DevTools addon or its autoload | That a scene plays right |
| `test-lint` | `tools/lint_tests.ps1` | Suites are suites and sit under `tests.root`, each test asserts, no `while true:` | That an assertion is a good one |
| `ruff`, `pytest` | `uv run ruff check tools`, `uv run pytest tools/tests` on up to four workers | The kit's Python tools and its own tests. After `install.ps1`: `ruff` checks only Python of your own under `tools/` | Anything about your game |
| `architecture` | `tools/check_architecture.py` | No reference crosses a layer the wrong way: [architecture.md](architecture.md). `skipped` while no `tools/ci/architecture_modules.json` declares the layers | References through scenes |
| `typed-ternary` | `tools/check_typed_ternaries.py` | No `[]` or `{}` in a ternary that feeds a typed container | The same literal as a function argument |
| `wall-clock` | `tools/check_test_wallclock.py` | No test waits on the clock. In any script: no loop that adds up its own sleeps | A `Timer` node a test builds |
| `agents-md` | `tools/check_agents_md.py` | `AGENTS.md` is within budget and names real files | That its rules are right |
| `tests` | `tools/test.ps1`, or `tools/test_shards.py` with `-Changed` | The suites pass | Behaviour no test covers; how the game looks |

| Flag | Effect |
|---|---|
| `-Changed` | Tests come from `tools/test_shards.py`: only the suites the diff can affect. A diff that selects none lists `tests` as `skipped`: nothing was tested. The `pytest` gate is skipped too, with the reason in the table, unless the diff against the base branch (uncommitted and untracked files included) touches `tools/`, `addons/agent_devtools/`, `kit.json`, `pyproject.toml` or `uv.lock`; when it runs, one line names the file that made it. Outside a git repository it runs. Every other gate still checks the whole project. The base branch: below |
| `-SkipTests` | No `tests` gate. It is listed as `skipped`. `pytest` still runs |
| `-Fix` | `gdformat` formats in place, then everything runs |
| `-Path <p>` | Narrows `gdlint` and `typecheck` to a file or folder. Nothing else |
| `-GateTimeoutSeconds <n>` | Longest one gate may run (900; the CI workflow passes 1800) |
| `-TestTimeoutSeconds <n>` | Passed to `tools/test.ps1` |

Output: one line per gate as it finishes, the output of failing gates only (cut at 200 lines;
each Godot error once, without its GDScript backtrace), then a table: `Gate`, `Result` (`pass`,
`FAIL`, `skipped`), `Seconds`, `Detail`. Every gate's full output stays in
`artifacts/check/<gate>.log`.

A gate is `skipped`, with the reason, when its tool or its input is not in the project (no
`.gdshader` file, no `tools/tests/`, no `AGENTS.md`, no `tools/ci/architecture_modules.json`),
when it had nothing to check, when `uv` is missing, or when a flag says so. A skipped gate is
not a pass: `check: PASSED: 11 passed, 0 failed, 4 skipped` means four things were not checked.

The Godot gates (`project-lint`, `shader-lint`, `validate-all`, `scenes`) pass on the tool's
own last line (`LINT_PROJECT_OK`, `DevTools: Scenes valid`), never on exit 0 alone: an
autoload of the game that calls `quit()` ends those runs too. A game that quits by itself
when headless fails `scenes`. One script that does not compile fails `typecheck` and most
of these with it: read the `typecheck` block first, it names file and line once.

`-Changed` compares with the base branch: the remote's default branch, else `origin/main`,
`main`, `origin/master`, `master`. Committed, uncommitted and untracked files all count. On
the base branch itself, with no remote copy, a commit is not a change: the run ends with
`-Changed: on 'main', the base branch itself: ...`, and a full `check.ps1` covers the rest.

Exit codes: 0 no gate failed, 1 a gate failed, 2 it could not run as asked (a bad `-Path`, an
unreadable `kit.json`, a PowerShell older than 7).

Cost on the sample game, on a 32-thread desktop: 131 to 227 s for a full run, idle to busy,
all but about 20 s of it `pytest` on four workers. `-Changed` leaves `pytest` out while you
work on the game: 12 to 20 s. After `new_game.ps1 -RemoveKitTests` a full run is 12 to 17 s.

## Setup

| Command | Does | Exit codes |
|---|---|---|
| `pwsh ./tools/setup.ps1` | `uv sync`, finds Godot, first import, then `doctor.ps1`. `-InstallGodot` downloads the pinned Godot when none is found. `-PreCommit` installs the gdlint and gdformat git hooks. Safe to run again | 0 ready, 1 a step failed or doctor found a problem |
| `pwsh ./tools/doctor.ps1` | One line per prerequisite, `[ok]`, `[FAIL]` or `[warn]`, with a `fix:` line under each problem | 0 nothing required is missing, 1 a `[FAIL]` |
| `pwsh ./tools/install_godot.ps1` | Downloads the Windows build of `kit.json` `godot.version` into `.tools/godot/` and checks its SHA-512 against `kit.json`. `-Force` installs again. `-Source <url or zip>` takes it from elsewhere. `-SkipChecksum` installs unverified | 0 installed or already there, 1 refused or failed |
| `pwsh ./tools/new_game.ps1 -Title "My Game"` | Sets `config/name` in `project.godot`, and `config/description` with `-Blurb "..."`; without it, it prints the description that stays. `-RemoveKitTests` deletes the kit's own tests: `tools/tests/` and `test/kit/` | 0, or 1 with one line |
| `pwsh ./install.ps1 -Target <project>` | Puts the kit into an existing Godot project, or updates it there. See below | 0 done or nothing to do, 1 refused |

`doctor.ps1` requires: PowerShell 7, `kit.json`, `project.godot`, the strict `[debug]` warnings,
Godot 4.7 (standard build), GDScript warnings reaching every `source.include` folder, `uv`,
gdlint and gdformat in `.venv`, gdUnit4 at the pinned version, the `DevTools` autoload (stored
as a `res://` path or as `uid://`), a fresh class cache. It warns about: no git, no DevTools
addon, a Godot patch other than the pinned one, a lone windowed Godot `.exe`, an `AGENTS.md`
that still says `(sample)` or has no `# Kit` part beside an `AGENTS.kit.md`, and
`devtools/allow_mutation=true` in `project.godot`. With `gdscript.strict` set to `false` in
`kit.json` the two strict-warning checks warn too.

Two fix lines depend on the project. gdlint missing: `uv sync`, or the `uv add --dev ...`
line when your own `pyproject.toml` does not name gdtoolkit. Another gdUnit4 than the pin:
set `gdunit4.version` in `kit.json` to keep yours, or `uv run python tools/install_gdunit4.py
--force` to replace it (edits in `addons/gdUnit4/` are lost).

`config/name` names the `user://` folder: after `new_game.ps1`, saves, logs and screenshots
start empty under the new name.

### install.ps1

```powershell
pwsh ./install.ps1 -Target C:\path\to\game -DryRun    # the plan; nothing is written
pwsh ./install.ps1 -Target C:\path\to\game
pwsh ./install.ps1 -Target C:\path\to\game -StrictWarnings
```

Run it from a clone of the kit. Commit the game first: without git there is no undo. The
same command updates the kit later. `Get-Help ./install.ps1 -Full` prints these rules.

| What | Rule |
|---|---|
| Kit files: `tools/`, `addons/agent_devtools/` | Copied, and updated on a later run. A file the kit no longer ships is removed, unless you changed it. Not copied, and never removed: the kit's own tests (`tools/tests/`, `test/kit/`) and one project's data under `tools/ci/` (the layer manifest, allowlists, test timings, suite lists) |
| `addons/gdUnit4/` | Copied only when the project has none, whole or not at all: after an interrupted run there is no `addons/gdUnit4/`, and the next run finishes the job |
| `kit.json` | Written when absent, with the kit's pins, `source.include` empty, `tests.root` the first of `test`, `tests`, `spec` that holds a suite, and `gdscript.strict` `false`. Never updated: take a newer kit's Godot and gdUnit4 pins by hand |
| `-StrictWarnings` | Sets the 16 strict `[debug]` warnings in `project.godot` to error and prints each value it replaced. A `kit.json` it writes gets `gdscript.strict` `true`; in one you already have, set it yourself |
| `AGENTS.md` | Written when absent. Yours is never touched; the kit's lands beside it as `AGENTS.kit.md`: [customizing-agents-md.md](customizing-agents-md.md) |
| Starter files: `CLAUDE.md`, `.claude/settings.json`, `.github/copilot-instructions.md`, `.gdlintrc`, `.editorconfig`, `.gitattributes`, `.pre-commit-config.yaml`, `.python-version`, `pyproject.toml` with `uv.lock`, the CI workflow, the pages of `docs/` | Written where the project has no such file. A later run updates one you have not changed (`~`); one you changed is kept, and named once when the kit's copy changed (`!`); one you deleted stays deleted. One the kit no longer ships stays and is yours (`!`, once). A file that was yours from the start is kept, also when it has the kit's text, and listed (`=`) on a dry run and a first install |
| `project.godot` | Gains the `DevTools` autoload, the two editor plugins and `devtools/allow_mutation=false`. Edited as text: every other byte is kept |
| `.gitignore` | Only appended to: the kit's entries it lacks, after your own bytes. A UTF-16 `.gitignore` is refused (git cannot read it either): save it as UTF-8 |
| A test | `<tests>/unit/test_kit_smoke.gd` with its `.uid`, once, when the test folder holds no gdUnit4 suite |

`tools/kit_manifest.json` records what the installer did; commit it. It is the installer's
only authority on which files are the kit's: a file it lists is updated, overwritten and
removed as the kit's. Let only the installer change it.

| Key | Holds |
|---|---|
| `kit_version` | The kit that ran last |
| `files` | The kit's files, each with its hash |
| `seeded` | The starter files, each with the hash of the kit's copy it was last compared with. No hash: the kit no longer ships it |
| `wrote.kit_json`, `wrote.gdunit4` | `true` when the installer wrote `kit.json`, copied `addons/gdUnit4/` |
| `wrote.starter_test` | The starter test it wrote |
| `wrote.gitignore` | Each line it added to `.gitignore` |
| `wrote.project_godot` | Each edit to `project.godot`, oldest first: `[autoload] DevTools`, `[editor_plugins] agent_devtools, gdUnit4` (the plugins it enabled), `[devtools] allow_mutation=false`, `[debug] name added` (a `gdscript/warnings/name=2` line), `[debug] name 1 -> 2` (a value it replaced; one entry per warning, the first), and `[debug] new section` for a section it made |
| `unfinished` | Only after a run that stopped halfway: the starter files and test it was writing |

`wrote` is what removing the kit has to undo, over every run; the kit's README lists the
steps under "Remove it". It is written before the first file, so a run that stops halfway
keeps it. Such a run ends with `install: stopped halfway`: fix what the line above it
names and run the same command again.

Refusals: exit 1, one `install:` line, nothing written.

| Case | Message | Way out |
|---|---|---|
| A kit file you changed | `N kit file(s) in the target differ from what the kit installed` | Move your changes out, or `-Force` overwrites them |
| A file of yours with a kit file's name: the manifest has no record of it | `N file(s) of yours have the name of a kit file` | Rename or move it. `-Force` does not overwrite it |
| A game made from the template: kit files, no manifest, and a `VERSION` other than this kit's | `the game has the kit's files and no tools/kit_manifest.json ...` | Once: run the `install.ps1` of the kit version in the game's `VERSION` (the kit's number: put it back if you changed it) and commit the manifest. Then the newer one. Without `VERSION` this is said when a kit file differs |
| An older kit on a newer install | `the project has kit X, this kit is Y` | Pull the kit; `-Force` goes back |
| A `kit_version` on record that is not a version | `... which is newer cannot be told` | `-Force` installs this kit |
| A manifest that is not a JSON object, or lacks `kit_version`, `files`, `seeded` or `wrote` | One line | Restore it from git |
| Your own autoload named `DevTools`, a `project.godot` that is not Godot 4, a target inside the kit | One line each | As the line says |

Your style rules judge your files only. `tools/lint_gdscript.ps1` neither lints nor formats
the files the manifest lists and says how many it left out; the `ruff` gate checks only
Python files of your own under `tools/`; the `ascii` gate lets a script of your own be UTF-8
with a byte order mark; the `.pre-commit-config.yaml` the installer writes leaves `addons/`
and `tools/` alone.

The first `check.ps1` in your project reports what your code does not pass yet, each finding
with its fix. Expect `gdlint` to fail until `pwsh ./tools/check.ps1 -Fix` has formatted your
scripts, and `project-lint` on a scene or resource without uids: its output ends with the
`--fix` command that writes them. Four gates start as `skipped`: `architecture` (no layers
declared: [architecture.md](architecture.md)), `ruff` (no Python of your own under `tools/`),
`pytest` (the kit's own tests are not installed) and `shader-lint` when you have no shader.
The type gate passes, and its line in the table says strict typing is off.

## One gate alone

| Command | Flags | Exit codes |
|---|---|---|
| `pwsh ./tools/lint_gdscript.ps1` | `-Path <file or folder>`, `-Fix` (format in place, LF kept), `-LineLength <n>` | 0 clean, 1 issues or no files, 2 gdtoolkit missing |
| `pwsh ./tools/typecheck.ps1` | `-Path <file or folder>`, `-NoImport`, `-TimeoutSeconds <n>` | 0 clean, 1 a file failed or was not checked, 2 Godot not found, 3 the checker itself broke |
| `pwsh ./tools/lint_tests.ps1` | | 0 clean, 1 warnings, 2 errors, no suite, or `uv` missing |
| `pwsh ./tools/test_scripts_ascii.ps1` | | 0 clean, 1 a bad file or no scripts |
| `uv run python tools/check_architecture.py` | `--shrink`, `--baseline`, `--update-node-classes` | 0 ok or not configured, 1 violation, 2 bad manifest |
| `uv run python tools/check_typed_ternaries.py` | `[path ...]` | 0 clean, 1 finding, 2 bad path |
| `uv run python tools/check_test_wallclock.py` | `[path ...]` | 0 clean, 1 finding, 2 bad path |
| `uv run python tools/check_agents_md.py` | `[path]`, `--max-bytes <n>`, `--max-lines <n>`, `--max-columns <n>`, `--allow <glob>`, `--root <dir>` | 0 clean, 1 findings, 2 usage |
| `uv run pytest tools/tests/test_<name>.py -k <test>` | The whole folder as the gate runs it: `uv run pytest tools/tests -n 4 --dist loadgroup` | 0 pass, 1 failure, 4 bad arguments, 5 no test ran |

`-Path` takes one file or folder, with or without the flag's name
(`pwsh ./tools/typecheck.ps1 scripts`). A second path is an error: run once per path, or name
their folder. A named path is always checked, even inside a folder `kit.json` excludes.

**lint_gdscript.ps1**, in a project `install.ps1` put the kit into (it has
`tools/kit_manifest.json`): the files that manifest lists are neither linted nor formatted,
also when named with `-Path`, and one line says how many were left out. They are the kit's
and pass the kit's rules; your `.gdlintrc` is for your scripts.

**typecheck.ps1** imports the project, then loads every authored `.gd` in one Godot process:
a load compiles the script with the warnings `project.godot` turns into errors. A file Godot
loaded with the warnings off was `not checked`. With `gdscript.strict` `true` that fails the
gate and prints the setting to add, and so does a `project.godot` that sets only some of the
16 strict warnings to error: `Not strict: project.godot leaves 15 of the 16 ...`. With
`false` such a file is checked for compile errors only, and one line says strict typing is
off and how to turn it on. Godot applies no warnings under `res://addons` unless
`[debug] gdscript/warnings/directory_rules` names the folder: an addon you want checked
needs that rule and an entry in `source.include`.

**lint_tests.ps1** asks `tools/ci/suites.py`, the rule the runner uses, which scripts are
suites: a script that reaches `GdUnitTestSuite` through what it extends, whatever its file
name. Errors: a script under `tests.root` with `test_` functions that extends no suite,
`while true:` in a suite, and a suite with tests outside `tests.root` that no suite under it
extends: no runner would start it
(`... never runs: move it there, or set tests.root in kit.json`). Warnings: no `test_`
function, a test without `assert_*(`, `verify*(` or `fail(`. Name a shared helper
`assert_<something>` so it counts. `check.ps1` fails the gate on warnings too.

**check_typed_ternaries.py** and **check_test_wallclock.py** take exceptions on the line
(`# typed-ternary-ok: <reason>`, `# wallclock-ok: <reason>`) or per file, as `path | reason`
lines in a file you add under `tools/ci/`: `typed_ternary_allowlist.txt`,
`test_wallclock_allowlist.txt` (a folder ends in `/`; an entry with nothing left to flag
fails). The wall-clock check flags `OS.delay_*`, `Time.get_ticks_*`, `Time.get_unix_time*`,
`create_timer(` and `await_millis(` under `tests.root`. Its second rule covers every authored
script and takes no exception: a loop that bounds its wait by adding up its sleeps
(`OS.delay_msec(10)` beside `waited += 10`) is reported as `summed sleep (use a deadline)`.

**test_scripts_ascii.ps1** checks bytes: a byte order mark, a NUL (UTF-16) or a non-ASCII
byte fails. Windows PowerShell 5.1 misreads such a file. In a project with
`tools/kit_manifest.json`, a script that is not the kit's may be UTF-8 with a byte order
mark; the verdict counts those.

**check_agents_md.py** fails on: more than 12288 bytes or 200 lines, a line over 100
columns, a non-ASCII character, an `@name` outside a code span, a link to a missing file, and
a project path or file name in code that does not exist. A placeholder is not checked
(`test_<name>.gd`), but the folders written out in front of one are:
`test/sequences/<name>.json` needs a `test/sequences/`. Also not checked: what `.gitignore`
lists, a file a kit tool writes later (`AGENTS.kit.md`), and paths under a `(sample)`
heading, which the last line counts. Every rule: the top of the script. A limit of 0 is off,
so it checks the paths of any page:

```powershell
uv run python tools/check_agents_md.py README.md --max-bytes 0 --max-lines 0 --max-columns 0
```

### Godot script tools

```powershell
pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd
pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd --user-args --fix
```

The script's own arguments go after `--user-args`; `--user-args --help` prints them. Exit
codes of all of them: 0 ok, 1 problems, 2 bad arguments or an unusable `kit.json`, 3 the tool
itself broke. On 3 the output ends with a `TOOL_ERROR` line and anything before it means
nothing, an `OK` line included.

| Tool | Arguments | Last line |
|---|---|---|
| `lint_project.gd` | `--scene <scene>` (repeatable), `--all`, `--uids-only`, `--warnings-only`, `--fail-on-warn`, `--fix`, `--json` | `LINT_PROJECT_OK` or `LINT_PROJECT_FAILED errors=<n> warnings=<n>` |
| `lint_shaders.gd` | `[<shader.gdshader> ...]` | `LINT_SHADERS_OK checked=<n>` or `LINT_SHADERS_FAILED failed=<n> checked=<n>` |
| `validate_all.gd` | none | `VALIDATE_ALL_OK loaded=<n>` or `VALIDATE_ALL_FAILED failures=<n> loaded=<n>` |

**lint_project.gd**, uid errors, one line each with the fix: a scene or resource header
without a uid, an `ext_resource` with a missing or wrong uid or a missing file, a script or
shader without its `.uid` file, an orphan `.uid`, two files with one uid. Under them it
prints the whole `--fix` command. `--fix` writes header uids, `ext_resource` uids and the new
path of a moved file; every other byte is kept. It does not write the `.uid` of a new script
or shader: `pwsh ./tools/godot.ps1 --headless --import` does.
NodePath warnings: a stored `NodePath` that names no node of its scene. They exit 0 unless
`--fail-on-warn`. Silence one node with the metadata `lint_ignore_node_paths = true`.
`--json` prints one line, `LINT_PROJECT_JSON {...}`.

**lint_shaders.gd** parses and type-checks with includes expanded. Headless Godot has no GPU:
a shader that passes here can still fail on screen.

**validate_all.gd** loads every authored `.gd`, `.tscn`, `.tres`, `.scn` and `.res`. It does
not instantiate scenes. The `scenes` gate does, headless; `uv run python tools/devtools.py
validate-all` does in the running game.

Your own tool: `extends "res://tools/headless_tool.gd"`, implement
`func _main(args: PackedStringArray) -> Verdict`. A script error in it then exits 3, not 0.

## Tests

```powershell
uv run python tools/test_shards.py                              # suites the diff can affect
uv run python tools/test_shards.py <tests>/unit/test_<name>.gd  # exactly these (file or folder)
uv run python tools/test_shards.py --all                        # every suite
pwsh ./tools/test.ps1 <tests>/unit/test_<name>.gd               # one process, gdUnit4's own log
```

A path is `res://` or project-relative.

| | `tools/test_shards.py` | `tools/test.ps1` |
|---|---|---|
| Runs | Parallel Godot processes, each with its own empty `user://` | One process, with its own empty `user://` |
| Default | Suites the diff against the base branch can affect. No git or no base: every suite | Every suite under `tests.root` |
| A failure | Every case still runs; one line per failure at the end | Every case still runs; `-FailFast` stops at the first |
| Machine | Takes slots from a machine-wide pool, below-normal priority | Below-normal priority, no pool |
| Timeout | `--timeout` per shard (900 s), `--queue-timeout` (3600 s) | `-TimeoutSeconds` (300) |
| Exit codes | 0 pass, 1 failure, 124 timeout, 2 usage or setup | 0 pass, 1 failure, 2 Godot not found, 124 timeout |

Both fail when a suite did not parse and when the suites they ran held no test case. gdUnit4
by itself exits 105 on a suite that does not parse and 0 when it found no test case: do not
call it directly. A test never sees your game's saves: `user://` is a temp folder, removed
afterwards. A diff that selects no suite is another matter: `test_shards.py` prints
`No suites to run` and exits 0, and `check.ps1 -Changed` lists `tests` as `skipped`.

A failed string compare prints gdUnit4's character diff, not the value: `but was
'LPampus 0/3   Moves 1/1d'` for an actual `Paused`. Assert a shorter fact beside the string.

More `test_shards.py` flags: `--changed [BASE]`, `--dev` (every suite but the heavy ones),
`--heavy-seconds <n>` (what heavy means: 3), `--skip-slow`, `--shards <n>`, `--slots <n>`,
`--no-import`, `--keep`, `--verbose`, `--slowest <n>`, `--write-timings [FILE]`,
`--timings <file>`, `--slow-list <file>`, `--always-list <file>`. A flag is written in full.
Optional data files under `tools/ci/`: `test_timings.json` (written by `--all
--write-timings`; commit it), `slow_suites.txt`, `always_run_suites.txt`. A file one of the
last four flags names must lie inside the project. The Godot is the resolver's: set
`GODOT_BIN` to use another.

The diff selection follows references: a `res://` path, a `uid://`, a `class_name`, an
autoload name. It cannot see a path built at run time (`"res://levels/%s.tscn" % name`): name
that suite by path. A change to `project.godot`, `kit.json` or an excluded folder runs every
suite that is not heavy.

A script under `tests.root` with a `func test_...` that is not a suite fails the run: those
tests would never run. Reports: `artifacts/gdunit-reports/` (`test.ps1`).

Where tests live in the template:

| Folder | Holds |
|---|---|
| `<tests>/unit/`, `<tests>/sequences/` | Your game's suites and DevTools sequences. The sample game's are there now |
| `test/kit/` | The DevTools addon's own gdUnit4 suites |
| `tools/tests/` | The tools' own pytest suite |

Building a game, not changing the kit? `new_game.ps1 -RemoveKitTests` deletes the last two.
`install.ps1` does not install them.

## Godot and the project

| Command | Does |
|---|---|
| `pwsh ./tools/godot.ps1 <godot arguments>` | Runs the pinned Godot on this project. No arguments: the game. `-e`: the editor. `-?`: its usage. Re-imports first when the class cache is stale, and creates the output folder of an `--export-*` run. Exit code: Godot's; 1 when Godot could not start |
| `... --script res://tools/add_input_action.gd --user-args <action> <events>` | Adds an input action, or events to one that exists |
| `uv run python tools/install_gdunit4.py` | Checks the vendored gdUnit4 against the `kit.json` pin. `--verify` compares every file with the release archive; `--force` reinstalls it and says that local edits are lost |
| `uv run python tools/ci/godot_binary.py` | Prints the Godot binary the Python tools use. Takes no argument |
| `uv run python tools/visual_diff/compare.py <baseline> <current> --output-dir <dir>` | Compares two screenshots: `tools/visual_diff/README.md` |

`godot.ps1` blocks until Godot exits. To keep working while the game runs, start it as a
separate process: [devtools.md](devtools.md).

### Input actions

```powershell
pwsh ./tools/godot.ps1 --headless --script res://tools/add_input_action.gd --user-args jump --key Space --key W --joy-button a --joy-axis left_x:-1 --mouse-button left --deadzone 0.2
pwsh ./tools/godot.ps1 --headless --script res://tools/add_input_action.gd --user-args --list
```

It only adds: a new action, or new events on an existing one. Run twice, it changes nothing
the second time. It edits the `[input]` section of `project.godot` as text, in the form the
editor writes (device -1, physical keycodes), and keeps every other byte. `--list` prints the
project's actions and their events. Close or reload the editor afterwards. To remove an
action, delete its entry from `[input]` in `project.godot` or in the editor's Input Map.

- Keys are Godot's key names, joined to modifiers with `+`: `--key Ctrl+S`. The plus key
  itself is `Plus` (`--key Ctrl+Plus`); `--key +` is refused with that hint.
- `project.godot` must be plain UTF-8. With an invalid byte or a byte-order mark the tool
  exits 1 and writes nothing: save the file from the Godot editor first.
- Exit codes: 0 done (last line `INPUT_ACTION_OK`), 1 not written, 2 bad arguments, 3 the
  tool itself broke.

## kit.json

The one configuration file. A missing key takes its default. It must be standard JSON: a
comment, a trailing comma, a control character in a string, `01` or `NaN` is one line of
error from every tool, never a silent default.

| Key | Default | Meaning |
|---|---|---|
| `godot.version` | `4.7.2` | The Godot the tools accept (by major.minor) and the build `install_godot.ps1` downloads |
| `godot.windows_x86_64_sha512` | | SHA-512 of that download. Empty: the install is refused |
| `godot.search_roots` | `[]` | Folders to look for an unzipped Godot in. Absolute, project-relative, or with `%VARS%` |
| `gdunit4.version`, `gdunit4.source_archive_sha256` | | The vendored gdUnit4 and the hash of its release archive. `doctor.ps1` fails on another version in `addons/gdUnit4/` |
| `source.exclude` | `["addons", "artifacts", "docs"]` | Folders that are not your code. Folders starting with `.`, holding a `.gdignore` or a `project.godot`, and directory links are always left out |
| `source.include` | `[]` | Folders inside an excluded one that are checked anyway. The template lists `addons/agent_devtools` |
| `gdscript.strict` | `true` | `true`: `doctor.ps1` requires the 16 strict `[debug]` warnings and `typecheck.ps1` fails a file Godot loaded with warnings off. `false`: both only warn; compile errors still fail |
| `tests.root` | `test` | Where gdUnit4 suites live. A suite anywhere else fails `test-lint` |

"Authored source" is every file outside those exclusions, plus `source.include`. No folder
layout is assumed. `tools/kit_config.ps1`, `tools/kit_config.py` and `tools/kit_config.gd` read
the file with one shared rule.

Godot is looked for in this order: `GODOT_BIN`, `GODOT4_EXE`, `.tools/godot/`, in a linked
git worktree the main worktree's `.tools/godot/`, `godot.search_roots`, then `godot` on
`PATH`. No other folder is searched: a Godot unzipped beside your projects needs
`"search_roots": [".."]`. A Mono build is never used. Only the download into `.tools/godot/`
is checked against a hash: a Godot found any other way is run as it is. One `-InstallGodot`
in the main checkout serves every `git worktree add` of it. To move to another Godot: change
`godot.version` and the hash, run `pwsh ./tools/install_godot.ps1`.

To put back a gdUnit4 someone edited: `uv run python tools/install_gdunit4.py --force`.

## Environment variables

| Variable | Meaning |
|---|---|
| `GODOT_BIN`, `GODOT4_EXE` | The Godot binary to use. One that does not fit is an error, not a skip |
| `GODOT_VERSION` | Major.minor to accept in place of the `kit.json` pin |
| `GODOT_AGENT_SESSION` | Which running game `tools/devtools.py` talks to: [devtools.md](devtools.md) |
| `GODOT_TEST_SLOTS` | Size of the machine-wide test slot pool. Default: a quarter of the cores, 2 to 8; every core when `CI` is set; 0 is no limit |
| `GODOT_TEST_SLOT_DIR` | Where the slot lock files live. Default: `godot_test_slots` in the temp folder |

## Not evidence

- `--headless --import` exiting 0. Import registers files; it does not compile scripts.
- `0 failures` without the count of executed cases.
- A gate marked `skipped`.
- A screenshot nobody opened.
