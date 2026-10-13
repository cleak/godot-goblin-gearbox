extends RefCounted
## DevTools' file mailbox. A thread polls the command folder, parses and removes each command
## file, and writes results and log lines, so a command costs the main thread its handler and
## no file I/O. The thread never touches the scene tree: main posts entries and takes parsed
## commands.
##
## Files, in user://devtools/ or, with a session, user://devtools_<session>/:
##   commands/<id>.json   one command, written by a client, removed here once read
##   results/<id>.json    the reply to command <id>, written here, removed by the client
##   log.jsonl            one JSON object per line, appended
##   log.1.jsonl          the log as it was before it grew past LOG_BYTES_MAX
##
## One file per command and per reply: several clients can talk to one game at the same time
## (two shells, a shell and the MCP server) and each gets the reply to its own command. The
## id is the file name: ASCII letters, digits, "_" and "-", at most ID_LENGTH_MAX.
##
## Preload it; it declares no class_name.

const ROOT_BASENAME: String = "devtools"
const COMMANDS_FOLDER: String = "commands"
const RESULTS_FOLDER: String = "results"
const LOG_NAME: String = "log.jsonl"
const ROTATED_LOG_NAME: String = "log.1.jsonl"
const COMMAND_EXTENSION: String = "json"
const ID_LENGTH_MAX: int = 64
const POLL_MSEC: int = 100
const IDLE_MSEC: int = 5
## Polls in a row that may fail on one command file before it is discarded. A client that
## does not write atomically is seen half-written for a poll or two; 30 polls is 3 seconds.
const BAD_POLLS_MAX: int = 30
## A reply nobody collected for this long belongs to a client that gave up; it is removed.
const RESULT_KEPT_SECONDS: int = 60
const SWEEP_MSEC: int = 10000
## start() sets a log larger than this aside as log.1.jsonl and begins a new one.
const LOG_BYTES_MAX: int = 5 * 1024 * 1024
const ERROR_CATEGORY: String = "error"
const ERROR_MESSAGE: String = "Failed to process command"


## A log line (appended to the log) or a result (a file of its own).
class Entry:
	extends RefCounted
	## The command id of a result; "" for a log line.
	var result_id: String = ""
	var text: String = ""
	## Printed after the write (the console echo of a log line); "" for none.
	var echo: String = ""


var root_path: String = ""
var commands_path: String = ""
var results_path: String = ""
var log_path: String = ""
## Command file name -> polls in a row that failed on it (the polling thread's own).
var _bad_polls: Dictionary[String, int] = {}
var _next_sweep_msec: int = 0
var _mutex: Mutex = Mutex.new()
var _inbox: Array[Dictionary] = []
var _outbox: Array[Entry] = []
var _stopping: bool = false
var _thread: Thread = null
## Batches written by a thread other than main (under _mutex).
var _written_off_main: int = 0
## While no thread runs, main polls no earlier than this tick.
var _main_poll_msec: int = 0


## `root` is the session's folder; it and its subfolders are created.
func _init(root: String) -> void:
	root_path = root
	commands_path = root.path_join(COMMANDS_FOLDER)
	results_path = root.path_join(RESULTS_FOLDER)
	log_path = root.path_join(LOG_NAME)
	DirAccess.make_dir_recursive_absolute(commands_path)
	DirAccess.make_dir_recursive_absolute(results_path)


## A session name reduced to characters that are safe in a file name on every platform:
## ASCII letters, digits, "_", "-" and "."; anything else becomes "_". No leading or
## trailing dot. tools/devtools.py applies the same rule.
static func sanitize_session(raw_session: String) -> String:
	var sanitized: String = ""
	for character: String in raw_session.strip_edges():
		sanitized += character if _is_safe_character(character) else "_"
	while sanitized.begins_with("."):
		sanitized = sanitized.substr(1)
	while sanitized.ends_with("."):
		sanitized = sanitized.left(-1)
	return sanitized


## The mailbox folder's name: "devtools", or "devtools_<session>" when a session is set.
static func folder_name(raw_session: String) -> String:
	var session: String = sanitize_session(raw_session)
	return ROOT_BASENAME if session.is_empty() else "%s_%s" % [ROOT_BASENAME, session]


## True when `id` can name a command file: letters, digits, "_" and "-" only.
static func is_valid_id(id: String) -> bool:
	if id.is_empty() or id.length() > ID_LENGTH_MAX:
		return false
	for character: String in id:
		if character == "." or not _is_safe_character(character):
			return false
	return true


## The file a client writes command `id` to.
func command_file(id: String) -> String:
	return commands_path.path_join("%s.%s" % [id, COMMAND_EXTENSION])


## The file the reply to command `id` is written to.
func result_file(id: String) -> String:
	return results_path.path_join("%s.%s" % [id, COMMAND_EXTENSION])


## True for ASCII letters, digits, "_", "-" and ".".
static func _is_safe_character(character: String) -> bool:
	var code: int = character.unicode_at(0)
	return (
		(code >= 48 and code <= 57)
		or (code >= 65 and code <= 90)
		or (code >= 97 and code <= 122)
		or character == "_"
		or character == "-"
		or character == "."
	)


## Starts the polling thread (idempotent). If the thread cannot start the mailbox stays on
## main: take_commands polls and posts write at once.
func start() -> void:
	if _thread != null:
		return
	_stopping = false
	_thread = Thread.new()
	var error: Error = _thread.start(_run)
	if error != OK:
		_thread = null
		push_warning(
			"DevTools mailbox thread did not start (%s): polling on main" % error_string(error)
		)


func running() -> bool:
	return _thread != null


## Joins the thread once it has written everything posted, then writes what came after.
func stop() -> void:
	if _thread != null:
		_mutex.lock()
		_stopping = true
		_mutex.unlock()
		_thread.wait_to_finish()
		_thread = null
	write_pending()


## Removes the commands and results an earlier run left, so none is mistaken for a new one.
func clear_stale_files() -> void:
	for folder: String in [commands_path, results_path]:
		for file: String in _files_in(folder):
			DirAccess.remove_absolute(folder.path_join(file))
	_bad_polls.clear()


## Sets the log aside as log.1.jsonl (replacing the one before) when it is larger than
## `max_bytes`, so it cannot grow without bound. True when it did.
func rotate_log(max_bytes: int = LOG_BYTES_MAX) -> bool:
	var file: FileAccess = FileAccess.open(log_path, FileAccess.READ)
	if file == null:
		return false
	var size: int = file.get_length()
	file.close()
	if size <= max_bytes:
		return false
	var rotated: String = root_path.path_join(ROTATED_LOG_NAME)
	if FileAccess.file_exists(rotated):
		DirAccess.remove_absolute(rotated)
	return DirAccess.rename_absolute(log_path, rotated) == OK


## Batches written off the main thread so far.
func written_off_main() -> int:
	_mutex.lock()
	var count: int = _written_off_main
	_mutex.unlock()
	return count


## The commands parsed since the last call, oldest first (main). With no thread, main polls
## the command folder itself, every POLL_MSEC.
func take_commands() -> Array[Dictionary]:
	_mutex.lock()
	var taken: Array[Dictionary] = _inbox
	_inbox = []
	_mutex.unlock()
	if _thread != null:
		return taken
	var now: int = Time.get_ticks_msec()
	if now < _main_poll_msec:
		return taken
	_main_poll_msec = now + POLL_MSEC
	taken.append_array(poll_commands())
	return taken


## Queues a log line (a JSON row) and its console echo; written now when no thread runs.
func post_log(json: String, echo: String) -> void:
	var entry: Entry = Entry.new()
	entry.text = json
	entry.echo = echo
	_post(entry)


## Queues the reply to command `id`; written now when no thread runs.
func post_result(id: String, json: String) -> void:
	assert(is_valid_id(id), "a result needs the id of its command")
	var entry: Entry = Entry.new()
	entry.result_id = id
	entry.text = json
	_post(entry)


## One poll of the command folder: every command that is whole, in file name order. Each
## carries its file's name as "id". A command is returned only once its file is removed, so
## it never runs twice. A file that does not parse stays for BAD_POLLS_MAX polls (it may be
## half-written), then is discarded with a log row.
func poll_commands() -> Array[Dictionary]:
	var files: PackedStringArray = _files_in(commands_path)
	files.sort()
	var taken: Array[Dictionary] = []
	var seen: Dictionary[String, int] = {}
	for file: String in files:
		# A client writes "<id>.json" by renaming a temporary file into place; skip those.
		if file.begins_with(".") or file.get_extension() != COMMAND_EXTENSION:
			continue
		var command: Dictionary = _take_command(file)
		if not command.is_empty():
			taken.append(command)
		elif _bad_polls.has(file):
			seen[file] = _bad_polls[file]
	_bad_polls = seen
	var now: int = Time.get_ticks_msec()
	if now >= _next_sweep_msec:
		_next_sweep_msec = now + SWEEP_MSEC
		sweep_results()
	return taken


## Polls in a row that failed on the command file `id`.
func bad_polls(id: String) -> int:
	return _bad_polls.get("%s.%s" % [id, COMMAND_EXTENSION], 0)


## A log row as JSON (main and the thread write the same shape).
static func log_row(category: String, message: String, data: Variant) -> String:
	var row: Dictionary = {
		"timestamp": Time.get_unix_time_from_system(),
		"frame": Engine.get_process_frames(),
		"category": category,
		"message": message,
		"data": data,
	}
	return JSON.stringify(row)


## The console line that mirrors a log row.
static func echo_of(category: String, message: String) -> String:
	return "[%s] %s" % [category, message]


## Writes every posted entry in order (the thread, or main when none runs). The log is opened,
## appended and closed per batch, so another process on the same session can append too.
func write_pending() -> void:
	_mutex.lock()
	var batch: Array[Entry] = _outbox
	_outbox = []
	_mutex.unlock()
	if batch.is_empty():
		return
	var log_file: FileAccess = null
	for entry: Entry in batch:
		if not entry.result_id.is_empty():
			_write_result(entry.result_id, entry.text)
			continue
		if log_file == null:
			log_file = _open_log()
		if log_file != null:
			log_file.store_line(entry.text)
		if not entry.echo.is_empty():
			print(entry.echo)
	if log_file != null:
		log_file.close()
	if OS.get_thread_caller_id() != OS.get_main_thread_id():
		_mutex.lock()
		_written_off_main += 1
		_mutex.unlock()


## The command in `file`, with its file name as "id"; {} when it is not taken on this poll.
func _take_command(file: String) -> Dictionary:
	var path: String = commands_path.path_join(file)
	var id: String = file.get_basename()
	if not is_valid_id(id):
		DirAccess.remove_absolute(path)
		_post_error("command file name is not a valid id: %s" % file)
		return {}
	var parsed: Variant = null
	var read_file: FileAccess = FileAccess.open(path, FileAccess.READ)
	if read_file != null:
		var json: JSON = JSON.new()
		if json.parse(read_file.get_as_text()) == OK:
			parsed = json.data
		read_file.close()
	if parsed is Dictionary and DirAccess.remove_absolute(path) == OK:
		var command: Dictionary = parsed
		command["id"] = id
		return command
	var failed: int = _bad_polls.get(file, 0)
	failed += 1
	_bad_polls[file] = failed
	if failed < BAD_POLLS_MAX:
		return {}
	_bad_polls.erase(file)
	var reason: String = "command file is not a JSON object"
	if read_file == null:
		reason = "could not open command file"
	elif parsed is Dictionary:
		reason = "could not remove command file"
	DirAccess.remove_absolute(path)
	_post_error("%s: %s" % [reason, file])
	return {}


## Removes the replies nobody collected for `kept_seconds`: their clients gave up. Returns
## how many.
func sweep_results(kept_seconds: int = RESULT_KEPT_SECONDS) -> int:
	var oldest_kept: int = int(Time.get_unix_time_from_system()) - kept_seconds
	var removed: int = 0
	for file: String in _files_in(results_path):
		var path: String = results_path.path_join(file)
		if FileAccess.get_modified_time(path) < oldest_kept:
			DirAccess.remove_absolute(path)
			removed += 1
	return removed


## Every file in `folder`, hidden ones included. On Linux and macOS a name that begins with
## "." is hidden and DirAccess.get_files_at leaves it out; a temporary file has such a name.
static func files_in(folder: String) -> PackedStringArray:
	var directory: DirAccess = DirAccess.open(folder)
	if directory == null:
		return PackedStringArray()
	directory.include_hidden = true
	return directory.get_files()


## The files in `folder`, which is created again if someone removed it while the game runs.
func _files_in(folder: String) -> PackedStringArray:
	if not DirAccess.dir_exists_absolute(folder):
		DirAccess.make_dir_recursive_absolute(folder)
	return files_in(folder)


func _post(entry: Entry) -> void:
	_mutex.lock()
	_outbox.append(entry)
	_mutex.unlock()
	if _thread == null:
		write_pending()


func _post_error(reason: String) -> void:
	post_log(
		log_row(ERROR_CATEGORY, ERROR_MESSAGE, {"error": reason}),
		echo_of(ERROR_CATEGORY, "%s: %s" % [ERROR_MESSAGE, reason])
	)


func _run() -> void:
	var next_poll: int = 0
	while true:
		_mutex.lock()
		var stopping: bool = _stopping
		_mutex.unlock()
		write_pending()
		if stopping:
			return
		var now: int = Time.get_ticks_msec()
		if now >= next_poll:
			next_poll = now + POLL_MSEC
			var commands: Array[Dictionary] = poll_commands()
			if not commands.is_empty():
				_mutex.lock()
				_inbox.append_array(commands)
				_mutex.unlock()
		OS.delay_msec(IDLE_MSEC)


## Writes a reply under a temporary name and renames it into place, so a client never reads
## half of one.
func _write_result(id: String, text: String) -> void:
	var path: String = result_file(id)
	var temporary: String = results_path.path_join(".%s.tmp" % id)
	var file: FileAccess = FileAccess.open(temporary, FileAccess.WRITE)
	if file == null:
		printerr("DevTools: cannot write %s (%s)" % [temporary, FileAccess.get_open_error()])
		return
	file.store_string(text)
	file.close()
	var error: Error = DirAccess.rename_absolute(temporary, path)
	if error != OK:
		printerr("DevTools: cannot write %s (%s)" % [path, error_string(error)])
		DirAccess.remove_absolute(temporary)


func _open_log() -> FileAccess:
	var file: FileAccess = FileAccess.open(log_path, FileAccess.READ_WRITE)
	if file == null:
		file = FileAccess.open(log_path, FileAccess.WRITE)
	if file != null:
		file.seek_end()
	return file
