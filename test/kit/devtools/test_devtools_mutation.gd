extends GdUnitTestSuite
## set_state and run_method: the gate, JSON converted to the type of its target, typed
## collections, and replies that do not claim what did not happen.

const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const ErrorLog := preload("res://addons/agent_devtools/dev_tools_errors.gd")
const ALLOW_MUTATION: String = "devtools/allow_mutation"


class Sample:
	extends Node2D
	var count: int = 3
	var spot: Vector2 = Vector2(1, 2)
	var tint: Color = Color.WHITE
	var label: StringName = &"idle"
	@warning_ignore("untyped_declaration") var loose = 5
	var calls: int = 0
	var numbers: Array[int] = [1, 2, 3]
	var cells: Array[Vector2i] = []
	var flags: Dictionary[String, bool] = {"lit": false}
	var scores: Dictionary[int, float] = {}
	var friends: Array[Node] = []
	## A setter that limits what it takes.
	var capped: int = 5:
		set(value):
			capped = mini(value, 10)
	## Read-only: it has no setter.
	var fixed: int:
		get:
			return 7
	## Where fail_like_a_script reports to: the DevTools under test.
	var error_log: ErrorLog = null

	func add(amount: int, at: Vector2 = Vector2.ZERO) -> int:
		calls += 1
		count += amount
		spot = at
		return count

	func add_later(amount: int) -> int:
		await get_tree().process_frame
		count += amount
		return count

	func total(values: Array[int], bonus: Dictionary[String, int] = {}) -> int:
		var sum: int = bonus.get("bonus", 0)
		for value: int in values:
			sum += value
		return sum

	func befriend(others: Array[Node]) -> int:
		return others.size()

	## What a method that hits a script error looks like from outside: the engine logs the
	## error and the call returns null. A real one would fail the test run.
	func fail_like_a_script() -> Variant:
		error_log.record(ErrorLog.KIND_SCRIPT, "No key 'missing'", "sample.gd", 12)
		return null


var _sample: Sample = null
var _sample_path: String = ""


func before_test() -> void:
	_sample = auto_free(Sample.new())
	_sample.name = "Sample"
	add_child(_sample)
	_sample_path = str(_sample.get_path())


func after_test() -> void:
	ProjectSettings.set_setting(ALLOW_MUTATION, false)


func test_mutation_is_refused_unless_the_project_allows_it() -> void:
	assert_bool(ProjectSettings.get_setting(ALLOW_MUTATION, true) == false).is_true()
	var dev_tools: DevToolsScript = _new_dev_tools()
	var args: Dictionary = {"node_path": _sample_path, "property": "count", "value": 9}
	for action: String in ["set_state", "run_method"]:
		var reply: Dictionary = await dev_tools.execute(action, args)
		assert_bool(reply["success"]).is_false()
		assert_str(str(reply["message"])).contains("Refused").contains(ALLOW_MUTATION)
	assert_int(_sample.count).is_equal(3)


func test_set_state_converts_json_to_the_property_type() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var rows: Array[Array] = [
		["count", 9.0, 9],
		["spot", {"x": 4, "y": 5.5}, Vector2(4, 5.5)],
		["spot:x", -1.0, -1.0],
		["position", [10.0, 20.0], Vector2(10, 20)],
		["tint", "#ff0000", Color.RED],
		["label", "run", &"run"],
		["loose", 6.0, 6],
		["loose", "text", "text"],
		["capped", 8.0, 8],
	]
	for row: Array in rows:
		var property: String = row[0]
		var reply: Dictionary = await dev_tools.execute(
			"set_state", {"node_path": _sample_path, "property": property, "value": row[1]}
		)
		assert_str(str(reply["message"])).is_equal("State updated")
		var now: Variant = _sample.get_indexed(NodePath(property))
		assert_int(typeof(now)).is_equal(typeof(row[2]))
		assert_that(now).is_equal(row[2])


func test_set_state_refuses_what_does_not_fit() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var bad: Dictionary = {"count": "nine", "spot": {"x": 1}, "no_such_property": 1}
	for property: String in bad:
		var reply: Dictionary = await dev_tools.execute(
			"set_state", {"node_path": _sample_path, "property": property, "value": bad[property]}
		)
		assert_bool(reply["success"]).is_false()
	assert_int(_sample.count).is_equal(3)
	assert_that(_sample.spot).is_equal(Vector2(1, 2))
	var no_value: Dictionary = await dev_tools.execute(
		"set_state", {"node_path": _sample_path, "property": "count"}
	)
	assert_str(str(no_value["message"])).is_equal("Missing 'value' argument")
	var wrong: Dictionary = await dev_tools.execute(
		"set_state", {"node_path": _sample_path, "property": "count", "value": "nine"}
	)
	# Named the way a NodePath names a property.
	assert_str(str(wrong["message"])).starts_with("Cannot set %s:count: " % _sample_path)


## The engine drops a plain list assigned to a typed array without a word: the list must be
## built with the element type the property declares.
func test_set_state_fills_typed_collections() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var numbers: Dictionary = await _set_property(dev_tools, "numbers", [4.0, 5.0, 6.0])
	assert_str(str(numbers["message"])).is_equal("State updated")
	assert_that(numbers["data"]["value"]).is_equal([4, 5, 6])
	assert_that(_sample.numbers).is_equal([4, 5, 6])
	assert_int(typeof(_sample.numbers[0])).is_equal(TYPE_INT)
	await _set_property(dev_tools, "cells", [{"x": 1, "y": 2}, [3.0, 4.0]])
	assert_that(_sample.cells).is_equal([Vector2i(1, 2), Vector2i(3, 4)])
	await _set_property(dev_tools, "flags", {"lit": true, "open": false})
	assert_that(_sample.flags).is_equal({"lit": true, "open": false})
	await _set_property(dev_tools, "scores", {"3": 1.5})
	assert_that(_sample.scores).is_equal({3: 1.5})
	var emptied: Dictionary = await _set_property(dev_tools, "friends", [])
	assert_bool(emptied["success"]).is_true()


func test_set_state_refuses_items_a_typed_collection_cannot_hold() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var bad: Array[Array] = [
		["numbers", ["a", "b"], "item 0: expected int"],
		["numbers", [1.0, 2.5], "item 1: expected a whole number"],
		["numbers", "1,2", "expected Array"],
		["cells", [[1.0]], "item 0: expected Vector2i"],
		["flags", {"lit": 1.0}, 'key "lit": expected bool'],
		["scores", {"three": 1.0}, 'key "three": a JSON key cannot be read as int'],
		["friends", ["Player"], "item 0: an object cannot be sent"],
	]
	for row: Array in bad:
		var property: String = row[0]
		var expected: String = row[2]
		var reply: Dictionary = await _set_property(dev_tools, property, row[1])
		assert_bool(reply["success"]).is_false()
		assert_str(str(reply["message"])).contains(expected)
	assert_that(_sample.numbers).is_equal([1, 2, 3])
	assert_that(_sample.flags).is_equal({"lit": false})


## A reply never says "updated" for a property that still holds its old value.
func test_set_state_fails_when_the_property_keeps_its_value() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var read_only: Dictionary = await _set_property(dev_tools, "fixed", 9.0)
	assert_bool(read_only["success"]).is_false()
	assert_str(str(read_only["message"])).contains(":fixed kept its value 7").contains("refused 9")
	# A setter that changes the value it is given did set something.
	var clamped: Dictionary = await _set_property(dev_tools, "capped", 99.0)
	assert_bool(clamped["success"]).is_true()
	assert_that(clamped["data"]["value"]).is_equal(10)
	# Asked for more again: nothing changes any more, and the reply says so.
	var stuck: Dictionary = await _set_property(dev_tools, "capped", 99.0)
	assert_bool(stuck["success"]).is_false()
	assert_str(str(stuck["message"])).contains("kept its value 10")
	# Setting the value it already has is a success.
	var same: Dictionary = await _set_property(dev_tools, "capped", 10.0)
	assert_bool(same["success"]).is_true()


func test_run_method_converts_arguments_and_awaits_the_result() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var reply: Dictionary = await dev_tools.execute(
		"run_method", {"node_path": _sample_path, "method": "add", "args": [2, {"x": 7, "y": 8}]}
	)
	assert_that(reply["data"]).is_equal({"result": 5})
	assert_that(_sample.spot).is_equal(Vector2(7, 8))
	var later: Dictionary = await dev_tools.execute(
		"run_method", {"node_path": _sample_path, "method": "add_later", "args": [1]}
	)
	assert_that(later["data"]).is_equal({"result": 6})
	var typed: Dictionary = await dev_tools.execute(
		"run_method",
		{"node_path": _sample_path, "method": "total", "args": [[1, 2, 3], {"bonus": 4}]}
	)
	assert_that(typed["data"]).is_equal({"result": 10})
	var nobody: Dictionary = await dev_tools.execute(
		"run_method", {"node_path": _sample_path, "method": "befriend", "args": [[]]}
	)
	assert_that(nobody["data"]).is_equal({"result": 0})


## A method that raises returns null and the engine only logs it: the reply must not read
## "Method called".
func test_run_method_fails_when_the_method_raised_a_script_error() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	_sample.error_log = dev_tools.get("_errors")
	var reply: Dictionary = await dev_tools.execute(
		"run_method", {"node_path": _sample_path, "method": "fail_like_a_script"}
	)
	assert_bool(reply["success"]).is_false()
	var raised: String = "raised a script error: No key 'missing' (sample.gd:12)"
	assert_str(str(reply["message"])).is_equal("%s:fail_like_a_script %s" % [_sample_path, raised])
	assert_that(reply["data"]).is_equal({"result": null})


func test_run_method_refuses_bad_calls_without_calling() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var bad_calls: Array[Dictionary] = [
		{"method": "no_such_method"},
		{"method": "add"},
		{"method": "add", "args": ["two"]},
		{"method": "add", "args": [1, [0, 0], 3]},
		{"method": "add", "args": "1"},
		{"method": "total", "args": [["one"]]},
		{"method": "total", "args": [[1], {"bonus": "big"}]},
	]
	for bad_call: Dictionary in bad_calls:
		bad_call["node_path"] = _sample_path
		var reply: Dictionary = await dev_tools.execute("run_method", bad_call)
		assert_bool(reply["success"]).is_false()
	assert_int(_sample.calls).is_zero()


## Its own private methods skip the checks its commands make: `_validate` would load a scene
## from user://, whose scripts then run in the game.
func test_run_method_cannot_call_devtools_itself() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var reply: Dictionary = await (
		dev_tools
		. execute(
			"run_method",
			{
				"node_path": str(dev_tools.get_path()),
				"method": "_validate",
				"args": [["user://no_such_scene.tscn"], false],
			}
		)
	)
	assert_bool(reply["success"]).is_false()
	assert_str(str(reply["message"])).is_equal("run_method cannot call DevTools itself")


## That refusal stops a slip, not an attacker, and the docs say so (docs/devtools.md,
## Safety): with mutation on, a method of another node reaches DevTools all the same.
func test_run_method_reaches_devtools_through_another_node() -> void:
	var dev_tools: DevToolsScript = _mutating_dev_tools()
	var reply: Dictionary = await (
		dev_tools
		. execute(
			"run_method",
			{
				"node_path": str(get_path()),
				"method": "propagate_call",
				"args": ["set_meta", ["reached", true]],
			}
		)
	)
	assert_bool(reply["success"]).is_true()
	assert_bool(dev_tools.has_meta("reached")).is_true()
	propagate_call("remove_meta", ["reached"])


func _set_property(dev_tools: DevToolsScript, property: String, value: Variant) -> Dictionary:
	return await dev_tools.execute(
		"set_state", {"node_path": _sample_path, "property": property, "value": value}
	)


## A DevTools built while devtools/allow_mutation is true (in memory only; never saved).
## It answers execute() without being started: no mailbox, no files.
func _mutating_dev_tools() -> DevToolsScript:
	ProjectSettings.set_setting(ALLOW_MUTATION, true)
	return _new_dev_tools()


func _new_dev_tools() -> DevToolsScript:
	var dev_tools: DevToolsScript = auto_free(DevToolsScript.new())
	add_child(dev_tools)
	dev_tools.stop()
	return dev_tools
