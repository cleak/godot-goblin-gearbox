extends GdUnitTestSuite
## Screenshot file names, and the background write of a frame that was read back.

const DevCommand := preload("res://addons/agent_devtools/dev_command.gd")
const Screenshots := preload("res://addons/agent_devtools/dev_tools_screenshots.gd")
const InputSimulator := preload("res://addons/agent_devtools/dev_tools_input.gd")
## Physics ticks a test waits for a sequence before it gives up (10 seconds).
const SEQUENCE_TICKS_MAX: int = 600


## A viewport that is captured and a disk that refuses: every capture answers "captured",
## and the write behind it fails.
class RefusedShots:
	extends Screenshots

	func capture(filename: String, _background: bool = false) -> Dictionary:
		var path: String = "/shots".path_join(filename)
		_keep_error(path, "Failed to save screenshot %s: Can't open file" % filename)
		return DevCommand.reply(true, "Screenshot captured", {"path": path})


func test_safe_png_basenames_are_accepted() -> void:
	for filename: String in ["shot.png", "feature_state-01.png", "a.b.PNG", "CONSOLE.png"]:
		assert_str(Screenshots.validate_filename(filename)).is_empty()


func test_paths_and_unsafe_names_are_refused() -> void:
	var unsafe: Array[String] = [
		"",
		".png",
		"../escape.png",
		"..\\escape.png",
		"folder/capture.png",
		"folder\\capture.png",
		"C:capture.png",
		"capture.jpg",
		"capture",
		"capture name.png",
		"captúre.png",
		".hidden.png",
		"CON.png",
		"nul.png",
		"lpt1.capture.png",
	]
	for filename: String in unsafe:
		assert_str(Screenshots.validate_filename(filename)).is_not_empty()


func test_timestamped_names_are_valid_file_names() -> void:
	var filename: String = Screenshots.timestamped_name("sequence")
	assert_str(filename).starts_with("sequence_").ends_with(".png")
	assert_str(Screenshots.validate_filename(filename)).is_empty()


func test_a_session_has_a_screenshot_folder_of_its_own() -> void:
	assert_str(Screenshots.directory_for("")).is_equal("user://screenshots")
	assert_str(Screenshots.directory_for("agent-1")).is_equal("user://screenshots/agent-1")
	assert_str(Screenshots.new(self).directory).is_equal("user://screenshots")


## macOS draws no frame for a window that is covered, on another Space or behind a locked
## screen: "minimized?" alone sent the reader the wrong way there.
func test_a_window_that_draws_nothing_is_explained_for_every_system() -> void:
	var said: String = Screenshots.NOT_DRAWING
	assert_str(said).starts_with("Nothing to capture: the window is not drawing")
	for cause: String in ["minimized", "macOS", "covered", "another Space", "locked screen"]:
		assert_str(said).contains(cause)


func test_capture_refuses_a_bad_name_before_touching_the_viewport() -> void:
	var screenshots: Screenshots = Screenshots.new(self)
	var reply: Dictionary = await screenshots.capture("../outside.png")
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).contains("basename")


## The kit runs its suites headless, where no frame is rendered: the capture says so. A run
## with a window (or under Xvfb) writes the picture.
func test_capture_writes_the_viewport_or_says_that_nothing_is_rendered() -> void:
	var screenshots: Screenshots = Screenshots.new(self)
	# Unique per process: several test runs may share one user:// folder.
	var reply: Dictionary = await screenshots.capture("gdunit_%d.png" % OS.get_process_id())
	if DisplayServer.get_name() == "headless":
		assert_bool(reply["success"]).is_false()
		assert_str(str(reply["message"])).contains("headless")
		return
	assert_str(str(reply["message"])).is_equal("Screenshot captured")
	var data: Dictionary = reply["data"]
	var path: String = data["path"]
	var size: Dictionary = data["size"]
	var width: int = size["width"]
	var height: int = size["height"]
	var written: Image = Image.load_from_file(path)
	assert_object(written).is_not_null()
	assert_that(written.get_size()).is_equal(Vector2i(width, height))
	DirAccess.remove_absolute(path)


func test_a_frame_that_was_read_back_is_written_by_a_worker_before_the_wait_ends() -> void:
	var screenshots: Screenshots = Screenshots.new(self)
	var image: Image = Image.create(64, 32, false, Image.FORMAT_RGBA8)
	image.fill(Color(0.4, 0.3, 0.2))
	# Unique per process: several test runs may share one user:// folder.
	var path: String = OS.get_user_data_dir().path_join("gdunit_%d.png" % OS.get_process_id())
	# As if an asynchronous readback were in flight and landed on a later frame.
	screenshots.set("_reads", 1)
	screenshots.call_deferred("_on_read", image, path, "")
	var error: String = await screenshots.wait_for_writes()
	assert_str(error).is_empty()
	assert_bool(FileAccess.file_exists(path)).is_true()
	var written: Image = Image.load_from_file(path)
	assert_int(written.get_width()).is_equal(64)
	assert_int(written.get_format()).is_equal(Image.FORMAT_RGB8)
	DirAccess.remove_absolute(path)


func test_a_failed_readback_is_reported_once_by_the_wait() -> void:
	var screenshots: Screenshots = Screenshots.new(self)
	screenshots.set("_reads", 1)
	screenshots.call_deferred("_on_read", null, "user://gdunit_lost.png", "readback error")
	assert_str(await screenshots.wait_for_writes()).contains("gdunit_lost.png")
	assert_array(screenshots.unwritten.keys()).is_equal(["user://gdunit_lost.png"])
	assert_str(await screenshots.wait_for_writes()).is_empty()
	assert_dict(screenshots.unwritten).is_empty()


## A background capture answers "captured" before its file is written: the wait is where a
## write that fails shows, with the file it was for.
func test_files_a_worker_could_not_write_are_named_by_the_wait() -> void:
	var screenshots: Screenshots = Screenshots.new(self)
	# As a worker reports a PNG it could not save (a real one logs an engine error).
	screenshots.call("_keep_error", "/shots/a.png", "Failed to save screenshot a.png: first")
	screenshots.call("_keep_error", "/shots/b.png", "Failed to save screenshot b.png")
	screenshots.call("_keep_error", "/shots/a.png", "Failed to save screenshot a.png: again")
	var error: String = await screenshots.wait_for_writes()
	assert_str(error).is_equal("Failed to save screenshot a.png: first")
	assert_array(screenshots.unwritten.keys()).is_equal(["/shots/a.png", "/shots/b.png"])
	assert_str(await screenshots.wait_for_writes()).is_empty()
	assert_dict(screenshots.unwritten).is_empty()


## A screenshot step answers before its file is written: the sequence fails at its end, and
## the step's row must not go on saying "ok".
func test_a_sequence_fails_and_marks_the_step_whose_capture_was_not_written() -> void:
	var quiet: Callable = func(_category: String, _message: String, _data: Variant) -> void: pass
	var simulator: InputSimulator = InputSimulator.new(
		self, RefusedShots.new(self), quiet, Callable()
	)
	var handlers: Dictionary = simulator.handlers()
	var start: Callable = handlers[DevCommand.INPUT_SEQUENCE]
	var poll: Callable = handlers[DevCommand.INPUT_SEQUENCE_STATUS]
	var steps: Array = [{"type": "screenshot", "filename": "a.png"}, {"type": "clear"}]
	var started: Dictionary = start.call({"steps": steps, "timeout": 10.0})
	assert_bool(started["success"]).is_true()
	var status: Dictionary = {}
	for _tick: int in range(SEQUENCE_TICKS_MAX):
		var reply: Dictionary = poll.call({"sequence_id": started["data"]["sequence_id"]})
		status = reply["data"]
		if status["status"] != "running":
			break
		await get_tree().physics_frame
	assert_str(str(status["status"])).is_equal("failed")
	assert_str(str(status["message"])).ends_with("Failed to save screenshot a.png: Can't open file")
	var rows: Array = status["results"]
	assert_int(rows.size()).is_equal(2)
	assert_str(str(rows[0]["status"])).is_equal("error")
	assert_str(str(rows[0]["result"]["path"])).is_equal("/shots/a.png")
	assert_str(str(rows[1]["status"])).is_equal("ok")


func test_a_sequence_row_whose_file_was_not_written_is_marked_failed() -> void:
	var results: Array[Dictionary] = [
		{"step": 0, "type": "screenshot", "status": "ok", "result": {"path": "/shots/a.png"}},
		{"step": 1, "type": "screenshot", "status": "ok", "result": {"path": "/shots/b.png"}},
		# Not a screenshot, and a screenshot that failed before it had a file.
		{"step": 2, "type": "call", "status": "ok", "result": {"path": "/shots/b.png"}},
		{"step": 3, "type": "screenshot", "status": "error", "error": "Nothing to capture"},
	]
	var unwritten: Dictionary[String, String] = {"": "lost", "/shots/b.png": "Can't open file"}
	InputSimulator.mark_unwritten(results, unwritten)
	var statuses: Array = results.map(func(row: Dictionary) -> String: return row["status"])
	assert_array(statuses).is_equal(["ok", "error", "ok", "error"])
	assert_str(str(results[1]["error"])).is_equal("Can't open file")
	# The file's name stays in the row: a client says which capture is missing.
	assert_str(str(results[1]["result"]["path"])).is_equal("/shots/b.png")
	assert_str(str(results[3]["error"])).is_equal("Nothing to capture")
