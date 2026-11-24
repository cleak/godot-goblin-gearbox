class_name HarbourBackdrop
extends Node2D
## What lies beyond the 2D showcase's quay: the open sea out to a misty horizon with the
## moon's glitter path on it, the hills behind the town, a far headland, and the lighthouse
## on its rock. It sits on the far layer, which drifts slower than the quay as the camera
## moves (parallax). Presentation only.

const SEA_SHADER: Shader = preload("res://showcase/shaders/sea_2d.gdshader")
const SILHOUETTE_SHADER: Shader = preload("res://showcase/shaders/silhouette_2d.gdshader")
const HORIZON: float = 690.0
const MOON: Color = Color(0.75, 0.82, 1.0)
const WARM: Color = Color(1.0, 0.72, 0.4)
## The lighthouse's foot.
const LIGHTHOUSE_AT: Vector2 = Vector2(1530.0, 652.0)

var lighthouse: Lighthouse = null

var _sea: ShaderMaterial = null


func build() -> void:
	_sea = Paint.shader(SEA_SHADER)
	_sea.set_shader_parameter(&"near", 0.0)
	_sea.set_shader_parameter(&"top", HORIZON)
	_sea.set_shader_parameter(&"span", 200.0)
	var colours: PackedColorArray = PackedColorArray([MOON, WARM, MOON, MOON, MOON, MOON])
	_sea.set_shader_parameter(&"glint_colors", colours)
	var sea: Polygon2D = Paint.polygon(
		self, Paint.box(Vector2(-600.0, HORIZON), Vector2(3200.0, 320.0)), Color.WHITE, _sea
	)
	sea.name = "Sea"
	_hills(
		PackedVector2Array(
			[
				Vector2(-600.0, HORIZON),
				Vector2(-600.0, 420.0),
				Vector2(-300.0, 380.0),
				Vector2(-60.0, 330.0),
				Vector2(180.0, 360.0),
				Vector2(420.0, 300.0),
				Vector2(700.0, 350.0),
				Vector2(980.0, 410.0),
				Vector2(1200.0, 520.0),
				Vector2(1330.0, 640.0),
				Vector2(1400.0, HORIZON),
			]
		),
		Color(0.035, 0.045, 0.08),
		300.0
	)
	_hills(
		PackedVector2Array(
			[
				Vector2(1880.0, HORIZON),
				Vector2(1960.0, 640.0),
				Vector2(2080.0, 600.0),
				Vector2(2250.0, 585.0),
				Vector2(2500.0, 610.0),
				Vector2(2600.0, HORIZON),
			]
		),
		Color(0.06, 0.075, 0.12),
		580.0
	)
	lighthouse = Lighthouse.new()
	lighthouse.name = "Lighthouse"
	lighthouse.position = LIGHTHOUSE_AT
	add_child(lighthouse)


## The moon's glitter at `moon_x` (this canvas) and the lighthouse's when it shines.
func set_glints(moon_x: float, moon: float, beacon: float) -> void:
	var glints: PackedVector2Array = PackedVector2Array(
		[
			Vector2(moon_x, moon),
			Vector2(LIGHTHOUSE_AT.x, beacon),
			Vector2.ZERO,
			Vector2.ZERO,
			Vector2.ZERO,
			Vector2.ZERO
		]
	)
	_sea.set_shader_parameter(&"glints", glints)


func _hills(points: PackedVector2Array, crest: Color, crest_y: float) -> void:
	var look: ShaderMaterial = Paint.shader(SILHOUETTE_SHADER)
	look.set_shader_parameter(&"crest_y", crest_y)
	look.set_shader_parameter(&"foot_y", HORIZON)
	look.set_shader_parameter(&"mist", Color(0.13, 0.16, 0.24))
	Paint.polygon(self, points, crest, look)
