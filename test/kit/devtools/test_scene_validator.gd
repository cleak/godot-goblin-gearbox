extends GdUnitTestSuite
## Scene checks: what fails a validation, what only warns, and paths of nodes outside the tree.

const SceneValidator := preload("res://addons/agent_devtools/scene_validator.gd")
const ErrorLog := preload("res://addons/agent_devtools/dev_tools_errors.gd")
const BROKEN_SCENE: String = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="Timer" type="Timer" parent="."]

[node name="Skin" type="Sprite2D" parent="."]

[connection signal="timeout" from="Timer" to="." method="_on_timer_gone"]
"""
## The usual shape of a scene whose art is set from code: nothing here is broken.
const FILLED_IN_CODE_SCENE: String = """[gd_scene format=3]

[node name="Enemy" type="CharacterBody2D"]

[node name="Skin" type="Sprite2D" parent="."]

[node name="Hitbox" type="CollisionShape2D" parent="."]

[node name="Model" type="MeshInstance3D" parent="."]
"""


func test_the_sample_scene_has_no_issues() -> void:
	assert_array(SceneValidator.validate_scene("res://main.tscn")).is_empty()


func test_a_missing_file_is_an_error() -> void:
	var issues: Array[Dictionary] = SceneValidator.validate_scene("res://no_such_scene.tscn")
	assert_int(issues.size()).is_equal(1)
	assert_str(str(issues[0]["code"])).is_equal("file_not_found")
	assert_bool(SceneValidator.has_blocking_issues(issues)).is_true()


func test_a_connection_to_a_missing_method_and_an_empty_sprite_are_found() -> void:
	var issues: Array[Dictionary] = _validate_text(BROKEN_SCENE)
	assert_array(_codes(issues)).contains_exactly_in_any_order(
		["invalid_connection", "missing_texture"]
	)
	for issue: Dictionary in issues:
		if issue["code"] == "invalid_connection":
			assert_str(str(issue["severity"])).is_equal("error")
			assert_str(str(issue["message"])).contains("_on_timer_gone")
		else:
			assert_str(str(issue["severity"])).is_equal("warning")
			assert_str(str(issue["message"])).contains("Root/Skin")
	assert_bool(SceneValidator.has_blocking_issues(issues)).is_true()


func test_a_scene_filled_in_from_code_passes_unless_strict() -> void:
	var issues: Array[Dictionary] = _validate_text(FILLED_IN_CODE_SCENE)
	assert_array(_codes(issues)).contains_exactly_in_any_order(
		["missing_texture", "missing_collision_shape", "missing_mesh"]
	)
	assert_int(SceneValidator.count(issues, SceneValidator.WARNING)).is_equal(3)
	assert_int(SceneValidator.count(issues, SceneValidator.ERROR)).is_zero()
	assert_bool(SceneValidator.has_blocking_issues(issues)).is_false()
	assert_bool(SceneValidator.has_blocking_issues(issues, true)).is_true()


## The engine loads a script with a parse error as a script that cannot run, and the scene
## around it loads and instantiates, the node simply without its script: only the stored
## script tells. Built in memory, because a real parse error would fail this test run too:
## the scene's compiled script is swapped for one that was never compiled.
func test_a_script_that_did_not_compile_is_an_error() -> void:
	var compiled: GDScript = _compiled_script()
	var packed_scene: PackedScene = _scene_with_script_on_child(compiled)
	var issues: Array[Dictionary] = []
	SceneValidator.check_packed_scene(packed_scene, issues)
	assert_array(issues).is_empty()

	var uncompiled: GDScript = GDScript.new()
	uncompiled.source_code = "extends Node\nfunc broken( -> void:\n\tpass\n"
	assert_bool(uncompiled.can_instantiate()).is_false()
	var bundled: Dictionary = packed_scene.get("_bundled")
	var variants: Array = bundled["variants"]
	variants[variants.find(compiled)] = uncompiled
	packed_scene.set("_bundled", bundled)
	SceneValidator.check_packed_scene(packed_scene, issues)
	assert_array(_codes(issues)).contains_exactly(["script_not_compiled"])
	assert_str(str(issues[0]["severity"])).is_equal("error")
	assert_str(str(issues[0]["message"])).is_equal(
		"Node './Child': script (built-in) did not compile"
	)
	assert_bool(SceneValidator.has_blocking_issues(issues)).is_true()


## A child scene keeps its nodes' scripts in its own state: the scene that instances it is
## broken too, and says where.
func test_a_broken_script_in_an_instanced_scene_is_found_from_outside() -> void:
	var compiled: GDScript = _compiled_script()
	var inner: PackedScene = _scene_with_script_on_child(compiled)
	var bundled: Dictionary = inner.get("_bundled")
	var variants: Array = bundled["variants"]
	variants[variants.find(compiled)] = GDScript.new()
	inner.set("_bundled", bundled)
	inner.take_over_path("res://gdunit_inner_%d.tscn" % OS.get_process_id())
	var root: Node = auto_free(Node.new())
	root.name = "Outer"
	var instanced: Node = inner.instantiate()
	instanced.name = "Enemy"
	root.add_child(instanced)
	instanced.owner = root
	var outer: PackedScene = PackedScene.new()
	assert_int(outer.pack(root)).is_equal(OK)
	var issues: Array[Dictionary] = []
	SceneValidator.check_packed_scene(outer, issues)
	assert_array(_codes(issues)).contains_exactly(["script_not_compiled"])
	assert_str(str(issues[0]["message"])).contains("Child' of res://gdunit_inner_")


## An inherited scene that takes its base scene's script off stores "script = null".
func test_a_script_set_to_null_is_not_an_issue() -> void:
	var scene: String = '[gd_scene format=3]\n\n[node name="Root" type="Node"]\nscript = null\n'
	assert_array(_validate_text(scene)).is_empty()


func test_what_the_engine_logged_during_a_check_is_an_error() -> void:
	var logged: ErrorLog = ErrorLog.new()
	logged.record(
		ErrorLog.KIND_SCRIPT, "Parse Error: Expected parameter name.", "res://enemy.gd", 3
	)
	var issues: Array[Dictionary] = []
	SceneValidator.add_logged_errors(logged.since(0), issues)
	assert_array(_codes(issues)).contains_exactly(["engine_error"])
	assert_str(str(issues[0]["message"])).is_equal(
		"Parse Error: Expected parameter name. (res://enemy.gd:3)"
	)
	assert_bool(SceneValidator.has_blocking_issues(issues)).is_true()


func test_errors_block_warnings_block_when_strict_and_notes_never() -> void:
	var note: Array[Dictionary] = [{"severity": "info", "code": "relative_nodepath"}]
	var warning: Array[Dictionary] = [{"severity": "warning", "code": "missing_texture"}]
	var error: Array[Dictionary] = [{"severity": "error", "code": "missing_script"}]
	var unlabeled: Array[Dictionary] = [{"code": "unknown"}]
	assert_bool(SceneValidator.has_blocking_issues([])).is_false()
	assert_bool(SceneValidator.has_blocking_issues([], true)).is_false()
	assert_bool(SceneValidator.has_blocking_issues(note)).is_false()
	assert_bool(SceneValidator.has_blocking_issues(note, true)).is_false()
	assert_bool(SceneValidator.has_blocking_issues(warning)).is_false()
	assert_bool(SceneValidator.has_blocking_issues(warning, true)).is_true()
	assert_bool(SceneValidator.has_blocking_issues(error)).is_true()
	assert_bool(SceneValidator.has_blocking_issues(unlabeled)).is_true()


## The validator inspects a branch that never enters the tree, where Node.get_path() fails;
## issue paths are built from node names.
func test_issue_paths_come_from_node_names_outside_the_tree() -> void:
	var root: Node = auto_free(Node.new())
	root.name = "Root"
	var body: Node2D = Node2D.new()
	body.name = "Body"
	root.add_child(body)
	var shape: CollisionShape2D = CollisionShape2D.new()
	shape.name = "Hitbox"
	body.add_child(shape)
	assert_bool(root.is_inside_tree()).is_false()
	var issues: Array[Dictionary] = []
	SceneValidator.check_branch(root, issues)
	assert_int(issues.size()).is_equal(1)
	assert_str(str(issues[0]["code"])).is_equal("missing_collision_shape")
	assert_str(str(issues[0]["message"])).contains("CollisionShape2D 'Root/Body/Hitbox'")


func _compiled_script() -> GDScript:
	var compiled: GDScript = GDScript.new()
	compiled.source_code = "extends Node\n"
	assert_int(compiled.reload()).is_equal(OK)
	assert_bool(compiled.can_instantiate()).is_true()
	return compiled


## A packed scene: a root named "Root" with a child named "Child" that carries `script`.
func _scene_with_script_on_child(script: GDScript) -> PackedScene:
	var root: Node = auto_free(Node.new())
	root.name = "Root"
	var child: Node = Node.new()
	child.name = "Child"
	root.add_child(child)
	child.owner = root
	child.set_script(script)
	var packed_scene: PackedScene = PackedScene.new()
	assert_int(packed_scene.pack(root)).is_equal(OK)
	return packed_scene


## Validates `text` as a scene file in user://.
func _validate_text(text: String) -> Array[Dictionary]:
	# Unique per process (several test runs may share one user:// folder) and per text (a
	# path loaded twice may come from the resource cache).
	var scene_path: String = "user://gdunit_scene_%d_%d.tscn" % [OS.get_process_id(), text.hash()]
	var file: FileAccess = FileAccess.open(scene_path, FileAccess.WRITE)
	file.store_string(text)
	file.close()
	var issues: Array[Dictionary] = SceneValidator.validate_scene(scene_path)
	DirAccess.remove_absolute(ProjectSettings.globalize_path(scene_path))
	return issues


func _codes(issues: Array[Dictionary]) -> Array[String]:
	var codes: Array[String] = []
	for issue: Dictionary in issues:
		codes.append(str(issue["code"]))
	return codes
