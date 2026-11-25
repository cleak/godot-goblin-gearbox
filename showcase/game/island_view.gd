class_name IslandView
extends Node3D
## Builds the island from the Isle's map: turf on every terrace, dry-stone walls where the
## ground drops, grass, gnarled trees, boulders, glowing mushrooms, the spring pool and its
## waterfall, the rock it floats on, and the stones that drift around it. Reads the rules
## once; never changes them.
##
## The heavy meshes (walls, turf, trees) are the same for the same map and seed: they are
## built once per process and shared by every instance.

const CELL: float = 1.0
const STEP: float = 0.35
## How far the columns reach below the lowest terrace before the rock takes over.
const COLUMN_FOOT: float = -0.7
const TURF_DEPTH: float = 0.1
## How deep the spring pool's bed lies below the terrace it sits in.
const POOL_DEPTH: float = 0.42
## The pool's surface, below the terrace top; its outer lip sits lower still, to spill.
const WATER_LEVEL: float = 0.09
const TUFTS_PER_CELL: int = 16
const BLADES_PER_TUFT: int = 11
## How wide a blade is against the mesh's own leaf.
const BLADE_WIDTH: float = 0.5
## The chance that an open cell has a drift of flowers.
const FLOWER_CHANCE: float = 0.35
const SEED: int = 2026
const DIRECTIONS: Array[Vector2i] = [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]
const GRASS_SHADER: Shader = preload("res://showcase/shaders/grass.gdshader")
const WATER_SHADER: Shader = preload("res://showcase/shaders/water.gdshader")
const WATERFALL_SHADER: Shader = preload("res://showcase/shaders/waterfall.gdshader")

## Mesh name -> mesh, for the standard map; see the class comment.
static var _meshes: Dictionary[StringName, Mesh] = {}
static var _grass: MultiMesh = null

## 0 asleep, 1 awake: grass tips and leaves catch the aurora.
var awake: float = 0.0:
	set(value):
		awake = value
		if _grass_material != null:
			_grass_material.set_shader_parameter(&"awake", value)
			_leaf_material.set_shader_parameter(&"awake", value)
var motion_scale: float = 1.0

var _isle: Isle = null
var _grass_material: ShaderMaterial = null
var _leaf_material: ShaderMaterial = null
var _drifters: Array[Node3D] = []
var _drifter_homes: Array[Vector3] = []
var _time: float = 0.0
## Presentation's own numbers, fixed so every run looks the same.
var _random: RandomNumberGenerator = RandomNumberGenerator.new()


## The centre of a cell's top face, in this node's space.
static func cell_top(isle: Isle, cell: Vector2i) -> Vector3:
	var centre: Vector2 = (Vector2(cell) - Vector2(isle.size - Vector2i.ONE) * 0.5) * CELL
	return Vector3(centre.x, maxi(isle.height_at(cell), 0) * STEP, centre.y)


func build(isle: Isle) -> void:
	_isle = isle
	_random.seed = SEED
	# Lanternworks always builds the standard isle, so the first build serves them all.
	if not _meshes.has(&"walls"):
		_meshes[&"walls"] = _wall_mesh()
		_meshes[&"turf"] = _turf_mesh()
		_meshes[&"earth"] = _earth_mesh()
		_grass = _grass_mesh()
	var stone: ShaderMaterial = Surfaces.stone(0.7)
	_add_named("Walls", _meshes[&"walls"], stone)
	_add_named("Turf", _meshes[&"turf"], Surfaces.turf())
	_add_named("Earth", _meshes[&"earth"], Surfaces.turf())
	_grass_material = MeshKit.shader_material(GRASS_SHADER)
	var lawn: MultiMeshInstance3D = MultiMeshInstance3D.new()
	lawn.name = "Grass"
	lawn.multimesh = _grass
	lawn.material_override = _grass_material
	lawn.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(lawn)
	_random.seed = SEED + 1
	_leaf_material = Surfaces.leaves()
	var bark: ShaderMaterial = Surfaces.bark()
	for cell: Vector2i in isle.cells_with(Isle.Feature.TREE):
		_build_tree(cell_top(isle, cell), bark)
	var boulder: ShaderMaterial = Surfaces.stone(0.9)
	for cell: Vector2i in isle.cells_with(Isle.Feature.ROCK):
		_build_boulder(cell_top(isle, cell), boulder)
	_build_mushrooms()
	_build_flowers()
	_build_pool()
	_build_root()


## Grass parts around the goblin's feet.
func set_goblin(at: Vector3) -> void:
	_grass_material.set_shader_parameter(&"push_origin", to_global(at))


func _process(delta: float) -> void:
	_time += delta
	for i: int in range(_drifters.size()):
		var home: Vector3 = _drifter_homes[i]
		var angle: float = atan2(home.z, home.x) + _time * 0.05
		var orbit: Vector3 = Vector3(cos(angle), 0.0, sin(angle)) * Vector2(home.x, home.z).length()
		var bob: float = sin(_time * 0.7 + i) * 0.25 * motion_scale
		_drifters[i].position = Vector3(orbit.x, home.y + bob, orbit.z)
		_drifters[i].rotation.y = _time * 0.1 + i


func _add_named(node_name: String, mesh: Mesh, surface: Material) -> MeshInstance3D:
	var instance: MeshInstance3D = MeshKit.add_mesh(self, mesh, surface)
	instance.name = node_name
	return instance


## Where the ground under `cell`'s neighbour that way stands: its top, or -INF for none.
func _neighbour_top(cell: Vector2i, direction: Vector2i) -> float:
	var next: Vector2i = cell + direction
	if not _isle.has_ground(next):
		return -INF
	return _floor_of(next)


## The top of what stands in `cell`: the terrace, or the pool's bed.
func _floor_of(cell: Vector2i) -> float:
	var top: float = cell_top(_isle, cell).y
	if _isle.feature_at(cell) == Isle.Feature.WATER:
		return top - POOL_DEPTH
	return top


## Dry-stone walls: courses of chamfered stones wherever the ground falls away.
func _wall_mesh() -> ArrayMesh:
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	for cell: Vector2i in _isle.ground_cells():
		var top: Vector3 = cell_top(_isle, cell)
		var water: bool = _isle.feature_at(cell) == Isle.Feature.WATER
		# A pool's own walls stop under its surface, so the water spills over them.
		var crown: float = top.y - (WATER_LEVEL + 0.03 if water else TURF_DEPTH * 0.6)
		for direction: Vector2i in DIRECTIONS:
			var below: float = _neighbour_top(cell, direction)
			if below >= _floor_of(cell) - 0.01:
				continue
			var foot: float = below if below > -INF else COLUMN_FOOT - 0.35
			var out: Vector3 = Vector3(direction.x, 0.0, direction.y)
			var along: Vector3 = Vector3(absi(direction.y), 0.0, absi(direction.x))
			var face: Vector3 = top + out * CELL * 0.5
			_build_wall(tool, face, out, along, foot, crown)
	return tool.commit()


func _build_wall(
	tool: SurfaceTool, face: Vector3, out: Vector3, along: Vector3, foot: float, crown: float
) -> void:
	var y: float = foot
	var course: int = 0
	while y < crown - 0.03:
		var tall: float = minf(_random.randf_range(0.11, 0.17), crown - y)
		# Courses stagger: every other one starts half a stone in.
		var x: float = -0.52 - (0.12 if course % 2 == 1 else 0.0)
		while x < 0.52:
			var long: float = _random.randf_range(0.2, 0.38)
			if 0.52 - (x + long) < 0.1:
				long = 0.52 - x
			var deep: float = _random.randf_range(0.16, 0.24)
			var proud: float = _random.randf_range(-0.01, 0.035)
			var size: Vector3 = Vector3(long - 0.014, tall - 0.012, deep)
			var centre: Vector3 = (
				Vector3(face.x, y + tall * 0.5, face.z)
				+ along * (x + long * 0.5)
				+ out * (proud - deep * 0.5)
			)
			var turn: Basis = Basis.looking_at(-out, Vector3.UP)
			turn = turn.rotated(Vector3.UP, _random.randf_range(-0.05, 0.05))
			turn = turn.rotated(out, _random.randf_range(-0.04, 0.04))
			var shade: float = _random.randf_range(0.78, 1.12)
			var tint: Color = Color(shade, shade * _random.randf_range(0.96, 1.0), shade * 0.97)
			_append_box(tool, Transform3D(turn, centre), size, minf(tall, long) * 0.25, tint)
			x += long
		y += tall
		course += 1


## Appends a chamfered box, turned by `place`, tinted `tint` through the vertex colour.
## The box's own x runs along the wall, y up, z into it.
func _append_box(
	tool: SurfaceTool, place: Transform3D, size: Vector3, roundness: float, tint: Color
) -> void:
	var box: ArrayMesh = MeshKit.rounded_box(size, roundness, 1)
	var arrays: Array = box.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	for i: int in range(points.size()):
		tool.set_color(tint)
		tool.set_normal(place.basis * normals[i])
		tool.add_vertex(place * points[i])


## A slab of turf on every terrace top, with rounded lips.
func _turf_mesh() -> ArrayMesh:
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	for cell: Vector2i in _isle.ground_cells():
		var feature: Isle.Feature = _isle.feature_at(cell)
		if feature == Isle.Feature.WATER:
			continue
		var top: Vector3 = cell_top(_isle, cell)
		var size: Vector3 = Vector3(CELL + 0.02, TURF_DEPTH, CELL + 0.02)
		var place: Transform3D = Transform3D(Basis.IDENTITY, top - Vector3(0, TURF_DEPTH * 0.5, 0))
		_append_box(tool, place, size, 0.006, Color.WHITE)
	return tool.commit()


## Earth behind the walls, down to the rock: fills any gap between the stones.
func _earth_mesh() -> ArrayMesh:
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	for cell: Vector2i in _isle.ground_cells():
		var top: Vector3 = cell_top(_isle, cell)
		var water: bool = _isle.feature_at(cell) == Isle.Feature.WATER
		var surface_y: float = top.y - (POOL_DEPTH if water else TURF_DEPTH * 0.8)
		var foot: float = COLUMN_FOOT - 0.3
		var size: Vector3 = Vector3(CELL - 0.12, surface_y - foot, CELL - 0.12)
		var place: Transform3D = Transform3D(
			Basis.IDENTITY, Vector3(top.x, (surface_y + foot) * 0.5, top.z)
		)
		_append_box(tool, place, size, 0.03, Color.WHITE)
	return tool.commit()


## Grass in tufts: blades fan out from a few roots per cell, short and leaning outwards, so the
## lawn reads as soft clumps with turf between them, not a bed of spikes.
func _grass_mesh() -> MultiMesh:
	var transforms: Array[Transform3D] = []
	var colours: Array[Color] = []
	for cell: Vector2i in _isle.ground_cells():
		var feature: Isle.Feature = _isle.feature_at(cell)
		if feature == Isle.Feature.WATER or feature == Isle.Feature.GEARBOX:
			continue
		var top: Vector3 = cell_top(_isle, cell)
		for tuft: int in range(TUFTS_PER_CELL):
			var root: Vector3 = (
				top
				+ Vector3(_random.randf_range(-0.46, 0.46), 0.0, _random.randf_range(-0.46, 0.46))
			)
			var tuft_tall: float = _random.randf_range(0.07, 0.15)
			var tuft_shade: float = _random.randf_range(0.8, 1.15)
			for blade: int in range(BLADES_PER_TUFT):
				var around: float = _random.randf() * TAU
				var spread: float = sqrt(_random.randf()) * 0.07
				var spot: Vector3 = root + Vector3(cos(around), 0.0, sin(around)) * spread
				var tall: float = tuft_tall * _random.randf_range(0.6, 1.15)
				# Blades lean away from the tuft's middle, more the further out they stand.
				var turn: Basis = Basis(Vector3.UP, -around + PI * 0.5)
				var tilt: Basis = Basis(
					Vector3.RIGHT, spread * 6.0 + _random.randf_range(0.0, 0.25)
				)
				# Narrow blades: the mesh is a long leaf, cut down to the blade's height.
				var shape: Basis = (turn * tilt).scaled(Vector3(BLADE_WIDTH, tall, BLADE_WIDTH))
				transforms.append(Transform3D(shape, spot))
				var shade: float = tuft_shade * _random.randf_range(0.85, 1.1)
				var dry: float = _random.randf() * _random.randf()
				colours.append(Color(shade, shade, shade).lerp(Color(1.5, 1.2, 0.6), dry * 0.45))
	var multimesh: MultiMesh = MultiMesh.new()
	multimesh.transform_format = MultiMesh.TRANSFORM_3D
	multimesh.use_colors = true
	multimesh.mesh = MeshKit.grass_blade()
	multimesh.instance_count = transforms.size()
	for i: int in range(transforms.size()):
		multimesh.set_instance_transform(i, transforms[i])
		multimesh.set_instance_color(i, colours[i])
	return multimesh


## Wild flowers in small drifts: a head of petals on a short stalk, three colours.
func _build_flowers() -> void:
	var colours: Array[Color] = [
		Color(0.95, 0.93, 0.85), Color(1.0, 0.8, 0.25), Color(0.85, 0.5, 0.85)
	]
	var heads: Array[Transform3D] = []
	var tints: Array[Color] = []
	for cell: Vector2i in _isle.ground_cells():
		if not _isle.is_open(cell) or _random.randf() > FLOWER_CHANCE:
			continue
		var top: Vector3 = cell_top(_isle, cell)
		var drift: Vector3 = (
			top + Vector3(_random.randf_range(-0.3, 0.3), 0.0, _random.randf_range(-0.3, 0.3))
		)
		var colour: Color = colours[_random.randi() % colours.size()]
		for i: int in range(_random.randi_range(4, 8)):
			var spot: Vector3 = (
				drift
				+ Vector3(_random.randf_range(-0.16, 0.16), 0.0, _random.randf_range(-0.16, 0.16))
			)
			var tall: float = _random.randf_range(0.09, 0.17)
			var size: float = _random.randf_range(0.8, 1.2)
			var lean: Basis = Basis(Vector3.RIGHT, _random.randf_range(-0.4, 0.4))
			lean = Basis(Vector3.UP, _random.randf() * TAU) * lean
			heads.append(
				Transform3D(
					lean.scaled(Vector3(size, size * 0.6, size)), spot + Vector3(0, tall, 0)
				)
			)
			tints.append(colour * _random.randf_range(0.85, 1.05))
	var multimesh: MultiMesh = MultiMesh.new()
	multimesh.transform_format = MultiMesh.TRANSFORM_3D
	multimesh.use_colors = true
	multimesh.mesh = MeshKit.sphere(0.022, 8, 4)
	multimesh.instance_count = heads.size()
	for i: int in range(heads.size()):
		multimesh.set_instance_transform(i, heads[i])
		multimesh.set_instance_color(i, tints[i])
	var petals: StandardMaterial3D = MeshKit.material(Color.WHITE, 0.55)
	petals.vertex_color_use_as_albedo = true
	petals.backlight_enabled = true
	petals.backlight = Color(0.4, 0.35, 0.3)
	var bed: MultiMeshInstance3D = MultiMeshInstance3D.new()
	bed.name = "Flowers"
	bed.multimesh = multimesh
	bed.material_override = petals
	bed.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(bed)


## An old, wind-bent tree: a twisting trunk with root flare, three or four limbs, and
## a crown of lumpy leaf masses over them that glows where the low sun shines through.
func _build_tree(at: Vector3, bark: ShaderMaterial) -> void:
	var tree: Node3D = Node3D.new()
	add_child(tree)
	tree.position = (
		at + Vector3(_random.randf_range(-0.12, 0.12), 0.0, _random.randf_range(-0.12, 0.12))
	)
	tree.rotation.y = _random.randf_range(0.0, TAU)
	tree.scale = Vector3.ONE * _random.randf_range(0.9, 1.2)
	var lean: Vector3 = Vector3(
		_random.randf_range(-0.25, 0.25), 0.0, _random.randf_range(-0.25, 0.25)
	)
	var trunk: Array[Vector3] = []
	var radii: Array[float] = []
	var height: float = _random.randf_range(1.3, 1.7)
	for i: int in range(8):
		var t: float = i / 7.0
		var wander: Vector3 = (
			Vector3(sin(t * 4.0 + lean.x * 9.0), 0.0, cos(t * 3.0 + lean.z * 9.0)) * 0.06
		)
		trunk.append(Vector3(0.0, t * height, 0.0) + lean * t * t + wander * t)
		radii.append(lerpf(0.13, 0.045, pow(t, 0.8)) + 0.07 * pow(1.0 - t, 6.0))
	MeshKit.add_mesh(tree, MeshKit.tube(trunk, radii, 9), bark)
	var crowns: Array[Vector3] = [trunk[7] + Vector3(0, 0.15, 0)]
	for limb: int in range(_random.randi_range(3, 4)):
		var start_index: int = _random.randi_range(3, 6)
		var start: Vector3 = trunk[start_index]
		var angle: float = limb * TAU / 4.0 + _random.randf_range(-0.5, 0.5)
		var heading: Vector3 = Vector3(cos(angle), _random.randf_range(0.5, 0.9), sin(angle))
		heading = heading.normalized()
		var reach: float = _random.randf_range(0.45, 0.7)
		var path: Array[Vector3] = []
		var limb_radii: Array[float] = []
		for k: int in range(5):
			var s: float = k / 4.0
			var droop: Vector3 = Vector3(0.0, -0.12 * s * s, 0.0)
			path.append(start + heading * reach * s + droop)
			limb_radii.append(lerpf(radii[start_index] * 0.7, 0.015, s))
		MeshKit.add_mesh(tree, MeshKit.tube(path, limb_radii, 6), bark)
		crowns.append(path[4])
	# The crown: a big mass over the trunk, a smaller one at each limb's end.
	var lumps: Array[Vector4] = []
	for i: int in range(crowns.size()):
		var crown: Vector3 = crowns[i]
		var size: float = (
			_random.randf_range(0.56, 0.66) if i == 0 else _random.randf_range(0.38, 0.48)
		)
		lumps.append(Vector4(crown.x, crown.y + size * 0.15, crown.z, size))
	var leaves: MeshInstance3D = MeshKit.add_mesh(
		tree, MeshKit.canopy(lumps, 0.28, _random.randi()), _leaf_material
	)
	leaves.name = "Crown"
	leaves.set_instance_shader_parameter(&"phase", _random.randf_range(0.0, TAU))
	var hue: Color = Color(1.0, 1.0, 1.0).lerp(Color(1.25, 1.05, 0.7), _random.randf() * 0.6)
	leaves.set_instance_shader_parameter(&"tint", hue)


func _build_boulder(at: Vector3, stone: ShaderMaterial) -> void:
	var big: MeshInstance3D = MeshKit.add_mesh(
		self, MeshKit.smooth_rock(0.38, 0.35, _random.randi()), stone, at + Vector3(0, 0.12, 0)
	)
	big.rotation.y = _random.randf_range(0.0, TAU)
	var small: MeshInstance3D = MeshKit.add_mesh(
		self, MeshKit.smooth_rock(0.17, 0.4, _random.randi()), stone, at + Vector3(0.33, 0.04, 0.24)
	)
	small.rotation.y = _random.randf_range(0.0, TAU)


## Little caps that glow cyan and violet among the trees and the boulders.
func _build_mushrooms() -> void:
	var stalk: StandardMaterial3D = Surfaces.cloth(Color(0.82, 0.78, 0.68), 0.6, 0.3)
	var glows: Array[Color] = [Color(0.3, 0.95, 1.0), Color(0.75, 0.4, 1.0), Color(0.4, 1.0, 0.6)]
	var spots: Array[Vector2i] = []
	spots.append_array(_isle.cells_with(Isle.Feature.TREE))
	spots.append_array(_isle.cells_with(Isle.Feature.ROCK))
	for cell: Vector2i in spots:
		var base: Vector3 = cell_top(_isle, cell)
		var colour: Color = glows[_random.randi() % glows.size()]
		var cap: StandardMaterial3D = MeshKit.material(colour * 0.5, 0.35, 0.0, colour)
		cap.emission_energy_multiplier = 1.6
		cap.subsurf_scatter_enabled = true
		cap.subsurf_scatter_strength = 0.6
		for i: int in range(_random.randi_range(3, 5)):
			var spot: Vector3 = (
				base
				+ Vector3(_random.randf_range(-0.42, 0.42), 0.0, _random.randf_range(-0.42, 0.42))
			)
			var tall: float = _random.randf_range(0.05, 0.12)
			MeshKit.add_mesh(
				self,
				MeshKit.cylinder(0.012, 0.016, tall, 6),
				stalk,
				spot + Vector3(0, tall * 0.5, 0)
			)
			var top: MeshInstance3D = MeshKit.add_mesh(
				self, MeshKit.sphere(0.035 + tall * 0.25, 12, 6), cap, spot + Vector3(0, tall, 0)
			)
			top.scale = Vector3(1.0, 0.55, 1.0)


func _build_pool() -> void:
	var water_cells: Array[Vector2i] = _isle.cells_with(Isle.Feature.WATER)
	if water_cells.is_empty():
		return
	var water: ShaderMaterial = MeshKit.shader_material(WATER_SHADER)
	var south: float = -INF
	var lip_y: float = 0.0
	var span: Vector2 = Vector2(INF, -INF)
	var depth_span: Vector2 = Vector2(INF, -INF)
	for cell: Vector2i in water_cells:
		var top: Vector3 = cell_top(_isle, cell)
		south = maxf(south, top.z + CELL * 0.5)
		lip_y = top.y - WATER_LEVEL
		span = Vector2(minf(span.x, top.x), maxf(span.y, top.x))
		depth_span = Vector2(minf(depth_span.x, top.z), maxf(depth_span.y, top.z))
	# One surface over the whole pool, so no seam runs through it.
	var plane: PlaneMesh = PlaneMesh.new()
	plane.size = Vector2(span.y - span.x + CELL, depth_span.y - depth_span.x + CELL)
	plane.subdivide_width = 8
	plane.subdivide_depth = 4
	var middle: Vector3 = Vector3(
		(span.x + span.y) * 0.5, lip_y, (depth_span.x + depth_span.y) * 0.5
	)
	var surface: MeshInstance3D = MeshKit.add_mesh(self, plane, water, middle)
	surface.name = "Pool"
	surface.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	surface.gi_mode = GeometryInstance3D.GI_MODE_DISABLED
	var bed: MeshInstance3D = MeshKit.add_mesh(
		self,
		MeshKit.smooth_rock(0.3, 0.5, 5),
		Surfaces.stone(1.0),
		middle + Vector3(0.2, -POOL_DEPTH + WATER_LEVEL, -0.1)
	)
	bed.scale = Vector3(2.2, 0.5, 1.4)
	# The spill: a curtain off the south edge, facing the camera, down into the cloud.
	var fall: ShaderMaterial = MeshKit.shader_material(WATERFALL_SHADER)
	var drop: float = lip_y + 4.6
	var curtain: QuadMesh = QuadMesh.new()
	curtain.size = Vector2(span.y - span.x + CELL * 0.6, drop)
	var sheet: MeshInstance3D = MeshKit.add_mesh(
		self, curtain, fall, Vector3((span.x + span.y) * 0.5, lip_y - drop * 0.5, south + 0.08)
	)
	sheet.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var mist: ParticleProcessMaterial = ParticleProcessMaterial.new()
	mist.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	mist.emission_box_extents = Vector3(curtain.size.x * 0.4, 0.05, 0.1)
	mist.direction = Vector3(0, 0.3, 1)
	mist.spread = 40.0
	mist.initial_velocity_min = 0.2
	mist.initial_velocity_max = 0.5
	mist.gravity = Vector3(0, 0.05, 0)
	mist.scale_min = 3.0
	mist.scale_max = 6.0
	mist.color_ramp = MeshKit.fade_ramp(Color(0.85, 0.9, 1.0, 0.3), Color(0.7, 0.75, 0.9, 0.1))
	var spray: GPUParticles3D = MeshKit.particles(24, 2.5, mist, 0.16, false)
	add_child(spray)
	spray.position = Vector3((span.x + span.y) * 0.5, lip_y - 0.1, south + 0.12)


## The crooked rock under the terraces, and the stones that drift around it.
func _build_root() -> void:
	var rock: ShaderMaterial = Surfaces.stone(0.35, Color(0.85, 0.82, 0.8))
	rock.set_shader_parameter(&"scale", 0.45)
	var radius: float = maxf(_isle.size.x, _isle.size.y) * CELL * 0.5
	var root: MeshInstance3D = MeshKit.add_mesh(
		self, MeshKit.island_root(radius * 0.92, 6.5, 11), rock, Vector3(0, COLUMN_FOOT - 0.2, 0)
	)
	root.name = "Root"
	var turf: ShaderMaterial = Surfaces.turf()
	for i: int in range(6):
		var holder: Node3D = Node3D.new()
		add_child(holder)
		var size: float = _random.randf_range(0.25, 0.6)
		MeshKit.add_mesh(holder, MeshKit.smooth_rock(size, 0.45, _random.randi()), rock)
		var cap: MeshInstance3D = MeshKit.add_mesh(
			holder,
			MeshKit.smooth_rock(size * 0.85, 0.2, _random.randi()),
			turf,
			Vector3(0, size * 0.25, 0)
		)
		cap.scale = Vector3(1.0, 0.35, 1.0)
		var distance: float = radius + _random.randf_range(1.5, 3.5)
		var angle: float = i * TAU / 6.0 + _random.randf_range(-0.3, 0.3)
		var home: Vector3 = Vector3(
			cos(angle) * distance, _random.randf_range(-2.2, 0.6), sin(angle) * distance
		)
		_drifters.append(holder)
		_drifter_homes.append(home)
		holder.position = home
