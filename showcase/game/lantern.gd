class_name Lantern
extends Node3D
## A brass lantern hanging from a post. Dark, a rune at its foot glimmers so the player can
## find it, brighter while the goblin faces it. ignite() is the beat: a wind-up, a pop of
## light, embers, and a swing that settles. Presentation only: the Isle decides what is lit.

const WARM: Color = Color(1.0, 0.6, 0.28)
const RUNE: Color = Color(0.35, 0.8, 1.0)
const LIT_ENERGY: float = 4.5
const FLASH_ENERGY: float = 10.0
const HOOK: Vector3 = Vector3(0.34, 1.2, 0.0)

## Presentation's copy of the rule: set by ignite() and set_lit().
var lit: bool = false
## 1 while the goblin faces this lantern; the rune brightens towards it.
var focus: float = 0.0
## How bright the halo round the lamp is, 0 while dark: tests read it.
var shine: float = 0.0
var motion_scale: float = 1.0
var flash_scale: float = 1.0

var _lamp: Node3D = null
var _glass: StandardMaterial3D = null
var _pane: StandardMaterial3D = null
## The soft light round a lit lamp; its colour's alpha is how bright it is.
var _halo: StandardMaterial3D = null
var _rune: StandardMaterial3D = null
var _light: OmniLight3D = null
var _embers: GPUParticles3D = null
var _burst: GPUParticles3D = null
var _glow: float = 0.0
var _flash: float = 0.0
var _focus_shown: float = 0.0
var _swing: Vector2 = Vector2.ZERO
var _swing_velocity: Vector2 = Vector2.ZERO
var _time: float = 0.0
var _flicker: FastNoiseLite = FastNoiseLite.new()
var _pop: Tween = null


func _ready() -> void:
	_flicker.seed = get_instance_id() % 997
	_flicker.frequency = 2.5
	var wood: ShaderMaterial = Surfaces.wood(Color(0.36, 0.26, 0.18), Color(0.12, 0.08, 0.06), 0.5)
	var brass: ShaderMaterial = Surfaces.metal(Surfaces.BRASS, 0.24, 0.3)
	var stone: ShaderMaterial = Surfaces.stone(0.6)
	MeshKit.add_mesh(
		self, MeshKit.rounded_box(Vector3(0.28, 0.14, 0.28), 0.04, 2), stone, Vector3(0, 0.07, 0)
	)
	MeshKit.add_mesh(
		self, MeshKit.rounded_box(Vector3(0.09, 1.25, 0.09), 0.022, 2), wood, Vector3(0, 0.74, 0)
	)
	var arm: MeshInstance3D = MeshKit.add_mesh(
		self, MeshKit.rounded_box(Vector3(0.06, 0.44, 0.06), 0.018, 2), wood, Vector3(0.17, 1.27, 0)
	)
	arm.rotation.z = PI * 0.5
	MeshKit.add_mesh(self, MeshKit.sphere(0.05, 16, 8), brass, Vector3(0, 1.39, 0))
	MeshKit.add_mesh(self, MeshKit.torus(0.05, 0.07, 20), brass, Vector3(0, 1.2, 0))
	_lamp = Node3D.new()
	_lamp.name = "Lamp"
	add_child(_lamp)
	_lamp.position = HOOK
	var hook: MeshInstance3D = MeshKit.add_mesh(
		_lamp, MeshKit.torus(0.012, 0.022, 12), brass, Vector3(0, -0.02, 0)
	)
	hook.rotation.x = PI * 0.5
	MeshKit.add_mesh(_lamp, MeshKit.cylinder(0.01, 0.1, 0.08, 20), brass, Vector3(0, -0.12, 0))
	MeshKit.add_mesh(_lamp, MeshKit.cylinder(0.1, 0.1, 0.02, 20), brass, Vector3(0, -0.165, 0))
	MeshKit.add_mesh(_lamp, MeshKit.cylinder(0.095, 0.08, 0.035, 20), brass, Vector3(0, -0.35, 0))
	for bar: int in range(4):
		var angle: float = bar * TAU / 4.0 + PI / 4.0
		var at: Vector3 = Vector3(cos(angle) * 0.078, -0.255, sin(angle) * 0.078)
		MeshKit.add_mesh(_lamp, MeshKit.cylinder(0.009, 0.009, 0.18, 8), brass, at)
	# The flame: a small hot teardrop, and the glass chimney round it.
	_glass = MeshKit.material(Color(1.0, 0.7, 0.4), 0.3, 0.0, WARM)
	_glass.emission_energy_multiplier = 0.0
	var flame: MeshInstance3D = MeshKit.add_mesh(
		_lamp, MeshKit.sphere(0.036, 12, 6), _glass, Vector3(0, -0.27, 0)
	)
	flame.scale = Vector3(1.0, 1.8, 1.0)
	flame.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# The chimney takes the flame's colour when lit: from afar the whole glass glows.
	_pane = Surfaces.glass(Color(1.0, 0.95, 0.85), WARM)
	_pane.emission_energy_multiplier = 0.0
	var pane: MeshInstance3D = MeshKit.add_mesh(
		_lamp, MeshKit.cylinder(0.072, 0.072, 0.17, 20), _pane
	)
	pane.position = Vector3(0, -0.255, 0)
	pane.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_halo = _make_halo()
	_light = OmniLight3D.new()
	_light.light_color = WARM
	_light.omni_range = 6.0
	_light.omni_attenuation = 1.4
	_light.light_energy = 0.0
	_light.light_volumetric_fog_energy = 1.2
	_lamp.add_child(_light)
	_light.position = Vector3(0, -0.25, 0)
	_rune = MeshKit.material(Color(0.05, 0.08, 0.1), 0.6, 0.0, RUNE)
	var ring: MeshInstance3D = MeshKit.add_mesh(
		self, MeshKit.torus(0.3, 0.36, 28), _rune, Vector3(0.0, 0.02, 0.0)
	)
	ring.scale = Vector3(1.0, 0.15, 1.0)
	_embers = _make_embers(24, 1.8, false)
	_burst = _make_embers(60, 0.9, true)


## Lights the lantern with its whole beat.
func ignite() -> void:
	lit = true
	_flash = 1.0
	_swing_velocity += Vector2(6.0, -4.0) * motion_scale
	_embers.emitting = true
	_burst.restart()
	if _pop != null:
		_pop.kill()
	_pop = create_tween()
	_pop.tween_property(self, ^"_glow", 1.0, 0.25).set_trans(Tween.TRANS_QUAD)
	_lamp.scale = Vector3.ONE
	if motion_scale <= 0.0:
		return
	# Anticipation: the lamp draws in, then bursts tall and settles.
	var squash: Tween = create_tween()
	squash.tween_property(_lamp, ^"scale", Vector3(1.18, 0.8, 1.18), 0.06)
	squash.tween_property(_lamp, ^"scale", Vector3(0.86, 1.3, 0.86), 0.08)
	var settle: PropertyTweener = squash.tween_property(_lamp, ^"scale", Vector3.ONE, 0.35)
	settle.set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)


## Shows `value` at once, with no beat: for a reset.
func set_lit(value: bool) -> void:
	lit = value
	_glow = 1.0 if value else 0.0
	_flash = 0.0
	_swing = Vector2.ZERO
	_swing_velocity = Vector2.ZERO
	_lamp.scale = Vector3.ONE
	_embers.emitting = value
	if _pop != null:
		_pop.kill()


## Where the lamp hangs, in world space: sparks fly from here.
func lamp_position() -> Vector3:
	return _lamp.global_position + Vector3(0.0, -0.25, 0.0)


func _process(delta: float) -> void:
	_time += delta
	_flash = maxf(_flash - delta * 3.0, 0.0)
	_focus_shown = move_toward(_focus_shown, focus, delta * 4.0)
	# Follow-through: the lamp swings on a damped spring about its hook.
	var pull: Vector2 = -_swing * 60.0 - _swing_velocity * 3.2
	_swing_velocity += pull * delta
	_swing += _swing_velocity * delta * motion_scale
	_swing = _swing.limit_length(0.6)
	_lamp.rotation = Vector3(_swing.y, 0.0, _swing.x)
	var flicker: float = 0.88 + 0.12 * _flicker.get_noise_1d(_time * 20.0)
	var energy: float = LIT_ENERGY * _glow * flicker + FLASH_ENERGY * _flash * _flash * flash_scale
	_light.light_energy = energy
	_light.visible = energy > 0.01
	_glass.emission_energy_multiplier = 18.0 * _glow * flicker + 10.0 * _flash * flash_scale
	_pane.emission_energy_multiplier = 1.6 * _glow * flicker + 3.0 * _flash * flash_scale
	shine = clampf(0.4 * _glow * flicker + 0.6 * _flash * flash_scale, 0.0, 1.0)
	_halo.albedo_color = Color(WARM, shine)
	var pulse: float = 0.5 + 0.5 * sin(_time * 2.4)
	var dark: float = 1.0 - _glow
	_rune.emission_energy_multiplier = dark * (0.35 + 0.35 * pulse + 2.2 * _focus_shown)


## A camera-facing sprite of soft additive light round the lamp, faded where it meets
## nearby surfaces so it never shows an edge.
func _make_halo() -> StandardMaterial3D:
	var gradient: Gradient = Gradient.new()
	gradient.set_color(0, Color.WHITE)
	gradient.set_color(1, Color(1.0, 1.0, 1.0, 0.0))
	gradient.add_point(0.18, Color(1.0, 1.0, 1.0, 0.55))
	gradient.add_point(0.5, Color(1.0, 1.0, 1.0, 0.12))
	var texture: GradientTexture2D = GradientTexture2D.new()
	texture.gradient = gradient
	texture.fill = GradientTexture2D.FILL_RADIAL
	texture.fill_from = Vector2(0.5, 0.5)
	texture.fill_to = Vector2(0.5, 0.0)
	texture.width = 128
	texture.height = 128
	var out: StandardMaterial3D = StandardMaterial3D.new()
	out.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	out.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	out.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	out.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	out.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	out.proximity_fade_enabled = true
	out.proximity_fade_distance = 0.25
	out.albedo_texture = texture
	out.albedo_color = Color(WARM, 0.0)
	var quad: QuadMesh = QuadMesh.new()
	quad.size = Vector2(1.1, 1.1)
	var sprite: MeshInstance3D = MeshKit.add_mesh(_lamp, quad, out, Vector3(0, -0.26, 0))
	sprite.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	sprite.gi_mode = GeometryInstance3D.GI_MODE_DISABLED
	return out


func _make_embers(amount: int, lifetime: float, one_shot: bool) -> GPUParticles3D:
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	process.emission_sphere_radius = 0.06
	process.direction = Vector3.UP
	process.spread = 80.0 if one_shot else 25.0
	process.initial_velocity_min = 1.2 if one_shot else 0.2
	process.initial_velocity_max = 2.4 if one_shot else 0.5
	process.gravity = Vector3(0.0, -1.2, 0.0) if one_shot else Vector3(0.0, 0.25, 0.0)
	process.damping_min = 1.5
	process.damping_max = 3.0
	process.turbulence_enabled = true
	process.turbulence_noise_strength = 0.6
	process.scale_min = 0.5
	process.scale_max = 1.2
	process.color_ramp = MeshKit.fade_ramp(Color(1.0, 0.75, 0.35), Color(1.0, 0.3, 0.05))
	var particles: GPUParticles3D = MeshKit.particles(amount, lifetime, process, 0.035)
	particles.one_shot = one_shot
	particles.explosiveness = 0.95 if one_shot else 0.0
	particles.emitting = false
	_lamp.add_child(particles)
	particles.position = Vector3(0.0, -0.22, 0.0)
	return particles
