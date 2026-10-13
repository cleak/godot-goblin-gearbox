extends GdUnitTestSuite
## JSON <-> Variant: what get_state writes, what set_state / run_method / assert read.

const DevState := preload("res://addons/agent_devtools/dev_tools_state.gd")


class Sample:
	extends Node
	var count: int = 3
	var spot: Vector2 = Vector2(1.5, -2.0)
	@warning_ignore("untyped_declaration") var loose = null

	func move(to: Vector2, steps: int = 1) -> Vector2:
		return to * steps

	func typed(
		_numbers: Array[int],
		_cells: Array[Vector2i],
		_nodes: Array[Node],
		_boards: Array[Board],
		_named: Dictionary[StringName, float],
		_plain: Array,
		_loose: Dictionary[String, Variant]
	) -> void:
		pass


func test_scalars_and_text_pass_through() -> void:
	assert_that(DevState.to_json(null)).is_null()
	assert_that(DevState.to_json(true)).is_equal(true)
	assert_that(DevState.to_json(7)).is_equal(7)
	assert_that(DevState.to_json(0.5)).is_equal(0.5)
	assert_that(DevState.to_json("text")).is_equal("text")
	assert_that(DevState.to_json(&"name")).is_equal("name")
	assert_that(DevState.to_json(^"A/B:c")).is_equal("A/B:c")


func test_vectors_and_colors_become_named_components() -> void:
	assert_that(DevState.to_json(Vector2(1.5, -2.0))).is_equal({"x": 1.5, "y": -2.0})
	assert_that(DevState.to_json(Vector2i(3, 4))).is_equal({"x": 3, "y": 4})
	assert_that(DevState.to_json(Vector3(1, 2, 3))).is_equal({"x": 1.0, "y": 2.0, "z": 3.0})
	assert_that(DevState.to_json(Vector3i(1, 2, 3))).is_equal({"x": 1, "y": 2, "z": 3})
	assert_that(DevState.to_json(Color(1, 0, 0, 0.5))).is_equal(
		{"r": 1.0, "g": 0.0, "b": 0.0, "a": 0.5}
	)


func test_other_math_types_use_the_engine_tagged_form() -> void:
	assert_that(DevState.to_json(Rect2(1, 2, 3, 4))).is_equal(
		{"type": "Rect2", "args": [1.0, 2.0, 3.0, 4.0]}
	)
	var transform: Variant = DevState.to_json(Transform2D.IDENTITY)
	assert_that(transform).is_equal({"type": "Transform2D", "args": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]})


func test_containers_convert_their_items_and_long_lists_are_cut() -> void:
	var nested: Variant = DevState.to_json({1: [Vector2i(1, 2), &"n"], "k": PackedInt32Array([5])})
	assert_that(nested).is_equal({"1": [{"x": 1, "y": 2}, "n"], "k": [5]})
	var long: Array = range(DevState.MAX_ITEMS + 10)
	var cut: Array = DevState.to_json(long)
	assert_int(cut.size()).is_equal(DevState.MAX_ITEMS + 1)
	assert_that(cut[-1]).is_equal("... (10 more)")


func test_objects_are_display_text_and_freed_ones_are_null() -> void:
	var node: Node = auto_free(Node.new())
	assert_str(str(DevState.to_json(node))).starts_with("<Node#")
	var resource: Resource = load("res://icon.svg")
	assert_str(str(DevState.to_json(resource))).contains("res://icon.svg")
	var freed: Node = Node.new()
	freed.free()
	assert_that(DevState.to_json(freed)).is_null()


func test_json_numbers_become_the_wanted_number_type() -> void:
	var whole: DevState.Decoded = DevState.from_json(5.0, TYPE_INT)
	assert_int(typeof(whole.value)).is_equal(TYPE_INT)
	assert_that(whole.value).is_equal(5)
	assert_str(DevState.from_json(5.5, TYPE_INT).error).contains("whole number")
	assert_int(typeof(DevState.from_json(5.0, TYPE_FLOAT).value)).is_equal(TYPE_FLOAT)
	assert_str(DevState.from_json("5", TYPE_INT).error).contains("expected int")
	assert_str(DevState.from_json(1.0, TYPE_BOOL).error).contains("expected bool")
	assert_that(DevState.from_json(true, TYPE_BOOL).value).is_equal(true)


func test_text_becomes_string_name_or_node_path() -> void:
	assert_int(typeof(DevState.from_json("a", TYPE_STRING_NAME).value)).is_equal(TYPE_STRING_NAME)
	assert_that(DevState.from_json("A/B", TYPE_NODE_PATH).value).is_equal(^"A/B")
	assert_str(DevState.from_json(3.0, TYPE_STRING).error).contains("expected String")


func test_vectors_read_from_an_object_or_a_list() -> void:
	assert_that(DevState.from_json({"x": 1, "y": 2.5}, TYPE_VECTOR2).value).is_equal(
		Vector2(1, 2.5)
	)
	assert_that(DevState.from_json([1.0, 2.0], TYPE_VECTOR2I).value).is_equal(Vector2i(1, 2))
	assert_that(DevState.from_json({"x": 1, "y": 2, "z": 3}, TYPE_VECTOR3).value).is_equal(
		Vector3(1, 2, 3)
	)
	assert_that(DevState.from_json([1.0, 2.0, 3.0], TYPE_VECTOR3I).value).is_equal(
		Vector3i(1, 2, 3)
	)


func test_malformed_vectors_are_refused_with_the_expected_form() -> void:
	for bad: Variant in [{"x": 1}, {"x": 1, "y": 2, "w": 3}, [1.0], [1.0, 2.0, 3.0], "1,2", 5.0]:
		var decoded: DevState.Decoded = DevState.from_json(bad, TYPE_VECTOR2)
		assert_str(decoded.error).contains('Vector2 as {"x", "y"} or [x, y]')
		assert_that(decoded.value).is_null()
	assert_str(DevState.from_json({"x": 1.5, "y": 2}, TYPE_VECTOR2I).error).contains(
		"whole numbers"
	)


func test_colors_read_from_components_a_list_a_name_or_hex() -> void:
	var red: Color = Color(1, 0, 0)
	assert_that(DevState.from_json({"r": 1, "g": 0, "b": 0}, TYPE_COLOR).value).is_equal(red)
	assert_that(DevState.from_json({"r": 1, "g": 0, "b": 0, "a": 0.5}, TYPE_COLOR).value).is_equal(
		Color(1, 0, 0, 0.5)
	)
	assert_that(DevState.from_json([1.0, 0.0, 0.0, 1.0], TYPE_COLOR).value).is_equal(red)
	assert_that(DevState.from_json("red", TYPE_COLOR).value).is_equal(red)
	assert_that(DevState.from_json("#ff0000", TYPE_COLOR).value).is_equal(red)
	assert_str(DevState.from_json("not-a-color", TYPE_COLOR).error).contains("Color as")


func test_tagged_form_decodes_and_is_checked() -> void:
	var tagged: Dictionary = {"type": "Rect2", "args": [1, 2, 3, 4]}
	assert_that(DevState.from_json(tagged, TYPE_RECT2).value).is_equal(Rect2(1, 2, 3, 4))
	assert_that(DevState.from_json(tagged, TYPE_NIL).value).is_equal(Rect2(1, 2, 3, 4))
	assert_str(DevState.from_json(tagged, TYPE_VECTOR2).error).contains("expected Vector2")
	assert_str(DevState.from_json({"type": "Rect2", "args": [1, 2]}, TYPE_NIL).error).contains(
		"takes 4 numbers"
	)
	assert_str(DevState.from_json({"type": "Node", "args": []}, TYPE_NIL).error).contains(
		'unknown "type"'
	)
	assert_str(DevState.from_json([1.0, 2.0, 3.0, 4.0], TYPE_RECT2).error).contains(
		'{"type": "Rect2", "args": [4 numbers]}'
	)


func test_untyped_targets_take_json_as_parsed() -> void:
	assert_that(DevState.from_json(5.0, TYPE_NIL).value).is_equal(5.0)
	assert_that(DevState.from_json({"x": 1.0}, TYPE_NIL).value).is_equal({"x": 1.0})
	assert_that(DevState.from_json(null, TYPE_OBJECT).error).is_equal("")
	assert_str(DevState.from_json("node", TYPE_OBJECT).error).contains("only null")


func test_a_typed_array_is_built_with_the_element_type_of_its_target() -> void:
	var like: Array[int] = []
	var decoded: DevState.Decoded = DevState.from_json([4.0, 5.0], TYPE_ARRAY, like)
	var numbers: Array = decoded.value
	assert_str(decoded.error).is_empty()
	assert_bool(numbers.is_typed()).is_true()
	assert_int(numbers.get_typed_builtin()).is_equal(TYPE_INT)
	assert_that(numbers).is_equal([4, 5])
	var cells_like: Array[Vector2i] = []
	assert_that(DevState.from_json([[1.0, 2.0]], TYPE_ARRAY, cells_like).value).is_equal(
		[Vector2i(1, 2)]
	)
	assert_str(DevState.from_json(["a"], TYPE_ARRAY, like).error).is_equal(
		'item 0: expected int, got "a"'
	)
	assert_str(DevState.from_json({"a": 1}, TYPE_ARRAY, like).error).contains("expected Array")
	# With nothing typed to copy from, a list stays as parsed.
	var plain: Array = DevState.from_json([4.0, "x"], TYPE_ARRAY).value
	assert_bool(plain.is_typed()).is_false()
	assert_that(plain).is_equal([4.0, "x"])


func test_a_typed_dictionary_is_built_with_its_key_and_value_types() -> void:
	var like: Dictionary[int, Vector2] = {}
	var decoded: DevState.Decoded = DevState.from_json({"7": [1.0, 2.0]}, TYPE_DICTIONARY, like)
	var built: Dictionary = decoded.value
	assert_str(decoded.error).is_empty()
	assert_bool(built.is_typed()).is_true()
	assert_that(built).is_equal({7: Vector2(1, 2)})
	var names_like: Dictionary[StringName, bool] = {}
	var named: Dictionary = DevState.from_json({"a": true}, TYPE_DICTIONARY, names_like).value
	assert_int(typeof(named.keys()[0])).is_equal(TYPE_STRING_NAME)
	assert_str(DevState.from_json({"x": [1.0, 2.0]}, TYPE_DICTIONARY, like).error).is_equal(
		'key "x": a JSON key cannot be read as int'
	)
	assert_str(DevState.from_json({"7": "up"}, TYPE_DICTIONARY, like).error).contains(
		'key "7": expected Vector2'
	)
	var plain: Dictionary = DevState.from_json({"7": 1.0}, TYPE_DICTIONARY).value
	assert_bool(plain.is_typed()).is_false()


## What a method declares for its typed collections, by name; a template is built from it.
func test_typed_templates_follow_the_declared_parameters() -> void:
	var sample: Sample = auto_free(Sample.new())
	var parameters: Array[Dictionary] = []
	for info: Dictionary in sample.get_method_list():
		if info["name"] == "typed":
			var listed: Array = info["args"]
			parameters.assign(listed)
	assert_int(parameters.size()).is_equal(7)
	var numbers: Array = DevState.typed_template(parameters[0]).value
	assert_int(numbers.get_typed_builtin()).is_equal(TYPE_INT)
	var cells: Array = DevState.typed_template(parameters[1]).value
	assert_int(cells.get_typed_builtin()).is_equal(TYPE_VECTOR2I)
	var nodes: Array = DevState.typed_template(parameters[2]).value
	assert_int(nodes.get_typed_builtin()).is_equal(TYPE_OBJECT)
	assert_str(str(nodes.get_typed_class_name())).is_equal("Node")
	# A script class cannot be rebuilt from its name: refused, not passed as a plain list.
	assert_str(DevState.typed_template(parameters[3]).error).contains("cannot be sent as JSON")
	var named: Dictionary = DevState.typed_template(parameters[4]).value
	assert_int(named.get_typed_key_builtin()).is_equal(TYPE_STRING_NAME)
	assert_int(named.get_typed_value_builtin()).is_equal(TYPE_FLOAT)
	assert_that(DevState.typed_template(parameters[5]).value).is_null()
	assert_str(DevState.typed_template(parameters[5]).error).is_empty()
	var loose: Dictionary = DevState.typed_template(parameters[6]).value
	assert_int(loose.get_typed_key_builtin()).is_equal(TYPE_STRING)
	assert_int(loose.get_typed_value_builtin()).is_equal(TYPE_NIL)
	var converted: Array = []
	var given: Array = [[1.0], [[2.0, 3.0]], [], [], {"a": 1.0}, [1.0], {"k": [1.0]}]
	assert_str(DevState.method_arguments(sample, "typed", given, converted)).is_equal(
		"argument 4 (_boards): an Array[Board] cannot be sent as JSON"
	)


func test_every_supported_value_round_trips() -> void:
	var values: Array = [
		true,
		-4,
		2.5,
		"text",
		&"name",
		^"A/B",
		Vector2(1.5, 2),
		Vector2i(-1, 9),
		Vector3(1, 2, 3),
		Vector3i(4, 5, 6),
		Color(0.1, 0.2, 0.3, 0.4),
		Rect2(1, 2, 3, 4),
		Rect2i(1, 2, 3, 4),
		Vector4(1, 2, 3, 4),
		Quaternion(0, 0, 0, 1),
		Transform2D(0.5, Vector2(3, 4)),
		Basis.IDENTITY,
		Transform3D(Basis.IDENTITY, Vector3(1, 2, 3)),
		PackedInt32Array([1, 2]),
		PackedStringArray(["a", "b"]),
	]
	for value: Variant in values:
		# Through text, as the mailbox does: every number comes back a float.
		var wire: Variant = JSON.parse_string(JSON.stringify(DevState.to_json(value)))
		var decoded: DevState.Decoded = DevState.from_json(wire, typeof(value))
		assert_str(decoded.error).is_empty()
		assert_int(typeof(decoded.value)).is_equal(typeof(value))
		assert_str(DevState.mismatch(decoded.value, DevState.to_json(value))).is_empty()


func test_mismatch_compares_as_the_type_of_the_actual_value() -> void:
	assert_str(DevState.mismatch(3, 3.0)).is_empty()
	assert_str(DevState.mismatch(0.1 + 0.2, 0.3)).is_empty()
	assert_str(DevState.mismatch(Vector2i(3, 2), {"x": 3, "y": 2})).is_empty()
	assert_str(DevState.mismatch(Vector2(3, 2), [3.0, 2.0])).is_empty()
	assert_str(DevState.mismatch(&"idle", "idle")).is_empty()
	assert_str(DevState.mismatch(null, null)).is_empty()
	assert_str(DevState.mismatch([1, Vector2i(1, 2)], [1.0, {"x": 1.0, "y": 2.0}])).is_empty()
	assert_str(DevState.mismatch({"hp": 3}, {"hp": 3.0})).is_empty()
	assert_str(DevState.mismatch(3, 4.0)).is_equal("is 3, expected 4")
	assert_str(DevState.mismatch(Vector2i(3, 2), {"x": 4, "y": 2})).contains('"x":3').contains(
		'"x":4'
	)
	# Comparing a number with text must not raise: it is simply not equal.
	assert_str(DevState.mismatch(3, "three")).contains("expected int")
	assert_str(DevState.mismatch("three", 3.0)).contains("expected String")
	assert_str(DevState.mismatch(null, 3.0)).contains("is null")


func test_read_finds_nodes_and_indexed_properties() -> void:
	var sample: Sample = auto_free(Sample.new())
	add_child(sample)
	var path: String = str(sample.get_path())
	assert_that(DevState.read(get_tree(), path, "spot").value).is_equal(Vector2(1.5, -2.0))
	assert_that(DevState.read(get_tree(), path, "spot:y").value).is_equal(-2.0)
	assert_str(DevState.read(get_tree(), path, "nope").error).contains("Property not found")
	assert_str(DevState.read(get_tree(), "/root/NoSuchNode", "x").error).contains("Node not found")
	assert_str(DevState.read(get_tree(), "Relative", "x").error).is_equal("No current scene")


func test_declared_type_tells_typed_from_untyped() -> void:
	var sample: Sample = auto_free(Sample.new())
	assert_int(DevState.declared_type(sample, "count")).is_equal(TYPE_INT)
	assert_int(DevState.declared_type(sample, "spot")).is_equal(TYPE_VECTOR2)
	assert_int(DevState.declared_type(sample, "spot:x")).is_equal(TYPE_FLOAT)
	assert_int(DevState.declared_type(sample, "loose")).is_equal(TYPE_NIL)


func test_method_arguments_follow_the_declared_parameters() -> void:
	var sample: Sample = auto_free(Sample.new())
	var converted: Array = []
	assert_str(DevState.method_arguments(sample, "move", [[1.0, 2.0], 3.0], converted)).is_empty()
	assert_that(converted).is_equal([Vector2(1, 2), 3])
	assert_int(typeof(converted[1])).is_equal(TYPE_INT)
	converted = []
	assert_str(DevState.method_arguments(sample, "move", [{"x": 1, "y": 2}], converted)).is_empty()
	assert_str(DevState.method_arguments(sample, "move", [], [])).contains("takes 2 arguments")
	assert_str(DevState.method_arguments(sample, "move", [1.0, 2.0, 3.0], [])).contains("got 3")
	assert_str(DevState.method_arguments(sample, "move", ["up"], [])).contains("argument 1 (to)")
