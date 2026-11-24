class_name Lighthouse
extends Node2D
## The lighthouse on its rock across the water: the 2D showcase's payoff. Each lamp lit on
## the quay sends a spark that lights one pane of its lantern room; the fourth wakes it: the
## lamp flares, the beam turns, the keeper's window lights. Presentation only.
##
## The origin is the middle of the tower's foot. The beam turns about the tower's axis: seen
## from the side it swings out to one side, shortens as it comes round, and flares when it
## points at the viewer.

const TOWER_SHADER: Shader = preload("res://showcase/shaders/tower_2d.gdshader")
const BEAM_SHADER: Shader = preload("res://showcase/shaders/beam_2d.gdshader")
const SILHOUETTE_SHADER: Shader = preload("res://showcase/shaders/silhouette_2d.gdshader")
const FOOT: float = 46.0
const CROWN: float = 30.0
const TALL: float = 330.0
const PANES: int = 4
const WARM: Color = Color(1.0, 0.7, 0.36)
const BEAM_REACH: float = 1500.0
const BEAM_SPREAD: float = 0.12
## Radians a second the lamp turns at full motion.
const TURN_SPEED: float = 0.45
const WAKE_SECONDS: float = 2.5
## Where the beam points as the lighthouse wakes (radians about the tower; 0 is out to
## the right, PI / 2 at the viewer).
const WAKE_TURN: float = 0.8
## Where the lamp sits above the foot.
const LAMP: Vector2 = Vector2(0.0, -TALL - 28.0)

var charges: int = 0
var awake: bool = false
var motion_scale: float = 1.0
var flash_scale: float = 1.0
## 0 dark to 1 fully awake, as shown: tests read it.
var wake_shown: float = 0.0

var _panes: Array[Polygon2D] = []
var _pane_shown: Array[float] = []
var _lamp: Node2D = null
var _glow: Sprite2D = null
var _flare: Sprite2D = null
var _beam: Polygon2D = null
var _beam_material: ShaderMaterial = null
var _tower_material: ShaderMaterial = null
var _cottage_window: Polygon2D = null
var _turn: float = 0.0
var _flash: float = 0.0
var _wake_tween: Tween = null


func _ready() -> void:
	_build_rock()
	_build_cottage()
	_beam_material = Paint.shader(BEAM_SHADER)
	_beam_material.set_shader_parameter(&"reach", BEAM_REACH)
	_beam_material.set_shader_parameter(&"spread", BEAM_SPREAD)
	_beam = (
		Paint
		. polygon(
			self,
			PackedVector2Array(
				[
					Vector2(0.0, -5.0),
					Vector2(BEAM_REACH, -BEAM_REACH * BEAM_SPREAD * 1.7),
					Vector2(BEAM_REACH, BEAM_REACH * BEAM_SPREAD * 1.7),
					Vector2(0.0, 5.0),
				]
			),
			Color.WHITE,
			_beam_material
		)
	)
	_beam.name = "Beam"
	_beam.position = LAMP
	_beam.visible = false
	_tower_material = Paint.shader(TOWER_SHADER)
	_tower_material.set_shader_parameter(&"foot", FOOT)
	_tower_material.set_shader_parameter(&"crown", CROWN)
	_tower_material.set_shader_parameter(&"tall", TALL)
	var tower: Polygon2D = (
		Paint
		. polygon(
			self,
			PackedVector2Array(
				[
					Vector2(-FOOT, 0.0),
					Vector2(-CROWN, -TALL),
					Vector2(CROWN, -TALL),
					Vector2(FOOT, 0.0),
				]
			),
			Color.WHITE,
			_tower_material
		)
	)
	tower.name = "Tower"
	# A door and two slit windows up the tower.
	Paint.polygon(
		self, Paint.rounded(Vector2(-9.0, -34.0), Vector2(18.0, 34.0), 9.0), Color(0.05, 0.04, 0.05)
	)
	for rise: float in [120.0, 220.0]:
		Paint.polygon(
			self,
			Paint.box(Vector2(-3.0, -rise - 16.0), Vector2(6.0, 16.0)),
			Color(0.04, 0.05, 0.07)
		)
	_build_lantern_room()


func _build_rock() -> void:
	var rock: ShaderMaterial = Paint.shader(SILHOUETTE_SHADER)
	rock.set_shader_parameter(&"crest_y", global_position.y - 40.0)
	rock.set_shader_parameter(&"foot_y", global_position.y + 60.0)
	rock.set_shader_parameter(&"mist", Color(0.1, 0.12, 0.18))
	var crag: PackedVector2Array = PackedVector2Array(
		[
			Vector2(-210.0, 60.0),
			Vector2(-170.0, 22.0),
			Vector2(-120.0, 10.0),
			Vector2(-80.0, -6.0),
			Vector2(-30.0, -4.0),
			Vector2(40.0, -2.0),
			Vector2(90.0, 6.0),
			Vector2(130.0, 2.0),
			Vector2(175.0, 26.0),
			Vector2(230.0, 60.0),
		]
	)
	Paint.polygon(self, crag, Color(0.05, 0.06, 0.09), rock)
	# A moonlit lip along the rock's top.
	var lip: PackedVector2Array = PackedVector2Array()
	for i: int in range(1, crag.size() - 1):
		lip.append(crag[i] + Vector2(0.0, -1.5))
	for i: int in range(crag.size() - 2, 0, -1):
		lip.append(crag[i] + Vector2(0.0, 3.0))
	Paint.polygon(self, lip, Color(0.2, 0.24, 0.34))


func _build_cottage() -> void:
	var at: Vector2 = Vector2(-150.0, 14.0)
	Paint.polygon(
		self, Paint.box(at + Vector2(0.0, -46.0), Vector2(74.0, 46.0)), Color(0.16, 0.17, 0.2)
	)
	Paint.polygon(
		self,
		PackedVector2Array(
			[at + Vector2(-8.0, -44.0), at + Vector2(37.0, -74.0), at + Vector2(82.0, -44.0)]
		),
		Color(0.09, 0.08, 0.1)
	)
	Paint.polygon(
		self, Paint.box(at + Vector2(52.0, -82.0), Vector2(10.0, 22.0)), Color(0.1, 0.09, 0.1)
	)
	_cottage_window = Paint.polygon(
		self, Paint.box(at + Vector2(16.0, -32.0), Vector2(16.0, 14.0)), Color(0.05, 0.06, 0.08)
	)


func _build_lantern_room() -> void:
	var iron: Color = Color(0.06, 0.06, 0.08)
	var top: float = -TALL
	Paint.polygon(
		self, Paint.box(Vector2(-CROWN - 16.0, top - 6.0), Vector2((CROWN + 16.0) * 2.0, 7.0)), iron
	)
	for post: int in range(7):
		var x: float = -CROWN - 14.0 + post * (CROWN + 14.0) * 2.0 / 6.0
		Paint.polygon(self, Paint.box(Vector2(x - 1.0, top - 20.0), Vector2(2.0, 14.0)), iron)
	Paint.polygon(
		self,
		Paint.box(Vector2(-CROWN - 15.0, top - 21.0), Vector2((CROWN + 15.0) * 2.0, 2.0)),
		iron
	)
	var room: Vector2 = Vector2(-24.0, top - 52.0)
	Paint.polygon(self, Paint.box(room, Vector2(48.0, 46.0)), Color(0.05, 0.07, 0.1))
	for i: int in range(PANES):
		var pane: Polygon2D = Paint.polygon(
			self,
			Paint.box(room + Vector2(3.0 + i * 11.0, 4.0), Vector2(9.0, 38.0)),
			Color(0.08, 0.1, 0.14)
		)
		_panes.append(pane)
		_pane_shown.append(0.0)
	(
		Paint
		. polygon(
			self,
			PackedVector2Array(
				[
					Vector2(-30.0, top - 50.0),
					Vector2(-18.0, top - 70.0),
					Vector2(0.0, top - 76.0),
					Vector2(18.0, top - 70.0),
					Vector2(30.0, top - 50.0),
				]
			),
			Color(0.3, 0.07, 0.06)
		)
	)
	Paint.polygon(self, Paint.ellipse(Vector2(0.0, top - 80.0), Vector2(4.0, 4.0), 10), iron)
	_lamp = Node2D.new()
	_lamp.name = "Lamp"
	_lamp.position = LAMP
	add_child(_lamp)
	_glow = Paint.glow(_lamp, Vector2.ZERO, 260.0, Color(WARM, 0.0))
	_flare = Paint.glow(_lamp, Vector2.ZERO, 380.0, Color(WARM, 0.0))


## A landed spark lights pane `count`.
func charge(count: int) -> void:
	charges = clampi(count, 0, PANES)
	_flash = 1.0


## The payoff: the lamp flares, the beam starts to turn, the keeper's window lights.
func awaken() -> void:
	awake = true
	charges = PANES
	_flash = 1.0
	_beam.visible = true
	# Start the turn so that, a few seconds on, the beam sweeps long over the town.
	_turn = WAKE_TURN
	if _wake_tween != null:
		_wake_tween.kill()
	_wake_tween = create_tween()
	_wake_tween.tween_property(self, ^"wake_shown", 1.0, WAKE_SECONDS).set_trans(Tween.TRANS_SINE)


## Shows `count` panes lit and the lamp awake or dark, at once: for a reset.
func show_state(count: int, is_awake: bool) -> void:
	charges = count
	awake = is_awake
	if _wake_tween != null:
		_wake_tween.kill()
	wake_shown = 1.0 if is_awake else 0.0
	_beam.visible = is_awake
	_flash = 0.0
	for i: int in range(PANES):
		_pane_shown[i] = 1.0 if i < count else 0.0


## Where sparks fly to, in this canvas.
func lamp_position() -> Vector2:
	return _lamp.global_position


func _process(delta: float) -> void:
	_flash = maxf(_flash - delta * 2.5, 0.0)
	for i: int in range(PANES):
		var target: float = 1.0 if i < charges else 0.0
		_pane_shown[i] = move_toward(_pane_shown[i], target, delta * 3.0)
		var heat: float = _pane_shown[i] * (2.2 + 1.5 * _flash * flash_scale)
		_panes[i].color = Color(0.08, 0.1, 0.14).lerp(WARM * 3.0, minf(heat / 3.0, 1.0))
		_panes[i].color = _panes[i].color * maxf(1.0, heat * 0.6)
	var lit_panes: float = 0.0
	for shown: float in _pane_shown:
		lit_panes += shown
	_turn += delta * TURN_SPEED * motion_scale
	var side: float = cos(_turn)
	var toward: float = sin(_turn)
	_beam.scale = Vector2(side, 1.0)
	_beam.z_index = 1 if toward > 0.0 else -1
	_beam_material.set_shader_parameter(&"strength", wake_shown * (0.75 + 0.25 * toward))
	var face: float = pow(maxf(toward, 0.0), 10.0) * wake_shown
	_flare.modulate = Color(WARM * 1.6, face * 0.7)
	var glow: float = lit_panes / PANES * 0.35 + wake_shown * 0.9 + _flash * flash_scale * 0.4
	_glow.modulate = Color(WARM * 1.6, minf(glow, 1.0))
	_tower_material.set_shader_parameter(&"lamp", wake_shown)
	_cottage_window.color = Color(0.05, 0.06, 0.08).lerp(WARM * 2.2, wake_shown)
