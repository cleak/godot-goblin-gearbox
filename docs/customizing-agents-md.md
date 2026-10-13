# Customizing AGENTS.md

For the project's owner: how to turn the shipped `AGENTS.md` into your game's, and keep it short.

`AGENTS.md` has two parts. **Project**, on top, is yours. **Kit**, below, describes the tools
and is the same in every project that uses the kit. Where they disagree, Project wins.

Building a game, not changing the kit? Start with this. It names the game and deletes the
kit's own tests, which you do not need:

```powershell
pwsh ./tools/new_game.ps1 -Title "My Game" -Blurb "One line about it." -RemoveKitTests
```

## 1. Find the marks

```powershell
Select-String -Path AGENTS.md -Pattern '\((sample|default)\)'
```

| Mark | Sections | Do |
|---|---|---|
| `(sample)` | Mission, Fixed decisions, Layout | They describe the kit's sample game. Replace the text, delete the mark |
| `(default)` | Architecture, Quality, Git, Done | Starting rules. Read each line; keep, change or delete it. Delete the mark when it is yours |

Three lines at the top of the file explain the marks. With the last `(sample)` gone, delete
the two that begin `A (sample) section` and `Ask the owner`: until then agents are told to
ask you before they invent any design, and `pwsh ./tools/doctor.ps1` warns while the text
`(sample)` is anywhere in the file. Delete `A (default) section` with the last `(default)`.

## 2. Fill the sections

| Section | Write | Size | Leave out |
|---|---|---|---|
| Mission | What the game is, in one line. What wins when two good options conflict. Where the design lives | 2-4 lines | Story, feature lists, roadmap |
| Fixed decisions | Choices that are costly to reverse and that an agent would otherwise decide again: renderer, physics, platforms, player count, input, frame target, what needs your yes | 1 line each | Taste that changes monthly; anything a gate enforces |
| Layout | One row per module: path, what it holds, what it may name | 5-15 rows | Every folder; file lists |
| Architecture | The rules that keep this codebase's shape | 1-2 lines each | A rule nobody will enforce |
| Quality | Your bar for what the player sees and hears: how it must move, what may not ship, where each asset's source is recorded. The pages behind it: [feel.md](feel.md), [assets.md](assets.md), [critique.md](critique.md) | 3-5 lines | The method itself: it is in those pages |
| Git | When agents commit, push, branch; your message format | 2-4 lines | What git does anyway |
| Done | Your extra gates: design updated, changelog, save migration | 1 line each | Steps the Kit part already has |

| | Weak | Strong |
|---|---|---|
| Mission | "A fun, polished game with clean code." | "Top-down fishing roguelike, 2D, single-player. Optimise for a ten-minute playable loop and cheap content." |
| Fixed decision | "We care about performance." | "60 Hz at 1080p on a five-year-old laptop. Jolt. No network call at run time." |
| Layout row | "`scripts/` - code" | "`src/sim/` - turn rules, pure - may name core, content" |
| Architecture | "Keep things decoupled." | "One writer per field per tick; a test fails otherwise." |

Or have an agent interview you:

```text
Interview me, one question at a time, to replace every (sample) section of AGENTS.md and to
confirm every (default) line. Follow docs/customizing-agents-md.md. Never answer for me.
Then make tools/ci/architecture_modules.json match Layout and run pwsh ./tools/check.ps1.
```

## 3. Match the tools

| Changed in `AGENTS.md` | Change too |
|---|---|
| The game's name | `pwsh ./tools/new_game.ps1 -Title "My Game" -Blurb "One line about it."` sets name and description in `project.godot`. Screenshots and saves follow the name |
| Layout | `tools/ci/architecture_modules.json`, then `uv run python tools/check_architecture.py`: [architecture.md](architecture.md) |
| Input actions | `tools/add_input_action.gd` adds one: [tools.md](tools.md). No tool removes one: delete its entry under `[input]` in `project.godot`, or in the editor's Input Map |
| A folder of third-party or generated code | `source.exclude` in `kit.json` |
| Tests somewhere other than `test/` | `tests.root` in `kit.json`, and move every suite: one left outside fails `test-lint`. The Kit part writes the folder as `<tests>`, so it needs no edit. Sequences may live anywhere |
| The sample game is gone | Delete `scripts/core/`, `scripts/game/`, `main.tscn`, `test/unit/test_board.gd`, `test/unit/test_game.gd`, the `.uid` file beside each script, and `test/sequences/smoke.json`. Delete the sample's actions you do not use from `[input]`: `move_left`, `move_right`, `move_up`, `move_down`, `interact`, `pause`. Point `run/main_scene` in `project.godot` and the globs of `tools/ci/architecture_modules.json` at your own files. Write one sequence that asserts your game's state |
| The game was made from the template | The kit's own pages are still at its root. Write your `README.md`. Delete the kit's `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `install.ps1` and `docs/images/`. Keep `VERSION`: the first upgrade asks which kit version you started from. Put your licence in `LICENSE`: the kit's notice stays in `tools/LICENSE` and `addons/agent_devtools/LICENSE` |

Halfway through, `check.ps1` is red and each gate says what is missing: an orphan `.uid`, a
main scene that does not load, no suite under the test folder, a glob that matches no script.

Leave the other pages of `docs/` as shipped. They describe the kit, with the sample game as
their example, and an upgrade keeps them current only while you have not changed them. The
one exception is `docs/provenance.md`: it is yours to fill. Your game's own docs go in files
of their own.

Finish with `pwsh ./tools/check.ps1`.

## 4. Keep it under budget

Agents load this file on every turn of every session. Each line costs each time.

```powershell
uv run python tools/check_agents_md.py
```

`pwsh ./tools/check.ps1` runs it as the `agents-md` gate. It fails on:

| Rule | Limit | Why |
|---|---|---|
| Size | 200 lines, 12288 bytes | A long file gets skimmed |
| Columns | 100 | It is read in terminals and diffs |
| ASCII | No other character | It survives every shell and code page |
| No `@name` outside backticks | | Claude Code reads it as an import and loads that file on every turn |
| Paths | Every project path and file name in backticks or in a command exists; every link resolves | A renamed tool makes the file wrong on day one |

- The Kit part takes 100 of the 200 lines, and a test of the kit keeps it there. The rest is
  yours: the title, the lines on top and Project. They ship at about 60.
- Over the budget: move a rule to its folder or to `docs/` (below). An agent does not raise
  a limit.
- A placeholder in angle brackets (`test_<name>.gd`) is not checked; the folders written out
  in front of one are (`test/sequences/<name>.json` needs a `test/sequences/`). The Kit part
  names your files only as placeholders, the test folder as `<tests>`, so it stays true in
  any project.
- Paths under a `(sample)` heading are not checked either: after an install they name a game
  you do not have. The check's last line counts them until you replace those sections.
- The gate runs with the defaults. `--max-lines`, `--max-bytes` and `--max-columns` change a
  limit when you run the check by hand.

The test for a line:

1. Would an agent act differently without it? No: delete it.
2. Is it true today? No: fix or delete it.
3. Can a tool check it? Put it in the tool. Keep the command, if anything.
4. Does it apply to one folder only? Move it there (below).
5. Is it the why? Move it to `docs/`; leave the rule and the path.

When Project grows past its share, something leaves for everything that enters. Architecture
grows fastest: every painful bug wants a rule.

Point at a doc with a plain path in a `When | Open` table, as the Reference section does. An
agent then opens the page only when the task calls for it.

## The Kit part

- Leave it as shipped. To override a line, write your rule in Project.
- A tool fact that is wrong is a bug in the kit: fix the line, and report it upstream.
- Agents add a rule only when asked, or after the same mistake twice: one line, in Project.

### Kit updates and AGENTS.kit.md

Pull the kit, then run its installer on your project:

```powershell
pwsh ./install.ps1 -Target C:\path\to\game -DryRun
pwsh ./install.ps1 -Target C:\path\to\game
```

A game that began as the template has no `tools/kit_manifest.json`, the record that tells the
kit's files from yours. Once, before its first upgrade: check out the kit at the version in
the game's `VERSION`, run that `install.ps1` on the game, commit the manifest. Until then a
newer installer refuses, and says this.

The installer never touches an `AGENTS.md` that exists. The kit's current text lands beside
it as `AGENTS.kit.md`:

1. First time: copy everything from `# Kit` down into your `AGENTS.md`, below your own
   sections. Take any `(default)` section you want from the Project half. Until then
   `pwsh ./tools/doctor.ps1` warns that agents are not told the kit's commands.
2. Commit `AGENTS.kit.md`, or delete it once merged: the installer puts it back only when
   the kit's text changes. Do not edit it: an edited copy is reported as a conflict.
3. After an upgrade: `git diff AGENTS.kit.md` is exactly what changed in the kit's part, when
   you committed the copy before. Carry those lines over.

No agent loads `AGENTS.kit.md` by itself, and `tools/check_agents_md.py` looks at `AGENTS.md`
only.

What else an upgrade changes, and what it refuses: [tools.md](tools.md), `install.ps1`.

## Nested files

A rule for one folder can live in that folder's own `AGENTS.md`.

| Agent | Loads a nested `AGENTS.md` |
|---|---|
| Codex | When started in that folder or below: root first, nearest last. 32 KiB across all of them |
| Cursor | When working on files in that folder |
| Claude Code | Only through a `CLAUDE.md` beside it that imports it, as the root one does |

A rule every agent must obey stays in the root file.

## How agents find it

| Agent | Reads |
|---|---|
| Claude Code | `CLAUDE.md`, which imports `AGENTS.md`. Without a `CLAUDE.md` it reads `AGENTS.md` itself from version 2.1.277. Claude-only lines go in `CLAUDE.md` |
| Codex, Cursor | `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md`, which points at `AGENTS.md` |
| Gemini CLI | `AGENTS.md` once `.gemini/settings.json` has `"context": {"fileName": "AGENTS.md"}` |
| Aider | `AGENTS.md` once `.aider.conf.yml` has `read: AGENTS.md` |

## Blocks to paste

Optional starting points for Project. Edit before you keep one.

```markdown
## Players
- N players from the first commit, in every system: input, camera, AI targets, saves, UI.
- The host decides. Intent is a command; outcomes are events and state.
- Feel is local: hit-stop and shake never touch shared state.
```

```markdown
## Performance
- Target: <fps> at <resolution> on <machine>.
- Bound the work per frame: stagger updates, keep dirty flags. No whole-world loop on a frame path.
- A speed claim needs numbers before and after, several runs each.
```

```markdown
## Saves
- A save is a contract: append ids, never reorder; bump the version when the layout changes.
- One fixture per version; a test loads every one.
```

```markdown
## Assets
- Every third-party or generated asset: source, licence and date in docs/provenance.md.
- Generated output lands in artifacts/ and is promoted only after your review.
```
