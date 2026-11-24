class_name HarbourCamera
extends Camera2D
## The 2D showcase's camera. It fits the 1920 by 1080 stage to any window, leans a little
## after the goblin, shakes with trauma (docs/feel.md), and on `wide` 1 eases back and over
## towards the lighthouse for the payoff.

const STAGE: Vector2 = Vector2(1920.0, 1080.0)
## How far the view leans from the stage's middle towards the goblin.
const LEAN: float = 0.14
const WIDE_ZOOM: float = 0.9
const WIDE_SHIFT: Vector2 = Vector2(90.0, -60.0)
const TRAUMA_DECAY: float = 1.8
const SHAKE_PIXELS: float = 16.0
const SHAKE_ROLL: float = 0.01

## The x the camera leans towards.
var focus_x: float = STAGE.x * 0.5
## 0 the play view, 1 the wide view of the payoff.
var wide: float = 0.0
var motion_scale: float = 1.0
var shake_scale: float = 1.0

var _trauma: float = 0.0
var _follow: Spring = Spring.new(Vector3(STAGE.x * 0.5, 0.0, 0.0), 30.0, 11.0)
var _time: float = 0.0
var _shake: FastNoiseLite = FastNoiseLite.new()


func _ready() -> void:
	ignore_rotation = false
	_shake.seed = 9
	_shake.frequency = 2.0
	snap()


func add_trauma(amount: float) -> void:
	_trauma = minf(_trauma + amount, 1.0)


## Jumps to where it should be, with no easing: for a reset.
func snap() -> void:
	_follow.snap(Vector3(_lean_x(), 0.0, 0.0))
	_trauma = 0.0
	_place(0.0)


func _process(delta: float) -> void:
	_time += delta
	_trauma = maxf(_trauma - TRAUMA_DECAY * delta, 0.0)
	_follow.target = Vector3(_lean_x(), 0.0, 0.0)
	if motion_scale <= 0.0:
		_follow.snap(_follow.target)
	else:
		_follow.step(delta)
	_place(_trauma * _trauma * shake_scale)


func _lean_x() -> float:
	return STAGE.x * 0.5 + (focus_x - STAGE.x * 0.5) * LEAN


func _place(shake: float) -> void:
	var view: Vector2 = get_viewport_rect().size
	var fit: float = maxf(view.x / STAGE.x, view.y / STAGE.y)
	zoom = Vector2.ONE * fit * lerpf(1.0, WIDE_ZOOM, wide)
	position = Vector2(_follow.value.x, STAGE.y * 0.5) + WIDE_SHIFT * wide
	offset = (
		Vector2(_shake.get_noise_2d(_time * 30.0, 0.0), _shake.get_noise_2d(0.0, _time * 30.0))
		* SHAKE_PIXELS
		* shake
	)
	rotation = _shake.get_noise_2d(_time * 30.0, 50.0) * SHAKE_ROLL * shake
