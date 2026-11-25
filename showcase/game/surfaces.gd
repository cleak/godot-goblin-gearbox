class_name Surfaces
extends RefCounted
## Physically based materials for the showcase, made from noise at start: no image files.
## Each material reads height and normal maps baked once per process from FastNoiseLite
## (NoiseTexture2D, seamless, generated on a thread), projected in world space.

const STONE_SHADER: Shader = preload("res://showcase/shaders/stone.gdshader")
const METAL_SHADER: Shader = preload("res://showcase/shaders/metal.gdshader")
const WOOD_SHADER: Shader = preload("res://showcase/shaders/wood.gdshader")
const GROUND_SHADER: Shader = preload("res://showcase/shaders/ground.gdshader")
const LEAVES_SHADER: Shader = preload("res://showcase/shaders/leaves.gdshader")
const MAP_SIZE: int = 512
const BRASS: Color = Color(0.93, 0.72, 0.38)
const COPPER: Color = Color(0.93, 0.55, 0.4)
const IRON: Color = Color(0.42, 0.42, 0.44)

## Name -> map, made once per process and shared.
static var _maps: Dictionary[StringName, Texture2D] = {}


## A noise map by name: rock, fine, soil, grain, grime or leaf; `normal` for its normal map.
static func map(map_name: StringName, normal: bool = false) -> Texture2D:
	var key: StringName = StringName("%s_%s" % [map_name, "n" if normal else "h"])
	if not _maps.has(key):
		var texture: NoiseTexture2D = NoiseTexture2D.new()
		texture.width = MAP_SIZE
		texture.height = MAP_SIZE
		texture.seamless = true
		texture.generate_mipmaps = true
		texture.noise = _noise(map_name)
		texture.as_normal_map = normal
		# Cells rise in the middle, as clumps of leaves do, rather than sink.
		texture.invert = map_name == &"leaf"
		texture.bump_strength = _bump(map_name)
		_maps[key] = texture
	return _maps[key]


static func stone(moss: float = 0.55, tint: Color = Color.WHITE) -> ShaderMaterial:
	var out: ShaderMaterial = _with_maps(
		STONE_SHADER,
		{
			&"rock_height": map(&"rock"),
			&"rock_normal": map(&"rock", true),
			&"fine_height": map(&"fine"),
			&"fine_normal": map(&"fine", true),
		}
	)
	out.set_shader_parameter(&"moss", moss)
	out.set_shader_parameter(&"stone_light", Color(0.56, 0.53, 0.49) * tint)
	out.set_shader_parameter(&"stone_dark", Color(0.24, 0.23, 0.23) * tint)
	return out


## Bare metal: brass, copper, iron. `patina` 0 to 1 is how much verdigris has grown.
static func metal(colour: Color, roughness: float = 0.26, patina: float = 0.2) -> ShaderMaterial:
	var out: ShaderMaterial = _with_maps(
		METAL_SHADER,
		{
			&"grime_height": map(&"grime"),
			&"fine_height": map(&"fine"),
			&"fine_normal": map(&"fine", true),
		}
	)
	out.set_shader_parameter(&"metal_color", colour)
	out.set_shader_parameter(&"metal_roughness", roughness)
	out.set_shader_parameter(&"patina", patina)
	return out


## Iron under enamel paint of `colour`, chipped back to the metal here and there.
static func painted(colour: Color) -> ShaderMaterial:
	var out: ShaderMaterial = metal(IRON, 0.42, 0.0)
	out.set_shader_parameter(&"paint", 1.0)
	out.set_shader_parameter(&"paint_color", colour)
	return out


static func wood(light: Color, dark: Color, moss: float = 0.0) -> ShaderMaterial:
	var out: ShaderMaterial = _with_maps(
		WOOD_SHADER,
		{
			&"grain_height": map(&"grain"),
			&"grain_normal": map(&"grain", true),
		}
	)
	out.set_shader_parameter(&"wood_light", light)
	out.set_shader_parameter(&"wood_dark", dark)
	out.set_shader_parameter(&"moss", moss)
	return out


## Bark: deep fissures along the trunk.
static func bark() -> ShaderMaterial:
	var out: ShaderMaterial = wood(Color(0.33, 0.25, 0.19), Color(0.08, 0.06, 0.05), 0.6)
	out.set_shader_parameter(&"stretch", Vector3(3.0, 0.35, 3.0))
	out.set_shader_parameter(&"normal_strength", 3.0)
	out.set_shader_parameter(&"roughness_base", 0.9)
	return out


static func turf() -> ShaderMaterial:
	return _with_maps(
		GROUND_SHADER,
		{
			&"soil_height": map(&"soil"),
			&"soil_normal": map(&"soil", true),
			&"fine_height": map(&"fine"),
			&"fine_normal": map(&"fine", true),
		}
	)


## Glass that shows what is behind it, a little blurred, with a sharp reflection.
static func glass(tint: Color, glow: Color = Color.BLACK) -> StandardMaterial3D:
	var out: StandardMaterial3D = StandardMaterial3D.new()
	out.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	out.albedo_color = Color(tint, 0.12)
	out.roughness = 0.04
	out.metallic_specular = 0.9
	out.refraction_enabled = true
	out.refraction_scale = 0.03
	if glow != Color.BLACK:
		out.emission_enabled = true
		out.emission = glow
	return out


## Skin that light passes into: subsurface scattering in skin mode, fine pores.
static func skin(colour: Color) -> StandardMaterial3D:
	var out: StandardMaterial3D = StandardMaterial3D.new()
	out.albedo_color = colour
	out.roughness = 0.52
	out.subsurf_scatter_enabled = true
	out.subsurf_scatter_strength = 0.55
	out.subsurf_scatter_skin_mode = true
	out.normal_enabled = true
	out.normal_texture = map(&"fine", true)
	out.normal_scale = 0.25
	out.uv1_triplanar = true
	out.uv1_scale = Vector3.ONE * 6.0
	out.rim_enabled = true
	out.rim = 0.25
	out.rim_tint = 0.6
	return out


## Leather or cloth: a weave or grain in the normal map, soft rim light.
static func cloth(colour: Color, roughness: float = 0.85, weave: float = 0.6) -> StandardMaterial3D:
	var out: StandardMaterial3D = StandardMaterial3D.new()
	out.albedo_color = colour
	out.roughness = roughness
	out.normal_enabled = true
	out.normal_texture = map(&"fine", true)
	out.normal_scale = weave
	out.uv1_triplanar = true
	out.uv1_scale = Vector3.ONE * 9.0
	out.rim_enabled = true
	out.rim = 0.35
	out.rim_tint = 0.4
	return out


## Leaves massed into tree crowns: clumps from a cellular map, see leaves.gdshader.
static func leaves() -> ShaderMaterial:
	return _with_maps(
		LEAVES_SHADER, {&"clump_height": map(&"leaf"), &"clump_normal": map(&"leaf", true)}
	)


static func _with_maps(shader: Shader, maps: Dictionary[StringName, Texture2D]) -> ShaderMaterial:
	var out: ShaderMaterial = MeshKit.shader_material(shader)
	for uniform: StringName in maps:
		out.set_shader_parameter(uniform, maps[uniform])
	return out


static func _noise(map_name: StringName) -> FastNoiseLite:
	var noise: FastNoiseLite = FastNoiseLite.new()
	noise.fractal_type = FastNoiseLite.FRACTAL_FBM
	match map_name:
		&"rock":
			# Cells with cracked edges, warped so no two stones look cut by one tool.
			noise.seed = 11
			noise.noise_type = FastNoiseLite.TYPE_CELLULAR
			noise.cellular_return_type = FastNoiseLite.RETURN_DISTANCE2_SUB
			noise.frequency = 0.012
			noise.fractal_octaves = 4
			noise.domain_warp_enabled = true
			noise.domain_warp_amplitude = 18.0
		&"fine":
			noise.seed = 23
			noise.noise_type = FastNoiseLite.TYPE_SIMPLEX
			noise.frequency = 0.045
			noise.fractal_octaves = 4
		&"soil":
			noise.seed = 37
			noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
			noise.frequency = 0.012
			noise.fractal_octaves = 5
		&"grain":
			noise.seed = 41
			noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
			noise.frequency = 0.02
			noise.fractal_octaves = 5
			noise.fractal_type = FastNoiseLite.FRACTAL_RIDGED
		&"grime":
			noise.seed = 53
			noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
			noise.frequency = 0.008
			noise.fractal_octaves = 6
		&"leaf":
			# Round cells, a little warped: clumps of leaves.
			noise.seed = 61
			noise.noise_type = FastNoiseLite.TYPE_CELLULAR
			noise.cellular_return_type = FastNoiseLite.RETURN_DISTANCE
			noise.frequency = 0.016
			noise.fractal_octaves = 2
			noise.domain_warp_enabled = true
			noise.domain_warp_amplitude = 6.0
		_:
			push_error("Surfaces: no noise map named %s" % map_name)
	return noise


static func _bump(map_name: StringName) -> float:
	match map_name:
		&"rock":
			return 10.0
		&"grain":
			return 8.0
		_:
			return 5.0
