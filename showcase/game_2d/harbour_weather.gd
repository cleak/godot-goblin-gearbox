class_name HarbourWeather
extends Node2D
## The 2D showcase's sky and weather: the night sky, rain, the glow and
## the ambient dark. `storm` 1 is a downpour under cloud, 0 a clear night: each lamp's spark
## eases it down, and the lighthouse's waking clears it. `hushed` greys the world while
## paused. Rain only shows bright where a light falls on it: the lamps pick it out.

const SKY_SHADER: Shader = preload("res://showcase/shaders/sky_2d.gdshader")
const AMBIENT_STORM: Color = Color(0.4, 0.44, 0.64)
const AMBIENT_CLEAR: Color = Color(0.56, 0.6, 0.82)
## Where the rain falls: the whole stage, with a margin for the camera.
const STAGE: Rect2 = Rect2(-300.0, -120.0, 2520.0, 1300.0)
const QUAY_Y: float = 822.0
const HUSH_SECONDS: float = 0.25
## Bloom per blur level, fine to wide: a tight core and a broad soft haze.
const GLOW_LEVELS: Array[float] = [0.0, 0.5, 0.8, 0.6, 0.35, 0.18, 0.08]
const MIX_RATE: int = 22050
## The light mask rain and splashes are on: the lamps light it (their range_item_cull_mask
## includes it). The moon is not a light: the stone shades its own relief from it.
const RAIN_MASK: int = 2

## 1 a downpour under cloud, 0 a clear night.
var storm: float = 1.0:
	set(value):
		storm = clampf(value, 0.0, 1.0)
		_apply()
## True while paused: the world drains of colour behind the menu.
var hushed: bool = false:
	set(value):
		if value == hushed:
			return
		hushed = value
		_hush_to(0.25 if value else 1.0, 0.75 if value else 1.0)

var _sky: ColorRect = null
var _environment: Environment = null
var _ambient: CanvasModulate = null
var _rain_lit: GPUParticles2D = null
var _rain_dim: GPUParticles2D = null
var _splashes: GPUParticles2D = null
var _rain_sound: AudioStreamPlayer = null
var _hush: Tween = null


func _ready() -> void:
	var sky_layer: CanvasLayer = CanvasLayer.new()
	sky_layer.name = "SkyLayer"
	sky_layer.layer = -10
	add_child(sky_layer)
	_sky = ColorRect.new()
	_sky.name = "Sky"
	_sky.set_anchors_preset(Control.PRESET_FULL_RECT)
	_sky.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_sky.material = Paint.shader(SKY_SHADER)
	sky_layer.add_child(_sky)
	var world: WorldEnvironment = WorldEnvironment.new()
	world.name = "WorldEnvironment"
	_environment = Environment.new()
	_environment.background_mode = Environment.BG_CANVAS
	_environment.glow_enabled = true
	_environment.glow_intensity = 0.9
	_environment.glow_strength = 1.1
	_environment.glow_bloom = 0.04
	_environment.glow_hdr_threshold = 1.0
	_environment.glow_blend_mode = Environment.GLOW_BLEND_MODE_SCREEN
	for level: int in range(GLOW_LEVELS.size()):
		_environment.set_glow_level(level, GLOW_LEVELS[level])
	_environment.tonemap_mode = Environment.TONE_MAPPER_AGX
	_environment.tonemap_white = 6.0
	_environment.adjustment_enabled = true
	_environment.adjustment_contrast = 1.06
	_environment.adjustment_saturation = 1.0
	world.environment = _environment
	add_child(world)
	_ambient = CanvasModulate.new()
	_ambient.name = "Ambient"
	add_child(_ambient)
	_rain_dim = _rain(600, Color(0.62, 0.72, 0.95, 0.06), CanvasItemMaterial.LIGHT_MODE_UNSHADED)
	_rain_dim.name = "RainDim"
	_rain_lit = _rain(1600, Color(0.95, 0.95, 1.0, 0.9), CanvasItemMaterial.LIGHT_MODE_LIGHT_ONLY)
	_rain_lit.name = "RainLit"
	_splashes = _splash()
	_rain_sound = AudioStreamPlayer.new()
	_rain_sound.name = "RainSound"
	_rain_sound.stream = _rain_loop()
	add_child(_rain_sound)
	_rain_sound.play()
	_apply()


func _process(_delta: float) -> void:
	var view: Vector2 = get_viewport().get_visible_rect().size
	_sky.material.set(&"shader_parameter/aspect", view.x / maxf(view.y, 1.0))


func _apply() -> void:
	if _sky == null:
		return
	var calm: float = 1.0 - storm
	_sky.material.set(&"shader_parameter/clear", calm)
	_ambient.color = AMBIENT_STORM.lerp(AMBIENT_CLEAR, calm)
	_rain_lit.amount_ratio = lerpf(0.06, 1.0, storm)
	_rain_dim.amount_ratio = lerpf(0.04, 1.0, storm)
	_splashes.amount_ratio = lerpf(0.1, 1.0, storm)
	_rain_sound.volume_db = linear_to_db(lerpf(0.04, 0.22, storm))


## The colour the ambient dark is now: lit things divide by it to keep their own colour.
func ambient() -> Color:
	return _ambient.color


func _hush_to(saturation: float, brightness: float) -> void:
	if _hush != null:
		_hush.kill()
	_hush = create_tween().set_parallel(true).set_trans(Tween.TRANS_SINE)
	_hush.tween_property(_environment, ^"adjustment_saturation", saturation, HUSH_SECONDS)
	_hush.tween_property(_environment, ^"adjustment_brightness", brightness, HUSH_SECONDS)


## Streaks that fall across the whole stage, slanted by the wind. `light_mode` LIGHT_ONLY:
## they show only where a light falls.
func _rain(amount: int, colour: Color, light_mode: CanvasItemMaterial.LightMode) -> GPUParticles2D:
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(STAGE.size.x * 0.5, 10.0, 0.0)
	process.direction = Vector3(0.14, 1.0, 0.0)
	process.spread = 2.0
	process.initial_velocity_min = 1250.0
	process.initial_velocity_max = 1550.0
	process.gravity = Vector3(0.0, 500.0, 0.0)
	process.scale_min = 0.7
	process.scale_max = 1.4
	process.color = colour
	process.particle_flag_align_y = true
	process.particle_flag_disable_z = true
	var look: CanvasItemMaterial = CanvasItemMaterial.new()
	look.light_mode = light_mode
	var out: GPUParticles2D = GPUParticles2D.new()
	out.amount = amount
	out.lifetime = 0.95
	out.preprocess = 1.0
	out.process_material = process
	out.texture = Paint.streak(36)
	out.material = look
	out.local_coords = false
	out.z_index = 10
	out.light_mask = RAIN_MASK
	out.position = Vector2(STAGE.get_center().x, STAGE.position.y)
	out.visibility_rect = Rect2(-STAGE.size.x, -200.0, STAGE.size.x * 2.0, STAGE.size.y + 400.0)
	add_child(out)
	return out


## Droplets that jump where the rain hits the quay: lit only near a light.
func _splash() -> GPUParticles2D:
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(STAGE.size.x * 0.5, 4.0, 0.0)
	process.direction = Vector3(0.0, -1.0, 0.0)
	process.spread = 55.0
	process.initial_velocity_min = 50.0
	process.initial_velocity_max = 150.0
	process.gravity = Vector3(0.0, 700.0, 0.0)
	process.scale_min = 0.012
	process.scale_max = 0.03
	process.color = Color(1.0, 1.0, 1.0, 0.9)
	process.particle_flag_disable_z = true
	var look: CanvasItemMaterial = CanvasItemMaterial.new()
	look.light_mode = CanvasItemMaterial.LIGHT_MODE_LIGHT_ONLY
	var out: GPUParticles2D = GPUParticles2D.new()
	out.name = "Splashes"
	out.amount = 500
	out.lifetime = 0.35
	out.preprocess = 0.5
	out.process_material = process
	out.texture = Paint.soft_light(64)
	out.material = look
	out.local_coords = false
	out.z_index = 10
	out.light_mask = RAIN_MASK
	out.position = Vector2(STAGE.get_center().x, QUAY_Y)
	out.visibility_rect = Rect2(-STAGE.size.x, -200.0, STAGE.size.x * 2.0, 400.0)
	add_child(out)
	return out


## Two seconds of rain on stone, looping: soft rushing noise with scattered taps.
func _rain_loop() -> AudioStreamWAV:
	var random: RandomNumberGenerator = RandomNumberGenerator.new()
	random.seed = 11
	var count: int = MIX_RATE * 2
	var bytes: PackedByteArray = PackedByteArray()
	bytes.resize(count * 2)
	var low: float = 0.0
	var tap: float = 0.0
	for i: int in range(count):
		low = lerpf(low, random.randf_range(-1.0, 1.0), 0.08)
		if random.randf() < 0.004:
			tap = random.randf_range(0.3, 0.8)
		tap *= 0.93
		var sample: float = low * 0.6 + random.randf_range(-1.0, 1.0) * tap * 0.5
		bytes.encode_s16(i * 2, int(clampf(sample, -1.0, 1.0) * 32767.0))
	var out: AudioStreamWAV = AudioStreamWAV.new()
	out.format = AudioStreamWAV.FORMAT_16_BITS
	out.mix_rate = MIX_RATE
	out.stereo = false
	out.data = bytes
	out.loop_mode = AudioStreamWAV.LOOP_FORWARD
	out.loop_end = count
	return out
