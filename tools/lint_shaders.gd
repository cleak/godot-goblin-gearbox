extends "res://tools/headless_tool.gd"
## Shader lint: runs every authored .gdshader through Godot's shader compiler
## and fails on any error it reports.
##
## Run:  pwsh ./tools/godot.ps1 --headless --script res://tools/lint_shaders.gd
##       pwsh ./tools/godot.ps1 --headless --script res://tools/lint_shaders.gd `
##           --user-args res://shaders/water.gdshader [more.gdshader ...]
##
## What it proves: the shader parses, type-checks and only uses built-ins its
## shader_type has, with every #include expanded. What it does not: --headless
## has no GPU, so no backend code is generated and no driver compiles anything.
## A shader that passes here can still fail on a real device; watch the log of
## a rendered run for that.
##
## One check is the tool's own: a `global uniform` must exist somewhere, in
## project.godot [shader_globals] or in an authored script that calls
## RenderingServer.global_shader_parameter_add("its_name", ...). Godot compiles
## and runs a shader whose global is missing without a message, GPU or not.
##
## .gdshaderinc files are not shaders on their own; each is checked through
## the .gdshader files that #include it. One that no checked shader includes
## is listed as unchecked (not a failure). Text shaders only: a VisualShader
## is a .tres, which validate_all.gd loads.
##
## With no paths it checks the authored source set (kit.json source.exclude /
## source.include, see tools/kit_config.gd); a path given by name is always
## checked. A project without shaders passes.
##
## Exit codes: 0 clean, 1 a shader failed, 2 bad arguments or a broken
## kit.json, 3 the tool itself failed.

const SHADER_EXTENSION: String = "gdshader"
const INCLUDE_EXTENSION: String = "gdshaderinc"
## Appended to the code under test. Godot lists a shader's uniforms only when
## the whole shader compiled, so finding this one afterwards proves it did,
## even where the renderer rejects a shader without logging an error.
const PROBE_UNIFORM: String = "_kit_lint_probe"
const GLOBALS_SETTING_PREFIX: String = "shader_globals/"
const USAGE: String = (
	"usage: pwsh ./tools/godot.ps1 --headless --script res://tools/lint_shaders.gd"
	+ " [--user-args <shader.gdshader>...]"
)

var _include_regex: RegEx = RegEx.create_from_string('(?m)^[ \\t]*#include[ \\t]+"([^"\\n]+)"')
var _shader_type_regex: RegEx = RegEx.create_from_string("(?m)^\\s*shader_type\\s+\\w+\\s*;")
## A string, a line comment or a block comment, whichever starts first.
var _comment_regex: RegEx = RegEx.create_from_string(
	'"(?:[^"\\\\\\n]|\\\\.)*"|//[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|\\z)'
)
var _global_uniform_regex: RegEx = RegEx.create_from_string(
	"\\bglobal\\s+uniform\\s+(?:(?:lowp|mediump|highp)\\s+)?\\w+\\s+(\\w+)"
)
var _added_global_regex: RegEx = RegEx.create_from_string(
	'\\bglobal_shader_parameter_add\\(\\s*&?"(\\w+)"'
)
## res:// path of every include file reached from a checked shader.
var _included: Dictionary = {}
## Name -> true for each shader global an authored script adds; null until read.
var _globals_added_by_code: Variant = null


func _main(args: PackedStringArray) -> Verdict:
	if args.has("--help") or args.has("-h"):
		print(USAGE)
		return Verdict.OK
	if _kit_json_is_broken():
		return Verdict.USAGE
	var whole_project: bool = args.is_empty()
	var shaders: PackedStringArray = PackedStringArray()
	if whole_project:
		shaders = KitConfig.source_files([SHADER_EXTENSION])
	else:
		for arg: String in args:
			var problem: String = _argument_problem(arg)
			if not problem.is_empty():
				printerr("lint_shaders: %s" % problem)
				printerr(USAGE)
				return Verdict.USAGE
			shaders.append(_project_path(arg))

	if DisplayServer.get_name() == "headless":
		print("Headless: shaders are parsed and type-checked, not compiled for a GPU.")
	if shaders.is_empty():
		print("LINT_SHADERS_OK checked=0 (no .%s files in the authored source)" % SHADER_EXTENSION)
		return Verdict.OK
	return _lint_all(shaders, whole_project)


func _lint_all(shaders: PackedStringArray, whole_project: bool) -> Verdict:
	var failed: int = 0
	for shader_path: String in shaders:
		var errors: PackedStringArray = _lint_shader(shader_path)
		if errors.is_empty():
			print("ok   %s" % shader_path)
			continue
		failed += 1
		print("FAIL %s" % shader_path)
		for error: String in errors:
			print("    %s" % error)
	if whole_project:
		_report_unchecked_includes()

	if failed > 0:
		print("LINT_SHADERS_FAILED failed=%d checked=%d" % [failed, shaders.size()])
		return Verdict.PROBLEMS
	print("LINT_SHADERS_OK checked=%d" % shaders.size())
	return Verdict.OK


## Why `arg` cannot be linted ("" when it can).
func _argument_problem(arg: String) -> String:
	if arg.begins_with("-"):
		return "unknown option: %s" % arg
	var path: String = _project_path(arg)
	if path.is_empty():
		return "path is outside the project: %s" % arg
	if path.get_extension().to_lower() == INCLUDE_EXTENSION:
		return "%s is an include file; pass a shader that includes it" % path
	if path.get_extension().to_lower() != SHADER_EXTENSION:
		return "not a .%s file: %s" % [SHADER_EXTENSION, arg]
	if not FileAccess.file_exists(path):
		return "file not found: %s" % path
	return ""


## The errors of one shader (empty when it is clean).
func _lint_shader(shader_path: String) -> PackedStringArray:
	var code: String = FileAccess.get_file_as_string(shader_path)
	if code.strip_edges().is_empty():
		return PackedStringArray(["file is empty or cannot be read"])
	var missing_includes: PackedStringArray = PackedStringArray()
	# The shader and each include it reaches, comments removed.
	var sources: PackedStringArray = PackedStringArray()
	_collect_includes(shader_path, code, missing_includes, sources, {})

	_capture.take()
	var shader: Shader = Shader.new()
	# Lets a relative #include resolve against the shader's own folder, without
	# replacing whatever the resource cache holds for this path.
	shader.set_path_cache(shader_path)
	shader.code = "%s\nuniform float %s;\n" % [code, PROBE_UNIFORM]
	var compiled: bool = false
	for parameter: Dictionary in RenderingServer.get_shader_parameter_list(shader.get_rid()):
		if parameter.get("name") == PROBE_UNIFORM:
			compiled = true
	var errors: PackedStringArray = _capture.take()
	if compiled:
		errors.append_array(_undeclared_globals(sources))
		return errors
	# Godot reports a missing include as "Unknown character #35", which names
	# neither the cause nor the file, so the missing files come first. They are
	# only listed for a shader that failed: an #include in an inactive #ifdef
	# block is never read.
	var reasons: PackedStringArray = missing_includes.duplicate()
	reasons.append_array(errors)
	# Without a shader_type the renderers either stay silent or blame themselves.
	if _shader_type_regex.search(sources[0]) == null:
		reasons.insert(0, "no shader_type line; start the file with e.g. shader_type spatial;")
	elif reasons.is_empty():
		reasons.append("Godot rejected the shader without an error message")
	return reasons


## Follows the #include lines of `code` (the text of `from_path`), recording
## each include file in _included, each missing one in `missing`, and the
## comment-free text of every file read in `sources` (`code` first).
func _collect_includes(
	from_path: String,
	code: String,
	missing: PackedStringArray,
	sources: PackedStringArray,
	visiting: Dictionary
) -> void:
	visiting[from_path] = true
	var bare: String = _without_comments(code)
	sources.append(bare)
	for found: RegExMatch in _include_regex.search_all(bare):
		var target: String = found.get_string(1)
		if not target.begins_with("res://"):
			target = from_path.get_base_dir().path_join(target).simplify_path()
		if not FileAccess.file_exists(target):
			missing.append("#include not found: %s (in %s)" % [target, from_path])
			continue
		_included[target] = true
		if not visiting.has(target):
			var included_code: String = FileAccess.get_file_as_string(target)
			_collect_includes(target, included_code, missing, sources, visiting)


## `code` with its comments removed; line breaks and strings are kept.
func _without_comments(code: String) -> String:
	var bare: String = ""
	var at: int = 0
	for found: RegExMatch in _comment_regex.search_all(code):
		var text: String = found.get_string()
		if text.begins_with('"'):
			continue
		bare += code.substr(at, found.get_start() - at) + "\n".repeat(text.count("\n"))
		at = found.get_end()
	return bare + code.substr(at)


## One error per `global uniform` that nothing provides: project.godot
## [shader_globals] does not declare it and no authored script adds it.
func _undeclared_globals(sources: PackedStringArray) -> PackedStringArray:
	var errors: PackedStringArray = PackedStringArray()
	for source: String in sources:
		for found: RegExMatch in _global_uniform_regex.search_all(source):
			var uniform_name: String = found.get_string(1)
			if ProjectSettings.has_setting(GLOBALS_SETTING_PREFIX + uniform_name):
				continue
			if _is_added_by_code(uniform_name):
				continue
			var declare: String = "Project Settings > Globals > Shader Globals"
			errors.append(
				"global uniform %s is not declared; add it in %s" % [uniform_name, declare]
			)
	return errors


## True when an authored script adds the shader global `uniform_name` by its
## literal name. The scripts are only read once a shader needs the answer.
func _is_added_by_code(uniform_name: String) -> bool:
	if _globals_added_by_code == null:
		var added: Dictionary = {}
		for script_path: String in KitConfig.source_files(["gd"]):
			var code: String = FileAccess.get_file_as_string(script_path)
			for found: RegExMatch in _added_global_regex.search_all(code):
				added[found.get_string(1)] = true
		_globals_added_by_code = added
	var names: Dictionary = _globals_added_by_code
	return names.has(uniform_name)


func _report_unchecked_includes() -> void:
	for include_path: String in KitConfig.source_files([INCLUDE_EXTENSION]):
		if not _included.has(include_path):
			print("note %s: no checked shader includes it, so it was not checked" % include_path)
