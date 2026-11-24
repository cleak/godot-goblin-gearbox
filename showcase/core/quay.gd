class_name Quay
extends RefCounted
## The rules of the 2D showcase, Lamplight Quay: a goblin walks a harbour quay on a rainy
## night and lights four lamps; the fourth wakes the lighthouse. Pure logic with no nodes:
## test/showcase/test_quay.gd runs it headless.
##
## The quay is one row of stops. The goblin steps one stop left or right and turns to face
## the way it went, blocked or not. Interact lights the lamp at the goblin's stop. When
## every lamp burns, the lighthouse is awake.

## The quay the showcase plays on, a character per stop: . open quay, L a lamp (it stands
## at the back, so the goblin can stand there), S the start, s the start under a lamp.
const LAYOUT: String = ".L..L..S..L..L."
const LAMP_CODES: String = "Ls"
const START_CODES: String = "Ss"

var length: int = 0
var player: int = 0
## The way the goblin looks: -1 left, 1 right; the direction of its last step.
var facing: int = 1
var moves: int = 0
## Lamp stop -> lit, left to right.
var _lit: Dictionary[int, bool] = {}


## `layout` is a row of stops; see LAYOUT.
func _init(layout: String) -> void:
	assert(not layout.is_empty(), "a quay needs at least one stop")
	length = layout.length()
	var starts: int = 0
	for stop: int in range(length):
		var code: String = layout[stop]
		if not ".LSs".contains(code):
			push_error("Quay: bad stop %d: '%s'" % [stop, code])
			continue
		if LAMP_CODES.contains(code):
			_lit[stop] = false
		if START_CODES.contains(code):
			player = stop
			starts += 1
	if starts != 1:
		push_error("Quay: expected one start (S or s), found %d" % starts)


## The quay the showcase plays on.
static func standard() -> Quay:
	return Quay.new(LAYOUT)


## Turns to face `direction` (-1 or 1) and steps that way; false at either end.
func move(direction: int) -> bool:
	assert(direction == -1 or direction == 1, "a step is one stop left or right")
	facing = direction
	var target: int = player + direction
	if target < 0 or target >= length:
		return false
	player = target
	moves += 1
	return true


## Lights the lamp where the goblin stands; false when there is none, or it is lit.
func interact() -> bool:
	if not _lit.has(player) or _lit[player]:
		return false
	_lit[player] = true
	return true


func has_lamp(stop: int) -> bool:
	return _lit.has(stop)


func lamp_stops() -> Array[int]:
	return _lit.keys()


func is_lit(stop: int) -> bool:
	return _lit.get(stop, false)


func lit_count() -> int:
	return _lit.values().count(true)


func lamp_count() -> int:
	return _lit.size()


## The lighthouse wakes when every lamp burns.
func is_awake() -> bool:
	return not _lit.is_empty() and lit_count() == _lit.size()
