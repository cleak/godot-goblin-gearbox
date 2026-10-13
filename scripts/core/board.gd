class_name Board
extends RefCounted
## The rules of the sample game: a grid, a player cell, and lamps to light.
## Pure logic with no nodes, so test/unit/test_board.gd runs it in milliseconds.

var size: Vector2i = Vector2i.ONE
var player: Vector2i = Vector2i.ZERO
var moves: int = 0
## Lamp cell -> lit.
var _lamps: Dictionary[Vector2i, bool] = {}


## Cells outside the board are clamped (the start) or dropped (lamps).
func _init(board_size: Vector2i, start: Vector2i, lamps: Array[Vector2i]) -> void:
	assert(board_size.x > 0 and board_size.y > 0, "a board needs at least one cell")
	size = board_size
	player = start.clamp(Vector2i.ZERO, size - Vector2i.ONE)
	for cell: Vector2i in lamps:
		if contains(cell):
			_lamps[cell] = false


func contains(cell: Vector2i) -> bool:
	return Rect2i(Vector2i.ZERO, size).has_point(cell)


## Moves the player one step; false when the edge of the board blocks it.
func move(direction: Vector2i) -> bool:
	var target: Vector2i = player + direction
	if not contains(target):
		return false
	player = target
	moves += 1
	return true


## Toggles the lamp under the player; false when there is none.
func toggle_lamp() -> bool:
	if not _lamps.has(player):
		return false
	_lamps[player] = not _lamps[player]
	return true


func lamp_cells() -> Array[Vector2i]:
	return _lamps.keys()


func is_lit(cell: Vector2i) -> bool:
	return _lamps.get(cell, false)


func lit_count() -> int:
	return _lamps.values().count(true)


func all_lit() -> bool:
	return not _lamps.is_empty() and lit_count() == _lamps.size()
