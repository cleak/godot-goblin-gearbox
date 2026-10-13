extends Logger
## Collects the errors Godot reports while a headless tool loads or compiles a
## file, so the tool can pin them on that file. Preload it (no class_name, so it
## adds nothing to the project's global namespace):
##
##     const ErrorCapture := preload("res://tools/error_capture.gd")
##
##     var capture: ErrorCapture = ErrorCapture.new()
##     OS.add_logger(capture)
##     capture.take()
##     ... load or compile one file ...
##     var errors: PackedStringArray = capture.take()
##     OS.remove_logger(capture)
##
## Tools built on tools/headless_tool.gd already have one, `_capture`.
## Warnings are not collected.

## Script files whose errors mean the tool itself broke: see own_errors().
## Set it before the capture is added as a logger.
var own_files: PackedStringArray = PackedStringArray()

var _errors: PackedStringArray = PackedStringArray()
var _own_errors: PackedStringArray = PackedStringArray()
# Godot calls a logger from whichever thread reports the error.
var _mutex: Mutex = Mutex.new()


func _log_error(
	_function: String,
	file: String,
	line: int,
	code: String,
	rationale: String,
	_editor_notify: bool,
	error_type: int,
	_script_backtraces: Array[ScriptBacktrace]
) -> void:
	if error_type == ERROR_TYPE_WARNING:
		return
	# Engine checks put the readable text in `rationale` and the failed
	# condition in `code`; script and shader errors only fill `code`.
	var message: String = code if rationale.is_empty() else rationale
	# Keep where the project is wrong (a res:// file, or a line of the shader
	# being compiled), not which line of the engine's source noticed.
	if file.begins_with("res://"):
		message = "%s (%s:%d)" % [message, file, line]
	elif file.is_empty():
		message = "%s (line %d)" % [message, line]
	_mutex.lock()
	_errors.append(message)
	if error_type == ERROR_TYPE_SCRIPT and own_files.has(file):
		_own_errors.append(message)
	_mutex.unlock()


## The errors collected since the last call; clears the list.
func take() -> PackedStringArray:
	_mutex.lock()
	var taken: PackedStringArray = _errors
	_errors = PackedStringArray()
	_mutex.unlock()
	return taken


## Every script error raised so far by one of `own_files`; take() does not
## clear these.
func own_errors() -> PackedStringArray:
	_mutex.lock()
	var copy: PackedStringArray = _own_errors.duplicate()
	_mutex.unlock()
	return copy
