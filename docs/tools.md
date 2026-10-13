# Tools

Every command the kit ships: what it runs, its flags and exit codes, and what a pass proves.

Run from the project root. Every command starts with `pwsh` (PowerShell 7) or `uv` and is
typed the same in pwsh and in bash, on Windows and on Linux. What differs between systems:
[platforms.md](platforms.md). Driving the running game is a page of its own:
[devtools.md](devtools.md).

- Every `.ps1` command except `tools/godot.ps1` rejects an argument it does not know: exit 1,
  nothing runs. Four are no commands but functions the others load: `class_cache.ps1`,
  `export_paths.ps1`, `godot_resolver.ps1`, `kit_config.ps1`.
- An unreadable `kit.json` is one line from every tool, never a stack trace.
- The `.ps1` tools work from any working directory and in a project path with spaces or brackets.
- PowerShell: 7.0 or newer on Windows, 7.2 or newer on Linux and macOS. Started with Windows
  PowerShell 5.1 (`powershell`), `check.ps1`, `setup.ps1`, `doctor.ps1` and `install.ps1`
  say to use `pwsh` and stop.
- A path takes `/` on every system. Its letter case is the system's: `Test/unit` runs on
  Windows and is `path does not exist` on Linux.
- `<tests>` on this page is `tests.root` in `kit.json`: `test` unless you changed it.

## check.ps1: every gate

```sh
pwsh ./tools/check.ps1
pwsh ./tools/check.ps1 -Changed
pwsh ./tools/check.ps1 -Fix
pwsh ./tools/check.ps1 -SkipTests -Path <file or folder>
```

Every gate, then every suite. `-Changed`: the gates, and tests only for the suites the diff
can affect. `-Fix`: gdformat rewrites files first. Fifteen gates run one after another. A
failure never stops the run.

| Gate | Runs | A pass proves | It does not prove |
|---|---|---|---|
| `ascii` | `tools/test_scripts_ascii.ps1` | Every PowerShell script is BOM-less ASCII. After `install.ps1`, a script of your own may also be UTF-8 with a byte order mark | |
| `gdlint` | `tools/lint_gdscript.ps1` | Format (gdformat, 100 columns) and style (`.gdlintrc`) | Types. The kit's own files in a project it was installed into: they are left out |
| `typecheck` | `tools/typecheck.ps1` | Every authored `.gd` compiles with the `[debug]` warnings as errors | Run-time type errors: [gotchas.md](gotchas.md) |
| `project-lint` | `tools/lint_project.gd` | Every uid is present and names the right file | A wrong but valid reference. NodePath warnings do not fail it |
| `shader-lint` | `tools/lint_shaders.gd` | Shaders parse and type-check; global uniforms are declared | That a GPU compiles them |
| `validate-all` | `tools/validate_all.gd` | Every script, scene and resource loads; every node type exists | That a scene instantiates and runs |
| `scenes` | Godot, headless, with `--devtools-validate` | Every authored scene instantiates in the project as it starts: scripts compile and attach, signal connections hold. `skipped` without the DevTools addon, its autoload or a main scene | That a scene plays right |
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
| `-GateTimeoutSeconds <n>` | Longest one gate may run (900; the CI workflow passes 1800; 0 is no limit). A gate over it is killed with everything it started, listed as `FAIL  timed out after <n> s`, and the check goes on. Two gates report a timeout of their own, as `FAIL  exit <code>: ...`: `typecheck` gets this limit for each Godot it starts (its log says `did not finish in <n> s` or `exit 124`), and `tests` has `-TestTimeoutSeconds` |
| `-TestTimeoutSeconds <n>` | Passed to `tools/test.ps1` |

Output: one line per gate as it finishes, the output of failing gates only (cut at 200 lines;
each Godot error once, without its GDScript backtrace), then a table: `Gate`, `Result` (`pass`,
`FAIL`, `skipped`), `Seconds`, `Detail`. Every gate's full output stays in
`artifacts/check/<gate>.log`.

A gate is `skipped`, with the reason, when its tool or its input is not in the project (no
`.gdshader` file, no `tools/tests/`, no `AGENTS.md`, no `tools/ci/architecture_modules.json`),
when it had nothing to check, when `uv` is missing, or when a flag says so. A skipped gate is
not a pass: `check: PASSED: 11 passed, 0 failed, 4 skipped` means four things were not checked.
Without Godot the resolver's message is printed once and each Godot gate is
`FAIL  Godot not found`.

A project with no main scene has no game to start, so `scenes` is skipped:

```text
scenes         skipped        -  no main scene: project.godot sets no application/run/main_scene, so no game starts
```

Godot would say so in a dialog and wait for a click, with `--headless` too. A main scene
counts when `project.godot` or an `override.cfg` beside it names one, for every system or for
one feature (`run/main_scene.<feature>`).

No gate needs a display: every Godot the check starts runs `--headless`. The Godot gates
other than `tests` write a log under the project's own `user://logs/`, and `scenes` a
mailbox of its own, `user://devtools_check/`.

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
unreadable `kit.json`). Started with a PowerShell older than 7 it says so in one line and
exits 2 from `powershell -File`, 1 from `powershell ./tools/check.ps1`. The last line is
the verdict:
`check: PASSED: ...` or `check: FAILED (<gates>): ...`. Exit 0 without it is not a pass: a
PowerShell that aborts as it starts runs nothing and can exit 0
([gotchas.md](gotchas.md), "Linux").

Cost on the sample game: about three and a half minutes for a full run. Windows, 32
threads: 210 to 230 s. Linux, 16 cores: about 220 s. A machine busy with other work took
four to seven minutes. All but 13 to 21 s of it is `pytest` on four workers. `-Changed` leaves `pytest`
out while you work on the game: 13 to 17 s on Windows, 10 to 13 s on Linux. After
`new_game.ps1 -RemoveKitTests` a full run is 11 to 15 s. GitHub's hosted Windows runner is
slower: 1,026 s for a full run, 934 s of it `pytest`, measured with three quarters of today's
Python tests. The hosted Linux runner was not measured.

## Setup

| Command | Does | Exit codes |
|---|---|---|
| `pwsh ./tools/setup.ps1` | `uv sync`, finds Godot, first import, then `doctor.ps1`. `-InstallGodot` downloads the pinned Godot when none is found. `-PreCommit` installs the gdlint and gdformat git hooks. Safe to run again | 0 ready, 1 a step failed or doctor found a problem |
| `pwsh ./tools/doctor.ps1` | One line per prerequisite, `[ok]`, `[FAIL]` or `[warn]`, with a `fix:` line under each problem | 0 nothing required is missing, 1 a `[FAIL]` |
| `pwsh ./tools/install_godot.ps1` | Downloads this machine's build of `kit.json` `godot.version` (Windows x86_64, Linux x86_64 or arm64, macOS universal) into `.tools/godot/`, checks its SHA-512 against `kit.json` before anything is unpacked, makes the program executable on Linux and macOS, and runs its `--version`. `-Force` installs again. `-Source <url or zip>` takes it from elsewhere. `-SkipChecksum` installs unverified. `-Platform`, `-Architecture`: another system's download; it must still run here | 0 installed or already there, 1 refused or failed |
| `pwsh ./tools/new_game.ps1 -Title "My Game"` | Sets `config/name` in `project.godot`, and `config/description` with `-Blurb "..."`; without it, it prints the description that stays. `-RemoveKitTests` deletes the kit's own tests: `tools/tests/` and `test/kit/` | 0, or 1 with one line |
| `pwsh ./install.ps1 -Target <project>` | Puts the kit into an existing Godot project, or updates it there. See below | 0 done or nothing to do, 1 refused |

`doctor.ps1` requires: PowerShell (7.0 or newer on Windows, 7.2 or newer on Linux and macOS),
`kit.json`, `project.godot`, the strict `[debug]` warnings, Godot 4.7 (standard build),
GDScript warnings reaching every `source.include` folder, `uv`, gdlint and gdformat in
`.venv`, gdUnit4 at the pinned version, the `DevTools` autoload (stored as a `res://` path or
as `uid://`), a fresh class cache. It warns about: no git (on macOS also Apple's placeholder
for it, `/usr/bin/git` without the Command Line Tools; not run), no DevTools addon, a Godot patch
other than the pinned one, on Windows a lone windowed Godot `.exe`, an `AGENTS.md` that
still says `(sample)` or has no `# Kit` part beside an `AGENTS.kit.md`, and
`devtools/allow_mutation=true` in `project.godot`. With `gdscript.strict` set to `false` in
`kit.json` the two strict-warning checks warn too.

The `fix:` lines for PowerShell, `uv` and git are the system's own: `winget install ...` on
Windows, `brew install uv` and `xcode-select --install` on macOS, the install page and
`apt install git` on Linux. gdlint and gdformat are looked for where this system's `uv sync`
puts them (`.venv/Scripts/` on Windows, `.venv/bin/` elsewhere): a `.venv` another system
made is "not in .venv", fix `uv sync`.

`setup.ps1`, `install_godot.ps1` and `install.ps1` refuse PowerShell 7.0 and 7.1 on Linux
and macOS in one line, exit 1, before any step: `setup: PowerShell 7.0.13: on Linux the kit's
scripts need 7.2 or newer: 7.0 and 7.1 cannot compute a checksum where only OpenSSL 3 is
installed. Fix: install the current PowerShell: ...`.

Two fix lines depend on the project. gdlint missing: `uv sync`, or the `uv add --dev ...`
line when your own `pyproject.toml` does not name gdtoolkit. Another gdUnit4 than the pin:
set `gdunit4.version` in `kit.json` to keep yours, or `uv run python tools/install_gdunit4.py
--force` to replace it (edits in `addons/gdUnit4/` are lost).

`config/name` names the `user://` folder: after `new_game.ps1`, saves, logs and screenshots
start empty under the new name.

### install.ps1

```sh
pwsh ./install.ps1 -Target ../my-game -DryRun
pwsh ./install.ps1 -Target ../my-game
pwsh ./install.ps1 -Target ../my-game -StrictWarnings
```

Run it from a clone of the kit. `-DryRun` prints the plan and writes nothing. Commit the
game first: without git there is no undo. The same command updates the kit later. `Get-Help ./install.ps1 -Full`, in pwsh, prints these
rules. Windows and Linux; written for macOS and never run there.

No file of the project is written in place. Each is written beside its place under a
temporary name, `.<name>.kit-new` (`.gitignore.kit-new` for a name that starts with a dot),
and renamed onto its own name when complete: after a full disk, a killed run or a crash every
file is whole, as it was or as the kit writes it. A rewritten file keeps its permission bits.
A killed run can leave `.kit-new` files; the next run lists them, on a dry run too, deletes
them, and does not say `Up to date`:

```text
  left over      1 temporary file(s) of a run that was cut short
                 - tools/.check.ps1.kit-new
```

| What | Rule |
|---|---|
| Kit files: `tools/`, `addons/agent_devtools/` | Copied, and updated on a later run. A file the kit no longer ships is removed, unless you changed it. Not copied, and never removed: the kit's own tests (`tools/tests/`, `test/kit/`) and one project's data under `tools/ci/` (the layer manifest, allowlists, test timings, suite lists) |
| `addons/gdUnit4/` | Copied only when the project has none, whole or not at all: after an interrupted run there is no `addons/gdUnit4/`, and the next run finishes the job |
| `kit.json` | Written when absent, with the kit's pins, `source.include` empty, `tests.root` the first of `test`, `tests`, `spec` that holds a suite, in your folder's own spelling (`Tests/` gives `Tests`), and `gdscript.strict` `false`. Never updated: take a newer kit's Godot and gdUnit4 pins by hand |
| `-StrictWarnings` | Sets the 16 strict `[debug]` warnings in `project.godot` to error and prints each value it replaced. A `kit.json` it writes gets `gdscript.strict` `true`; in one you already have, set it yourself |
| `AGENTS.md` | Written when absent. Yours is never touched; the kit's lands beside it as `AGENTS.kit.md`: [customizing-agents-md.md](customizing-agents-md.md) |
| Starter files: `CLAUDE.md`, `.claude/settings.json`, `.github/copilot-instructions.md`, `.gdlintrc`, `.editorconfig`, `.gitattributes`, `.pre-commit-config.yaml`, `.python-version`, `pyproject.toml` with `uv.lock`, the CI workflow, the pages of `docs/` | Written where the project has no such file. A later run updates one you have not changed (`~`); one you changed is kept, and named once when the kit's copy changed (`!`); one you deleted stays deleted. One the kit no longer ships stays and is yours (`!`, once). A file that was yours from the start is kept, also when it has the kit's text, and listed (`=`) on a dry run and a first install |
| `project.godot` | Gains the `DevTools` autoload, the two editor plugins and `devtools/allow_mutation=false`. Edited as text: every other byte is kept |
| `.gitignore` | Gains the kit's entries it lacks, at its end; your own bytes stay as they are. A UTF-16 `.gitignore` is refused (git cannot read it either): save it as UTF-8 |
| A test | `<tests>/unit/test_kit_smoke.gd` with its `.uid`, once, when the test folder holds no gdUnit4 suite |

`tools/kit_manifest.json` records what the installer did; commit it. It is the installer's
only authority on which files are the kit's: a file it lists is updated, overwritten and
removed as the kit's. Let only the installer change it. It is the same on every system
(forward slashes, LF, hashes that read CRLF as LF): a project installed into on Windows is
`Up to date` on Linux, and updates from either.

| Key | Holds |
|---|---|
| `kit_version` | The kit that ran last |
| `hash` | A note on how the hashes are made: `sha256 of the file with CRLF read as LF`. Not read back |
| `files` | The kit's files, each with its hash |
| `seeded` | The starter files, each with the hash of the kit's copy it was last compared with. No hash: the kit no longer ships it |
| `wrote.kit_json`, `wrote.gdunit4` | `true` when the installer wrote `kit.json`, copied `addons/gdUnit4/` |
| `wrote.starter_test` | The starter test it wrote |
| `wrote.gitignore` | Each line it added to `.gitignore` |
| `wrote.project_godot` | Each edit to `project.godot`, oldest first: `[autoload] DevTools`, `[editor_plugins] agent_devtools, gdUnit4` (the plugins it enabled), `[devtools] allow_mutation=false`, `[debug] name added` (a `gdscript/warnings/name=2` line), `[debug] name 1 -> 2` (a value it replaced; one entry per warning, the first), and `[debug] new section` for a section it made |
| `unfinished` | Only after a run that stopped halfway: the starter files and test it was writing |

`wrote` is what removing the kit has to undo, over every run: the steps are under
[Removing the kit](#removing-the-kit). It is written before the first file, so a run that
stops halfway keeps it. Such a run ends with two lines, exit 1: fix what the first names and run the same
command again.

```text
install: 'docs/architecture.md' was not written: Permission denied
install: stopped halfway. Fix that, then run the same command again.
```

What stops a run halfway: a full disk; a folder that takes no new file, on a first install
and on an update of a file in it; on Windows a file another program holds open.

Ctrl+C while it writes is one line, `install: interrupted: stopped halfway. Run the same
command again.`, and exit 1 from `pwsh ./install.ps1`. Called from a session you type into,
it prints the line and leaves the session open. Ctrl+C before the first write (while it
reads and compares) ends the run as it ends any script: nothing written, no line, exit 0.

Two notes are no refusal: the run goes on, and each says what to do.

| Note | When |
|---|---|
| `note: N name(s) in the project differ from the kit's in letter case only: 'Docs' (the kit writes 'docs') ...` | Windows, and a macOS volume that ignores letter case (not run): the kit's files go into your folder. At every run until you rename it, in two steps: `git mv Docs Docs-x`, `git mv Docs-x docs`. Until then `agents-md` fails with `is spelled Docs/tools.md in the project` |
| `note: the project is the folder 'game' of a git repository ...` | The game sits below its repository's root, and this run writes `.github/workflows/ci.yml`, `.github/copilot-instructions.md` or `.pre-commit-config.yaml`: GitHub and pre-commit read them at the root only. Move them there and put the folder in front of the paths they name; the workflow also needs `working-directory` under `defaults: run:` |

Refusals: exit 1, one `install:` line, nothing written, on a dry run too.

| Case | Message | Way out |
|---|---|---|
| A kit file you changed | `N kit file(s) in the target differ from what the kit installed` | Move your changes out, or `-Force` overwrites them |
| A file of yours with a kit file's name: the manifest has no record of it | `N file(s) of yours have the name of a kit file` | Rename or move it. `-Force` does not overwrite it |
| A game made from the template: kit files, no manifest, and a `VERSION` other than this kit's | `the game has the kit's files and no tools/kit_manifest.json ...` | Once: run the `install.ps1` of the kit version in the game's `VERSION` (the kit's number: put it back if you changed it; the kit's tag is `v` and that number) and commit the manifest. Then the newer one. Without `VERSION` this is said when a kit file differs |
| An older kit on a newer install | `the project has kit X, this kit is Y` | Pull the kit; `-Force` goes back |
| A `kit_version` on record that is not a version | `... which is newer cannot be told` | `-Force` installs this kit |
| A manifest that is not a JSON object, or lacks `kit_version`, `files`, `seeded` or `wrote` | One line | Restore it from git |
| Your own autoload named `DevTools`, a `project.godot` that is not Godot 4, a target inside the kit | One line each | As the line says |
| A `project.godot` another program holds open, or that this user may not read | `project.godot cannot be read: <the system's reason>` | Close the program, or make the file readable |
| PowerShell 7.0 or 7.1 on Linux or macOS | `PowerShell 7.0.13: on Linux the kit's scripts need 7.2 or newer ...` | Install the current PowerShell: the line ends with the link |
| A link (symbolic link, junction) on the way to a file the run writes or deletes | `the kit writes no file through a link, and the project has N on the way: ...` | Put a real folder or file there. A link the run only reads through (your own linked `addons/gdUnit4`) is left alone |
| Linux: a name that differs from the kit's in letter case only (`Tools/`, `Docs/`, `agents.md`) | `N name(s) in the project differ from the kit's in letter case only: 'Tools' (the kit writes 'tools') ...` | `git mv Tools tools`. On Windows the kit goes into your folder, with the note above; such a repository is refused on Linux until the rename |
| A file where the kit needs a folder, or a folder where it writes a file | `'docs' in the project is a file where the kit needs a folder (for docs/architecture.md)` | Rename or move it |
| A file the run replaces, adds to or deletes is read-only, or open in another program | `N file(s) in the project cannot be written (read-only, no write permission, or open in another program): ...` | Make them writable |

Your style rules judge your files only. `tools/lint_gdscript.ps1` neither lints nor formats
the files the manifest lists and says how many it left out; the `ruff` gate checks only
Python files of your own under `tools/`; the `ascii` gate lets a script of your own be UTF-8
with a byte order mark; the `.pre-commit-config.yaml` the installer writes leaves `addons/`
and `tools/` alone.

The first `check.ps1` in your project reports what your code does not pass yet, each finding
with its fix. Expect `gdlint` to fail until `pwsh ./tools/check.ps1 -Fix` has formatted your
scripts and you have renamed what its naming rules reject, or relaxed the rule in your
`.gdlintrc`, and `project-lint` on a scene or resource without uids: its output ends with the
`--fix` command that writes them. Three gates start as `skipped`: `architecture` (no layers
declared: [architecture.md](architecture.md)), `ruff` (no Python of your own under `tools/`)
and `pytest` (the kit's own tests are not installed). So do `shader-lint` when you have no
shader and `scenes` when the project has no main scene. A project with neither ends its first
clean run with `check: PASSED: 10 passed, 0 failed, 5 skipped`; with a main scene it is 11
passed, 4 skipped. The type gate passes, and its line in the table says strict typing is off.

### Removing the kit

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
  game's user data ([where that is](platforms.md#where-things-live)). `.godot/` is
  Godot's own cache, not the kit's.

## One gate alone

| Command | Flags | Exit codes |
|---|---|---|
| `pwsh ./tools/lint_gdscript.ps1` | `-Path <file or folder>`, `-Fix` (format in place, LF kept), `-LineLength <n>`. gdtoolkit builds its grammar cache on a machine's first run, so the first batch runs alone | 0 clean, 1 issues or no files, 2 gdtoolkit missing |
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
so it checks the paths of any page; `--allow <glob>` passes a name the page is right to give
as an example:

```sh
uv run python tools/check_agents_md.py docs/devtools.md --max-bytes 0 --max-lines 0 --max-columns 0
```

### Godot script tools

```sh
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

```sh
uv run python tools/test_shards.py
uv run python tools/test_shards.py <tests>/unit/test_<name>.gd
uv run python tools/test_shards.py --all
pwsh ./tools/test.ps1 <tests>/unit/test_<name>.gd
```

In that order: the suites the diff can affect; exactly the ones named (a file or a folder);
every suite; one process, with gdUnit4's own log.

A path is `res://`, project-relative, or absolute inside the project. Outside it:
`path is outside the project: <path>`, exit 2 (`test.ps1`: `-Test '<path>' is not a path
inside the project`, exit 1). Missing: `path does not exist: <path>`, exit 2.

| | `tools/test_shards.py` | `tools/test.ps1` |
|---|---|---|
| Runs | Parallel Godot processes, each with its own empty `user://` | One process, with its own empty `user://` |
| Default | Suites the diff against the base branch can affect. No git or no base: every suite | Every suite under `tests.root` |
| A failure | Every case still runs; one line per failure at the end. A shard that broke another way (a suite that does not parse, a crash, a timeout) prints its log, each Godot error once and without its GDScript backtrace; `--verbose` prints all of it | Every case still runs; `-FailFast` stops at the first |
| Machine | Takes slots from a machine-wide pool, at low priority | Low priority, no pool |
| Timeout | `--timeout` per shard (900 s), `--queue-timeout` (3600 s). The hung Godot is killed with everything it started; its slot goes to the next shard | `-TimeoutSeconds` (300) |
| Exit codes | 0 pass, 1 failure, 124 timeout, 2 usage or setup, 130 stopped by Ctrl+C | 0 pass, 1 failure, 2 Godot not found, 124 timeout |

Both fail when a suite did not parse and when the suites they ran held no test case. gdUnit4
by itself exits 105 on a suite that does not parse and 0 when it found no test case: do not
call it directly. A test never sees your game's saves: `user://` is a temp folder, removed
afterwards. The runner moves it for the Godot it starts alone, with `APPDATA` on Windows,
`XDG_DATA_HOME` on Linux, `HOME` on macOS. A diff that selects no suite is another matter:
`test_shards.py` prints `No suites to run` and exits 0, and `check.ps1 -Changed` lists
`tests` as `skipped`.

Low priority is below-normal on Windows and `nice`, 10 steps, on Linux and macOS.

A suite ends what it starts. A process still running when its shard has ended:

| System | What happens |
|---|---|
| Linux | It is ended with the shard, and the run prints `shard <n> left <k> process(es) running, now ended: <pid> <command>`. The result stands |
| Windows | Godot does not end while a process it started with `OS.create_process` runs: the shard runs into `--timeout` (exit 124), and the process is killed with it |
| macOS (not run) | A process that left its session, or whose parent ended, is left running |

Stopping `test_shards.py` with Ctrl+C (exit 130), or on Linux and macOS with SIGTERM (143),
SIGHUP (129) or SIGQUIT (131), kills the shards, frees the slots and removes the run's temp
folder and `artifacts/gdunit-shards/run_<pid>`. Ctrl+C ends with one line, `Stopped by
Ctrl+C.`, and no traceback. A signal during that cleanup is ignored. Killed outright, it
removes no folder; its Godot processes end with it and its slots are free at once.

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
| `pwsh ./tools/godot.ps1 <godot arguments>` | Runs the pinned Godot on this project. No arguments: the game. `-e`: the editor. `-?`: its usage. Re-imports first when the class cache is stale, and creates the output folder of an `--export-*` run (the preset is found by its exact name). Exit code: Godot's; 1 when Godot could not start. Linux without a display: a run that would open a window starts nothing and says `godot.ps1: no display: ...`, exit 1; add `--headless`, or `xvfb-run -a` in front. No main scene: below |
| `... --script res://tools/add_input_action.gd --user-args <action> <events>` | Adds an input action, or events to one that exists |
| `uv run python tools/install_gdunit4.py` | Checks the vendored gdUnit4 against the `kit.json` pin. `--verify` compares every file with the release archive; `--force` reinstalls it and says that local edits are lost. A destination that is a link is refused before any download: `gdunit4 install path may not be a link or junction: <path>` |
| `uv run python tools/ci/godot_binary.py` | Prints the Godot binary the Python tools use. Takes no argument |
| `uv run python tools/visual_diff/compare.py <baseline> <current> --output-dir <dir>` | Compares two screenshots: `tools/visual_diff/README.md` |

`godot.ps1` blocks until Godot exits. To keep working while the game runs, start it as a
separate process: [devtools.md](devtools.md). Called from a session
(`$said = & ./tools/godot.ps1 --version`), Godot's output is on the pipeline. Two cases
write to the console only: a lone windowed Godot `.exe` on Windows, and under PowerShell
before 7.3 a run with a double quote inside an argument or an empty one.

In a project with no main scene a run of the game starts nothing: one line, exit 1.

```text
godot.ps1: no main scene: project.godot sets no application/run/main_scene, so there is no game to run. Set it in the editor (Project Settings > Application > Run > Main Scene), or name a scene: pwsh ./tools/godot.ps1 res://main.tscn
```

A run of the game is one with no argument, or only switches that leave what runs alone
(`--headless`, `--verbose`, `--quit-after <n>`, `--rendering-method <name>`), whatever
follows `--user-args`: `--devtools-validate` and `--devtools-screenshot` need a main scene,
or a scene named in front of `--user-args`. The editor (`-e`), `--script`, `--import`, a
scene path and any argument the wrapper does not know go to Godot as they are. On Linux this
line comes before the `no display` line.

The import that runs first on a stale class cache (`godot.ps1`, `setup.ps1`, `typecheck.ps1`,
both test runners) is an editor run: it reads your editor settings and uses your own Godot
folders, as opening the editor does. [platforms.md](platforms.md#where-things-live) lists
what it creates.

### Input actions

```sh
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
| `godot.version` | `4.7.2` | The Godot the tools accept (by major.minor) and the release `install_godot.ps1` downloads |
| `godot.windows_x86_64_sha512`, `godot.linux_x86_64_sha512`, `godot.linux_arm64_sha512`, `godot.macos_universal_sha512` | | SHA-512 of that release's download for each system, from its `SHA512-SUMS.txt`. The one for this machine empty: the install is refused |
| `godot.search_roots` | `[]` | Folders to look for an unzipped Godot in. Absolute, project-relative, or with `%VAR%`, `$VAR`, `${VAR}` or a leading `~`; an unset variable stays as written |
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
`godot.version` and the four hashes, run `pwsh ./tools/install_godot.ps1`.

What a search root may hold, per system:

| System | Looked for |
|---|---|
| Windows | `Godot_v<version>-stable_win64.exe` with its `_console.exe`, in the root or in a folder named `Godot_v<version>-stable_win64` or `Godot_v<version>-stable_win64.exe` |
| Linux | `Godot_v<version>-stable_linux.x86_64` (`.arm64`), in the root or in a folder of that name |
| macOS (not run) | `Godot_v<version>-stable_macos.universal/Godot.app`, then the root's own `Godot.app` |

- Blanks around `GODOT_BIN`, `GODOT4_EXE` and `GODOT_VERSION` are not part of the value.
- Linux and macOS: a file this user may not execute is refused with
  `'<path>' is not executable. Fix: chmod +x '<path>'`.
- Not found: the message names what to install or set, then `Searched:` with every folder
  tried and one `Skipped:` line per candidate refused. `tools/godot.ps1` and the Python tools
  resolve the same Godot and print the same lines.

To put back a gdUnit4 someone edited: `uv run python tools/install_gdunit4.py --force`.

## Environment variables

| Variable | Meaning |
|---|---|
| `GODOT_BIN`, `GODOT4_EXE` | The Godot binary to use. One that does not fit is an error, not a skip |
| `GODOT_VERSION` | Major.minor to accept in place of the `kit.json` pin |
| `GODOT_AGENT_SESSION` | Which running game `tools/devtools.py` talks to: [devtools.md](devtools.md) |
| `GODOT_TEST_SLOTS` | Size of the machine-wide test slot pool. Default: a quarter of the cores, 2 to 8; every core when `CI` is set; 0 is no limit |
| `GODOT_TEST_SLOT_DIR` | Where the slot lock files live: an absolute path, or the run exits 2. Default: `%TEMP%\godot_test_slots` on Windows (one user's runs), `/tmp/godot_test_slots` on Linux and macOS (every user's runs; the folder is made with mode 1777) |

## Not evidence

- Exit 0 from `check.ps1` without its last line, `check: PASSED: ...`.
- `--headless --import` exiting 0. Import registers files; it does not compile scripts.
- `0 failures` without the count of executed cases.
- A gate marked `skipped`.
- A screenshot nobody opened.
