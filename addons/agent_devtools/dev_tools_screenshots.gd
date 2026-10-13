extends RefCounted
## Screenshots for DevTools: the game viewport as user://screenshots/<name>.png, or as
## user://screenshots/<session>/<name>.png in a game that runs with a session, so that two
## games of one project never write over each other's captures.
##
## capture(name) waits for the frame in progress to be drawn, reads it back and writes the PNG
## before it returns, which stalls the game for a few milliseconds. capture(name, true) does
## not stall: the frame downloads from the GPU asynchronously and a worker thread writes the
## file. Input sequences use that form so a screenshot step does not shift the timing of the
## steps around it, and call wait_for_writes() before they report success.
##
## Preload it; it declares no class_name.

const DevCommand := preload("res://addons/agent_devtools/dev_command.gd")

const DIRECTORY: String = "user://screenshots"
## How long wait_for_writes() waits for the asynchronous downloads.
const READBACK_WAIT_MSEC: int = 10000
const READBACK_LOST: String = "Screenshot readback never arrived"
## Frames capture() waits for a draw before it reads the viewport as it is.
const DRAW_WAIT_FRAMES: int = 3
## Said when the window draws no frame. Minimized on every system; macOS also draws none for
## a window that is covered, on another Space or behind a locked screen (Godot's
## window_can_draw() there is the window's occlusion state).
const NOT_DRAWING: String = (
	"Nothing to capture: the window is not drawing. It is minimized, or on macOS covered by "
	+ "another window, on another Space, or behind a locked screen"
)
## File stems Windows reserves for devices, whatever the extension.
const WINDOWS_RESERVED_STEMS: PackedStringArray = [
	"CON",
	"PRN",
	"AUX",
	"NUL",
	"COM1",
	"COM2",
	"COM3",
	"COM4",
	"COM5",
	"COM6",
	"COM7",
	"COM8",
	"COM9",
	"LPT1",
	"LPT2",
	"LPT3",
	"LPT4",
	"LPT5",
	"LPT6",
	"LPT7",
	"LPT8",
	"LPT9",
]

## Where captures go: directory_for() of the session this game runs with.
var directory: String = DIRECTORY
## The background captures the last wait_for_writes() found unwritten: the file's absolute
## path -> why. A capture() with `background` has answered "captured" for each by then.
var unwritten: Dictionary[String, String] = {}

var _host: Node = null
## Asynchronous downloads that have not landed yet (main thread).
var _reads: int = 0
## The files those downloads are for (main thread).
var _reading: Array[String] = []
## Worker tasks writing a PNG (main thread).
var _tasks: Array[int] = []
## Failures of background captures, kept for wait_for_writes(): the file's absolute path, or
## "" when it is not known -> why, oldest first (under _mutex).
var _errors: Dictionary[String, String] = {}
var _mutex: Mutex = Mutex.new()


## `host` is a node inside the tree; its viewport is the one captured.
func _init(host: Node) -> void:
	_host = host


## The folder for the captures of a game on `session` (a name already made file-safe, as
## dev_tools_mailbox.gd sanitize_session gives it): a folder of its own under
## user://screenshots, or that folder itself for "".
static func directory_for(session: String) -> String:
	return DIRECTORY if session.is_empty() else DIRECTORY.path_join(session)


## "" when `filename` is a safe .png basename; else why not. Letters, digits, "_", "-" and "."
## only, so a client cannot write outside the screenshot folder or hit a device name.
static func validate_filename(filename: String) -> String:
	if filename.is_empty():
		return "Screenshot filename must not be empty"
	if filename.contains("/") or filename.contains("\\"):
		return "Screenshot filename must be a basename without path separators"
	if not filename.to_lower().ends_with(".png"):
		return "Screenshot filename must use the .png extension"
	var stem: String = filename.left(-4)
	if stem.is_empty() or filename.begins_with("."):
		return "Screenshot filename must have a safe, non-hidden basename"
	if WINDOWS_RESERVED_STEMS.has(stem.get_slice(".", 0).to_upper()):
		return "Screenshot filename uses a reserved Windows device name"
	for character: String in filename:
		var code: int = character.unicode_at(0)
		var is_alphanumeric: bool = (
			(code >= 48 and code <= 57)
			or (code >= 65 and code <= 90)
			or (code >= 97 and code <= 122)
		)
		if not is_alphanumeric and not character in "_-.":
			return "Screenshot filename contains unsafe characters"
	return ""


## "<prefix>_YYYYMMDD_HHMMSS.png", for captures that were given no name.
static func timestamped_name(prefix: String) -> String:
	var now: Dictionary = Time.get_datetime_dict_from_system()
	return (
		"%s_%04d%02d%02d_%02d%02d%02d.png"
		% [prefix, now["year"], now["month"], now["day"], now["hour"], now["minute"], now["second"]]
	)


## Captures the viewport to <directory>/<filename>. Returns a command reply whose data holds
## the absolute "path" and the "size". Await it. With `background`, the file exists only once
## wait_for_writes() has returned.
func capture(filename: String, background: bool = false) -> Dictionary:
	var filename_error: String = validate_filename(filename)
	if not filename_error.is_empty():
		return DevCommand.reply(false, filename_error)
	var texture: ViewportTexture = _host.get_viewport().get_texture()
	var size: Vector2i = Vector2i(texture.get_size())
	if DisplayServer.get_name() == "headless" or size.x <= 0 or size.y <= 0:
		return DevCommand.reply(false, "Nothing to capture: this run renders no frames (headless)")
	if not DisplayServer.window_can_draw():
		return DevCommand.reply(false, NOT_DRAWING)
	await _next_draw()
	var folder: String = ProjectSettings.globalize_path(directory)
	if DirAccess.make_dir_recursive_absolute(folder) != OK:
		# Windows refuses a folder named like a device: a session called NUL or CON.
		return DevCommand.reply(
			false, "Cannot create the screenshot folder %s: try another session name" % folder
		)
	var abs_path: String = folder.path_join(filename)
	var data: Dictionary = {"path": abs_path, "size": {"width": size.x, "height": size.y}}

	var failure: String = ""
	if background and _read_async(texture.get_rid(), abs_path):
		data["async"] = true
	else:
		failure = _read_now(texture, abs_path, background)
	if not failure.is_empty():
		return DevCommand.reply(false, failure)
	return DevCommand.reply(true, "Screenshot captured", data)


## Resumes once every background capture is read back and written; frames keep running
## meanwhile. Returns the first failure, or ""; `unwritten` then names every file that failed.
func wait_for_writes() -> String:
	var tree: SceneTree = _host.get_tree()
	var deadline: int = Time.get_ticks_msec() + READBACK_WAIT_MSEC
	while _reads > 0 and Time.get_ticks_msec() < deadline:
		await tree.process_frame
	if _reads > 0:
		_reads = 0
		_keep_error("", READBACK_LOST)
		for path: String in _reading:
			_keep_error(path, READBACK_LOST)
		_reading.clear()
	while not _tasks.is_empty():
		var task: int = _tasks.pop_front()
		while not WorkerThreadPool.is_task_completed(task):
			await tree.process_frame
		WorkerThreadPool.wait_for_task_completion(task)
	_mutex.lock()
	unwritten = _errors
	_errors = {}
	_mutex.unlock()
	for message: String in unwritten.values():
		return message
	return ""


## Waits until the frame in progress is drawn, so the capture shows the state the game is in
## now and not the frame before it. Resumes at the start of a frame. Gives up after
## DRAW_WAIT_FRAMES: a game in low-processor mode redraws only when something changed.
func _next_draw() -> void:
	var tree: SceneTree = _host.get_tree()
	var drawn: int = Engine.get_frames_drawn()
	for _frame: int in range(DRAW_WAIT_FRAMES):
		await tree.process_frame
		if Engine.get_frames_drawn() > drawn:
			return


## Reads the frame back, stalling until the GPU hands it over, and writes it: here, or on a
## worker with `on_worker`. Returns "" or the failure.
func _read_now(texture: ViewportTexture, abs_path: String, on_worker: bool) -> String:
	var image: Image = texture.get_image()
	if image == null or image.is_empty():
		return "Could not read the viewport back"
	if on_worker:
		_write_on_worker(image, abs_path)
		return ""
	var error: Error = image.save_png(abs_path)
	if error != OK:
		return "Failed to save screenshot: %s" % error_string(error)
	return ""


## Asks the GPU for the frame without waiting for it; it arrives in _on_read a few frames
## later. False when this renderer has no RenderingDevice (the Compatibility renderer).
func _read_async(viewport_texture: RID, abs_path: String) -> bool:
	var device: RenderingDevice = RenderingServer.get_rendering_device()
	if device == null:
		return false
	_reads += 1
	_reading.append(abs_path)
	RenderingServer.call_on_render_thread(_request.bind(device, viewport_texture, abs_path))
	return true


## Render thread: requests the download of the viewport's texture.
func _request(device: RenderingDevice, viewport_texture: RID, abs_path: String) -> void:
	var texture: RID = RenderingServer.texture_get_rd_texture(viewport_texture)
	var format: RDTextureFormat = device.texture_get_format(texture) if texture.is_valid() else null
	var image_format: int = _image_format_of(format.format) if format != null else -1
	if image_format < 0:
		_on_read.call_deferred(null, abs_path, "viewport format not decoded")
		return
	var width: int = format.width
	var height: int = format.height
	var error: Error = device.texture_get_data_async(
		texture,
		0,
		func(bytes: PackedByteArray) -> void:
			var image: Image = Image.create_from_data(
				width, height, false, image_format as Image.Format, bytes
			)
			_on_read.call_deferred(image, abs_path, "")
	)
	if error != OK:
		_on_read.call_deferred(null, abs_path, "readback error %s" % error_string(error))


## Main thread: an asynchronous download arrived, or failed.
func _on_read(image: Image, abs_path: String, error: String) -> void:
	_reads -= 1
	_reading.erase(abs_path)
	if image == null or not error.is_empty():
		_keep_error(abs_path, "Failed to read screenshot %s: %s" % [abs_path.get_file(), error])
		return
	_write_on_worker(image, abs_path)


func _write_on_worker(image: Image, abs_path: String) -> void:
	# get_image() gives an opaque viewport as RGB; write the downloaded frame the same way.
	var opaque: bool = not _host.get_viewport().transparent_bg
	_tasks.append(
		WorkerThreadPool.add_task(_save.bind(image, abs_path, opaque), false, "DevTools screenshot")
	)


## Worker thread: writes one PNG.
func _save(image: Image, abs_path: String, opaque: bool) -> void:
	var wanted: Image.Format = Image.FORMAT_RGB8 if opaque else Image.FORMAT_RGBA8
	if image.get_format() != wanted:
		image.convert(wanted)
	var error: Error = image.save_png(abs_path)
	if error != OK:
		_keep_error(
			abs_path,
			"Failed to save screenshot %s: %s" % [abs_path.get_file(), error_string(error)]
		)


## Keeps the first failure of each file.
func _keep_error(abs_path: String, message: String) -> void:
	_mutex.lock()
	if not _errors.has(abs_path):
		_errors[abs_path] = message
	_mutex.unlock()


## The Image format of a viewport's RenderingDevice data format, or -1 when not handled.
static func _image_format_of(data_format: RenderingDevice.DataFormat) -> int:
	match data_format:
		RenderingDevice.DATA_FORMAT_R8G8B8A8_UNORM, RenderingDevice.DATA_FORMAT_R8G8B8A8_SRGB:
			return Image.FORMAT_RGBA8
		RenderingDevice.DATA_FORMAT_R16G16B16A16_SFLOAT:
			return Image.FORMAT_RGBAH
		RenderingDevice.DATA_FORMAT_R32G32B32A32_SFLOAT:
			return Image.FORMAT_RGBAF
	return -1
