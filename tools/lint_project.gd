extends "res://tools/headless_tool.gd"
## Project lint: resource UIDs, and the NodePaths stored in scenes.
##
## Run:  pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd
##       pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd `
##           --user-args [--scene <scene.tscn>]... [--uids-only | --warnings-only]
##                       [--fail-on-warn] [--fix] [--json]
##
## UID checks (errors) cover every authored .tscn/.tres, script and shader:
##   - a scene or resource header carries a valid uid
##   - each [ext_resource] names a file that exists, and carries that file's uid
##   - each .gd/.gdshader/.gdshaderinc has a .uid sidecar holding a valid uid
##   - no .uid sidecar outlives its source file
##   - no two files share a uid (a copied scene keeps the original's)
## UIDs are read from the files themselves (headers, .uid sidecars, .import
## files), so the verdict does not depend on how fresh the .godot cache is; a
## cache that lags behind the files is reported as a note. Binary .scn/.res
## files are not parsed.
##
## NodePath checks (warnings) cover the scenes named by --scene, or all of them:
## every NodePath a scene stores (also in an Array, and `@export var x: Node`)
## must point at a node of that scene, nodes of instanced and inherited scenes
## included. Not judged: an empty path, an absolute one, one that climbs above
## the scene root (it depends on where the scene is used), one into an
## InstancePlaceholder, and String properties. Warnings fail the run only with
## --fail-on-warn. A path that only resolves at run time is silenced by giving
## the node that stores it the metadata `lint_ignore_node_paths = true`
## (Inspector > Add Metadata, or `metadata/lint_ignore_node_paths = true` in
## the .tscn).
##
## --fix writes the repairs that have one right answer: a new uid for a header
## that has none, the right uid on an [ext_resource], the new path of a moved
## file. Every other byte of the file is kept; a file that is not plain UTF-8
## (a byte order mark, invalid bytes) is reported and left alone.
## --json prints the report as one line, `LINT_PROJECT_JSON {...}`, instead of
## the text report. Read that line: Godot and the project's autoloads may print
## others. Keys: ok, uid_errors [{file, line, problem, fix}], scene_errors
## [{scene, problem}], scene_warnings [{scene, node, property, problem}],
## fixed [path], not_in_uid_cache [path], notes [text].
##
## "Authored" follows kit.json source.exclude / source.include (see
## tools/kit_config.gd); a scene given with --scene is always checked.
##
## Exit codes: 0 clean, 1 problems found, 2 bad arguments or a broken kit.json,
## 3 the tool itself failed.

const FLAG_SCENE: String = "--scene"
const FLAG_ALL: String = "--all"
const FLAG_JSON: String = "--json"
const FLAG_FAIL_ON_WARN: String = "--fail-on-warn"
const FLAG_UIDS_ONLY: String = "--uids-only"
const FLAG_WARNINGS_ONLY: String = "--warnings-only"
const FLAG_FIX: String = "--fix"
const RUN: String = "pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd"
const USAGE: String = (
	"usage: %s [--user-args options]\n" % RUN
	+ """  --scene <path>    check the NodePaths of this scene only (repeatable)
  --all             check the NodePaths of every authored scene (the default)
  --uids-only       skip the NodePath checks
  --warnings-only   skip the UID checks
  --fail-on-warn    exit 1 on NodePath warnings too
  --fix             repair missing and stale UIDs in place, then check
  --json            print the report as one line: LINT_PROJECT_JSON {...}"""
)

const TEXT_RESOURCE_EXTENSIONS: PackedStringArray = ["tscn", "tres"]
const SIDECAR_SOURCE_EXTENSIONS: PackedStringArray = ["gd", "gdshader", "gdshaderinc"]
const SCENE_EXTENSIONS: PackedStringArray = ["tscn", "scn"]
const SIDECAR_SUFFIX: String = ".uid"
const IMPORT_SUFFIX: String = ".import"
const IMPORT_COMMAND: String = "pwsh ./tools/godot.ps1 --headless --import"
const CLASS_CACHE: String = "res://.godot/global_script_class_cache.cfg"
const JSON_MARKER: String = "LINT_PROJECT_JSON"
## Instanced scenes are followed this deep when collecting a scene's nodes.
const MAX_INSTANCE_DEPTH: int = 8
const UNIQUE_NAME_PROPERTY: String = "unique_name_in_owner"
## Node metadata (stored as this property) that silences its NodePath warnings.
const IGNORE_PROPERTY: String = "metadata/lint_ignore_node_paths"


class Options:
	extends RefCounted
	var scenes: PackedStringArray = PackedStringArray()
	var all_scenes: bool = false
	var json: bool = false
	var fail_on_warn: bool = false
	var uids_only: bool = false
	var warnings_only: bool = false
	var fix: bool = false
	var help: bool = false
	var errors: PackedStringArray = PackedStringArray()


## The nodes of one scene, instanced and inherited scenes followed. Scene paths
## are relative to the root: "." is the root, "A/B" a grandchild.
class SceneNodes:
	extends RefCounted
	## Scene path -> true.
	var paths: Dictionary = {}
	## Scene path -> scene path of the scene root that owns the node. The root
	## of the checked scene has no entry.
	var owners: Dictionary = {}
	## Owner's scene path -> {unique name -> scene path}.
	var unique_names: Dictionary = {}
	## Scene path -> true for nodes whose children cannot be listed.
	var opaque: Dictionary = {}

	## Where `%unique_name` leads from the node at `from` ("" for nowhere).
	## Godot asks that node (a scene root knows the names in its scene), then
	## the scene root that owns it.
	func unique_path(from: String, unique_name: String) -> String:
		var scopes: PackedStringArray = [from]
		if owners.has(from):
			var owner_path: String = owners[from]
			scopes.append(owner_path)
		for scope: String in scopes:
			var names: Dictionary = unique_names.get(scope, {})
			if names.has(unique_name):
				return names[unique_name]
		return ""


## {"file": String, "line": int (0 = whole file), "problem": String, "fix": String}
var _uid_errors: Array[Dictionary] = []
## {"scene": String, "problem": String}
var _scene_errors: Array[Dictionary] = []
## {"scene": String, "node": String, "property": String, "problem": String}
var _scene_warnings: Array[Dictionary] = []
var _fixed_files: PackedStringArray = PackedStringArray()
## Files --fix could not rewrite; each is reported once.
var _unfixable: Dictionary = {}
## Files whose uid Godot's cache does not hold yet (fixed by an import).
var _unregistered: PackedStringArray = PackedStringArray()
## Hints that are not findings (a stale cache, a project never imported).
var _notes: PackedStringArray = PackedStringArray()
## res:// path -> uid text, "" when the file has none (see _uid_of).
var _uid_by_path: Dictionary = {}
## uid text -> the first authored file that carries it.
var _path_by_uid: Dictionary = {}
## Attribute name -> RegEx matching name="value".
var _attribute_regexes: Dictionary = {}


func _main(args: PackedStringArray) -> Verdict:
	var options: Options = _parse_args(args)
	if options.help:
		print(USAGE)
		return Verdict.OK
	if not options.errors.is_empty():
		for error: String in options.errors:
			printerr("lint_project: %s" % error)
		printerr(USAGE)
		return Verdict.USAGE
	if _kit_json_is_broken():
		return Verdict.USAGE

	var text_resources: PackedStringArray = KitConfig.source_files(TEXT_RESOURCE_EXTENSIONS)
	var sidecar_sources: PackedStringArray = KitConfig.source_files(SIDECAR_SOURCE_EXTENSIONS)
	var scenes: PackedStringArray = options.scenes
	if scenes.is_empty():
		scenes = KitConfig.source_files(SCENE_EXTENSIONS)
	if text_resources.is_empty() and sidecar_sources.is_empty() and scenes.is_empty():
		printerr("lint_project: no authored scenes, resources or scripts found.")
		printerr("Check kit.json source.exclude.")
		return Verdict.PROBLEMS

	if not options.warnings_only:
		if options.fix:
			_fix_uids(text_resources, sidecar_sources)
		_check_uids(text_resources, sidecar_sources)
	if not options.uids_only:
		for scene_path: String in scenes:
			_check_scene(scene_path)
		# Without an import, imported assets (textures, audio) and class_name
		# types do not load, and every scene that uses one fails here.
		if not _scene_errors.is_empty() and not FileAccess.file_exists(CLASS_CACHE):
			var never_imported: String = "this project was never imported, so scenes that use"
			never_imported += " imported assets or class_name types do not load. Run: %s"
			_notes.append(never_imported % IMPORT_COMMAND)
		if not _scene_warnings.is_empty():
			var silence: String = "a NodePath to a node that only exists at run time: give the node"
			silence += " that stores it the metadata %s = true."
			_notes.append(silence % IGNORE_PROPERTY.get_file())

	var failed: bool = not _uid_errors.is_empty() or not _scene_errors.is_empty()
	if options.fail_on_warn and not _scene_warnings.is_empty():
		failed = true
	if options.json:
		print("%s %s" % [JSON_MARKER, JSON.stringify(_report(not failed))])
	else:
		_print_report(options, text_resources.size(), sidecar_sources.size(), scenes.size())
		var error_count: int = _uid_errors.size() + _scene_errors.size()
		var counts: String = "errors=%d warnings=%d" % [error_count, _scene_warnings.size()]
		print("LINT_PROJECT_%s %s" % ["FAILED" if failed else "OK", counts])
	return Verdict.PROBLEMS if failed else Verdict.OK


# --- Arguments ---


func _parse_args(args: PackedStringArray) -> Options:
	var options: Options = Options.new()
	var index: int = 0
	while index < args.size():
		var arg: String = args[index]
		index += 1
		match arg:
			FLAG_SCENE:
				if index >= args.size():
					options.errors.append("%s needs a scene path" % FLAG_SCENE)
				else:
					_add_scene(options, args[index])
					index += 1
			FLAG_ALL:
				options.all_scenes = true
			FLAG_JSON:
				options.json = true
			FLAG_FAIL_ON_WARN:
				options.fail_on_warn = true
			FLAG_UIDS_ONLY:
				options.uids_only = true
			FLAG_WARNINGS_ONLY:
				options.warnings_only = true
			FLAG_FIX:
				options.fix = true
			"--help", "-h":
				options.help = true
			_:
				options.errors.append("unknown argument: %s" % arg)
	var clashes: Array[PackedStringArray] = []
	if options.warnings_only and options.uids_only:
		clashes.append(PackedStringArray([FLAG_UIDS_ONLY, FLAG_WARNINGS_ONLY]))
	if options.warnings_only and options.fix:
		clashes.append(PackedStringArray([FLAG_FIX, FLAG_WARNINGS_ONLY]))
	if options.all_scenes and not options.scenes.is_empty():
		clashes.append(PackedStringArray([FLAG_ALL, FLAG_SCENE]))
	for clash: PackedStringArray in clashes:
		options.errors.append("%s and %s cannot be combined" % [clash[0], clash[1]])
	return options


func _add_scene(options: Options, arg: String) -> void:
	var scene_path: String = _project_path(arg)
	if scene_path.is_empty():
		options.errors.append("scene is outside the project: %s" % arg)
	elif not SCENE_EXTENSIONS.has(scene_path.get_extension().to_lower()):
		options.errors.append("not a scene (.tscn or .scn): %s" % arg)
	elif not FileAccess.file_exists(scene_path):
		options.errors.append("scene not found: %s" % scene_path)
	else:
		options.scenes.append(scene_path)


# --- Report ---


func _report(ok: bool) -> Dictionary:
	return {
		"ok": ok,
		"uid_errors": _uid_errors,
		"scene_errors": _scene_errors,
		"scene_warnings": _scene_warnings,
		"fixed": Array(_fixed_files),
		"not_in_uid_cache": Array(_unregistered),
		"notes": Array(_notes),
	}


func _print_report(options: Options, resources: int, sources: int, scenes: int) -> void:
	for fixed_file: String in _fixed_files:
		print("fixed %s" % fixed_file)
	if not options.warnings_only:
		for error: Dictionary in _uid_errors:
			var line: int = error["line"]
			var where: String = str(error["file"]) + (":%d" % line if line > 0 else "")
			print("%s: %s Fix: %s" % [where, error["problem"], error["fix"]])
		if _uid_errors.is_empty():
			var checked: Array = [resources, sources]
			print("UIDs: OK (%d scenes and resources, %d scripts and shaders)" % checked)
		else:
			print("UIDs: %d problem(s)" % _uid_errors.size())
			if not options.fix:
				# The fixes above say "--fix"; this is the whole command.
				print("%s:  %s --user-args %s" % [FLAG_FIX, RUN, FLAG_FIX])
	if not options.uids_only:
		for error: Dictionary in _scene_errors:
			print("%s: %s" % [error["scene"], error["problem"]])
		for warning: Dictionary in _scene_warnings:
			var parts: Array = [
				warning["scene"], warning["node"], warning["property"], warning["problem"]
			]
			print("%s: warning: node %s: %s %s" % parts)
		if _scene_errors.is_empty() and _scene_warnings.is_empty():
			print("Scene NodePaths: OK (%d scenes)" % scenes)
		else:
			var totals: Array = [_scene_errors.size(), _scene_warnings.size(), scenes]
			print("Scene NodePaths: %d error(s), %d warning(s) in %d scenes" % totals)
	for note: String in _notes:
		print("note: %s" % note)


func _uid_error(file: String, line: int, problem: String, fix: String) -> void:
	_uid_errors.append({"file": file, "line": line, "problem": problem, "fix": fix})


# --- UID checks ---


func _check_uids(text_resources: PackedStringArray, sidecar_sources: PackedStringArray) -> void:
	_index_uids(text_resources, sidecar_sources, true)
	for path: String in text_resources:
		var lines: PackedStringArray = FileAccess.get_file_as_string(path).split("\n")
		for index: int in range(lines.size()):
			if not lines[index].begins_with("[ext_resource "):
				continue
			var diagnosis: Dictionary = _diagnose_ext_resource(path, lines[index])
			if not diagnosis.is_empty():
				var problem: String = diagnosis["problem"]
				var fix: String = diagnosis["fix"]
				_uid_error(path, index + 1, problem, fix)
	for sidecar: String in KitConfig.source_files(["uid"]):
		if not FileAccess.file_exists(sidecar.trim_suffix(SIDECAR_SUFFIX)):
			_uid_error(
				sidecar,
				0,
				"its source file %s is gone." % sidecar.trim_suffix(SIDECAR_SUFFIX).get_file(),
				"delete it, or move it next to the file it belongs to."
			)
	_uid_errors.sort_custom(_error_comes_first)
	# Godot resolves uid:// through .godot/uid_cache.bin, which only an import
	# refreshes. A stale cache is not a defect of the files, so it is a note.
	for uid: String in _path_by_uid:
		var id: int = ResourceUID.text_to_id(uid)
		var path: String = _path_by_uid[uid]
		if not ResourceUID.has_id(id) or ResourceUID.get_id_path(id) != path:
			_unregistered.append(path)
	if not _unregistered.is_empty():
		var stale: Array = [_unregistered.size(), _unregistered[0], IMPORT_COMMAND]
		_notes.append("Godot's uid cache lacks %d file(s), e.g. %s. Refresh it: %s" % stale)


static func _error_comes_first(first: Dictionary, second: Dictionary) -> bool:
	var first_file: String = first["file"]
	var second_file: String = second["file"]
	if first_file != second_file:
		return first_file < second_file
	var first_line: int = first["line"]
	var second_line: int = second["line"]
	return first_line < second_line


## Reads the uid of every authored file into _uid_by_path and _path_by_uid.
## With `report`, records what is wrong with the uid a file carries itself.
func _index_uids(
	text_resources: PackedStringArray, sidecar_sources: PackedStringArray, report: bool
) -> void:
	_uid_by_path.clear()
	_path_by_uid.clear()
	for path: String in text_resources:
		var header: String = _first_line(path)
		var written_uid: String = _attribute(header, "uid")
		var uid: String = _canonical_uid(written_uid)
		if not _is_resource_header(header):
			if report:
				_uid_error(
					path,
					1,
					"does not start with a [gd_scene] or [gd_resource] header.",
					"restore the header; Godot cannot load the file without it."
				)
		elif written_uid.is_empty():
			if report:
				_uid_error(
					path,
					1,
					"the header has no uid, so references break when the file moves.",
					"run this tool with %s, or save the file from the editor." % FLAG_FIX
				)
		elif uid.is_empty():
			if report:
				_uid_error(
					path,
					1,
					'the header uid "%s" is not a valid uid.' % written_uid,
					"run this tool with %s to give it a new one." % FLAG_FIX
				)
		else:
			_claim_uid(uid, path, 1, report)
	for path: String in sidecar_sources:
		var sidecar: String = path + SIDECAR_SUFFIX
		if not FileAccess.file_exists(sidecar):
			if report:
				_uid_error(
					path, 0, "has no %s sidecar." % sidecar.get_file(), "run: %s" % IMPORT_COMMAND
				)
			continue
		var written_uid: String = FileAccess.get_file_as_string(sidecar).strip_edges()
		var uid: String = _canonical_uid(written_uid)
		if not uid.is_empty():
			_claim_uid(uid, path, 0, report)
		elif report:
			_uid_error(
				sidecar,
				0,
				'holds "%s", which is not a valid uid.' % written_uid,
				"delete it and run: %s" % IMPORT_COMMAND
			)


func _claim_uid(uid: String, path: String, line: int, report: bool) -> void:
	if not _path_by_uid.has(uid):
		_path_by_uid[uid] = path
		return
	if not report:
		return
	var fix: String = "delete its %s and run: %s" % [SIDECAR_SUFFIX, IMPORT_COMMAND]
	if TEXT_RESOURCE_EXTENSIONS.has(path.get_extension().to_lower()):
		fix = "if this file is the copy, delete uid=... from its header and run %s." % FLAG_FIX
	_uid_error(path, line, "shares %s with %s." % [uid, _path_by_uid[uid]], fix)


## What is wrong with one [ext_resource] line of the file `from_path`: {} when
## nothing is, else {"problem", "fix"} plus, when the repair is known, the
## "uid" or "path" the line should carry.
func _diagnose_ext_resource(from_path: String, line: String) -> Dictionary:
	var ref_path: String = _attribute(line, "path")
	var written_uid: String = _attribute(line, "uid")
	var ref_uid: String = _canonical_uid(written_uid)
	if ref_path.is_empty():
		return {"problem": "ext_resource has no path.", "fix": 'add path="res://...".'}
	if not ref_path.contains("://"):
		# Godot reads a relative path against the folder of the referring file.
		ref_path = from_path.get_base_dir().path_join(ref_path).simplify_path()
	if not FileAccess.file_exists(ref_path):
		var moved_to: String = _path_of_uid(ref_uid)
		if moved_to.is_empty():
			return {
				"problem": "ext_resource %s does not exist." % ref_path,
				"fix": "restore the file, or point path (and uid) at its replacement.",
			}
		var moved: Array = [ref_path, ref_uid, moved_to]
		return {
			"problem": "ext_resource %s does not exist; %s is now %s." % moved,
			"fix": 'set path="%s", or run with %s.' % [moved_to, FLAG_FIX],
			"path": moved_to,
		}
	var actual_uid: String = _uid_of(ref_path)
	# Two empty uids only agree when the line really carries none.
	if actual_uid == ref_uid and (written_uid.is_empty() or not ref_uid.is_empty()):
		return {}
	if actual_uid.is_empty():
		var fix: String = "remove the uid, or run: %s" % IMPORT_COMMAND
		if TEXT_RESOURCE_EXTENSIONS.has(ref_path.get_extension().to_lower()):
			fix = "run with %s to give %s a uid and use it here." % [FLAG_FIX, ref_path]
		return {
			"problem": "ext_resource has uid %s, but %s has no uid." % [written_uid, ref_path],
			"fix": fix,
		}
	var mismatch: Array = [written_uid, ref_path, actual_uid]
	var problem: String = "ext_resource has uid %s, but %s is %s." % mismatch
	if written_uid.is_empty():
		problem = "ext_resource has no uid; %s is %s." % [ref_path, actual_uid]
	return {
		"problem": problem,
		"fix": 'set uid="%s", or run with %s.' % [actual_uid, FLAG_FIX],
		"uid": actual_uid,
	}


## The uid `path` carries, read from the file that defines it; "" for none.
func _uid_of(path: String) -> String:
	if _uid_by_path.has(path):
		return _uid_by_path[path]
	var uid: String = ""
	if TEXT_RESOURCE_EXTENSIONS.has(path.get_extension().to_lower()):
		uid = _attribute(_first_line(path), "uid")
	elif FileAccess.file_exists(path + SIDECAR_SUFFIX):
		uid = FileAccess.get_file_as_string(path + SIDECAR_SUFFIX).strip_edges()
	elif FileAccess.file_exists(path + IMPORT_SUFFIX):
		uid = _attribute(FileAccess.get_file_as_string(path + IMPORT_SUFFIX), "uid")
	else:
		# Anything else (binary resources): what Godot's own cache knows.
		var id: int = ResourceLoader.get_resource_uid(path)
		if id != ResourceUID.INVALID_ID:
			uid = ResourceUID.id_to_text(id)
	uid = _canonical_uid(uid)
	_uid_by_path[path] = uid
	return uid


## Where the file with `uid` (canonical) lives now ("" when unknown or gone).
func _path_of_uid(uid: String) -> String:
	if _path_by_uid.has(uid):
		return _path_by_uid[uid]
	if uid.is_empty():
		return ""
	var id: int = ResourceUID.text_to_id(uid)
	if not ResourceUID.has_id(id):
		return ""
	var path: String = ResourceUID.get_id_path(id)
	return path if FileAccess.file_exists(path) else ""


# --- UID repair (--fix) ---


func _fix_uids(text_resources: PackedStringArray, sidecar_sources: PackedStringArray) -> void:
	# Headers first: a reference can only take a uid its target already has.
	_index_uids(text_resources, sidecar_sources, false)
	for path: String in text_resources:
		var lines: PackedStringArray = FileAccess.get_file_as_string(path).split("\n")
		var header: String = lines[0].trim_suffix("\r")
		var has_uid: bool = not _canonical_uid(_attribute(header, "uid")).is_empty()
		if has_uid or not _is_resource_header(header):
			continue
		lines[0] = _with_attribute(lines[0], "uid", _new_uid(path), "")
		_write_lines(path, lines)
	_index_uids(text_resources, sidecar_sources, false)
	for path: String in text_resources:
		var lines: PackedStringArray = FileAccess.get_file_as_string(path).split("\n")
		var changed: bool = false
		for index: int in range(lines.size()):
			if not lines[index].begins_with("[ext_resource "):
				continue
			# A moved file needs two passes: its path first, then its uid.
			for _pass: int in range(2):
				var diagnosis: Dictionary = _diagnose_ext_resource(path, lines[index])
				var key: String = "path" if diagnosis.has("path") else "uid"
				if not diagnosis.has(key):
					break
				var value: String = diagnosis[key]
				var repaired: String = _with_attribute(lines[index], key, value, "type")
				changed = changed or repaired != lines[index]
				lines[index] = repaired
		if changed:
			_write_lines(path, lines)


func _new_uid(path: String) -> String:
	var uid: String = ResourceUID.id_to_text(ResourceUID.create_id())
	while _path_by_uid.has(uid):
		uid = ResourceUID.id_to_text(ResourceUID.create_id())
	_path_by_uid[uid] = path
	return uid


## `line` (a [tag ...] line, line ending included) with name="value" set:
## replaced when present, else added after the attribute `after`, else last.
func _with_attribute(line: String, attribute: String, value: String, after: String) -> String:
	var pair: String = '%s="%s"' % [attribute, value]
	var existing: RegExMatch = _attribute_regex(attribute).search(line)
	if existing != null:
		return line.substr(0, existing.get_start()) + pair + line.substr(existing.get_end())
	var anchor: RegExMatch = null if after.is_empty() else _attribute_regex(after).search(line)
	var at: int = line.rfind("]") if anchor == null else anchor.get_end()
	if at < 0:
		return line
	return "%s %s%s" % [line.substr(0, at).rstrip(" "), pair, line.substr(at)]


## Writes `lines` (the edited text of `path`) back, or records why it cannot.
func _write_lines(path: String, lines: PackedStringArray) -> void:
	if _unfixable.has(path):
		return
	# Text read and written back is only byte-identical for plain UTF-8: a byte
	# order mark is dropped on the way, an invalid byte becomes U+FFFD.
	var bytes: PackedByteArray = FileAccess.get_file_as_bytes(path)
	if bytes.get_string_from_utf8().to_utf8_buffer() != bytes:
		_unfixable[path] = true
		var problem: String = "is not plain UTF-8 (a byte order mark or an invalid byte)."
		_uid_error(path, 0, "%s %s leaves it alone." % [problem, FLAG_FIX], "re-save it as UTF-8.")
		return
	var file: FileAccess = FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		_unfixable[path] = true
		var reason: String = error_string(FileAccess.get_open_error())
		_uid_error(path, 0, "cannot be written (%s)." % reason, "make the file writable.")
		return
	file.store_string("\n".join(lines))
	file.close()
	if not _fixed_files.has(path):
		_fixed_files.append(path)


# --- Text helpers ---


static func _is_resource_header(line: String) -> bool:
	return line.begins_with("[gd_scene") or line.begins_with("[gd_resource")


## `uid` spelled the way Godot writes it, so two spellings of one id compare
## equal; "" when it is not a uid.
static func _canonical_uid(uid: String) -> String:
	if not uid.begins_with("uid://") or uid.length() <= "uid://".length():
		return ""
	var id: int = ResourceUID.text_to_id(uid)
	return "" if id == ResourceUID.INVALID_ID else ResourceUID.id_to_text(id)


static func _first_line(path: String) -> String:
	var file: FileAccess = FileAccess.open(path, FileAccess.READ)
	if file == null:
		return ""
	var line: String = file.get_line()
	file.close()
	return line


## The value of name="value" in `text` ("" when absent).
func _attribute(text: String, attribute: String) -> String:
	var found: RegExMatch = _attribute_regex(attribute).search(text)
	return "" if found == null else found.get_string(1)


func _attribute_regex(attribute: String) -> RegEx:
	if not _attribute_regexes.has(attribute):
		_attribute_regexes[attribute] = RegEx.create_from_string('\\b%s="([^"]*)"' % attribute)
	return _attribute_regexes[attribute]


# --- Scene NodePath checks ---


func _check_scene(scene_path: String) -> void:
	_capture.take()
	var resource: Resource = ResourceLoader.load(scene_path, "PackedScene")
	var load_errors: PackedStringArray = _capture.take()
	if not resource is PackedScene and load_errors.is_empty():
		load_errors.append("not a scene")
	if not load_errors.is_empty():
		var problem: String = "does not load: %s" % "; ".join(load_errors)
		_scene_errors.append({"scene": scene_path, "problem": problem})
		return
	var scene: PackedScene = resource
	var state: SceneState = scene.get_state()
	var found: SceneNodes = SceneNodes.new()
	_collect_nodes(state, ".", 0, found)
	for index: int in range(state.get_node_count()):
		if _property(state, index, IGNORE_PROPERTY) == true:
			continue
		var node_path: String = _scene_path(state.get_node_path(index))
		for property_index: int in range(state.get_node_property_count(index)):
			var property: String = state.get_node_property_name(index, property_index)
			var value: Variant = state.get_node_property_value(index, property_index)
			for candidate: NodePath in _node_paths_in(value):
				var problem: String = _node_path_problem(node_path, candidate, found)
				if problem.is_empty():
					continue
				var warning: Dictionary = {
					"scene": scene_path,
					"node": node_path,
					"property": property,
					"problem": problem,
				}
				_scene_warnings.append(warning)


## Records every node of `state` in `found`, following instanced scenes. `top`
## is the scene path of the node whose scene `state` is; it owns the nodes
## `state` adds.
func _collect_nodes(state: SceneState, top: String, depth: int, found: SceneNodes) -> void:
	for index: int in range(state.get_node_count()):
		var node_path: String = _join_scene_paths(top, _scene_path(state.get_node_path(index)))
		found.paths[node_path] = true
		# A scene lists an instance before the nodes it overrides inside it, so
		# the first owner recorded for a node is the scene that created it.
		if node_path != top and not found.owners.has(node_path):
			found.owners[node_path] = top
		if _property(state, index, UNIQUE_NAME_PROPERTY) == true and found.owners.has(node_path):
			var owner_path: String = found.owners[node_path]
			if not found.unique_names.has(owner_path):
				found.unique_names[owner_path] = {}
			var names: Dictionary = found.unique_names[owner_path]
			names[String(state.get_node_name(index))] = node_path
		var instance: PackedScene = state.get_node_instance(index)
		if not state.get_node_instance_placeholder(index).is_empty():
			found.opaque[node_path] = true
		elif instance != null and depth >= MAX_INSTANCE_DEPTH:
			found.opaque[node_path] = true
		elif instance != null:
			# The instance owns the nodes its scene brings. For an inherited
			# scene the instance is the root itself, so the base scene's nodes
			# get the same owner as its own.
			_collect_nodes(instance.get_state(), node_path, depth + 1, found)


## The value `state` stores for `property` on node `index` (null for none).
static func _property(state: SceneState, index: int, property: String) -> Variant:
	for property_index: int in range(state.get_node_property_count(index)):
		if state.get_node_property_name(index, property_index) == property:
			return state.get_node_property_value(index, property_index)
	return null


## The NodePaths a stored value holds: a NodePath, or an Array of them. A
## String is never read as one, whatever its property is called: `save_path`
## and FileDialog.current_path hold folders.
static func _node_paths_in(value: Variant) -> Array[NodePath]:
	var found: Array[NodePath] = []
	match typeof(value):
		TYPE_NODE_PATH:
			var node_path: NodePath = value
			found.append(node_path)
		TYPE_ARRAY:
			var items: Array = value
			for item: Variant in items:
				if typeof(item) == TYPE_NODE_PATH:
					var item_path: NodePath = item
					found.append(item_path)
	return found


## Why `node_path`, stored on the node at `owner_path`, is wrong ("" when it
## resolves, is not set, or cannot be judged from this scene alone).
static func _node_path_problem(
	owner_path: String, node_path: NodePath, found: SceneNodes
) -> String:
	# Empty is how "not set" is stored (MeshInstance3D.skeleton, a cleared export).
	if node_path.is_empty() or node_path.is_absolute():
		return ""
	var parts: PackedStringArray = _scene_path_parts(owner_path)
	for index: int in range(node_path.get_name_count()):
		var part: String = node_path.get_name(index)
		if part == ".":
			continue
		if part == "..":
			if parts.is_empty():
				return ""
			parts.remove_at(parts.size() - 1)
		elif part.begins_with("%"):
			var unique_path: String = found.unique_path(_scene_path_of(parts), part.substr(1))
			if unique_path.is_empty():
				return "= %s: no node of this scene has that unique name." % node_path
			parts = _scene_path_parts(unique_path)
		else:
			parts.append(part)
		if found.opaque.has(_scene_path_of(parts)):
			return ""
	if found.paths.has(_scene_path_of(parts)):
		return ""
	return "= %s: no node at %s in this scene." % [node_path, _scene_path_of(parts)]


static func _scene_path(node_path: NodePath) -> String:
	var text: String = str(node_path).trim_prefix("./")
	return "." if text.is_empty() else text


static func _scene_path_parts(scene_path: String) -> PackedStringArray:
	return PackedStringArray() if scene_path == "." else scene_path.split("/")


static func _scene_path_of(parts: PackedStringArray) -> String:
	return "." if parts.is_empty() else "/".join(parts)


static func _join_scene_paths(prefix: String, scene_path: String) -> String:
	if scene_path == ".":
		return prefix
	return scene_path if prefix == "." else "%s/%s" % [prefix, scene_path]
