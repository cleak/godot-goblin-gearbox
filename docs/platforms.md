# Platforms

What was run on which system, what each needs, where things live, what differs, and what on
macOS rests on reading alone.

## What was run

| System | State | On |
|---|---|---|
| Windows x86_64 | Run and checked | Windows 11, PowerShell 7.6: setup, the full check, the runtime loop, the installer |
| Linux x86_64 | Run and checked | Ubuntu 24.04, PowerShell 7.6, 16 cores, no display: the same four, the game under Xvfb with software rendering |
| Linux arm64 | Written, unit-tested, never run | Its Godot download was checksum-verified on x86_64, which cannot start it |
| macOS | Written, unit-tested, never run | [The list](#macos-what-rests-on-reading) |

"Never run" means: the code for that system is there, and its branches are tested on Windows
and Linux with the system's name passed in. Nobody has started it on such a machine. Expect
to be the first, and [send what you find](#check-a-mac-in-ten-minutes).

Also never run: Windows 10; Windows on ARM (it gets the x86_64 Godot); a Linux desktop
session with a window manager; a GPU on Linux; a Wayland session without X11 (below). Another
Unix (FreeBSD) is taken for Linux: the installer downloads the Linux Godot and refuses it
when it does not start.

## Prerequisites

| | Windows | Linux | macOS (not run) |
|---|---|---|---|
| PowerShell | 7.0 or newer: `winget install Microsoft.PowerShell` | 7.2 or newer: [install](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux) | 7.2 or newer: [install](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-macos) |
| uv | `winget install astral-sh.uv` | [install](https://docs.astral.sh/uv/getting-started/installation/) | `brew install uv` |
| git | `winget install Git.Git` | Your package manager: `apt install git`, `dnf install git` | `xcode-select --install` |
| A window for the game | The desktop | A display (`DISPLAY`), or Xvfb: below | A logged-in desktop session |

`pwsh ./tools/doctor.ps1` prints the line of this table that is missing, as its `fix:`.

Python 3.12 comes through uv. Godot comes through `pwsh ./tools/setup.ps1 -InstallGodot`.
The gates need no display and no graphics driver on any system: they run Godot `--headless`,
which loads no X11, Wayland, Vulkan or OpenGL library (measured on Ubuntu 24.04).

Only the runtime loop opens a window. On a Linux machine without a display (an ssh session,
a CI runner, a container) it runs under Xvfb with Mesa's software renderers. The test
machine has these packages; the install line itself, for Debian and Ubuntu, was not run:

```bash
sudo apt install xvfb xauth mesa-vulkan-drivers libvulkan1 libgl1-mesa-dri libxcursor1 libxi6 libxinerama1 libxrandr2
```

The last four are the X11 libraries Godot loads as it opens a window, and nothing in front
of them brings `libxcursor1` or `libxi6` (read from the packages' dependencies). Without
either the game opens no window: measured by hiding each library
([gotchas.md](gotchas.md), "Linux").

Fedora, from package names alone: `xorg-x11-server-Xvfb xorg-x11-xauth mesa-vulkan-drivers
vulkan-loader mesa-dri-drivers libXcursor libXi libXinerama libXrandr`.

## Where things live

`<name>` is `config/name` of `project.godot`, made folder-safe (`: * ? " < > | / \` become
`-`). `ping` prints the user data folder and the screenshot folder of the running game.

| | Windows | Linux | macOS (from Godot's source) |
|---|---|---|---|
| Godot from `-InstallGodot`, under `.tools/godot/` | `Godot_v4.7.2-stable_win64/` | `Godot_v4.7.2-stable_linux.x86_64/` (`.arm64`) | `Godot_v4.7.2-stable_macos.universal/Godot.app` |
| User data (`user://`): `<user data>` | `%APPDATA%\Godot\app_userdata\<name>` | `${XDG_DATA_HOME:-$HOME/.local/share}/godot/app_userdata/<name>` | `$HOME/Library/Application Support/Godot/app_userdata/<name>` |
| The variable that moves it | `APPDATA` | `XDG_DATA_HOME`, an absolute path | `HOME`, and nothing else |
| Screenshots | `<user data>/screenshots/<session>/` | the same | the same |
| DevTools mailbox | `<user data>/devtools_<session>/` | the same | the same |
| Test slot folder (`GODOT_TEST_SLOT_DIR`) | `%TEMP%\godot_test_slots`: one user's runs | `/tmp/godot_test_slots`: every user's runs | `/tmp/godot_test_slots` |
| gdlint, gdformat | `.venv/Scripts/<name>.exe` | `.venv/bin/<name>` | `.venv/bin/<name>` |
| gdtoolkit's grammar cache | `%LOCALAPPDATA%\gdtoolkit` | `~/.cache/gdtoolkit` | `~/Library/Caches/gdtoolkit` |
| Godot's editor settings and caches | `%APPDATA%\Godot` | `~/.config/godot`, `~/.cache/godot` | `~/Library/Application Support/Godot`, `~/Library/Caches/Godot` |
| PowerShell's startup profile, `StartupProfileData-NonInteractive` | `%LOCALAPPDATA%\Microsoft\PowerShell` | `${XDG_CACHE_HOME:-~/.cache}/powershell` | The Linux folder: PowerShell follows the XDG folders there too (its documentation; not run) |
| The kit's own pytest run | `%TEMP%\pytest-of-<user>\` | `/tmp/pytest-of-<user>/` | `$TMPDIR/pytest-of-<user>/` |

- Linux ignores an `XDG_DATA_HOME` that is empty or relative (Godot warns).
- The game and `tools/devtools.py` each work the folder out from those variables: both need
  the same values. To move it for one run, set the variable for the game and for every
  client call.
- A test run never uses that folder: `tools/test.ps1` and each shard of
  `tools/test_shards.py` start Godot with the variable pointed at a temp folder, removed
  afterwards. On macOS that variable is `HOME`: a test there sees the temp folder as home.
- The import that runs first when the class cache is stale (`setup.ps1`, `godot.ps1`,
  `typecheck.ps1`, both test runners) is an editor run. It reads your editor settings, so it
  uses your own Godot folders, as opening the editor does. On a Linux machine that never ran
  Godot it creates `~/.config/godot/editor_settings-4.7.tres`,
  `~/.cache/godot/editor_doc_cache-4.7.res`, `~/.local/share/godot/export_templates` and
  `~/.local/share/godot/app_userdata/<name>/`.
- The other Godot gates of `check.ps1` (`project-lint`, `validate-all`, `scenes`,
  `typecheck`) write a log under your own `<user data>/logs/`, and the `scenes` gate a
  mailbox of its own, `<user data>/devtools_check/`.
- The kit's own tests (`tools/tests/`) copy the kit into a temp folder each, about half a
  gigabyte a run. A passing test's folder goes when it ends; the folders of failed tests
  stay until a later run fails too: delete `pytest-of-<user>/` there when you are done.

## What differs

### Shells

Every command of the kit starts with `pwsh` or `uv` and is typed the same in pwsh and in
bash (run in both; zsh was not). Three things are the shell's own: setting
`GODOT_AGENT_SESSION`, starting the game in the background, and stopping it.
[devtools.md](devtools.md) gives each form.

- Write a path with `/`: every tool takes it on every system, and no shell eats it.
- Git Bash on Windows rewrites an argument that starts with `/` (`--node /root/Main`). A
  Linux or macOS shell does not.
- zsh, the Mac's own shell, typed into: `# ...` after a command is no comment there, and
  the words arrive as arguments (`setup.ps1: A positional parameter cannot be found that
  accepts argument '#'`). The pages' `sh` and `bash` blocks carry none. `AGENTS.md` does,
  for agents, whose shells nobody types into: before pasting a line of it into zsh,
  `setopt interactivecomments`. From the zsh manual; no zsh was run.

### Letter case

Windows takes `Tools` for `tools`. Linux does not. macOS does by default, and the kit
compares exactly there all the same (never run).

| | Windows | Linux |
|---|---|---|
| A path on the command line in other letters (`Test/unit`) | Runs | `path does not exist` |
| A project folder that differs from the kit's in letter case only (`Tools/`, `Docs/`, `agents.md`) | `install.ps1` puts the kit into your folder and says so in a note; git records `Tools/check.ps1`. Then the `agents-md` gate fails (`is spelled Tools/check.ps1 in the project`) and Godot warns `Case mismatch opening requested file`. Rename in two steps: `git mv Tools tools-x`, `git mv tools-x tools` | `install.ps1` refuses: `... differ from the kit's in letter case only`. `git mv Tools tools` |
| `a.gd` beside `A.gd` | One file | Two files, each checked |
| Session names `Agent` and `agent` | One mailbox | Two |
| `kit.json` `source.exclude`, `source.include`, `tests.root` | Letter case ignored | Letter case ignored |

A repository the kit was installed into on Windows with a `Tools/` folder is refused on
Linux until the folder is renamed.

### The display

- Windows: `pwsh ./tools/godot.ps1` opens a window. macOS: it should (not run), and a
  window there draws only while it can be seen: [below](#macos-what-rests-on-reading).
- Linux with `DISPLAY` set: the same.
- Linux with neither `DISPLAY` nor `WAYLAND_DISPLAY`: `godot.ps1` starts nothing and says
  `godot.ps1: no display: DISPLAY and WAYLAND_DISPLAY are not set, so Godot cannot open a
  window. Add --headless, or give it a display: xvfb-run -a pwsh ./tools/godot.ps1 ...`
  (exit 1). A run with `--headless`, `--version` or `--help` is not stopped.
- `xvfb-run -a pwsh ./tools/godot.ps1`: the game renders with Forward+ on Mesa's software
  Vulkan (`llvmpipe`), 1152x648, on the processor: frame rates say nothing about a GPU. With
  no Vulkan driver at all Godot falls back to its Compatibility renderer by itself. `-a` is
  required: plain `xvfb-run` takes display 99 and fails when it is taken.
- `--headless` needs no Xvfb and does everything but render: no screenshot
  (`Nothing to capture: this run renders no frames (headless)`).
- Wayland without X11 (`WAYLAND_DISPLAY` set, `DISPLAY` unset) is not proven and failed
  where tried: on a headless weston and a headless sway the game started and never answered.
  Put `xvfb-run -a` in front there too. A desktop Wayland session normally sets `DISPLAY` as
  well (XWayland); none was measured.
- A screenshot made under Xvfb passes `tools/visual_diff/compare.py` against one made on
  Windows with the default limits (rms 0.91, no changed pixel). `--noise` captures must come
  from the system that made the baseline.

### Stopping a game

`quit` first. When it gets no answer:

| System | Stop | Leaves |
|---|---|---|
| Windows | `taskkill /T /F /PID <id of the process you started>` | Nothing |
| Linux (macOS: not run) | `kill <pid that wait or ping printed>`: Godot's own | Nothing: `godot.ps1` exits 143, `xvfb-run` removes its Xvfb |
| Linux, a game that never answered `wait` | `pwsh -NoProfile -Command '(Get-Process -Id <id of the background job>).Kill($true)'` | `/tmp/.X<n>-lock`, `/tmp/.X11-unix/X<n>`, `/tmp/xvfb-run.<random>/`: harmless, `xvfb-run -a` skips that number. And four files of the killed `pwsh`: `/tmp/clr-debug-pipe-<pid>-*` (two), `/tmp/dotnet-diagnostic-<pid>-*`, `/tmp/CoreFxPipe_PSHost.*.<pid>.None.pwsh` |

On Linux never `kill <id of the background job>` alone: under `xvfb-run` only `xvfb-run`
dies, and Xvfb and the game keep running. `pkill -P <that id>` leaves Godot running too.

### Processes, signals, priority

| | Windows | Linux | macOS (not run) |
|---|---|---|---|
| A gate or shard over its time limit | The tree is killed (`taskkill /T`, a job object) | The tree is killed: each Godot leads a session, its tree is read from `/proc` | The same, the tree read from `ps` |
| A process a passing test left running | Godot waits for it: the shard runs into `--timeout` (exit 124), then the process is killed | Ended with the shard; the run prints `shard <n> left <k> process(es) running, now ended: ...` and its result stands | Left running when it has its own session or its parent ended |
| Stopping `tools/test_shards.py` | Ctrl+C (exit 130) | Ctrl+C (exit 130), SIGTERM (143), SIGHUP (129), SIGQUIT (131): shards killed, slots freed, temp folders removed | As Linux |
| The runner killed outright | Its Godot processes end with the job object | A watchdog process ends them | As Linux |
| Test priority | Below normal | `nice`, 10 steps | `nice`, 10 steps |
| An exit code above 255 | Kept | Modulo 256: `exit 300` is 44 | As Linux |
| Output a program left behind still holds open | Not waited for | Waited for up to 10 s | As Linux |

On Linux and macOS every program the tools start for a test run carries
`GODOT_PROCESS_GROUP=<32 hex digits>` in its environment: that mark is how Linux finds what
a shard left behind. A suite ends what it starts.

The slot folder on Linux and macOS is shared by every user of the machine: the folders the
pool creates get mode 1777, lock files 644, whatever the umask.

### PowerShell versions

- The scripts are written for PowerShell 7.0. Windows runs them from 7.0 on.
- Linux and macOS need 7.2. On Linux 7.0 and 7.1 abort while computing a checksum where only
  OpenSSL 3 is installed (Ubuntu 24.04). On macOS neither was tried, and neither has an
  Apple silicon build. `setup.ps1`, `install_godot.ps1` and `install.ps1` refuse such a
  PowerShell in one line, exit 1, before they download or write anything; `doctor.ps1` fails
  it with the install link.
- Run day to day: 7.6. The floor was run once per system: the gates' own tests under 7.0.13
  on Windows and 7.2.24 on Linux. Under 7.2.24, 2 of 7 runs of the installer's tests lost
  one process to a crash that could not be repeated; 7.6 showed none.
- Windows PowerShell 5.1 (`powershell`): `check.ps1`, `setup.ps1`, `doctor.ps1` and
  `install.ps1` say to use `pwsh` and stop.
- A PowerShell that is killed leaves files in the temp folder on Linux and macOS
  (`clr-debug-pipe-*`, `dotnet-diagnostic-*`). `check.ps1` starts its gates with
  `DOTNET_EnableDiagnostics=0` and `POWERSHELL_DIAGNOSTICS_OPTOUT=1` there, so a gate killed
  on its time limit leaves none. No debugger can attach to such a gate.
- Every `pwsh` of one user shares one startup profile ([where](#where-things-live)) and
  rewrites it in place as it ends. On Linux two that end at the same moment can leave it
  mixed: the next `pwsh` prints `An error has occurred that was not properly handled`, runs
  nothing and exits 0. Six ending together did that in 2 of 400 rounds; killing them, in 0
  of 400; on Windows, 0 of 500. The cure is to delete that one file:
  [gotchas.md](gotchas.md), "Linux".
- `DOTNET_MultiCoreJitNoProfileGather=1` prevents it: PowerShell 7.2 and newer then read the
  profile and write none (0 of 800 rounds, start time unchanged); 7.0 and 7.1 ignore it. The
  kit sets it for the script gates of `check.ps1` (`ascii`, `gdlint`, `typecheck`,
  `test-lint`, `tests`) and for every program its own tests start. A script you start
  yourself is an ordinary PowerShell: where several agents run kit scripts at once on Linux,
  `export DOTNET_MultiCoreJitNoProfileGather=1` in each shell.
- The variable prevents and does not cure. Without it a start that aborts rewrites the
  profile, and the next one works; with it a mixed profile stays as it is and every start
  aborts (5 of 5): delete the file. That is why the CI workflow, which sets the variable
  for its job, passes a step on the script's last line (`setup: ready`, `check: PASSED`)
  and not on exit 0.

## macOS: what rests on reading

Nothing was run on macOS. Each line below is code that exists and is tested with `macos`
passed in, or a fact read from documentation or source.

**PowerShell and starting programs**

- The system and the processor come from `$IsMacOS` and .NET's `OSArchitecture`.
- Starting a program, capturing its output, giving it environment variables, `nice`, killing
  its tree, and reading "permission denied" from a refused start are .NET's Unix code:
  measured on Linux.
- The floor of 7.2 rests on the release assets (7.2.0 is the first with an Apple silicon
  build). Whether 7.0 or 7.1 would run the scripts on an Intel Mac was not tried.
- The `fix:` lines `brew install uv` and `xcode-select --install` were not run.
- That the two variables above keep a killed PowerShell from leaving files in `$TMPDIR`.
- Where PowerShell keeps its startup profile (`~/.cache/powershell`, from its
  documentation: it follows the XDG folders on macOS), whether two that end together mix
  it, and whether `DOTNET_MultiCoreJitNoProfileGather=1` stops the writing: measured on
  Linux and Windows only. The kit's tests take the Linux folder for it.
- git: every Mac has `/usr/bin/git`, and without Apple's Command Line Tools it is a
  placeholder that runs no git command and asks to install them in a dialog. `doctor.ps1`
  asks `xcode-select -p` and warns (`git is Apple's placeholder ...`); tested with a
  stand-in for `xcode-select`. The tools look for a linked worktree with `git` at every
  Godot lookup and take a failure for "no worktree": whether the dialog then shows each
  time is not known.
- zsh: no comment after a command ([Shells](#shells)), and a job started with `&` runs at
  lower priority there (`BG_NICE`, on by default): `devtools.py performance` then measures
  a niced game. `setopt nobgnice` first, or start the game from bash. From the zsh manual.

**Godot: install and lookup**

- The archive holds `Godot.app/Contents/MacOS/Godot`, mode 755, signed: read from the real
  archive on Linux. That the unpacked app starts, and that its signature survives the
  extraction, is inferred.
- The installer does nothing about `com.apple.quarantine`. A zip fetched with a browser and
  passed with `-Source` may need `xattr -dr com.apple.quarantine <path to Godot.app>`.
- `chmod` is looked for on `PATH`, then `/bin/chmod`, `/usr/bin/chmod`.
- `GODOT_BIN` may name `Godot.app` or the program inside it; a search root's own `Godot.app`
  is a candidate.

**User data and HOME**

- The user data path is from Godot 4.7.2's source (`platform/macos/os_macos.mm`): no XDG
  variable is read.
- A test run moves `HOME` for its Godot. That a headless Godot and gdUnit4 run with an empty
  home folder was not tried.
- The kit's own tests move `HOME` for every real Godot they start: uv, git and PowerShell
  started under it look for their settings and caches there too.
- gdtoolkit's cache folder is read from gdtoolkit 4.5.0's source.

**Files, links and letter case**

- The kit compares paths exactly, though the default volume ignores letter case. A relative
  path in other letters (`Test/unit`) should run all the same, as on Windows, and name its
  suites in the letters typed (`res://Test/unit/...`): replayed on Windows with the
  comparison set as on macOS. Only a path whose project folder is spelled in other letters
  is `not a path inside the project`. A kit file whose spelling on disk differs from
  `tools/kit_manifest.json` is taken for the project's own.
- `/tmp` and `/var` are links to `/private/...`, so most temp paths go through the code that
  follows links. It ran with symbolic links on Linux and junctions on Windows.
- The installer has no branch on the system's name: it asks the file system. Renaming a
  finished file onto its name is `rename(2)`, whole or not at all on ext4 and NTFS, assumed
  so on APFS. That a rewritten file keeps its permission bits is from documentation. On a
  volume that ignores letter case it should act as on Windows, on one that does not as on
  Linux; only those two ran.
- File names that are not ASCII (Unicode normal forms) were not looked at. The kit's own
  names are ASCII.

**Test runner**

- `/tmp/godot_test_slots` is assumed shared by every user (`$TMPDIR` is one user's); mode
  1777 and locks on a read-only descriptor are standard calls, measured on Linux.
- The process tree is read with `/bin/ps -A -o pid= -o ppid=`; the parsing ran against
  Linux's `ps`.
- What Godot does at exit while a process it started still runs (Linux exits, Windows
  waits) is unknown there.
- Stop signals, priority and CPU seconds are standard calls, measured on Linux.

**Display and rendering** (each from Godot 4.7.2's source)

- A window draws only while it can be seen: `window_can_draw()` is the window's occlusion
  state (`platform/macos/godot_window_delegate.mm`). Covered by another window, on another
  Space or behind a locked screen, the game renders no frame and `screenshot` fails with
  `Nothing to capture: the window is not drawing ...`. Keep the game window uncovered and
  the screen unlocked. Whether a game started from a shell opens in front is not known.
- Forward+ runs on Metal on Apple silicon (`rendering/rendering_device/driver.macos`); on an
  Intel Mac Godot switches to Vulkan (MoltenVK). No capture from a Mac was ever compared
  with `docs/images/smoke.png`.
- On a Retina display the window is 576x324 points; the viewport and the capture stay
  1152x648.
- Without a main scene Godot shows an alert and waits for a click, with `--headless` too
  (`OS_MacOS::alert` runs a modal window): `godot.ps1` refuses such a run before it starts,
  from what `project.godot` says, on every system alike.

**Runtime loop**

- In a desktop session `pwsh ./tools/godot.ps1` should open a window. Over ssh, with no
  window server, it is unknown; there is no Xvfb. `--headless` should do all but
  screenshots, as on Linux.
- `kill <pid>` to stop a game: measured on Linux.
- Session names that differ in letter case only would share a mailbox on the default volume.

**The kit's own tests**

- Six installer tests stand in for a full disk with a per-process file-size limit. Whether
  PowerShell starts under such a limit on macOS is unknown: a failure there is the test's.
- Tests that need `a.x` beside `A.x` run on Linux only.

## Check a Mac in ten minutes

Not run. In Terminal (zsh), with PowerShell 7.2 or newer, uv and git installed. Keep the
game's window uncovered until the last step.

```bash
sw_vers; uname -m; pwsh --version; uv --version; git --version; xcode-select -p
git clone https://github.com/cleak/godot-goblin-gearbox kit && cd kit
pwsh ./tools/setup.ps1 -InstallGodot 2>&1 | tee setup.log
codesign --verify --deep --strict .tools/godot/*/Godot.app; echo "codesign $?"
xattr -l .tools/godot/*/Godot.app
pwsh ./tools/check.ps1 2>&1 | tee check.log
uv run python tools/test_shards.py --all 2>&1 | tee shards.log
export GODOT_AGENT_SESSION=mac-1
mkdir -p artifacts
pwsh ./tools/godot.ps1 > artifacts/game.log 2>&1 < /dev/null &
uv run python tools/devtools.py wait
uv run python tools/devtools.py ping
uv run python tools/devtools.py input sequence test/sequences/smoke.json
uv run python tools/devtools.py performance
```

Then three things by hand:

1. Put the Terminal window over the game's window and run
   `uv run python tools/devtools.py screenshot --filename covered.png`. Expected: exit 1,
   `Nothing to capture: the window is not drawing`.
2. Paste the `doctor.ps1` line of `AGENTS.md`, "Commands", with its `# ...` comment.
   Expected in zsh: an error that names `#`.
3. `uv run python tools/devtools.py quit`.

Send back, as an issue: the first line's output, `setup.log`, the `codesign` and `xattr`
lines, `check.log`, `shards.log`, the `artifacts/` folder (each gate's log, the game's
output), what `wait`, `ping`, the sequence, `performance` and the three steps printed, which
window was in front when the game started, and the screenshot the sequence saved. A red
gate is as useful as a green one.
