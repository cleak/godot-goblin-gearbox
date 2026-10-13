extends "res://tools/headless_tool.gd"
## Load check: loads every authored script, scene and resource once and fails
## on anything Godot reports while doing so (a parse or type error, a strict
## warning promoted to an error in project.godot [debug], a missing
## ext_resource, a broken sub-resource), and on a scene node whose type the
## engine does not have.
##
## Run:  pwsh ./tools/godot.ps1 --headless --script res://tools/validate_all.gd
##
## Checks .gd .tscn .tres .scn .res in the authored source set (kit.json
## source.exclude / source.include, see tools/kit_config.gd). Scenes are loaded,
## not instantiated, so _ready code does not run; loading a script runs its
## static initialisers.
##
## Prints one FAIL block per bad file, then VALIDATE_ALL_OK loaded=<n> or
## VALIDATE_ALL_FAILED failures=<n> loaded=<n>.
## Exit codes: 0 everything loads, 1 a file failed (or none was found),
## 2 bad arguments or a broken kit.json, 3 the tool itself failed.

const EXTENSIONS: PackedStringArray = ["gd", "tscn", "tres", "scn", "res"]
const CLASS_CACHE: String = "res://.godot/global_script_class_cache.cfg"
const IMPORT_COMMAND: String = "pwsh ./tools/godot.ps1 --headless --import"
const GODOT: String = "pwsh ./tools/godot.ps1 --headless"
const USAGE: String = "usage: %s --script res://tools/validate_all.gd" % GODOT


func _main(args: PackedStringArray) -> Verdict:
	if args.has("--help") or args.has("-h"):
		print(USAGE)
		return Verdict.OK
	if not args.is_empty():
		printerr("validate_all: unexpected argument: %s" % args[0])
		printerr(USAGE)
		return Verdict.USAGE
	if _kit_json_is_broken():
		return Verdict.USAGE

	# Reloading the script that is running, or one it extends, would swap out
	# the code under its feet; they compiled under the same strict settings to
	# get here.
	var running: PackedStringArray = _script_chain()
	var loaded: int = 0
	var failures: int = 0
	for path: String in KitConfig.source_files(EXTENSIONS):
		if running.has(path):
			continue
		var errors: PackedStringArray = _load_errors(path)
		if errors.is_empty():
			loaded += 1
			continue
		failures += 1
		print("FAIL %s" % path)
		for error: String in errors:
			print("    %s" % error)

	if loaded + failures == 0:
		printerr("validate_all: no authored %s files found." % "/".join(EXTENSIONS))
		printerr("Check kit.json source.exclude.")
		return Verdict.PROBLEMS
	if failures == 0:
		print("VALIDATE_ALL_OK loaded=%d" % loaded)
		return Verdict.OK
	if not FileAccess.file_exists(CLASS_CACHE):
		print("Note: this project was never imported, so class_name types do not resolve.")
		print("Run: %s" % IMPORT_COMMAND)
	print("VALIDATE_ALL_FAILED failures=%d loaded=%d" % [failures, loaded])
	return Verdict.PROBLEMS


## Why `path` does not load cleanly (empty when it does).
func _load_errors(path: String) -> PackedStringArray:
	_capture.take()
	# CACHE_MODE_IGNORE: Godot reports a broken script once, where it is first
	# loaded (by an autoload, or by a scene that uses it); a cached copy would
	# then pass when its own turn comes.
	var resource: Resource = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
	var errors: PackedStringArray = _capture.take()
	# A script that fails its strict compile still comes back as a (broken)
	# resource, so the null check alone would pass it.
	if resource == null and errors.is_empty():
		errors.append("Godot returned nothing and reported no error")
	if resource is PackedScene:
		var scene: PackedScene = resource
		errors.append_array(_unknown_node_types(scene))
	return errors


## One line per node of `scene` whose type is not an engine class. Godot loads
## such a scene without a word and only fails when it is instantiated.
static func _unknown_node_types(scene: PackedScene) -> PackedStringArray:
	var problems: PackedStringArray = PackedStringArray()
	var state: SceneState = scene.get_state()
	for index: int in range(state.get_node_count()):
		# Empty for an instanced scene and for an override of one of its nodes.
		var type: StringName = state.get_node_type(index)
		if type.is_empty() or ClassDB.class_exists(type):
			continue
		var node: String = str(state.get_node_path(index)).trim_prefix("./")
		if index == 0:
			node = state.get_node_name(index)
		problems.append("node %s: there is no node type %s" % [node, type])
	return problems
