class_name Gearbox
extends Node3D
## The machine at the island's heart: an iron housing on a stone plinth, brass gears that
## mesh, four bulbs (one per lantern), a crystal that floats above, a chimney.
##
## charge(n) lights the n-th bulb, and the gears try to turn and fail: anticipation.
## awaken() is the payoff: the gears spin up, the crystal flares, a beam climbs into the
## sky and a ring of light rolls out. Presentation only: the Isle decides when it wakes.

signal woke

const BRASS: Color = Color(0.86, 0.62, 0.26)
const CORE: Color = Color(0.35, 1.0, 0.85)
const HOUSING: Vector3 = Vector3(2.2, 1.25, 2.2)
const PLINTH_HEIGHT: float = 0.3
## Gears: where (on the housing), which way the axle points, teeth, pitch radius.
const GEARS: Array[Dictionary] = [
	{"at": Vector3(-0.42, 0.95, 1.13), "axis": Vector3.BACK, "teeth": 18, "radius": 0.5},
	{"at": Vector3(0.38, 0.7, 1.13), "axis": Vector3.BACK, "teeth": 9, "radius": 0.25},
	{"at": Vector3(1.13, 0.85, 0.15), "axis": Vector3.RIGHT, "teeth": 14, "radius": 0.4},
	{"at": Vector3(1.13, 0.71, -0.56), "axis": Vector3.RIGHT, "teeth": 8, "radius": 0.23},
	{"at": Vector3(-1.13, 0.9, 0.0), "axis": Vector3.LEFT, "teeth": 16, "radius": 0.45},
	{"at": Vector3(0.0, 1.66, 0.0), "axis": Vector3.UP, "teeth": 24, "radius": 0.78},
	{"at": Vector3(0.8, 1.66, 0.8), "axis": Vector3.UP, "teeth": 9, "radius": 0.26},
]
## Turns per second of an 18-tooth gear at full speed; the others turn to match.
const BASE_TURNS: float = 0.35

## How many bulbs are lit; set by charge().
var charges: int = 0
## True once awaken() has run, until a reset.
var awake: bool = false
## 0 still, 1 full speed.
var spin: float = 0.0
var motion_scale: float = 1.0
var flash_scale: float = 1.0

var _gears: Array[Node3D] = []
var _gear_speeds: Array[float] = []
var _gear_angles: Array[float] = []
var _bulbs: Array[StandardMaterial3D] = []
var _crystal: Node3D = null
var _crystal_material: StandardMaterial3D = null
var _core_light: OmniLight3D = null
## A warm lamp before the front gears: brighter with every charge.
var _work_light: OmniLight3D = null
var _needle: Node3D = null
## Shown on the gauge: rises with each charge, quivers while the machine runs.
var _pressure: Spring = Spring.new(Vector3.ZERO, 40.0, 7.0)
var _rods: Array[Node3D] = []
## Scaled in y from 0 to 1 to grow the beam up out of the crystal.
var _beam_pivot: Node3D = null
var _beam_material: ShaderMaterial = null
var _ring: MeshInstance3D = null
var _ring_material: StandardMaterial3D = null
var _steam: GPUParticles3D = null
var _sparks: GPUParticles3D = null
var _jolt: Spring = Spring.new(Vector3.ZERO, 260.0, 9.0)
var _glow: float = 0.0
var _flare: float = 0.0
var _time: float = 0.0
var _wake: Tween = null


func _ready() -> void:
	var stone: ShaderMaterial = Surfaces.stone(0.45)
	var enamel: ShaderMaterial = Surfaces.painted(Color(0.05, 0.22, 0.23))
	var brass: ShaderMaterial = Surfaces.metal(Surfaces.BRASS, 0.22, 0.22)
	var copper: ShaderMaterial = Surfaces.metal(Surfaces.COPPER, 0.3, 0.3)
	var iron: ShaderMaterial = Surfaces.metal(Surfaces.IRON * 0.55, 0.45, 0.0)
	var top: float = PLINTH_HEIGHT + HOUSING.y
	# A plinth of dressed stone blocks, a hair apart.
	for x: int in range(3):
		for z: int in range(3):
			var block: Vector3 = Vector3((x - 1) * 0.95, PLINTH_HEIGHT * 0.5, (z - 1) * 0.95)
			var slab: MeshInstance3D = MeshKit.add_mesh(
				self, MeshKit.rounded_box(Vector3(0.94, PLINTH_HEIGHT, 0.94), 0.05, 2), stone, block
			)
			slab.rotation.y = (x * 3 + z) * 0.007 - 0.03
	MeshKit.add_mesh(
		self,
		MeshKit.rounded_box(HOUSING, 0.07, 3),
		enamel,
		Vector3(0, PLINTH_HEIGHT + HOUSING.y * 0.5, 0)
	)
	# Brass edging round the roof and the foot: four bars each, the enamel showing between.
	var bar_long: ArrayMesh = MeshKit.rounded_box(Vector3(2.34, 0.09, 0.12), 0.035, 2)
	for y: float in [top, PLINTH_HEIGHT + 0.045]:
		for z: float in [-1.11, 1.11]:
			MeshKit.add_mesh(self, bar_long, brass, Vector3(0, y, z))
		for x: float in [-1.11, 1.11]:
			var bar: MeshInstance3D = MeshKit.add_mesh(self, bar_long, brass, Vector3(x, y, 0))
			bar.rotation.y = PI * 0.5
	var pillar: ArrayMesh = MeshKit.rounded_box(Vector3(0.17, HOUSING.y + 0.12, 0.17), 0.04, 2)
	var glass: StandardMaterial3D = Surfaces.glass(Color(1.0, 0.9, 0.8))
	for corner: Vector2 in [Vector2(1, 1), Vector2(-1, 1), Vector2(1, -1), Vector2(-1, -1)]:
		var at: Vector3 = Vector3(corner.x * 1.12, PLINTH_HEIGHT + HOUSING.y * 0.5, corner.y * 1.12)
		MeshKit.add_mesh(self, pillar, brass, at)
		var cap: Vector3 = at + Vector3(0, HOUSING.y * 0.5 + 0.06, 0)
		MeshKit.add_mesh(self, MeshKit.cylinder(0.07, 0.09, 0.06, 16), brass, cap)
		# A glass bulb round a filament that glows when its lantern's spark lands.
		var filament: StandardMaterial3D = MeshKit.material(
			Color(0.3, 0.2, 0.1), 0.4, 0.0, Color(1.0, 0.55, 0.2)
		)
		filament.emission_energy_multiplier = 0.0
		MeshKit.add_mesh(self, MeshKit.sphere(0.045, 12, 6), filament, cap + Vector3(0, 0.14, 0))
		MeshKit.add_mesh(self, MeshKit.sphere(0.1, 24, 12), glass, cap + Vector3(0, 0.13, 0))
		_bulbs.append(filament)
	# Rivets along the housing's seams: small domes that each catch the sun.
	var rivet: SphereMesh = MeshKit.sphere(0.022, 10, 5)
	for i: int in range(11):
		var x: float = -1.0 + i * 0.2
		for y: float in [top - 0.09, PLINTH_HEIGHT + 0.14]:
			MeshKit.add_mesh(self, rivet, brass, Vector3(x, y, 1.106))
			MeshKit.add_mesh(self, rivet, brass, Vector3(1.106, y, x))
	_build_gears(brass, copper)
	_build_chimney(copper)
	_build_gauge(brass, glass)
	_build_nameplate(brass)
	_build_pistons(iron, brass, top)
	_build_crystal(brass, top)
	_build_beam(top)
	_work_light = OmniLight3D.new()
	_work_light.light_color = Color(1.0, 0.7, 0.4)
	_work_light.omni_range = 2.6
	_work_light.light_energy = 0.0
	add_child(_work_light)
	_work_light.position = Vector3(0.0, 0.9, 1.9)


## Lights bulb `count`; the machine strains to start and settles back.
func charge(count: int) -> void:
	charges = clampi(count, 0, _bulbs.size())
	for i: int in range(_bulbs.size()):
		_bulbs[i].emission_energy_multiplier = 9.0 if i < charges else 0.0
	_jolt.velocity += Vector3(3.5 + charges, 0.0, 0.0) * motion_scale
	_flare = maxf(_flare, 0.5)


## The payoff: spin up, flare, beam, ring of light.
func awaken() -> void:
	awake = true
	_flare = 1.0
	_steam.emitting = true
	_sparks.restart()
	_ring.visible = true
	_ring.scale = Vector3(0.2, 1.0, 0.2)
	_ring_material.albedo_color.a = 1.0
	if _wake != null:
		_wake.kill()
	_wake = create_tween().set_parallel(true)
	_wake.tween_property(self, ^"spin", 1.0, 2.2).set_trans(Tween.TRANS_QUAD).set_ease(
		Tween.EASE_IN
	)
	_wake.tween_property(self, ^"_glow", 1.0, 1.2).set_trans(Tween.TRANS_SINE)
	(
		_wake
		. tween_property(_ring, ^"scale", Vector3(14.0, 1.0, 14.0), 1.6)
		. set_ease(Tween.EASE_OUT)
		. set_trans(Tween.TRANS_CUBIC)
	)
	_wake.tween_property(_ring_material, ^"albedo_color:a", 0.0, 1.6).set_ease(Tween.EASE_IN)
	(
		_wake
		. tween_method(_set_beam, 0.0, 1.0, 0.9)
		. set_delay(0.15)
		. set_trans(Tween.TRANS_BACK)
		. set_ease(Tween.EASE_OUT)
	)
	_wake.chain().tween_callback(woke.emit)


## Shows `count` charges and `is_awake` at once, with no beat: for a reset.
func show_state(count: int, is_awake: bool) -> void:
	if _wake != null:
		_wake.kill()
	charges = count
	awake = is_awake
	for i: int in range(_bulbs.size()):
		_bulbs[i].emission_energy_multiplier = 9.0 if i < charges else 0.0
	spin = 1.0 if is_awake else 0.0
	_glow = 1.0 if is_awake else 0.0
	_flare = 0.0
	_jolt.snap(Vector3.ZERO)
	_set_beam(1.0 if is_awake else 0.0)
	_ring.visible = false
	_steam.emitting = is_awake


## Where sparks from the lanterns fly to: the bulb they light.
func bulb_position(index: int) -> Vector3:
	var corners: Array[Vector2] = [Vector2(1, 1), Vector2(-1, 1), Vector2(1, -1), Vector2(-1, -1)]
	var corner: Vector2 = corners[clampi(index, 0, corners.size() - 1)]
	var local: Vector3 = Vector3(corner.x * 1.12, PLINTH_HEIGHT + HOUSING.y + 0.2, corner.y * 1.12)
	return global_transform * local


func crystal_position() -> Vector3:
	return _crystal.global_position


func _process(delta: float) -> void:
	_time += delta
	_flare = maxf(_flare - delta * 0.9, 0.0)
	_jolt.step(delta)
	var strain: float = _jolt.value.x * 0.4
	for i: int in range(_gears.size()):
		_gear_angles[i] += _gear_speeds[i] * spin * delta * TAU * BASE_TURNS
		_gears[i].rotation = Vector3.ZERO
		_gears[i].rotate_object_local(
			Vector3.BACK, _gear_angles[i] + strain * signf(_gear_speeds[i])
		)
	var bob: float = sin(_time * 1.6) * 0.06 * motion_scale
	_crystal.position.y = PLINTH_HEIGHT + HOUSING.y + 1.05 + bob + _glow * 0.25
	_crystal.rotation.y = _time * (0.4 + spin * 1.6)
	var pulse: float = 0.85 + 0.15 * sin(_time * 3.0)
	var glow: float = 0.4 + 0.15 * charges + _glow * 3.5 * pulse
	_crystal_material.emission_energy_multiplier = glow + _flare * 8.0 * flash_scale
	_work_light.light_energy = 0.35 * charges + _glow * 0.6
	_core_light.light_energy = 0.15 * charges + _glow * 3.2 * pulse + _flare * 6.0 * flash_scale
	_pressure.target = Vector3(charges * 0.25 + spin * 0.05 * sin(_time * 9.0), 0.0, 0.0)
	_pressure.step(delta)
	_needle.rotation.z = lerpf(2.2, -2.2, clampf(_pressure.value.x, 0.0, 1.1))
	for i: int in range(_rods.size()):
		var stroke: float = (0.5 + 0.5 * sin(_gear_angles[0] * 2.0 + i * PI)) * spin
		_rods[i].position.y = PLINTH_HEIGHT + HOUSING.y + 0.32 + stroke * 0.16
	if awake and _beam_material != null:
		_beam_material.set_shader_parameter(&"strength", 0.85 + 0.15 * pulse)


func _set_beam(amount: float) -> void:
	_beam_pivot.visible = amount > 0.001
	var wide: float = 1.0 + (1.0 - clampf(amount, 0.0, 1.0)) * 0.5
	_beam_pivot.scale = Vector3(wide, maxf(amount, 0.001), wide)
	_beam_material.set_shader_parameter(&"strength", clampf(amount, 0.0, 1.5))


func _build_gears(brass: Material, copper: Material) -> void:
	for spec: Dictionary in GEARS:
		var teeth: int = spec["teeth"]
		var radius: float = spec["radius"]
		var axis: Vector3 = spec["axis"]
		var at: Vector3 = spec["at"]
		var mount: Node3D = Node3D.new()
		add_child(mount)
		# Every other gear stands a little proud, so meshing teeth never share a plane.
		mount.position = at + axis * 0.03 * (_gears.size() % 2)
		# Turn the gear's own Z (its axle) onto `axis`.
		if axis == Vector3.UP:
			mount.rotation.x = -PI * 0.5
		elif axis == Vector3.RIGHT:
			mount.rotation.y = PI * 0.5
		elif axis == Vector3.LEFT:
			mount.rotation.y = -PI * 0.5
		var gear: Node3D = Node3D.new()
		mount.add_child(gear)
		var surface: Material = copper if teeth < 12 else brass
		MeshKit.add_mesh(gear, MeshKit.gear_rim(teeth, radius, 0.09, 0.09, 0.08), surface)
		MeshKit.gear_hub(gear, radius, 0.09, 5 if teeth > 12 else 3, surface)
		_gears.append(gear)
		# Neighbours on one face turn the other way; small gears faster.
		var direction: float = 1.0 if _gears.size() % 2 == 0 else -1.0
		_gear_speeds.append(direction * 18.0 / teeth)
		_gear_angles.append(PI / teeth * (_gears.size() % 2))


func _build_chimney(copper: Material) -> void:
	var at: Vector3 = Vector3(-0.75, PLINTH_HEIGHT + HOUSING.y, -0.75)
	MeshKit.add_mesh(self, MeshKit.cylinder(0.12, 0.15, 0.9, 12), copper, at + Vector3(0, 0.45, 0))
	MeshKit.add_mesh(self, MeshKit.cylinder(0.17, 0.13, 0.12, 12), copper, at + Vector3(0, 0.92, 0))
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.direction = Vector3.UP
	process.spread = 12.0
	process.initial_velocity_min = 0.6
	process.initial_velocity_max = 1.0
	process.gravity = Vector3(0.35, 0.2, 0.1)
	process.damping_min = 0.3
	process.damping_max = 0.6
	process.scale_min = 4.0
	process.scale_max = 7.0
	var grow: Curve = Curve.new()
	grow.add_point(Vector2(0.0, 0.3))
	grow.add_point(Vector2(1.0, 1.0))
	var grow_texture: CurveTexture = CurveTexture.new()
	grow_texture.curve = grow
	process.scale_curve = grow_texture
	process.color_ramp = MeshKit.fade_ramp(Color(0.75, 0.8, 0.9, 0.3), Color(0.6, 0.65, 0.8, 0.12))
	_steam = MeshKit.particles(26, 2.6, process, 0.16, false)
	_steam.emitting = false
	add_child(_steam)
	_steam.position = at + Vector3(0, 1.0, 0)
	var burst: ParticleProcessMaterial = ParticleProcessMaterial.new()
	burst.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	burst.emission_sphere_radius = 0.2
	burst.direction = Vector3.UP
	burst.spread = 70.0
	burst.initial_velocity_min = 3.0
	burst.initial_velocity_max = 6.5
	burst.gravity = Vector3(0, -4.0, 0)
	burst.damping_min = 0.5
	burst.damping_max = 1.5
	burst.color_ramp = MeshKit.fade_ramp(Color(0.25, 0.9, 0.7), Color(0.6, 0.3, 0.1))
	_sparks = MeshKit.particles(140, 1.6, burst, 0.035)
	_sparks.one_shot = true
	_sparks.explosiveness = 0.9
	_sparks.emitting = false
	add_child(_sparks)
	_sparks.position = Vector3(0, PLINTH_HEIGHT + HOUSING.y + 1.1, 0)


func _build_crystal(brass: Material, top: float) -> void:
	MeshKit.add_mesh(self, MeshKit.cylinder(0.06, 0.08, 0.45, 8), brass, Vector3(0, top + 0.28, 0))
	MeshKit.add_mesh(self, MeshKit.torus(0.16, 0.22, 20), brass, Vector3(0, top + 0.52, 0))
	_crystal = Node3D.new()
	_crystal.name = "Crystal"
	add_child(_crystal)
	_crystal_material = MeshKit.material(Color(0.25, 0.6, 0.65), 0.05, 0.2, CORE)
	_crystal_material.emission_energy_multiplier = 0.4
	var upper: MeshInstance3D = MeshKit.add_mesh(
		_crystal, MeshKit.cylinder(0.0, 0.2, 0.42, 6), _crystal_material, Vector3(0, 0.21, 0)
	)
	upper.name = "Upper"
	var lower: MeshInstance3D = MeshKit.add_mesh(
		_crystal, MeshKit.cylinder(0.2, 0.0, 0.28, 6), _crystal_material, Vector3(0, -0.14, 0)
	)
	lower.name = "Lower"
	_core_light = OmniLight3D.new()
	_core_light.light_color = CORE
	_core_light.omni_range = 10.0
	_core_light.omni_attenuation = 1.3
	_core_light.light_energy = 0.0
	_core_light.light_volumetric_fog_energy = 0.6
	_core_light.shadow_enabled = true
	_crystal.add_child(_core_light)
	_ring_material = MeshKit.material(Color(0.4, 1.0, 0.9, 1.0), 1.0, 0.0, CORE)
	_ring_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_ring_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	_ring_material.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	_ring_material.albedo_color = Color(1.2, 3.0, 2.6, 1.0)
	_ring = MeshKit.add_mesh(
		self, MeshKit.torus(0.92, 1.0, 64), _ring_material, Vector3(0, 0.45, 0)
	)
	_ring.scale = Vector3(0.2, 1.0, 0.2)
	_ring.visible = false
	_ring.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF


func _build_beam(top: float) -> void:
	_beam_material = MeshKit.shader_material(preload("res://showcase/shaders/beam.gdshader"))
	# The mesh spans 0..1 in y so that scaling y grows it upward from the crystal.
	var holder: Node3D = Node3D.new()
	add_child(holder)
	holder.position = Vector3(0, top + 1.1, 0)
	holder.scale = Vector3(1.0, 40.0, 1.0)
	_beam_pivot = Node3D.new()
	holder.add_child(_beam_pivot)
	# A tight bright core inside a wide faint halo; both spread a little as they climb.
	# [top radius, bottom radius, softness, intensity]
	var layers: Array[Vector4] = [Vector4(0.42, 0.14, 4.0, 2.6), Vector4(1.6, 0.5, 5.0, 0.8)]
	for layer: Vector4 in layers:
		var column: CylinderMesh = MeshKit.cylinder(layer.x, layer.y, 1.0, 32)
		column.cap_top = false
		column.cap_bottom = false
		column.rings = 8
		var part: MeshInstance3D = MeshKit.add_mesh(
			_beam_pivot, column, _beam_material, Vector3(0, 0.5, 0)
		)
		part.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		part.set_instance_shader_parameter(&"softness", layer.z)
		part.set_instance_shader_parameter(&"intensity", layer.w)
	_beam_material.set_shader_parameter(&"strength", 0.0)
	_beam_pivot.visible = false


## A pressure gauge on the front face: brass bezel, cream dial, a red needle under glass.
func _build_gauge(brass: Material, glass: Material) -> void:
	var gauge: Node3D = Node3D.new()
	gauge.name = "Gauge"
	add_child(gauge)
	gauge.position = Vector3(0.72, PLINTH_HEIGHT + 1.0, 1.12)
	var bezel: MeshInstance3D = MeshKit.add_mesh(gauge, MeshKit.torus(0.13, 0.17, 32), brass)
	bezel.rotation.x = PI * 0.5
	var dial_material: StandardMaterial3D = Surfaces.cloth(Color(0.86, 0.8, 0.66), 0.55, 0.1)
	var dial: MeshInstance3D = MeshKit.add_mesh(
		gauge, MeshKit.cylinder(0.135, 0.135, 0.02, 32), dial_material, Vector3(0, 0, -0.01)
	)
	dial.rotation.x = PI * 0.5
	var ink: StandardMaterial3D = MeshKit.material(Color(0.1, 0.08, 0.06), 0.6)
	for tick: int in range(9):
		var angle: float = lerpf(2.2, -2.2, tick / 8.0) + PI * 0.5
		var mark: MeshInstance3D = MeshKit.add_mesh(
			gauge,
			MeshKit.box(Vector3(0.008, 0.03, 0.004)),
			ink,
			Vector3(cos(angle), sin(angle), 0.0) * 0.105 + Vector3(0, 0, 0.002)
		)
		mark.rotation.z = angle - PI * 0.5
	_needle = Node3D.new()
	gauge.add_child(_needle)
	_needle.position = Vector3(0, 0, 0.01)
	var red: StandardMaterial3D = MeshKit.material(Color(0.7, 0.08, 0.05), 0.35)
	MeshKit.add_mesh(_needle, MeshKit.box(Vector3(0.012, 0.11, 0.005)), red, Vector3(0, 0.05, 0))
	MeshKit.add_mesh(_needle, MeshKit.sphere(0.014, 8, 4), brass)
	var cover: MeshInstance3D = MeshKit.add_mesh(
		gauge, MeshKit.cylinder(0.135, 0.135, 0.01, 32), glass, Vector3(0, 0, 0.025)
	)
	cover.rotation.x = PI * 0.5


## Two pistons on the roof that pump once the machine runs.
func _build_pistons(iron: Material, brass: Material, top: float) -> void:
	for side: float in [-1.0, 1.0]:
		var at: Vector3 = Vector3(side * 0.55, top, -0.72)
		MeshKit.add_mesh(
			self, MeshKit.cylinder(0.11, 0.13, 0.36, 20), iron, at + Vector3(0, 0.18, 0)
		)
		MeshKit.add_mesh(self, MeshKit.torus(0.1, 0.135, 24), brass, at + Vector3(0, 0.36, 0))
		var rod: Node3D = Node3D.new()
		add_child(rod)
		rod.position = at + Vector3(0, 0.32, 0)
		MeshKit.add_mesh(rod, MeshKit.cylinder(0.035, 0.035, 0.3, 12), brass, Vector3(0, 0.15, 0))
		MeshKit.add_mesh(
			rod, MeshKit.rounded_box(Vector3(0.12, 0.05, 0.12), 0.015, 2), iron, Vector3(0, 0.31, 0)
		)
		_rods.append(rod)
	# A copper pipe from the chimney's foot over the roof edge and down the back.
	var pipe: Array[Vector3] = []
	var radii: Array[float] = []
	for k: int in range(9):
		var s: float = k / 8.0
		var bend: float = smoothstep(0.35, 0.75, s)
		pipe.append(Vector3(-0.75 + s * 1.5, top + 0.1 - bend * 0.0, -0.98 - bend * 0.18))
		radii.append(0.035)
	var copper: ShaderMaterial = Surfaces.metal(Surfaces.COPPER, 0.3, 0.45)
	MeshKit.add_mesh(self, MeshKit.tube(pipe, radii, 10), copper)
	for k: int in [1, 4, 7]:
		var clamp_ring: MeshInstance3D = MeshKit.add_mesh(
			self, MeshKit.torus(0.03, 0.05, 16), brass, pipe[k]
		)
		clamp_ring.rotation.z = PI * 0.5


## The machine's name, engraved in a brass plate on the plinth: the only words in the game.
func _build_nameplate(brass: Material) -> void:
	var plate: MeshInstance3D = MeshKit.add_mesh(
		self,
		MeshKit.rounded_box(Vector3(0.92, 0.15, 0.02), 0.008, 2),
		brass,
		Vector3(0.0, PLINTH_HEIGHT * 0.5, 1.43)
	)
	plate.name = "Nameplate"
	var serif: SystemFont = SystemFont.new()
	serif.font_names = PackedStringArray(
		["Cinzel", "Trajan Pro", "Georgia", "Times New Roman", "DejaVu Serif", "Liberation Serif"]
	)
	var tracked: FontVariation = FontVariation.new()
	tracked.base_font = serif
	tracked.spacing_glyph = 10
	var words: Label3D = Label3D.new()
	words.name = "Engraving"
	words.text = "LANTERNWORKS"
	words.font = tracked
	words.font_size = 44
	words.pixel_size = 0.0014
	words.outline_size = 0
	words.shaded = true
	words.double_sided = false
	words.modulate = Color(0.16, 0.1, 0.05)
	plate.add_child(words)
	words.position = Vector3(0.0, -0.004, 0.0115)
