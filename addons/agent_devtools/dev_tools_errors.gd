extends Logger
## Keeps the errors the engine logs: script errors, push_error, failed loads. A windowed game
## can detach from the shell that started it, so its console is often out of a client's
## reach; DevTools copies these rows into its log, counts them in `ping`, and adds the ones
## logged while a command ran to that command's reply.
##
## Register it with OS.add_logger and remove it with OS.remove_logger. The engine calls
## _log_error from any thread: rows are kept under a mutex, and nothing here logs, prints or
## touches a file. Warnings are not kept.
##
## A row is {"kind": "error" | "script" | "shader", "message", "file", "line", "function"}.
##
## Preload it; it declares no class_name.

const KIND_ERROR: String = "error"
const KIND_SCRIPT: String = "script"
const KIND_SHADER: String = "shader"
## Rows kept; older ones are dropped, the total keeps counting.
const KEPT_MAX: int = 200

var _mutex: Mutex = Mutex.new()
var _rows: Array[Dictionary] = []
var _total: int = 0


func _log_error(
	function: String,
	file: String,
	line: int,
	code: String,
	rationale: String,
	_editor_notify: bool,
	error_type: int,
	_script_backtraces: Array[ScriptBacktrace]
) -> void:
	if error_type == ERROR_TYPE_WARNING:
		return
	var kind: String = KIND_ERROR
	if error_type == ERROR_TYPE_SCRIPT:
		kind = KIND_SCRIPT
	elif error_type == ERROR_TYPE_SHADER:
		kind = KIND_SHADER
	# An engine check logs the failed condition as `code` and its explanation as `rationale`.
	record(kind, code if rationale.is_empty() else rationale, file, line, function)


## Keeps one row. The engine gets here through _log_error.
func record(kind: String, message: String, file: String, line: int, function: String = "") -> void:
	var row: Dictionary = {
		"kind": kind, "message": message, "file": file, "line": line, "function": function
	}
	_mutex.lock()
	_rows.append(row)
	_total += 1
	if _rows.size() > KEPT_MAX:
		_rows.pop_front()
	_mutex.unlock()


## How many errors were logged so far. Use it as a mark for since().
func total() -> int:
	_mutex.lock()
	var count: int = _total
	_mutex.unlock()
	return count


## The rows logged after `mark` (an earlier total()), oldest first; at most KEPT_MAX.
func since(mark: int) -> Array[Dictionary]:
	_mutex.lock()
	var wanted: int = clampi(_total - mark, 0, _rows.size())
	var rows: Array[Dictionary] = _rows.slice(_rows.size() - wanted)
	_mutex.unlock()
	return rows


## The rows of `kind` among `rows`.
static func of_kind(rows: Array[Dictionary], kind: String) -> Array[Dictionary]:
	var matching: Array[Dictionary] = []
	for row: Dictionary in rows:
		if row["kind"] == kind:
			matching.append(row)
	return matching


## One line for a person: "message (file:line)".
static func describe(row: Dictionary) -> String:
	return "%s (%s:%d)" % [row["message"], row["file"], row["line"]]


## describe() of each row.
static func describe_all(rows: Array[Dictionary]) -> PackedStringArray:
	var lines: PackedStringArray = []
	for row: Dictionary in rows:
		lines.append(describe(row))
	return lines
