# Feel

For anything the player sees or hears move: the twelve principles of animation (Thomas and
Johnston, *The Illusion of Life*), and juice.

Two rules for every player-visible change:

1. Apply the twelve principles wherever they apply: characters, props, camera, UI, effects.
2. Add juice, in code first: springs, curves, particles and sound before hand-keyed animation.

## Limits

- Feel is presentation. It reads game state and never writes it: with every effect off, the
  rules, the saves and the tests give the same result.
- It never blocks input for more than about 0.12 s.
- It uses its own random numbers, never the seeded generator the rules use.
- Every intensity has a scale the player can lower: motion, flash, shake. At 0 the cue that
  carries information stays (a sound, a colour, a number).
- With more than one player it is local: one player's hit-stop or shake never slows or moves
  another's game. Never change `Engine.time_scale` for it.

## The twelve principles

| Principle | In a game |
|---|---|
| Squash and stretch | Scale about the feet, volume kept: x and z take `1 / sqrt(y)`. Hops, landings, hits, UI pops |
| Anticipation | A wind-up before every attack, jump, throw and heavy swing. On an enemy it is also fairness: the player can read what is coming |
| Staging | One clear read per moment: light, silhouette or a highlight on what matters; quiet the rest |
| Straight ahead and pose to pose | Key poses are authored; springs and noise add life on top |
| Follow through and overlapping action | Damped springs on whatever hangs or trails: cloth, tails, hair, lanterns, weapons |
| Slow in and slow out | No linear tween for motion. Ease curves and springs |
| Arcs | Jumps, thrown things, drops, projectiles and pop-up numbers travel on arcs |
| Secondary action | Breathing, blinks, a glance at a threat, drifting dust, a flickering flame |
| Timing | Heavy is slow, light is snappy. Durations are data, tuned per thing |
| Exaggeration | Push hits, kills and rare rewards past realistic |
| Solid drawing | One light direction and one sense of volume across every view of a thing |
| Appeal | A silhouette that reads at the game's real zoom; one accent that carries personality |

## Starting numbers

Tune each one in play. They are where to begin, not a standard.

| Beat | Start with |
|---|---|
| Hit-stop | 50 to 60 ms on contact, about 120 ms on a critical hit. Freeze the two things involved, not the game, then let them catch up with the rules |
| Screen shake | Keep a `trauma` from 0 to 1 that decays by about 1.8 a second; shake by `trauma * trauma`. A hit taken adds 0.25, a kill 0.2, the heaviest slam 0.6. A small offset, at most 0.6 degrees of roll. Never for walking |
| Hit flash | White for 60 ms, the damage colour for 100 ms, a short knockback, a squash |
| Hop or step | Squash to y 0.82 for 40 ms, stretch to 1.12, the arc, land at 0.85, settle through 1.03 |
| Attack | Lean back 80 ms, strike 60 ms, hit-stop, ease out to rest in 150 ms |
| Death | Collapse with an ease in, then dissolve. Leave something behind where it is cheap |
| Pop-up number | Scale 0 to 1.4 to 1 in 120 ms, rise on an arc, fade after 0.5 s |
| Drop and pickup | Fall on an arc, bounce, squash; fly to the player on a curve |
| Alert mark | Scale 0 to 1.3 to 1 with a small wobble |
| Something placed or built | A snap on a spring, a squash on landing, a puff, a thud |
| Sound | Every beat has one. Vary its pitch by 5 to 10 percent |

## In Godot

A tween that eases: a `Tween` is linear until told otherwise, and linear is wrong for
motion. The node's origin is at its feet. Keep the tween and `kill()` it before the next
squash, or two will fight over `scale`:

```gdscript
## Squashes to `y`, widening by 1 / sqrt(y), then settles through 1.
func squash(node: Node2D, y: float = 0.82, seconds: float = 0.04) -> Tween:
	var tween: Tween = node.create_tween()
	tween.set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(node, ^"scale", Vector2(1.0 / sqrt(y), y), seconds)
	var settle: PropertyTweener = tween.tween_property(node, ^"scale", Vector2.ONE, 0.22)
	settle.set_trans(Tween.TRANS_BACK)
	return tween
```

A damped spring for whatever follows something else. Call it once a frame and keep both
numbers. At 60 frames a second, stiffness 180 with damping 14 overshoots once, by about 12
percent (10 at 30 frames, 14 at 144), and stays within 2 percent of the target from 0.4 s on:

```gdscript
## Returns the new value in x and the new velocity in y.
static func spring_step(
	value: float,
	velocity: float,
	target: float,
	delta: float,
	stiffness: float = 180.0,
	damping: float = 14.0
) -> Vector2:
	var acceleration: float = stiffness * (target - value) - damping * velocity
	var next_velocity: float = velocity + acceleration * delta
	return Vector2(value + next_velocity * delta, next_velocity)
```

## Proving it

One screenshot holds one frame, and motion is several. A sequence takes the beats:

```json
{
  "steps": [
    {"type": "call", "action": "<your reset command>"},
    {"type": "screenshot", "filename": "hit_0_before.png"},
    {"type": "tap", "action": "<the action>"},
    {"type": "screenshot", "filename": "hit_1_windup.png"},
    {"type": "wait", "seconds": 0.14},
    {"type": "screenshot", "filename": "hit_2_contact.png"},
    {"type": "wait", "seconds": 0.5},
    {"type": "screenshot", "filename": "hit_3_settled.png"},
    {"type": "assert", "node": ".", "property": "<state the rules own>", "equals": 1}
  ]
}
```

- Time the waits to your own durations: `seconds` is game time.
- Open every picture and say what each one shows: [devtools.md](devtools.md).
- Keep each scale a plain property of a named node (`motion_scale`, `flash_scale`): a
  sequence can assert it and a test can set it.
- Prove the first limit with a test: the same inputs with motion at 0 and at 1 leave the
  rules in the same state.

## Checklist

- [ ] Which principles apply here, and is each one present?
- [ ] Anticipation before, follow-through after?
- [ ] A response to every impact: flash, stop, shake, particles, sound?
- [ ] Every intensity behind a scale, and the game readable with the scales at 0?
- [ ] Presentation only: state, saves and other players untouched?
- [ ] The beats captured, opened and described?
