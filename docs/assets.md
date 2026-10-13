# Assets

For models, sprites, textures, sound and effects: what is recorded for each, how a batch is
made and reviewed, and how to fix a look that is off.

## Every asset

- Its source, licence and date go in [provenance.md](provenance.md). Third-party and
  generated alike.
- Raw generated output lands in `artifacts/`, which git ignores. It moves into the project
  after the checks below and after the owner has looked at it.
- Never run a generated script, shader or shell line unread.
- A grey box is fine on a branch and a defect in a release.
- Give each budget a number in `AGENTS.md` (triangles, texture size, voices) and a test that
  counts it.

## A batch

Ten creatures, a tile set, a sound pass. Make the work parallel and measurable before the
first asset is touched.

1. **One file per thing.** Makers work side by side; shared files are read-only to them.
2. **A review sheet any agent can make with one command.** Fixed views of one thing: front,
   back, profile, top, the game's own camera at real scale, a silhouette, the key poses. Under
   them its numbers: size, triangles, texture size, each budget. Beside it a copy a script
   can read.
3. **Rules as code.** A complaint that can be stated as a rule becomes an audit, a test or a
   number on the sheet before its fix lands. Nobody finds the same kind of defect by eye twice.
4. **"Before" sheets** of everything, kept in `artifacts/`.
5. **A brief per thing:** the owner's words, word for word; then notes; then what the last
   round left open.

A round:

- One maker per thing. It makes a sheet after every edit and reads it.
- Two reviewers per thing, side by side. An art director makes a fresh sheet and judges by
  eye at game scale. A technical reviewer reads how the thing is built: topology, naming,
  pivots and rig, budgets. It passes when both pass it. At most two revisions a round.
- Makers report what the shared code lacks. They do not edit it.

Between rounds, promote, do not patch: when three things reinvent one helper, or all of them
hit the same ceiling, the fix belongs in the shared code, reviewed on its own, merged before
the next round.

To close:

1. Show the owner the sheets. Run another round for whatever still misses their own words,
   not only the reviewers'.
2. A kind of defect the owner found and the reviewers passed becomes a counted check first,
   then gets fixed.
3. A deliberate pose that reads as a defect is a defect.
4. Leave a before-and-after montage and one list of what is still open.

## At game scale

- Judge in the game's camera, at the game's resolution, beside its neighbours. A close-up
  flatters.
- The silhouette alone must say what the thing is.
- Detail smaller than a few pixels at game scale is noise: spend it on shape.

## A look that is off

- Split it first: colour only, normals only, light only. See which one carries the fault.
- List the ingredients that give the unwanted read: materials, the light model, proportions,
  outlines, the shapes of effects. Change those at the source.
- Colour grading is the final trim, never the fix.
- Popping, or a hard edge that moves: look for one continuous model before another threshold,
  and hold it with a test that bounds how much a small step may change the result.
- Show before and after as real captures, made the same way.

## With the kit

| For | Use |
|---|---|
| A sprite with no texture, a mesh instance with no mesh, a material with no shader, an animation track whose node is gone | `uv run python tools/devtools.py validate-all --strict`, game running: [devtools.md](devtools.md) |
| A capture against a baseline, and a before-and-after picture | `tools/visual_diff/compare.py` writes the numbers and a `contact_sheet.png`: [its page](../tools/visual_diff/README.md). Pin the seed, renderer, window size, camera and the frame |
| A review sheet | A scene that shows one thing; a command of your own that sets each fixed view (`DevTools.register_handler`); a sequence with one `screenshot` step per view; a script that tiles the pictures into `artifacts/` (Pillow is installed) |
| The thing in the running game | The runtime loop and a screenshot you open: [devtools.md](devtools.md) |
| A folder Godot must not import (sheets, source files) | An empty `.gdignore` in it |
| How it moves | [feel.md](feel.md) |
| Whether it is good | [critique.md](critique.md) |
