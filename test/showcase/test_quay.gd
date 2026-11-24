extends GdUnitTestSuite
## The 2D showcase's rules (showcase/core/quay.gd), without a scene or a frame.

const START: int = 7


func test_the_standard_quay_starts_dark_at_its_start() -> void:
	var quay: Quay = Quay.standard()
	assert_int(quay.length).is_equal(15)
	assert_int(quay.player).is_equal(START)
	assert_int(quay.facing).is_equal(1)
	assert_int(quay.lamp_count()).is_equal(4)
	assert_int(quay.lit_count()).is_zero()
	assert_int(quay.moves).is_zero()
	assert_bool(quay.is_awake()).is_false()
	assert_array(quay.lamp_stops()).contains_exactly([1, 4, 10, 13])


func test_a_step_moves_one_stop_and_turns_the_goblin() -> void:
	var quay: Quay = Quay.standard()
	assert_bool(quay.move(-1)).is_true()
	assert_int(quay.player).is_equal(START - 1)
	assert_int(quay.facing).is_equal(-1)
	assert_int(quay.moves).is_equal(1)


func test_the_ends_of_the_quay_block_and_still_turn() -> void:
	var quay: Quay = Quay.new(".S.")
	assert_bool(quay.move(-1)).is_true()
	assert_bool(quay.move(-1)).is_false()
	assert_int(quay.player).is_equal(0)
	assert_int(quay.moves).is_equal(1)
	assert_bool(quay.move(1)).is_true()
	assert_bool(quay.move(1)).is_true()
	assert_bool(quay.move(1)).is_false()
	assert_int(quay.facing).is_equal(1)
	assert_int(quay.player).is_equal(2)


func test_interact_lights_the_lamp_where_the_goblin_stands_once() -> void:
	var quay: Quay = Quay.standard()
	assert_bool(quay.interact()).is_false()  # no lamp at the start
	for _step: int in range(3):
		quay.move(1)
	assert_int(quay.player).is_equal(10)
	assert_bool(quay.interact()).is_true()
	assert_bool(quay.is_lit(10)).is_true()
	assert_bool(quay.interact()).is_false()  # already lit
	assert_int(quay.lit_count()).is_equal(1)
	assert_int(quay.moves).is_equal(3)  # interacting is not a step


func test_every_lamp_lit_wakes_the_lighthouse() -> void:
	var quay: Quay = Quay.standard()
	for stop: int in [10, 13, 4, 1]:
		while quay.player != stop:
			quay.move(signi(stop - quay.player))
		assert_bool(quay.is_awake()).is_false()
		assert_bool(quay.interact()).is_true()
	assert_bool(quay.is_awake()).is_true()
	assert_int(quay.moves).is_equal(18)


func test_a_quay_without_lamps_never_wakes() -> void:
	var quay: Quay = Quay.new("..S..")
	assert_int(quay.lamp_count()).is_zero()
	assert_bool(quay.is_awake()).is_false()


func test_a_lamp_can_be_the_start() -> void:
	var quay: Quay = Quay.new(".s.")
	assert_int(quay.player).is_equal(1)
	assert_bool(quay.interact()).is_true()
	assert_bool(quay.is_awake()).is_true()
