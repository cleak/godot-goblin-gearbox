class_name Spark
extends Node3D
## A mote of lantern fire that flies on an arc to the gearbox, trailing embers, and lands
## on a bulb. Presentation only: it carries no state; `landed` tells the views to catch up.

signal landed

const SECONDS: float = 0.75

var _from: Vector3 = Vector3.ZERO
var _to: Vector3 = Vector3.ZERO
var _peak: Vector3 = Vector3.ZERO
var _trail: GPUParticles3D = null


## Flies from `from` to `to` over SECONDS, or lands at once when `motion_scale` is 0.
func launch(from: Vector3, to: Vector3, motion_scale: float) -> void:
	_from = from
	_to = to
	_peak = (from + to) * 0.5 + Vector3(0.0, 1.6 + from.distance_to(to) * 0.25, 0.0)
	var glow: StandardMaterial3D = MeshKit.material(
		Color(1, 0.8, 0.5), 0.2, 0.0, Color(1.0, 0.65, 0.3)
	)
	glow.emission_energy_multiplier = 8.0
	glow.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	MeshKit.add_mesh(self, MeshKit.sphere(0.06, 10, 5), glow)
	var light: OmniLight3D = OmniLight3D.new()
	light.light_color = Color(1.0, 0.7, 0.4)
	light.light_energy = 2.0
	light.omni_range = 2.5
	add_child(light)
	var process: ParticleProcessMaterial = ParticleProcessMaterial.new()
	process.spread = 180.0
	process.initial_velocity_min = 0.05
	process.initial_velocity_max = 0.3
	process.gravity = Vector3(0.0, -0.6, 0.0)
	process.scale_min = 0.6
	process.scale_max = 1.4
	process.color_ramp = MeshKit.fade_ramp(Color(1.0, 0.8, 0.45), Color(1.0, 0.35, 0.1))
	_trail = MeshKit.particles(80, 0.6, process, 0.04)
	_trail.local_coords = false
	add_child(_trail)
	global_position = from
	if motion_scale <= 0.0:
		_arrive()
		return
	var flight: Tween = create_tween()
	flight.tween_method(_fly, 0.0, 1.0, SECONDS).set_trans(Tween.TRANS_SINE).set_ease(
		Tween.EASE_IN_OUT
	)
	flight.tween_callback(_arrive)


func _fly(t: float) -> void:
	var a: Vector3 = _from.lerp(_peak, t)
	var b: Vector3 = _peak.lerp(_to, t)
	global_position = a.lerp(b, t)


func _arrive() -> void:
	global_position = _to
	landed.emit()
	queue_free()
