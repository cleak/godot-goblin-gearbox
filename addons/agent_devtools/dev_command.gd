extends RefCounted
## DevTools command names: the `action` of a mailbox command. tools/devtools.py sends the same
## strings; tools/tests/test_devtools_commands.py pins the two sets together.
##
## Preload it (no class_name, so it adds nothing to the project's global namespace):
##
##     const DevCommand := preload("res://addons/agent_devtools/dev_command.gd")

const PING: String = "ping"
const QUIT: String = "quit"
const SCREENSHOT: String = "screenshot"
const SCENE_TREE: String = "scene_tree"
const GET_STATE: String = "get_state"
const PERFORMANCE: String = "performance"
const VALIDATE_SCENE: String = "validate_scene"
const VALIDATE_ALL_SCENES: String = "validate_all_scenes"
const INPUT_PRESS: String = "input_press"
const INPUT_RELEASE: String = "input_release"
const INPUT_TAP: String = "input_tap"
const INPUT_CLEAR: String = "input_clear"
const INPUT_ACTIONS: String = "input_actions"
const INPUT_SEQUENCE: String = "input_sequence"
const INPUT_SEQUENCE_STATUS: String = "input_sequence_status"
## Registered only when the project setting devtools/allow_mutation is true.
const SET_STATE: String = "set_state"
const RUN_METHOD: String = "run_method"


## What a command handler returns: did it work, one line for a person, data for a program.
static func reply(success: bool, message: String, data: Variant = null) -> Dictionary:
	return {"success": success, "message": message, "data": data}
