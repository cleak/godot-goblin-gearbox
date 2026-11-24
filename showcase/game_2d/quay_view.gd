class_name QuayView
extends Node2D
## The quay of the 2D showcase: a cobbled walk, its coping and the harbour wall below with
## its tide line and weed, and what a working quay has on it: bollards, crates, barrels, a
## lobster pot, rope, mooring rings, a ladder down to the water. Everything on it casts the
## lamps' shadows. Below the wall, the harbour water breaks each lamp's light into a
## shimmering column. Presentation only.

const SEA_SHADER: Shader = preload("res://showcase/shaders/sea_2d.gdshader")
const LEFT: float = -320.0
const RIGHT: float = 2240.0
const STREET_Y: float = 806.0
const EDGE_Y: float = 850.0
const WALL_BOTTOM: float = 1030.0
const WATER_Y: float = 1004.0
const TIDE_Y: float = 968.0
const WOOD: Color = Color(0.42, 0.28, 0.16)
const WOOD_DARK: Color = Color(0.25, 0.16, 0.09)
const IRON: Color = Color(0.12, 0.12, 0.14)
const WARM: Color = Color(1.0, 0.62, 0.3)

var _water: ShaderMaterial = null


## Builds the quay; the harbour water goes under `glow_parent`, on the glow layer.
func build(glow_parent: Node2D) -> void:
	var cobbles: ShaderMaterial = Paint.masonry(3, 0.9)
	var ashlar: ShaderMaterial = Paint.masonry(2, 0.7, TIDE_Y)
	Paint.polygon(
		self,
		Paint.box(Vector2(LEFT, STREET_Y), Vector2(RIGHT - LEFT, EDGE_Y - STREET_Y)),
		Color(0.5, 0.48, 0.47),
		cobbles
	)
	Paint.polygon(
		self,
		Paint.box(Vector2(LEFT, EDGE_Y), Vector2(RIGHT - LEFT, 12.0)),
		Color(0.7, 0.66, 0.6),
		Paint.masonry(2, 0.8)
	)
	Paint.polygon(
		self,
		Paint.box(Vector2(LEFT, EDGE_Y + 12.0), Vector2(RIGHT - LEFT, WALL_BOTTOM - EDGE_Y - 12.0)),
		Color(0.55, 0.52, 0.5),
		ashlar
	)
	# A shadow under the coping's lip.
	Paint.polygon(
		self,
		Paint.box(Vector2(LEFT, EDGE_Y + 12.0), Vector2(RIGHT - LEFT, 5.0)),
		Color(0.0, 0.0, 0.0, 0.45)
	)
	for x: float in [100.0, 520.0, 1180.0, 1510.0, 1880.0]:
		_mooring_ring(Vector2(x, EDGE_Y + 44.0))
	_ladder(860.0)
	_ladder(1735.0)
	# Props along the back of the quay, clear of the stops.
	_crates(Vector2(-60.0, STREET_Y + 6.0), 2)
	_barrel(Vector2(130.0, STREET_Y + 8.0))
	_lobster_pot(Vector2(470.0, STREET_Y + 8.0))
	_crates(Vector2(700.0, STREET_Y + 6.0), 1)
	_barrel(Vector2(1090.0, STREET_Y + 8.0))
	_barrel(Vector2(1122.0, STREET_Y + 10.0))
	_rope(Vector2(1470.0, STREET_Y + 14.0))
	_crates(Vector2(1830.0, STREET_Y + 6.0), 3)
	# Bollards on the front edge.
	for x: float in [40.0, 790.0, 1240.0, 1570.0, 2000.0]:
		_bollard(Vector2(x, EDGE_Y + 2.0))
	_water = Paint.shader(SEA_SHADER)
	_water.set_shader_parameter(&"near", 1.0)
	_water.set_shader_parameter(&"top", WATER_Y)
	_water.set_shader_parameter(&"span", 120.0)
	_water.set_shader_parameter(&"surface_color", Color(0.05, 0.07, 0.11))
	_water.set_shader_parameter(&"deep_color", Color(0.01, 0.015, 0.03))
	var colours: PackedColorArray = PackedColorArray()
	colours.resize(6)
	colours.fill(WARM)
	_water.set_shader_parameter(&"glint_colors", colours)
	var water: Polygon2D = Paint.polygon(
		glow_parent,
		Paint.box(Vector2(LEFT, WATER_Y), Vector2(RIGHT - LEFT, 260.0)),
		Color.WHITE,
		_water
	)
	water.name = "Harbour"
	set_glints([], 1.0)


## The lights the harbour water reflects: (x, strength) each, at most six; and the rain.
func set_glints(glints: Array[Vector2], rain: float) -> void:
	var packed: PackedVector2Array = PackedVector2Array()
	packed.resize(6)
	for i: int in range(mini(glints.size(), 6)):
		packed[i] = glints[i]
	_water.set_shader_parameter(&"glints", packed)
	_water.set_shader_parameter(&"rain", rain)


func _bollard(foot: Vector2) -> void:
	var shape: PackedVector2Array = PackedVector2Array(
		[
			foot + Vector2(-12.0, 0.0),
			foot + Vector2(-10.0, -18.0),
			foot + Vector2(-14.0, -26.0),
			foot + Vector2(-13.0, -32.0),
			foot + Vector2(13.0, -32.0),
			foot + Vector2(14.0, -26.0),
			foot + Vector2(10.0, -18.0),
			foot + Vector2(12.0, 0.0),
		]
	)
	Paint.polygon(self, shape, IRON)
	Paint.polygon(
		self, Paint.box(foot + Vector2(-8.0, -30.0), Vector2(4.0, 28.0)), Color(0.35, 0.34, 0.38)
	)
	Paint.occluder(self, shape)


func _crates(foot: Vector2, count: int) -> void:
	var size: float = 46.0
	for i: int in range(count):
		var corner: Vector2 = foot + Vector2(i * (size + 2.0), -size)
		if i == 2:
			corner = foot + Vector2(size * 0.5, -size * 2.0 - 2.0)
		var box: PackedVector2Array = Paint.box(corner, Vector2(size, size))
		Paint.polygon(self, box, WOOD)
		for plank: int in range(1, 4):
			Paint.polygon(
				self,
				Paint.box(corner + Vector2(0.0, plank * size / 4.0 - 1.0), Vector2(size, 2.0)),
				WOOD_DARK
			)
		Paint.polygon(self, Paint.box(corner, Vector2(size, 5.0)), WOOD_DARK)
		Paint.polygon(
			self, Paint.box(corner + Vector2(0.0, size - 5.0), Vector2(size, 5.0)), WOOD_DARK
		)
		Paint.polygon(
			self,
			PackedVector2Array(
				[
					corner + Vector2(4.0, 5.0),
					corner + Vector2(9.0, 5.0),
					corner + Vector2(size - 4.0, size - 5.0),
					corner + Vector2(size - 9.0, size - 5.0)
				]
			),
			WOOD_DARK
		)
		Paint.occluder(self, box)


func _barrel(foot: Vector2) -> void:
	var shape: PackedVector2Array = PackedVector2Array(
		[
			foot + Vector2(-15.0, 0.0),
			foot + Vector2(-18.0, -22.0),
			foot + Vector2(-15.0, -44.0),
			foot + Vector2(15.0, -44.0),
			foot + Vector2(18.0, -22.0),
			foot + Vector2(15.0, 0.0),
		]
	)
	Paint.polygon(self, shape, WOOD.lightened(0.05))
	for hoop: float in [-6.0, -38.0]:
		Paint.polygon(self, Paint.box(foot + Vector2(-17.0, hoop - 2.0), Vector2(34.0, 4.0)), IRON)
	Paint.polygon(
		self, Paint.box(foot + Vector2(-9.0, -42.0), Vector2(3.0, 40.0)), WOOD.lightened(0.25)
	)
	Paint.occluder(self, shape)


func _lobster_pot(foot: Vector2) -> void:
	var shape: PackedVector2Array = PackedVector2Array()
	for step: int in range(13):
		var angle: float = PI + step * PI / 12.0
		shape.append(foot + Vector2(cos(angle) * 30.0, sin(angle) * 30.0 - 2.0))
	Paint.polygon(self, shape, WOOD_DARK)
	# The net: bars across, so the light comes through in stripes.
	for bar: int in range(-2, 3):
		var x: float = bar * 10.0
		var bar_shape: PackedVector2Array = Paint.box(
			foot + Vector2(x - 1.5, -30.0), Vector2(3.0, 28.0)
		)
		Paint.polygon(self, bar_shape, WOOD)
		Paint.occluder(self, bar_shape)
	Paint.polygon(self, Paint.box(foot + Vector2(-31.0, -4.0), Vector2(62.0, 4.0)), WOOD)


func _rope(foot: Vector2) -> void:
	for turn: int in range(4):
		var radii: Vector2 = Vector2(30.0 - turn * 5.0, 9.0 - turn * 1.2)
		Paint.polygon(
			self,
			Paint.ellipse(foot + Vector2(0.0, -turn * 6.0), radii, 20),
			Color(0.55, 0.45, 0.3).darkened(turn * 0.08)
		)
	Paint.occluder(self, Paint.ellipse(foot + Vector2(0.0, -8.0), Vector2(28.0, 12.0), 12))


func _mooring_ring(at: Vector2) -> void:
	Paint.polygon(self, Paint.box(at + Vector2(-7.0, -6.0), Vector2(14.0, 8.0)), IRON)
	var ring: PackedVector2Array = PackedVector2Array()
	for step: int in range(17):
		ring.append(at + Vector2.from_angle(PI * step / 16.0) * 12.0)
	for step: int in range(16, -1, -1):
		ring.append(at + Vector2.from_angle(PI * step / 16.0) * 9.0)
	Paint.polygon(self, ring, IRON)


func _ladder(x: float) -> void:
	for rail: float in [-14.0, 14.0]:
		Paint.polygon(
			self,
			Paint.box(
				Vector2(x + rail - 2.0, EDGE_Y - 10.0), Vector2(4.0, WALL_BOTTOM - EDGE_Y + 10.0)
			),
			IRON
		)
	for rung: int in range(8):
		Paint.polygon(
			self,
			Paint.box(Vector2(x - 14.0, EDGE_Y + 14.0 + rung * 22.0), Vector2(28.0, 3.0)),
			IRON
		)
