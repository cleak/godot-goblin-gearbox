extends RefCounted
## Shared configuration for the kit's headless GDScript tools: kit.json and the
## authored source set. Preload it (no class_name, so it adds nothing to the
## project's global namespace):
##
##     const KitConfig := preload("res://tools/kit_config.gd")
##
## "Authored source" is every matching file under res://, skipping folders whose
## name starts with ".", folders that hold a .gdignore or a project.godot (a
## nested project), directory links, and the folders listed in kit.json
## source.exclude; the folders in source.include are then added back.
## tools/kit_config.py and tools/kit_config.ps1 read kit.json the same way, with
## the same error texts, and apply the same rules; change the three together
## (tools/tests/test_kit_config_parity.py compares them). The keys and their
## defaults are listed at the top of tools/kit_config.py.
##
## GDScript has no exceptions, so a kit.json that cannot be used is reported two
## ways: config_problem() returns the one-line reason for a tool to print, and
## every other function here logs that reason as an error and returns nothing
## (no files, an empty value). Neither falls back to the defaults.

const CONFIG_NAME: String = "kit.json"
const ROOT: String = "res://"
const DEFAULT_GODOT_VERSION: String = "4.7.2"
# The official Godot downloads the kit knows: kit.json pins one checksum for
# each, as godot.<build>_sha512.
const GODOT_BUILDS: PackedStringArray = [
	"windows_x86_64", "linux_x86_64", "linux_arm64", "macos_universal"
]
const DEFAULT_EXCLUDE: PackedStringArray = ["addons", "artifacts", "docs"]
const DEFAULT_TEST_ROOT: String = "test"
const BYTE_ORDER_MARK: PackedByteArray = [0xEF, 0xBB, 0xBF]
# JSON's white space besides the line feed, and its number grammar.
const JSON_WHITE_SPACE: String = " \t\r"
const NUMBER_START: String = "-0123456789"
const NUMBER_CHARACTERS: String = "0123456789+-.eE"
const JSON_NUMBER: String = "^-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?$"


## Why kit.json cannot be used, as one line ("" when it is fine or absent).
## A tool that reads the source set calls this first and stops on an answer.
static func config_problem(root: String = ROOT) -> String:
	var problem: String = read(root)["error"]
	return problem


## source.exclude as project-relative folders (no res://, no slashes at the ends).
static func source_exclude() -> PackedStringArray:
	var folders: PackedStringArray = _usable(read())["source_exclude"]
	return folders


## source.include as project-relative folders.
static func source_include() -> PackedStringArray:
	var folders: PackedStringArray = _usable(read())["source_include"]
	return folders


## tests.root as a project-relative folder ("" when kit.json cannot be used).
static func test_root() -> String:
	var folder: String = _usable(read())["test_root"]
	return folder


## gdscript.strict: false when the project treats the strict warnings as advice.
static func gdscript_strict() -> bool:
	var strict: bool = _usable(read())["gdscript_strict"]
	return strict


## Every authored file under `root` with one of `extensions` (no dot: "gd",
## "tscn"), as sorted paths that start with `root`.
static func source_files(extensions: PackedStringArray, root: String = ROOT) -> PackedStringArray:
	var config: Dictionary = _usable(read(root))
	var paths: PackedStringArray = PackedStringArray()
	if not str(config["error"]).is_empty():
		return paths
	var base: String = _folder_path(root)
	var exclude: PackedStringArray = config["source_exclude"]
	var include: PackedStringArray = config["source_include"]
	var found: Dictionary[String, bool] = {}
	_walk(base, base, extensions, exclude, found)
	for included: String in include:
		_walk(base, base + included, extensions, PackedStringArray(), found)
	for path: String in found:
		paths.append(path)
	paths.sort()
	return paths


## True when `directory` (under `root`) is a folder the source walk skips. A
## directory link is skipped by the walk itself.
static func is_skipped_dir(
	directory: String, exclude: PackedStringArray, root: String = ROOT
) -> bool:
	var name: String = directory.trim_suffix("/").get_file()
	if name.begins_with("."):
		return true
	if FileAccess.file_exists(directory.path_join(".gdignore")):
		return true
	if FileAccess.file_exists(directory.path_join("project.godot")):
		return true
	var relative: String = directory.trim_prefix(_folder_path(root)).trim_suffix("/").to_lower()
	for excluded: String in exclude:
		if relative == excluded.to_lower():
			return true
	return false


## `text` as a project-relative folder with forward slashes: "res://test",
## "test/", "\test" and "./test" all give "test". "" when it names the project
## root itself or a place outside the project (too many "..", a drive letter).
static func project_folder(text: String) -> String:
	var parts: PackedStringArray = PackedStringArray()
	for part: String in text.replace("\\", "/").trim_prefix(ROOT).split("/"):
		if part.is_empty() or part == ".":
			continue
		if part == "..":
			if parts.is_empty():
				return ""
			parts.remove_at(parts.size() - 1)
		elif part.contains(":"):
			return ""
		else:
			parts.append(part)
	return "/".join(parts)


## kit.json under `root` as a Dictionary: "error" (one line, "" when the file is
## fine or absent) and the values godot_version, godot_minor, godot_sha512 (one
## entry for each of GODOT_BUILDS), godot_search_roots, gdunit4_version,
## gdunit4_sha256, source_exclude, source_include, test_root, gdscript_strict.
## With an error the values are not to be used.
static func read(root: String = ROOT) -> Dictionary:
	var checksums: Dictionary[String, String] = {}
	for build: String in GODOT_BUILDS:
		checksums[build] = ""
	var config: Dictionary = {
		"error": "",
		"godot_version": DEFAULT_GODOT_VERSION,
		"godot_sha512": checksums,
		"godot_search_roots": PackedStringArray(),
		"gdunit4_version": "",
		"gdunit4_sha256": "",
		"source_exclude": DEFAULT_EXCLUDE.duplicate(),
		"source_include": PackedStringArray(),
		"test_root": DEFAULT_TEST_ROOT,
		"gdscript_strict": true,
	}
	var path: String = _folder_path(root) + CONFIG_NAME
	if FileAccess.file_exists(path):
		var problems: PackedStringArray = PackedStringArray()
		var raw: Dictionary = _parse(path, problems)
		if problems.is_empty():
			_fill(config, raw, _folder_path(root), problems)
		if not problems.is_empty():
			config["error"] = problems[0]
	# "4.7.2" -> "4.7": the major.minor a Godot binary must report.
	var version: String = config["godot_version"]
	var parts: PackedStringArray = version.split(".")
	config["godot_minor"] = version
	if parts.size() >= 2:
		config["godot_minor"] = "%s.%s" % [parts[0], parts[1]]
	return config


## `config` when it can be used. Otherwise its problem is logged as an error and
## the answer holds empty values, so the caller checks nothing rather than the
## wrong files.
static func _usable(config: Dictionary) -> Dictionary:
	var problem: String = config["error"]
	if problem.is_empty():
		return config
	push_error(problem)
	return {
		"error": problem,
		"source_exclude": PackedStringArray(),
		"source_include": PackedStringArray(),
		"test_root": "",
		"gdscript_strict": true,
	}


static func _folder_path(root: String) -> String:
	if root.ends_with("/"):
		return root
	return root + "/"


## The object kit.json holds; a reason lands in `problems` when it holds none.
static func _parse(path: String, problems: PackedStringArray) -> Dictionary:
	var shown: String = ProjectSettings.globalize_path(path)
	var bytes: PackedByteArray = FileAccess.get_file_as_bytes(path)
	# Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes a byte order mark.
	if bytes.slice(0, 3) == BYTE_ORDER_MARK:
		bytes = bytes.slice(3)
	var text: String = bytes.get_string_from_utf8()
	var json: JSON = JSON.new()
	var reason: String = ""
	if text.to_utf8_buffer() != bytes:
		reason = "the file is not UTF-8 text"
	elif json.parse(text) != OK:
		reason = "%s (line %d)" % [json.get_error_message(), json.get_error_line() + 1]
	else:
		reason = _beyond_json(text)
	if not reason.is_empty():
		problems.append("%s is not valid JSON: %s (%s)" % [CONFIG_NAME, reason, shown])
		return {}
	if not json.data is Dictionary:
		problems.append("%s must hold a JSON object (%s)" % [CONFIG_NAME, shown])
		return {}
	var raw: Dictionary = json.data
	return raw


## What Godot's parser took in `text` that JSON does not allow, with its line
## ("" when there is nothing). The Python and PowerShell tools that read the same
## file keep to the standard, so this loader must refuse what they refuse: a
## comma before a closing bracket, a control character (inside a string, or as
## white space outside one), and a number such as 01 or 1.
static func _beyond_json(text: String) -> String:
	var in_string: bool = false
	var escaped: bool = false
	var after_comma: bool = false
	var line: int = 1
	var index: int = 0
	while index < text.length():
		var character: String = text[index]
		var code: int = text.unicode_at(index)
		index += 1
		if in_string:
			if code < 0x20:
				return "a control character in a string (line %d)" % line
			if escaped:
				escaped = false
			elif character == "\\":
				escaped = true
			elif character == '"':
				in_string = false
		elif character == "\n":
			line += 1
		elif JSON_WHITE_SPACE.contains(character):
			pass
		elif code < 0x20:
			return "a control character (line %d)" % line
		elif character == '"':
			in_string = true
			after_comma = false
		elif character == ",":
			after_comma = true
		elif character == "}" or character == "]":
			if after_comma:
				return "a comma before a closing bracket (line %d)" % line
		else:
			after_comma = false
			if NUMBER_START.contains(character):
				var start: int = index - 1
				while index < text.length() and NUMBER_CHARACTERS.contains(text[index]):
					index += 1
				var number: String = text.substr(start, index - start)
				if RegEx.create_from_string(JSON_NUMBER).search(number) == null:
					return "a number JSON does not allow: %s (line %d)" % [number, line]
	return ""


## Checks and copies the values of `raw` into `config`, in the order
## tools/kit_config.py does, so the first problem is the same one.
static func _fill(
	config: Dictionary, raw: Dictionary, base: String, problems: PackedStringArray
) -> void:
	var godot: Dictionary = _section(raw, "godot", problems)
	var gdunit4: Dictionary = _section(raw, "gdunit4", problems)
	var source: Dictionary = _section(raw, "source", problems)
	var tests: Dictionary = _section(raw, "tests", problems)
	var gdscript: Dictionary = _section(raw, "gdscript", problems)
	if not problems.is_empty():
		return
	config["godot_version"] = _text(godot, "godot", "version", DEFAULT_GODOT_VERSION, problems)
	var checksums: Dictionary[String, String] = config["godot_sha512"]
	for build: String in GODOT_BUILDS:
		checksums[build] = _text(godot, "godot", build + "_sha512", "", problems)
	config["godot_search_roots"] = _texts(
		godot, "godot", "search_roots", PackedStringArray(), problems
	)
	config["gdunit4_version"] = _text(gdunit4, "gdunit4", "version", "", problems)
	config["gdunit4_sha256"] = _text(gdunit4, "gdunit4", "source_archive_sha256", "", problems)
	var exclude: PackedStringArray = _folders(
		_texts(source, "source", "exclude", DEFAULT_EXCLUDE, problems),
		"source",
		"exclude",
		problems
	)
	var include: PackedStringArray = _folders(
		_texts(source, "source", "include", PackedStringArray(), problems),
		"source",
		"include",
		problems
	)
	config["source_exclude"] = exclude
	config["source_include"] = include
	var written_root: String = _text(tests, "tests", "root", DEFAULT_TEST_ROOT, problems)
	config["test_root"] = project_folder(written_root)
	if project_folder(written_root).is_empty():
		var named: Array = [CONFIG_NAME, written_root]
		problems.append('%s: "tests.root" must be a folder inside the project, not "%s"' % named)
	config["gdscript_strict"] = _flag(gdscript, "gdscript", "strict", true, problems)
	for folder: String in include:
		# A typo here would silently drop the folder from every check.
		if not DirAccess.dir_exists_absolute(base + folder):
			var missing: Array = [CONFIG_NAME, folder]
			problems.append('%s: "source.include" names a folder that does not exist: %s' % missing)


static func _section(raw: Dictionary, name: String, problems: PackedStringArray) -> Dictionary:
	var value: Variant = raw.get(name)
	if value == null:
		return {}
	if not value is Dictionary:
		problems.append('%s: "%s" must be an object' % [CONFIG_NAME, name])
		return {}
	var section: Dictionary = value
	return section


static func _text(
	section: Dictionary, name: String, key: String, fallback: String, problems: PackedStringArray
) -> String:
	var value: Variant = section.get(key)
	if value == null:
		return fallback
	if not value is String:
		# A bare number is refused too: 4.10 would arrive as 4.1.
		problems.append('%s: "%s.%s" must be a string' % [CONFIG_NAME, name, key])
		return fallback
	var text: String = value
	if text.is_empty():
		return fallback
	return text


static func _texts(
	section: Dictionary,
	name: String,
	key: String,
	fallback: PackedStringArray,
	problems: PackedStringArray
) -> PackedStringArray:
	var value: Variant = section.get(key)
	if value == null:
		return fallback
	var problem: String = '%s: "%s.%s" must be a list of strings' % [CONFIG_NAME, name, key]
	if not value is Array:
		problems.append(problem)
		return fallback
	var items: Array = value
	var texts: PackedStringArray = PackedStringArray()
	for item: Variant in items:
		if not item is String:
			problems.append(problem)
			return fallback
		var text: String = item
		texts.append(text)
	return texts


static func _flag(
	section: Dictionary, name: String, key: String, fallback: bool, problems: PackedStringArray
) -> bool:
	var value: Variant = section.get(key)
	if value == null:
		return fallback
	if not value is bool:
		problems.append('%s: "%s.%s" must be true or false' % [CONFIG_NAME, name, key])
		return fallback
	var flag: bool = value
	return flag


static func _folders(
	names: PackedStringArray, name: String, key: String, problems: PackedStringArray
) -> PackedStringArray:
	var folders: PackedStringArray = PackedStringArray()
	for entry: String in names:
		var folder: String = project_folder(entry)
		if folder.is_empty():
			var named: Array = [CONFIG_NAME, name, key, entry]
			problems.append('%s: "%s.%s" must list folders inside the project, not "%s"' % named)
		folders.append(folder)
	return folders


static func _walk(
	base: String,
	directory_path: String,
	extensions: PackedStringArray,
	exclude: PackedStringArray,
	found: Dictionary[String, bool]
) -> void:
	var directory: DirAccess = DirAccess.open(directory_path)
	if directory == null:
		return
	directory.include_hidden = true
	directory.list_dir_begin()
	var entry: String = directory.get_next()
	while not entry.is_empty():
		var child: String = directory_path.path_join(entry)
		if directory.current_is_dir():
			# A link is not followed: one that points at an ancestor never ends.
			if not directory.is_link(child) and not is_skipped_dir(child, exclude, base):
				_walk(base, child, extensions, exclude, found)
		elif extensions.has(entry.get_extension().to_lower()):
			found[child] = true
		entry = directory.get_next()
	directory.list_dir_end()
