extends Node
## Adds this game's own DevTools commands while the scene lives:
##
##     python tools/devtools.py call board     # the board as data
##     python tools/devtools.py call reset     # start over
##
## A sequence runs them too: {"type": "call", "action": "reset"}.
##
## The DevTools autoload is looked up, not assumed, so the game also runs with the Agent
## DevTools plugin disabled. In a release build register_handler does nothing.

const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const BOARD_COMMAND: String = "board"
const RESET_COMMAND: String = "reset"

var _game: Game = null
var _dev_tools: DevToolsScript = null


func _ready() -> void:
	_game = owner  # the scene root
	assert(_game != null, "DevCommands belongs in a scene whose root is the Game")
	_dev_tools = get_node_or_null(^"/root/DevTools")
	if _dev_tools != null:
		_dev_tools.register_handler(BOARD_COMMAND, _on_board_command)
		_dev_tools.register_handler(RESET_COMMAND, _on_reset_command)


func _exit_tree() -> void:
	if _dev_tools != null:
		_dev_tools.unregister_handler(BOARD_COMMAND, _on_board_command)
		_dev_tools.unregister_handler(RESET_COMMAND, _on_reset_command)


func _on_board_command(_args: Dictionary) -> Dictionary:
	var board: Board = _game.board
	var lamps: Dictionary[String, bool] = {}
	for cell: Vector2i in board.lamp_cells():
		lamps["%d,%d" % [cell.x, cell.y]] = board.is_lit(cell)
	return {
		"success": true,
		"message": "%d of %d lamps lit" % [board.lit_count(), lamps.size()],
		# Vector2i goes out as {"x", "y"}: DevTools converts reply data to JSON.
		"data": {"size": board.size, "player": board.player, "moves": board.moves, "lamps": lamps},
	}


## Lets a test sequence start from a known state, however the game was left.
func _on_reset_command(_args: Dictionary) -> Dictionary:
	_game.reset()
	return {"success": true, "message": "Board reset", "data": {"player": _game.player_cell}}
