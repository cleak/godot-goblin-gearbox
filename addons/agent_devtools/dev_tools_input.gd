extends RefCounted
## Input simulation for DevTools: single actions and scripted sequences.
##
## An action is sent as an InputEventAction through Input.parse_input_event, so both styles of
## game code see it: polling (Input.is_action_pressed, is_action_just_pressed) and events
## (_input, _unhandled_input). Sequence steps act at the start of a frame, before any node
## processes, and a tap lasts at least one whole process frame and one whole physics tick, so
## a poll in either callback cannot miss it.
##
## Sequence steps are JSON objects; "type" picks one:
##   press       {action, strength?}            hold the action down
##   release     {action}
##   tap         {action, seconds?, strength?}  press, wait, release
##   hold        {action, seconds, strength?, until?}
##                 press for `seconds`; with `until`, release as soon as it is true
##                 (`seconds` is then the longest it is held)
##   wait        {seconds, until?}
##                 with `until`, go on as soon as it is true and fail if it never is
##   assert      {node, property, equals | ge | le}   fail the sequence unless it holds
##   screenshot  {filename?}
##   clear       {}                             release every action DevTools holds
##   call        {action, args?}                run a DevTools command, such as one the game
##                 registered ("reset", "spawn"); the step fails when the command fails
##
## `until` is {node, property, equals | ge | le}, like an assert. `equals` takes any JSON
## value and reads it as the property's type (dev_tools_state.gd); `ge` and `le` compare
## numbers. `seconds` is game time (it follows Engine.time_scale); a sequence's `timeout` is
## real time. A sequence that fails or times out releases every held action.
##
## Preload it; it declares no class_name.

enum Waited { ELAPSED, CONDITION_MET, DEADLINE }

const DevCommand := preload("res://addons/agent_devtools/dev_command.gd")
const DevState := preload("res://addons/agent_devtools/dev_tools_state.gd")
const Screenshots := preload("res://addons/agent_devtools/dev_tools_screenshots.gd")

const STEP_PRESS: String = "press"
const STEP_RELEASE: String = "release"
const STEP_TAP: String = "tap"
const STEP_HOLD: String = "hold"
const STEP_WAIT: String = "wait"
const STEP_ASSERT: String = "assert"
const STEP_SCREENSHOT: String = "screenshot"
const STEP_CLEAR: String = "clear"
const STEP_CALL: String = "call"
const STEP_TYPES: PackedStringArray = [
	STEP_PRESS,
	STEP_RELEASE,
	STEP_TAP,
	STEP_HOLD,
	STEP_WAIT,
	STEP_ASSERT,
	STEP_SCREENSHOT,
	STEP_CLEAR,
	STEP_CALL,
]
const ACTION_STEPS: PackedStringArray = [STEP_PRESS, STEP_RELEASE, STEP_TAP, STEP_HOLD]
const TIMED_STEPS: PackedStringArray = [STEP_TAP, STEP_HOLD, STEP_WAIT]
const COMPARISONS: PackedStringArray = ["equals", "ge", "le"]
const STATUS_RUNNING: String = "running"
const STATUS_PASSED: String = "passed"
const STATUS_FAILED: String = "failed"
const STATUS_TIMED_OUT: String = "timed_out"
const DEFAULT_TIMEOUT_SECONDS: float = 60.0
## Finished sequences whose status is kept for input_sequence_status.
const KEPT_SEQUENCES: int = 32
## Actions with this prefix are Godot's built-in UI actions.
const BUILTIN_PREFIX: String = "ui_"


class Outcome:
	extends RefCounted
	## How one sequence step ended: `data`, an `error`, or out of time.
	var data: Dictionary = {}
	var error: String = ""
	var timed_out: bool = false


var _host: Node = null
var _screenshots: Screenshots = null
## Called with (category, message, data) for each log row.
var _log: Callable = Callable()
## Called with (action, args) for a `call` step; returns that command's reply. Await it.
var _execute: Callable = Callable()
## Actions DevTools pressed and has not released.
var _held: Array[String] = []
## Sequence id -> its status, oldest first.
var _sequences: Dictionary = {}


## `host` is a node inside the tree (the DevTools autoload).
func _init(host: Node, screenshots: Screenshots, log_event: Callable, execute: Callable) -> void:
	_host = host
	_screenshots = screenshots
	_log = log_event
	_execute = execute


## The input commands, by action name.
func handlers() -> Dictionary:
	return {
		DevCommand.INPUT_PRESS: _cmd_press,
		DevCommand.INPUT_RELEASE: _cmd_release,
		DevCommand.INPUT_TAP: _cmd_tap,
		DevCommand.INPUT_CLEAR: _cmd_clear,
		DevCommand.INPUT_ACTIONS: _cmd_actions,
		DevCommand.INPUT_SEQUENCE: _cmd_sequence,
		DevCommand.INPUT_SEQUENCE_STATUS: _cmd_sequence_status,
	}


## Releases every action DevTools holds; returns their names.
func release_all() -> Array[String]:
	var released: Array[String] = _held.duplicate()
	for action: String in released:
		_send(action, false)
		# Also covers an action the game itself pressed through the Input API meanwhile.
		Input.action_release(action)
	if not released.is_empty():
		_log.call("input", "Released all simulated inputs", {"actions": released})
	return released


func _cmd_press(args: Dictionary) -> Dictionary:
	var error: String = _action_error(args)
	if not error.is_empty():
		return DevCommand.reply(false, error)
	var action: String = args["action"]
	var strength: float = clampf(_number(args, "strength", 1.0), 0.0, 1.0)
	_send(action, true, strength)
	_log.call("input", "Pressed action: %s" % action, {"action": action, "strength": strength})
	var data: Dictionary = {
		"action": action, "strength": strength, "active_inputs": _held.duplicate()
	}
	return DevCommand.reply(true, "Pressed: %s" % action, data)


func _cmd_release(args: Dictionary) -> Dictionary:
	var error: String = _action_error(args)
	if not error.is_empty():
		return DevCommand.reply(false, error)
	var action: String = args["action"]
	_send(action, false)
	_log.call("input", "Released action: %s" % action, {"action": action})
	return DevCommand.reply(
		true, "Released: %s" % action, {"action": action, "active_inputs": _held.duplicate()}
	)


func _cmd_tap(args: Dictionary) -> Dictionary:
	var error: String = _action_error(args)
	if not error.is_empty():
		return DevCommand.reply(false, error)
	var action: String = args["action"]
	var seconds: float = maxf(0.0, _number(args, "hold_seconds", 0.0))
	var strength: float = clampf(_number(args, "strength", 1.0), 0.0, 1.0)
	var data: Dictionary = {"action": action, "hold_seconds": seconds, "strength": strength}
	_log.call("input", "Tapped action: %s" % action, data)
	# Not awaited: the reply goes out now and the release follows on a later frame.
	_tap(action, seconds, strength, Time.get_ticks_msec() + int(seconds * 1000.0) + 5000)
	return DevCommand.reply(true, "Tapped: %s" % action, data)


func _cmd_clear(_args: Dictionary) -> Dictionary:
	var released: Array[String] = release_all()
	return DevCommand.reply(
		true, "Cleared %d inputs" % released.size(), {"cleared_actions": released}
	)


func _cmd_actions(args: Dictionary) -> Dictionary:
	var include_builtin: bool = args.get("include_builtin") == true
	var listed: Array[Dictionary] = []
	for action: StringName in InputMap.get_actions():
		var name: String = action
		if name.begins_with(BUILTIN_PREFIX) and not include_builtin:
			continue
		var events: Array[String] = []
		for event: InputEvent in InputMap.action_get_events(action):
			events.append(event.as_text())
		listed.append(
			{"name": name, "events": events, "is_pressed": Input.is_action_pressed(action)}
		)
	return DevCommand.reply(true, "Found %d actions" % listed.size(), {"actions": listed})


func _cmd_sequence(args: Dictionary) -> Dictionary:
	var raw_steps: Array = []
	if args.get("steps") is Array:
		raw_steps = args["steps"]
	if raw_steps.is_empty():
		return DevCommand.reply(false, "'steps' must be a non-empty array")
	var steps: Array[Dictionary] = []
	for raw_step: Variant in raw_steps:
		var step: Dictionary = _parse_step(raw_step, steps.size())
		if step.has("error"):
			return DevCommand.reply(false, str(step["error"]))
		steps.append(step)
	var timeout: float = _number(args, "timeout", DEFAULT_TIMEOUT_SECONDS)
	if is_nan(timeout) or timeout <= 0.0:
		return DevCommand.reply(false, "'timeout' must be a number greater than zero")

	var sequence_id: String = "%08x" % (randi() & 0xFFFFFFFF)
	_set_status(sequence_id, STATUS_RUNNING, "Sequence %s is running" % sequence_id, steps, [])
	var data: Dictionary = {"sequence_id": sequence_id, "step_count": steps.size()}
	_log.call("input", "Starting sequence %s (timeout %ss)" % [sequence_id, timeout], data)
	# Not awaited: the client polls input_sequence_status until it ends.
	_run_sequence(sequence_id, steps, timeout)
	return DevCommand.reply(
		true, "Sequence %s started with %d steps" % [sequence_id, steps.size()], data
	)


func _cmd_sequence_status(args: Dictionary) -> Dictionary:
	var sequence_id: String = str(args.get("sequence_id", ""))
	if not _sequences.has(sequence_id):
		return DevCommand.reply(false, "Unknown sequence: '%s'" % sequence_id)
	var status: Dictionary = _sequences[sequence_id]
	var running: bool = status["status"] == STATUS_RUNNING
	var success: bool = status["success"]
	return DevCommand.reply(running or success, str(status["message"]), status.duplicate(true))


## "" when `args` names a known input action and its numbers are numbers; else why not.
func _action_error(args: Dictionary) -> String:
	var action: String = ""
	if args.get("action") is String:
		action = args["action"]
	if action.is_empty():
		return "Missing 'action' argument"
	if not InputMap.has_action(action):
		var listing: String = "devtools.py input list (MCP: input_actions)"
		return "Unknown action: %s. List them: %s." % [action, listing]
	for key: String in ["strength", "hold_seconds", "seconds"]:
		if is_nan(_number(args, key, 0.0)):
			return "'%s' must be a number" % key
	return ""


## The number at `key`, `fallback` when absent, NAN when it is not a number.
func _number(args: Dictionary, key: String, fallback: float) -> float:
	if not args.has(key):
		return fallback
	var value: Variant = args[key]
	if not DevState.is_number(value):
		return NAN
	var number: float = value
	return number


func _send(action: String, pressed: bool, strength: float = 1.0) -> void:
	var event: InputEventAction = InputEventAction.new()
	event.action = action
	event.pressed = pressed
	event.strength = strength if pressed else 0.0
	Input.parse_input_event(event)
	# Godot buffers parsed events until the next frame; apply this one now.
	Input.flush_buffered_events()
	if not pressed:
		_held.erase(action)
	elif not _held.has(action):
		_held.append(action)


## Presses, lets `seconds` pass (and at least one process frame and one physics tick), then
## releases. Returns how the wait ended.
func _tap(action: String, seconds: float, strength: float, deadline_msec: int) -> Waited:
	_send(action, true, strength)
	var waited: Waited = await _pass_time(seconds, deadline_msec, {}, true)
	_send(action, false)
	return waited


## Lets `seconds` of game time pass, at least one frame, resuming at the start of a frame.
## Ends early once `until` holds, when one is given. With `span_tick` it also waits for a
## physics tick. DEADLINE means real time ran out first.
func _pass_time(
	seconds: float, deadline_msec: int, until: Dictionary, span_tick: bool = false
) -> Waited:
	var tree: SceneTree = _host.get_tree()
	var first_frame: int = Engine.get_process_frames()
	var first_tick: int = Engine.get_physics_frames()
	var remaining: float = seconds
	while Time.get_ticks_msec() < deadline_msec:
		if not until.is_empty() and _condition_error(until).is_empty():
			return Waited.CONDITION_MET
		# A whole frame (and tick) has run once the counters moved past where this began.
		var framed: bool = Engine.get_process_frames() > first_frame
		var ticked: bool = not span_tick or Engine.get_physics_frames() > first_tick
		if framed and ticked and remaining <= 0.0:
			return Waited.ELAPSED
		await tree.process_frame
		remaining -= _host.get_process_delta_time()
	return Waited.DEADLINE


## One step, checked and with defaults filled in; {"error": ...} when it is malformed.
func _parse_step(raw_step: Variant, index: int) -> Dictionary:
	var raw: Dictionary = {}
	if raw_step is Dictionary:
		raw = raw_step
	if not raw.get("type") is String:
		return {"error": "Step %d: missing 'type'" % index}
	var type: String = raw["type"]
	var step: Dictionary = {"type": type, "index": index}
	var problems: PackedStringArray = []
	if not STEP_TYPES.has(type):
		problems.append("unknown step type '%s'" % type)
	if ACTION_STEPS.has(type):
		problems.append(_action_error(raw))
		step["action"] = raw.get("action")
		step["strength"] = clampf(_number(raw, "strength", 1.0), 0.0, 1.0)
	if TIMED_STEPS.has(type):
		var seconds: float = _number(raw, "seconds", 0.0 if type == STEP_TAP else NAN)
		step["seconds"] = seconds
		if is_nan(seconds) or seconds < 0.0:
			problems.append("'%s' requires 'seconds' (a number, zero or more)" % type)
	if (type == STEP_HOLD or type == STEP_WAIT) and raw.has("until"):
		problems.append(_parse_condition(raw["until"], step, "'until'"))
	if type == STEP_ASSERT:
		problems.append(_parse_condition(raw, step, "'assert'"))
	if type == STEP_SCREENSHOT and raw.has("filename"):
		step["filename"] = raw["filename"]
		problems.append(Screenshots.validate_filename(str(raw["filename"])))
	if type == STEP_CALL:
		problems.append(_parse_call(raw, step))
	for problem: String in problems:
		if not problem.is_empty():
			return {"error": "Step %d: %s" % [index, problem]}
	return step


## Reads {action, args?} from `raw` into step["command"] and step["args"]. Whether the
## command exists is checked when the step runs: a game may register it mid-sequence.
func _parse_call(raw: Dictionary, step: Dictionary) -> String:
	if not raw.get("action") is String or str(raw["action"]).is_empty():
		return "'call' requires 'action' (a command name)"
	if raw["action"] == DevCommand.INPUT_SEQUENCE:
		return "'call' cannot start another sequence"
	if not raw.get("args", {}) is Dictionary:
		return "'call': 'args' must be an object"
	step["command"] = raw["action"]
	step["args"] = raw.get("args", {})
	return ""


## Reads {node, property, equals | ge | le} from `raw` into step["condition"].
func _parse_condition(raw: Variant, step: Dictionary, what: String) -> String:
	if not raw is Dictionary:
		return "%s must be an object" % what
	var source: Dictionary = raw
	if not source.get("node") is String or not source.get("property") is String:
		return "%s requires 'node' and 'property' (strings)" % what
	var comparison: String = ""
	for candidate: String in COMPARISONS:
		if source.has(candidate):
			comparison = candidate
	if comparison.is_empty():
		return "%s requires one of %s" % [what, ", ".join(COMPARISONS)]
	if comparison != "equals" and not DevState.is_number(source[comparison]):
		return "%s: '%s' takes a number" % [what, comparison]
	step["condition"] = {
		"node": source["node"],
		"property": source["property"],
		"comparison": comparison,
		"value": source[comparison],
	}
	return ""


## "" when `condition` holds right now; else what it found instead.
func _condition_error(condition: Dictionary) -> String:
	var node_path: String = condition["node"]
	var property: String = condition["property"]
	var read: DevState.Decoded = DevState.read(_host.get_tree(), node_path, property)
	if not read.error.is_empty():
		return read.error
	var comparison: String = condition["comparison"]
	var expected: Variant = condition["value"]
	var found: String = ""
	if comparison == "equals":
		found = DevState.mismatch(read.value, expected)
	elif not DevState.is_number(read.value):
		found = "is %s, not a number" % JSON.stringify(DevState.to_json(read.value))
	else:
		var actual: float = read.value
		var limit: float = expected
		var holds: bool = actual >= limit if comparison == "ge" else actual <= limit
		if not holds:
			found = "is %s, expected %s %s" % [actual, comparison, limit]
	# Named the way a NodePath names a property: "Player:position".
	return "" if found.is_empty() else "%s:%s %s" % [node_path, property, found]


func _run_sequence(sequence_id: String, steps: Array[Dictionary], timeout: float) -> void:
	var deadline_msec: int = Time.get_ticks_msec() + maxi(1, int(timeout * 1000.0))
	var results: Array[Dictionary] = []
	var status: String = STATUS_PASSED
	var message: String = "Sequence %s completed" % sequence_id

	for step: Dictionary in steps:
		var index: int = step["index"]
		var row: Dictionary = {"step": index, "type": step["type"], "status": "ok"}
		# Steps count from 0, as in the results; the message says so where it is read.
		var where: String = "step %d of %d (counted from 0)" % [index, steps.size()]
		var outcome: Outcome = Outcome.new()
		outcome.timed_out = Time.get_ticks_msec() >= deadline_msec
		if not outcome.timed_out:
			outcome = await _run_step(step, deadline_msec)
		if outcome.timed_out:
			status = STATUS_TIMED_OUT
			message = "Sequence %s timed out at %s" % [sequence_id, where]
			row.merge({"status": "timeout", "error": message}, true)
		elif not outcome.error.is_empty():
			status = STATUS_FAILED
			message = "Sequence %s failed at %s: %s" % [sequence_id, where, outcome.error]
			row.merge({"status": "error", "error": outcome.error}, true)
		else:
			row["result"] = outcome.data
		results.append(row)
		_log.call("input", "Sequence %s step %d: %s" % [sequence_id, index, row["status"]], row)
		if status != STATUS_PASSED:
			break

	var shot_error: String = await _screenshots.wait_for_writes()
	mark_unwritten(results, _screenshots.unwritten)
	if not shot_error.is_empty() and status == STATUS_PASSED:
		status = STATUS_FAILED
		message = "Sequence %s failed: %s" % [sequence_id, shot_error]
	var released: Array[String] = []
	if status != STATUS_PASSED:
		released = release_all()
	_set_status(sequence_id, status, message, steps, results, released)
	_log.call("input", message, {"status": status, "cleared_actions": released})


## Marks the screenshot rows of `results` whose file is in `unwritten` (path -> why) as
## failed. A capture is written after its step, so its row said "ok" before the write failed.
static func mark_unwritten(
	results: Array[Dictionary], unwritten: Dictionary[String, String]
) -> void:
	for row: Dictionary in results:
		var result: Dictionary = row.get("result", {})
		var path: String = str(result.get("path", ""))
		if row.get("type") == STEP_SCREENSHOT and not path.is_empty() and unwritten.has(path):
			row.merge({"status": "error", "error": unwritten[path]}, true)


func _run_step(step: Dictionary, deadline_msec: int) -> Outcome:
	var outcome: Outcome = Outcome.new()
	var type: String = step["type"]
	var action: String = str(step.get("action", ""))
	var seconds: float = step.get("seconds", 0.0)
	var strength: float = step.get("strength", 1.0)
	var condition: Dictionary = step.get("condition", {})
	var waited: Waited = Waited.ELAPSED
	match type:
		STEP_PRESS:
			_send(action, true, strength)
		STEP_RELEASE:
			_send(action, false)
		STEP_TAP:
			waited = await _tap(action, seconds, strength, deadline_msec)
		STEP_HOLD:
			_send(action, true, strength)
			waited = await _pass_time(seconds, deadline_msec, condition, true)
			_send(action, false)
		STEP_WAIT:
			waited = await _pass_time(seconds, deadline_msec, condition)
			if waited == Waited.ELAPSED and not condition.is_empty():
				outcome.error = "Not within %ss: %s" % [seconds, _condition_error(condition)]
		STEP_ASSERT:
			var failure: String = _condition_error(condition)
			if not failure.is_empty():
				outcome.error = "Assertion failed: %s" % failure
		STEP_SCREENSHOT:
			var filename: String = step.get("filename", Screenshots.timestamped_name("sequence"))
			var reply: Dictionary = await _screenshots.capture(filename, true)
			if reply["success"]:
				outcome.data = reply["data"]
			else:
				outcome.error = str(reply["message"])
		STEP_CLEAR:
			outcome.data["cleared_actions"] = release_all()
		STEP_CALL:
			var command: String = step["command"]
			var command_args: Dictionary = step["args"]
			var answer: Dictionary = await _execute.call(command, command_args)
			outcome.data = {"action": command, "message": answer["message"], "data": answer["data"]}
			if not answer["success"]:
				outcome.error = "Command '%s' failed: %s" % [command, answer["message"]]
	outcome.timed_out = waited == Waited.DEADLINE
	if ACTION_STEPS.has(type):
		outcome.data["action"] = action
	if TIMED_STEPS.has(type):
		outcome.data["seconds"] = seconds
		outcome.data["condition_met"] = waited == Waited.CONDITION_MET
	return outcome


func _set_status(
	sequence_id: String,
	status: String,
	message: String,
	steps: Array[Dictionary],
	results: Array[Dictionary],
	released: Array[String] = []
) -> void:
	_sequences[sequence_id] = {
		"sequence_id": sequence_id,
		"status": status,
		"success": status == STATUS_PASSED,
		"message": message,
		"step_count": steps.size(),
		"completed_steps": results.size(),
		"results": results,
		"cleared_actions": released,
	}
	while _sequences.size() > KEPT_SEQUENCES:
		_sequences.erase(_sequences.keys()[0])
