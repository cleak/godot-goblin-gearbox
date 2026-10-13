class_name Game
extends Node2D
## The node side of the sample game: reads input, asks the Board, shows the result.
## Arrows or WASD move, E or Space lights the lamp under you, P or Escape pauses.

const CELL_SIZE: float = 64.0
const SLIDE_SECONDS: float = 0.12
const PULSE_SECONDS: float = 0.35
const MOVES: Dictionary[StringName, Vector2i] = {
	&"move_left": Vector2i.LEFT,
	&"move_right": Vector2i.RIGHT,
	&"move_up": Vector2i.UP,
	&"move_down": Vector2i.DOWN,
}
const LAMP_CELLS: Array[Vector2i] = [Vector2i(1, 1), Vector2i(5, 1), Vector2i(3, 3)]
const FLOOR_COLOR: Color = Color("2f3542")
const LAMP_DARK_COLOR: Color = Color("57606f")
const LAMP_LIT_COLOR: Color = Color("ffd32a")

@export var board_size: Vector2i = Vector2i(7, 5)
@export var start_cell: Vector2i = Vector2i(3, 2)
@export var player_path: NodePath
@export var status_path: NodePath

var board: Board = null
var paused: bool = false
## Game state as plain properties: tests and DevTools sequences assert on these.
## The board exists from _ready on.
var player_cell: Vector2i:
	get:
		return board.player if board != null else start_cell
var lit_count: int:
	get:
		return board.lit_count() if board != null else 0
## 1 when a lamp is toggled, easing back to 0; grows the lamp under the player.
var pulse: float = 0.0:
	set(value):
		pulse = value
		queue_redraw()

var _player: Node2D = null
var _status: Label = null
var _slide: Tween = null


func _ready() -> void:
	_player = get_node_or_null(player_path)
	_status = get_node_or_null(status_path)
	assert(_player != null and _status != null, "set player_path and status_path on Main")
	reset()


## Starts over: a new board, the player on the start cell, no lamp lit, not paused.
func reset() -> void:
	board = Board.new(board_size, start_cell, LAMP_CELLS)
	paused = false
	if _slide != null:
		_slide.kill()
	_player.position = _center_of(board.player)
	_refresh()


func _physics_process(_delta: float) -> void:
	# Polled, so a real key and an action simulated by DevTools or a test look the same.
	if Input.is_action_just_pressed(&"pause"):
		paused = not paused
		_refresh()
	if paused:
		return
	for action: StringName in MOVES:
		if Input.is_action_just_pressed(action):
			_step(MOVES[action])
	if Input.is_action_just_pressed(&"interact") and board.toggle_lamp():
		_pulse_lamp()
		_refresh()


func _draw() -> void:
	for x: int in range(board.size.x):
		for y: int in range(board.size.y):
			var corner: Vector2 = Vector2(x, y) * CELL_SIZE
			draw_rect(Rect2(corner, Vector2.ONE * CELL_SIZE).grow(-2.0), FLOOR_COLOR)
	for cell: Vector2i in board.lamp_cells():
		var radius: float = CELL_SIZE * 0.3
		if cell == board.player:
			radius += pulse * CELL_SIZE * 0.15
		var color: Color = LAMP_LIT_COLOR if board.is_lit(cell) else LAMP_DARK_COLOR
		draw_circle(_center_of(cell), radius, color)


func _step(direction: Vector2i) -> void:
	var from: Vector2 = _center_of(board.player)
	var moved: bool = board.move(direction)
	if _slide != null:
		_slide.kill()
	_slide = create_tween()
	if moved:
		# Overshoot a little, then settle on the cell.
		_slide.set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
		_slide.tween_property(_player, ^"position", _center_of(board.player), SLIDE_SECONDS)
	else:
		# Blocked by the edge: lean into it and spring back.
		var lean: Vector2 = from + Vector2(direction) * CELL_SIZE * 0.2
		_slide.tween_property(_player, ^"position", lean, SLIDE_SECONDS * 0.5)
		_slide.tween_property(_player, ^"position", from, SLIDE_SECONDS * 0.5)
	_refresh()


func _pulse_lamp() -> void:
	pulse = 1.0
	var tween: Tween = create_tween()
	tween.set_trans(Tween.TRANS_ELASTIC).set_ease(Tween.EASE_OUT)
	tween.tween_property(self, ^"pulse", 0.0, PULSE_SECONDS)


func _refresh() -> void:
	var text: String = "Lamps %d/%d   Moves %d" % [lit_count, LAMP_CELLS.size(), board.moves]
	if paused:
		text = "Paused"
	elif board.all_lit():
		text += "   All lit!"
	_status.text = text
	modulate.a = 0.35 if paused else 1.0
	queue_redraw()


func _center_of(cell: Vector2i) -> Vector2:
	return (Vector2(cell) + Vector2(0.5, 0.5)) * CELL_SIZE
