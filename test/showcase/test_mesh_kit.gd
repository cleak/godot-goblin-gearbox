extends GdUnitTestSuite
## The showcase's procedural meshes (showcase/game/mesh_kit.gd): every face looks outward and
## is wound the way Godot draws a front face, or light and culling go wrong on it.


func test_a_rounded_box_has_outward_normals_and_front_faces_outside() -> void:
	var box: ArrayMesh = MeshKit.rounded_box(Vector3(1.0, 0.4, 0.6), 0.08, 2)
	_assert_outward(box, Vector3.ZERO)


func test_a_rounded_box_keeps_its_size() -> void:
	var box: ArrayMesh = MeshKit.rounded_box(Vector3(1.0, 0.4, 0.6), 0.08, 2)
	var bounds: AABB = box.get_aabb()
	assert_vector(bounds.size).is_equal_approx(Vector3(1.0, 0.4, 0.6), Vector3.ONE * 0.001)


func test_a_rounded_box_never_rounds_past_its_thinnest_side() -> void:
	var box: ArrayMesh = MeshKit.rounded_box(Vector3(1.0, 0.1, 1.0), 0.5, 1)
	var bounds: AABB = box.get_aabb()
	assert_float(bounds.size.y).is_equal_approx(0.1, 0.001)


func test_a_gear_rim_faces_outward() -> void:
	var gear: ArrayMesh = MeshKit.gear_rim(12, 0.5, 0.08, 0.09, 0.08)
	var arrays: Array = gear.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	assert_int(points.size() % 3).is_zero()
	for i: int in range(0, points.size(), 3):
		var a: Vector3 = points[i]
		var b: Vector3 = points[i + 1]
		var c: Vector3 = points[i + 2]
		# Godot's front faces are clockwise seen from outside: the cross product points in.
		assert_float((b - a).cross(c - a).dot(normals[i])).is_less_equal(0.0)


## A tube's normals point away from its axis.
func test_a_tube_faces_away_from_its_axis() -> void:
	var path: Array[Vector3] = [Vector3.ZERO, Vector3(0.1, 0.5, 0.0), Vector3(0.0, 1.0, 0.1)]
	var radii: Array[float] = [0.2, 0.15, 0.1]
	var tube: ArrayMesh = MeshKit.tube(path, radii, 8)
	var arrays: Array = tube.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	assert_int(points.size()).is_equal(24)  # three rings of eight, shared once indexed
	for i: int in range(points.size()):
		var nearest: Vector3 = path[0]
		for centre: Vector3 in path:
			if points[i].distance_to(centre) < points[i].distance_to(nearest):
				nearest = centre
		assert_float(normals[i].dot(points[i] - nearest)).is_greater(0.0)


func test_a_boulder_faces_outward() -> void:
	var rock: ArrayMesh = MeshKit.smooth_rock(0.4, 0.3, 3)
	_assert_outward(rock, rock.get_aabb().get_center())


func test_the_island_root_faces_outward() -> void:
	var root: ArrayMesh = MeshKit.island_root(4.0, 6.0, 11)
	var arrays: Array = root.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	var outward: int = 0
	for i: int in range(points.size()):
		var axis: Vector3 = Vector3(0.0, points[i].y, 0.0)
		if normals[i].dot(points[i] - axis) > 0.0 or normals[i].y > 0.5:
			outward += 1
	# The crooked tip bends past its own axis in places: nearly all, not all.
	assert_float(float(outward) / points.size()).is_greater(0.95)


## The flat top and the sides meet at a hard edge. Shared rim normals would lean the top band
## of the sides upward, and the triplanar shader would smear the texture straight down it.
func test_the_island_root_sides_meet_its_top_at_a_hard_edge() -> void:
	var root: ArrayMesh = MeshKit.island_root(4.0, 6.0, 11)
	var arrays: Array = root.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	var rim: int = 0
	for i: int in range(points.size()):
		var on_rim: bool = (
			absf(points[i].y) < 0.001 and Vector2(points[i].x, points[i].z).length() > 1.0
		)
		if not on_rim:
			continue
		rim += 1
		var up: float = normals[i].y
		(
			assert_bool(up > 0.99 or up < 0.8)
			. override_failure_message(
				"rim normal %s leans between the top and the side" % normals[i]
			)
			. is_true()
		)
	assert_int(rim).is_greater(0)


func _assert_outward(mesh: ArrayMesh, centre: Vector3) -> void:
	var arrays: Array = mesh.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	var order: PackedInt32Array = PackedInt32Array()
	if arrays[Mesh.ARRAY_INDEX] != null:
		order = arrays[Mesh.ARRAY_INDEX]
	if order.is_empty():
		order = PackedInt32Array(range(points.size()))
	assert_int(points.size()).is_greater(0)
	for i: int in range(points.size()):
		assert_float(normals[i].dot(points[i] - centre)).is_greater(0.0)
	for i: int in range(0, order.size(), 3):
		var a: Vector3 = points[order[i]]
		var b: Vector3 = points[order[i + 1]]
		var c: Vector3 = points[order[i + 2]]
		var facing: Vector3 = normals[order[i]] + normals[order[i + 1]] + normals[order[i + 2]]
		# Godot's front faces are clockwise seen from outside: the cross product points in.
		assert_float((b - a).cross(c - a).dot(facing)).is_less_equal(0.0)
