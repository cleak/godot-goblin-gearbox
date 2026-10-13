extends GdUnitTestSuite
## Screenshot file names, and the background write of a frame that was read back.

const Screenshots := preload("res://addons/agent_devtools/dev_tools_screenshots.gd")


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


func test_capture_refuses_a_bad_name_before_touching_the_viewport() -> void:
	var screenshots: Screenshots = Screenshots.new(self)
	var reply: Dictionary = await screenshots.capture("../outside.png")
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).contains("basename")


func test_capture_fails_loudly_when_nothing_is_rendered() -> void:
	if DisplayServer.get_name() != "headless":
		return
	var screenshots: Screenshots = Screenshots.new(self)
	var reply: Dictionary = await screenshots.capture("gdunit_headless.png")
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).contains("headless")


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
	assert_str(await screenshots.wait_for_writes()).is_empty()
