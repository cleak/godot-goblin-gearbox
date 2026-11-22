class_name Spring
extends RefCounted
## A damped spring that chases `target` (docs/feel.md). Stiffness 180 with damping 14
## overshoots once, by about 12 percent, and settles within 0.4 s. Kick it through
## `velocity`; read `value`. A float spring uses x alone.

## Steps longer than this are split, so a slow frame cannot make the spring explode.
const MAX_STEP: float = 1.0 / 120.0

var value: Vector3 = Vector3.ZERO
var velocity: Vector3 = Vector3.ZERO
var target: Vector3 = Vector3.ZERO
var stiffness: float = 180.0
var damping: float = 14.0


func _init(
	start: Vector3 = Vector3.ZERO, spring_stiffness: float = 180.0, spring_damping: float = 14.0
) -> void:
	value = start
	target = start
	stiffness = spring_stiffness
	damping = spring_damping


func step(delta: float) -> Vector3:
	var left: float = delta
	while left > 0.0:
		var dt: float = minf(left, MAX_STEP)
		var acceleration: Vector3 = stiffness * (target - value) - damping * velocity
		velocity += acceleration * dt
		value += velocity * dt
		left -= dt
	return value


## Rests at `at` at once.
func snap(at: Vector3) -> void:
	value = at
	target = at
	velocity = Vector3.ZERO
