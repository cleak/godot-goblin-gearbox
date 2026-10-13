extends GdUnitTestSuite
## Board rules. Pure logic: no scene, no frames.

const SIZE: Vector2i = Vector2i(3, 2)
const LAMPS: Array[Vector2i] = [Vector2i(0, 0), Vector2i(2, 1)]


func test_starts_where_asked_with_every_lamp_dark() -> void:
	var board: Board = Board.new(SIZE, Vector2i(1, 1), LAMPS)
	assert_that(board.player).is_equal(Vector2i(1, 1))
	assert_int(board.moves).is_zero()
	assert_int(board.lit_count()).is_zero()
	assert_array(board.lamp_cells()).contains_exactly_in_any_order(LAMPS)


func test_start_and_lamps_outside_the_board_are_clamped_or_dropped() -> void:
	var board: Board = Board.new(SIZE, Vector2i(9, -4), [Vector2i(1, 0), Vector2i(3, 0)])
	assert_that(board.player).is_equal(Vector2i(2, 0))
	assert_array(board.lamp_cells()).contains_exactly([Vector2i(1, 0)])


func test_move_steps_one_cell_and_counts() -> void:
	var board: Board = Board.new(SIZE, Vector2i(1, 0), LAMPS)
	assert_bool(board.move(Vector2i.RIGHT)).is_true()
	assert_bool(board.move(Vector2i.DOWN)).is_true()
	assert_that(board.player).is_equal(Vector2i(2, 1))
	assert_int(board.moves).is_equal(2)


func test_the_edge_blocks_a_move_and_does_not_count_it() -> void:
	var board: Board = Board.new(SIZE, Vector2i(0, 0), LAMPS)
	assert_bool(board.move(Vector2i.LEFT)).is_false()
	assert_bool(board.move(Vector2i.UP)).is_false()
	assert_that(board.player).is_equal(Vector2i.ZERO)
	assert_int(board.moves).is_zero()


func test_toggle_lights_only_the_lamp_under_the_player() -> void:
	var board: Board = Board.new(SIZE, Vector2i(0, 0), LAMPS)
	assert_bool(board.toggle_lamp()).is_true()
	assert_bool(board.is_lit(Vector2i(0, 0))).is_true()
	assert_bool(board.is_lit(Vector2i(2, 1))).is_false()
	assert_int(board.lit_count()).is_equal(1)
	assert_bool(board.toggle_lamp()).is_true()
	assert_int(board.lit_count()).is_zero()


func test_toggle_away_from_a_lamp_does_nothing() -> void:
	var board: Board = Board.new(SIZE, Vector2i(1, 0), LAMPS)
	assert_bool(board.toggle_lamp()).is_false()
	assert_int(board.lit_count()).is_zero()


func test_all_lit_needs_every_lamp() -> void:
	var board: Board = Board.new(Vector2i(2, 1), Vector2i(0, 0), [Vector2i(0, 0), Vector2i(1, 0)])
	assert_bool(board.all_lit()).is_false()
	board.toggle_lamp()
	assert_bool(board.all_lit()).is_false()
	board.move(Vector2i.RIGHT)
	board.toggle_lamp()
	assert_bool(board.all_lit()).is_true()
	assert_bool(Board.new(SIZE, Vector2i.ZERO, []).all_lit()).is_false()
