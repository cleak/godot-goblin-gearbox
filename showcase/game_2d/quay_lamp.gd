class_name QuayLamp
extends Node2D
## An iron lamp post on the 2D showcase's quay, its lantern hung from a curled bracket.
## Dark, a blue pilot ember glows in it, brighter while the goblin stands below. ignite() is
## the beat: the lantern draws in, flares, throws embers and swings on its chain. Lit, its
## light casts every prop's shadow on the house fronts and catches the rain.
## Presentation only: the Quay decides what is lit.
##
## The origin is the post's foot. Flames and halos must not be darkened by the night's
## ambient, so they live on the glow layer (`glow_parent`) and follow the lantern there.

const WARM: Color = Color(1.0, 0.62, 0.3)
const PILOT: Color = Color(0.35, 0.75, 1.0)
const POST_TALL: float = 236.0
const ARM: float = 50.0
const LIT_ENERGY: float = 1.7
const FLASH_ENERGY: float = 5.0
const REACH: float = 760.0
const IRON: Color = Color(0.13, 0.13, 0.15)
const IRON_LIGHT: Color = Color(0.38, 0.37, 0.4)

var lit: bool = false
## 1 while the goblin stands at this lamp; the pilot brightens towards it.
var focus: float = 0.0
## How bright the halo is, 0 while dark: tests read it.
var shine: float = 0.0
var motion_scale: float = 1.0
var flash_scale: float = 1.0
## Which way the bracket reaches: 1 right, -1 left.
var side: float = 1.0

var _lantern: Node2D = null
var _light: PointLight2D = null
var _glow_root: Node2D = null
var _flame: Polygon2D = null
var _halo: Sprite2D = null
var _pilot: Sprite2D = null
var _embers: GPUParticles2D = null
var _burst: GPUParticles2D = null
var _glow: float = 0.0
var _flash: float = 0.0
var _focus_shown: float = 0.0
var _swing: Spring = Spring.new(Vector3.ZERO, 40.0, 2.2)
var _time: float = 0.0
var _flicker: FastNoiseLite = FastNoiseLite.new()
var _pop: Tween = null


## Builds the lamp; its flame and halo go under `glow_parent`, on the glow layer.
func build(glow_parent: Node2D, reach_side: float) -> void:
	side = reach_side
	_flicker.seed = get_instance_id() % 991
	_flicker.frequency = 3.0
	_time = float(get_instance_id() % 97)
	# Plinth, shaft with collars, finial.
	Paint.polygon(self, Paint.rounded(Vector2(-14.0, -22.0), Vector2(28.0, 22.0), 4.0), IRON)
	Paint.polygon(self, Paint.rounded(Vector2(-10.0, -34.0), Vector2(20.0, 14.0), 3.0), IRON)
	Paint.polygon(self, Paint.box(Vector2(-5.0, -POST_TALL), Vector2(10.0, POST_TALL - 30.0)), IRON)
	Paint.polygon(
		self, Paint.box(Vector2(-4.0, -POST_TALL), Vector2(2.5, POST_TALL - 30.0)), IRON_LIGHT
	)
	for collar: float in [70.0, 150.0, POST_TALL - 4.0]:
		Paint.polygon(
			self, Paint.rounded(Vector2(-8.0, -collar - 5.0), Vector2(16.0, 8.0), 3.0), IRON
		)
	Paint.polygon(self, Paint.ellipse(Vector2(0.0, -POST_TALL - 10.0), Vector2(5.0, 7.0), 12), IRON)
	Paint.occluder(self, Paint.box(Vector2(-5.0, -POST_TALL), Vector2(10.0, POST_TALL)))
	# The bracket: a bar out to the side, braced by a curl.
	var bar_y: float = -POST_TALL + 18.0
	Paint.polygon(
		self, Paint.box(Vector2(minf(0.0, side * ARM), bar_y - 3.0), Vector2(ARM, 6.0)), IRON
	)
	var curl: PackedVector2Array = PackedVector2Array()
	for step: int in range(11):
		var angle: float = PI * 0.5 + step * PI * 0.12
		curl.append(Vector2(side * (18.0 - cos(angle) * 14.0), bar_y + 18.0 - sin(angle) * 14.0))
	for step: int in range(10, -1, -1):
		var angle: float = PI * 0.5 + step * PI * 0.12
		curl.append(Vector2(side * (18.0 - cos(angle) * 11.0), bar_y + 18.0 - sin(angle) * 11.0))
	Paint.polygon(self, curl, IRON)
	# The lantern hangs from the bracket's end and swings there.
	_lantern = Node2D.new()
	_lantern.name = "Lantern"
	_lantern.position = Vector2(side * (ARM - 6.0), bar_y + 3.0)
	add_child(_lantern)
	Paint.polygon(_lantern, Paint.box(Vector2(-1.0, 0.0), Vector2(2.0, 10.0)), IRON)
	Paint.polygon(
		_lantern,
		PackedVector2Array(
			[Vector2(-4.0, 10.0), Vector2(4.0, 10.0), Vector2(15.0, 20.0), Vector2(-15.0, 20.0)]
		),
		IRON
	)
	Paint.polygon(
		_lantern, Paint.box(Vector2(-12.0, 20.0), Vector2(24.0, 32.0)), Color(0.2, 0.22, 0.25, 0.85)
	)
	for bar: float in [-12.0, -1.0, 10.0]:
		Paint.polygon(_lantern, Paint.box(Vector2(bar, 20.0), Vector2(2.0, 32.0)), IRON)
	Paint.polygon(
		_lantern,
		PackedVector2Array(
			[Vector2(-14.0, 52.0), Vector2(14.0, 52.0), Vector2(8.0, 58.0), Vector2(-8.0, 58.0)]
		),
		IRON
	)
	_light = Paint.lamp_light(_lantern, Vector2(0.0, 36.0), WARM, REACH)
	_light.shadow_item_cull_mask = 3
	# On the glow layer: flame, halo, pilot ember and the ember particles.
	_glow_root = Node2D.new()
	_glow_root.name = "%sGlow" % name
	glow_parent.add_child(_glow_root)
	_halo = Paint.glow(_glow_root, Vector2(0.0, 36.0), 280.0, Color(WARM, 0.0))
	_pilot = Paint.glow(_glow_root, Vector2(0.0, 44.0), 40.0, Color(PILOT * 2.0, 0.0))
	_flame = (
		Paint
		. polygon(
			_glow_root,
			PackedVector2Array(
				[
					Vector2(0.0, 24.0),
					Vector2(4.0, 33.0),
					Vector2(5.0, 40.0),
					Vector2(2.5, 45.0),
					Vector2(-2.5, 45.0),
					Vector2(-5.0, 40.0),
					Vector2(-4.0, 33.0),
				]
			),
			WARM * 5.0
		)
	)
	_flame.visible = false
	_embers = _make_embers(18, 1.6, false)
	_burst = _make_embers(50, 0.8, true)
	_sync_glow()


## Lights the lamp with its whole beat.
func ignite() -> void:
	lit = true
	_flash = 1.0
	_swing.velocity += Vector3(7.0 * side, 0.0, 0.0) * motion_scale
	_embers.emitting = true
	_burst.restart()
	if _pop != null:
		_pop.kill()
	_pop = create_tween()
	_pop.tween_property(self, ^"_glow", 1.0, 0.25).set_trans(Tween.TRANS_QUAD)
	_lantern.scale = Vector2.ONE
	if motion_scale <= 0.0:
		return
	# Anticipation: the lantern draws in, then bursts tall and settles.
	var squash: Tween = create_tween()
	squash.tween_property(_lantern, ^"scale", Vector2(1.2, 0.82), 0.06)
	squash.tween_property(_lantern, ^"scale", Vector2(0.86, 1.25), 0.08)
	var settle: PropertyTweener = squash.tween_property(_lantern, ^"scale", Vector2.ONE, 0.35)
	settle.set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)


## Shows `value` at once, with no beat: for a reset.
func set_lit(value: bool) -> void:
	lit = value
	_glow = 1.0 if value else 0.0
	_flash = 0.0
	_swing.snap(Vector3.ZERO)
	_lantern.scale = Vector2.ONE
	_embers.emitting = value
	if _pop != null:
		_pop.kill()


## Where sparks leave from, in this canvas.
func lamp_position() -> Vector2:
	return _lantern.to_global(Vector2(0.0, 36.0))


func _process(delta: float) -> void:
	_time += delta
	_flash = maxf(_flash - delta * 3.0, 0.0)
	_focus_shown = move_toward(_focus_shown, focus, delta * 4.0)
	# The wind keeps the lantern rocking a little; ignite kicks it.
	_swing.target = Vector3(sin(_time * 1.3) * 0.05 * motion_scale, 0.0, 0.0)
	_lantern.rotation = _swing.step(delta).x
	var flicker: float = 0.86 + 0.14 * _flicker.get_noise_1d(_time * 20.0)
	var energy: float = LIT_ENERGY * _glow * flicker + FLASH_ENERGY * _flash * _flash * flash_scale
	_light.energy = energy
	_light.visible = energy > 0.01
	shine = clampf(0.55 * _glow * flicker + 0.45 * _flash * flash_scale, 0.0, 1.0)
	_halo.modulate = Color(WARM * 1.4, shine)
	_flame.visible = _glow > 0.01
	_flame.scale = Vector2(1.0, 0.9 + 0.2 * flicker) * _glow
	var pulse: float = 0.5 + 0.5 * sin(_time * 2.6)
	_pilot.modulate = Color(PILOT * 2.0, (1.0 - _glow) * (0.35 + 0.25 * pulse + 0.4 * _focus_shown))
	_sync_glow()


func _sync_glow() -> void:
	_glow_root.global_position = _lantern.global_position
	_glow_root.rotation = _lantern.global_rotation
	_glow_root.scale = _lantern.scale


func _make_embers(amount: int, lifetime: float, one_shot: bool) -> GPUParticles2D:
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	process.emission_sphere_radius = 5.0
	process.direction = Vector3(0.0, -1.0, 0.0)
	process.spread = 80.0 if one_shot else 25.0
	process.initial_velocity_min = 90.0 if one_shot else 12.0
	process.initial_velocity_max = 220.0 if one_shot else 35.0
	process.gravity = Vector3(0.0, 160.0, 0.0) if one_shot else Vector3(0.0, -20.0, 0.0)
	process.damping_min = 40.0
	process.damping_max = 90.0
	process.scale_min = 0.03
	process.scale_max = 0.07
	process.particle_flag_disable_z = true
	var ramp: Gradient = Gradient.new()
	ramp.set_color(0, Color(WARM * 4.0, 1.0))
	ramp.set_color(1, Color(1.0, 0.25, 0.05, 0.0))
	var colours: GradientTexture1D = GradientTexture1D.new()
	colours.gradient = ramp
	process.color_ramp = colours
	var out: GPUParticles2D = GPUParticles2D.new()
	out.amount = amount
	out.lifetime = lifetime
	out.one_shot = one_shot
	out.explosiveness = 0.95 if one_shot else 0.0
	out.emitting = false
	out.process_material = process
	out.texture = Paint.soft_light(64)
	out.material = Paint.additive()
	out.local_coords = false
	out.position = Vector2(0.0, 36.0)
	_glow_root.add_child(out)
	return out
