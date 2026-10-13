extends GdUnitTestSuite
## The DevTools autoload: when it runs, how it dispatches, the built-in commands, the
## extension seam, and commands through the real mailbox files. set_state and run_method are
## in test_devtools_mutation.gd.

const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const ErrorLog := preload("res://addons/agent_devtools/dev_tools_errors.gd")
const Mailbox := preload("res://addons/agent_devtools/dev_tools_mailbox.gd")
## Physics ticks a test waits for the mailbox thread before it gives up (5 seconds).
const MAILBOX_TICKS_MAX: int = 300


class Sample:
	extends Node2D
	var count: int = 3
	var spot: Vector2 = Vector2(1, 2)
	var tint: Color = Color.WHITE
	var label: StringName = &"idle"
	## Where fail_like_a_script reports to: the DevTools under test.
	var error_log: ErrorLog = null

	## What a handler that hits a script error looks like from outside: the engine logs the
	## error and the call goes on. A real one would fail the test run.
	func fail_like_a_script() -> void:
		error_log.record(ErrorLog.KIND_SCRIPT, "No key 'missing'", "sample.gd", 12)

	func reply_later(args: Dictionary) -> Dictionary:
		await get_tree().process_frame
		return {"success": true, "message": "late", "data": {"at": spot, "args": args}}


## Unique per process: several test runs may share one user:// folder.
var _session: String = "gdunit_commands_%d" % OS.get_process_id()
var _dev_tools: DevToolsScript = null
var _sample: Sample = null
var _sample_path: String = ""


func before_test() -> void:
	_dev_tools = _new_dev_tools()
	_sample = auto_free(Sample.new())
	_sample.name = "Sample"
	add_child(_sample)
	_sample_path = str(_sample.get_path())


func after_test() -> void:
	_dev_tools.stop()
	_remove_mailbox(_mailbox_folder())


func _remove_mailbox(root: String) -> void:
	for folder: String in [
		root.path_join(Mailbox.COMMANDS_FOLDER), root.path_join(Mailbox.RESULTS_FOLDER), root
	]:
		if DirAccess.dir_exists_absolute(folder):
			for file: String in DirAccess.get_files_at(folder):
				DirAccess.remove_absolute(folder.path_join(file))
			DirAccess.remove_absolute(folder)


func test_active_only_in_debug_runs_that_are_not_headless_script_tools() -> void:
	var script_run: PackedStringArray = ["--headless", "-s", "res://tools/lint_project.gd"]
	var long_flag: PackedStringArray = ["--script", "res://tools/lint_project.gd"]
	assert_bool(DevToolsScript.is_active_run(true, "Windows", [])).is_true()
	assert_bool(DevToolsScript.is_active_run(true, "headless", [])).is_true()
	assert_bool(DevToolsScript.is_active_run(true, "Windows", script_run)).is_true()
	assert_bool(DevToolsScript.is_active_run(true, "headless", script_run)).is_false()
	assert_bool(DevToolsScript.is_active_run(true, "headless", long_flag)).is_false()
	assert_bool(DevToolsScript.is_active_run(false, "Windows", [])).is_false()


func test_an_idle_autoload_takes_no_handlers_and_does_not_process() -> void:
	assert_bool(_dev_tools.is_enabled()).is_false()
	assert_bool(_dev_tools.is_processing()).is_false()
	assert_bool(_dev_tools.register_handler("gdunit_idle", _reply_ok)).is_false()


func test_ping_says_who_answers() -> void:
	var reply: Dictionary = await _dev_tools.execute("ping")
	var data: Dictionary = reply["data"]
	assert_bool(reply["success"]).is_true()
	assert_str(str(reply["message"])).is_equal("pong")
	assert_that(data["pid"]).is_equal(OS.get_process_id())
	# Whatever the game is called: tools/new_game.ps1 renames it and may keep these tests.
	var title: String = ProjectSettings.get_setting("application/config/name")
	assert_str(title).is_not_empty()
	assert_str(str(data["project"])).is_equal(title)
	assert_bool(data["mutation_enabled"]).is_false()
	assert_str(str(data["user_dir"])).is_equal(OS.get_user_data_dir())
	# No session: the one folder every game without a session writes to.
	assert_str(str(data["screenshots"])).is_equal(OS.get_user_data_dir().path_join("screenshots"))
	assert_that(data["errors"]).is_equal(0)


## Two games of one project (two agents, two worktrees) each ran the sample sequence and
## wrote user://screenshots/smoke.png: the second capture replaced the first.
func test_a_game_with_a_session_keeps_its_screenshots_in_a_folder_of_that_session() -> void:
	# As typed by a person; unique per process, since test runs may share one user:// folder.
	var typed: String = " agent/%d " % OS.get_process_id()
	var session: String = "agent_%d" % OS.get_process_id()
	_dev_tools.start(typed)
	var reply: Dictionary = await _dev_tools.execute("ping")
	_dev_tools.stop()
	assert_str(str(reply["data"]["session"])).is_equal(session)
	assert_str(str(reply["data"]["screenshots"])).is_equal(
		OS.get_user_data_dir().path_join("screenshots").path_join(session)
	)
	var screenshots: RefCounted = _dev_tools.get("_screenshots")
	assert_str(str(screenshots.get("directory"))).is_equal("user://screenshots/" + session)
	_remove_mailbox(OS.get_user_data_dir().path_join(Mailbox.folder_name(session)))


func test_ping_counts_the_errors_the_engine_logged() -> void:
	var errors: ErrorLog = _dev_tools.get("_errors")
	errors.record(ErrorLog.KIND_SCRIPT, "Parse Error: Could not find type", "res://game.gd", 3)
	errors.record(ErrorLog.KIND_ERROR, "Failed to load script", "res://game.gd", 0)
	var reply: Dictionary = await _dev_tools.execute("ping")
	assert_that(reply["data"]["errors"]).is_equal(2)


func test_unknown_command_is_an_error_reply() -> void:
	var reply: Dictionary = await _dev_tools.execute("no_such_command")
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).is_equal("Unknown command: no_such_command")


func test_get_state_lists_script_variables_as_json() -> void:
	var reply: Dictionary = await _dev_tools.execute("get_state", {"node_path": _sample_path})
	var data: Dictionary = reply["data"]
	assert_bool(reply["success"]).is_true()
	assert_str(str(data["node_class"])).is_equal("Node2D")
	assert_that(data["count"]).is_equal(3)
	assert_that(data["spot"]).is_equal({"x": 1.0, "y": 2.0})
	assert_that(data["tint"]).is_equal({"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0})
	assert_that(data["label"]).is_equal("idle")
	assert_that(data["position"]).is_equal({"x": 0.0, "y": 0.0})
	var missing: Dictionary = await _dev_tools.execute("get_state", {"node_path": "/root/Nope"})
	assert_str(str(missing["message"])).is_equal("Node not found: /root/Nope")


func test_scene_tree_describes_the_current_scene() -> void:
	var scene: Node2D = Node2D.new()
	scene.name = "GdunitScene"
	var child: Node = Node.new()
	child.name = "Child"
	scene.add_child(child)
	var previous: Node = get_tree().current_scene
	get_tree().root.add_child(scene)
	get_tree().current_scene = scene
	var deep: Dictionary = await _dev_tools.execute("scene_tree")
	var flat: Dictionary = await _dev_tools.execute("scene_tree", {"depth": 0})
	var relative: Dictionary = await _dev_tools.execute("get_state", {"node_path": "Child"})
	get_tree().current_scene = previous
	scene.free()
	var tree: Dictionary = deep["data"]
	assert_str(str(tree["name"])).is_equal("GdunitScene")
	assert_str(str(tree["type"])).is_equal("Node2D")
	assert_that(tree["position"]).is_equal({"x": 0.0, "y": 0.0})
	var children: Array = tree["children"]
	assert_str(str(children[0]["path"])).is_equal("/root/GdunitScene/Child")
	var cut: Dictionary = flat["data"]
	assert_bool(cut.has("children")).is_false()
	assert_that(cut["child_count"]).is_equal(1)
	assert_str(str(relative["data"]["node_path"])).is_equal("/root/GdunitScene/Child")


func test_scene_list_holds_the_project_scenes_and_no_third_party_ones() -> void:
	var scenes: PackedStringArray = DevToolsScript.scene_paths()
	assert_bool(scenes.has("res://main.tscn")).is_true()
	for scene_path: String in scenes:
		assert_bool(FileAccess.file_exists(scene_path)).is_true()
		assert_bool(scene_path.begins_with("res://addons/gdUnit4")).is_false()


func test_validate_commands_report_scenes() -> void:
	var all: Dictionary = await _dev_tools.execute("validate_all_scenes")
	var total: int = DevToolsScript.scene_paths().size()
	assert_bool(all["success"]).is_true()
	assert_that(all["data"]["total_scenes"]).is_equal(total)
	assert_str(str(all["message"])).is_equal(
		"Scenes valid: %d checked, 0 errors, 0 warnings" % total
	)
	var one: Dictionary = await _dev_tools.execute("validate_scene", {"path": "res://main.tscn"})
	assert_bool(one["success"]).is_true()
	assert_that(one["data"]["issues"]).is_equal({})
	var missing: Dictionary = await _dev_tools.execute(
		"validate_scene", {"path": "res://no_such_scene.tscn"}
	)
	assert_bool(missing["success"]).is_false()
	assert_str(str(missing["message"])).is_equal("1 of 1 scenes fail: 1 errors, 0 warnings")
	assert_that(missing["data"]["errors"]).is_equal(1)
	assert_str(str(missing["data"]["issues"]["res://no_such_scene.tscn"][0]["code"])).is_equal(
		"file_not_found"
	)
	var no_path: Dictionary = await _dev_tools.execute("validate_scene")
	assert_str(str(no_path["message"])).is_equal("Missing 'path' argument")


## Validating a scene runs the _init of its scripts, and a mailbox command can come from
## anything that can write a file: only the project's own scenes are taken.
func test_validate_scene_takes_only_scenes_inside_the_project() -> void:
	for inside: String in ["res://main.tscn", "res://levels/a.b/cave.SCN"]:
		assert_str(DevToolsScript.scene_path_error(inside)).is_empty()
	var outside: Array[String] = [
		"C:/temp/outside.tscn",
		"/tmp/outside.tscn",
		"user://outside.tscn",
		"res://../outside.tscn",
		"res://levels/../../outside.tscn",
		"res://..\\outside.tscn",
		"main.tscn",
	]
	for scene_path: String in outside:
		assert_str(DevToolsScript.scene_path_error(scene_path)).contains("res:// path inside")
		var reply: Dictionary = await _dev_tools.execute("validate_scene", {"path": scene_path})
		assert_bool(reply["success"]).is_false()
		assert_str(str(reply["message"])).contains("res:// path inside")
	for not_a_scene: String in ["res://scripts/game/game.gd", "res://icon.svg", "res://main"]:
		assert_str(DevToolsScript.scene_path_error(not_a_scene)).contains("takes a scene")


## What --devtools-validate prints: the issues that fail the run apart from the others.
func test_validation_lines_split_what_fails_from_what_does_not() -> void:
	var issues: Array[Dictionary] = [
		{"severity": "error", "code": "missing_script", "message": "Node 'A' lost its script"},
		{"severity": "warning", "code": "missing_texture", "message": "Sprite2D 'B' has none"},
		{"severity": "info", "code": "missing_audio", "message": "no stream"},
	]
	var reply: Dictionary = {"data": {"strict": false, "issues": {"res://enemy.tscn": issues}}}
	var failing: PackedStringArray = DevToolsScript.validation_lines(reply, true)
	var others: PackedStringArray = DevToolsScript.validation_lines(reply, false)
	assert_array(Array(failing)).contains_exactly(
		["res://enemy.tscn: [ERROR] missing_script: Node 'A' lost its script"]
	)
	assert_int(others.size()).is_equal(2)
	assert_str(others[0]).is_equal(
		"res://enemy.tscn: [WARNING] missing_texture: Sprite2D 'B' has none"
	)
	reply["data"]["strict"] = true
	assert_int(DevToolsScript.validation_lines(reply, true).size()).is_equal(2)
	assert_int(DevToolsScript.validation_lines({"data": null}, true).size()).is_zero()


func test_a_game_adds_and_removes_its_own_command() -> void:
	_dev_tools.start(_session)
	assert_bool(_dev_tools.register_handler("gdunit_sample", _sample.reply_later)).is_true()
	assert_bool(_dev_tools.register_handler("gdunit_sample", _reply_ok)).is_false()
	assert_bool(_dev_tools.register_handler("ping", _reply_ok)).is_false()
	var reply: Dictionary = await _dev_tools.execute("gdunit_sample", {"n": 1})
	assert_str(str(reply["message"])).is_equal("late")
	# Reply data is converted to JSON: the Vector2 arrives as {"x", "y"}.
	assert_that(reply["data"]).is_equal({"at": {"x": 1.0, "y": 2.0}, "args": {"n": 1}})
	_dev_tools.unregister_handler("gdunit_sample", _reply_ok)  # not the one that added it
	reply = await _dev_tools.execute("gdunit_sample")
	assert_bool(reply["success"]).is_true()
	_dev_tools.unregister_handler("gdunit_sample", _sample.reply_later)
	reply = await _dev_tools.execute("gdunit_sample")
	assert_str(str(reply["message"])).is_equal("Unknown command: gdunit_sample")


func test_a_handler_that_returns_the_wrong_shape_is_an_error_reply() -> void:
	_dev_tools.start(_session)
	_dev_tools.register_handler("gdunit_shapeless", func(_args: Dictionary) -> String: return "ok")
	var reply: Dictionary = await _dev_tools.execute("gdunit_shapeless")
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).contains("must return")


func test_a_command_file_is_answered_in_the_result_file_of_its_id() -> void:
	_dev_tools.start(_session)
	assert_bool(_dev_tools.is_enabled()).is_true()
	assert_int(_dev_tools.process_mode).is_equal(Node.PROCESS_MODE_ALWAYS)
	_write(_command_file("abc123"), '{"action": "ping", "args": {}}')
	var answer: Dictionary = await _answer_to("abc123")
	assert_str(str(answer.get("id"))).is_equal("abc123")
	assert_str(str(answer.get("action"))).is_equal("ping")
	assert_bool(answer.get("success") == true).is_true()
	assert_bool(answer.has("errors")).is_false()
	var data: Dictionary = answer.get("data", {})
	assert_str(str(data.get("session"))).is_equal(_session)
	assert_str(str(data.get("mailbox"))).is_equal(_mailbox_folder())
	assert_str(str(data.get("screenshots"))).ends_with("/screenshots/" + _session)
	assert_bool(FileAccess.file_exists(_command_file("abc123"))).is_false()
	_dev_tools.stop()
	var log_text: String = _text(_mailbox_folder().path_join(Mailbox.LOG_NAME))
	assert_str(log_text).contains("DevTools initialized").contains("Command ping completed")


## Two clients on one session (two shells, or a shell and the MCP server): each command has
## its own file and its own reply, so neither can take or overwrite the other's.
func test_commands_sent_at_the_same_time_each_get_their_own_reply() -> void:
	_dev_tools.start(_session)
	_dev_tools.register_handler("gdunit_slow", _sample.reply_later)
	_write(_command_file("client-a"), '{"action": "gdunit_slow", "args": {"from": "a"}}')
	_write(_command_file("client-b"), '{"action": "ping"}')
	_write(_command_file("client-c"), '{"action": "no_such_command"}')
	var slow: Dictionary = await _answer_to("client-a")
	var ping: Dictionary = await _answer_to("client-b")
	var unknown: Dictionary = await _answer_to("client-c")
	assert_str(str(slow.get("id"))).is_equal("client-a")
	assert_that(slow["data"]["args"]).is_equal({"from": "a"})
	assert_str(str(ping.get("message"))).is_equal("pong")
	assert_str(str(unknown.get("message"))).is_equal("Unknown command: no_such_command")


## Errors the engine logs while a command runs come back with its reply and land in the log,
## so a client with no view of the game's console still sees them.
func test_errors_logged_while_a_command_runs_are_in_its_reply_and_in_the_log() -> void:
	_dev_tools.start(_session)
	_sample.error_log = _dev_tools.get("_errors")
	_dev_tools.register_handler(
		"gdunit_raises",
		func(_args: Dictionary) -> Dictionary:
			_sample.fail_like_a_script()
			return {"success": true, "message": "done", "data": null}
	)
	_write(_command_file("raises"), '{"action": "gdunit_raises"}')
	var answer: Dictionary = await _answer_to("raises")
	assert_bool(answer.get("success") == true).is_true()
	assert_that(answer.get("errors")).is_equal(["No key 'missing' (sample.gd:12)"])
	_write(_command_file("after"), '{"action": "ping"}')
	var ping: Dictionary = await _answer_to("after")
	assert_that(ping["data"]["errors"]).is_equal(1.0)  # through JSON: every number is a float
	assert_bool(ping.has("errors")).is_false()
	_dev_tools.stop()
	var log_text: String = _text(_mailbox_folder().path_join(Mailbox.LOG_NAME))
	assert_str(log_text).contains('"category":"error"').contains("No key 'missing' (sample.gd:12)")


## A script error raised on every frame must not fill the log.
func test_an_error_that_repeats_is_logged_a_few_times_and_counted_every_time() -> void:
	_dev_tools.start(_session)
	_sample.error_log = _dev_tools.get("_errors")
	for _frame: int in range(DevToolsScript.ERROR_REPEATS_LOGGED + 4):
		_sample.fail_like_a_script()
	_sample.error_log.record(ErrorLog.KIND_ERROR, "another error", "core/a.cpp", 1)
	await get_tree().process_frame
	await get_tree().process_frame
	var ping: Dictionary = await _dev_tools.execute("ping")
	assert_that(ping["data"]["errors"]).is_equal(DevToolsScript.ERROR_REPEATS_LOGGED + 5)
	_dev_tools.stop()
	var log_text: String = _text(_mailbox_folder().path_join(Mailbox.LOG_NAME))
	assert_int(log_text.count("No key 'missing' (sample.gd:12)")).is_equal(
		DevToolsScript.ERROR_REPEATS_LOGGED
	)
	assert_int(log_text.count("further repeats are left out")).is_equal(1)
	assert_int(log_text.count("another error (core/a.cpp:1)")).is_equal(1)


func _reply_ok(_args: Dictionary) -> Dictionary:
	return {"success": true, "message": "ok", "data": null}


func _text(path: String) -> String:
	return FileAccess.get_file_as_string(path) if FileAccess.file_exists(path) else ""


func _write(path: String, text: String) -> void:
	var file: FileAccess = FileAccess.open(path, FileAccess.WRITE)
	file.store_string(text)
	file.close()


func _mailbox_folder() -> String:
	return OS.get_user_data_dir().path_join(Mailbox.folder_name(_session))


func _command_file(id: String) -> String:
	return _mailbox_folder().path_join("%s/%s.json" % [Mailbox.COMMANDS_FOLDER, id])


## The reply to command `id`, once the mailbox thread has written it; {} if it never does.
func _answer_to(id: String) -> Dictionary:
	var path: String = _mailbox_folder().path_join("%s/%s.json" % [Mailbox.RESULTS_FOLDER, id])
	for _tick: int in range(MAILBOX_TICKS_MAX):
		await get_tree().physics_frame
		if FileAccess.file_exists(path):
			var answer: Dictionary = JSON.parse_string(_text(path))
			return answer
	return {}


## A DevTools that is idle whatever kind of run this is; tests start it themselves.
func _new_dev_tools() -> DevToolsScript:
	var dev_tools: DevToolsScript = auto_free(DevToolsScript.new())
	add_child(dev_tools)
	dev_tools.stop()
	return dev_tools
