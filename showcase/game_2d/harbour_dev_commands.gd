extends Node
## The 2D showcase's own DevTools commands while its scene lives:
##
##     uv run python tools/devtools.py call quay      # the quay as data
##     uv run python tools/devtools.py call reset     # start over
##
## A sequence runs them too: {"type": "call", "action": "reset"}. The DevTools autoload is
## looked up, not assumed: the game also runs with the Agent DevTools plugin disabled.

const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const QUAY_COMMAND: String = "quay"
const RESET_COMMAND: String = "reset"

var _game: Harbour = null
var _dev_tools: DevToolsScript = null


func _ready() -> void:
	_game = owner  # the scene root
	assert(_game != null, "DevCommands belongs in a scene whose root is Harbour")
	_dev_tools = get_node_or_null(^"/root/DevTools")
	if _dev_tools != null:
		_dev_tools.register_handler(QUAY_COMMAND, _on_quay_command)
		_dev_tools.register_handler(RESET_COMMAND, _on_reset_command)


func _exit_tree() -> void:
	if _dev_tools != null:
		_dev_tools.unregister_handler(QUAY_COMMAND, _on_quay_command)
		_dev_tools.unregister_handler(RESET_COMMAND, _on_reset_command)


func _on_quay_command(_args: Dictionary) -> Dictionary:
	var quay: Quay = _game.quay
	var lamps: Dictionary[String, bool] = {}
	for stop: int in quay.lamp_stops():
		lamps[str(stop)] = quay.is_lit(stop)
	return {
		"success": true,
		"message": "%d of %d lamps lit" % [quay.lit_count(), quay.lamp_count()],
		"data":
		{
			"length": quay.length,
			"player": quay.player,
			"facing": quay.facing,
			"steps": quay.moves,
			"awake": quay.is_awake(),
			"lamps": lamps,
		},
	}


## Lets a sequence start from a known state, however the game was left.
func _on_reset_command(_args: Dictionary) -> Dictionary:
	_game.reset()
	return {"success": true, "message": "Quay reset", "data": {"player": _game.player_stop}}
