class_name Goblin2D
extends Node2D
## The goblin lamplighter of the 2D showcase, drawn from polygons: boots, a rust coat with
## a brass buckle, a teal scarf, long ears, goggles up on its brow, and a brass lighter pole
## whose small flame lights the way and throws its shadow on the house fronts.
##
## A step is a hop on an arc with squash and stretch; it turns on a spring; ears and scarf
## follow through; it breathes and blinks. Presentation only: it is told where to go.
##
## The origin is between its feet. Its flame lives on the glow layer (`glow_parent`), so the
## night's ambient does not dim it.

const SKIN: Color = Color(0.45, 0.66, 0.31)
const SKIN_DARK: Color = Color(0.3, 0.48, 0.22)
const COAT: Color = Color(0.5, 0.24, 0.13)
const COAT_LIGHT: Color = Color(0.66, 0.36, 0.2)
const SCARF: Color = Color(0.13, 0.5, 0.5)
const BRASS: Color = Color(0.85, 0.65, 0.3)
const LEATHER: Color = Color(0.2, 0.12, 0.08)
const FLAME: Color = Color(1.0, 0.62, 0.3)
const HOP_SECONDS: float = 0.24
const HOP_HEIGHT: float = 26.0
const TIP: Vector2 = Vector2(0.0, -82.0)

## True while a hop is in the air.
var hopping: bool = false
## The y its feet rest on.
var ground_y: float = 0.0
var motion_scale: float = 1.0

var _body: Node2D = null
var _head: Node2D = null
var _ears: Array[Node2D] = []
var _eye: Node2D = null
var _scarf_tail: Node2D = null
var _pole: Node2D = null
var _light: PointLight2D = null
var _glow_root: Node2D = null
var _flame_glow: Sprite2D = null
var _flame: Polygon2D = null
var _turn: Spring = Spring.new(Vector3.ONE, 260.0, 22.0)
var _squash: Spring = Spring.new(Vector3.ONE, 320.0, 16.0)
var _ear_swing: Spring = Spring.new(Vector3.ZERO, 120.0, 7.0)
var _pole_swing: Spring = Spring.new(Vector3.ZERO, 160.0, 10.0)
var _hop_from: float = 0.0
var _hop_to: float = 0.0
var _hop_time: float = 0.0
var _time: float = 0.0
var _blink_at: float = 2.0
var _random: RandomNumberGenerator = RandomNumberGenerator.new()
var _flicker: FastNoiseLite = FastNoiseLite.new()


## Builds the goblin; its flame goes under `glow_parent`, on the glow layer.
func build(glow_parent: Node2D) -> void:
	_random.seed = 5
	_flicker.seed = 3
	_flicker.frequency = 3.5
	_body = Node2D.new()
	_body.name = "Body"
	add_child(_body)
	# Back ear first, so the head covers its root.
	_head = Node2D.new()
	_head.name = "Head"
	_head.position = Vector2(0.0, -64.0)
	_ears.append(_ear(Vector2(-14.0, -22.0), -1.0, SKIN_DARK))
	_build_legs()
	_build_pack()
	_build_coat()
	_body.add_child(_head)
	_build_head()
	_ears.append(_ear(Vector2(10.0, -24.0), 1.0, SKIN))
	_build_arm_and_pole()
	Paint.occluder(self, Paint.ellipse(Vector2(0.0, -48.0), Vector2(18.0, 44.0), 12), 2)
	_light = Paint.lamp_light(_pole, TIP, FLAME, 340.0)
	_light.energy = 0.9
	_light.height = 30.0
	_light.shadow_item_cull_mask = 1
	_glow_root = Node2D.new()
	_glow_root.name = "GoblinGlow"
	glow_parent.add_child(_glow_root)
	_flame_glow = Paint.glow(_glow_root, Vector2.ZERO, 110.0, Color(FLAME * 1.3, 0.75))
	_flame = Paint.polygon(
		_glow_root,
		PackedVector2Array(
			[
				Vector2(0.0, -11.0),
				Vector2(3.5, -3.0),
				Vector2(3.0, 2.5),
				Vector2(-3.0, 2.5),
				Vector2(-3.5, -3.0)
			]
		),
		FLAME * 5.0
	)


## Stands at `x` facing `facing` (-1 or 1), at rest, at once.
func place(x: float, facing: int) -> void:
	hopping = false
	position.x = x
	position.y = ground_y
	_turn.snap(Vector3.ONE * facing)
	_squash.snap(Vector3.ONE)
	_ear_swing.snap(Vector3.ZERO)
	_pole_swing.snap(Vector3.ZERO)
	_pose()


## Hops to `x`, facing `facing`.
func hop_to(x: float, facing: int) -> void:
	_turn.target = Vector3.ONE * facing
	if motion_scale <= 0.0:
		place(x, facing)
		return
	_hop_from = position.x
	_hop_to = x
	_hop_time = 0.0
	hopping = true
	# Anticipation: a crouch before the jump.
	_squash.value = Vector3(1.0, 0.8, 1.0)
	_squash.velocity = Vector3.ZERO


## Tries to go `facing` and cannot: turns, leans and bounces back.
func bump(facing: int) -> void:
	_turn.target = Vector3.ONE * facing
	if motion_scale <= 0.0:
		return
	_squash.velocity += Vector3(0.0, -3.0, 0.0) * motion_scale
	_ear_swing.velocity += Vector3(-6.0 * facing, 0.0, 0.0) * motion_scale


## Reaches up with the lighter pole.
func poke() -> void:
	if motion_scale <= 0.0:
		return
	_pole_swing.velocity += Vector3(-7.0, 0.0, 0.0) * motion_scale
	_squash.velocity += Vector3(0.0, 2.5, 0.0) * motion_scale


## Where the lighter's flame is, in this canvas.
func tip_position() -> Vector2:
	return _pole.to_global(TIP)


func _process(delta: float) -> void:
	_time += delta
	if hopping:
		_hop_time += delta
		var t: float = clampf(_hop_time / HOP_SECONDS, 0.0, 1.0)
		var eased: float = t * t * (3.0 - 2.0 * t)
		position.x = lerpf(_hop_from, _hop_to, eased)
		position.y = ground_y - sin(PI * t) * HOP_HEIGHT * motion_scale
		_squash.target = Vector3(1.0, 1.0 + 0.12 * sin(PI * t), 1.0)
		if t >= 1.0:
			hopping = false
			position.y = ground_y
			_squash.target = Vector3.ONE
			_squash.value = Vector3(1.0, 0.84, 1.0)
			_ear_swing.velocity.x += 5.0 * motion_scale
	_turn.step(delta)
	_squash.step(delta)
	# Ears and scarf lag behind the body: they swing against the hop.
	_ear_swing.target = Vector3((ground_y - position.y) * 0.02, 0.0, 0.0)
	_ear_swing.step(delta)
	_pole_swing.step(delta)
	if _time >= _blink_at:
		_blink_at = _time + _random.randf_range(2.2, 4.5)
	_pose()
	_sync_glow()


func _pose() -> void:
	if _body == null:
		return
	var breathe: float = 1.0 + 0.014 * sin(_time * 2.2) * motion_scale
	var tall: float = _squash.value.y * breathe
	_body.scale = Vector2(_turn.value.x / sqrt(maxf(tall, 0.2)), tall)
	var swing: float = _ear_swing.value.x * motion_scale
	_ears[0].rotation = -0.15 + swing + 0.05 * sin(_time * 1.7) * motion_scale
	_ears[1].rotation = 0.12 - swing * 0.8 + 0.05 * sin(_time * 1.9 + 1.0) * motion_scale
	_scarf_tail.rotation = 0.3 + swing * 1.5 + 0.12 * sin(_time * 3.1) * motion_scale
	_pole.rotation = 0.18 + _pole_swing.value.x * 0.12 + 0.03 * sin(_time * 1.4) * motion_scale
	var closing: float = _blink_at - _time
	_eye.scale.y = 0.1 if closing < 0.12 else 1.0
	_head.rotation = 0.04 * sin(_time * 1.1) * motion_scale


func _sync_glow() -> void:
	if _glow_root == null:
		return
	var flicker: float = 0.85 + 0.15 * _flicker.get_noise_1d(_time * 18.0)
	_glow_root.global_position = tip_position()
	_flame.scale = Vector2(1.0, flicker)
	_flame_glow.modulate.a = 0.6 + 0.2 * flicker
	_light.energy = 0.8 * flicker


func _ear(root: Vector2, way: float, colour: Color) -> Node2D:
	var pivot: Node2D = Node2D.new()
	pivot.position = root
	_head.add_child(pivot)
	var points: PackedVector2Array = PackedVector2Array(
		[
			Vector2(0.0, -6.0),
			Vector2(way * 18.0, -16.0),
			Vector2(way * 38.0, -30.0),
			Vector2(way * 26.0, -8.0),
			Vector2(way * 10.0, 6.0),
		]
	)
	Paint.polygon(pivot, points, colour)
	Paint.polygon(
		pivot,
		PackedVector2Array(
			[Vector2(way * 6.0, -4.0), Vector2(way * 30.0, -24.0), Vector2(way * 20.0, -6.0)]
		),
		Color(0.7, 0.45, 0.4)
	)
	return pivot


func _build_legs() -> void:
	Paint.polygon(
		_body, Paint.box(Vector2(-12.0, -24.0), Vector2(8.0, 14.0)), Color(0.2, 0.18, 0.2)
	)
	Paint.polygon(
		_body, Paint.box(Vector2(3.0, -24.0), Vector2(8.0, 14.0)), Color(0.24, 0.21, 0.24)
	)
	Paint.polygon(_body, Paint.rounded(Vector2(-16.0, -12.0), Vector2(15.0, 12.0), 5.0), LEATHER)
	Paint.polygon(_body, Paint.rounded(Vector2(1.0, -12.0), Vector2(17.0, 12.0), 5.0), LEATHER)


func _build_pack() -> void:
	Paint.polygon(_body, Paint.rounded(Vector2(-30.0, -66.0), Vector2(18.0, 30.0), 5.0), LEATHER)
	Paint.polygon(_body, Paint.ellipse(Vector2(-21.0, -54.0), Vector2(5.0, 5.0), 12), BRASS)
	Paint.polygon(
		_body, Paint.ellipse(Vector2(-21.0, -54.0), Vector2(3.2, 3.2), 12), Color(0.9, 0.88, 0.8)
	)


func _build_coat() -> void:
	var coat: PackedVector2Array = PackedVector2Array(
		[
			Vector2(-15.0, -66.0),
			Vector2(15.0, -66.0),
			Vector2(19.0, -40.0),
			Vector2(23.0, -20.0),
			Vector2(14.0, -16.0),
			Vector2(0.0, -18.0),
			Vector2(-14.0, -16.0),
			Vector2(-22.0, -20.0),
			Vector2(-18.0, -40.0),
		]
	)
	Paint.polygon(_body, coat, COAT)
	Paint.polygon(
		_body,
		PackedVector2Array(
			[Vector2(8.0, -64.0), Vector2(15.0, -64.0), Vector2(21.0, -22.0), Vector2(14.0, -19.0)]
		),
		COAT_LIGHT
	)
	Paint.polygon(_body, Paint.box(Vector2(-18.0, -38.0), Vector2(38.0, 5.0)), LEATHER)
	Paint.polygon(_body, Paint.box(Vector2(4.0, -39.0), Vector2(8.0, 7.0)), BRASS)
	for button: float in [-58.0, -48.0, -28.0]:
		Paint.polygon(_body, Paint.ellipse(Vector2(10.0, button), Vector2(1.8, 1.8), 8), BRASS)
	# The scarf, and a tail that streams behind.
	Paint.polygon(_body, Paint.rounded(Vector2(-16.0, -72.0), Vector2(32.0, 10.0), 4.0), SCARF)
	_scarf_tail = Node2D.new()
	_scarf_tail.position = Vector2(-12.0, -66.0)
	_body.add_child(_scarf_tail)
	Paint.polygon(
		_scarf_tail,
		PackedVector2Array(
			[
				Vector2(0.0, -3.0),
				Vector2(-18.0, 4.0),
				Vector2(-24.0, 14.0),
				Vector2(-14.0, 12.0),
				Vector2(2.0, 5.0)
			]
		),
		SCARF
	)


func _build_head() -> void:
	Paint.polygon(_head, Paint.ellipse(Vector2(2.0, -18.0), Vector2(23.0, 20.0), 28), SKIN)
	Paint.polygon(_head, Paint.ellipse(Vector2(0.0, -10.0), Vector2(19.0, 9.0), 20), SKIN_DARK)
	Paint.polygon(_head, Paint.ellipse(Vector2(3.0, -16.0), Vector2(20.0, 12.0), 24), SKIN)
	Paint.polygon(
		_head, Paint.ellipse(Vector2(8.0, -26.0), Vector2(11.0, 6.0), 16), Color(0.55, 0.76, 0.4)
	)
	# The nose, long and a little hooked.
	Paint.polygon(
		_head,
		PackedVector2Array(
			[Vector2(16.0, -22.0), Vector2(31.0, -14.0), Vector2(27.0, -9.0), Vector2(16.0, -11.0)]
		),
		SKIN_DARK
	)
	Paint.polygon(
		_head, Paint.ellipse(Vector2(12.0, -5.0), Vector2(6.0, 2.0), 10), Color(0.2, 0.12, 0.1)
	)
	_eye = Node2D.new()
	_eye.position = Vector2(13.0, -20.0)
	_head.add_child(_eye)
	Paint.polygon(_eye, Paint.ellipse(Vector2.ZERO, Vector2(5.5, 6.5), 16), Color(0.96, 0.94, 0.86))
	Paint.polygon(
		_eye, Paint.ellipse(Vector2(1.8, 0.5), Vector2(3.0, 3.6), 12), Color(0.08, 0.07, 0.06)
	)
	Paint.polygon(_eye, Paint.ellipse(Vector2(2.8, -1.2), Vector2(1.0, 1.0), 6), Color.WHITE)
	# Goggles up on the brow: a strap, two brass rims, blue lenses with a glint.
	Paint.polygon(_head, Paint.box(Vector2(-20.0, -36.0), Vector2(40.0, 6.0)), LEATHER)
	for at: Vector2 in [Vector2(-4.0, -34.0), Vector2(12.0, -35.0)]:
		Paint.polygon(_head, Paint.ellipse(at, Vector2(8.0, 7.0), 16), BRASS)
		Paint.polygon(_head, Paint.ellipse(at, Vector2(5.5, 4.8), 16), Color(0.25, 0.55, 0.65))
		Paint.polygon(
			_head,
			Paint.ellipse(at + Vector2(-2.0, -1.5), Vector2(1.6, 1.2), 8),
			Color(0.9, 0.97, 1.0)
		)


func _build_arm_and_pole() -> void:
	_pole = Node2D.new()
	_pole.name = "Pole"
	_pole.position = Vector2(17.0, -40.0)
	_body.add_child(_pole)
	Paint.polygon(_pole, Paint.box(Vector2(-1.8, -74.0), Vector2(3.6, 84.0)), BRASS)
	Paint.polygon(
		_pole, Paint.box(Vector2(-1.8, -74.0), Vector2(1.2, 84.0)), Color(1.0, 0.86, 0.55)
	)
	Paint.polygon(
		_pole, Paint.rounded(Vector2(-5.0, -80.0), Vector2(10.0, 9.0), 3.0), Color(0.3, 0.22, 0.12)
	)
	# The sleeve and hand that hold it.
	Paint.polygon(
		_body,
		PackedVector2Array(
			[
				Vector2(2.0, -62.0),
				Vector2(12.0, -60.0),
				Vector2(21.0, -44.0),
				Vector2(14.0, -38.0),
				Vector2(4.0, -50.0)
			]
		),
		COAT
	)
	Paint.polygon(_body, Paint.ellipse(Vector2(17.0, -40.0), Vector2(5.0, 5.0), 12), SKIN)
