extends GdUnitTestSuite
## DevTools mailbox: session and file names, one file per command and per reply, half-written
## commands, ordered writes, stale files, log rotation.

const Mailbox := preload("res://addons/agent_devtools/dev_tools_mailbox.gd")
## Bound on a wait for the mailbox thread, which polls every Mailbox.POLL_MSEC.
const THREAD_WAIT_MSEC: int = 5000

## Unique per process: several test runs may share one user:// folder.
var _session: String = "gdunit_mailbox_%d" % OS.get_process_id()
var _mailbox: Mailbox = null


func before_test() -> void:
	_mailbox = Mailbox.new(OS.get_user_data_dir().path_join(Mailbox.folder_name(_session)))
	_remove_files()


func after_test() -> void:
	_mailbox.stop()
	_remove_files()
	for folder: String in [_mailbox.commands_path, _mailbox.results_path, _mailbox.root_path]:
		DirAccess.remove_absolute(folder)


func test_session_names_keep_only_file_safe_characters() -> void:
	assert_str(Mailbox.sanitize_session("")).is_empty()
	assert_str(Mailbox.sanitize_session("agent-2")).is_equal("agent-2")
	assert_str(Mailbox.sanitize_session(" feature/one?two ")).is_equal("feature_one_two")
	assert_str(Mailbox.sanitize_session("a b\tc")).is_equal("a_b_c")
	assert_str(Mailbox.sanitize_session("naïve")).is_equal("na_ve")
	assert_str(Mailbox.sanitize_session("..\\..\\up")).is_equal("_.._up")
	assert_str(Mailbox.sanitize_session("..hidden..")).is_equal("hidden")
	assert_str(Mailbox.sanitize_session("...")).is_empty()


func test_the_folder_carries_the_session_only_when_one_is_set() -> void:
	# The same vectors as tools/tests/test_devtools_cli.py.
	assert_str(Mailbox.folder_name("")).is_equal("devtools")
	assert_str(Mailbox.folder_name(" feature/one?two ")).is_equal("devtools_feature_one_two")
	assert_str(Mailbox.folder_name("agent-2")).is_equal("devtools_agent-2")


func test_a_session_has_one_folder_with_commands_results_and_log() -> void:
	var root: String = OS.get_user_data_dir().path_join("devtools_" + _session)
	assert_str(_mailbox.root_path).is_equal(root)
	assert_str(_mailbox.command_file("abc")).is_equal(root.path_join("commands/abc.json"))
	assert_str(_mailbox.result_file("abc")).is_equal(root.path_join("results/abc.json"))
	assert_str(_mailbox.log_path).is_equal(root.path_join("log.jsonl"))
	assert_bool(DirAccess.dir_exists_absolute(_mailbox.commands_path)).is_true()
	assert_bool(DirAccess.dir_exists_absolute(_mailbox.results_path)).is_true()


func test_command_ids_are_plain_file_names() -> void:
	for id: String in ["abc", "0123456789abcdef0123456789abcdef", "A_b-9", "x".repeat(64)]:
		assert_bool(Mailbox.is_valid_id(id)).is_true()
	for id: String in ["", "a.b", "../up", "a/b", "a b", "naïve", "x".repeat(65)]:
		assert_bool(Mailbox.is_valid_id(id)).is_false()


func test_poll_returns_a_command_once_with_its_file_name_as_id() -> void:
	assert_array(_mailbox.poll_commands()).is_empty()
	# The id inside the file does not count: the reply goes to the file of the same name.
	_write(_mailbox.command_file("abc"), '{"action": "ping", "args": {}, "id": "zzz"}')
	var commands: Array[Dictionary] = _mailbox.poll_commands()
	assert_int(commands.size()).is_equal(1)
	assert_str(str(commands[0].get("action"))).is_equal("ping")
	assert_str(str(commands[0].get("id"))).is_equal("abc")
	assert_bool(FileAccess.file_exists(_mailbox.command_file("abc"))).is_false()
	assert_array(_mailbox.poll_commands()).is_empty()


## Two clients wrote before one poll: both commands are taken, and each reply gets its own
## file, so neither client can read or remove the other's.
func test_commands_of_several_clients_are_all_taken_and_answered_apart() -> void:
	_write(_mailbox.command_file("b2"), '{"action": "scene_tree"}')
	_write(_mailbox.command_file("a1"), '{"action": "ping"}')
	# A client's temporary file, before it is renamed into place.
	_write(_mailbox.commands_path.path_join(".c3.json.x1.tmp"), '{"action": "quit"}')
	var commands: Array[Dictionary] = _mailbox.poll_commands()
	assert_int(commands.size()).is_equal(2)
	assert_str(str(commands[0]["id"])).is_equal("a1")
	assert_str(str(commands[1]["id"])).is_equal("b2")
	_mailbox.post_result("b2", '{"n": 2}')
	_mailbox.post_result("a1", '{"n": 1}')
	assert_str(_text(_mailbox.result_file("a1"))).is_equal('{"n": 1}')
	assert_str(_text(_mailbox.result_file("b2"))).is_equal('{"n": 2}')
	# Only whole replies are ever visible: no temporary file is left.
	assert_int(DirAccess.get_files_at(_mailbox.results_path).size()).is_equal(2)


func test_half_written_command_is_retried_then_read_whole() -> void:
	_write(_mailbox.command_file("half"), '{"action": "pi')
	_write(_mailbox.command_file("whole"), '{"action": "quit"}')
	var first: Array[Dictionary] = _mailbox.poll_commands()
	# The whole command is not held up by the half-written one.
	assert_int(first.size()).is_equal(1)
	assert_str(str(first[0]["id"])).is_equal("whole")
	assert_bool(FileAccess.file_exists(_mailbox.command_file("half"))).is_true()
	assert_int(_mailbox.bad_polls("half")).is_equal(1)
	_write(_mailbox.command_file("half"), '{"action": "ping"}')
	var second: Array[Dictionary] = _mailbox.poll_commands()
	assert_str(str(second[0].get("action"))).is_equal("ping")
	assert_int(_mailbox.bad_polls("half")).is_zero()


func test_command_that_never_parses_is_discarded_with_a_log_row() -> void:
	_write(_mailbox.command_file("list"), "[1, 2]")
	for poll: int in range(Mailbox.BAD_POLLS_MAX - 1):
		assert_array(_mailbox.poll_commands()).is_empty()
	assert_bool(FileAccess.file_exists(_mailbox.command_file("list"))).is_true()
	assert_array(_mailbox.poll_commands()).is_empty()
	assert_bool(FileAccess.file_exists(_mailbox.command_file("list"))).is_false()
	var lines: PackedStringArray = _log_lines()
	assert_int(lines.size()).is_equal(1)
	assert_str(lines[0]).contains('"category":"error"').contains("not a JSON object: list.json")


func test_a_command_file_with_an_unsafe_name_is_discarded_at_once() -> void:
	var path: String = _mailbox.commands_path.path_join("not an id.json")
	_write(path, '{"action": "ping"}')
	assert_array(_mailbox.poll_commands()).is_empty()
	assert_bool(FileAccess.file_exists(path)).is_false()
	assert_str(_log_lines()[0]).contains("not a valid id: not an id.json")


## The thread did not start (or was never started): main polls and writes at once.
func test_without_its_thread_the_mailbox_works_on_main() -> void:
	assert_bool(_mailbox.running()).is_false()
	_write(_mailbox.command_file("abc"), '{"action": "ping"}')
	var taken: Array[Dictionary] = _mailbox.take_commands()
	assert_int(taken.size()).is_equal(1)
	assert_bool(FileAccess.file_exists(_mailbox.command_file("abc"))).is_false()
	_mailbox.post_result("abc", '{"n": 1}')
	_mailbox.post_log(Mailbox.log_row("result", "a", null), "")
	assert_str(_text(_mailbox.result_file("abc"))).is_equal('{"n": 1}')
	assert_int(_log_lines().size()).is_equal(1)
	assert_int(_mailbox.written_off_main()).is_zero()


func test_thread_takes_a_command_and_writes_in_order_off_main() -> void:
	_mailbox.start()
	assert_bool(_mailbox.running()).is_true()
	_write(_mailbox.command_file("abc"), '{"action": "ping"}')
	_mailbox.post_log(Mailbox.log_row("command", "a", null), "")
	_mailbox.post_result("abc", '{"n": 1}')
	_mailbox.post_log(Mailbox.log_row("result", "b", null), "")
	var taken: Array[Dictionary] = []
	var deadline: int = Time.get_ticks_msec() + THREAD_WAIT_MSEC  # wallclock-ok: bounds a thread
	while (
		taken.is_empty()
		or _log_lines().size() < 2
		or _text(_mailbox.result_file("abc")) != '{"n": 1}'
	):
		if Time.get_ticks_msec() >= deadline:  # wallclock-ok: bounds a thread
			break
		taken.append_array(_mailbox.take_commands())
		OS.delay_msec(1)  # wallclock-ok: yields to the mailbox thread
	assert_int(taken.size()).is_equal(1)
	assert_str(str(taken[0].get("action"))).is_equal("ping")
	assert_bool(FileAccess.file_exists(_mailbox.command_file("abc"))).is_false()
	assert_int(_mailbox.written_off_main()).is_greater(0)
	assert_str(_text(_mailbox.result_file("abc"))).is_equal('{"n": 1}')
	var lines: PackedStringArray = _log_lines()
	assert_int(lines.size()).is_equal(2)
	assert_str(lines[0]).contains('"message":"a"')
	assert_str(lines[1]).contains('"message":"b"')
	_mailbox.stop()
	assert_bool(_mailbox.running()).is_false()


func test_stop_writes_what_was_posted_last() -> void:
	_mailbox.start()
	_mailbox.post_result("last", '{"last": true}')
	_mailbox.stop()
	assert_str(_text(_mailbox.result_file("last"))).is_equal('{"last": true}')


func test_log_appends_after_another_writer() -> void:
	_mailbox.post_log(Mailbox.log_row("command", "a", null), "")
	var other: FileAccess = FileAccess.open(_mailbox.log_path, FileAccess.READ_WRITE)
	other.seek_end()
	other.store_line("other")
	other.close()
	_mailbox.post_log(Mailbox.log_row("result", "b", null), "")
	var lines: PackedStringArray = _log_lines()
	assert_int(lines.size()).is_equal(3)
	assert_str(lines[1]).is_equal("other")
	assert_str(lines[2]).contains('"message":"b"')


func test_clear_stale_files_drops_commands_and_results_and_keeps_the_log() -> void:
	_write(_mailbox.command_file("old"), '{"action": "quit"}')
	_write(_mailbox.result_file("old"), "{}")
	_mailbox.post_log(Mailbox.log_row("system", "kept", null), "")
	_mailbox.clear_stale_files()
	assert_int(DirAccess.get_files_at(_mailbox.commands_path).size()).is_zero()
	assert_int(DirAccess.get_files_at(_mailbox.results_path).size()).is_zero()
	assert_int(_log_lines().size()).is_equal(1)


## A client that timed out never collects its reply; the game removes it later.
func test_only_replies_nobody_collected_in_time_are_swept() -> void:
	_mailbox.post_result("abandoned", "{}")
	assert_int(_mailbox.sweep_results()).is_zero()
	assert_array(_mailbox.poll_commands()).is_empty()
	assert_bool(FileAccess.file_exists(_mailbox.result_file("abandoned"))).is_true()
	# As if the reply had been waiting for longer than it is kept.
	assert_int(_mailbox.sweep_results(-5)).is_equal(1)
	assert_bool(FileAccess.file_exists(_mailbox.result_file("abandoned"))).is_false()


func test_a_log_past_its_cap_is_set_aside_and_a_new_one_begins() -> void:
	var rotated: String = _mailbox.root_path.path_join(Mailbox.ROTATED_LOG_NAME)
	assert_bool(_mailbox.rotate_log(10)).is_false()  # no log yet
	_mailbox.post_log(Mailbox.log_row("system", "first run", null), "")
	assert_bool(_mailbox.rotate_log()).is_false()  # far below the real cap
	assert_bool(_mailbox.rotate_log(10)).is_true()
	assert_bool(FileAccess.file_exists(_mailbox.log_path)).is_false()
	assert_str(_text(rotated)).contains("first run")
	_mailbox.post_log(Mailbox.log_row("system", "second run", null), "")
	assert_int(_log_lines().size()).is_equal(1)
	# The log before the last is the only one kept.
	assert_bool(_mailbox.rotate_log(10)).is_true()
	assert_str(_text(rotated)).contains("second run").not_contains("first run")


func test_the_folders_come_back_if_they_are_removed_while_running() -> void:
	DirAccess.remove_absolute(_mailbox.commands_path)
	assert_array(_mailbox.poll_commands()).is_empty()
	assert_bool(DirAccess.dir_exists_absolute(_mailbox.commands_path)).is_true()


func _remove_files() -> void:
	for folder: String in [_mailbox.commands_path, _mailbox.results_path, _mailbox.root_path]:
		for file: String in DirAccess.get_files_at(folder):
			DirAccess.remove_absolute(folder.path_join(file))


func _write(path: String, text: String) -> void:
	var file: FileAccess = FileAccess.open(path, FileAccess.WRITE)
	file.store_string(text)
	file.close()


func _text(path: String) -> String:
	return FileAccess.get_file_as_string(path) if FileAccess.file_exists(path) else ""


func _log_lines() -> PackedStringArray:
	var text: String = _text(_mailbox.log_path).strip_edges()
	return PackedStringArray() if text.is_empty() else text.split("\n")
