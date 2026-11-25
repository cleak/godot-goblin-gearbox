class_name Atmosphere
extends Node3D
## Everything around the island: the sky, the sun and the moon, fog, glow, screen-space
## reflections and bounce light, a sea of cloud below, and fireflies.
##
## `dusk` runs the day out: 0 is golden hour, a low sun throwing long soft shadows across
## the terraces; 0.5 twilight; 1 night under the moon. `awake` (0 to 1) brings the aurora.

const SKY_SHADER: Shader = preload("res://showcase/shaders/sky.gdshader")
const CLOUD_SHADER: Shader = preload("res://showcase/shaders/clouds.gdshader")
## Cloud layers: height, scale, density, drift.
const CLOUD_LAYERS: Array[Vector4] = [
	Vector4(-4.2, 0.075, 0.62, 0.012),
	Vector4(-5.4, 0.05, 0.7, 0.008),
	Vector4(-7.0, 0.032, 0.8, 0.005),
]
## The sun sits low in the west-south-west: it lights the faces the camera sees from the
## side, so every bevel and stone catches a highlight, and its shadows reach north-east.
const SUN_YAW: float = -62.0
const SUN_HIGH: float = 30.0
const SUN_LOW: float = -4.0
## The moon rises in the north-east, right of the beam in the wide shot.
const MOON_ROTATION: Vector3 = Vector3(-11.0, 163.0, 0.0)
const SUN_GOLD: Color = Color(1.0, 0.74, 0.5)
const SUN_RED: Color = Color(1.0, 0.45, 0.24)
const FOG_GOLD: Color = Color(0.56, 0.6, 0.78)
const FOG_TWILIGHT: Color = Color(0.36, 0.28, 0.44)
const FOG_NIGHT: Color = Color(0.13, 0.15, 0.32)

## 0 golden hour to 1 night; Lanternworks eases it as lanterns are lit.
var dusk: float = 0.0:
	set(value):
		dusk = clampf(value, 0.0, 1.0)
		_apply()
## 0 asleep, 1 the gearbox is awake: aurora and a cooler fog.
var awake: float = 0.0:
	set(value):
		awake = clampf(value, 0.0, 1.0)
		_apply()

## True while the game is paused: the world drains to near grey and dims, behind the veil.
var hushed: bool = false:
	set(value):
		if value == hushed:
			return
		hushed = value
		if environment == null:
			return
		var fade: Tween = create_tween().set_parallel(true).set_trans(Tween.TRANS_SINE)
		fade.tween_property(environment, ^"adjustment_saturation", 0.25 if value else 1.08, 0.3)
		fade.tween_property(environment, ^"adjustment_brightness", 0.8 if value else 1.0, 0.3)

var environment: Environment = null
var _sky_material: ShaderMaterial = null
var _cloud_materials: Array[ShaderMaterial] = []
var _sun: DirectionalLight3D = null
var _moon: DirectionalLight3D = null
var _fireflies: GPUParticles3D = null
var _bounce: VoxelGI = null


func _ready() -> void:
	_build_environment()
	_build_lights()
	_build_clouds()
	_build_fireflies()
	_apply()


## Bounce light: a VoxelGI volume over `extent`, baked from the island's meshes once they
## exist. Lights stay live: the lanterns and the setting sun bounce as they change. A
## headless run renders nothing, so it bakes nothing.
func bake_bounce_light(extent: Vector3, centre: Vector3) -> void:
	_bounce = VoxelGI.new()
	_bounce.name = "BounceLight"
	_bounce.size = extent
	_bounce.subdiv = VoxelGI.SUBDIV_128
	add_child(_bounce)
	_bounce.position = centre
	if DisplayServer.get_name() == "headless":
		return
	_bounce.bake()
	_bounce.data.energy = 1.0
	_bounce.data.propagation = 0.7
	_bounce.data.bias = 1.2


## Turns the costly effects on or off by name (fog, ssao, ssil, ssr, glow, shadows, gi),
## for comparing their cost on a slow GPU. Returns the names it knew.
func set_effects(switches: Dictionary[String, bool]) -> Array[String]:
	var known: Array[String] = []
	for effect: String in switches:
		var on: bool = switches[effect]
		match effect:
			"fog":
				environment.volumetric_fog_enabled = on
			"ssao":
				environment.ssao_enabled = on
			"ssil":
				environment.ssil_enabled = on
			"ssr":
				environment.ssr_enabled = on
			"glow":
				environment.glow_enabled = on
			"shadows":
				_sun.shadow_enabled = on
				_moon.shadow_enabled = on
			"gi":
				if _bounce != null:
					_bounce.visible = on
			_:
				continue
		known.append(effect)
	return known


func _build_environment() -> void:
	_sky_material = MeshKit.shader_material(SKY_SHADER)
	var sky: Sky = Sky.new()
	sky.sky_material = _sky_material
	sky.process_mode = Sky.PROCESS_MODE_INCREMENTAL
	sky.radiance_size = Sky.RADIANCE_SIZE_256
	environment = Environment.new()
	environment.background_mode = Environment.BG_SKY
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_sky_contribution = 1.0
	environment.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	environment.tonemap_mode = Environment.TONE_MAPPER_AGX
	environment.glow_enabled = true
	environment.glow_intensity = 0.55
	environment.glow_strength = 1.0
	environment.glow_bloom = 0.02
	environment.glow_hdr_threshold = 1.1
	environment.glow_blend_mode = Environment.GLOW_BLEND_MODE_SOFTLIGHT
	environment.set_glow_level(0, 0.0)
	environment.set_glow_level(2, 1.0)
	environment.set_glow_level(4, 0.8)
	environment.set_glow_level(5, 0.5)
	environment.ssao_enabled = true
	environment.ssao_radius = 0.6
	environment.ssao_intensity = 2.6
	environment.ssao_detail = 0.8
	environment.ssil_enabled = true
	environment.ssil_radius = 2.5
	environment.ssil_intensity = 1.2
	environment.ssr_enabled = true
	environment.ssr_max_steps = 64
	environment.ssr_fade_in = 0.1
	environment.fog_enabled = true
	environment.fog_mode = Environment.FOG_MODE_EXPONENTIAL
	environment.fog_density = 0.006
	environment.fog_sky_affect = 0.0
	environment.fog_height = -2.5
	environment.fog_height_density = 0.16
	environment.fog_aerial_perspective = 0.25
	environment.volumetric_fog_enabled = true
	environment.volumetric_fog_emission = Color(0.0, 0.0, 0.0)
	environment.volumetric_fog_anisotropy = 0.6
	environment.volumetric_fog_length = 28.0
	environment.volumetric_fog_sky_affect = 0.0
	environment.adjustment_enabled = true
	environment.adjustment_saturation = 1.08
	environment.adjustment_contrast = 1.12
	var world: WorldEnvironment = WorldEnvironment.new()
	world.name = "WorldEnvironment"
	world.environment = environment
	add_child(world)


func _build_lights() -> void:
	_sun = DirectionalLight3D.new()
	_sun.name = "Sun"
	_sun.shadow_enabled = true
	_sun.shadow_blur = 1.2
	# A sun of real size: penumbrae widen with distance from the caster.
	_sun.light_angular_distance = 0.6
	_sun.directional_shadow_max_distance = 30.0
	_sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	_sun.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
	_sun.light_volumetric_fog_energy = 0.6
	add_child(_sun)
	_moon = DirectionalLight3D.new()
	_moon.name = "Moon"
	_moon.light_color = Color(0.62, 0.72, 1.0)
	_moon.shadow_enabled = true
	_moon.shadow_blur = 1.5
	_moon.light_angular_distance = 0.6
	_moon.directional_shadow_max_distance = 30.0
	_moon.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
	_moon.light_volumetric_fog_energy = 0.4
	add_child(_moon)
	_moon.rotation = MOON_ROTATION * (PI / 180.0)


func _build_clouds() -> void:
	for layer: Vector4 in CLOUD_LAYERS:
		var surface: ShaderMaterial = MeshKit.shader_material(CLOUD_SHADER)
		surface.set_shader_parameter(&"scale", layer.y)
		surface.set_shader_parameter(&"density", layer.z)
		surface.set_shader_parameter(&"drift", Vector2(layer.w, layer.w * 0.5))
		var plane: PlaneMesh = PlaneMesh.new()
		plane.size = Vector2(160.0, 160.0)
		var instance: MeshInstance3D = MeshKit.add_mesh(
			self, plane, surface, Vector3(0, layer.x, 0)
		)
		instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		instance.gi_mode = GeometryInstance3D.GI_MODE_DISABLED
		_cloud_materials.append(surface)


func _build_fireflies() -> void:
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(5.0, 0.8, 5.0)
	process.gravity = Vector3.ZERO
	process.initial_velocity_min = 0.02
	process.initial_velocity_max = 0.1
	process.spread = 180.0
	process.turbulence_enabled = true
	process.turbulence_noise_strength = 1.6
	process.turbulence_noise_scale = 3.0
	process.turbulence_noise_speed_random = 0.4
	process.turbulence_influence_min = 0.02
	process.turbulence_influence_max = 0.06
	var ramp: GradientTexture1D = MeshKit.fade_ramp(Color(0.8, 1.0, 0.4), Color(0.6, 1.0, 0.3))
	ramp.gradient.set_color(0, Color(0.8, 1.0, 0.4, 0.0))
	ramp.gradient.add_point(0.15, Color(0.8, 1.0, 0.4, 1.0))
	process.color_ramp = ramp
	_fireflies = MeshKit.particles(70, 7.0, process, 0.05)
	_fireflies.name = "Fireflies"
	_fireflies.preprocess = 7.0
	add_child(_fireflies)
	_fireflies.position = Vector3(0, 1.4, 0)


func _apply() -> void:
	if _sky_material == null:
		return
	var day: float = 1.0 - smoothstep(0.0, 0.75, dusk)
	var night: float = smoothstep(0.45, 1.0, dusk)
	var twilight: float = smoothstep(0.0, 0.55, dusk)
	# The sun sinks and reddens, then is gone.
	var elevation: float = lerpf(SUN_HIGH, SUN_LOW, smoothstep(0.0, 0.8, dusk))
	_sun.rotation = Vector3(deg_to_rad(-elevation), deg_to_rad(SUN_YAW), 0.0)
	_sun.light_color = SUN_GOLD.lerp(SUN_RED, twilight)
	_sun.light_energy = 5.0 * day
	_sun.visible = day > 0.001
	_moon.light_energy = 0.42 * night + 0.12 * awake
	_moon.visible = night > 0.001
	_sky_material.set_shader_parameter(&"dusk", dusk)
	_sky_material.set_shader_parameter(&"aurora", awake)
	_sky_material.set_shader_parameter(&"sun_direction", _sun.global_basis.z)
	_sky_material.set_shader_parameter(&"moon_direction", _moon.global_basis.z)
	var fog: Color = FOG_GOLD.lerp(FOG_TWILIGHT, twilight).lerp(FOG_NIGHT, night)
	environment.fog_light_color = fog.lerp(Color(0.1, 0.26, 0.3), awake * 0.6)
	environment.volumetric_fog_density = lerpf(0.0015, 0.004, night)
	environment.volumetric_fog_albedo = fog.lerp(Color(0.6, 0.95, 0.85), awake * 0.5)
	environment.ambient_light_energy = lerpf(0.2, 0.3, night) + awake * 0.1
	environment.tonemap_exposure = lerpf(1.0, 1.1, night)
	var lit: Color = Color(1.25, 0.95, 0.72).lerp(Color(0.85, 0.5, 0.6), twilight)
	lit = lit.lerp(Color(0.3, 0.38, 0.62), night)
	var shade: Color = Color(0.3, 0.34, 0.55).lerp(Color(0.025, 0.035, 0.09), night)
	for surface: ShaderMaterial in _cloud_materials:
		surface.set_shader_parameter(&"awake", awake)
		surface.set_shader_parameter(&"lit_color", lit)
		surface.set_shader_parameter(&"shadow_color", shade)
