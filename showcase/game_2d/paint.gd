class_name Paint
extends RefCounted
## Helpers the 2D showcase builds its scene with: shapes, soft light textures, materials.
## Everything is made in code; nothing is imported.

const MASONRY: Shader = preload("res://showcase/shaders/masonry_2d.gdshader")

## Light textures by size, made once per process and shared.
static var _soft: Dictionary[int, GradientTexture2D] = {}


static func polygon(
	parent: Node, points: PackedVector2Array, colour: Color, look: Material = null
) -> Polygon2D:
	var out: Polygon2D = Polygon2D.new()
	out.polygon = points
	out.color = colour
	out.material = look
	parent.add_child(out)
	return out


static func box(corner: Vector2, size: Vector2) -> PackedVector2Array:
	return PackedVector2Array(
		[corner, corner + Vector2(size.x, 0.0), corner + size, corner + Vector2(0.0, size.y)]
	)


static func ellipse(centre: Vector2, radii: Vector2, sides: int = 24) -> PackedVector2Array:
	var out: PackedVector2Array = PackedVector2Array()
	for side: int in range(sides):
		out.append(centre + Vector2.from_angle(TAU * side / sides) * radii)
	return out


## A rectangle with its corners rounded by `radius`, each corner in `steps` segments.
static func rounded(
	corner: Vector2, size: Vector2, radius: float, steps: int = 4
) -> PackedVector2Array:
	var r: float = minf(radius, minf(size.x, size.y) * 0.5)
	var out: PackedVector2Array = PackedVector2Array()
	var centres: Array[Vector2] = [
		corner + Vector2(size.x - r, r),
		corner + Vector2(size.x - r, size.y - r),
		corner + Vector2(r, size.y - r),
		corner + Vector2(r, r),
	]
	for i: int in range(4):
		for step: int in range(steps + 1):
			var angle: float = -PI * 0.5 + (i + float(step) / steps) * PI * 0.5
			out.append(centres[i] + Vector2.from_angle(angle) * r)
	return out


## A soft round light, white in the middle and clear at the rim: for halos and lights.
static func soft_light(size: int = 256) -> GradientTexture2D:
	if _soft.has(size):
		return _soft[size]
	var gradient: Gradient = Gradient.new()
	gradient.set_color(0, Color.WHITE)
	gradient.set_color(1, Color(1.0, 1.0, 1.0, 0.0))
	gradient.add_point(0.15, Color(1.0, 1.0, 1.0, 0.75))
	gradient.add_point(0.45, Color(1.0, 1.0, 1.0, 0.2))
	var out: GradientTexture2D = GradientTexture2D.new()
	out.gradient = gradient
	out.fill = GradientTexture2D.FILL_RADIAL
	out.fill_from = Vector2(0.5, 0.5)
	out.fill_to = Vector2(0.5, 0.0)
	out.width = size
	out.height = size
	_soft[size] = out
	return out


## Masonry of one `kind` (see masonry_2d.gdshader): 0 plaster over brick, 1 slate,
## 2 ashlar, 3 cobbles.
static func masonry(kind: int, wet: float = 0.6, tide: float = 100000.0) -> ShaderMaterial:
	var out: ShaderMaterial = ShaderMaterial.new()
	out.shader = MASONRY
	out.set_shader_parameter(&"kind", kind)
	out.set_shader_parameter(&"wet", wet)
	out.set_shader_parameter(&"tide", tide)
	return out


static func shader(code: Shader) -> ShaderMaterial:
	var out: ShaderMaterial = ShaderMaterial.new()
	out.shader = code
	return out


## Adds light instead of covering what is behind; not shaded by the 2D lights.
static func additive() -> CanvasItemMaterial:
	var out: CanvasItemMaterial = CanvasItemMaterial.new()
	out.blend_mode = CanvasItemMaterial.BLEND_MODE_ADD
	out.light_mode = CanvasItemMaterial.LIGHT_MODE_UNSHADED
	return out


## A sprite of soft additive light of `colour` (HDR: above 1 blooms), `size` across.
static func glow(parent: Node, at: Vector2, size: float, colour: Color) -> Sprite2D:
	var out: Sprite2D = Sprite2D.new()
	out.texture = soft_light()
	out.material = additive()
	out.modulate = colour
	out.position = at
	out.scale = Vector2.ONE * size / 256.0
	parent.add_child(out)
	return out


## Casts 2D shadows from `points`; `mask` is which lights' shadows it casts (their
## shadow_item_cull_mask).
static func occluder(parent: Node, points: PackedVector2Array, mask: int = 1) -> LightOccluder2D:
	var shape: OccluderPolygon2D = OccluderPolygon2D.new()
	shape.polygon = points
	var out: LightOccluder2D = LightOccluder2D.new()
	out.occluder = shape
	out.occluder_light_mask = mask
	parent.add_child(out)
	return out


## A warm point light that casts soft shadows.
static func lamp_light(parent: Node, at: Vector2, colour: Color, reach: float) -> PointLight2D:
	var out: PointLight2D = PointLight2D.new()
	out.texture = soft_light(512)
	out.texture_scale = reach / 256.0
	out.color = colour
	out.energy = 0.0
	out.height = 60.0
	out.shadow_enabled = true
	out.shadow_filter = Light2D.SHADOW_FILTER_PCF13
	out.shadow_filter_smooth = 3.0
	out.shadow_color = Color(0.0, 0.0, 0.0, 0.85)
	# It lights the rain too (HarbourWeather.RAIN_MASK).
	out.range_item_cull_mask = 1 | 2
	out.position = at
	parent.add_child(out)
	return out


## A streak texture for rain: a thin vertical line, bright in the middle.
static func streak(length: int = 32) -> GradientTexture2D:
	var gradient: Gradient = Gradient.new()
	gradient.set_color(0, Color(1.0, 1.0, 1.0, 0.0))
	gradient.set_color(1, Color(1.0, 1.0, 1.0, 0.0))
	gradient.add_point(0.6, Color.WHITE)
	var out: GradientTexture2D = GradientTexture2D.new()
	out.gradient = gradient
	out.fill_from = Vector2(0.5, 0.0)
	out.fill_to = Vector2(0.5, 1.0)
	out.width = 2
	out.height = length
	return out
