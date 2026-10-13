extends GdUnitTestSuite
## The record of engine errors: what is kept and what a mark returns. Rows are fed in by
## hand: a real engine error would fail this test run too.

const ErrorLog := preload("res://addons/agent_devtools/dev_tools_errors.gd")


func test_rows_are_kept_in_order_and_read_back_from_a_mark() -> void:
	var errors: ErrorLog = ErrorLog.new()
	assert_int(errors.total()).is_zero()
	assert_array(errors.since(0)).is_empty()
	errors.record(ErrorLog.KIND_ERROR, "first", "core/a.cpp", 10, "load")
	var mark: int = errors.total()
	errors.record(ErrorLog.KIND_SCRIPT, "second", "res://b.gd", 20)
	errors.record(ErrorLog.KIND_SCRIPT, "third", "res://c.gd", 30)
	assert_int(errors.total()).is_equal(3)
	assert_int(errors.since(0).size()).is_equal(3)
	var later: Array[Dictionary] = errors.since(mark)
	assert_int(later.size()).is_equal(2)
	assert_that(later[0]).is_equal(
		{"kind": "script", "message": "second", "file": "res://b.gd", "line": 20, "function": ""}
	)
	assert_str(ErrorLog.describe(later[1])).is_equal("third (res://c.gd:30)")
	assert_array(errors.since(errors.total())).is_empty()
	assert_array(errors.since(99)).is_empty()


func test_only_the_newest_rows_are_kept_and_the_total_keeps_counting() -> void:
	var errors: ErrorLog = ErrorLog.new()
	for number: int in range(ErrorLog.KEPT_MAX + 5):
		errors.record(ErrorLog.KIND_ERROR, "error %d" % number, "x", number)
	assert_int(errors.total()).is_equal(ErrorLog.KEPT_MAX + 5)
	var kept: Array[Dictionary] = errors.since(0)
	assert_int(kept.size()).is_equal(ErrorLog.KEPT_MAX)
	assert_str(str(kept[0]["message"])).is_equal("error 5")
	assert_str(str(errors.since(errors.total() - 1)[0]["message"])).is_equal(
		"error %d" % (ErrorLog.KEPT_MAX + 4)
	)


func test_rows_filter_by_kind_and_describe_as_lines() -> void:
	var errors: ErrorLog = ErrorLog.new()
	errors.record(ErrorLog.KIND_ERROR, "engine", "core/a.cpp", 1)
	errors.record(ErrorLog.KIND_SCRIPT, "script", "res://b.gd", 2)
	var scripts: Array[Dictionary] = ErrorLog.of_kind(errors.since(0), ErrorLog.KIND_SCRIPT)
	assert_int(scripts.size()).is_equal(1)
	assert_str(str(scripts[0]["message"])).is_equal("script")
	assert_array(Array(ErrorLog.describe_all(errors.since(0)))).contains_exactly(
		["engine (core/a.cpp:1)", "script (res://b.gd:2)"]
	)


## The engine's callback: its explanation wins over the failed condition, the error type
## becomes the kind, warnings are left out.
func test_the_engine_callback_keeps_errors_and_drops_warnings() -> void:
	var errors: ErrorLog = ErrorLog.new()
	var no_trace: Array[ScriptBacktrace] = []
	var callback: Callable = Callable(errors, "_log_error")
	callback.call(
		"f", "core/a.cpp", 5, "Condition failed", "Why", false, Logger.ERROR_TYPE_ERROR, no_trace
	)
	callback.call(
		"g", "res://b.gd", 7, "Invalid access", "", false, Logger.ERROR_TYPE_SCRIPT, no_trace
	)
	callback.call("h", "res://c.gd", 9, "Unused", "", false, Logger.ERROR_TYPE_WARNING, no_trace)
	callback.call("i", "x.gdshader", 2, "Bad", "", false, Logger.ERROR_TYPE_SHADER, no_trace)
	var rows: Array[Dictionary] = errors.since(0)
	assert_int(rows.size()).is_equal(3)
	assert_that(rows[0]).is_equal(
		{"kind": "error", "message": "Why", "file": "core/a.cpp", "line": 5, "function": "f"}
	)
	assert_str(str(rows[1]["kind"])).is_equal("script")
	assert_str(str(rows[1]["message"])).is_equal("Invalid access")
	assert_str(str(rows[2]["kind"])).is_equal("shader")
