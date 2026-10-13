extends RefCounted
## Checks a scene by loading and instantiating it. Complements tools/lint_project.gd, which
## reads scene files without running them.
##
## An issue is {"severity": "error" | "warning" | "info", "code", "message"}.
##
## Errors, which always block (has_blocking_issues):
##   file_not_found, load_failed, instantiate_failed
##   missing_script        a node's script reference is gone
##   script_not_compiled   a node's script has a parse or type error
##   script_not_attached   the engine refused to attach a node's script
##   invalid_connection    a signal connection whose node, signal or method is gone
##   engine_error          anything else the engine logged as an error meanwhile
##
## Warnings, which block only when asked to be strict, because a game may fill these in from
## code: missing_texture, missing_mesh, missing_shader, missing_collision_shape,
## missing_resource, invalid_animation_path.
##
## Notes ("info"), which never block: relative_nodepath, missing_audio.
##
## Preload it; it declares no class_name.

const ErrorLog := preload("res://addons/agent_devtools/dev_tools_errors.gd")

const ERROR: String = "error"
const WARNING: String = "warning"
const INFO: String = "info"


## Every issue found in the scene at `scene_path`.
static func validate_scene(scene_path: String) -> Array[Dictionary]:
	var issues: Array[Dictionary] = []
	if not FileAccess.file_exists(scene_path):
		_add(issues, ERROR, "file_not_found", "Scene file does not exist: %s" % scene_path)
		return issues
	# A script that fails to compile still loads, as a script that cannot run, and the scene
	# around it loads and instantiates. The engine only logs the failure, so listen for it.
	var logged: ErrorLog = ErrorLog.new()
	OS.add_logger(logged)
	var packed_scene: PackedScene = load(scene_path) as PackedScene
	if packed_scene == null:
		_add(issues, ERROR, "load_failed", "Failed to load scene: %s" % scene_path)
	else:
		check_packed_scene(packed_scene, issues)
	OS.remove_logger(logged)
	add_logged_errors(logged.since(0), issues)
	return issues


## True when an issue fails the validation: any error, and with `strict` any warning too.
## An issue with no known severity counts as an error.
static func has_blocking_issues(issues: Array[Dictionary], strict: bool = false) -> bool:
	for issue: Dictionary in issues:
		var severity: String = str(issue.get("severity", ERROR))
		if severity != INFO and (strict or severity != WARNING):
			return true
	return false


## How many of `issues` have `severity`.
static func count(issues: Array[Dictionary], severity: String) -> int:
	var found: int = 0
	for issue: Dictionary in issues:
		if issue.get("severity") == severity:
			found += 1
	return found


## Checks a loaded scene: its stored properties, then one instance of it.
static func check_packed_scene(packed_scene: PackedScene, issues: Array[Dictionary]) -> void:
	var state: SceneState = packed_scene.get_state()
	_check_stored_scripts(state, issues, "")
	_check_stored_properties(state, issues)
	var instance: Node = packed_scene.instantiate()
	if instance == null:
		_add(issues, ERROR, "instantiate_failed", "Failed to instantiate the scene")
		return
	_check_attached_scripts(state, instance, issues)
	_check_connections(state, instance, issues)
	check_branch(instance, issues)
	# The instance never entered the tree, so it can be freed at once.
	instance.free()


## Checks `root` and its descendants. Issue paths are built from node names, because the
## branch need not be inside the tree (Node.get_path() fails there).
static func check_branch(root: Node, issues: Array[Dictionary], path: String = "") -> void:
	var node_path: String = str(root.name) if path.is_empty() else path
	_check_node(root, issues, node_path)
	for child: Node in root.get_children():
		check_branch(child, issues, "%s/%s" % [node_path, child.name])


## Adds one error per row of an ErrorLog: what the engine logged during a check.
static func add_logged_errors(rows: Array[Dictionary], issues: Array[Dictionary]) -> void:
	for row: Dictionary in rows:
		_add(issues, ERROR, "engine_error", ErrorLog.describe(row))


static func _add(issues: Array[Dictionary], severity: String, code: String, text: String) -> void:
	issues.append({"severity": severity, "code": code, "message": text})


## The scripts `state` stores, and those of the scenes it instances (a child scene keeps its
## nodes' scripts in its own state): each must exist and compile. The engine loads a script
## with a parse error as a script that cannot run and attaches nothing to the node, so the
## stored value is the place to look. `where` names the child scene, "" for the scene itself.
static func _check_stored_scripts(
	state: SceneState, issues: Array[Dictionary], where: String
) -> void:
	for node: int in range(state.get_node_count()):
		var node_path: String = str(state.get_node_path(node))
		var child_scene: PackedScene = state.get_node_instance(node)
		if child_scene != null:
			_check_stored_scripts(
				child_scene.get_state(), issues, " of %s" % child_scene.resource_path
			)
		var value: Variant = _stored_script(state, node)
		if value == null:
			continue  # none, or an inherited scene that takes its base scene's script off
		if not value is Script:
			_add(
				issues,
				ERROR,
				"missing_script",
				"Node '%s'%s has a missing or invalid script reference" % [node_path, where]
			)
			continue
		var stored: Script = value
		if not stored.can_instantiate():
			var source: String = stored.resource_path
			if source.is_empty() or source.contains("::"):
				source = "(built-in)"
			_add(
				issues,
				ERROR,
				"script_not_compiled",
				"Node '%s'%s: script %s did not compile" % [node_path, where, source]
			)


## What node `node` of `state` stores as its script; null when it stores none.
static func _stored_script(state: SceneState, node: int) -> Variant:
	for property: int in range(state.get_node_property_count(node)):
		if state.get_node_property_name(node, property) == "script":
			return state.get_node_property_value(node, property)
	return null


static func _check_stored_properties(state: SceneState, issues: Array[Dictionary]) -> void:
	for node: int in range(state.get_node_count()):
		var node_path: String = str(state.get_node_path(node))
		for property: int in range(state.get_node_property_count(node)):
			var name: String = state.get_node_property_name(node, property)
			var value: Variant = state.get_node_property_value(node, property)
			if value is MissingResource:
				_add(
					issues,
					WARNING,
					"missing_resource",
					"Node '%s' property '%s' references a missing resource" % [node_path, name]
				)
			elif value is NodePath and str(value).contains(".."):
				_add(
					issues,
					INFO,
					"relative_nodepath",
					"Node '%s' property '%s' uses relative path: %s" % [node_path, name, value]
				)


## A node the scene stores a script for must carry one once instantiated. A script that
## did not compile is reported by _check_stored_scripts.
static func _check_attached_scripts(
	state: SceneState, root: Node, issues: Array[Dictionary]
) -> void:
	for node: int in range(state.get_node_count()):
		var value: Variant = _stored_script(state, node)
		if not value is Script:
			continue
		var stored: Script = value
		var node_path: NodePath = state.get_node_path(node)
		var instance: Node = root.get_node_or_null(node_path)
		if instance != null and instance.get_script() == null and stored.can_instantiate():
			_add(
				issues,
				ERROR,
				"script_not_attached",
				"Node '%s' stores a script the engine did not attach" % node_path
			)


static func _check_connections(state: SceneState, root: Node, issues: Array[Dictionary]) -> void:
	for connection: int in range(state.get_connection_count()):
		var source_path: NodePath = state.get_connection_source(connection)
		var target_path: NodePath = state.get_connection_target(connection)
		var signal_name: StringName = state.get_connection_signal(connection)
		var method: StringName = state.get_connection_method(connection)
		var source: Node = root.get_node_or_null(source_path)
		var target: Node = root.get_node_or_null(target_path)
		var problem: String = ""
		if source == null:
			problem = "source node is missing"
		elif target == null:
			problem = "target node is missing"
		elif not source.has_signal(signal_name):
			problem = "source signal '%s' does not exist" % signal_name
		elif not target.has_method(method):
			problem = "target method '%s' does not exist" % method
		if not problem.is_empty():
			_add(
				issues,
				ERROR,
				"invalid_connection",
				"Connection %s -> %s: %s" % [source_path, target_path, problem]
			)


static func _check_node(node: Node, issues: Array[Dictionary], path: String) -> void:
	var type: String = node.get_class()
	if node is MeshInstance3D:
		_check_mesh_instance(node as MeshInstance3D, issues, path)
	elif node is AnimationPlayer:
		_check_animation_player(node as AnimationPlayer, issues, path)
	elif (node is Sprite2D or node is Sprite3D) and node.get("texture") == null:
		_add(issues, WARNING, "missing_texture", "%s '%s' has no texture assigned" % [type, path])
	elif (node is CollisionShape2D or node is CollisionShape3D) and node.get("shape") == null:
		_add(
			issues,
			WARNING,
			"missing_collision_shape",
			"%s '%s' has no shape assigned" % [type, path]
		)
	elif node is AudioStreamPlayer and node.get("stream") == null:
		_add(issues, INFO, "missing_audio", "%s '%s' has no stream assigned" % [type, path])


static func _check_mesh_instance(
	mesh_instance: MeshInstance3D, issues: Array[Dictionary], path: String
) -> void:
	if mesh_instance.mesh == null:
		_add(issues, WARNING, "missing_mesh", "MeshInstance3D '%s' has no mesh assigned" % path)
		return
	for surface: int in range(mesh_instance.mesh.get_surface_count()):
		var material: ShaderMaterial = mesh_instance.get_active_material(surface) as ShaderMaterial
		if material != null and material.shader == null:
			_add(
				issues,
				WARNING,
				"missing_shader",
				(
					"MeshInstance3D '%s' surface %d has a ShaderMaterial with no shader"
					% [path, surface]
				)
			)


static func _check_animation_player(
	player: AnimationPlayer, issues: Array[Dictionary], path: String
) -> void:
	var animation_root: Node = player.get_node_or_null(player.root_node)
	if animation_root == null:
		return  # the tracks start outside this scene; nothing to check them against
	for animation_name: String in player.get_animation_list():
		var animation: Animation = player.get_animation(animation_name)
		for track: int in range(animation.get_track_count()):
			var track_path: NodePath = animation.track_get_path(track)
			var node_part: NodePath = NodePath(track_path.get_concatenated_names())
			if animation_root.get_node_or_null(node_part) == null:
				_add(
					issues,
					WARNING,
					"invalid_animation_path",
					(
						"AnimationPlayer '%s' animation '%s' track %d targets a missing node: %s"
						% [path, animation_name, track, track_path]
					)
				)
