extends GdUnitTestSuite
## The showcase's rules (showcase/core/isle.gd), without a scene or a frame.

const START: Vector2i = Vector2i(5, 7)


func test_the_standard_isle_starts_dark_on_its_start_cell() -> void:
	var isle: Isle = Isle.standard()
	assert_that(isle.size).is_equal(Vector2i(9, 9))
	assert_that(isle.player).is_equal(START)
	assert_that(isle.facing).is_equal(Vector2i.UP)
	assert_int(isle.lantern_count()).is_equal(4)
	assert_int(isle.lit_count()).is_zero()
	assert_int(isle.moves).is_zero()
	assert_bool(isle.is_awake()).is_false()


func test_heights_and_features_come_from_the_map() -> void:
	var isle: Isle = Isle.standard()
	assert_int(isle.height_at(Vector2i(7, 1))).is_equal(3)
	assert_int(isle.height_at(Vector2i(0, 0))).is_equal(-1)
	assert_bool(isle.has_ground(Vector2i(0, 0))).is_false()
	assert_that(isle.feature_at(Vector2i(4, 4))).is_equal(Isle.Feature.GEARBOX)
	assert_that(isle.feature_at(Vector2i(2, 8))).is_equal(Isle.Feature.WATER)
	assert_int(isle.cells_with(Isle.Feature.GEARBOX).size()).is_equal(9)


func test_a_step_moves_one_cell_and_turns_the_goblin() -> void:
	var isle: Isle = Isle.standard()
	assert_bool(isle.move(Vector2i.RIGHT)).is_true()
	assert_that(isle.player).is_equal(START + Vector2i.RIGHT)
	assert_that(isle.facing).is_equal(Vector2i.RIGHT)
	assert_int(isle.moves).is_equal(1)


func test_a_blocked_step_turns_but_does_not_move_or_count() -> void:
	var isle: Isle = Isle.standard()
	assert_bool(isle.move(Vector2i.LEFT)).is_true()
	# South of (4, 7) is a boulder.
	assert_bool(isle.move(Vector2i.DOWN)).is_false()
	assert_that(isle.player).is_equal(START + Vector2i.LEFT)
	assert_that(isle.facing).is_equal(Vector2i.DOWN)
	assert_int(isle.moves).is_equal(1)


func test_the_edge_of_the_island_blocks() -> void:
	var isle: Isle = Isle.new(["0"], ["S"])
	for direction: Vector2i in [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]:
		assert_bool(isle.move(direction)).is_false()
	assert_that(isle.player).is_equal(Vector2i.ZERO)


func test_the_goblin_climbs_one_terrace_but_not_two() -> void:
	var isle: Isle = Isle.new(["012"], ["S.."])
	assert_bool(isle.move(Vector2i.RIGHT)).is_true()
	assert_bool(isle.move(Vector2i.RIGHT)).is_true()
	assert_bool(isle.move(Vector2i.LEFT)).is_true()
	var cliff: Isle = Isle.new(["02"], ["S."])
	assert_bool(cliff.move(Vector2i.RIGHT)).is_false()
	assert_bool(cliff.can_step(Vector2i(1, 0), Vector2i(0, 0))).is_false()


func test_trees_rocks_lanterns_water_and_the_gearbox_block() -> void:
	var isle: Isle = Isle.new(["000000"], ["TrSLWG"])
	assert_bool(isle.move(Vector2i.LEFT)).is_false()
	assert_bool(isle.move(Vector2i.RIGHT)).is_false()
	for blocked: Vector2i in [Vector2i(0, 0), Vector2i(1, 0), Vector2i(4, 0), Vector2i(5, 0)]:
		assert_bool(isle.is_open(blocked)).is_false()


func test_interact_lights_only_the_dark_lantern_the_goblin_faces() -> void:
	var isle: Isle = Isle.new(["000"], ["LS."])
	assert_bool(isle.interact()).is_false()  # it faces up, at the sky
	assert_bool(isle.move(Vector2i.RIGHT)).is_true()
	assert_bool(isle.interact()).is_false()  # faces away from the lantern
	assert_bool(isle.move(Vector2i.LEFT)).is_true()
	assert_bool(isle.move(Vector2i.LEFT)).is_false()  # bumps the lantern, faces it
	assert_bool(isle.faces_dark_lantern()).is_true()
	assert_bool(isle.interact()).is_true()
	assert_bool(isle.is_lit(Vector2i(0, 0))).is_true()
	assert_bool(isle.faces_dark_lantern()).is_false()
	assert_bool(isle.interact()).is_false()  # already lit
	assert_int(isle.lit_count()).is_equal(1)


func test_the_gearbox_wakes_when_every_lantern_burns() -> void:
	var isle: Isle = Isle.new(["000"], ["LSL"])
	isle.move(Vector2i.LEFT)
	isle.interact()
	assert_bool(isle.is_awake()).is_false()
	isle.move(Vector2i.RIGHT)
	isle.interact()
	assert_bool(isle.is_awake()).is_true()


func test_an_isle_without_lanterns_never_wakes() -> void:
	var isle: Isle = Isle.new(["0"], ["S"])
	assert_bool(isle.is_awake()).is_false()


## Every lantern of the standard isle has an open neighbour the goblin can walk to.
func test_every_lantern_can_be_reached() -> void:
	var isle: Isle = Isle.standard()
	var reachable: Dictionary[Vector2i, bool] = isle.reachable()
	for lantern: Vector2i in isle.lantern_cells():
		var reached: bool = false
		for step: Vector2i in [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]:
			reached = reached or reachable.has(lantern + step)
		(
			assert_bool(reached)
			. override_failure_message("lantern %s is out of reach" % lantern)
			. is_true()
		)


## The high plateau is reached by the stair alone: its cliffs are too tall to climb.
func test_the_plateau_has_cliffs() -> void:
	var isle: Isle = Isle.standard()
	assert_bool(isle.can_step(Vector2i(6, 3), Vector2i(6, 2))).is_false()
	assert_bool(isle.can_step(Vector2i(5, 1), Vector2i(6, 1))).is_true()
