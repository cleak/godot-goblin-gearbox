extends Node
## DevTools: lets a coding agent drive a running debug build through files in user://.
## Registered as the autoload "DevTools" (enable the Agent DevTools plugin); the client is
## tools/devtools.py.
##
## A client writes one JSON command {"action", "args"} to a file of its own, named by an id;
## DevTools runs it on the main thread and writes {"action", "id", "success", "message",
## "data"} to the result file of that id (dev_tools_mailbox.gd names the files), so several
## clients can talk to one game at once. The environment variable GODOT_AGENT_SESSION gives
## each running game its own mailbox folder and its own screenshot folder
## (user://screenshots/<session>/), so several games can run side by side.
##
## Errors the engine logs (script errors, push_error) are copied into the DevTools log with
## the category "error", counted in `ping`, and listed as "errors" in the reply of the command
## that was running when they were logged.
##
## Active only in debug builds, and never in a headless `--script` run (the kit's own tools).
## set_state and run_method exist only when the project setting devtools/allow_mutation is
## true; keep it false in the committed project.godot.
##
## A game adds its own commands while it runs:
##
##     func _ready() -> void:
##         DevTools.register_handler("spawn", _on_devtools_spawn)
##
##     func _exit_tree() -> void:
##         DevTools.unregister_handler("spawn", _on_devtools_spawn)
##
##     func _on_devtools_spawn(args: Dictionary) -> Dictionary:
##         return {"success": true, "message": "Spawned", "data": {"count": 1}}
##
## and a client calls it with `devtools.py call spawn kind=slime`. A handler may await.
##
## Launch flags (engine arguments, or user arguments after `--`):
##   --devtools-screenshot                capture after a delay, then quit
##   --devtools-screenshot-delay=<sec>    the delay before that capture (default 0.5)
##   --devtools-screenshot-name=<f.png>   the file name of that capture
##   --devtools-validate                  validate every scene, print the issues, exit 0 or 1
##   --devtools-validate-strict           the same, and warnings fail too

const DevCommand := preload("res://addons/agent_devtools/dev_command.gd")
const DevState := preload("res://addons/agent_devtools/dev_tools_state.gd")
const ErrorLog := preload("res://addons/agent_devtools/dev_tools_errors.gd")
const Mailbox := preload("res://addons/agent_devtools/dev_tools_mailbox.gd")
const Screenshots := preload("res://addons/agent_devtools/dev_tools_screenshots.gd")
const InputSimulator := preload("res://addons/agent_devtools/dev_tools_input.gd")
const SceneValidator := preload("res://addons/agent_devtools/scene_validator.gd")

const ALLOW_MUTATION_SETTING: String = "devtools/allow_mutation"
const SESSION_ENV: String = "GODOT_AGENT_SESSION"
## The kit's source rules (kit.json). Optional: the addon also works on its own.
const KIT_CONFIG_PATH: String = "res://tools/kit_config.gd"
const SCENE_EXTENSIONS: PackedStringArray = ["tscn", "scn"]
## A client polls this several times a second while a sequence runs; logging every poll
## would bury the rest of the log. The sequence logs its own steps.
const UNLOGGED_COMMANDS: PackedStringArray = [DevCommand.INPUT_SEQUENCE_STATUS]
const DEFAULT_TREE_DEPTH: int = 10
const DEFAULT_SHOT_DELAY: float = 0.5
## Engine errors listed in one reply; the log holds them all.
const REPLY_ERRORS_MAX: int = 20
## How often one and the same engine error is written to the log; `ping` counts them all.
const ERROR_REPEATS_LOGGED: int = 3

var _enabled: bool = false
var _session: String = ""
## The command, result and log files, on their own thread while DevTools is enabled.
var _mailbox: Mailbox = null
var _screenshots: Screenshots = null
var _input: InputSimulator = null
## Command name -> Callable(args: Dictionary) -> Dictionary.
var _handlers: Dictionary = {}
## The file name for the --devtools-screenshot capture; "" for a timestamped one.
var _launch_shot_name: String = ""
## What the engine logged as errors while this DevTools watched.
var _errors: ErrorLog = ErrorLog.new()
var _watching_errors: bool = false
## How many of those errors are in the DevTools log already.
var _errors_logged: int = 0
## Error text -> how often it was seen.
var _error_repeats: Dictionary[String, int] = {}


func _init() -> void:
	_screenshots = Screenshots.new(self)
	_input = InputSimulator.new(self, _screenshots, log_event, execute)
	_register_builtin_handlers()
	# An autoload is created before the main scene loads: watch from here, so a main scene
	# whose scripts do not compile shows up in `ping` and in the log.
	if is_active_run(OS.is_debug_build(), DisplayServer.get_name(), OS.get_cmdline_args()):
		_watch_errors()


func _notification(what: int) -> void:
	if what == NOTIFICATION_PREDELETE:
		_unwatch_errors()


func _ready() -> void:
	set_process(false)
	if is_active_run(OS.is_debug_build(), DisplayServer.get_name(), OS.get_cmdline_args()):
		start(OS.get_environment(SESSION_ENV))
		_apply_launch_flags()


func _process(_delta: float) -> void:
	_log_new_errors()
	# Not awaited: a command that waits (a screenshot, a handler that awaits) does not hold
	# up the commands of other clients.
	for command: Dictionary in _mailbox.take_commands():
		_run_command(command)


func _exit_tree() -> void:
	stop()


## Starts answering commands on the mailbox of `raw_session`. _ready calls it in an active
## run; a test may call it on its own instance, with its own session.
func start(raw_session: String) -> void:
	if _enabled:
		return
	_enabled = true
	# A paused game still answers, so an agent can inspect it and unpause it.
	process_mode = Node.PROCESS_MODE_ALWAYS
	_watch_errors()
	_open_mailbox(raw_session)
	_mailbox.rotate_log()
	_mailbox.clear_stale_files()
	_mailbox.start()
	set_process(true)
	log_event("system", "DevTools initialized", _identity())
	_log_new_errors()


## Releases every simulated input, writes what is still queued and stops answering.
func stop() -> void:
	_input.release_all()
	if _enabled:
		_log_new_errors()
		_enabled = false
		set_process(false)
		_mailbox.stop()  # writes every result and log line posted so far
		_mailbox = null
	_unwatch_errors()


## False in release builds and in headless `--script` runs: those are tools (tests, linters),
## not a game to drive, and several may run at once.
static func is_active_run(
	debug_build: bool, display_server: String, command_line: PackedStringArray
) -> bool:
	if not debug_build:
		return false
	var runs_script: bool = command_line.has("-s") or command_line.has("--script")
	return not (display_server == "headless" and runs_script)


## True while DevTools answers commands.
func is_enabled() -> bool:
	return _enabled


## Adds a command while its owner lives. `handler` takes the command's args (a Dictionary) and
## returns {"success": bool, "message": String, "data": Variant}; it may await. Built-in
## commands cannot be replaced. Returns false when DevTools is not enabled (release builds) or
## the name is taken, so game code can call this unconditionally.
func register_handler(action: String, handler: Callable) -> bool:
	if not _enabled or action.is_empty() or _handlers.has(action) or not handler.is_valid():
		return false
	_handlers[action] = handler
	return true


## Removes a command added with register_handler; only the handler that added it can.
func unregister_handler(action: String, handler: Callable) -> void:
	if _handlers.get(action) == handler:
		_handlers.erase(action)


## Appends a row to the DevTools log (read it with `devtools.py logs`).
func log_event(category: String, message: String, data: Variant = null) -> void:
	if _enabled:
		_mailbox.post_log(
			Mailbox.log_row(category, message, DevState.to_json(data)),
			Mailbox.echo_of(category, message)
		)


## Runs one command and returns its reply {"success", "message", "data"}.
func execute(action: String, args: Dictionary = {}) -> Dictionary:
	if not _handlers.has(action):
		return DevCommand.reply(false, _unknown_command_message(action))
	var handler: Callable = _handlers[action]
	if not handler.is_valid():
		_handlers.erase(action)
		return DevCommand.reply(false, "The handler of '%s' no longer exists" % action)
	var raw: Variant = await handler.call(args)
	var reply: Dictionary = {}
	if raw is Dictionary:
		reply = raw
	if not reply.get("success") is bool:
		return DevCommand.reply(
			false, 'The handler of \'%s\' must return {"success", "message", "data"}' % action
		)
	var success: bool = reply["success"]
	return DevCommand.reply(
		success, str(reply.get("message", "")), DevState.to_json(reply.get("data"))
	)


func _register_builtin_handlers() -> void:
	_handlers = {
		DevCommand.PING: _cmd_ping,
		DevCommand.QUIT: _cmd_quit,
		DevCommand.SCREENSHOT: _cmd_screenshot,
		DevCommand.SCENE_TREE: _cmd_scene_tree,
		DevCommand.GET_STATE: _cmd_get_state,
		DevCommand.PERFORMANCE: _cmd_performance,
		DevCommand.VALIDATE_SCENE: _cmd_validate_scene,
		DevCommand.VALIDATE_ALL_SCENES: _cmd_validate_all_scenes,
	}
	_handlers.merge(_input.handlers())
	if OS.is_debug_build() and _mutation_is_enabled():
		_handlers[DevCommand.SET_STATE] = _cmd_set_state
		_handlers[DevCommand.RUN_METHOD] = _cmd_run_method


func _mutation_is_enabled() -> bool:
	return ProjectSettings.get_setting(ALLOW_MUTATION_SETTING, false) == true


func _unknown_command_message(action: String) -> String:
	if action == DevCommand.SET_STATE or action == DevCommand.RUN_METHOD:
		return (
			"Refused: %s needs the project setting %s=true (it is false)"
			% [action, ALLOW_MUTATION_SETTING]
		)
	return "Unknown command: %s" % action


func _open_mailbox(raw_session: String) -> void:
	_session = Mailbox.sanitize_session(raw_session)
	_mailbox = Mailbox.new(OS.get_user_data_dir().path_join(Mailbox.folder_name(_session)))
	# A session's captures get a folder of their own, as its commands do.
	_screenshots.directory = Screenshots.directory_for(_session)


func _watch_errors() -> void:
	if not _watching_errors:
		_watching_errors = true
		OS.add_logger(_errors)


func _unwatch_errors() -> void:
	if _watching_errors:
		_watching_errors = false
		OS.remove_logger(_errors)


## Copies the errors logged since the last call into the DevTools log. Not echoed: the engine
## printed them already.
func _log_new_errors() -> void:
	var total: int = _errors.total()
	if total == _errors_logged or _mailbox == null:
		return
	for row: Dictionary in _errors.since(_errors_logged):
		var text: String = ErrorLog.describe(row)
		# An error raised on every frame would fill the log: each one is logged a few times.
		var seen: int = _error_repeats.get(text, 0)
		seen += 1
		_error_repeats[text] = seen
		if seen == ERROR_REPEATS_LOGGED:
			text += " [logged %d times; further repeats are left out]" % seen
		if seen <= ERROR_REPEATS_LOGGED:
			_mailbox.post_log(Mailbox.log_row(Mailbox.ERROR_CATEGORY, text, row), "")
	_errors_logged = total


func _apply_launch_flags() -> void:
	var arguments: PackedStringArray = OS.get_cmdline_args()
	arguments.append_array(OS.get_cmdline_user_args())
	var shot_delay: float = DEFAULT_SHOT_DELAY
	var wants_shot: bool = false
	var wants_validation: bool = false
	var strict: bool = false
	for argument: String in arguments:
		if argument == "--devtools-screenshot":
			wants_shot = true
		elif argument.begins_with("--devtools-screenshot-delay="):
			shot_delay = maxf(0.0, argument.get_slice("=", 1).to_float())
		elif argument.begins_with("--devtools-screenshot-name="):
			_launch_shot_name = argument.get_slice("=", 1)
		elif argument == "--devtools-validate" or argument == "--devtools-validate-strict":
			wants_validation = true
			strict = strict or argument.ends_with("-strict")
	if wants_validation:
		get_tree().create_timer(0.1).timeout.connect(_on_validate_flag_timeout.bind(strict))
	if wants_shot:
		get_tree().create_timer(shot_delay).timeout.connect(_on_screenshot_flag_timeout)


## A launch flag has no client to read a reply: the outcome goes to the console, failures to
## stderr, and into the exit code.
func _on_screenshot_flag_timeout() -> void:
	var args: Dictionary = {}
	if not _launch_shot_name.is_empty():
		args["filename"] = _launch_shot_name
	var reply: Dictionary = await _cmd_screenshot(args)
	_post_reply(DevCommand.SCREENSHOT, "", reply)
	if reply["success"]:
		print("DevTools: screenshot saved: %s" % reply["data"]["path"])
	else:
		printerr("DevTools: screenshot failed: %s" % reply["message"])
	get_tree().quit(0 if reply["success"] else 1)


func _on_validate_flag_timeout(strict: bool) -> void:
	var reply: Dictionary = _cmd_validate_all_scenes({"strict": strict})
	_post_reply(DevCommand.VALIDATE_ALL_SCENES, "", reply)
	for line: String in validation_lines(reply, true):
		printerr(line)
	for line: String in validation_lines(reply, false):
		print(line)
	if reply["success"]:
		print("DevTools: %s" % reply["message"])
	else:
		printerr("DevTools: validation failed: %s" % reply["message"])
	get_tree().quit(0 if reply["success"] else 1)


## One line per issue of a validation reply, "res://x.tscn: [ERROR] code: message": the
## issues that fail the validation (`blocking`), or the others.
static func validation_lines(reply: Dictionary, blocking: bool) -> PackedStringArray:
	var lines: PackedStringArray = []
	if not reply.get("data") is Dictionary:
		return lines
	var data: Dictionary = reply["data"]
	var strict: bool = data.get("strict") == true
	var by_scene: Dictionary = data.get("issues", {})
	for scene_path: String in by_scene:
		var listed: Array = by_scene[scene_path]
		var issues: Array[Dictionary] = []
		issues.assign(listed)
		for issue: Dictionary in issues:
			if SceneValidator.has_blocking_issues([issue], strict) == blocking:
				var label: String = str(issue.get("severity", "")).to_upper()
				lines.append(
					"%s: [%s] %s: %s" % [scene_path, label, issue.get("code"), issue.get("message")]
				)
	return lines


## Runs one command from the mailbox and posts its reply.
func _run_command(command: Dictionary) -> void:
	var action: String = ""
	if command.get("action") is String:
		action = command["action"]
	var logged: bool = not UNLOGGED_COMMANDS.has(action)
	if logged:
		log_event("command", "Received command: %s" % action, command)
	var errors_before: int = _errors.total()
	var args: Variant = command.get("args", {})
	var reply: Dictionary = {}
	if action.is_empty():
		reply = DevCommand.reply(false, "A command needs an 'action' (a string)")
	elif not args is Dictionary:
		reply = DevCommand.reply(false, "'args' must be a JSON object")
	else:
		var args_map: Dictionary = args
		reply = await execute(action, args_map)
	_post_reply(action, str(command.get("id", "")), reply, logged, _errors.since(errors_before))


## Writes the reply to command `id` (none for ""; a launch flag has no client). `raised` are
## the errors the engine logged while the command ran, another command's and the game's own
## included: the reply lists them as "errors".
func _post_reply(
	action: String,
	id: String,
	reply: Dictionary,
	logged: bool = true,
	raised: Array[Dictionary] = []
) -> void:
	var response: Dictionary = {
		"action": action,
		"id": id,
		"success": reply["success"],
		"message": reply["message"],
		"data": reply["data"],
		"timestamp": Time.get_unix_time_from_system(),
	}
	if not raised.is_empty():
		response["errors"] = ErrorLog.describe_all(raised.slice(0, REPLY_ERRORS_MAX))
	if _mailbox != null and not id.is_empty():
		_mailbox.post_result(id, JSON.stringify(response, "\t", false))
	if logged:
		log_event("result", "Command %s completed" % action, {"success": reply["success"]})


## Who is answering: lets a client tell one running game from another.
func _identity() -> Dictionary:
	var scene: Node = get_tree().current_scene if is_inside_tree() else null
	return {
		"pid": OS.get_process_id(),
		"session": _session,
		"project": str(ProjectSettings.get_setting("application/config/name", "")),
		"godot": str(Engine.get_version_info()["string"]),
		"headless": DisplayServer.get_name() == "headless",
		"mutation_enabled": _handlers.has(DevCommand.SET_STATE),
		"scene": scene.scene_file_path if scene != null else "",
		"user_dir": OS.get_user_data_dir(),
		"mailbox": _mailbox.root_path if _mailbox != null else "",
		"screenshots": ProjectSettings.globalize_path(_screenshots.directory),
		# Errors the engine logged since the game started; the log has them (category "error").
		"errors": _errors.total(),
	}


func _cmd_ping(_args: Dictionary) -> Dictionary:
	var data: Dictionary = _identity()
	data["timestamp"] = Time.get_unix_time_from_system()
	return DevCommand.reply(true, "pong", data)


func _cmd_quit(args: Dictionary) -> Dictionary:
	var exit_code: int = _whole_number(args, "exit_code", 0)
	# The tree quits at the end of this frame; _exit_tree flushes this reply first.
	get_tree().quit(exit_code)
	return DevCommand.reply(true, "Quitting", {"exit_code": exit_code, "pid": OS.get_process_id()})


func _cmd_screenshot(args: Dictionary) -> Dictionary:
	var requested: Variant = args.get("filename", Screenshots.timestamped_name("screenshot"))
	if not requested is String:
		return DevCommand.reply(false, "Screenshot filename must be a string")
	var filename: String = requested
	return await _screenshots.capture(filename)


func _cmd_scene_tree(args: Dictionary) -> Dictionary:
	var scene: Node = get_tree().current_scene
	if scene == null:
		return DevCommand.reply(false, "No current scene")
	var depth: int = _whole_number(args, "depth", DEFAULT_TREE_DEPTH)
	return DevCommand.reply(true, "Scene tree captured", _describe_node(scene, depth))


## The number at `key` as an int; `fallback` when it is absent or not a number.
func _whole_number(args: Dictionary, key: String, fallback: int) -> int:
	if not DevState.is_number(args.get(key)):
		return fallback
	var number: float = args[key]
	return int(number)


func _describe_node(node: Node, levels_left: int) -> Dictionary:
	var described: Dictionary = {
		"name": str(node.name),
		"type": node.get_class(),
		"path": str(node.get_path()),
	}
	var attached: Script = node.get_script()
	if attached != null and not attached.resource_path.is_empty():
		described["script"] = attached.resource_path
	if node is Node2D:
		var node_2d: Node2D = node
		described["position"] = DevState.to_json(node_2d.position)
		described["rotation"] = node_2d.rotation
		described["visible"] = node_2d.visible
	elif node is Node3D:
		var node_3d: Node3D = node
		described["position"] = DevState.to_json(node_3d.position)
		described["rotation"] = DevState.to_json(node_3d.rotation)
		described["visible"] = node_3d.visible
	elif node is Control:
		var control: Control = node
		described["position"] = DevState.to_json(control.position)
		described["size"] = DevState.to_json(control.size)
		described["visible"] = control.visible
	if node.get_child_count() > 0:
		if levels_left > 0:
			var children: Array[Dictionary] = []
			for child: Node in node.get_children():
				children.append(_describe_node(child, levels_left - 1))
			described["children"] = children
		else:
			described["child_count"] = node.get_child_count()
	return described


func _cmd_get_state(args: Dictionary) -> Dictionary:
	var node_path: String = str(args.get("node_path", ""))
	var target: Node = DevState.find_node(get_tree(), node_path)
	if target == null:
		return DevCommand.reply(false, DevState.node_error(get_tree(), node_path))
	var state: Dictionary = {
		"node_class": target.get_class(),
		"node_path": str(target.get_path()),
	}
	var shown: int = PROPERTY_USAGE_SCRIPT_VARIABLE | PROPERTY_USAGE_STORAGE
	for property: Dictionary in target.get_property_list():
		var usage: int = property["usage"]
		if usage & shown != 0:
			var property_name: String = property["name"]
			state[property_name] = DevState.to_json(target.get(property_name))
	return DevCommand.reply(true, "State retrieved", state)


func _cmd_set_state(args: Dictionary) -> Dictionary:
	for key: String in ["node_path", "property", "value"]:
		if not args.has(key):
			return DevCommand.reply(false, "Missing '%s' argument" % key)
	var node_path: String = str(args["node_path"])
	var property: String = str(args["property"])
	var current: DevState.Decoded = DevState.read(get_tree(), node_path, property)
	if not current.error.is_empty():
		return DevCommand.reply(false, current.error)
	var target: Node = DevState.find_node(get_tree(), node_path)
	var declared: int = DevState.declared_type(target, property)
	var wanted: DevState.Decoded = DevState.from_json(args["value"], declared, current.value)
	if declared == TYPE_NIL:
		# An untyped property: read the value as the type it holds now, when it fits.
		var like_current: DevState.Decoded = DevState.from_json(
			args["value"], typeof(current.value)
		)
		if like_current.error.is_empty():
			wanted = like_current
	# Named the way a NodePath names a property: "Player:position".
	var named: String = "%s:%s" % [node_path, property]
	if not wanted.error.is_empty():
		return DevCommand.reply(false, "Cannot set %s: %s" % [named, wanted.error])
	target.set_indexed(NodePath(property), wanted.value)
	# The value read back: a setter may have clamped or changed it.
	var before: Variant = DevState.to_json(current.value)
	var asked: Variant = DevState.to_json(wanted.value)
	var after: Variant = DevState.to_json(target.get_indexed(NodePath(property)))
	var data: Dictionary = {"node_path": node_path, "property": property, "value": after}
	if DevState.json_equal(after, before) and not DevState.json_equal(after, asked):
		# The engine drops a value a property cannot hold without a word.
		var kept: String = "%s kept its value %s" % [named, JSON.stringify(after)]
		return DevCommand.reply(
			false, "%s: it is read-only, or it refused %s" % [kept, JSON.stringify(asked)], data
		)
	return DevCommand.reply(true, "State updated", data)


func _cmd_run_method(args: Dictionary) -> Dictionary:
	var node_path: String = str(args.get("node_path", ""))
	var method: String = str(args.get("method", ""))
	var given: Array = []
	if args.get("args") is Array:
		given = args["args"]
	elif args.has("args"):
		return DevCommand.reply(false, "'args' must be an array")
	var target: Node = DevState.find_node(get_tree(), node_path)
	if target == null:
		return DevCommand.reply(false, DevState.node_error(get_tree(), node_path))
	var refusal: String = _run_method_refusal(target, method)
	if not refusal.is_empty():
		return DevCommand.reply(false, refusal)
	var call_args: Array = []
	var problem: String = DevState.method_arguments(target, method, given, call_args)
	var named: String = "%s:%s" % [node_path, method]
	if not problem.is_empty():
		return DevCommand.reply(false, "Cannot call %s: %s" % [named, problem])
	var errors_before: int = _errors.total()
	var result: Variant = await target.callv(method, call_args)
	var data: Dictionary = {"result": DevState.to_json(result)}
	# A method that hits a script error stops there and returns null; the engine only logs it.
	var raised: Array[Dictionary] = ErrorLog.of_kind(
		_errors.since(errors_before), ErrorLog.KIND_SCRIPT
	)
	if not raised.is_empty():
		var first: String = ErrorLog.describe(raised[0])
		return DevCommand.reply(false, "%s raised a script error: %s" % [named, first], data)
	return DevCommand.reply(true, "Method called", data)


## Why run_method may not call `method` on `target`; "" when it may.
func _run_method_refusal(target: Node, method: String) -> String:
	if target == self:
		# Its private methods skip the checks the commands make (a scene path, a file name).
		# This stops a slip, not an attacker: a method of another node (propagate_call on
		# the root) calls them all the same. With mutation on, a client runs code.
		return "run_method cannot call DevTools itself"
	if method.is_empty() or not target.has_method(method):
		return "Method not found: '%s' on %s" % [method, target.get_path()]
	return ""


func _cmd_performance(_args: Dictionary) -> Dictionary:
	var megabyte: float = 1024.0 * 1024.0
	var fps: float = Engine.get_frames_per_second()
	var viewport: RID = get_viewport().get_viewport_rid()
	# Render times are measured from this call on, so the first reading is zero.
	RenderingServer.viewport_set_measure_render_time(viewport, true)
	var data: Dictionary = {
		"fps": fps,
		"frame_time_ms": 1000.0 / maxf(1.0, fps),
		"physics_fps": Engine.physics_ticks_per_second,
		"max_fps": Engine.max_fps,
		"vsync_mode": DisplayServer.window_get_vsync_mode(),
		"process_ms": Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0,
		"physics_process_ms": Performance.get_monitor(Performance.TIME_PHYSICS_PROCESS) * 1000.0,
		"render_cpu_ms": RenderingServer.viewport_get_measured_render_time_cpu(viewport),
		"render_gpu_ms": RenderingServer.viewport_get_measured_render_time_gpu(viewport),
		"draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		"static_memory_mb": OS.get_static_memory_usage() / megabyte,
		"video_memory_mb": Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / megabyte,
		"objects": Performance.get_monitor(Performance.OBJECT_COUNT),
		"nodes": Performance.get_monitor(Performance.OBJECT_NODE_COUNT),
		"orphan_nodes": Performance.get_monitor(Performance.OBJECT_ORPHAN_NODE_COUNT),
		"physics_2d_active_objects": Performance.get_monitor(Performance.PHYSICS_2D_ACTIVE_OBJECTS),
		"physics_3d_active_objects": Performance.get_monitor(Performance.PHYSICS_3D_ACTIVE_OBJECTS),
	}
	return DevCommand.reply(true, "Performance data captured", data)


func _cmd_validate_scene(args: Dictionary) -> Dictionary:
	var scene_path: String = str(args.get("path", ""))
	if scene_path.is_empty():
		return DevCommand.reply(false, "Missing 'path' argument")
	var refusal: String = scene_path_error(scene_path)
	if not refusal.is_empty():
		return DevCommand.reply(false, refusal)
	var strict: bool = args.get("strict") == true
	return _validate(PackedStringArray([scene_path]), strict)


## Validating a scene loads it and runs the _init of its scripts, so only a scene of this
## project is taken: "" for one, else why `scene_path` is refused.
static func scene_path_error(scene_path: String) -> String:
	var inside: bool = scene_path.begins_with("res://") and not scene_path.contains("\\")
	if not inside or scene_path.split("/").has(".."):
		return "validate_scene takes a res:// path inside the project, got: %s" % scene_path
	if not SCENE_EXTENSIONS.has(scene_path.get_extension().to_lower()):
		return "validate_scene takes a scene (.tscn or .scn), got: %s" % scene_path
	return ""


func _cmd_validate_all_scenes(args: Dictionary) -> Dictionary:
	var scenes: PackedStringArray = scene_paths()
	if scenes.is_empty():
		return DevCommand.reply(false, "No scenes found to validate")
	var strict: bool = args.get("strict") == true
	return _validate(scenes, strict)


## Validates `scenes`. Errors fail the reply; with `strict`, warnings do too. The data holds
## the issues by scene path and the counts.
func _validate(scenes: PackedStringArray, strict: bool) -> Dictionary:
	var issues_by_scene: Dictionary = {}
	var blocked: int = 0
	var errors: int = 0
	var warnings: int = 0
	for scene_path: String in scenes:
		var issues: Array[Dictionary] = SceneValidator.validate_scene(scene_path)
		if not issues.is_empty():
			issues_by_scene[scene_path] = issues
		if SceneValidator.has_blocking_issues(issues, strict):
			blocked += 1
		errors += SceneValidator.count(issues, SceneValidator.ERROR)
		warnings += SceneValidator.count(issues, SceneValidator.WARNING)
	var data: Dictionary = {
		"total_scenes": scenes.size(),
		"scenes_with_issues": issues_by_scene.size(),
		"scenes_with_blocking_issues": blocked,
		"strict": strict,
		"errors": errors,
		"warnings": warnings,
		"issues": issues_by_scene,
	}
	var counted: String = "%d errors, %d warnings" % [errors, warnings]
	if blocked > 0:
		var failing: String = "%d of %d scenes fail" % [blocked, scenes.size()]
		return DevCommand.reply(false, "%s: %s" % [failing, counted], data)
	return DevCommand.reply(true, "Scenes valid: %d checked, %s" % [scenes.size(), counted], data)


## Every scene validate_all_scenes checks, sorted. With the kit's tools/ folder present the
## list follows kit.json (source.exclude, source.include). A project that carries only this
## addon gets every scene outside addons/ and outside hidden, ignored and nested-project
## folders.
static func scene_paths() -> PackedStringArray:
	if ResourceLoader.exists(KIT_CONFIG_PATH):
		var kit_config: GDScript = load(KIT_CONFIG_PATH)
		return kit_config.call("source_files", SCENE_EXTENSIONS)
	var found: PackedStringArray = []
	_collect_scenes("res://", found)
	found.sort()
	return found


static func _collect_scenes(directory_path: String, found: PackedStringArray) -> void:
	var directory: DirAccess = DirAccess.open(directory_path)
	if directory == null:
		return
	directory.list_dir_begin()
	var entry: String = directory.get_next()
	while not entry.is_empty():
		var child: String = directory_path.path_join(entry)
		if not directory.current_is_dir():
			if SCENE_EXTENSIONS.has(entry.get_extension().to_lower()):
				found.append(child)
		elif not _is_skipped_directory(child):
			_collect_scenes(child, found)
		entry = directory.get_next()
	directory.list_dir_end()


static func _is_skipped_directory(directory_path: String) -> bool:
	return (
		directory_path.get_file().begins_with(".")
		or directory_path == "res://addons"
		or FileAccess.file_exists(directory_path.path_join(".gdignore"))
		or FileAccess.file_exists(directory_path.path_join("project.godot"))
	)
