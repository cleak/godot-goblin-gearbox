extends RefCounted
## Game state for DevTools: finding nodes, reading properties, and JSON <-> Variant for
## get_state, set_state, run_method and sequence asserts.
##
## Node paths are relative to the current scene ("" or "." is its root) or absolute
## ("/root/Main/Player"). A property may be indexed: "position:x".
##
## To JSON (to_json):
##   null, bool, int, float, String     unchanged
##   StringName, NodePath               a string
##   Vector2, Vector2i                  {"x", "y"}
##   Vector3, Vector3i                  {"x", "y", "z"}
##   Color                              {"r", "g", "b", "a"}
##   other math types (TAGGED_ARGS)     {"type": "Rect2", "args": [numbers]}, as JSON.from_native
##   Array, packed arrays               a list, cut at MAX_ITEMS with a "... (N more)" string
##   Dictionary                         an object; keys become strings
##   Object, Callable, Signal, RID      text for display; it cannot be sent back
##
## From JSON (from_json) takes the Variant type wanted, because JSON has one number type and
## no vectors: Godot parses every JSON number as a float. Vectors and colors are accepted as
## the object above or as a list ([x, y]); a color also as a name or "#rrggbb[aa]". Any
## TAGGED_ARGS type is accepted in its {"type", "args"} form. With no type wanted
## (TYPE_NIL: an untyped property or argument) the JSON value passes through as parsed, so
## numbers stay floats; send {"type", "args"} to pass a vector there.
##
## A typed collection (Array[int], Dictionary[String, bool]) is built with the element types
## of the property or parameter it is headed for, each element converted like any other
## value; the engine would refuse a plain list there. JSON object keys are text, so only
## String, StringName, int and float keys can be sent. A collection of objects can only be
## sent empty.
##
## Preload it; it declares no class_name.

## Lists longer than this are cut when written as JSON.
const MAX_ITEMS: int = 256
## Containers nested deeper than this are written as a placeholder string.
const MAX_DEPTH: int = 8
## Math types and their number count in the {"type", "args"} form.
const TAGGED_ARGS: Dictionary = {
	"Vector2": 2,
	"Vector2i": 2,
	"Rect2": 4,
	"Rect2i": 4,
	"Vector3": 3,
	"Vector3i": 3,
	"Transform2D": 6,
	"Vector4": 4,
	"Vector4i": 4,
	"Plane": 4,
	"Quaternion": 4,
	"AABB": 6,
	"Basis": 9,
	"Transform3D": 12,
	"Projection": 16,
	"Color": 4,
}
const AXES: PackedStringArray = ["x", "y", "z"]
const CHANNELS: PackedStringArray = ["r", "g", "b", "a"]
const INTEGER_TYPES: PackedInt32Array = [TYPE_INT, TYPE_VECTOR2I, TYPE_VECTOR3I]
const TEXT_TYPES: PackedInt32Array = [TYPE_STRING, TYPE_STRING_NAME, TYPE_NODE_PATH]
const PACKED_SCALAR_TYPES: PackedInt32Array = [
	TYPE_PACKED_BYTE_ARRAY,
	TYPE_PACKED_INT32_ARRAY,
	TYPE_PACKED_INT64_ARRAY,
	TYPE_PACKED_FLOAT32_ARRAY,
	TYPE_PACKED_FLOAT64_ARRAY,
	TYPE_PACKED_STRING_ARRAY,
]


class Decoded:
	extends RefCounted
	## The outcome of a read or a conversion: `value`, or why there is none.
	var value: Variant = null
	var error: String = ""


## What a typed collection holds: a Variant.Type (TYPE_NIL: anything) and, for objects, the
## engine class.
class Element:
	extends RefCounted
	var type: int = TYPE_NIL
	var native_class: StringName = &""


## The node at `path`, or null. Relative paths start at the current scene.
static func find_node(tree: SceneTree, path: String) -> Node:
	if path.begins_with("/"):
		return tree.root.get_node_or_null(path)
	var scene: Node = tree.current_scene
	if scene == null or path.is_empty():
		return scene
	return scene.get_node_or_null(path)


## True when `target` has `property` (the part before any ":" index).
static func has_property(target: Object, property: String) -> bool:
	return not property.is_empty() and property.get_slice(":", 0) in target


## The value of `property` on the node at `node_path`.
static func read(tree: SceneTree, node_path: String, property: String) -> Decoded:
	var decoded: Decoded = Decoded.new()
	var target: Node = find_node(tree, node_path)
	if target == null:
		decoded.error = node_error(tree, node_path)
	elif not has_property(target, property):
		decoded.error = "Property not found: '%s' on %s" % [property, target.get_path()]
	else:
		decoded.value = target.get_indexed(NodePath(property))
	return decoded


## The declared type of `property` on `target` (a Variant.Type); TYPE_NIL when it is untyped.
## An indexed property ("position:x") has the type of the value it holds.
static func declared_type(target: Object, property: String) -> int:
	if property.contains(":"):
		return typeof(target.get_indexed(NodePath(property)))
	for info: Dictionary in target.get_property_list():
		if info["name"] == property:
			var type: int = info["type"]
			return type
	return TYPE_NIL


## Converts the JSON arguments `given` to the types `method` declares on `target`, appending
## them to `converted`. Returns "" or why they do not fit.
static func method_arguments(
	target: Object, method: String, given: Array, converted: Array
) -> String:
	var parameters: Array = []
	var optional: int = 0
	var takes_more: bool = true
	for info: Dictionary in target.get_method_list():
		if info["name"] == method:
			var defaults: Array = info["default_args"]
			var flags: int = info["flags"]
			parameters = info["args"]
			optional = defaults.size()
			takes_more = flags & METHOD_FLAG_VARARG != 0
			break
	var too_many: bool = given.size() > parameters.size() and not takes_more
	if given.size() < parameters.size() - optional or too_many:
		return "it takes %d arguments, got %d" % [parameters.size(), given.size()]
	for index: int in range(given.size()):
		var type: int = TYPE_NIL
		var name: String = "argument %d" % (index + 1)
		var like: Decoded = Decoded.new()
		if index < parameters.size():
			var parameter: Dictionary = parameters[index]
			type = parameter["type"]
			name = "%s (%s)" % [name, parameter["name"]]
			like = typed_template(parameter)
		var decoded: Decoded = like
		if like.error.is_empty():
			decoded = from_json(given[index], type, like.value)
		if not decoded.error.is_empty():
			return "%s: %s" % [name, decoded.error]
		converted.append(decoded.value)
	return ""


## An empty Array or Dictionary with the element types `info` declares (a description from
## get_method_list or get_property_list), for from_json to copy them from; a null value when
## it declares none. An error when the types cannot be built from their names.
static func typed_template(info: Dictionary) -> Decoded:
	var template: Decoded = Decoded.new()
	var names: PackedStringArray = str(info.get("hint_string", "")).split(";")
	var hint: int = info.get("hint", PROPERTY_HINT_NONE)
	if info["type"] == TYPE_ARRAY and hint == PROPERTY_HINT_ARRAY_TYPE:
		var item: Element = _element_type(names[0])
		if item == null:
			template.error = "an Array[%s] cannot be sent as JSON" % names[0]
		else:
			template.value = Array([], item.type, item.native_class, null)
	elif info["type"] == TYPE_DICTIONARY and hint == PROPERTY_HINT_DICTIONARY_TYPE:
		var key: Element = _element_type(names[0])
		var value: Element = _element_type(names[1] if names.size() > 1 else "")
		if key == null or value == null:
			template.error = "a Dictionary[%s] cannot be sent as JSON" % ", ".join(names)
		else:
			template.value = Dictionary(
				{}, key.type, key.native_class, null, value.type, value.native_class, null
			)
	return template


## Why find_node returned null for `path`.
static func node_error(tree: SceneTree, path: String) -> String:
	if tree.current_scene == null and not path.begins_with("/"):
		return "No current scene"
	return "Node not found: %s" % path


## `value` as something JSON.stringify writes faithfully (see the table above).
static func to_json(value: Variant, depth: int = 0) -> Variant:
	var type: int = typeof(value)
	var json: Variant = null
	match type:
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			json = value
		TYPE_VECTOR2, TYPE_VECTOR2I, TYPE_VECTOR3, TYPE_VECTOR3I, TYPE_COLOR:
			json = _named_components(value)
		TYPE_ARRAY, TYPE_DICTIONARY:
			json = _container_to_json(value, depth)
		TYPE_OBJECT:
			json = _object_text(value)
		_:
			if TAGGED_ARGS.has(type_string(type)):
				json = JSON.from_native(value)
			elif type >= TYPE_PACKED_BYTE_ARRAY and type < TYPE_MAX:
				json = _container_to_json(type_convert(value, TYPE_ARRAY), depth)
			else:
				json = str(value)
	return json


## The JSON value `value` as a Variant of `type` (a Variant.Type; TYPE_NIL: as parsed).
## `like` is a value of the target (a property's current value, or a typed_template): a
## typed Array or Dictionary is built with its element types.
static func from_json(value: Variant, type: int, like: Variant = null) -> Decoded:
	var decoded: Decoded = Decoded.new()
	if _is_tagged(value):
		_decode_tagged(value, decoded)
	elif type == TYPE_NIL:
		decoded.value = value
	elif type == TYPE_BOOL:
		decoded.value = value
	elif type == TYPE_INT or type == TYPE_FLOAT:
		_decode_number(value, type, decoded)
	elif TEXT_TYPES.has(type):
		if value is String:
			decoded.value = type_convert(value, type)
	elif type == TYPE_COLOR:
		_decode_color(value, decoded)
	elif type in [TYPE_VECTOR2, TYPE_VECTOR2I, TYPE_VECTOR3, TYPE_VECTOR3I]:
		_decode_vector(value, type, decoded)
	elif type == TYPE_ARRAY:
		_decode_array(value, like, decoded)
	elif type == TYPE_DICTIONARY:
		_decode_dictionary(value, like, decoded)
	elif PACKED_SCALAR_TYPES.has(type):
		if value is Array:
			decoded.value = type_convert(value, type)
	elif type == TYPE_OBJECT and value != null:
		decoded.error = "an object cannot be sent as JSON; only null"
	var fits: bool = typeof(decoded.value) == type or type == TYPE_NIL or type == TYPE_OBJECT
	if decoded.error.is_empty() and not fits:
		decoded.error = "expected %s, got %s" % [describe(type), JSON.stringify(value)]
	if not decoded.error.is_empty():
		decoded.value = null
	return decoded


## "" when `actual` equals the JSON value `expected`, read as the type of `actual`; else why
## not. Floats compare approximately, so 0.1 + 0.2 equals 0.3.
static func mismatch(actual: Variant, expected: Variant) -> String:
	var wanted: Variant = expected
	var type: int = typeof(actual)
	var is_container: bool = type >= TYPE_DICTIONARY and type < TYPE_MAX
	if type != TYPE_NIL and type != TYPE_OBJECT and not is_container:
		var decoded: Decoded = from_json(expected, type)
		if not decoded.error.is_empty():
			return decoded.error
		wanted = to_json(decoded.value)
	if json_equal(to_json(actual), wanted):
		return ""
	return "is %s, expected %s" % [JSON.stringify(to_json(actual)), JSON.stringify(wanted)]


## Deep equality of two JSON values; numbers compare approximately.
static func json_equal(a: Variant, b: Variant) -> bool:
	if is_number(a) and is_number(b):
		var left: float = a
		var right: float = b
		return is_equal_approx(left, right)
	if typeof(a) != typeof(b):
		return false
	if a is Array:
		var a_list: Array = a
		var b_list: Array = b
		return _lists_equal(a_list, b_list)
	if a is Dictionary:
		var a_map: Dictionary = a
		var b_map: Dictionary = b
		return _maps_equal(a_map, b_map)
	return a == b


static func is_number(value: Variant) -> bool:
	return value is int or value is float


## How to write `type` as JSON, for error messages.
static func describe(type: int) -> String:
	var name: String = type_string(type)
	match type:
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return '%s as {"x", "y"} or [x, y]' % name
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return '%s as {"x", "y", "z"} or [x, y, z]' % name
		TYPE_COLOR:
			return 'Color as {"r", "g", "b", "a"}, [r, g, b, a], a name or "#rrggbb"'
	if TAGGED_ARGS.has(name):
		return '%s as {"type": "%s", "args": [%d numbers]}' % [name, name, TAGGED_ARGS[name]]
	return name


static func _lists_equal(a: Array, b: Array) -> bool:
	if a.size() != b.size():
		return false
	for index: int in range(a.size()):
		if not json_equal(a[index], b[index]):
			return false
	return true


static func _maps_equal(a: Dictionary, b: Dictionary) -> bool:
	if a.size() != b.size():
		return false
	for key: Variant in a:
		if not b.has(key) or not json_equal(a[key], b[key]):
			return false
	return true


static func _named_components(value: Variant) -> Dictionary:
	if value is Color:
		var color: Color = value
		return {"r": color.r, "g": color.g, "b": color.b, "a": color.a}
	var size: int = 2 if value is Vector2 or value is Vector2i else 3
	var out: Dictionary = {}
	for axis: int in range(size):
		out[AXES[axis]] = value[axis]
	return out


static func _container_to_json(value: Variant, depth: int) -> Variant:
	if depth >= MAX_DEPTH:
		return "<nested deeper than %d>" % MAX_DEPTH
	if value is Dictionary:
		var source: Dictionary = value
		var out: Dictionary = {}
		for key: Variant in source:
			out[str(key)] = to_json(source[key], depth + 1)
		return out
	var items: Array = value
	var list: Array = []
	for index: int in range(mini(items.size(), MAX_ITEMS)):
		list.append(to_json(items[index], depth + 1))
	if items.size() > MAX_ITEMS:
		list.append("... (%d more)" % (items.size() - MAX_ITEMS))
	return list


static func _object_text(value: Variant) -> Variant:
	if not is_instance_valid(value):
		return null
	if value is Resource:
		var resource: Resource = value
		if not resource.resource_path.is_empty():
			return "<%s %s>" % [resource.get_class(), resource.resource_path]
	return str(value)


static func _is_tagged(value: Variant) -> bool:
	if not value is Dictionary:
		return false
	var tagged: Dictionary = value
	return tagged.size() == 2 and tagged.get("type") is String and tagged.get("args") is Array


static func _decode_tagged(value: Variant, decoded: Decoded) -> void:
	var tagged: Dictionary = value
	var name: String = tagged["type"]
	var args: Array = tagged["args"]
	if not TAGGED_ARGS.has(name):
		var known: PackedStringArray = PackedStringArray(TAGGED_ARGS.keys())
		decoded.error = 'unknown "type" %s; one of %s' % [name, ", ".join(known)]
	elif args.size() != TAGGED_ARGS[name] or not args.all(is_number):
		decoded.error = '%s takes %d numbers in "args"' % [name, TAGGED_ARGS[name]]
	else:
		decoded.value = JSON.to_native(tagged)


static func _decode_number(value: Variant, type: int, decoded: Decoded) -> void:
	if not is_number(value):
		return
	var number: float = value
	if type == TYPE_FLOAT:
		decoded.value = number
	elif number == floorf(number):
		decoded.value = int(number)
	else:
		decoded.error = "expected a whole number, got %s" % JSON.stringify(value)


static func _decode_color(value: Variant, decoded: Decoded) -> void:
	if value is String:
		var text: String = value
		# from_string returns its default for text that is neither a name nor hex.
		var parsed: Color = Color.from_string(text, Color.BLACK)
		if parsed == Color.from_string(text, Color.WHITE):
			decoded.value = parsed
		return
	var parts: PackedFloat64Array = _numbers(value, CHANNELS, 3)
	if parts.size() == 3:
		decoded.value = Color(parts[0], parts[1], parts[2])
	elif parts.size() == 4:
		decoded.value = Color(parts[0], parts[1], parts[2], parts[3])


static func _decode_vector(value: Variant, type: int, decoded: Decoded) -> void:
	var size: int = 2 if type == TYPE_VECTOR2 or type == TYPE_VECTOR2I else 3
	var parts: PackedFloat64Array = _numbers(value, AXES.slice(0, size), size)
	if parts.size() != size:
		return
	if INTEGER_TYPES.has(type):
		for part: float in parts:
			if part != floorf(part):
				decoded.error = "expected whole numbers, got %s" % JSON.stringify(value)
				return
	match type:
		TYPE_VECTOR2:
			decoded.value = Vector2(parts[0], parts[1])
		TYPE_VECTOR2I:
			decoded.value = Vector2i(int(parts[0]), int(parts[1]))
		TYPE_VECTOR3:
			decoded.value = Vector3(parts[0], parts[1], parts[2])
		TYPE_VECTOR3I:
			decoded.value = Vector3i(int(parts[0]), int(parts[1]), int(parts[2]))


## A list as the kind of Array `like` is: untyped as parsed, typed with each item converted.
static func _decode_array(value: Variant, like: Variant, decoded: Decoded) -> void:
	if not value is Array:
		return
	var items: Array = value
	var template: Array = []
	if like is Array:
		template = like
	if not template.is_typed():
		decoded.value = items
		return
	var type: int = template.get_typed_builtin()
	var converted: Array = []
	for index: int in range(items.size()):
		var item: Decoded = from_json(items[index], type)
		if not item.error.is_empty():
			decoded.error = "item %d: %s" % [index, item.error]
			return
		converted.append(item.value)
	decoded.value = Array(
		converted, type, template.get_typed_class_name(), template.get_typed_script()
	)


## An object as the kind of Dictionary `like` is: untyped as parsed, typed with each key and
## value converted.
static func _decode_dictionary(value: Variant, like: Variant, decoded: Decoded) -> void:
	if not value is Dictionary:
		return
	var entries: Dictionary = value
	var template: Dictionary = {}
	if like is Dictionary:
		template = like
	if not template.is_typed():
		decoded.value = entries
		return
	var key_type: int = template.get_typed_key_builtin()
	var value_type: int = template.get_typed_value_builtin()
	var converted: Dictionary = {}
	for name: Variant in entries:
		var key: Decoded = _decode_key(str(name), key_type)
		var entry: Decoded = from_json(entries[name], value_type)
		if not key.error.is_empty() or not entry.error.is_empty():
			decoded.error = "key %s: %s" % [JSON.stringify(name), key.error + entry.error]
			return
		converted[key.value] = entry.value
	decoded.value = Dictionary(
		converted,
		key_type,
		template.get_typed_key_class_name(),
		template.get_typed_key_script(),
		value_type,
		template.get_typed_value_class_name(),
		template.get_typed_value_script()
	)


## A JSON object key (always text) as a dictionary key of `type`.
static func _decode_key(text: String, type: int) -> Decoded:
	var decoded: Decoded = Decoded.new()
	if type == TYPE_NIL:
		decoded.value = text
	elif TEXT_TYPES.has(type):
		decoded.value = type_convert(text, type)
	elif type == TYPE_INT and text.is_valid_int():
		decoded.value = text.to_int()
	elif type == TYPE_FLOAT and text.is_valid_float():
		decoded.value = text.to_float()
	else:
		decoded.error = "a JSON key cannot be read as %s" % type_string(type)
	return decoded


## The element type a type name stands for: "int", "Vector2", "Node", "Variant". Null for a
## name that is not an engine type (a script class).
static func _element_type(name: String) -> Element:
	var element: Element = Element.new()
	if name.is_empty() or name == "Variant":
		return element
	var number: String = name.get_slice(":", 0).get_slice("/", 0)
	if name.contains(":") and number.is_valid_int():
		# "<type>/<hint>:<hint text>", the way an exported collection names its elements.
		element.type = number.to_int()
		if element.type == TYPE_OBJECT:
			return null
		return element
	for type: int in range(TYPE_MAX):
		if type != TYPE_OBJECT and type_string(type) == name:
			element.type = type
			return element
	if ClassDB.class_exists(name):
		element.type = TYPE_OBJECT
		element.native_class = name
		return element
	return null


## The numbers of a list, or of an object keyed by `keys` (those past `required` may be
## left out). Empty when `value` is neither, or holds anything but numbers.
static func _numbers(value: Variant, keys: PackedStringArray, required: int) -> PackedFloat64Array:
	var items: Array = []
	if value is Array:
		items = value
	elif value is Dictionary:
		var named: Dictionary = value
		for key: String in keys:
			if named.has(key):
				items.append(named[key])
		if items.size() != named.size() or items.size() < required:
			return PackedFloat64Array()
	if items.size() > keys.size() or not items.all(is_number):
		return PackedFloat64Array()
	return PackedFloat64Array(items)
