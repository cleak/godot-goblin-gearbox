class_name Lanternworks
extends Node3D
## The showcase game, Lanternworks: a goblin tinker on a floating island at blue hour lights
## four lanterns, and each sends a spark to the gearbox at the island's heart. The fourth
## wakes it. Arrows or WASD hop, E or Space lights the lantern the goblin faces, P or Escape
## pauses; while paused, up and down choose a setting and left and right change it.
##
## Reads input, asks the Isle, tells the views. Decides nothing itself.

const MOVES: Dictionary[StringName, Vector2i] = {
	&"move_left": Vector2i.LEFT,
	&"move_right": Vector2i.RIGHT,
	&"move_up": Vector2i.UP,
	&"move_down": Vector2i.DOWN,
}
const SETTING_STEP: float = 0.25
const SETTINGS: Array[StringName] = [&"motion_scale", &"flash_scale", &"shake_scale"]
const WAKE_TRAUMA: float = 0.6
const AWAKEN_SECONDS: float = 3.0
## How far each lit lantern takes the day towards night, and how long that takes.
const DUSK_PER_LANTERN: float = 0.17
const DUSK_SECONDS: float = 2.5
## How far the camera's focus leans from the island's centre towards the goblin.
const FOLLOW: float = 0.2
const GOBLIN_SIZE: float = 1.35
const LANTERN_SIZE: float = 1.25

var isle: Isle = Isle.standard()
var paused: bool = false
## Which pause-menu row is chosen: an index into SETTINGS.
var menu_index: int = 0
## Feel settings, each 0 to 1 (docs/feel.md). At 0 the game still reads: colour, light,
## sound and numbers carry every cue.
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
var player_cell: Vector2i:
	get:
		return isle.player
var facing: Vector2i:
	get:
		return isle.facing
var lit_count: int:
	get:
		return isle.lit_count()
var steps: int:
	get:
		return isle.moves
## The rules' verdict, at once; the views catch up when the last spark lands.
var awake: bool:
	get:
		return isle.is_awake()
## Presentation: 0 to 1 while the world wakes, after the last spark lands.
var wake_shown: float = 0.0:
	set(value):
		wake_shown = value
		if _atmosphere != null:
			_atmosphere.awake = value
			_island.awake = value

## Presentation: how far the day has gone, 0 golden hour to 1 night. Each lantern's spark
## sinks the sun a little further; the gearbox's waking brings the night.
var dusk_shown: float = 0.0:
	set(value):
		dusk_shown = value
		if _atmosphere != null:
			_atmosphere.dusk = value

var _atmosphere: Atmosphere = null
var _island: IslandView = null
var _gearbox: Gearbox = null
var _goblin: Goblin = null
var _camera: ShowcaseCamera = null
var _hud: ShowcaseHud = null
var _sfx: Sfx = null
var _sparks: Node3D = null
var _lanterns: Dictionary[Vector2i, Lantern] = {}
var _wake_tween: Tween = null
var _dusk_tween: Tween = null


func _ready() -> void:
	_atmosphere = Atmosphere.new()
	_atmosphere.name = "Atmosphere"
	add_child(_atmosphere)
	_island = IslandView.new()
	_island.name = "Island"
	add_child(_island)
	_island.build(isle)
	_gearbox = Gearbox.new()
	_gearbox.name = "Gearbox"
	add_child(_gearbox)
	_gearbox.position = _centre_of(isle.cells_with(Isle.Feature.GEARBOX))
	var middle: Vector3 = _gearbox.position
	for cell: Vector2i in isle.lantern_cells():
		var lantern: Lantern = Lantern.new()
		lantern.name = "Lantern_%d_%d" % [cell.x, cell.y]
		add_child(lantern)
		lantern.position = _world(cell)
		lantern.scale = Vector3.ONE * LANTERN_SIZE
		# The lamp hangs on the side towards the gearbox, where the camera sees it.
		var inward: Vector3 = (middle - lantern.position) * Vector3(1, 0, 1)
		lantern.rotation.y = atan2(-inward.z, inward.x)
		_lanterns[cell] = lantern
	_goblin = Goblin.new()
	_goblin.name = "Goblin"
	add_child(_goblin)
	_goblin.scale = Vector3.ONE * GOBLIN_SIZE
	_sparks = Node3D.new()
	_sparks.name = "Sparks"
	add_child(_sparks)
	_camera = ShowcaseCamera.new()
	_camera.name = "Camera"
	add_child(_camera)
	_camera.current = true
	_hud = ShowcaseHud.new()
	_hud.name = "Hud"
	add_child(_hud)
	_sfx = Sfx.new()
	_sfx.name = "Sfx"
	add_child(_sfx)
	_apply_settings()
	reset()
	get_viewport().msaa_3d = Viewport.MSAA_4X
	_atmosphere.bake_bounce_light(Vector3(15.0, 9.0, 15.0), Vector3(0.0, 1.2, 0.0))


## Starts over: a new isle, the goblin on the start cell, every lantern dark, not paused.
## The feel settings stay as the player left them.
func reset() -> void:
	isle = Isle.standard()
	paused = false
	if _wake_tween != null:
		_wake_tween.kill()
	wake_shown = 0.0
	if _dusk_tween != null:
		_dusk_tween.kill()
	dusk_shown = 0.0
	for spark: Node in _sparks.get_children():
		spark.queue_free()
	for cell: Vector2i in _lanterns:
		_lanterns[cell].set_lit(false)
	_gearbox.show_state(0, false)
	_goblin.place(_world(isle.player), isle.facing)
	_camera.focus = _focus()
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
	for action: StringName in MOVES:
		if Input.is_action_just_pressed(action):
			_step(MOVES[action])
	if Input.is_action_just_pressed(&"interact"):
		_interact()


func _process(_delta: float) -> void:
	_camera.focus = _focus()
	_island.set_goblin(_goblin.position)
	var faced: Vector2i = isle.faced_cell()
	for cell: Vector2i in _lanterns:
		_lanterns[cell].focus = 1.0 if cell == faced and not isle.is_lit(cell) else 0.0


## Switches costly effects by name; see Atmosphere.set_effects. "dof" is the camera's.
## Returns the names it knew.
func set_effects(switches: Dictionary[String, bool]) -> Array[String]:
	var known: Array[String] = _atmosphere.set_effects(switches)
	if switches.has("dof"):
		_camera.set_depth_of_field(switches["dof"])
		known.append("dof")
	return known


func _step(direction: Vector2i) -> void:
	if isle.move(direction):
		_goblin.hop_to(_world(isle.player), direction)
		_sfx.play(&"hop", -6.0)
	else:
		_goblin.bump(direction)
		_sfx.play(&"bump", -4.0)
	_refresh()


func _interact() -> void:
	_goblin.poke(isle.facing)
	if not isle.interact():
		_sfx.play(&"bump", -12.0)
		return
	var cell: Vector2i = isle.faced_cell()
	var lantern: Lantern = _lanterns[cell]
	lantern.ignite()
	_sfx.play(&"ignite", -3.0)
	var count: int = isle.lit_count()
	var spark: Spark = Spark.new()
	_sparks.add_child(spark)
	spark.landed.connect(_on_spark_landed.bind(count))
	spark.launch(lantern.lamp_position(), _gearbox.bulb_position(count - 1), motion_scale)
	_refresh()


func _on_spark_landed(count: int) -> void:
	_gearbox.charge(count)
	_sfx.play(&"chime", -4.0)
	_sfx.play(&"clunk", -6.0)
	if count == isle.lantern_count() and isle.is_awake():
		_wake()
		return
	_ease_dusk(DUSK_PER_LANTERN * count, DUSK_SECONDS)


## Eases the time of day towards `target` over `seconds`.
func _ease_dusk(target: float, seconds: float) -> void:
	if _dusk_tween != null:
		_dusk_tween.kill()
	_dusk_tween = create_tween()
	_dusk_tween.tween_property(self, ^"dusk_shown", target, seconds).set_trans(Tween.TRANS_SINE)


## The payoff: the gearbox spins up, the sky lights, the words pop.
func _wake() -> void:
	_gearbox.awaken()
	_camera.add_trauma(WAKE_TRAUMA)
	_sfx.play(&"wake", -2.0)
	_ease_dusk(1.0, AWAKEN_SECONDS)
	if _wake_tween != null:
		_wake_tween.kill()
	_wake_tween = create_tween().set_parallel(true)
	_wake_tween.tween_property(self, ^"wake_shown", 1.0, AWAKEN_SECONDS).set_trans(Tween.TRANS_SINE)
	# Staging: pull back and look up, so the beam and the aurora fill the frame.
	if motion_scale <= 0.0:
		_camera.wide = 1.0
	else:
		_wake_tween.tween_property(_camera, ^"wide", 1.0, AWAKEN_SECONDS).set_delay(0.4).set_trans(
			Tween.TRANS_SINE
		)


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
	_gearbox.motion_scale = motion_scale
	_gearbox.flash_scale = flash_scale
	_island.motion_scale = motion_scale
	for cell: Vector2i in _lanterns:
		_lanterns[cell].motion_scale = motion_scale
		_lanterns[cell].flash_scale = flash_scale


func _refresh() -> void:
	var values: Array[float] = [motion_scale, flash_scale, shake_scale]
	_hud.show_menu(paused, menu_index, values)
	_atmosphere.hushed = paused


func _focus() -> Vector3:
	var centre: Vector3 = Vector3(0.0, 0.6, 0.55)
	return centre.lerp(_goblin.position, FOLLOW)


func _world(cell: Vector2i) -> Vector3:
	return _island.position + IslandView.cell_top(isle, cell)


func _centre_of(cells: Array[Vector2i]) -> Vector3:
	assert(not cells.is_empty(), "the isle has no gearbox")
	var total: Vector3 = Vector3.ZERO
	for cell: Vector2i in cells:
		total += _world(cell)
	return total / cells.size()
