class_name Isle
extends RefCounted
## The rules of the showcase: a terraced floating island, a goblin, four lanterns and the
## gearbox they wake. Pure logic with no nodes: test/showcase/test_isle.gd runs it headless.
##
## The goblin steps one cell at a time and climbs at most one terrace. Every step turns it to
## face the way it went, blocked or not. Interact lights the lantern it faces. When every
## lantern burns, the gearbox is awake.

## What stands on a cell. Not saved anywhere; the order is still append only.
enum Feature { NONE, TREE, ROCK, LANTERN, GEARBOX, WATER }

## The island the showcase plays on. HEIGHTS: a digit per cell, the terrace it sits on;
## a space is open sky. FEATURES: what stands there (see FEATURE_CODES), S the start.
const HEIGHTS: Array[String] = [
	"  01122  ",
	" 0111233 ",
	"001112333",
	"011111133",
	"011111112",
	"001111111",
	"000111110",
	" 0001100 ",
	"  00000  ",
]
const FEATURES: Array[String] = [
	"  T...T  ",
	" L.....L ",
	"r.......T",
	"...GGG...",
	"T..GGG..r",
	"...GGG...",
	"T.......T",
	" L...S.L ",
	"  WWr..  ",
]
const FEATURE_CODES: Dictionary[String, Feature] = {
	".": Feature.NONE,
	"S": Feature.NONE,
	"T": Feature.TREE,
	"r": Feature.ROCK,
	"L": Feature.LANTERN,
	"G": Feature.GEARBOX,
	"W": Feature.WATER,
}
const START_CODE: String = "S"
## The highest terrace the goblin can climb in one step.
const MAX_CLIMB: int = 1

var size: Vector2i = Vector2i.ZERO
var player: Vector2i = Vector2i.ZERO
## The way the goblin looks: the direction of its last step, blocked or not.
var facing: Vector2i = Vector2i.UP
var moves: int = 0
## Cell -> terrace, for every cell with ground under it.
var _heights: Dictionary[Vector2i, int] = {}
var _features: Dictionary[Vector2i, Feature] = {}
## Lantern cell -> lit, in reading order.
var _lit: Dictionary[Vector2i, bool] = {}


## `heights` and `features` are rows of equal length; see HEIGHTS and FEATURES.
func _init(heights: Array[String], features: Array[String]) -> void:
	assert(not heights.is_empty(), "an isle needs at least one row")
	assert(heights.size() == features.size(), "heights and features differ in rows")
	size = Vector2i(heights[0].length(), heights.size())
	var starts: int = 0
	for y: int in range(size.y):
		assert(heights[y].length() == size.x, "height row %d is not %d long" % [y, size.x])
		assert(features[y].length() == size.x, "feature row %d is not %d long" % [y, size.x])
		for x: int in range(size.x):
			var cell: Vector2i = Vector2i(x, y)
			var digit: String = heights[y][x]
			var code: String = features[y][x]
			if digit == " ":
				continue
			if not digit.is_valid_int() or not FEATURE_CODES.has(code):
				push_error("Isle: bad cell %s: height '%s', feature '%s'" % [cell, digit, code])
				continue
			_heights[cell] = digit.to_int()
			_features[cell] = FEATURE_CODES[code]
			if _features[cell] == Feature.LANTERN:
				_lit[cell] = false
			if code == START_CODE:
				player = cell
				starts += 1
	if starts != 1:
		push_error("Isle: expected one start cell (S), found %d" % starts)


## The island the showcase plays on.
static func standard() -> Isle:
	return Isle.new(HEIGHTS, FEATURES)


func has_ground(cell: Vector2i) -> bool:
	return _heights.has(cell)


## The terrace of `cell`; -1 where there is no ground.
func height_at(cell: Vector2i) -> int:
	return _heights.get(cell, -1)


func feature_at(cell: Vector2i) -> Feature:
	return _features.get(cell, Feature.NONE)


## Every cell with ground, in reading order.
func ground_cells() -> Array[Vector2i]:
	return _heights.keys()


func cells_with(feature: Feature) -> Array[Vector2i]:
	var out: Array[Vector2i] = []
	for cell: Vector2i in _features:
		if _features[cell] == feature:
			out.append(cell)
	return out


## True when the goblin may stand on `cell`: ground with nothing on it.
func is_open(cell: Vector2i) -> bool:
	return has_ground(cell) and feature_at(cell) == Feature.NONE


## True when one step takes the goblin from `from` to `to`: open ground no more than
## MAX_CLIMB terraces up or down.
func can_step(from: Vector2i, to: Vector2i) -> bool:
	return is_open(to) and absi(height_at(to) - height_at(from)) <= MAX_CLIMB


## Turns to face `direction` and steps that way; false when the way is blocked.
func move(direction: Vector2i) -> bool:
	assert(absi(direction.x) + absi(direction.y) == 1, "a step is one cell along one axis")
	facing = direction
	var target: Vector2i = player + direction
	if not can_step(player, target):
		return false
	player = target
	moves += 1
	return true


## The cell the goblin faces.
func faced_cell() -> Vector2i:
	return player + facing


## True when the goblin faces a lantern that is still dark.
func faces_dark_lantern() -> bool:
	var cell: Vector2i = faced_cell()
	return _lit.has(cell) and not _lit[cell]


## Lights the lantern the goblin faces; false when it faces none, or one already lit.
func interact() -> bool:
	if not faces_dark_lantern():
		return false
	_lit[faced_cell()] = true
	return true


func lantern_cells() -> Array[Vector2i]:
	return _lit.keys()


func is_lit(cell: Vector2i) -> bool:
	return _lit.get(cell, false)


func lit_count() -> int:
	return _lit.values().count(true)


func lantern_count() -> int:
	return _lit.size()


## The gearbox wakes when every lantern burns.
func is_awake() -> bool:
	return not _lit.is_empty() and lit_count() == _lit.size()


## Every cell the goblin can walk to from where it stands.
func reachable() -> Dictionary[Vector2i, bool]:
	var seen: Dictionary[Vector2i, bool] = {player: true}
	var frontier: Array[Vector2i] = [player]
	while not frontier.is_empty():
		var cell: Vector2i = frontier.pop_back()
		for step: Vector2i in [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]:
			var next: Vector2i = cell + step
			if not seen.has(next) and can_step(cell, next):
				seen[next] = true
				frontier.append(next)
	return seen
