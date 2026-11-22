class_name Goblin
extends Node3D
## The player: a goblin tinker with brass goggles, ears too big for it, eyes that glow in
## the dark and a lantern on a hook. Its origin is at its feet; it looks along +Z.
##
## Every move is a hop: a crouch (anticipation), a stretch into an arc, a squash on landing
## that settles on a spring. Ears and lantern follow through. It breathes and blinks.
## Presentation only: Lanternworks moves it where the Isle says the goblin stands.

enum HopPhase { REST, CROUCH, AIR }

const SKIN: Color = Color(0.42, 0.66, 0.3)
const CROUCH_SECONDS: float = 0.04
const AIR_SECONDS: float = 0.17
const HOP_HEIGHT: float = 0.26

## 0 snaps every move; 1 plays every beat in full.
var motion_scale: float = 1.0
## True from the crouch to the landing.
var hopping: bool:
	get:
		return _phase != HopPhase.REST

var _body: Node3D = null
var _hand_lamp: Node3D = null
var _ears: Array[Node3D] = []
var _eyes: Array[Node3D] = []
var _pack_gear: Node3D = null
var _from: Vector3 = Vector3.ZERO
var _to: Vector3 = Vector3.ZERO
var _phase: HopPhase = HopPhase.REST
var _phase_time: float = 0.0
var _yaw: float = 0.0
var _yaw_target: float = 0.0
## Scale of the body in y; x and z keep its volume.
var _squash: Spring = Spring.new(Vector3.ONE)
## A push of the body away from its feet: bumps and pokes.
var _nudge: Spring = Spring.new(Vector3.ZERO, 220.0, 16.0)
var _lamp_swing: Spring = Spring.new(Vector3.ZERO, 50.0, 3.5)
var _ear_flop: Spring = Spring.new(Vector3.ZERO, 120.0, 6.0)
var _last_position: Vector3 = Vector3.ZERO
var _last_velocity: Vector3 = Vector3.ZERO
var _time: float = 0.0
var _blink_in: float = 2.0
var _random: RandomNumberGenerator = RandomNumberGenerator.new()


func _ready() -> void:
	_random.seed = 31
	_body = Node3D.new()
	_body.name = "Body"
	add_child(_body)
	_build()


## Stands at `at`, looking along `facing`, with no beat: for a reset.
func place(at: Vector3, facing: Vector2i) -> void:
	_phase = HopPhase.REST
	_from = at
	_to = at
	position = at
	_last_position = at
	_last_velocity = Vector3.ZERO
	_yaw_target = _yaw_of(facing)
	_yaw = _yaw_target
	rotation.y = _yaw
	_squash.snap(Vector3.ONE)
	_nudge.snap(Vector3.ZERO)
	_lamp_swing.snap(Vector3.ZERO)


## Hops from where it is now to `at`, turning to `facing`.
func hop_to(at: Vector3, facing: Vector2i) -> void:
	_yaw_target = _yaw_of(facing)
	_from = position
	_to = at
	if motion_scale <= 0.0:
		place(at, facing)
		return
	_phase = HopPhase.CROUCH
	_phase_time = 0.0


## Turns to `facing` and leans into a way it cannot go, then springs back.
func bump(facing: Vector2i) -> void:
	_yaw_target = _yaw_of(facing)
	var push: Vector3 = Vector3(facing.x, 0.0, facing.y)
	_nudge.velocity += push * 2.6 * motion_scale
	_squash.velocity += Vector3(0.0, -2.5, 0.0) * motion_scale
	_ear_flop.velocity += Vector3(9.0, 0.0, 0.0) * motion_scale


## Reaches towards what it faces: the poke that lights a lantern.
func poke(facing: Vector2i) -> void:
	_yaw_target = _yaw_of(facing)
	_nudge.velocity += Vector3(facing.x, 0.0, facing.y) * 1.6 * motion_scale
	_lamp_swing.velocity += Vector3(3.0, 0.0, 2.0) * motion_scale
	_squash.velocity += Vector3(0.0, 1.8, 0.0) * motion_scale


## The goblin's own lantern, in world space: the grass parts and the camera looks here.
func lamp_position() -> Vector3:
	return _hand_lamp.global_position


func _process(delta: float) -> void:
	_time += delta
	_advance_hop(delta)
	if motion_scale <= 0.0:
		_yaw = _yaw_target
	else:
		_yaw = lerp_angle(_yaw, _yaw_target, 1.0 - exp(-delta * 22.0))
	rotation.y = _yaw
	# Follow-through: what hangs swings against the body's acceleration.
	var velocity: Vector3 = (position - _last_position) / maxf(delta, 1e-4)
	var acceleration: Vector3 = (velocity - _last_velocity) / maxf(delta, 1e-4)
	_last_position = position
	_last_velocity = velocity
	var local_push: Vector3 = (global_basis.inverse() * acceleration).limit_length(40.0)
	_lamp_swing.velocity += Vector3(-local_push.z, 0.0, local_push.x) * 0.02 * delta * 60.0
	_ear_flop.velocity += Vector3(-acceleration.y * 0.01, 0.0, 0.0) * delta * 60.0
	_lamp_swing.step(delta)
	_ear_flop.step(delta)
	_squash.step(delta)
	_nudge.step(delta)
	_pose()


func _advance_hop(delta: float) -> void:
	if _phase == HopPhase.REST:
		return
	_phase_time += delta
	if _phase == HopPhase.CROUCH:
		var crouch: float = minf(_phase_time / CROUCH_SECONDS, 1.0)
		_squash.snap(Vector3(0.0, lerpf(1.0, 0.82, crouch), 0.0) + Vector3(1, 0, 1))
		if _phase_time >= CROUCH_SECONDS:
			_phase = HopPhase.AIR
			_phase_time = 0.0
		return
	var t: float = minf(_phase_time / AIR_SECONDS, 1.0)
	var climb: float = maxf(_to.y - _from.y, 0.0)
	var arc: float = (HOP_HEIGHT + climb) * 4.0 * t * (1.0 - t) * motion_scale
	var glide: float = ease(t, -1.6)
	position = _from.lerp(_to, glide) + Vector3(0.0, arc, 0.0)
	var stretch: float = lerpf(1.12, 0.96, t)
	_squash.snap(Vector3(1.0, stretch, 1.0))
	if t >= 1.0:
		_phase = HopPhase.REST
		position = _to
		# Land: squash and let the spring settle it through a little overshoot.
		_squash.snap(Vector3(1.0, 0.85, 1.0))
		_squash.target = Vector3.ONE
		_ear_flop.velocity += Vector3(-6.0, 0.0, 0.0) * motion_scale
		_lamp_swing.velocity += Vector3(2.5, 0.0, 0.0) * motion_scale


func _pose() -> void:
	var breathe: float = sin(_time * 2.3) * 0.018 * motion_scale
	var y: float = 1.0 + (_squash.value.y - 1.0) * motion_scale + breathe
	y = maxf(y, 0.5)
	var wide: float = 1.0 / sqrt(y)
	_body.scale = Vector3(wide, y, wide)
	_body.position = global_basis.inverse() * _nudge.value
	_body.rotation.x = (_nudge.value * global_basis).z * 0.6
	var swing: Vector3 = _lamp_swing.value.limit_length(0.9)
	_hand_lamp.rotation = Vector3(swing.x, 0.0, swing.z)
	var flop: float = _ear_flop.value.x * 0.25
	var twitch: float = maxf(sin(_time * 0.9) * sin(_time * 3.7), 0.0) * 0.25 * motion_scale
	_ears[0].rotation.x = flop + twitch
	_ears[1].rotation.x = flop - twitch * 0.5
	_pack_gear.rotation.z = _time * 1.5
	_blink_in -= get_process_delta_time()
	var lid: float = 1.0
	if _blink_in < 0.0:
		lid = 0.1
		if _blink_in < -0.09:
			_blink_in = _random.randf_range(1.8, 4.5)
	for eye: Node3D in _eyes:
		eye.scale.y = lid


func _yaw_of(facing: Vector2i) -> float:
	return atan2(float(facing.x), float(facing.y))


func _build() -> void:
	var skin: StandardMaterial3D = Surfaces.skin(SKIN)
	var tunic: StandardMaterial3D = Surfaces.cloth(Color(0.36, 0.2, 0.11), 0.62, 0.9)
	var trousers: StandardMaterial3D = Surfaces.cloth(Color(0.18, 0.16, 0.2), 0.95, 1.0)
	var brass: ShaderMaterial = Surfaces.metal(Surfaces.BRASS, 0.2, 0.1)
	var lens: StandardMaterial3D = Surfaces.glass(Color(0.3, 0.75, 0.8))
	var glow: StandardMaterial3D = MeshKit.material(
		Color(1.0, 0.85, 0.3), 0.4, 0.0, Color(1.0, 0.8, 0.2)
	)
	glow.emission_energy_multiplier = 3.0
	var pupil: StandardMaterial3D = MeshKit.material(Color(0.05, 0.03, 0.02), 0.3)
	for side: float in [-1.0, 1.0]:
		MeshKit.add_mesh(
			_body, MeshKit.cylinder(0.045, 0.05, 0.2, 16), trousers, Vector3(0.075 * side, 0.1, 0)
		)
		MeshKit.add_mesh(
			_body, MeshKit.sphere(0.06, 16, 8), tunic, Vector3(0.075 * side, 0.02, 0.03)
		)
	var torso: MeshInstance3D = MeshKit.add_mesh(
		_body, MeshKit.sphere(0.16, 32, 16), tunic, Vector3(0, 0.32, 0)
	)
	torso.scale = Vector3(1.0, 1.12, 0.88)
	MeshKit.add_mesh(_body, MeshKit.torus(0.13, 0.165, 32), brass, Vector3(0, 0.24, 0))
	var head: MeshInstance3D = MeshKit.add_mesh(
		_body, MeshKit.sphere(0.17, 36, 20), skin, Vector3(0, 0.58, 0.01)
	)
	head.scale = Vector3(1.08, 0.95, 1.0)
	var nose: MeshInstance3D = MeshKit.add_mesh(
		_body, MeshKit.cylinder(0.0, 0.04, 0.13, 16), skin, Vector3(0, 0.54, 0.2)
	)
	nose.rotation.x = PI * 0.5 + 0.25
	for side: float in [-1.0, 1.0]:
		var ear: Node3D = Node3D.new()
		_body.add_child(ear)
		ear.position = Vector3(0.15 * side, 0.62, -0.01)
		ear.rotation.z = -side * deg_to_rad(72.0)
		var flap: MeshInstance3D = MeshKit.add_mesh(
			ear, MeshKit.cylinder(0.0, 0.065, 0.32, 16), skin, Vector3(0, 0.15, 0)
		)
		flap.scale = Vector3(1.0, 1.0, 0.45)
		_ears.append(ear)
		var eye: Node3D = Node3D.new()
		_body.add_child(eye)
		eye.position = Vector3(0.068 * side, 0.6, 0.142)
		MeshKit.add_mesh(eye, MeshKit.sphere(0.045, 24, 12), glow)
		MeshKit.add_mesh(eye, MeshKit.sphere(0.02, 16, 8), pupil, Vector3(0, 0, 0.035))
		_eyes.append(eye)
		var goggle: MeshInstance3D = MeshKit.add_mesh(
			_body, MeshKit.cylinder(0.05, 0.05, 0.04, 24), brass, Vector3(0.07 * side, 0.7, 0.13)
		)
		goggle.rotation.x = PI * 0.5 - 0.5
		var glass: MeshInstance3D = MeshKit.add_mesh(
			_body,
			MeshKit.cylinder(0.038, 0.038, 0.045, 24),
			lens,
			Vector3(0.07 * side, 0.705, 0.14)
		)
		glass.rotation.x = PI * 0.5 - 0.5
		var arm: MeshInstance3D = MeshKit.add_mesh(
			_body, MeshKit.cylinder(0.03, 0.04, 0.22, 16), skin, Vector3(0.18 * side, 0.3, 0.03)
		)
		arm.rotation = Vector3(-0.5 if side > 0.0 else 0.1, 0.0, side * 0.35)
	var band: MeshInstance3D = MeshKit.add_mesh(
		_body, MeshKit.torus(0.165, 0.185, 40), tunic, Vector3(0, 0.68, 0.0)
	)
	band.rotation.x = -0.35
	# A backpack with a little gear that never stops turning.
	MeshKit.add_mesh(_body, MeshKit.box(Vector3(0.2, 0.22, 0.1)), tunic, Vector3(0, 0.34, -0.17))
	_pack_gear = Node3D.new()
	_body.add_child(_pack_gear)
	_pack_gear.position = Vector3(0, 0.38, -0.235)
	MeshKit.add_mesh(_pack_gear, MeshKit.gear_rim(8, 0.07, 0.025, 0.025, 0.025), brass)
	MeshKit.gear_hub(_pack_gear, 0.07, 0.025, 3, brass)
	_build_hand_lamp(brass)


func _build_hand_lamp(brass: Material) -> void:
	_hand_lamp = Node3D.new()
	_hand_lamp.name = "HandLamp"
	_body.add_child(_hand_lamp)
	_hand_lamp.position = Vector3(0.24, 0.24, 0.12)
	var flame: StandardMaterial3D = MeshKit.material(
		Color(1.0, 0.7, 0.35), 0.2, 0.0, Color(1.0, 0.6, 0.25)
	)
	flame.emission_energy_multiplier = 4.0
	MeshKit.add_mesh(
		_hand_lamp, MeshKit.cylinder(0.006, 0.006, 0.07, 12), brass, Vector3(0, -0.035, 0)
	)
	MeshKit.add_mesh(
		_hand_lamp, MeshKit.cylinder(0.01, 0.045, 0.035, 16), brass, Vector3(0, -0.08, 0)
	)
	MeshKit.add_mesh(_hand_lamp, MeshKit.sphere(0.038, 20, 12), flame, Vector3(0, -0.12, 0))
	var light: OmniLight3D = OmniLight3D.new()
	light.light_color = Color(1.0, 0.68, 0.38)
	light.light_energy = 1.3
	light.omni_range = 3.4
	light.omni_attenuation = 1.2
	light.light_volumetric_fog_energy = 1.5
	_hand_lamp.add_child(light)
	light.position = Vector3(0, -0.12, 0.05)
