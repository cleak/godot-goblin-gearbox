class_name Spark2D
extends Node2D
## A spark that leaps from a lit lamp across the water to the lighthouse, on an arc, with a
## fading trail; it signals `landed` when it arrives. With motion off it lands at once.
## Presentation only: the lighthouse's state comes from the rules, not from the spark.

signal landed

const FLIGHT_SECONDS: float = 1.1
const COLOUR: Color = Color(1.0, 0.72, 0.35)
const TRAIL_POINTS: int = 18

var _from: Vector2 = Vector2.ZERO
## Where it flies to, asked each frame: the target drifts with the parallax.
var _to: Callable = Callable()
var _time: float = 0.0
var _trail: Line2D = null
var _flying: bool = false


## Flies from `from` to wherever `to` (a Callable returning a Vector2) says. Connect
## `landed` first: with motion off it lands inside this call.
func launch(from: Vector2, to: Callable, motion_scale: float) -> void:
	_from = from
	_to = to
	position = from
	if motion_scale <= 0.0:
		landed.emit()
		queue_free()
		return
	_flying = true
	Paint.glow(self, Vector2.ZERO, 70.0, Color(COLOUR * 3.0, 1.0))
	Paint.glow(self, Vector2.ZERO, 22.0, Color(4.0, 3.6, 3.0, 1.0))
	_trail = Line2D.new()
	_trail.top_level = true
	_trail.width = 5.0
	_trail.material = Paint.additive()
	var fade: Gradient = Gradient.new()
	fade.set_color(0, Color(COLOUR * 2.0, 0.0))
	fade.set_color(1, Color(COLOUR * 3.0, 1.0))
	_trail.gradient = fade
	var taper: Curve = Curve.new()
	taper.add_point(Vector2(0.0, 0.1))
	taper.add_point(Vector2(1.0, 1.0))
	_trail.width_curve = taper
	add_child(_trail)


func _process(delta: float) -> void:
	if not _flying:
		return
	_time += delta
	var t: float = clampf(_time / FLIGHT_SECONDS, 0.0, 1.0)
	var to: Vector2 = _to.call()
	position = _arc(_from, to, t * t * (3.0 - 2.0 * t))
	_trail.add_point(global_position)
	while _trail.get_point_count() > TRAIL_POINTS:
		_trail.remove_point(0)
	if t >= 1.0:
		_flying = false
		landed.emit()
		queue_free()


## A high arc from `a` to `b`: up over the water and down into the lantern room.
static func _arc(a: Vector2, b: Vector2, t: float) -> Vector2:
	var top: Vector2 = (a + b) * 0.5 + Vector2(0.0, -220.0 - absf(b.x - a.x) * 0.15)
	return a.lerp(top, t).lerp(top.lerp(b, t), t)
