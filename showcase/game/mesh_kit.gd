class_name MeshKit
extends RefCounted
## Procedural meshes and materials for the showcase: gears, faceted rocks, the island's
## underside. No imported assets; every shape is built here from numbers.
##
## Triangles are wound by the outward hint they are given, so callers never think about
## winding order (Godot culls counter-clockwise faces).

static var _speck: GradientTexture2D = null


## A material with the usual knobs; emission is off when `glow` is black.
static func material(
	albedo: Color, roughness: float = 0.8, metallic: float = 0.0, glow: Color = Color.BLACK
) -> StandardMaterial3D:
	var out: StandardMaterial3D = StandardMaterial3D.new()
	out.albedo_color = albedo
	out.roughness = roughness
	out.metallic = metallic
	if glow != Color.BLACK:
		out.emission_enabled = true
		out.emission = glow
	return out


static func shader_material(shader: Shader) -> ShaderMaterial:
	var out: ShaderMaterial = ShaderMaterial.new()
	out.shader = shader
	return out


## A child mesh instance with a material, placed at `at`.
static func add_mesh(
	parent: Node3D, mesh: Mesh, surface: Material, at: Vector3 = Vector3.ZERO
) -> MeshInstance3D:
	var instance: MeshInstance3D = MeshInstance3D.new()
	instance.mesh = mesh
	instance.material_override = surface
	parent.add_child(instance)
	instance.position = at
	return instance


static func box(size: Vector3) -> BoxMesh:
	var out: BoxMesh = BoxMesh.new()
	out.size = size
	return out


static func cylinder(top: float, bottom: float, height: float, sides: int = 16) -> CylinderMesh:
	var out: CylinderMesh = CylinderMesh.new()
	out.top_radius = top
	out.bottom_radius = bottom
	out.height = height
	out.radial_segments = sides
	out.rings = 1
	return out


static func sphere(radius: float, sides: int = 16, rings: int = 8) -> SphereMesh:
	var out: SphereMesh = SphereMesh.new()
	out.radius = radius
	out.height = radius * 2.0
	out.radial_segments = sides
	out.rings = rings
	return out


static func torus(inner: float, outer: float, rings: int = 24) -> TorusMesh:
	var out: TorusMesh = TorusMesh.new()
	out.inner_radius = inner
	out.outer_radius = outer
	out.rings = rings
	out.ring_segments = 8
	return out


## A colour over a particle's life: `start`, then `end`, then gone.
static func fade_ramp(start: Color, end: Color) -> GradientTexture1D:
	var gradient: Gradient = Gradient.new()
	gradient.set_color(0, start)
	gradient.set_color(1, Color(end, 0.0))
	gradient.add_point(0.55, end)
	var out: GradientTexture1D = GradientTexture1D.new()
	out.gradient = gradient
	return out


## Camera-facing specks: embers, sparks and fireflies that glow, or (`glowing` false) soft
## puffs of mist and steam. Colour comes from the process material's ramp.
static func particles(
	amount: int,
	lifetime: float,
	process: ParticleProcessMaterial,
	size: float,
	glowing: bool = true
) -> GPUParticles3D:
	var speck: StandardMaterial3D = StandardMaterial3D.new()
	speck.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	speck.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	speck.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	speck.vertex_color_use_as_albedo = true
	speck.albedo_texture = _speck_texture()
	if glowing:
		speck.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
		speck.albedo_color = Color(4.0, 4.0, 4.0)
	else:
		speck.albedo_color = Color(1.0, 1.0, 1.0)
		speck.proximity_fade_enabled = true
		speck.proximity_fade_distance = 0.5
	var quad: QuadMesh = QuadMesh.new()
	quad.size = Vector2(size, size)
	quad.material = speck
	var out: GPUParticles3D = GPUParticles3D.new()
	out.amount = amount
	out.lifetime = lifetime
	out.process_material = process
	out.draw_pass_1 = quad
	out.visibility_aabb = AABB(Vector3(-6, -6, -6), Vector3(12, 12, 12))
	return out


## A soft round dot, made once.
static func _speck_texture() -> GradientTexture2D:
	if _speck == null:
		var gradient: Gradient = Gradient.new()
		gradient.set_color(0, Color.WHITE)
		gradient.set_color(1, Color(1, 1, 1, 0))
		gradient.add_point(0.25, Color(1, 1, 1, 0.8))
		_speck = GradientTexture2D.new()
		_speck.gradient = gradient
		_speck.fill = GradientTexture2D.FILL_RADIAL
		_speck.fill_from = Vector2(0.5, 0.5)
		_speck.fill_to = Vector2(1.0, 0.5)
		_speck.width = 32
		_speck.height = 32
	return _speck


## Adds a triangle that faces `outward`, with that face's own normal (flat shading).
static func add_facing_triangle(
	tool: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, outward: Vector3
) -> void:
	var normal: Vector3 = (b - a).cross(c - a)
	if normal.dot(outward) > 0.0:
		# Godot's front faces are clockwise seen from outside: swap to turn it round.
		var swap: Vector3 = b
		b = c
		c = swap
		normal = -normal
	normal = -normal.normalized()
	for corner: Vector3 in [a, b, c]:
		tool.set_normal(normal)
		tool.add_vertex(corner)


static func add_facing_quad(
	tool: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3, outward: Vector3
) -> void:
	add_facing_triangle(tool, a, b, c, outward)
	add_facing_triangle(tool, a, c, d, outward)


## A spur gear in the XY plane, turning about Z: a toothed rim, its hub and spokes are
## separate (see gear_hub). `teeth` trapezoid teeth stand `depth` proud of `radius`.
static func gear_rim(
	teeth: int, radius: float, depth: float, thickness: float, rim_width: float
) -> ArrayMesh:
	assert(teeth >= 3, "a gear needs at least three teeth")
	var outer: Array[Vector2] = []
	var step: float = TAU / teeth
	for tooth: int in range(teeth):
		var a: float = tooth * step
		for part: Vector2 in [
			Vector2(0.0, 0.0),
			Vector2(0.22, 0.0),
			Vector2(0.36, 1.0),
			Vector2(0.64, 1.0),
			Vector2(0.78, 0.0)
		]:
			var angle: float = a + part.x * step
			outer.append(Vector2.from_angle(angle) * (radius + part.y * depth))
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	var half: float = thickness * 0.5
	# Every outer edge is chamfered, so the teeth catch a line of light.
	var chamfer: float = minf(thickness * 0.2, depth * 0.3)
	var inner_radius: float = radius - rim_width
	var count: int = outer.size()
	for i: int in range(count):
		var o0: Vector2 = outer[i]
		var o1: Vector2 = outer[(i + 1) % count]
		var c0: Vector2 = o0.normalized() * (o0.length() - chamfer)
		var c1: Vector2 = o1.normalized() * (o1.length() - chamfer)
		var i0: Vector2 = o0.normalized() * inner_radius
		var i1: Vector2 = o1.normalized() * inner_radius
		var mid: Vector2 = (o0 + o1) * 0.5
		var wall: Vector2 = (o1 - o0).orthogonal().normalized()
		if wall.dot(mid) < 0.0:
			wall = -wall
		for side: float in [half, -half]:
			var face: Vector3 = Vector3(0.0, 0.0, signf(side))
			add_facing_quad(
				tool,
				Vector3(i0.x, i0.y, side),
				Vector3(c0.x, c0.y, side),
				Vector3(c1.x, c1.y, side),
				Vector3(i1.x, i1.y, side),
				face
			)
			var bevel: float = side - signf(side) * chamfer
			add_facing_quad(
				tool,
				Vector3(c0.x, c0.y, side),
				Vector3(c1.x, c1.y, side),
				Vector3(o1.x, o1.y, bevel),
				Vector3(o0.x, o0.y, bevel),
				Vector3(wall.x, wall.y, 0.0) + face
			)
		add_facing_quad(
			tool,
			Vector3(o0.x, o0.y, half - chamfer),
			Vector3(o1.x, o1.y, half - chamfer),
			Vector3(o1.x, o1.y, -half + chamfer),
			Vector3(o0.x, o0.y, -half + chamfer),
			Vector3(wall.x, wall.y, 0.0)
		)
		var inward: Vector2 = -((i0 + i1) * 0.5)
		add_facing_quad(
			tool,
			Vector3(i0.x, i0.y, half),
			Vector3(i1.x, i1.y, half),
			Vector3(i1.x, i1.y, -half),
			Vector3(i0.x, i0.y, -half),
			Vector3(inward.x, inward.y, 0.0)
		)
	return tool.commit()


## A box with rounded edges and corners (radius `roundness`), smooth-shaded, so its edges
## catch highlights the way real stone, wood and metal do. `steps` segments per edge.
static func rounded_box(size: Vector3, roundness: float, steps: int = 2) -> ArrayMesh:
	var half: Vector3 = size * 0.5
	var r: float = minf(roundness, minf(half.x, minf(half.y, half.z)) * 0.95)
	var inner: Vector3 = half - Vector3.ONE * r
	# Grid stops along one face axis: the edge band is split into `steps` segments.
	var stops: Array[float] = [-1.0]
	for k: int in range(1, steps + 1):
		stops.append(-1.0 + (1.0 - cos(PI * 0.5 * k / steps)))
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	for axis: int in range(3):
		for sign_value: float in [-1.0, 1.0]:
			var u_axis: int = (axis + 1) % 3
			var v_axis: int = (axis + 2) % 3
			var grid: Array[Vector3] = []
			var ticks_u: Array[float] = _ticks(stops, half[u_axis], r)
			var ticks_v: Array[float] = _ticks(stops, half[v_axis], r)
			for tv: float in ticks_v:
				for tu: float in ticks_u:
					var c: Vector3 = Vector3.ZERO
					c[axis] = half[axis] * sign_value
					c[u_axis] = tu
					c[v_axis] = tv
					grid.append(c)
			var face: Vector3 = Vector3.ZERO
			face[axis] = sign_value
			var columns: int = ticks_u.size()
			for row: int in range(ticks_v.size() - 1):
				for column: int in range(columns - 1):
					var quad: Array[Vector3] = [
						grid[row * columns + column],
						grid[row * columns + column + 1],
						grid[(row + 1) * columns + column + 1],
						grid[(row + 1) * columns + column],
					]
					var points: Array[Vector3] = []
					var normals: Array[Vector3] = []
					for c: Vector3 in quad:
						var core: Vector3 = c.clamp(-inner, inner)
						var out: Vector3 = c - core
						var normal: Vector3 = face if out.length() < 1e-5 else out.normalized()
						points.append(core + normal * r)
						normals.append(normal)
					_add_smooth_triangle(tool, points, normals, [0, 1, 2], face)
					_add_smooth_triangle(tool, points, normals, [0, 2, 3], face)
	return tool.commit()


## A boulder with smooth, lumpy faces: a sphere pushed in and out by noise, flattened a
## little so it sits.
static func smooth_rock(radius: float, lumpiness: float, seed_value: int) -> ArrayMesh:
	var noise: FastNoiseLite = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 1.3 / radius
	noise.fractal_octaves = 3
	var source: Array = sphere(radius, 24, 12).get_mesh_arrays()
	var points: PackedVector3Array = source[Mesh.ARRAY_VERTEX]
	var indices: PackedInt32Array = source[Mesh.ARRAY_INDEX]
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i: int in range(indices.size()):
		var p: Vector3 = points[indices[i]]
		var push: float = 1.0 + noise.get_noise_3dv(p) * lumpiness
		var squashed: Vector3 = p * push * Vector3(1.0, 0.7, 1.0)
		# Flat underneath, so it beds into the turf.
		squashed.y = maxf(squashed.y, -radius * 0.35)
		tool.add_vertex(squashed)
	return _smooth_commit(tool)


## A tapered tube along `path` (radius `radii` at each point), smooth-shaded: trunks,
## branches, roots, pipes.
static func tube(path: Array[Vector3], radii: Array[float], sides: int = 8) -> ArrayMesh:
	assert(path.size() >= 2 and path.size() == radii.size(), "a tube needs a radius per point")
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	var rings: Array[PackedVector3Array] = []
	# Each ring's frame is carried along from the last (parallel transport), so the rings
	# never twist against each other.
	var side: Vector3 = Vector3.ZERO
	for i: int in range(path.size()):
		var ahead: Vector3 = path[mini(i + 1, path.size() - 1)] - path[maxi(i - 1, 0)]
		ahead = ahead.normalized()
		if i == 0:
			side = ahead.cross(Vector3.RIGHT)
			if side.length() < 0.01:
				side = ahead.cross(Vector3.FORWARD)
		side = (side - ahead * side.dot(ahead)).normalized()
		var other: Vector3 = ahead.cross(side).normalized()
		var ring: PackedVector3Array = PackedVector3Array()
		for k: int in range(sides):
			var angle: float = TAU * k / sides
			ring.append(path[i] + (side * cos(angle) + other * sin(angle)) * radii[i])
		rings.append(ring)
	for i: int in range(path.size() - 1):
		var centre: Vector3 = (path[i] + path[i + 1]) * 0.5
		for k: int in range(sides):
			var next: int = (k + 1) % sides
			var a: Vector3 = rings[i][k]
			var b: Vector3 = rings[i][next]
			var c: Vector3 = rings[i + 1][next]
			var d: Vector3 = rings[i + 1][k]
			_add_wound(tool, a, b, c, (a + b + c) / 3.0 - centre)
			_add_wound(tool, a, c, d, (a + c + d) / 3.0 - centre)
	return _smooth_commit(tool)


## The hub and `spokes` spokes inside a gear_rim of the same size, also about Z.
static func gear_hub(
	parent: Node3D, radius: float, thickness: float, spokes: int, surface: Material
) -> void:
	var hub: MeshInstance3D = add_mesh(
		parent, cylinder(radius * 0.22, radius * 0.22, thickness * 1.6, 12), surface
	)
	hub.rotation.x = PI * 0.5
	for spoke: int in range(spokes):
		var arm: MeshInstance3D = add_mesh(
			parent, box(Vector3(radius * 0.86, radius * 0.12, thickness * 0.7)), surface
		)
		arm.rotation.z = spoke * TAU / spokes
		arm.position = Vector3(cos(arm.rotation.z), sin(arm.rotation.z), 0.0) * radius * 0.45


## The rock the island stands on: rings that narrow to a crooked tip far below.
static func island_root(radius: float, depth: float, seed_value: int) -> ArrayMesh:
	var noise: FastNoiseLite = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 0.35
	var sides: int = 44
	var rings: int = 18
	var grid: Array[PackedVector3Array] = []
	for ring: int in range(rings + 1):
		var t: float = float(ring) / rings
		var y: float = -t * depth
		var width: float = radius * pow(1.0 - t, 0.75) * (1.0 - 0.15 * t)
		var bend: Vector3 = Vector3(sin(t * 2.4) * 0.6, 0.0, cos(t * 1.7) * 0.4) * t
		var row: PackedVector3Array = PackedVector3Array()
		for side: int in range(sides):
			var angle: float = TAU * side / sides
			var dir: Vector3 = Vector3(cos(angle), 0.0, sin(angle))
			var jag: float = 1.0 + noise.get_noise_3d(dir.x * 3.0, y * 0.9, dir.z * 3.0) * 0.4
			row.append(dir * width * jag + Vector3(0.0, y, 0.0) + bend)
		grid.append(row)
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	for ring: int in range(rings):
		var axis: Vector3 = Vector3(0.0, -depth * (ring + 0.5) / rings, 0.0)
		for side: int in range(sides):
			var next: int = (side + 1) % sides
			var a: Vector3 = grid[ring][side]
			var b: Vector3 = grid[ring][next]
			var c: Vector3 = grid[ring + 1][next]
			var d: Vector3 = grid[ring + 1][side]
			_add_wound(tool, a, b, c, (a + b + c) / 3.0 - axis)
			_add_wound(tool, a, c, d, (a + c + d) / 3.0 - axis)
	# Close the top, under the terraces, facing up. Its own smooth group keeps a hard edge at
	# the rim: shared normals would lean the sides' top band up and smear the triplanar texture.
	tool.set_smooth_group(1)
	var top: Vector3 = Vector3.ZERO
	for side: int in range(sides):
		var next_side: int = (side + 1) % sides
		_add_wound(tool, top, grid[0][side], grid[0][next_side], Vector3.UP)
	return _smooth_commit(tool)


## A thin tapered blade of grass, 1 tall, UV.y 0 at the root and 1 at the tip.
static func grass_blade() -> ArrayMesh:
	var tool: SurfaceTool = SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	var levels: Array[float] = [0.0, 0.35, 0.7, 1.0]
	var widths: Array[float] = [0.045, 0.036, 0.022, 0.0]
	for i: int in range(levels.size() - 1):
		var y0: float = levels[i]
		var y1: float = levels[i + 1]
		var w0: float = widths[i]
		var w1: float = widths[i + 1]
		var corners: Array[Vector3] = [
			Vector3(-w0, y0, 0.0), Vector3(w0, y0, 0.0), Vector3(w1, y1, 0.0), Vector3(-w1, y1, 0.0)
		]
		var uvs: Array[Vector2] = [
			Vector2(0.0, y0), Vector2(1.0, y0), Vector2(1.0, y1), Vector2(0.0, y1)
		]
		for k: int in [0, 1, 2, 0, 2, 3]:
			tool.set_normal(Vector3.BACK)
			tool.set_uv(uvs[k])
			tool.add_vertex(corners[k])
	return tool.commit()


## Positions on one face axis: the flat middle, and the rounded band at each end.
static func _ticks(stops: Array[float], half: float, r: float) -> Array[float]:
	var out: Array[float] = []
	for s: float in stops:
		out.append(-half + (s + 1.0) * r)
	for k: int in range(stops.size() - 1, -1, -1):
		out.append(half - (stops[k] + 1.0) * r)
	return out


static func _add_smooth_triangle(
	tool: SurfaceTool,
	points: Array[Vector3],
	normals: Array[Vector3],
	order: Array[int],
	outward: Vector3
) -> void:
	var a: Vector3 = points[order[0]]
	var b: Vector3 = points[order[1]]
	var c: Vector3 = points[order[2]]
	var corners: Array[int] = order.duplicate()
	if (b - a).cross(c - a).dot(outward) > 0.0:
		corners = [order[0], order[2], order[1]]
	for k: int in corners:
		tool.set_normal(normals[k])
		tool.add_vertex(points[k])


## A triangle wound to face `outward`, no normal: for meshes that generate their own.
static func _add_wound(
	tool: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, outward: Vector3
) -> void:
	if (b - a).cross(c - a).dot(outward) > 0.0:
		tool.add_vertex(a)
		tool.add_vertex(c)
		tool.add_vertex(b)
	else:
		tool.add_vertex(a)
		tool.add_vertex(b)
		tool.add_vertex(c)


## Shares corners, makes smooth normals and commits. generate_normals() follows the same
## clockwise-front winding Godot draws with (test/showcase/test_mesh_kit.gd checks it).
static func _smooth_commit(tool: SurfaceTool) -> ArrayMesh:
	tool.index()
	tool.generate_normals()
	return tool.commit()
