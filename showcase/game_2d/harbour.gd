class_name Harbour
extends Node2D
## The 2D showcase, Lamplight Quay: on a night of rain a goblin lamplighter walks the
## harbour quay and lights four lamps. Each sends a spark across the water to the
## lighthouse and eases the storm; the fourth wakes the lighthouse, the town's windows light
## and the sky clears. Left and right (A, D or the arrows) walk, E or Space lights the lamp
## the goblin stands under, P or Escape pauses; while paused, up and down choose a setting
## and left and right change it.
##
## Reads input, asks the Quay, tells the views. Decides nothing itself.
##
## Layers, back to front: the sky (fixed), the far layer (sea, hills, lighthouse; it drifts
## at half the camera's speed), the world (houses, quay, lamps, goblin, rain, all shaded by
## the 2D lights under the night's ambient), the glow layer (flames, halos, the harbour
## water), and the pause veil.

const SETTING_STEP: float = 0.25
const SETTINGS: Array[StringName] = [&"motion_scale", &"flash_scale", &"shake_scale"]
const WAKE_TRAUMA: float = 0.5
const AWAKEN_SECONDS: float = 3.0
## How much each landed spark eases the storm, and how long that takes.
const STORM_PER_LAMP: float = 0.18
const STORM_SECONDS: float = 2.5
const FIRST_STOP_X: float = 160.0
const STOP_SPACING: float = 114.0
const WALK_Y: float = 830.0
const LAMP_Y: float = 812.0
const GOBLIN_SIZE: float = 1.3
## The far layer moves at this fraction of the camera's pan.
const PARALLAX: float = 0.5
## Where the sky shader draws the moon, as a fraction of the screen.
const MOON_ON_SCREEN: Vector2 = Vector2(0.85, 0.16)

var quay: Quay = Quay.standard()
var paused: bool = false
## Which pause-menu row is chosen: an index into SETTINGS.
var menu_index: int = 0
## Feel settings, each 0 to 1 (docs/feel.md). At 0 the game still reads: light, colour and
## sound carry every cue.
var motion_scale: float = 1.0:
	set(value):
		motion_scale = clampf(value, 0.0, 1.0)
		_apply_settings()
var flash_scale: float = 1.0:
	set(value):
		flash_scale = clampf(value, 0.0, 1.0)
		_apply_settings()
var shake_scale: float = 1.0:
	set(value):
		shake_scale = clampf(value, 0.0, 1.0)
		_apply_settings()
## Game state as plain properties: tests and DevTools sequences assert on these.
var player_stop: int:
	get:
		return quay.player
var facing: int:
	get:
		return quay.facing
var lit_count: int:
	get:
		return quay.lit_count()
var steps: int:
	get:
		return quay.moves
## The rules' verdict, at once; the views catch up when the last spark lands.
var awake: bool:
	get:
		return quay.is_awake()
## Presentation: the storm as shown, 1 a downpour to 0 a clear night.
var storm_shown: float = 1.0:
	set(value):
		storm_shown = value
		if _weather != null:
			_weather.storm = value

var _weather: HarbourWeather = null
var _far_root: Node2D = null
var _backdrop: HarbourBackdrop = null
var _town: HarbourTown = null
var _quay_view: QuayView = null
var _lamps: Dictionary[int, QuayLamp] = {}
var _goblin: Goblin2D = null
var _glow_root: Node2D = null
var _sparks: Node2D = null
var _camera: HarbourCamera = null
var _hud: ShowcaseHud = null
var _sfx: Sfx = null
var _storm_tween: Tween = null
var _wake_tween: Tween = null


func _ready() -> void:
	get_viewport().use_hdr_2d = true
	_weather = HarbourWeather.new()
	_weather.name = "Weather"
	add_child(_weather)
	var far_layer: CanvasLayer = CanvasLayer.new()
	far_layer.name = "FarLayer"
	far_layer.layer = -5
	far_layer.follow_viewport_enabled = true
	add_child(far_layer)
	_far_root = Node2D.new()
	_far_root.name = "Far"
	far_layer.add_child(_far_root)
	_backdrop = HarbourBackdrop.new()
	_backdrop.name = "Backdrop"
	_far_root.add_child(_backdrop)
	_backdrop.build()
	var glow_layer: CanvasLayer = CanvasLayer.new()
	glow_layer.name = "GlowLayer"
	glow_layer.layer = 1
	glow_layer.follow_viewport_enabled = true
	add_child(glow_layer)
	_glow_root = Node2D.new()
	_glow_root.name = "Glow"
	glow_layer.add_child(_glow_root)
	# Windows keep clear of each lamp's post and lantern.
	var lamp_xs: Array[float] = []
	for stop: int in quay.lamp_stops():
		lamp_xs.append(stop_x(stop))
		lamp_xs.append(stop_x(stop) + _reach_side(stop) * 44.0)
	_town = HarbourTown.new()
	_town.name = "Town"
	add_child(_town)
	_town.build(QuayView.STREET_Y, lamp_xs)
	_quay_view = QuayView.new()
	_quay_view.name = "Quay"
	add_child(_quay_view)
	_quay_view.build(_glow_root)
	for stop: int in quay.lamp_stops():
		var lamp: QuayLamp = QuayLamp.new()
		lamp.name = "Lamp_%d" % stop
		lamp.position = Vector2(stop_x(stop), LAMP_Y)
		add_child(lamp)
		lamp.build(_glow_root, _reach_side(stop))
		_lamps[stop] = lamp
	_goblin = Goblin2D.new()
	_goblin.name = "Goblin"
	_goblin.ground_y = WALK_Y
	_goblin.scale = Vector2.ONE * GOBLIN_SIZE
	add_child(_goblin)
	_goblin.build(_glow_root)
	_sparks = Node2D.new()
	_sparks.name = "Sparks"
	_glow_root.add_child(_sparks)
	_camera = HarbourCamera.new()
	_camera.name = "Camera"
	add_child(_camera)
	_camera.make_current()
	_hud = ShowcaseHud.new()
	_hud.name = "Hud"
	_hud.layer = 10
	add_child(_hud)
	_sfx = Sfx.new()
	_sfx.name = "Sfx"
	add_child(_sfx)
	_apply_settings()
	reset()


## Where stop `stop` is along the quay.
static func stop_x(stop: int) -> float:
	return FIRST_STOP_X + stop * STOP_SPACING


## Starts over: a new quay, the goblin at the start, every lamp dark, the storm back, not
## paused. The feel settings stay as the player left them.
func reset() -> void:
	quay = Quay.standard()
	paused = false
	if _storm_tween != null:
		_storm_tween.kill()
	if _wake_tween != null:
		_wake_tween.kill()
	storm_shown = 1.0
	for spark: Node in _sparks.get_children():
		spark.queue_free()
	for stop: int in _lamps:
		_lamps[stop].set_lit(false)
	_backdrop.lighthouse.show_state(0, false)
	_town.show_lit(false)
	_goblin.place(stop_x(quay.player), quay.facing)
	_camera.focus_x = _goblin.position.x
	_camera.wide = 0.0
	_camera.snap()
	_refresh()


func _physics_process(_delta: float) -> void:
	# Polled, so a real key and an action simulated by DevTools or a test look the same.
	if Input.is_action_just_pressed(&"pause"):
		paused = not paused
		_refresh()
	if paused:
		_navigate_menu()
		return
	if Input.is_action_just_pressed(&"move_left"):
		_step(-1)
	if Input.is_action_just_pressed(&"move_right"):
		_step(1)
	if Input.is_action_just_pressed(&"interact"):
		_interact()


func _process(_delta: float) -> void:
	_camera.focus_x = _goblin.position.x
	_far_root.position.x = (_camera.position.x - HarbourCamera.STAGE.x * 0.5) * PARALLAX
	_town.ambient = _weather.ambient()
	for stop: int in _lamps:
		_lamps[stop].focus = 1.0 if stop == quay.player and not quay.is_lit(stop) else 0.0
	_update_glints()


func _step(direction: int) -> void:
	if quay.move(direction):
		_goblin.hop_to(stop_x(quay.player), direction)
		_sfx.play(&"hop", -6.0)
	else:
		_goblin.bump(direction)
		_sfx.play(&"bump", -4.0)
	_refresh()


func _interact() -> void:
	_goblin.poke()
	if not quay.interact():
		_sfx.play(&"bump", -12.0)
		return
	var lamp: QuayLamp = _lamps[quay.player]
	lamp.ignite()
	_sfx.play(&"ignite", -3.0)
	var count: int = quay.lit_count()
	var spark: Spark2D = Spark2D.new()
	_sparks.add_child(spark)
	spark.landed.connect(_on_spark_landed.bind(count))
	spark.launch(lamp.lamp_position(), _backdrop.lighthouse.lamp_position, motion_scale)
	_refresh()


func _on_spark_landed(count: int) -> void:
	_backdrop.lighthouse.charge(count)
	_sfx.play(&"chime", -4.0)
	if count == quay.lamp_count() and quay.is_awake():
		_wake()
		return
	_ease_storm(1.0 - STORM_PER_LAMP * count, STORM_SECONDS)


func _ease_storm(target: float, seconds: float) -> void:
	if _storm_tween != null:
		_storm_tween.kill()
	_storm_tween = create_tween()
	_storm_tween.tween_property(self, ^"storm_shown", target, seconds).set_trans(Tween.TRANS_SINE)


## The payoff: the lighthouse wakes, the town lights, the rain stops, the sky clears.
func _wake() -> void:
	_backdrop.lighthouse.awaken()
	_town.light_up()
	_camera.add_trauma(WAKE_TRAUMA)
	_sfx.play(&"wake", -2.0)
	_sfx.play(&"clunk", -6.0)
	_ease_storm(0.0, AWAKEN_SECONDS)
	if _wake_tween != null:
		_wake_tween.kill()
	# Staging: ease back and over, so the beam and the clearing sky fill the frame.
	if motion_scale <= 0.0:
		_camera.wide = 1.0
		return
	_wake_tween = create_tween()
	_wake_tween.tween_property(_camera, ^"wide", 1.0, AWAKEN_SECONDS).set_delay(0.4).set_trans(
		Tween.TRANS_SINE
	)


## The lights the waters reflect: lamps and the goblin's flame in the harbour, the moon
## and the lighthouse out at sea.
func _update_glints() -> void:
	var near: Array[Vector2] = []
	for stop: int in _lamps:
		var lamp: QuayLamp = _lamps[stop]
		near.append(Vector2(lamp.lamp_position().x, lamp.shine * 1.3))
	near.append(Vector2(_goblin.tip_position().x, 0.25))
	_quay_view.set_glints(near, storm_shown)
	var view: Vector2 = get_viewport_rect().size
	var moon_screen: Vector2 = MOON_ON_SCREEN * view
	var moon_far: Vector2 = (
		_backdrop.get_global_transform_with_canvas().affine_inverse() * moon_screen
	)
	var clear: float = 1.0 - storm_shown
	_backdrop.set_glints(moon_far.x, 0.15 + 0.85 * clear, _backdrop.lighthouse.wake_shown)


func _navigate_menu() -> void:
	var before: int = menu_index
	if Input.is_action_just_pressed(&"move_up"):
		menu_index = posmod(menu_index - 1, SETTINGS.size())
	if Input.is_action_just_pressed(&"move_down"):
		menu_index = posmod(menu_index + 1, SETTINGS.size())
	var change: float = 0.0
	if Input.is_action_just_pressed(&"move_left"):
		change -= SETTING_STEP
	if Input.is_action_just_pressed(&"move_right"):
		change += SETTING_STEP
	if change != 0.0:
		var setting: StringName = SETTINGS[menu_index]
		var value: float = get(setting)
		set(setting, value + change)
		_sfx.play(&"hop", -10.0)
	if change != 0.0 or before != menu_index:
		_refresh()


func _apply_settings() -> void:
	if _sfx == null:
		return
	_goblin.motion_scale = motion_scale
	_camera.motion_scale = motion_scale
	_camera.shake_scale = shake_scale
	_town.motion_scale = motion_scale
	_backdrop.lighthouse.motion_scale = motion_scale
	_backdrop.lighthouse.flash_scale = flash_scale
	for stop: int in _lamps:
		_lamps[stop].motion_scale = motion_scale
		_lamps[stop].flash_scale = flash_scale


func _refresh() -> void:
	var values: Array[float] = [motion_scale, flash_scale, shake_scale]
	_hud.show_menu(paused, menu_index, values)
	_weather.hushed = paused


## Lamps left of the middle reach right with their brackets, the others left: towards the
## middle of the stage, where the camera looks.
func _reach_side(stop: int) -> float:
	return 1.0 if stop_x(stop) < HarbourCamera.STAGE.x * 0.5 else -1.0
