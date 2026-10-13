extends GdUnitTestSuite
## Simulated input: who sees it, sequences, asserts, waits, and what a failure cleans up.

const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
## Action names as a client sends them: JSON text.
const ACTION: String = "interact"
const OTHER_ACTION: String = "pause"
## Physics ticks a test waits for a sequence before it gives up (10 seconds).
const SEQUENCE_TICKS_MAX: int = 600


## Counts what each style of game code sees of ACTION.
class Probe:
	extends Node
	var polled_in_process: int = 0
	var polled_in_physics: int = 0
	var held_process_frames: int = 0
	var held_physics_ticks: int = 0
	var press_events: int = 0
	var release_events: int = 0
	var strength: float = 0.0
	var count: int = 0
	var spot: Vector2 = Vector2(3, 4)
	var ticks: int = 0

	func _process(_delta: float) -> void:
		if Input.is_action_just_pressed(ACTION):
			polled_in_process += 1
		if Input.is_action_pressed(ACTION):
			held_process_frames += 1
			strength = Input.get_action_strength(ACTION)

	func _physics_process(_delta: float) -> void:
		ticks += 1
		if Input.is_action_just_pressed(ACTION):
			polled_in_physics += 1
		if Input.is_action_pressed(ACTION):
			held_physics_ticks += 1

	func _input(event: InputEvent) -> void:
		if event.is_action_pressed(ACTION):
			press_events += 1
		elif event.is_action_released(ACTION):
			release_events += 1


## Unique per process: several test runs may share one user:// folder.
var _session: String = "gdunit_input_%d" % OS.get_process_id()
var _dev_tools: DevToolsScript = null
var _probe: Probe = null
var _probe_path: String = ""


func before_test() -> void:
	_dev_tools = auto_free(DevToolsScript.new())
	add_child(_dev_tools)
	_dev_tools.stop()
	_probe = auto_free(Probe.new())
	add_child(_probe)
	_probe_path = str(_probe.get_path())


func after_test() -> void:
	_dev_tools.stop()
	Input.action_release(ACTION)
	Input.action_release(OTHER_ACTION)
	# The mailbox folder of a test that started DevTools.
	var folder: String = OS.get_user_data_dir().path_join("devtools_" + _session)
	for part: String in ["commands", "results", ""]:
		if DirAccess.dir_exists_absolute(folder.path_join(part)):
			for file: String in DirAccess.get_files_at(folder.path_join(part)):
				DirAccess.remove_absolute(folder.path_join(part).path_join(file))
			DirAccess.remove_absolute(folder.path_join(part))


func test_a_sequence_tap_reaches_polling_and_event_code_exactly_once() -> void:
	var status: Dictionary = await _run([{"type": "tap", "action": ACTION}])
	assert_str(str(status["status"])).is_equal("passed")
	assert_int(_probe.polled_in_process).is_equal(1)
	assert_int(_probe.polled_in_physics).is_equal(1)
	assert_int(_probe.held_process_frames).is_greater_equal(1)
	assert_int(_probe.held_physics_ticks).is_greater_equal(1)
	assert_int(_probe.press_events).is_equal(1)
	assert_int(_probe.release_events).is_equal(1)
	assert_bool(Input.is_action_pressed(ACTION)).is_false()


func test_back_to_back_taps_are_separate_presses() -> void:
	var tap: Dictionary = {"type": "tap", "action": ACTION}
	var status: Dictionary = await _run([tap, tap, tap])
	assert_str(str(status["status"])).is_equal("passed")
	assert_int(_probe.polled_in_process).is_equal(3)
	assert_int(_probe.polled_in_physics).is_equal(3)
	assert_int(_probe.press_events).is_equal(3)


func test_the_tap_command_presses_then_releases_on_its_own() -> void:
	var reply: Dictionary = await _dev_tools.execute("input_tap", {"action": ACTION})
	assert_bool(reply["success"]).is_true()
	assert_bool(Input.is_action_pressed(ACTION)).is_true()
	for _tick: int in range(SEQUENCE_TICKS_MAX):
		if not Input.is_action_pressed(ACTION):
			break
		await get_tree().physics_frame
	assert_bool(Input.is_action_pressed(ACTION)).is_false()
	assert_int(_probe.polled_in_physics).is_equal(1)
	assert_int(_probe.press_events).is_equal(1)


func test_press_holds_with_strength_until_release() -> void:
	var pressed: Dictionary = await _dev_tools.execute(
		"input_press", {"action": ACTION, "strength": 0.25}
	)
	assert_that(pressed["data"]["active_inputs"]).is_equal(["interact"])
	await get_tree().physics_frame
	await get_tree().physics_frame
	assert_bool(Input.is_action_pressed(ACTION)).is_true()
	assert_float(_probe.strength).is_equal_approx(0.25, 0.001)
	var released: Dictionary = await _dev_tools.execute("input_release", {"action": ACTION})
	assert_that(released["data"]["active_inputs"]).is_equal([])
	assert_bool(Input.is_action_pressed(ACTION)).is_false()
	assert_int(_probe.release_events).is_equal(1)


func test_clear_releases_everything_held() -> void:
	await _dev_tools.execute("input_press", {"action": ACTION})
	await _dev_tools.execute("input_press", {"action": OTHER_ACTION})
	var reply: Dictionary = await _dev_tools.execute("input_clear")
	assert_that(reply["data"]["cleared_actions"]).is_equal(["interact", "pause"])
	assert_bool(Input.is_action_pressed(ACTION)).is_false()
	assert_bool(Input.is_action_pressed(OTHER_ACTION)).is_false()


func test_unknown_actions_and_bad_numbers_are_refused() -> void:
	var unknown: Dictionary = await _dev_tools.execute("input_press", {"action": "no_such"})
	assert_str(str(unknown["message"])).is_equal(
		"Unknown action: no_such. List them: devtools.py input list (MCP: input_actions)."
	)
	var missing: Dictionary = await _dev_tools.execute("input_tap")
	assert_str(str(missing["message"])).is_equal("Missing 'action' argument")
	var bad: Dictionary = await _dev_tools.execute(
		"input_press", {"action": ACTION, "strength": "hard"}
	)
	assert_str(str(bad["message"])).is_equal("'strength' must be a number")
	assert_bool(Input.is_action_pressed(ACTION)).is_false()


func test_actions_are_listed_without_the_built_in_ones_by_default() -> void:
	var listed: Dictionary = await _dev_tools.execute("input_actions")
	var everything: Dictionary = await _dev_tools.execute(
		"input_actions", {"include_builtin": true}
	)
	var names: Array = _names(listed)
	for action: String in ["move_left", "move_right", "move_up", "move_down", "interact", "pause"]:
		assert_bool(names.has(action)).is_true()
	assert_bool(names.has("ui_accept")).is_false()
	assert_bool(_names(everything).has("ui_accept")).is_true()


func test_a_malformed_sequence_does_not_start() -> void:
	var bad_sequences: Dictionary = {
		"'steps' must be a non-empty array": [],
		"Step 0: missing 'type'": [{"action": ACTION}],
		"Step 0: unknown step type 'jump'": [{"type": "jump"}],
		"Step 1: Unknown action: no_such":
		[{"type": "clear"}, {"type": "tap", "action": "no_such"}],
		"Step 0: 'wait' requires 'seconds'": [{"type": "wait"}],
		"Step 0: 'hold' requires 'seconds'": [{"type": "hold", "action": ACTION, "seconds": -1}],
		"Step 0: 'assert' requires 'node' and 'property'": [{"type": "assert", "equals": 1}],
		"Step 0: 'assert' requires one of equals, ge, le":
		[{"type": "assert", "node": ".", "property": "x"}],
		"Step 0: 'until': 'ge' takes a number":
		[{"type": "wait", "seconds": 1, "until": {"node": ".", "property": "x", "ge": "a"}}],
		"Step 1: Screenshot filename must be a basename":
		[
			{"type": "press", "action": ACTION},
			{"type": "screenshot", "filename": "../outside.png"},
		],
		"Step 0: 'call' requires 'action'": [{"type": "call"}],
		"Step 0: 'call': 'args' must be an object":
		[{"type": "call", "action": "ping", "args": [1]}],
		"Step 0: 'call' cannot start another sequence":
		[{"type": "call", "action": "input_sequence", "args": {"steps": []}}],
	}
	for expected: String in bad_sequences:
		var reply: Dictionary = await _dev_tools.execute(
			"input_sequence", {"steps": bad_sequences[expected]}
		)
		assert_bool(reply["success"]).is_false()
		assert_str(str(reply["message"])).starts_with(expected)
	var bad_timeout: Dictionary = await _dev_tools.execute(
		"input_sequence", {"steps": [{"type": "clear"}], "timeout": 0}
	)
	assert_str(str(bad_timeout["message"])).contains("'timeout'")
	assert_bool(Input.is_action_pressed(ACTION)).is_false()


func test_asserts_read_json_as_the_property_type() -> void:
	var status: Dictionary = await _run(
		[
			_assert_step("spot", "equals", {"x": 3, "y": 4}),
			_assert_step("spot", "equals", [3, 4]),
			_assert_step("spot:y", "equals", 4),
			_assert_step("spot:x", "ge", 3),
			_assert_step("spot:x", "le", 3.5),
			_assert_step("count", "equals", 0),
			_assert_step("name", "equals", str(_probe.name)),
		]
	)
	assert_str(str(status["message"])).contains("completed")
	assert_str(str(status["status"])).is_equal("passed")
	assert_that(status["completed_steps"]).is_equal(7)


func test_a_failed_assert_ends_the_sequence_and_releases_held_inputs() -> void:
	var status: Dictionary = await _run(
		[
			{"type": "press", "action": ACTION},
			_assert_step("spot", "equals", {"x": 9, "y": 4}),
			{"type": "press", "action": OTHER_ACTION},
		]
	)
	assert_str(str(status["status"])).is_equal("failed")
	assert_bool(status["success"]).is_false()
	# Steps count from 0, and the message says so: this is the second of three.
	assert_str(str(status["message"])).contains(
		"failed at step 1 of 3 (counted from 0): Assertion failed:"
	)
	assert_str(str(status["message"])).contains('spot is {"x":3.0,"y":4.0}, expected {"x":9.0')
	assert_that(status["completed_steps"]).is_equal(2)
	assert_that(status["cleared_actions"]).is_equal(["interact"])
	assert_bool(Input.is_action_pressed(ACTION)).is_false()
	assert_bool(Input.is_action_pressed(OTHER_ACTION)).is_false()


func test_an_assert_of_the_wrong_kind_fails_instead_of_raising() -> void:
	for step: Dictionary in [
		_assert_step("count", "equals", "zero"),
		_assert_step("spot", "ge", 1),
		_assert_step("no_such_property", "equals", 1),
		{"type": "assert", "node": "/root/NoSuchNode", "property": "x", "equals": 1},
	]:
		var status: Dictionary = await _run([step])
		assert_str(str(status["status"])).is_equal("failed")
		assert_str(str(status["message"])).contains("Assertion failed:")


func test_a_sequence_that_runs_out_of_time_says_so_and_releases_held_inputs() -> void:
	var status: Dictionary = await _run(
		[{"type": "press", "action": ACTION}, {"type": "wait", "seconds": 30.0}], 0.05
	)
	assert_str(str(status["status"])).is_equal("timed_out")
	assert_str(str(status["message"])).contains("timed out at step 1")
	assert_that(status["cleared_actions"]).is_equal(["interact"])
	assert_bool(Input.is_action_pressed(ACTION)).is_false()


func test_hold_spans_game_time_and_releases() -> void:
	var status: Dictionary = await _run([{"type": "hold", "action": ACTION, "seconds": 0.1}])
	assert_str(str(status["status"])).is_equal("passed")
	# 0.1 s is six physics ticks; allow for the frame the hold began in.
	assert_int(_probe.held_physics_ticks).is_greater_equal(5)
	assert_int(_probe.polled_in_physics).is_equal(1)
	assert_bool(Input.is_action_pressed(ACTION)).is_false()


func test_hold_until_releases_as_soon_as_the_condition_holds() -> void:
	var status: Dictionary = await _run(
		[
			{
				"type": "hold",
				"action": ACTION,
				"seconds": 30.0,
				"until": {"node": _probe_path, "property": "held_physics_ticks", "ge": 3},
			}
		]
	)
	assert_str(str(status["status"])).is_equal("passed")
	assert_bool(status["results"][0]["result"]["condition_met"]).is_true()
	# The condition is read once a frame, and a slow frame holds several physics ticks: the
	# frame that sees it true and the one that releases may each add a frame's worth.
	var slack: int = 2 * Engine.max_physics_steps_per_frame
	assert_int(_probe.held_physics_ticks).is_between(3, 3 + slack)
	assert_bool(Input.is_action_pressed(ACTION)).is_false()


func test_wait_until_passes_when_met_and_fails_when_never_met() -> void:
	var met: Dictionary = await _run(
		[
			{
				"type": "wait",
				"seconds": 30.0,
				"until": {"node": _probe_path, "property": "ticks", "ge": _probe.ticks + 3},
			}
		]
	)
	assert_str(str(met["status"])).is_equal("passed")
	var never: Dictionary = await _run(
		[
			{
				"type": "wait",
				"seconds": 0.05,
				"until": {"node": _probe_path, "property": "count", "equals": 99},
			}
		]
	)
	assert_str(str(never["status"])).is_equal("failed")
	assert_str(str(never["message"])).contains("Not within 0.05s").contains("count is 0")


## A sequence can run any DevTools command, so a game's own "reset" or "spawn" puts it in
## a known state before the steps that assert on it.
func test_a_call_step_runs_a_registered_command_with_its_args() -> void:
	_dev_tools.start(_session)
	_dev_tools.register_handler(
		"gdunit_set_count",
		func(args: Dictionary) -> Dictionary:
			var to: int = args.get("to", -1)
			_probe.count = to
			return {"success": true, "message": "set", "data": {"spot": _probe.spot}}
	)
	var status: Dictionary = await _run(
		[
			{"type": "call", "action": "gdunit_set_count", "args": {"to": 7}},
			_assert_step("count", "equals", 7),
			{"type": "call", "action": "gdunit_set_count"},
			_assert_step("count", "equals", -1),
		]
	)
	assert_str(str(status["status"])).is_equal("passed")
	var first: Dictionary = status["results"][0]["result"]
	assert_str(str(first["action"])).is_equal("gdunit_set_count")
	assert_str(str(first["message"])).is_equal("set")
	assert_that(first["data"]).is_equal({"spot": {"x": 3.0, "y": 4.0}})


func test_a_call_step_whose_command_fails_ends_the_sequence() -> void:
	var status: Dictionary = await _run(
		[
			{"type": "press", "action": ACTION},
			{"type": "call", "action": "no_such_command"},
			{"type": "press", "action": OTHER_ACTION},
		]
	)
	assert_str(str(status["status"])).is_equal("failed")
	var message: String = str(status["message"])
	assert_str(message).contains("failed at step 1 of 3 (counted from 0): ")
	assert_str(message).contains(
		"Command 'no_such_command' failed: Unknown command: no_such_command"
	)
	assert_that(status["cleared_actions"]).is_equal(["interact"])
	assert_bool(Input.is_action_pressed(OTHER_ACTION)).is_false()
	# set_state through a sequence meets the same gate as the command itself.
	var gated: Dictionary = await _run(
		[
			{
				"type": "call",
				"action": "set_state",
				"args": {"node_path": _probe_path, "property": "count", "value": 5},
			}
		]
	)
	assert_str(str(gated["message"])).contains("Refused: set_state needs")
	assert_int(_probe.count).is_zero()


func test_status_of_an_unknown_sequence_is_an_error() -> void:
	var reply: Dictionary = await _dev_tools.execute(
		"input_sequence_status", {"sequence_id": "nope"}
	)
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).is_equal("Unknown sequence: 'nope'")


## Starts a sequence and returns its status once it ended.
func _run(steps: Array, timeout: float = 20.0) -> Dictionary:
	var started: Dictionary = await _dev_tools.execute(
		"input_sequence", {"steps": steps, "timeout": timeout}
	)
	assert_str(str(started["message"])).contains("started")
	var sequence_id: String = started["data"]["sequence_id"]
	var status: Dictionary = {}
	for _tick: int in range(SEQUENCE_TICKS_MAX):
		var reply: Dictionary = await _dev_tools.execute(
			"input_sequence_status", {"sequence_id": sequence_id}
		)
		status = reply["data"]
		if status["status"] != "running":
			break
		await get_tree().physics_frame
	return status


func _assert_step(property: String, comparison: String, value: Variant) -> Dictionary:
	return {"type": "assert", "node": _probe_path, "property": property, comparison: value}


func _names(reply: Dictionary) -> Array:
	var actions: Array = reply["data"]["actions"]
	return actions.map(func(action: Dictionary) -> String: return action["name"])
