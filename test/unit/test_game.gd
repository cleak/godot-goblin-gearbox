extends GdUnitTestSuite
## The sample scene, driven the way a player drives it: through input actions.

const MAIN_SCENE: PackedScene = preload("res://main.tscn")
const DevToolsScript := preload("res://addons/agent_devtools/dev_tools.gd")
const ACTIONS: Array[StringName] = [
	&"move_left", &"move_right", &"move_up", &"move_down", &"interact", &"pause"
]
## Physics ticks the slide tween may take before the test gives up on it (2 seconds).
const SLIDE_TICKS_MAX: int = 120

var _game: Game = null


func before_test() -> void:
	_game = auto_free(MAIN_SCENE.instantiate())
	add_child(_game)
	await get_tree().physics_frame


func after_test() -> void:
	for action: StringName in ACTIONS:
		Input.action_release(action)


func test_starts_on_the_start_cell_with_no_lamp_lit() -> void:
	assert_that(_game.player_cell).is_equal(Vector2i(3, 2))
	assert_int(_game.lit_count).is_zero()
	assert_bool(_game.paused).is_false()
	assert_str(_status()).is_equal("Lamps 0/3   Moves 0")


func test_a_move_action_steps_one_cell() -> void:
	await _tap(&"move_right")
	await _tap(&"move_down")
	assert_that(_game.player_cell).is_equal(Vector2i(4, 3))
	assert_str(_status()).is_equal("Lamps 0/3   Moves 2")


func test_holding_a_move_action_steps_once() -> void:
	Input.action_press(&"move_left")
	for _tick: int in range(5):
		await get_tree().physics_frame
	assert_that(_game.player_cell).is_equal(Vector2i(2, 2))


func test_the_player_slides_to_its_cell() -> void:
	var player: Node2D = _game.get_node(^"Player")
	var target: Vector2 = Vector2(4.5, 2.5) * Game.CELL_SIZE
	await _tap(&"move_right")
	# Exactly there, not nearly: the slide overshoots and passes close by on its way back.
	for _tick: int in range(SLIDE_TICKS_MAX):
		if player.position == target:
			break
		await get_tree().physics_frame
	assert_that(player.position).is_equal(target)


func test_interact_lights_the_lamp_under_the_player() -> void:
	await _tap(&"interact")
	assert_int(_game.lit_count).is_zero()
	await _tap(&"move_down")
	await _tap(&"interact")
	assert_int(_game.lit_count).is_equal(1)
	# The pulse springs back elastically: it dips below zero on its way to rest at zero.
	assert_float(_game.pulse).is_not_equal(0.0)
	assert_str(_status()).is_equal("Lamps 1/3   Moves 1")


func test_pause_blocks_moves_until_resumed() -> void:
	await _tap(&"pause")
	assert_bool(_game.paused).is_true()
	assert_str(_status()).is_equal("Paused")
	await _tap(&"move_right")
	assert_that(_game.player_cell).is_equal(Vector2i(3, 2))
	await _tap(&"pause")
	await _tap(&"move_right")
	assert_that(_game.player_cell).is_equal(Vector2i(4, 2))


func test_reset_starts_over() -> void:
	await _tap(&"move_down")
	await _tap(&"interact")
	await _tap(&"pause")
	assert_int(_game.lit_count).is_equal(1)
	_game.reset()
	assert_that(_game.player_cell).is_equal(Vector2i(3, 2))
	assert_int(_game.lit_count).is_zero()
	assert_bool(_game.paused).is_false()
	assert_str(_status()).is_equal("Lamps 0/3   Moves 0")
	var player: Node2D = _game.get_node(^"Player")
	assert_that(player.position).is_equal(Vector2(3.5, 2.5) * Game.CELL_SIZE)


## The game's own DevTools commands (scripts/game/dev_commands.gd) answer through the handler
## seam; a sequence starts with "reset" so it can run again and again.
func test_the_game_registers_reset_and_board_commands() -> void:
	if get_node_or_null(^"/root/DevTools") != null:
		return  # run with autoloads: this test stands in for the autoload and would clash
	var dev_tools: DevToolsScript = auto_free(DevToolsScript.new())
	dev_tools.name = "DevTools"
	get_tree().root.add_child(dev_tools)
	var session: String = "gdunit_game_%d" % OS.get_process_id()
	dev_tools.start(session)
	var game: Game = MAIN_SCENE.instantiate()
	add_child(game)
	await get_tree().physics_frame
	Input.action_press(&"move_right")
	await get_tree().physics_frame
	await get_tree().physics_frame
	Input.action_release(&"move_right")
	var board: Dictionary = await dev_tools.execute("board")
	assert_that(board["data"]["player"]).is_equal({"x": 4, "y": 2})
	var answer: Dictionary = await dev_tools.execute("reset")
	assert_str(str(answer["message"])).is_equal("Board reset")
	assert_that(game.player_cell).is_equal(Vector2i(3, 2))
	game.free()
	var gone: Dictionary = await dev_tools.execute("reset")
	assert_str(str(gone["message"])).is_equal("Unknown command: reset")
	dev_tools.stop()
	get_tree().root.remove_child(dev_tools)
	var folder: String = OS.get_user_data_dir().path_join("devtools_" + session)
	for part: String in ["commands", "results", ""]:
		for file: String in DirAccess.get_files_at(folder.path_join(part)):
			DirAccess.remove_absolute(folder.path_join(part).path_join(file))
		DirAccess.remove_absolute(folder.path_join(part))


## Presses `action` for one whole physics tick, then releases it.
func _tap(action: StringName) -> void:
	Input.action_press(action)
	await get_tree().physics_frame
	await get_tree().physics_frame
	Input.action_release(action)


func _status() -> String:
	var status: Label = _game.get_node(^"Hud/Status")
	return status.text
