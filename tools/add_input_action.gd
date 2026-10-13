extends "res://tools/headless_tool.gd"
## Adds an input action to project.godot [input], or more events to one it has.
##
##   pwsh ./tools/godot.ps1 --headless --script res://tools/add_input_action.gd --user-args
##       jump --key Space --key W --joy-button a --joy-axis left_x:-1 --mouse-button left
##   ... --user-args --list     (the project's actions, one line of arguments each)
##
##   --key NAME           A Godot key name: W, Space, Escape, Left, F1, "Kp 1". Modifiers
##                        go in front: Ctrl+S, Shift+Alt+F1. Bound as a physical key
##                        (the same place on any layout), like the editor's Input Map.
##   --joy-button NAME    a, b, x, y, back, guide, start, left_stick, right_stick,
##                        left_shoulder, right_shoulder, dpad_up, dpad_down, dpad_left,
##                        dpad_right, misc1, paddle1..paddle4, touchpad; a name the
##                        Input Map shows ("Sony Cross", "D-pad Up"); or its number.
##   --joy-axis NAME:DIR  left_x, left_y, right_x, right_y, trigger_left, trigger_right;
##                        a name the Input Map shows ("Left Stick X-Axis"); or its
##                        number. DIR is -1 or 1.
##   --mouse-button NAME  left, right, middle, wheel_up, wheel_down, wheel_left,
##                        wheel_right, xbutton1, xbutton2, or its number.
##   --deadzone VALUE     0 to 1, for a new action (default 0.2).
##
## It only adds: a missing action is created, an event the action lacks is
## appended, every event already there is kept. To drop an event or change a
## deadzone use the editor (Project Settings > Input Map). Running the same
## command again changes nothing.
##
## Only the [input] section is edited, as text, in the form the editor writes
## (device -1, physical keycodes). ProjectSettings.save() is not used: it
## rewrites the whole file and drops every key that equals its engine default.
## The rest of project.godot stays byte for byte as it was.
##
## Close the editor first, or reload the project afterwards (Project > Reload
## Current Project): an open editor writes its own copy back on its next save.
##
## Exit codes: 0 the action holds every event asked for, 1 project.godot could
## not be updated (nothing is written), 2 bad arguments, 3 the tool itself failed.

const PROJECT_FILE: String = "res://project.godot"
const SECTION: String = "input"
const SETTING_PREFIX: String = "input/"
## Godot's own deadzone for a new action.
const DEFAULT_DEADZONE: float = 0.2
## The device id the editor's Input Map writes: the event matches every device.
const ALL_DEVICES: int = -1

const FLAG_LIST: String = "--list"
const FLAG_KEY: String = "--key"
const FLAG_JOY_BUTTON: String = "--joy-button"
const FLAG_JOY_AXIS: String = "--joy-axis"
const FLAG_MOUSE_BUTTON: String = "--mouse-button"
const FLAG_DEADZONE: String = "--deadzone"
const VALUE_FLAGS: PackedStringArray = [
	FLAG_KEY, FLAG_JOY_BUTTON, FLAG_JOY_AXIS, FLAG_MOUSE_BUTTON, FLAG_DEADZONE
]
const MODIFIERS: PackedStringArray = ["Shift", "Alt", "Ctrl", "Meta"]
## Short names, by JoyButton value.
const JOY_BUTTON_NAMES: PackedStringArray = [
	"a",
	"b",
	"x",
	"y",
	"back",
	"guide",
	"start",
	"left_stick",
	"right_stick",
	"left_shoulder",
	"right_shoulder",
	"dpad_up",
	"dpad_down",
	"dpad_left",
	"dpad_right",
	"misc1",
	"paddle1",
	"paddle2",
	"paddle3",
	"paddle4",
	"touchpad",
]
## Short names, by JoyAxis value.
const JOY_AXIS_NAMES: PackedStringArray = [
	"left_x", "left_y", "right_x", "right_y", "trigger_left", "trigger_right"
]
## Short names, by MouseButton value; there is no button 0.
const MOUSE_BUTTON_NAMES: PackedStringArray = [
	"",
	"left",
	"right",
	"middle",
	"wheel_up",
	"wheel_down",
	"wheel_left",
	"wheel_right",
	"xbutton1",
	"xbutton2",
]
const RUN: String = "pwsh ./tools/godot.ps1 --headless --script res://tools/add_input_action.gd"
const USAGE: String = (
	"usage: %s --user-args <action>" % RUN
	+ " [--key NAME]... [--joy-button NAME]... [--joy-axis NAME:DIR]..."
	+ " [--mouse-button NAME]... [--deadzone VALUE]\n"
	+ "       %s --user-args --list" % RUN
)

var _action_name_regex: RegEx = RegEx.create_from_string("^[A-Za-z0-9_.-]+$")
var _action: String = ""
var _deadzone: float = DEFAULT_DEADZONE
var _deadzone_given: bool = false
## The events asked for, in argument order.
var _wanted: Array[InputEvent] = []


func _main(args: PackedStringArray) -> Verdict:
	if args.has("--help") or args.has("-h"):
		print(USAGE)
		return Verdict.OK
	if args.has(FLAG_LIST):
		if args.size() > 1:
			return _usage_error("%s takes no other argument" % FLAG_LIST)
		return _list()
	var problem: String = _parse(args)
	if not problem.is_empty():
		return _usage_error(problem)
	return _update()


func _usage_error(problem: String) -> Verdict:
	printerr("add_input_action: %s; nothing was written." % problem)
	printerr(USAGE)
	return Verdict.USAGE


## Reads the arguments into _action, _deadzone and _wanted; returns what is
## wrong with them ("" when they can be used).
func _parse(args: PackedStringArray) -> String:
	var index: int = 0
	while index < args.size():
		var arg: String = args[index]
		index += 1
		if not arg.begins_with("--"):
			if not _action.is_empty():
				return "unexpected argument: %s (one action per run)" % arg
			_action = arg
			continue
		if not VALUE_FLAGS.has(arg):
			return "unknown argument: %s" % arg
		if index >= args.size():
			return "%s needs a value" % arg
		var problem: String = _take(arg, args[index])
		index += 1
		if not problem.is_empty():
			return problem
	return _request_problem()


func _request_problem() -> String:
	if _action.is_empty():
		return "name the action to add"
	if _action_name_regex.search(_action) == null:
		return "an action name is letters, digits, _ . and -, not %s" % JSON.stringify(_action)
	if _wanted.is_empty():
		var flags: String = ", ".join(VALUE_FLAGS.slice(0, 4))
		return "%s binds nothing; give it at least one of %s" % [_action, flags]
	return ""


func _take(flag: String, value: String) -> String:
	match flag:
		FLAG_KEY:
			return _take_key(value)
		FLAG_JOY_BUTTON:
			return _take_joy_button(value)
		FLAG_JOY_AXIS:
			return _take_joy_axis(value)
		FLAG_MOUSE_BUTTON:
			return _take_mouse_button(value)
	if not value.is_valid_float() or value.to_float() < 0.0 or value.to_float() > 1.0:
		return "%s %s is not a number from 0 to 1" % [FLAG_DEADZONE, JSON.stringify(value)]
	_deadzone = value.to_float()
	_deadzone_given = true
	return ""


func _take_key(value: String) -> String:
	var parts: PackedStringArray = value.split("+")
	for part: String in parts:
		# "+" and "Ctrl++" split into empty names.
		if parts.size() > 1 and part.strip_edges().is_empty():
			var asked: String = JSON.stringify(value)
			return "%s %s: write the plus key as Plus (Ctrl+Plus)" % [FLAG_KEY, asked]
	var key: InputEventKey = InputEventKey.new()
	key.device = ALL_DEVICES
	for index: int in parts.size() - 1:
		var modifier: String = parts[index].strip_edges().to_lower()
		match modifier:
			"shift":
				key.shift_pressed = true
			"alt":
				key.alt_pressed = true
			"ctrl":
				key.ctrl_pressed = true
			"meta":
				key.meta_pressed = true
			_:
				var known: String = ", ".join(MODIFIERS)
				return "%s %s: %s is not a modifier (%s)" % [FLAG_KEY, value, parts[index], known]
	# One name at a time: given "Meta+A" the engine drops what it does not know.
	var code: Key = OS.find_keycode_from_string(parts[parts.size() - 1].strip_edges())
	if code == KEY_NONE:
		var examples: String = 'W, Space, Escape, Left, F1, "Kp 1", Ctrl+S'
		return "%s %s is not a Godot key name (%s)" % [FLAG_KEY, JSON.stringify(value), examples]
	key.physical_keycode = code
	_wanted.append(key)
	return ""


func _take_joy_button(value: String) -> String:
	var shown: Array[PackedStringArray] = []
	for index: int in JOY_BUTTON_SDL_MAX:
		var probe: InputEventJoypadButton = InputEventJoypadButton.new()
		probe.button_index = index as JoyButton
		shown.append(_shown_names(probe))
	var found: int = _resolve(value, JOY_BUTTON_NAMES, shown, JOY_BUTTON_MAX)
	if found < 0:
		return _unknown(FLAG_JOY_BUTTON, value, JOY_BUTTON_NAMES, "Sony Cross", JOY_BUTTON_MAX)
	var button: InputEventJoypadButton = InputEventJoypadButton.new()
	button.device = ALL_DEVICES
	button.button_index = found as JoyButton
	_wanted.append(button)
	return ""


func _take_joy_axis(value: String) -> String:
	var colon: int = value.rfind(":")
	var direction: String = value.substr(colon + 1).strip_edges().trim_prefix("+")
	if colon < 1 or not (direction in ["1", "-1", "1.0", "-1.0"]):
		var asked: String = JSON.stringify(value)
		return (
			"%s %s: write the axis and its direction as left_x:-1 or left_x:1"
			% [FLAG_JOY_AXIS, asked]
		)
	var shown: Array[PackedStringArray] = []
	for index: int in JOY_AXIS_MAX:
		var probe: InputEventJoypadMotion = InputEventJoypadMotion.new()
		probe.axis = index as JoyAxis
		shown.append(_shown_names(probe))
	var axis_name: String = value.substr(0, colon)
	var found: int = _resolve(axis_name, JOY_AXIS_NAMES, shown, JOY_AXIS_MAX)
	if found < 0:
		return _unknown(FLAG_JOY_AXIS, axis_name, JOY_AXIS_NAMES, "Left Stick X-Axis", JOY_AXIS_MAX)
	var motion: InputEventJoypadMotion = InputEventJoypadMotion.new()
	motion.device = ALL_DEVICES
	motion.axis = found as JoyAxis
	motion.axis_value = direction.to_float()
	_wanted.append(motion)
	return ""


func _take_mouse_button(value: String) -> String:
	var shown: Array[PackedStringArray] = []
	for index: int in MOUSE_BUTTON_NAMES.size():
		var probe: InputEventMouseButton = InputEventMouseButton.new()
		probe.button_index = index as MouseButton
		shown.append(_shown_names(probe))
	var limit: int = MOUSE_BUTTON_NAMES.size()
	var found: int = _resolve(value, MOUSE_BUTTON_NAMES, shown, limit)
	if found < 1:
		return _unknown(FLAG_MOUSE_BUTTON, value, MOUSE_BUTTON_NAMES, "Left Mouse Button", limit)
	var button: InputEventMouseButton = InputEventMouseButton.new()
	button.device = ALL_DEVICES
	button.button_index = found as MouseButton
	_wanted.append(button)
	return ""


static func _unknown(
	flag: String, value: String, names: PackedStringArray, example: String, limit: int
) -> String:
	var short: PackedStringArray = PackedStringArray()
	for known: String in names:
		if not known.is_empty():
			short.append(known)
	var asked: String = JSON.stringify(value)
	var listed: String = ", ".join(short)
	return (
		'%s %s is not one of: %s; a name the Input Map shows ("%s"); or a number below %d'
		% [flag, asked, listed, example, limit]
	)


## The names the editor's Input Map shows for `event`: the list in brackets at
## the end of a joypad event's text, or the whole text of a mouse button.
static func _shown_names(event: InputEvent) -> PackedStringArray:
	var text: String = event.as_text()
	var open: int = text.find("(")
	var close: int = text.rfind(")")
	if open < 0 or close < open:
		return PackedStringArray([text])
	return text.substr(open + 1, close - open - 1).split(", ", false)


## The index `value` names: a number below `limit`, the position of a short name
## in `names`, or the index whose editor names (`shown`) hold it. -1 for none.
static func _resolve(
	value: String, names: PackedStringArray, shown: Array[PackedStringArray], limit: int
) -> int:
	var asked: String = value.strip_edges()
	if asked.is_valid_int():
		var number: int = asked.to_int()
		return number if number >= 0 and number < limit else -1
	var wanted: String = _plain(asked)
	if wanted.is_empty():
		return -1
	for index: int in names.size():
		if _plain(names[index]) == wanted:
			return index
	for index: int in shown.size():
		for editor_name: String in shown[index]:
			if _plain(editor_name) == wanted:
				return index
	return -1


## A name as it is compared: "D-pad Up", "dpad_up" and "DPAD UP" are the same.
static func _plain(text: String) -> String:
	return text.to_lower().replace(" ", "").replace("_", "").replace("-", "")


func _update() -> Verdict:
	var stored: PackedByteArray = FileAccess.get_file_as_bytes(PROJECT_FILE)
	if stored.is_empty():
		return _failed("cannot read %s" % PROJECT_FILE)
	var original: String = stored.get_string_from_utf8()
	# The file is written back from this text: it has to be the same bytes again.
	if original.to_utf8_buffer() != stored:
		var why: String = "(a byte that is not UTF-8, or a byte-order mark)"
		var fix: String = "Save it again from the Godot editor"
		return _failed("%s is not plain UTF-8 text %s. %s" % [PROJECT_FILE, why, fix])
	var setting: String = SETTING_PREFIX + _action
	var exists: bool = ProjectSettings.has_setting(setting)
	var deadzone: float = _deadzone
	var events: Array = []
	if exists:
		var current: Variant = ProjectSettings.get_setting(setting)
		var problem: String = _entry_problem(current)
		if not problem.is_empty():
			var where: Array = [PROJECT_FILE, SECTION, _action, problem]
			return _failed("%s [%s] %s %s Fix or remove it by hand" % where)
		var entry: Dictionary = current
		deadzone = entry.get("deadzone", DEFAULT_DEADZONE)
		if _deadzone_given and not is_equal_approx(deadzone, _deadzone):
			var has: Array = [_action, str(deadzone), FLAG_DEADZONE]
			return _failed("%s exists with deadzone %s; %s only sets a new action's" % has)
		var listed: Array = entry.get("events", [])
		# A copy keeps the array's type, so the events are written back in the
		# form the file already uses.
		events = listed.duplicate()

	var lines: PackedStringArray = PackedStringArray()
	var added: int = 0
	for event: InputEvent in _wanted:
		if _is_bound(events, event):
			lines.append("  = %s (already bound)" % _event_text(event))
		else:
			events.append(event)
			lines.append("  + %s" % _event_text(event))
			added += 1
	var updated: String = original
	if added > 0:
		var eol: String = "\r\n" if original.contains("\r\n") else "\n"
		# var_to_str writes a value the way Godot writes it into project.godot.
		var value: String = var_to_str({"deadzone": deadzone, "events": events})
		var block: String = ("%s=%s" % [_action, value]).replace("\n", eol)
		updated = _put_entry(original, _action, block, eol)
		var edit_problem: String = _edit_problem(original, updated)
		if edit_problem.is_empty():
			edit_problem = _write(updated)
		if not edit_problem.is_empty():
			return _failed(edit_problem)

	if exists:
		print("extend  %s" % _action)
	else:
		print("add     %s (deadzone %s)" % [_action, str(deadzone)])
	for line: String in lines:
		print(line)
	print("INPUT_ACTION_OK %s added=%d kept=%d" % [_action, added, _wanted.size() - added])
	if added > 0:
		print("Wrote %s. Reload the project if the editor has it open." % PROJECT_FILE)
	return Verdict.OK


func _failed(problem: String) -> Verdict:
	printerr("add_input_action: %s; nothing was written." % problem)
	return Verdict.PROBLEMS


## Prints every action project.godot stores, as the arguments that add it.
func _list() -> Verdict:
	var config: ConfigFile = ConfigFile.new()
	var loaded: Error = config.load(PROJECT_FILE)
	if loaded != OK:
		printerr("add_input_action: cannot read %s (%s)" % [PROJECT_FILE, error_string(loaded)])
		return Verdict.PROBLEMS
	var count: int = 0
	if config.has_section(SECTION):
		for action: String in config.get_section_keys(SECTION):
			print(_describe(action, config.get_value(SECTION, action)))
			count += 1
	print("INPUT_ACTIONS %d" % count)
	return Verdict.OK


static func _describe(action: String, current: Variant) -> String:
	var problem: String = _entry_problem(current)
	if not problem.is_empty():
		return "%s  # %s" % [action, problem]
	var entry: Dictionary = current
	var deadzone: float = entry.get("deadzone", DEFAULT_DEADZONE)
	var line: String = "%s %s %s" % [action, FLAG_DEADZONE, str(deadzone)]
	var others: PackedStringArray = PackedStringArray()
	var listed: Array = entry.get("events", [])
	for item: Variant in listed:
		var text: String = ""
		if item is InputEvent:
			var event: InputEvent = item
			text = _event_text(event)
			if text.is_empty():
				others.append(event.as_text())
		else:
			others.append(str(item))
		if text.is_empty():
			continue
		var space: int = text.find(" ")
		var value: String = text.substr(space + 1)
		if value.contains(" "):
			value = '"%s"' % value
		line += " --%s %s" % [text.substr(0, space), value]
	if not others.is_empty():
		line += "  # and, not writable by this tool: %s" % "; ".join(others)
	return line


## `event` as its flag without the dashes and its value ("key Ctrl+S",
## "joy-axis left_x:-1"); "" when no argument of this tool writes that event.
static func _event_text(event: InputEvent) -> String:
	if event.device != ALL_DEVICES:
		return ""
	if event is InputEventKey:
		var key: InputEventKey = event
		var physical_only: bool = key.keycode == KEY_NONE and key.key_label == KEY_NONE
		if key.physical_keycode == KEY_NONE or not physical_only:
			return ""
		if key.location != KEY_LOCATION_UNSPECIFIED:
			return ""
		var parts: PackedStringArray = PackedStringArray()
		var held: Array[bool] = [key.shift_pressed, key.alt_pressed, key.ctrl_pressed]
		held.append(key.meta_pressed)
		for index: int in MODIFIERS.size():
			if held[index]:
				parts.append(MODIFIERS[index])
		parts.append(OS.get_keycode_string(key.physical_keycode))
		return "key %s" % "+".join(parts)
	if event is InputEventJoypadButton:
		var joy_button: InputEventJoypadButton = event
		return "joy-button %s" % _short_name(JOY_BUTTON_NAMES, joy_button.button_index)
	if event is InputEventJoypadMotion:
		var motion: InputEventJoypadMotion = event
		if absf(motion.axis_value) != 1.0:
			return ""
		var direction: String = "-1" if motion.axis_value < 0.0 else "1"
		return "joy-axis %s:%s" % [_short_name(JOY_AXIS_NAMES, motion.axis), direction]
	if event is InputEventMouseButton:
		var mouse_button: InputEventMouseButton = event
		var modified: bool = mouse_button.get_modifiers_mask() != 0
		if modified or mouse_button.button_index == MOUSE_BUTTON_NONE:
			return ""
		return "mouse-button %s" % _short_name(MOUSE_BUTTON_NAMES, mouse_button.button_index)
	return ""


static func _short_name(names: PackedStringArray, index: int) -> String:
	return names[index] if index >= 0 and index < names.size() else str(index)


## Replaces project.godot with `text`; returns why it could not ("" on success).
static func _write(text: String) -> String:
	var file: FileAccess = FileAccess.open(PROJECT_FILE, FileAccess.WRITE)
	if file == null:
		return "cannot open %s (%s)" % [PROJECT_FILE, error_string(FileAccess.get_open_error())]
	file.store_string(text)
	file.close()
	return ""


## Why the value project.godot holds for an action cannot be extended ("" when
## it can): the tool would have to guess what to keep.
static func _entry_problem(current: Variant) -> String:
	if not current is Dictionary:
		return "is not a {deadzone, events} block."
	var entry: Dictionary = current
	var deadzone: Variant = entry.get("deadzone", DEFAULT_DEADZONE)
	if typeof(deadzone) != TYPE_FLOAT and typeof(deadzone) != TYPE_INT:
		return "has the deadzone %s, which is not a number." % JSON.stringify(deadzone)
	if not entry.get("events", []) is Array:
		return "has events that are not a list."
	return ""


## True when `events` already holds a binding for the same key, button or axis
## direction (on any device).
static func _is_bound(events: Array, candidate: InputEvent) -> bool:
	for existing: Variant in events:
		if existing is InputEvent:
			var event: InputEvent = existing
			if event.is_match(candidate, true):
				return true
	return false


## `text` (project.godot) with the [input] entry `key` replaced by `block`
## ("key=value"), or added at the end of the section, which is created in
## Godot's alphabetical section order when missing.
static func _put_entry(text: String, key: String, block: String, eol: String) -> String:
	var in_section: bool = false
	var has_entries: bool = false
	var insert_after: int = -1
	var later_section_start: int = -1
	for item: Dictionary in _scan(text):
		var start: int = item["start"]
		var end: int = item["end"]
		var section: String = item["section"]
		if item["header"]:
			in_section = section == SECTION
			if in_section:
				insert_after = end
			elif later_section_start < 0 and section > SECTION:
				later_section_start = start
		elif in_section:
			if item["key"] == key:
				return text.substr(0, start) + block + text.substr(end)
			insert_after = end
			has_entries = true
	if insert_after >= 0:
		var lead: String = eol if has_entries else eol + eol
		return text.substr(0, insert_after) + lead + block + text.substr(insert_after)
	var section_text: String = "[%s]%s%s%s%s" % [SECTION, eol, eol, block, eol]
	if later_section_start >= 0:
		var before: String = text.substr(0, later_section_start)
		return before + section_text + eol + text.substr(later_section_start)
	return (text if text.ends_with("\n") else text + eol) + eol + section_text


## The section headers and entries of project.godot text, in file order, as
## {"header": bool, "section": String, "key": String, "start": int, "end": int}.
## `start`..`end` spans the header line or the whole "key=value" (values run
## over several lines), without the line ending.
static func _scan(text: String) -> Array[Dictionary]:
	var items: Array[Dictionary] = []
	var section: String = ""
	var at: int = 0
	while at < text.length():
		var line_end: int = text.find("\n", at)
		if line_end < 0:
			line_end = text.length()
		var line: String = text.substr(at, line_end - at).trim_suffix("\r")
		var stripped: String = line.strip_edges()
		var equals: int = line.find("=")
		if _is_header(stripped):
			section = stripped.substr(1, stripped.find("]") - 1)
			items.append(_item(true, section, "", at, at + line.length()))
		elif equals > 0 and not stripped.begins_with(";"):
			line_end = _value_end(text, at + equals + 1)
			var end: int = line_end
			if end > 0 and text[end - 1] == "\r":
				end -= 1
			items.append(_item(false, section, line.substr(0, equals).strip_edges(), at, end))
		at = line_end + 1
	return items


## True for "[section]", alone on its line or with a ";" comment after it.
static func _is_header(stripped: String) -> bool:
	var close: int = stripped.find("]")
	if not stripped.begins_with("[") or close < 0:
		return false
	var rest: String = stripped.substr(close + 1).strip_edges()
	return rest.is_empty() or rest.begins_with(";")


static func _item(header: bool, section: String, key: String, start: int, end: int) -> Dictionary:
	return {"header": header, "section": section, "key": key, "start": start, "end": end}


## Index of the line break that ends the value starting at `from` (the text
## length when it is the last line). Line breaks inside strings, brackets and
## braces belong to the value.
static func _value_end(text: String, from: int) -> int:
	var depth: int = 0
	var in_string: bool = false
	var index: int = from
	while index < text.length():
		var character: String = text[index]
		if in_string:
			if character == "\\":
				index += 1
			elif character == '"':
				in_string = false
		elif character == '"':
			in_string = true
		elif character == "(" or character == "[" or character == "{":
			depth += 1
		elif character == ")" or character == "]" or character == "}":
			depth -= 1
		elif character == "\n" and depth <= 0:
			return index
		index += 1
	return text.length()


## Why `updated` must not be written ("" when it is safe): it has to parse,
## hold the action, and match `original` everywhere outside [input].
func _edit_problem(original: String, updated: String) -> String:
	var config: ConfigFile = ConfigFile.new()
	var parsed: Error = config.parse(updated)
	if parsed != OK:
		return "the edited file does not parse (%s)" % error_string(parsed)
	if not config.get_value(SECTION, _action, null) is Dictionary:
		return "the edited file lost the action %s" % _action
	if _outside_section(original) != _outside_section(updated):
		return "the edit reached outside [%s]" % SECTION
	return ""


## `text` without its [input] section; trailing blank lines do not count.
static func _outside_section(text: String) -> String:
	var section_start: int = -1
	var section_end: int = text.length()
	for item: Dictionary in _scan(text):
		if not item["header"]:
			continue
		var start: int = item["start"]
		if item["section"] == SECTION:
			section_start = start
		elif section_start >= 0:
			section_end = start
			break
	if section_start < 0:
		return text.strip_edges(false, true)
	return (text.substr(0, section_start) + text.substr(section_end)).strip_edges(false, true)
