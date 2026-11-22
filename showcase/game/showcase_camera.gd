class_name ShowcaseCamera
extends Camera3D
## A tilt-shift camera over the diorama. It follows a focus point on a spring, drifts as if
## held by hand, and shakes by trauma squared (docs/feel.md). Never for walking.

const OFFSET: Vector3 = Vector3(0.0, 7.0, 10.8)
## The wide shot once the gearbox wakes: lower and further back, looking up into the sky.
const WIDE_OFFSET: Vector3 = Vector3(0.0, 2.9, 13.0)
const WIDE_LIFT: Vector3 = Vector3(0.0, 2.2, 0.0)
const FOV: float = 36.0
const WIDE_FOV: float = 44.0
const TRAUMA_DECAY: float = 1.8
## 0.6 degrees.
const MAX_ROLL: float = 0.0105
const MAX_SHIFT: float = 0.18

## What the camera looks at; Lanternworks moves it.
var focus: Vector3 = Vector3.ZERO
## 0 to 1; shake is trauma * trauma.
var trauma: float = 0.0
## 0 the play shot, 1 the wide shot that shows the sky; Lanternworks eases it.
var wide: float = 0.0
var shake_scale: float = 1.0
var motion_scale: float = 1.0

var _follow: Spring = Spring.new(Vector3.ZERO, 14.0, 7.5)
var _time: float = 0.0
var _noise: FastNoiseLite = FastNoiseLite.new()


func _ready() -> void:
	_noise.seed = 5
	_noise.frequency = 0.9
	near = 0.1
	far = 300.0
	var lens: CameraAttributesPractical = CameraAttributesPractical.new()
	lens.dof_blur_far_enabled = true
	lens.dof_blur_far_distance = 17.0
	lens.dof_blur_far_transition = 9.0
	lens.dof_blur_near_enabled = true
	lens.dof_blur_near_distance = 8.0
	lens.dof_blur_near_transition = 3.0
	lens.dof_blur_amount = 0.06
	attributes = lens
	snap()


func set_depth_of_field(on: bool) -> void:
	var lens: CameraAttributesPractical = attributes as CameraAttributesPractical
	lens.dof_blur_far_enabled = on
	lens.dof_blur_near_enabled = on


func add_trauma(amount: float) -> void:
	trauma = clampf(trauma + amount, 0.0, 1.0)


## Jumps to the focus with no follow: for a reset.
func snap() -> void:
	_follow.snap(focus)
	trauma = 0.0
	_place(Vector3.ZERO, 0.0)


func _process(delta: float) -> void:
	_time += delta
	trauma = maxf(trauma - TRAUMA_DECAY * delta, 0.0)
	_follow.target = focus
	if motion_scale <= 0.0:
		_follow.snap(focus)
	else:
		_follow.step(delta)
	var shake: float = trauma * trauma * shake_scale
	var jitter: Vector3 = (
		Vector3(_noise.get_noise_2d(_time * 25.0, 0.0), _noise.get_noise_2d(0.0, _time * 25.0), 0.0)
		* MAX_SHIFT
		* shake
	)
	# Hand-held drift: slow, small, and gone with motion turned down.
	var drift: Vector3 = (
		Vector3(sin(_time * 0.21) * 0.25, sin(_time * 0.17 + 1.0) * 0.12, cos(_time * 0.13) * 0.2)
		* motion_scale
	)
	_place(jitter + drift, _noise.get_noise_1d(_time * 20.0 + 50.0) * MAX_ROLL * shake)


func _place(offset: Vector3, roll: float) -> void:
	var blend: float = smoothstep(0.0, 1.0, wide)
	var target: Vector3 = _follow.value + WIDE_LIFT * blend
	fov = lerpf(FOV, WIDE_FOV, blend)
	position = _follow.value + OFFSET.lerp(WIDE_OFFSET, blend) + offset
	look_at(target + offset * 0.5, Vector3.UP)
	rotate_object_local(Vector3.BACK, roll)
