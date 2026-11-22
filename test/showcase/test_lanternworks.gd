extends GdUnitTestSuite
## The showcase scene, driven the way a player drives it: through input actions.

const SHOWCASE_SCENE: PackedScene = preload("res://showcase/showcase.tscn")
const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const ACTIONS: Array[StringName] = [
	&"move_left", &"move_right", &"move_up", &"move_down", &"interact", &"pause"
]
const START: Vector2i = Vector2i(5, 7)
## Every lantern lit, from the start: 15 steps, as test/sequences/showcase.json plays it.
const LIGHT_ALL: Array[StringName] = [
	&"move_right",
	&"interact",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"interact",
	&"move_up",
	&"move_up",
	&"move_up",
	&"move_up",
	&"move_up",
	&"move_up",
	&"move_left",
	&"interact",
	&"move_right",
	&"move_right",
	&"move_right",
	&"move_right",
	&"move_right",
	&"interact",
]
## Physics ticks a beat may take before the test gives up on it (3 seconds).
const BEAT_TICKS_MAX: int = 180

var _game: Lanternworks = null


func before_test() -> void:
	_game = auto_free(SHOWCASE_SCENE.instantiate())
	add_child(_game)
	await get_tree().physics_frame


func after_test() -> void:
	for action: StringName in ACTIONS:
		Input.action_release(action)


func test_starts_on_the_start_cell_with_every_lantern_dark() -> void:
	assert_that(_game.player_cell).is_equal(START)
	assert_that(_game.facing).is_equal(Vector2i.UP)
	assert_int(_game.lit_count).is_zero()
	assert_bool(_game.awake).is_false()
	assert_bool(_game.paused).is_false()
	assert_int(_game.steps).is_zero()


func test_a_move_hops_one_cell_and_lands_on_its_top() -> void:
	await _tap(&"move_right")
	assert_that(_game.player_cell).is_equal(START + Vector2i.RIGHT)
	assert_int(_game.steps).is_equal(1)
	var goblin: Goblin = _game.get_node(^"Goblin")
	var target: Vector3 = IslandView.cell_top(_game.isle, START + Vector2i.RIGHT)
	for _tick: int in range(BEAT_TICKS_MAX):
		if not goblin.hopping:
			break
		await get_tree().physics_frame
	assert_bool(goblin.hopping).is_false()
	assert_that(goblin.position).is_equal(target)


func test_a_blocked_move_turns_without_a_step() -> void:
	await _tap(&"move_left")
	await _tap(&"move_down")  # a boulder
	assert_that(_game.player_cell).is_equal(START + Vector2i.LEFT)
	assert_that(_game.facing).is_equal(Vector2i.DOWN)
	assert_int(_game.steps).is_equal(1)


func test_interact_lights_the_lantern_and_its_spark_charges_the_gearbox() -> void:
	await _tap(&"move_right")
	await _tap(&"interact")
	assert_int(_game.lit_count).is_equal(1)
	assert_int(_game.steps).is_equal(1)
	var lantern: Lantern = _game.get_node(^"Lantern_7_7")
	assert_bool(lantern.lit).is_true()
	var gearbox: Gearbox = _game.get_node(^"Gearbox")
	assert_int(gearbox.charges).is_zero()  # the spark is still flying
	for _tick: int in range(BEAT_TICKS_MAX):
		if gearbox.charges == 1:
			break
		await get_tree().physics_frame
	assert_int(gearbox.charges).is_equal(1)
	assert_bool(gearbox.awake).is_false()


## A lit lantern reads from across the island: its halo shines, with flashes off too.
func test_a_lit_lantern_shines_and_a_dark_one_does_not() -> void:
	_game.flash_scale = 0.0
	await _tap(&"move_right")
	await _tap(&"interact")
	var lit: Lantern = _game.get_node(^"Lantern_7_7")
	var dark: Lantern = _game.get_node(^"Lantern_1_7")
	for _tick: int in range(BEAT_TICKS_MAX):
		if lit.shine > 0.3:
			break
		await get_tree().physics_frame
	assert_float(lit.shine).is_greater(0.3)
	assert_float(dark.shine).is_zero()


func test_the_fourth_lantern_wakes_the_gearbox() -> void:
	_game.motion_scale = 0.0  # sparks land at once
	for action: StringName in LIGHT_ALL:
		await _tap(action)
	assert_int(_game.lit_count).is_equal(4)
	assert_bool(_game.awake).is_true()
	assert_int(_game.steps).is_equal(15)
	var gearbox: Gearbox = _game.get_node(^"Gearbox")
	assert_bool(gearbox.awake).is_true()
	var camera: ShowcaseCamera = _game.get_node(^"Camera")
	assert_float(camera.wide).is_equal(1.0)
	# No words announce it: the night falls and the gearbox lights the sky.
	assert_float(_game.dusk_shown).is_greater(0.0)


## Each landed spark sinks the sun a little; the fourth brings the night.
func test_each_lantern_takes_the_day_towards_night() -> void:
	_game.motion_scale = 0.0
	assert_float(_game.dusk_shown).is_zero()
	await _tap(&"move_right")
	await _tap(&"interact")
	for _tick: int in range(BEAT_TICKS_MAX * 2):
		if is_equal_approx(_game.dusk_shown, Lanternworks.DUSK_PER_LANTERN):
			break
		await get_tree().physics_frame
	assert_float(_game.dusk_shown).is_equal_approx(Lanternworks.DUSK_PER_LANTERN, 0.001)
	_game.reset()
	assert_float(_game.dusk_shown).is_zero()


## docs/feel.md: feel is presentation. The same inputs give the same rules at any setting.
func test_motion_flash_and_shake_never_change_the_rules() -> void:
	var calm: Dictionary = await _play_all(0.0)
	_game.reset()
	var lively: Dictionary = await _play_all(1.0)
	assert_dict(calm).is_equal(lively)
	assert_bool(calm["awake"]).is_true()


func test_with_motion_off_the_goblin_snaps_to_its_cell() -> void:
	_game.motion_scale = 0.0
	await _tap(&"move_left")
	var goblin: Goblin = _game.get_node(^"Goblin")
	assert_bool(goblin.hopping).is_false()
	assert_that(goblin.position).is_equal(IslandView.cell_top(_game.isle, START + Vector2i.LEFT))


func test_pause_blocks_moves_and_its_menu_changes_the_settings() -> void:
	await _tap(&"pause")
	assert_bool(_game.paused).is_true()
	var hud: ShowcaseHud = _game.get_node(^"Hud")
	assert_bool(hud.menu_open).is_true()
	var atmosphere: Atmosphere = _game.get_node(^"Atmosphere")
	assert_bool(atmosphere.hushed).is_true()
	await _tap(&"move_left")  # Motion, one notch down
	assert_that(_game.player_cell).is_equal(START)
	assert_float(_game.motion_scale).is_equal(0.75)
	await _tap(&"move_down")
	assert_int(_game.menu_index).is_equal(1)
	await _tap(&"move_left")
	await _tap(&"move_left")
	assert_float(_game.flash_scale).is_equal(0.5)
	await _tap(&"move_up")
	await _tap(&"move_up")  # wraps round to Shake
	assert_int(_game.menu_index).is_equal(2)
	await _tap(&"move_right")  # already full: stays at 1
	assert_float(_game.shake_scale).is_equal(1.0)
	await _tap(&"pause")
	assert_bool(hud.menu_open).is_false()
	assert_bool(atmosphere.hushed).is_false()
	await _tap(&"move_right")
	assert_that(_game.player_cell).is_equal(START + Vector2i.RIGHT)


func test_reset_starts_over_and_keeps_the_settings() -> void:
	_game.motion_scale = 0.0
	await _tap(&"move_right")
	await _tap(&"interact")
	await _tap(&"pause")
	_game.reset()
	assert_that(_game.player_cell).is_equal(START)
	assert_int(_game.lit_count).is_zero()
	assert_bool(_game.paused).is_false()
	assert_float(_game.motion_scale).is_equal(0.0)
	assert_int(_game.steps).is_zero()
	var lantern: Lantern = _game.get_node(^"Lantern_7_7")
	assert_bool(lantern.lit).is_false()
	var gearbox: Gearbox = _game.get_node(^"Gearbox")
	assert_int(gearbox.charges).is_zero()


## The showcase's DevTools commands (showcase/game/showcase_dev_commands.gd).
func test_the_showcase_registers_isle_and_reset_commands() -> void:
	if get_node_or_null(^"/root/DevTools") != null:
		return  # run with autoloads: this test stands in for the autoload and would clash
	var dev_tools: DevToolsScript = auto_free(DevToolsScript.new())
	dev_tools.name = "DevTools"
	get_tree().root.add_child(dev_tools)
	var session: String = "gdunit_showcase_%d" % OS.get_process_id()
	dev_tools.start(session)
	var game: Lanternworks = SHOWCASE_SCENE.instantiate()
	add_child(game)
	await get_tree().physics_frame
	Input.action_press(&"move_right")
	await get_tree().physics_frame
	await get_tree().physics_frame
	Input.action_release(&"move_right")
	var isle: Dictionary = await dev_tools.execute("isle")
	assert_that(isle["data"]["player"]).is_equal({"x": 6, "y": 7})
	assert_that(isle["data"]["awake"]).is_equal(false)
	var answer: Dictionary = await dev_tools.execute("reset")
	assert_str(str(answer["message"])).is_equal("Isle reset")
	assert_that(game.player_cell).is_equal(START)
	var fog_off: Dictionary = await dev_tools.execute("effects", {"fog": false, "dof": false})
	assert_bool(fog_off["success"]).is_true()
	var world: WorldEnvironment = game.get_node(^"Atmosphere/WorldEnvironment")
	assert_bool(world.environment.volumetric_fog_enabled).is_false()
	var camera: ShowcaseCamera = game.get_node(^"Camera")
	var lens: CameraAttributesPractical = camera.attributes as CameraAttributesPractical
	assert_bool(lens.dof_blur_far_enabled).is_false()
	var unknown: Dictionary = await dev_tools.execute("effects", {"bloom": false})
	assert_bool(unknown["success"]).is_false()
	var not_a_switch: Dictionary = await dev_tools.execute("effects", {"fog": 3})
	assert_bool(not_a_switch["success"]).is_false()
	game.free()
	var gone: Dictionary = await dev_tools.execute("isle")
	assert_str(str(gone["message"])).is_equal("Unknown command: isle")
	dev_tools.stop()
	get_tree().root.remove_child(dev_tools)
	var folder: String = OS.get_user_data_dir().path_join("devtools_" + session)
	for part: String in ["commands", "results", ""]:
		for file: String in DirAccess.get_files_at(folder.path_join(part)):
			DirAccess.remove_absolute(folder.path_join(part).path_join(file))
		DirAccess.remove_absolute(folder.path_join(part))


## Lights every lantern at `motion` and returns the rules' state.
func _play_all(motion: float) -> Dictionary:
	_game.motion_scale = motion
	_game.flash_scale = motion
	_game.shake_scale = motion
	for action: StringName in LIGHT_ALL:
		await _tap(action)
	var isle: Isle = _game.isle
	return {
		"player": isle.player,
		"facing": isle.facing,
		"steps": isle.moves,
		"lit": isle.lit_count(),
		"awake": isle.is_awake(),
	}


## Presses `action` for one whole physics tick, then releases it.
func _tap(action: StringName) -> void:
	Input.action_press(action)
	await get_tree().physics_frame
	await get_tree().physics_frame
	Input.action_release(action)
