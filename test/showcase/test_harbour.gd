extends GdUnitTestSuite
## The 2D showcase's scene, driven the way a player drives it: through input actions.

const HARBOUR_SCENE: PackedScene = preload("res://showcase/showcase_2d.tscn")
const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const ACTIONS: Array[StringName] = [
	&"move_left", &"move_right", &"move_up", &"move_down", &"interact", &"pause"
]
const START: int = 7
## Every lamp lit, from the start: 18 steps, as test/sequences/showcase_2d.json plays it.
const LIGHT_ALL: Array[StringName] = [
	&"move_right",
	&"move_right",
	&"move_right",
	&"interact",
	&"move_right",
	&"move_right",
	&"move_right",
	&"interact",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"move_left",
	&"interact",
	&"move_left",
	&"move_left",
	&"move_left",
	&"interact",
]
## Physics ticks a beat may take before the test gives up on it (3 seconds).
const BEAT_TICKS_MAX: int = 180

var _game: Harbour = null


func before_test() -> void:
	_game = auto_free(HARBOUR_SCENE.instantiate())
	add_child(_game)
	await get_tree().physics_frame


func after_test() -> void:
	for action: StringName in ACTIONS:
		Input.action_release(action)


func test_starts_at_the_start_with_every_lamp_dark() -> void:
	assert_int(_game.player_stop).is_equal(START)
	assert_int(_game.facing).is_equal(1)
	assert_int(_game.lit_count).is_zero()
	assert_bool(_game.awake).is_false()
	assert_bool(_game.paused).is_false()
	assert_int(_game.steps).is_zero()
	assert_float(_game.storm_shown).is_equal(1.0)


func test_a_move_hops_one_stop_and_lands_on_it() -> void:
	await _tap(&"move_left")
	assert_int(_game.player_stop).is_equal(START - 1)
	assert_int(_game.steps).is_equal(1)
	var goblin: Goblin2D = _game.get_node(^"Goblin")
	for _tick: int in range(BEAT_TICKS_MAX):
		if not goblin.hopping:
			break
		await get_tree().physics_frame
	assert_bool(goblin.hopping).is_false()
	assert_vector(goblin.position).is_equal(Vector2(Harbour.stop_x(START - 1), Harbour.WALK_Y))


func test_the_end_of_the_quay_turns_without_a_step() -> void:
	_game.motion_scale = 0.0
	for _step: int in range(START):
		await _tap(&"move_left")
	assert_int(_game.player_stop).is_zero()
	await _tap(&"move_right")
	await _tap(&"move_left")
	await _tap(&"move_left")
	assert_int(_game.player_stop).is_zero()
	assert_int(_game.facing).is_equal(-1)
	assert_int(_game.steps).is_equal(START + 2)


func test_interact_lights_the_lamp_and_its_spark_charges_the_lighthouse() -> void:
	for _step: int in range(3):
		await _tap(&"move_right")
	await _tap(&"interact")
	assert_int(_game.lit_count).is_equal(1)
	assert_int(_game.steps).is_equal(3)
	var lamp: QuayLamp = _game.get_node(^"Lamp_10")
	assert_bool(lamp.lit).is_true()
	var lighthouse: Lighthouse = _game.get_node(^"FarLayer/Far/Backdrop/Lighthouse")
	assert_int(lighthouse.charges).is_zero()  # the spark is still flying
	for _tick: int in range(BEAT_TICKS_MAX):
		if lighthouse.charges == 1:
			break
		await get_tree().physics_frame
	assert_int(lighthouse.charges).is_equal(1)
	assert_bool(lighthouse.awake).is_false()


func test_interact_away_from_a_lamp_lights_nothing() -> void:
	await _tap(&"interact")
	assert_int(_game.lit_count).is_zero()
	assert_int(_game.steps).is_zero()


## A lit lamp reads from across the quay: its halo shines, with flashes off too.
func test_a_lit_lamp_shines_and_a_dark_one_does_not() -> void:
	_game.flash_scale = 0.0
	for _step: int in range(3):
		await _tap(&"move_right")
	await _tap(&"interact")
	var lit: QuayLamp = _game.get_node(^"Lamp_10")
	var dark: QuayLamp = _game.get_node(^"Lamp_4")
	for _tick: int in range(BEAT_TICKS_MAX):
		if lit.shine > 0.3:
			break
		await get_tree().physics_frame
	assert_float(lit.shine).is_greater(0.3)
	assert_float(dark.shine).is_zero()


## Each landed spark eases the storm a little; the fourth clears it.
func test_each_lamp_eases_the_storm() -> void:
	_game.motion_scale = 0.0
	for _step: int in range(3):
		await _tap(&"move_right")
	await _tap(&"interact")
	var eased: float = 1.0 - Harbour.STORM_PER_LAMP
	for _tick: int in range(BEAT_TICKS_MAX * 2):
		if is_equal_approx(_game.storm_shown, eased):
			break
		await get_tree().physics_frame
	assert_float(_game.storm_shown).is_equal_approx(eased, 0.001)
	_game.reset()
	assert_float(_game.storm_shown).is_equal(1.0)


func test_the_fourth_lamp_wakes_the_lighthouse() -> void:
	_game.motion_scale = 0.0  # sparks land at once
	for action: StringName in LIGHT_ALL:
		await _tap(action)
	assert_int(_game.lit_count).is_equal(4)
	assert_bool(_game.awake).is_true()
	assert_int(_game.steps).is_equal(18)
	var lighthouse: Lighthouse = _game.get_node(^"FarLayer/Far/Backdrop/Lighthouse")
	assert_bool(lighthouse.awake).is_true()
	assert_int(lighthouse.charges).is_equal(4)
	var camera: HarbourCamera = _game.get_node(^"Camera")
	assert_float(camera.wide).is_equal(1.0)
	for _tick: int in range(BEAT_TICKS_MAX * 2):
		if lighthouse.wake_shown >= 1.0 and is_zero_approx(_game.storm_shown):
			break
		await get_tree().physics_frame
	assert_float(lighthouse.wake_shown).is_equal(1.0)
	assert_float(_game.storm_shown).is_equal_approx(0.0, 0.001)
	var town: HarbourTown = _game.get_node(^"Town")
	assert_float(town.lit_shown).is_equal(1.0)


## docs/feel.md: feel is presentation. The same inputs give the same rules at any setting.
func test_motion_flash_and_shake_never_change_the_rules() -> void:
	var calm: Dictionary = await _play_all(0.0)
	_game.reset()
	var lively: Dictionary = await _play_all(1.0)
	assert_dict(calm).is_equal(lively)
	assert_bool(calm["awake"]).is_true()


func test_with_motion_off_the_goblin_snaps_to_its_stop() -> void:
	_game.motion_scale = 0.0
	await _tap(&"move_left")
	var goblin: Goblin2D = _game.get_node(^"Goblin")
	assert_bool(goblin.hopping).is_false()
	assert_vector(goblin.position).is_equal(Vector2(Harbour.stop_x(START - 1), Harbour.WALK_Y))


func test_pause_blocks_moves_and_its_menu_changes_the_settings() -> void:
	await _tap(&"pause")
	assert_bool(_game.paused).is_true()
	var hud: ShowcaseHud = _game.get_node(^"Hud")
	assert_bool(hud.menu_open).is_true()
	var weather: HarbourWeather = _game.get_node(^"Weather")
	assert_bool(weather.hushed).is_true()
	await _tap(&"move_left")  # Motion, one notch down
	assert_int(_game.player_stop).is_equal(START)
	assert_float(_game.motion_scale).is_equal(0.75)
	await _tap(&"move_down")
	assert_int(_game.menu_index).is_equal(1)
	await _tap(&"move_left")
	assert_float(_game.flash_scale).is_equal(0.75)
	await _tap(&"interact")  # lights nothing while paused
	assert_int(_game.lit_count).is_zero()
	await _tap(&"pause")
	assert_bool(hud.menu_open).is_false()
	assert_bool(weather.hushed).is_false()
	await _tap(&"move_right")
	assert_int(_game.player_stop).is_equal(START + 1)


func test_reset_starts_over_and_keeps_the_settings() -> void:
	_game.motion_scale = 0.0
	for _step: int in range(3):
		await _tap(&"move_right")
	await _tap(&"interact")
	await _tap(&"pause")
	_game.reset()
	assert_int(_game.player_stop).is_equal(START)
	assert_int(_game.lit_count).is_zero()
	assert_bool(_game.paused).is_false()
	assert_float(_game.motion_scale).is_equal(0.0)
	assert_int(_game.steps).is_zero()
	var lamp: QuayLamp = _game.get_node(^"Lamp_10")
	assert_bool(lamp.lit).is_false()
	var lighthouse: Lighthouse = _game.get_node(^"FarLayer/Far/Backdrop/Lighthouse")
	assert_int(lighthouse.charges).is_zero()


## The 2D showcase's DevTools commands (showcase/game_2d/harbour_dev_commands.gd).
func test_the_harbour_registers_quay_and_reset_commands() -> void:
	if get_node_or_null(^"/root/DevTools") != null:
		return  # run with autoloads: this test stands in for the autoload and would clash
	var dev_tools: DevToolsScript = auto_free(DevToolsScript.new())
	dev_tools.name = "DevTools"
	get_tree().root.add_child(dev_tools)
	var session: String = "gdunit_harbour_%d" % OS.get_process_id()
	dev_tools.start(session)
	var game: Harbour = HARBOUR_SCENE.instantiate()
	add_child(game)
	await get_tree().physics_frame
	Input.action_press(&"move_right")
	await get_tree().physics_frame
	await get_tree().physics_frame
	Input.action_release(&"move_right")
	var quay: Dictionary = await dev_tools.execute("quay")
	assert_that(quay["data"]["player"]).is_equal(START + 1)
	assert_that(quay["data"]["awake"]).is_equal(false)
	assert_str(str(quay["message"])).is_equal("0 of 4 lamps lit")
	var answer: Dictionary = await dev_tools.execute("reset")
	assert_str(str(answer["message"])).is_equal("Quay reset")
	assert_int(game.player_stop).is_equal(START)
	game.free()
	var gone: Dictionary = await dev_tools.execute("quay")
	assert_str(str(gone["message"])).is_equal("Unknown command: quay")
	dev_tools.stop()
	get_tree().root.remove_child(dev_tools)
	var folder: String = OS.get_user_data_dir().path_join("devtools_" + session)
	for part: String in ["commands", "results", ""]:
		for file: String in DirAccess.get_files_at(folder.path_join(part)):
			DirAccess.remove_absolute(folder.path_join(part).path_join(file))
		DirAccess.remove_absolute(folder.path_join(part))


## Lights every lamp at `motion` and returns the rules' state.
func _play_all(motion: float) -> Dictionary:
	_game.motion_scale = motion
	_game.flash_scale = motion
	_game.shake_scale = motion
	for action: StringName in LIGHT_ALL:
		await _tap(action)
	var quay: Quay = _game.quay
	return {
		"player": quay.player,
		"facing": quay.facing,
		"steps": quay.moves,
		"lit": quay.lit_count(),
		"awake": quay.is_awake(),
	}


## Presses `action` for one whole physics tick, then releases it.
func _tap(action: StringName) -> void:
	Input.action_press(action)
	await get_tree().physics_frame
	await get_tree().physics_frame
	Input.action_release(action)
