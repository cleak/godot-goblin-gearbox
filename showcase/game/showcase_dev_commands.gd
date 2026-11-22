extends Node
## The showcase's own DevTools commands while its scene lives:
##
##     uv run python tools/devtools.py call isle      # the island as data
##     uv run python tools/devtools.py call reset     # start over
##     uv run python tools/devtools.py call effects fog=false dof=false   # cost of each effect
##
## A sequence runs them too: {"type": "call", "action": "reset"}. The DevTools autoload is
## looked up, not assumed: the game also runs with the Agent DevTools plugin disabled.

const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const ISLE_COMMAND: String = "isle"
const RESET_COMMAND: String = "reset"
const EFFECTS_COMMAND: String = "effects"

var _game: Lanternworks = null
var _dev_tools: DevToolsScript = null


func _ready() -> void:
	_game = owner  # the scene root
	assert(_game != null, "DevCommands belongs in a scene whose root is Lanternworks")
	_dev_tools = get_node_or_null(^"/root/DevTools")
	if _dev_tools != null:
		_dev_tools.register_handler(ISLE_COMMAND, _on_isle_command)
		_dev_tools.register_handler(RESET_COMMAND, _on_reset_command)
		_dev_tools.register_handler(EFFECTS_COMMAND, _on_effects_command)


func _exit_tree() -> void:
	if _dev_tools != null:
		_dev_tools.unregister_handler(ISLE_COMMAND, _on_isle_command)
		_dev_tools.unregister_handler(RESET_COMMAND, _on_reset_command)
		_dev_tools.unregister_handler(EFFECTS_COMMAND, _on_effects_command)


func _on_isle_command(_args: Dictionary) -> Dictionary:
	var isle: Isle = _game.isle
	var lanterns: Dictionary[String, bool] = {}
	for cell: Vector2i in isle.lantern_cells():
		lanterns["%d,%d" % [cell.x, cell.y]] = isle.is_lit(cell)
	return {
		"success": true,
		"message": "%d of %d lanterns lit" % [isle.lit_count(), isle.lantern_count()],
		# Vector2i goes out as {"x", "y"}: DevTools converts reply data to JSON.
		"data":
		{
			"size": isle.size,
			"player": isle.player,
			"facing": isle.facing,
			"steps": isle.moves,
			"awake": isle.is_awake(),
			"lanterns": lanterns,
		},
	}


## Lets a sequence start from a known state, however the game was left.
func _on_reset_command(_args: Dictionary) -> Dictionary:
	_game.reset()
	return {"success": true, "message": "Isle reset", "data": {"player": _game.player_cell}}


## Switches effects off and on (fog, ssao, glow, shadows, dof) to measure what each costs.
func _on_effects_command(args: Dictionary) -> Dictionary:
	var switches: Dictionary[String, bool] = {}
	for key: Variant in args:
		var value: Variant = args[key]
		if not value is bool:
			return {"success": false, "message": "effects: %s must be true or false" % key}
		switches[str(key)] = value
	var known: Array[String] = _game.set_effects(switches)
	var unknown: Array[String] = []
	for key: String in switches:
		if not known.has(key):
			unknown.append(key)
	if not unknown.is_empty():
		return {"success": false, "message": "effects: unknown %s" % ", ".join(unknown)}
	return {"success": true, "message": "effects: %s" % str(switches)}
