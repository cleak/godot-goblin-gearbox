# Critique

For judging the game, not the code: is it good, is it clear, is any of it dull. Critics that
never edit, evidence for every finding, and rounds that check the round before.

## The bar

A starting bar. The owner edits it.

- Final quality in what ships. No placeholders.
- Characters and places with personality: a shape has a reason, a world suggests a story.
- Substantive and its own. Never a generic time-waster.
- Feel on everything the player sees and hears: [feel.md](feel.md).
- Proven: tests, and pictures someone opened. Never handed back broken.

Dull is a defect like any other: it gets a finding, evidence and a fix.

## Rules for a critic

- Read-only. A critic runs the game and reads the project. It writes under `artifacts/`
  only: one report, and the sequences and scripts that back it.
- Every finding has: where (a screenshot, a seed and the keys pressed, a file and line), why
  it matters to this game, a number, and a concrete fix that names the file.
- No generic advice. No finding the critic did not verify.
- Severity: critical, high, medium, low. Ranked.
- Say what is strong and must be kept. The fixers need to know what not to break.
- A finding only the designer can settle is a question with two to four options, never a
  silent decision.

## Three critics

| Critic | Does | Reports |
|---|---|---|
| Visual: an exacting art director | Captures every level or area, at its brightest and darkest, in each mode, the HUD, the title; several seeds. Measures the pictures against the reference: medians of small blocks, luma, chroma | A verdict against the reference and against the last round; about a dozen issues, each with the picture, the reason and a fix with numbers |
| Play: a veteran player of the genre | Plays through the runtime loop. Tries to break it. Hunts for bugs, confusion and dull moments | Bugs with an exact repro (seed, keys, evidence); what could not be read; how it feels; what is dull, unfair or always the right choice, each with a proposal |
| Code: correctness only | Reads the seams: async, re-entrancy, restart, pause, saves. May run the tests, starts no windowed game | Findings from critical to low with file and line, a failure scenario and a fix. No style |

Run them side by side. Run more than one of a kind when the game is large.

## Is it dull?

Questions for the play critic. Each answer names the moment it saw.

- The first five minutes: what did I do, and what did I want to do next?
- Where did I wait with nothing to decide?
- Is one choice always right? Then the others are decoration.
- When was I last surprised? Ten minutes without is a finding.
- Did anything hurt me that I could not have seen coming?
- Could this moment sit unchanged in another game? What here is this game's own?
- What would I tell a friend about afterwards? Nothing is a finding.

## Design panels

For reviewing a design before it is built, split the lenses of Jesse Schell's *The Art of Game
Design: A Book of Lenses* across panels so that every lens has an owner:

| Panel | Owns the chapters on |
|---|---|
| Player experience | Experience, venue, the game, elements, theme, the player, the player's mind, motivation |
| Systems and balance | Mechanics, balance, puzzles |
| Feel, interface, aesthetics | Interface, interest curves, presence, aesthetics |
| World, story, atmosphere | Story, indirect control, worlds, characters, spaces |
| Other players | Other players, communities |
| Technology and production | The idea, iteration, team, documents, playtesting, technology, client, pitch, profit, transformation, responsibility, purpose |

A panel applies every lens it owns and lists the ones that gave no finding, with the reason.

## Rounds

Run a round after a batch of design decisions, after each playable milestone, and after a
playtest by people.

1. Write the brief: the game in a paragraph, the decisions since the last round, the rules
   above.
2. Start the critics side by side, each with the brief and its own role.
3. The lead merges the reports: one list, duplicates joined, ranked, committed. The next
   round reads it, and `artifacts/` is not kept. Accepted findings become tasks; questions
   go to the designer.
4. The next round opens with a table of the last round's findings: fixed, partial or
   regressed, each with evidence. A fix that went too far shows up here.

## A brief

```text
You are a read-only critic: <role>. Do not edit the project. Write only under <report folder>.
The game: <one paragraph>. The reference it aims at: <pictures or games>.
Decided since last round: <list>. Last round's findings: <path>.
First give the status of every earlier finding (fixed, partial, regressed) with evidence.
Then rank at most fifteen new findings. Each has: severity, where, why it matters to this
game, a number, a concrete fix naming the file. Then what must be kept.
A finding only the designer can settle is a question with options.
Capture with: <the runtime loop lines>. Your session: <name>.
Stop only processes you started. Final message: ten lines and the report path.
```

## With the kit

- One `GODOT_AGENT_SESSION` per critic: its own mailbox and screenshot folder.
- One Godot at a time per critic; it quits its game when it is done.
- A sequence makes a finding repeatable: the steps that reach the moment, an assert, a
  screenshot. Commands and steps: [devtools.md](devtools.md).
- Two captures of one scene can be compared by number: [assets.md](assets.md).
