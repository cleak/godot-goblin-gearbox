extends SceneTree
## Base of the kit's `--script` tools. It runs the tool's _main and turns the
## verdict into the exit code, so a tool that breaks cannot report a pass.
##
##     extends "res://tools/headless_tool.gd"
##
##     func _main(args: PackedStringArray) -> Verdict:
##         ...
##         return Verdict.OK
##
## Why: GDScript does not stop at a script error. The failing function returns
## early (0 for an int) and its callers carry on, so a tool with a bug, or one
## run on a Godot version that lacks a method it calls, skips its checks and
## still prints its OK line. Two guards: no Verdict is 0, and a script error
## raised by the tool's own files ends the run with exit code 3.
##
## Exit codes: 0 OK, 1 PROBLEMS, 2 USAGE (bad arguments, or a kit.json that
## cannot be used), 3 the tool itself failed and its output means nothing.
## On 3 a stdout line starting with TOOL_ERROR comes after anything the tool
## printed, an OK line included.

## What _main returns. NONE is what an aborted _main yields; never return it.
enum Verdict { NONE, OK, PROBLEMS, USAGE }

const ErrorCapture := preload("res://tools/error_capture.gd")
const KitConfig := preload("res://tools/kit_config.gd")

const EXIT_TOOL_ERROR: int = 3
const PROJECT_ROOT: String = "res://"

## Collects every error Godot reports during the run. A tool pins errors on one
## file with `_capture.take()` before and after loading it.
var _capture: ErrorCapture = ErrorCapture.new()
var _exit_requested: bool = false
var _final_exit_code: int = EXIT_TOOL_ERROR


func _initialize() -> void:
	_capture.own_files = _own_files()
	OS.add_logger(_capture)
	var verdict: Verdict = _main(OS.get_cmdline_user_args())
	OS.remove_logger(_capture)
	var exit_code: int = _exit_code(verdict)
	var own_errors: PackedStringArray = _capture.own_errors()
	if not own_errors.is_empty():
		exit_code = EXIT_TOOL_ERROR
		var counts: Array = [_tool_name(), own_errors.size()]
		print("TOOL_ERROR %s: %d script error(s) in the tool itself; ignore its verdict." % counts)
		for error: String in own_errors:
			print("    %s" % error)
	elif exit_code == EXIT_TOOL_ERROR:
		print("TOOL_ERROR %s: ended without a verdict." % _tool_name())
	_exit_requested = true
	_final_exit_code = exit_code
	quit(exit_code)


## Godot only gets here before a quit when _initialize itself broke; without
## this the process would idle forever.
func _process(_delta: float) -> bool:
	if not _exit_requested:
		print("TOOL_ERROR %s: stopped before it could report." % _tool_name())
		quit(EXIT_TOOL_ERROR)
	return true


## The project's autoloads start after _initialize, and one that calls quit()
## replaces the exit code set there (a failed run would exit 0). The last word
## is the tool's.
func _finalize() -> void:
	quit(_final_exit_code)


## The tool. Override it and return a Verdict on every path.
func _main(_args: PackedStringArray) -> Verdict:
	return Verdict.NONE


## Prints why kit.json cannot be used and returns true; false when it is fine.
## A tool that reads the authored source set calls this first and returns
## Verdict.USAGE: scanning with fallback settings would check the wrong files.
func _kit_json_is_broken() -> bool:
	var problem: String = KitConfig.config_problem()
	if problem.is_empty():
		return false
	printerr("%s: %s" % [_tool_name(), problem])
	return true


func _tool_name() -> String:
	var script: Script = get_script()
	return script.resource_path.get_file().get_basename()


## `arg` (res://, project-relative or absolute, either slash) as a res:// path;
## "" when it names a place outside the project. Every path a tool is given goes
## through this before the file is opened: some commands are pre-approved for
## agents with any arguments, and loading a scene or a script runs its code.
static func _project_path(arg: String) -> String:
	var path: String = arg.replace("\\", "/")
	if not path.begins_with(PROJECT_ROOT):
		if path.is_absolute_path():
			# A path outside the project comes back without res://.
			path = ProjectSettings.localize_path(path)
		else:
			path = PROJECT_ROOT + path
	path = path.simplify_path()
	# simplify_path keeps the ".." that climb above the root.
	if not path.begins_with(PROJECT_ROOT) or path.split("/").has(".."):
		return ""
	return path


static func _exit_code(verdict: Verdict) -> int:
	match verdict:
		Verdict.OK:
			return 0
		Verdict.PROBLEMS:
			return 1
		Verdict.USAGE:
			return 2
	return EXIT_TOOL_ERROR


## The running script and the scripts it extends.
func _script_chain() -> PackedStringArray:
	var chain: PackedStringArray = PackedStringArray()
	var script: Script = get_script()
	while script != null:
		chain.append(script.resource_path)
		script = script.get_base_script()
	return chain


## Every script the tool is made of: its chain and the helpers preloaded here.
func _own_files() -> PackedStringArray:
	var files: PackedStringArray = _script_chain()
	var helpers: Array[Script] = [ErrorCapture, KitConfig]
	for helper: Script in helpers:
		files.append(helper.resource_path)
	return files
