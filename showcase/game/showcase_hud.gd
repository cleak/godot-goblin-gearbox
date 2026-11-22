class_name ShowcaseHud
extends CanvasLayer
## The showcase has no heads-up display: the world says everything. Progress is the
## gearbox's four bulbs and its pressure gauge, the lantern to light is the one whose rune
## glows, the payoff is the beam and the aurora.
##
## This layer draws only the pause veil: the edges darken, and three hairline sliders
## (motion, flash, shake) rise from the bottom, each marked by a drawn glyph, not a word.

const IVORY: Color = Color(0.96, 0.92, 0.84)
const TRACK_WIDTH: float = 168.0
const ROW_GAP: float = 34.0
const FADE_SECONDS: float = 0.25

## True while the pause veil is up; tests and sequences read it.
var menu_open: bool = false

var _veil: TextureRect = null
var _sliders: Control = null
var _index: int = 0
var _values: Array[float] = [1.0, 1.0, 1.0]
var _fade: Tween = null


func _ready() -> void:
	var root: Control = Control.new()
	root.name = "Root"
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)
	_veil = TextureRect.new()
	_veil.name = "Veil"
	_veil.texture = _vignette()
	_veil.stretch_mode = TextureRect.STRETCH_SCALE
	_veil.set_anchors_preset(Control.PRESET_FULL_RECT)
	_veil.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_veil.modulate.a = 0.0
	root.add_child(_veil)
	# A soft dark band rising from the bottom, for the sliders to sit on.
	var floor_shade: TextureRect = TextureRect.new()
	floor_shade.name = "Shade"
	floor_shade.texture = _floor_shade()
	floor_shade.stretch_mode = TextureRect.STRETCH_SCALE
	floor_shade.set_anchors_preset(Control.PRESET_FULL_RECT)
	floor_shade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_veil.add_child(floor_shade)
	_sliders = Control.new()
	_sliders.name = "Menu"
	_sliders.set_anchors_preset(Control.PRESET_FULL_RECT)
	_sliders.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_sliders.draw.connect(_draw_sliders)
	_sliders.modulate.a = 0.0
	_sliders.visible = false
	root.add_child(_sliders)


func show_menu(open: bool, index: int, values: Array[float]) -> void:
	_index = index
	_values = values.duplicate()
	_sliders.queue_redraw()
	if open == menu_open:
		return
	menu_open = open
	if _fade != null:
		_fade.kill()
	_sliders.visible = true
	_fade = create_tween().set_parallel(true).set_trans(Tween.TRANS_SINE)
	var target: float = 1.0 if open else 0.0
	_fade.tween_property(_veil, ^"modulate:a", target, FADE_SECONDS)
	_fade.tween_property(_sliders, ^"modulate:a", target, FADE_SECONDS)
	if not open:
		_fade.chain().tween_callback(_sliders.hide)


func _draw_sliders() -> void:
	var view: Vector2 = _sliders.size
	var left: float = view.x * 0.5 - TRACK_WIDTH * 0.5
	var first: float = view.y - 64.0 - ROW_GAP * (_values.size() - 1)
	for row: int in range(_values.size()):
		var y: float = first + row * ROW_GAP
		var chosen: bool = row == _index
		var ink: Color = Color(IVORY, 1.0 if chosen else 0.42)
		_draw_glyph(row, Vector2(left - 32.0, y), ink)
		var end: float = left + TRACK_WIDTH * clampf(_values[row], 0.0, 1.0)
		var track: Color = Color(IVORY, 0.18)
		_sliders.draw_line(Vector2(left, y), Vector2(left + TRACK_WIDTH, y), track, 1.0, true)
		_sliders.draw_line(Vector2(left, y), Vector2(end, y), ink, 1.5, true)
		if chosen:
			_sliders.draw_circle(Vector2(end, y), 9.0, Color(IVORY, 0.12))
		_sliders.draw_circle(Vector2(end, y), 4.0 if chosen else 3.0, ink)


## Motion: a curved arrow. Flash: a small sun. Shake: a zigzag.
func _draw_glyph(kind: int, at: Vector2, ink: Color) -> void:
	match kind:
		0:
			_sliders.draw_arc(at, 8.0, PI * 0.95, PI * 2.05, 20, ink, 1.6, true)
			var tip: Vector2 = at + Vector2.from_angle(PI * 2.05) * 8.0
			_sliders.draw_line(tip, tip + Vector2(-5.0, -1.0), ink, 1.6, true)
			_sliders.draw_line(tip, tip + Vector2(0.5, -5.0), ink, 1.6, true)
		1:
			_sliders.draw_circle(at, 3.2, ink)
			for ray: int in range(8):
				var dir: Vector2 = Vector2.from_angle(ray * TAU / 8.0)
				_sliders.draw_line(at + dir * 5.5, at + dir * 9.0, ink, 1.4, true)
		_:
			var points: PackedVector2Array = PackedVector2Array()
			for k: int in range(6):
				points.append(at + Vector2(-9.0 + k * 3.6, -4.0 if k % 2 == 0 else 4.0))
			_sliders.draw_polyline(points, ink, 1.6, true)


## Clear above, deepening towards the bottom edge.
static func _floor_shade() -> GradientTexture2D:
	var gradient: Gradient = Gradient.new()
	gradient.set_color(0, Color(0.0, 0.0, 0.0, 0.0))
	gradient.set_color(1, Color(0.01, 0.01, 0.025, 0.85))
	gradient.add_point(0.6, Color(0.0, 0.0, 0.0, 0.0))
	var out: GradientTexture2D = GradientTexture2D.new()
	out.gradient = gradient
	out.fill_from = Vector2(0.5, 0.0)
	out.fill_to = Vector2(0.5, 1.0)
	out.width = 4
	out.height = 256
	return out


## Dark at the edges, clear in the middle.
static func _vignette() -> GradientTexture2D:
	var gradient: Gradient = Gradient.new()
	gradient.set_color(0, Color(0.0, 0.0, 0.0, 0.0))
	gradient.set_color(1, Color(0.01, 0.01, 0.03, 0.78))
	gradient.add_point(0.55, Color(0.0, 0.0, 0.0, 0.12))
	var out: GradientTexture2D = GradientTexture2D.new()
	out.gradient = gradient
	out.fill = GradientTexture2D.FILL_RADIAL
	out.fill_from = Vector2(0.5, 0.5)
	out.fill_to = Vector2(1.05, 1.05)
	out.width = 256
	out.height = 256
	return out
