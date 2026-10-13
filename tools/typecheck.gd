extends "res://tools/headless_tool.gd"
## One-process strict type check, driven by tools/typecheck.ps1.
##
## Loading a script compiles it with the project's [debug] warnings promoted to
## errors, the same analysis `--check-only` runs, but one Godot start serves
## every file instead of one start per file (~0.5 s each). The errors each load
## reports are collected, so every file gets its own verdict.
##
## Run: godot --headless --script res://tools/typecheck.gd -- <list>
## where <list> is a file of res:// paths, one per line. Prints one
## `TC ok|unchecked|FAIL <path>` line per file, a `TC_ERR` line per FAIL error,
## then `TC_DONE checked=<n> failed=<n> unchecked=<n>`. A path outside the
## project is refused (exit 2) and no file is loaded.
##
## "unchecked": the file compiled, but in a folder where Godot applies no
## warning as an error, so nothing strict was checked. That fails the run unless
## kit.json says gdscript.strict = false.
##
## Exit codes: 0 clean, 1 a file failed (or was unchecked in a strict project),
## 2 bad arguments or a kit.json that cannot be used, 3 this tool itself broke
## (see tools/headless_tool.gd).

## A script that breaks several strict warnings at once. Compiled as if it sat
## in a folder, it shows whether any warning is an error there. Godot applies
## none under res://addons unless project.godot says otherwise ([debug]
## gdscript/warnings/directory_rules), and a file that loads with the warnings
## off has not been checked: it is reported as "unchecked", never as "ok".
const CANARY_NAME: String = "kit_typecheck_canary.gd"
const CANARY_SOURCE: String = "func probe(value):\n\tvar half = 7 / 2\n\treturn value\n"

var _strict_folders: Dictionary[String, bool] = {}


func _main(args: PackedStringArray) -> Verdict:
	if args.size() != 1:
		printerr("usage: --script res://tools/typecheck.gd -- <path-list-file>")
		return Verdict.USAGE
	if _kit_json_is_broken():
		return Verdict.USAGE
	var list_text: String = FileAccess.get_file_as_string(args[0])
	if list_text.is_empty():
		printerr("typecheck: empty or unreadable path list: %s" % args[0])
		return Verdict.USAGE
	# Loading a script runs its static initialisers: nothing is loaded before
	# every path is known to be the project's.
	var paths: PackedStringArray = _listed_paths(list_text)
	if paths.has(""):
		return Verdict.USAGE
	return _check(paths)


## Compiles each of `paths` and prints its verdict, then the totals.
func _check(paths: PackedStringArray) -> Verdict:
	# Reloading the script that is running, or one it extends, would swap out the
	# code under its feet. They compiled to get here, under whatever warnings
	# apply in their folder, so their verdict comes from the folder alone.
	var running: PackedStringArray = _script_chain()
	var counts: Dictionary[String, int] = {"ok": 0, "unchecked": 0, "FAIL": 0}
	for path: String in paths:
		var errors: PackedStringArray = PackedStringArray()
		if not running.has(path):
			errors = _compile(path)
		var verdict: String = "ok"
		if not errors.is_empty():
			verdict = "FAIL"
		elif not _is_strict_folder(path.get_base_dir()):
			verdict = "unchecked"
		counts[verdict] += 1
		print("TC %s %s" % [verdict, path])
		for error: String in errors:
			print("TC_ERR %s" % error)
	var totals: Array = [counts["ok"], counts["FAIL"], counts["unchecked"]]
	print("TC_DONE checked=%d failed=%d unchecked=%d" % totals)
	if counts["FAIL"] > 0:
		return Verdict.PROBLEMS
	if counts["unchecked"] > 0 and KitConfig.gdscript_strict():
		return Verdict.PROBLEMS
	return Verdict.OK


## The paths `list_text` names as res:// paths, "" for one outside the project
## (each is reported).
func _listed_paths(list_text: String) -> PackedStringArray:
	var paths: PackedStringArray = PackedStringArray()
	for line: String in list_text.split("\n", false):
		var listed: String = line.strip_edges()
		if listed.is_empty():
			continue
		var path: String = _project_path(listed)
		if path.is_empty():
			printerr("typecheck: path is outside the project: %s" % listed)
		paths.append(path)
	return paths


## The errors Godot reports while it compiles `path`; empty when it is clean.
func _compile(path: String) -> PackedStringArray:
	_capture.take()
	# CACHE_MODE_IGNORE: Godot reports a broken script once, where it is first
	# loaded (as something another script preloads or extends); a cached copy
	# would then pass when its own turn comes. Not IGNORE_DEEP: that recompiles
	# what the file depends on too, this tool's own base script included.
	var resource: Resource = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
	var errors: PackedStringArray = _capture.take()
	if not resource is GDScript and errors.is_empty():
		errors.append("not a GDScript resource")
	return errors


## True when a script in `folder` is compiled with at least one strict warning
## as an error: the canary is rejected there.
func _is_strict_folder(folder: String) -> bool:
	if not _strict_folders.has(folder):
		var canary: GDScript = GDScript.new()
		canary.source_code = CANARY_SOURCE
		canary.resource_path = folder.path_join(CANARY_NAME)
		# The canary's errors are the answer, not news: keep them off stderr.
		var printing: bool = Engine.print_error_messages
		Engine.print_error_messages = false
		_strict_folders[folder] = canary.reload() != OK
		Engine.print_error_messages = printing
		_capture.take()
	return _strict_folders[folder]
