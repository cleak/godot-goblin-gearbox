#!/usr/bin/env python3
"""devtools.py - drive a running Godot debug build through the DevTools autoload.

The game (addons/agent_devtools) polls a mailbox folder in its user:// folder: one file per
command, one file per reply, so several clients can talk to one game at once. Start the game,
then talk to it:

    $env:GODOT_AGENT_SESSION = "agent-1"     # the same value for the game and for this CLI
    pwsh ./tools/godot.ps1                    # in another terminal, or as a background job
    uv run python tools/devtools.py wait      # a windowed Godot can detach from its shell:
                                              # poll for it, do not sleep
    uv run python tools/devtools.py ping
    uv run python tools/devtools.py screenshot --filename menu.png
    uv run python tools/devtools.py scene-tree --depth 3
    uv run python tools/devtools.py get-state --node Player
    uv run python tools/devtools.py input list
    uv run python tools/devtools.py input tap interact
    uv run python tools/devtools.py input sequence test/sequences/smoke.json
    uv run python tools/devtools.py performance
    uv run python tools/devtools.py validate-all
    uv run python tools/devtools.py validate-all --strict # warnings fail too
    uv run python tools/devtools.py call board            # a command the game registered
    uv run python tools/devtools.py logs --tail 20
    uv run python tools/devtools.py input clear            # always, before you leave
    uv run python tools/devtools.py quit

Node paths are relative to the current scene ("." is its root) or absolute (/root/Main).

Options (before or after the command):
    --session NAME   mailbox session (default: $GODOT_AGENT_SESSION); one per running game
    --json           print the game's reply as JSON instead of text
Before the command only:
    --project PATH   the Godot project (default: the folder above tools/)

set-state and run-method are refused unless the project setting devtools/allow_mutation is
true. Screenshots land in <user data>/screenshots/, or with a session in
<user data>/screenshots/<session>/; every capture prints its path and `ping` the folder.

Files, in <user data>/devtools/ (devtools_<session>/ with a session):
    commands/<id>.json   written here, removed by the game once read
    results/<id>.json    the reply to command <id>, removed here once read
    log.jsonl            the game's DevTools log (`logs` reads it)

Exit codes:
    0  the command succeeded
    1  the game answered and the command failed (assert failed, node missing, refused)
    2  bad usage (arguments, an unreadable file, no project.godot)
    3  no answer in time (game not running, another session, or not a debug build)

Results go to stdout, diagnostics to stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = PROJECT_ROOT
SESSION_ENV = "GODOT_AGENT_SESSION"
MAILBOX_BASENAME = "devtools"
COMMANDS_FOLDER = "commands"
RESULTS_FOLDER = "results"
LOG_NAME = "log.jsonl"
LOG_BLOCK_BYTES = 64 * 1024
UNNAMED_PROJECT = "[unnamed project]"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_RESPONSE = 3

DEFAULT_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.05
WAIT_ATTEMPT_SECONDS = 2.0
SEQUENCE_POLL_INTERVAL_SECONDS = 0.1
SEQUENCE_STATUS_RUNNING = "running"
SEQUENCE_TERMINAL_STATUSES = frozenset({"passed", "failed", "timed_out"})

_active_session = os.environ.get(SESSION_ENV, "")


class UsageError(Exception):
    """The request cannot be sent as given (exit 2)."""


class NoResponseError(TimeoutError):
    """The game did not answer in time (exit 3)."""


def sanitize_session_name(raw_session: str) -> str:
    """The file-name-safe session the autoload derives (dev_tools_mailbox.gd)."""
    sanitized = "".join(
        character if character.isascii() and (character.isalnum() or character in "_-.") else "_"
        for character in raw_session.strip()
    )
    return sanitized.strip(".")


def mailbox_folder_name(session: str) -> str:
    """The mailbox folder: devtools, or devtools_<session> when a session is set."""
    safe_session = sanitize_session_name(session)
    return f"{MAILBOX_BASENAME}_{safe_session}" if safe_session else MAILBOX_BASENAME


def get_mailbox_paths(user_data: Path, session: str | None = None) -> dict[str, Path]:
    """One session's mailbox: its folder, the command and result folders, the log file."""
    active = _active_session if session is None else session
    root = user_data / mailbox_folder_name(active)
    return {
        "root": root,
        "commands": root / COMMANDS_FOLDER,
        "results": root / RESULTS_FOLDER,
        "log": root / LOG_NAME,
    }


def new_command_id() -> str:
    """A file-name-safe id no other client has; ids sort in the order they were made."""
    return f"{time.time_ns():016x}{uuid.uuid4().hex[:16]}"


def read_application_settings(project_file: Path) -> dict[str, str]:
    """The [application] section of project.godot, string values unquoted.

    A quoted value may run over several lines (a description): the lines inside it are
    not headers or entries, whatever they look like. Of such a value only the first line
    is kept.
    """
    settings: dict[str, str] = {}
    section = ""
    inside_string = False
    for line in project_file.read_text(encoding="utf-8").splitlines():
        continues = inside_string
        if line.replace('\\"', "").count('"') % 2:
            inside_string = not inside_string
        if continues:
            continue
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif section == "application" and "=" in stripped and not stripped.startswith(";"):
            key, raw = stripped.split("=", 1)
            value = raw.strip()
            if value.startswith('"'):
                try:
                    value = str(json.loads(value))
                except json.JSONDecodeError:
                    value = value.strip('"')
            settings[key.strip()] = value
    return settings


def safe_dir_name(name: str) -> str:
    """Godot's OS.get_safe_dir_name: characters a folder name cannot hold become "-"."""
    safe = name.strip()
    for invalid in ':*?"<>|/\\':
        safe = safe.replace(invalid, "-")
    return safe


def safe_dir_path(name: str) -> str:
    """Godot's OS.get_safe_dir_name for a name that may hold folders (custom_user_dir_name):
    slashes stay, and ".." becomes "-" like the other characters, so the folder never
    climbs out of the data folder."""
    safe = name.replace("\\", "/").replace("//", "/").strip()
    for invalid in (*':*?"<>|', ".."):
        safe = safe.replace(invalid, "-")
    return safe.rstrip(".").strip("/")


def get_user_data_path(project_path: Path) -> Path:
    """The folder Godot uses as user:// for the project at `project_path`."""
    project_file = project_path / "project.godot"
    if not project_file.is_file():
        raise UsageError(f"no project.godot in {project_path} (use --project)")
    settings = read_application_settings(project_file)
    name = safe_dir_name(settings.get("config/name", "")) or UNNAMED_PROJECT

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is not set; cannot locate Godot user data")
        data_root, godot_dir = Path(appdata), "Godot"
    elif sys.platform == "darwin":
        data_root, godot_dir = Path.home() / "Library" / "Application Support", "Godot"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        data_root = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
        godot_dir = "godot"

    if settings.get("config/use_custom_user_dir") == "true":
        custom = safe_dir_path(settings.get("config/custom_user_dir_name", ""))
        return data_root / (custom or name)
    return data_root / godot_dir / "app_userdata" / name


def atomic_write_json(path: Path, payload: dict) -> None:
    """Publish JSON with a same-folder rename, so the game never reads half a command."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary)
            temporary_path = Path(temporary.name)
        for attempt in range(20):
            try:
                os.replace(temporary_path, path)
                break
            except PermissionError:
                # Windows: a scanner or indexer can hold a new file open for a moment.
                if attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()


def take_result(result_path: Path) -> dict | None:
    """The reply in a result file, removing it; None while it is absent or half-written."""
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    with suppress(OSError):
        result_path.unlink()
    return result if isinstance(result, dict) else None


def send_command(
    project_path: Path,
    action: str,
    args: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    session: str | None = None,
) -> dict:
    """Send one command to the running game and return its reply.

    The reply is {"action", "id", "success", "message", "data"}, plus "errors" when the
    engine logged any while the command ran. Raises NoResponseError when none arrives within
    `timeout` seconds.

    The command and its reply are files of their own, named by a fresh id, so clients that
    send at the same time never read each other's replies.
    """
    paths = get_mailbox_paths(get_user_data_path(project_path), session)
    paths["commands"].mkdir(parents=True, exist_ok=True)
    paths["results"].mkdir(parents=True, exist_ok=True)
    command_id = new_command_id()
    command_path = paths["commands"] / f"{command_id}.json"
    result_path = paths["results"] / f"{command_id}.json"
    atomic_write_json(command_path, {"action": action, "args": args or {}, "id": command_id})

    deadline = time.monotonic() + timeout
    while True:
        result = take_result(result_path)
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    with suppress(OSError):
        command_path.unlink()  # do not let a game that starts later run a stale command
    raise NoResponseError(
        f"no reply to '{action}' after {timeout:g}s. Is the game running as a debug build "
        f"with the DevTools autoload, and with {SESSION_ENV}={_describe_session(session)}?"
    )


def _describe_session(session: str | None) -> str:
    active = _active_session if session is None else session
    return repr(sanitize_session_name(active)) if active else "unset"


def finish(result: dict, args: argparse.Namespace, show: Callable[[dict], None]) -> int:
    """Print a reply (as JSON with --json, else with `show`) and return the exit code."""
    succeeded = bool(result.get("success"))
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return EXIT_OK if succeeded else EXIT_FAILED
    if succeeded:
        show(result)
    else:
        print(f"Failed: {result.get('message', 'unknown error')}", file=sys.stderr)
    print_engine_errors(result)
    return EXIT_OK if succeeded else EXIT_FAILED


def print_engine_errors(result: dict) -> None:
    """The errors the engine logged while the command ran, to stderr."""
    errors = result.get("errors") or []
    if errors:
        print(f"Engine errors while the command ran ({len(errors)}):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)


def print_error_count(data: dict) -> None:
    """A note to stderr when the game has logged errors since it started."""
    count = data.get("errors") or 0
    if count:
        print(
            f"Warning: the game has logged {count} error(s) since it started; "
            "see `logs --category error`",
            file=sys.stderr,
        )


def print_data(result: dict) -> None:
    print(json.dumps(result.get("data"), indent=2))


def describe_game(data: dict) -> str:
    """One line that tells one running game from another."""
    session = data.get("session") or "none"
    mutation = "on" if data.get("mutation_enabled") else "off"
    return (
        f"{data.get('project', '?')} (pid {data.get('pid', '?')}, session {session}, "
        f"Godot {data.get('godot', '?')}, mutation {mutation})"
    )


def cmd_ping(args: argparse.Namespace, project_path: Path) -> int:
    """Check that the game answers."""
    result = send_command(project_path, "ping", timeout=5.0)

    def show(reply: dict) -> None:
        print(f"DevTools is running: {describe_game(reply['data'])}")
        print(f"User data: {reply['data'].get('user_dir', '?')}")
        if reply["data"].get("screenshots"):
            print(f"Screenshots: {reply['data']['screenshots']}")
        print_error_count(reply["data"])

    return finish(result, args, show)


def cmd_wait(args: argparse.Namespace, project_path: Path) -> int:
    """Poll until the game answers a ping."""
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise UsageError("--timeout must be a positive number")
    deadline = time.monotonic() + args.timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NoResponseError(
                f"the game did not answer within {args.timeout:g}s "
                f"({SESSION_ENV}={_describe_session(None)})"
            )
        try:
            result = send_command(
                project_path, "ping", timeout=min(WAIT_ATTEMPT_SECONDS, remaining)
            )
        except NoResponseError:
            continue

        def show(reply: dict) -> None:
            print(f"DevTools is ready: {describe_game(reply['data'])}")
            print_error_count(reply["data"])

        return finish(result, args, show)


def cmd_screenshot(args: argparse.Namespace, project_path: Path) -> int:
    """Capture the game viewport to a PNG."""
    command_args = {"filename": args.filename} if args.filename else {}
    result = send_command(project_path, "screenshot", command_args)

    def show(reply: dict) -> None:
        size = reply["data"]["size"]
        print(f"Screenshot saved: {reply['data']['path']}")
        print(f"Size: {size['width']}x{size['height']}")

    return finish(result, args, show)


def cmd_validate(args: argparse.Namespace, project_path: Path) -> int:
    """Validate one scene."""
    result = send_command(
        project_path, "validate_scene", {"path": args.scene, "strict": args.strict}
    )
    code = finish_validation(result, args)
    warn_of_logged_errors(args, project_path)
    return code


def cmd_validate_all(args: argparse.Namespace, project_path: Path) -> int:
    """Validate every scene of the project."""
    result = send_command(
        project_path, "validate_all_scenes", {"strict": args.strict}, timeout=120.0
    )
    code = finish_validation(result, args)
    warn_of_logged_errors(args, project_path)
    return code


def warn_of_logged_errors(args: argparse.Namespace, project_path: Path) -> None:
    """After a validation, the note `ping` gives when the game has logged errors.

    A scene is valid when its own scripts attach. A script it only uses (by class name)
    may not compile and the scene still passes: the game's error count is where that shows.
    """
    if getattr(args, "json", False):
        return
    try:
        game = send_command(project_path, "ping", timeout=5.0)
    except NoResponseError:
        return
    data = game.get("data")
    if isinstance(data, dict):
        print_error_count(data)


def finish_validation(result: dict, args: argparse.Namespace) -> int:
    """Print a validation reply with its issues; failures go to stdout with the rest.

    Errors fail the validation; warnings only with --strict, and are printed either way.
    """
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return EXIT_OK if result.get("success") else EXIT_FAILED
    data = result.get("data") or {}
    print(("[OK] " if result.get("success") else "[FAIL] ") + str(result.get("message", "")))
    by_scene = data.get("issues") or {}
    labels = {"error": "ERROR", "warning": "WARN", "info": "INFO"}
    for scene, scene_issues in by_scene.items():
        if scene_issues:
            print(f"\n{scene}:")
        for issue in scene_issues:
            label = labels.get(issue.get("severity"), "???")
            print(f"  [{label}] {issue.get('code')}: {issue.get('message')}")
    print_engine_errors(result)
    return EXIT_OK if result.get("success") else EXIT_FAILED


def cmd_scene_tree(args: argparse.Namespace, project_path: Path) -> int:
    """Print the current scene's node tree."""
    result = send_command(project_path, "scene_tree", {"depth": args.depth})
    return finish(result, args, print_data)


def cmd_performance(args: argparse.Namespace, project_path: Path) -> int:
    """Print frame, memory and object counters."""
    result = send_command(project_path, "performance")

    def show(reply: dict) -> None:
        data = reply["data"]
        rows = (
            ("FPS", "fps", "{:.1f}"),
            ("Frame time", "frame_time_ms", "{:.2f} ms"),
            ("Process", "process_ms", "{:.2f} ms"),
            ("Physics process", "physics_process_ms", "{:.2f} ms"),
            ("Render CPU", "render_cpu_ms", "{:.2f} ms"),
            ("Render GPU", "render_gpu_ms", "{:.2f} ms"),
            ("Physics ticks/s", "physics_fps", "{:.0f}"),
            ("Max FPS", "max_fps", "{:.0f}"),
            ("VSync mode", "vsync_mode", "{:.0f}"),
            ("Draw calls", "draw_calls", "{:.0f}"),
            ("Static memory", "static_memory_mb", "{:.1f} MB"),
            ("Video memory", "video_memory_mb", "{:.1f} MB"),
            ("Objects", "objects", "{:.0f}"),
            ("Nodes", "nodes", "{:.0f}"),
            ("Orphan nodes", "orphan_nodes", "{:.0f}"),
            ("Physics 2D active", "physics_2d_active_objects", "{:.0f}"),
            ("Physics 3D active", "physics_3d_active_objects", "{:.0f}"),
        )
        for label, key, pattern in rows:
            if isinstance(data.get(key), int | float):
                print(f"{label + ':':<19}{pattern.format(data[key])}")

    return finish(result, args, show)


def cmd_get_state(args: argparse.Namespace, project_path: Path) -> int:
    """Print a node's properties."""
    result = send_command(project_path, "get_state", {"node_path": args.node or ""})
    return finish(result, args, print_data)


def parse_value(raw: str) -> object:
    """A command-line value: JSON when it parses (5, true, [1, 2], {"x": 1}), else text."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def cmd_set_state(args: argparse.Namespace, project_path: Path) -> int:
    """Set a node property (needs devtools/allow_mutation)."""
    result = send_command(
        project_path,
        "set_state",
        {"node_path": args.node, "property": args.property, "value": parse_value(args.value)},
    )

    def show(reply: dict) -> None:
        value = json.dumps((reply.get("data") or {}).get("value"))
        # Named the way a NodePath names a property: "Player:position".
        print(f"{args.node}:{args.property} = {value}")

    return finish(result, args, show)


def cmd_run_method(args: argparse.Namespace, project_path: Path) -> int:
    """Call a method on a node (needs devtools/allow_mutation)."""
    method_args: object = []
    if args.args:
        try:
            method_args = json.loads(args.args)
        except json.JSONDecodeError as error:
            raise UsageError(f"--args is not valid JSON: {error}") from error
        if not isinstance(method_args, list):
            raise UsageError("--args must be a JSON array, for example '[25, \"name\"]'")
    result = send_command(
        project_path,
        "run_method",
        {"node_path": args.node, "method": args.method, "args": method_args},
    )
    return finish(
        result,
        args,
        lambda reply: print(f"Result: {json.dumps((reply.get('data') or {}).get('result'))}"),
    )


def parse_pairs(pairs: list[str]) -> dict:
    """key=value arguments as a dict; values are JSON when they parse, else text."""
    parsed: dict = {}
    for pair in pairs:
        key, separator, raw = pair.partition("=")
        if not separator or not key:
            raise UsageError(f"expected key=value, got {pair!r}")
        parsed[key] = parse_value(raw)
    return parsed


def cmd_call(args: argparse.Namespace, project_path: Path) -> int:
    """Send any command by name: one the game registered, or a built-in."""
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise UsageError("--timeout must be a positive number")
    result = send_command(project_path, args.action, parse_pairs(args.pairs), timeout=args.timeout)

    def show(reply: dict) -> None:
        if reply.get("data") is not None:
            print_data(reply)
        if reply.get("message"):
            print(reply["message"])

    return finish(result, args, show)


def lines_from_end(path: Path) -> Iterator[str]:
    """The non-empty lines of a text file, last line first, read a block at a time."""
    with path.open("rb") as file:
        position = file.seek(0, os.SEEK_END)
        rest = b""
        while position > 0:
            size = min(LOG_BLOCK_BYTES, position)
            position -= size
            file.seek(position)
            # The first piece may be the end of a line that starts in the block before.
            rest, *lines = (file.read(size) + rest).split(b"\n")
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", errors="replace").rstrip("\r")
        if rest.strip():
            yield rest.decode("utf-8", errors="replace").rstrip("\r")


def read_log(
    log_path: Path, tail: int | None = None, category: str | None = None
) -> list[tuple[str, dict | None]]:
    """Log rows as (line, parsed row or None), oldest first.

    `tail` keeps the last rows and `category` the rows of one category; with a tail only the
    end of the file is read, however long the log has grown.
    """
    rows: list[tuple[str, dict | None]] = []
    for line in lines_from_end(log_path):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            parsed = None
        row = parsed if isinstance(parsed, dict) else None
        if category and (row is None or row.get("category") != category):
            continue
        rows.append((line, row))
        if tail is not None and len(rows) >= tail:
            break
    rows.reverse()
    return rows


def cmd_logs(args: argparse.Namespace, project_path: Path) -> int:
    """Print the DevTools log of this session (works without the game running)."""
    if args.tail is not None and args.tail < 1:
        raise UsageError("--tail must be 1 or more")
    log_path = get_mailbox_paths(get_user_data_path(project_path))["log"]
    if not log_path.exists():
        print(f"No log at {log_path}", file=sys.stderr)
        return EXIT_FAILED

    rows = read_log(log_path, args.tail, args.category)
    for line, entry in rows:
        if entry is None or getattr(args, "json", False):
            print(line)
            continue
        stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("timestamp", 0)))
        print(f"[{stamp}] [{entry.get('category')}] {entry.get('message')}")
    if not rows:
        # Nothing printed must not read as "nothing happened": say what was looked at.
        what = f"'{args.category}' rows" if args.category else "rows"
        print(f"No {what} in {log_path}", file=sys.stderr)
    return EXIT_OK


def cmd_quit(args: argparse.Namespace, project_path: Path) -> int:
    """Ask the game to quit."""
    result = send_command(project_path, "quit", {"exit_code": args.exit_code}, timeout=5.0)

    def show(reply: dict) -> None:
        pid = (reply.get("data") or {}).get("pid", "?")
        print(f"Quit accepted; the game (pid {pid}) is closing")

    return finish(result, args, show)


def check_strength(strength: float | None) -> None:
    if strength is not None and (not math.isfinite(strength) or not 0.0 <= strength <= 1.0):
        raise UsageError("--strength must be between 0.0 and 1.0")


def print_active_inputs(reply: dict) -> None:
    active = (reply.get("data") or {}).get("active_inputs")
    if active:
        print(f"Active inputs: {', '.join(active)}")


def cmd_input_press(args: argparse.Namespace, project_path: Path) -> int:
    """Press an action and keep it held."""
    check_strength(args.strength)
    command_args: dict = {"action": args.action}
    if args.strength is not None:
        command_args["strength"] = args.strength
    result = send_command(project_path, "input_press", command_args)

    def show(reply: dict) -> None:
        print(f"Pressed: {args.action}")
        print_active_inputs(reply)

    return finish(result, args, show)


def cmd_input_release(args: argparse.Namespace, project_path: Path) -> int:
    """Release a held action."""
    result = send_command(project_path, "input_release", {"action": args.action})

    def show(reply: dict) -> None:
        print(f"Released: {args.action}")
        print_active_inputs(reply)

    return finish(result, args, show)


def cmd_input_tap(args: argparse.Namespace, project_path: Path) -> int:
    """Press an action, then release it."""
    if not math.isfinite(args.hold) or args.hold < 0.0:
        raise UsageError("--hold must be zero or a positive number")
    check_strength(args.strength)
    command_args: dict = {"action": args.action}
    if args.hold:
        command_args["hold_seconds"] = args.hold
    if args.strength is not None:
        command_args["strength"] = args.strength
    result = send_command(project_path, "input_tap", command_args)
    hold_note = f" (hold: {args.hold:g}s)" if args.hold else ""
    return finish(result, args, lambda _reply: print(f"Tapped: {args.action}{hold_note}"))


def cmd_input_clear(args: argparse.Namespace, project_path: Path) -> int:
    """Release every action DevTools holds."""
    result = send_command(project_path, "input_clear")

    def show(reply: dict) -> None:
        cleared = (reply.get("data") or {}).get("cleared_actions") or []
        if cleared:
            print(f"Cleared {len(cleared)} inputs: {', '.join(cleared)}")
        else:
            print("No active inputs to clear")

    return finish(result, args, show)


def cmd_input_list(args: argparse.Namespace, project_path: Path) -> int:
    """List the project's input actions."""
    result = send_command(project_path, "input_actions", {"include_builtin": args.all})

    def show(reply: dict) -> None:
        actions = (reply.get("data") or {}).get("actions") or []
        print(f"Actions ({len(actions)}):")
        for action in actions:
            pressed = " [PRESSED]" if action.get("is_pressed") else ""
            events = ", ".join(action.get("events", [])) or "(no events)"
            print(f"  {action['name']}{pressed}: {events}")

    return finish(result, args, show)


def load_sequence(path: Path) -> dict:
    """A sequence file: {"description"?, "steps": [...]}."""
    try:
        sequence = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise UsageError(f"cannot read sequence file: {error}") from error
    except json.JSONDecodeError as error:
        raise UsageError(f"sequence file is not valid JSON: {error}") from error
    if not isinstance(sequence, dict):
        raise UsageError("the sequence root must be a JSON object")
    steps = sequence.get("steps")
    if not isinstance(steps, list) or not steps:
        raise UsageError("the sequence needs a non-empty 'steps' array")
    return sequence


def wait_for_sequence_result(project_path: Path, sequence_id: str, timeout: float) -> dict:
    """Poll a started sequence until the game reports how it ended."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NoResponseError(f"sequence {sequence_id} did not finish within {timeout:g}s")
        result = send_command(
            project_path,
            "input_sequence_status",
            {"sequence_id": sequence_id},
            timeout=max(0.1, min(5.0, remaining)),
        )
        data = result.get("data")
        if not isinstance(data, dict) or data.get("sequence_id") != sequence_id:
            raise RuntimeError(f"invalid status reply for sequence {sequence_id}")
        status = data.get("status")
        if status in SEQUENCE_TERMINAL_STATUSES:
            return result
        if status != SEQUENCE_STATUS_RUNNING:
            raise RuntimeError(f"unknown status for sequence {sequence_id}: {status!r}")
        time.sleep(SEQUENCE_POLL_INTERVAL_SECONDS)


def clear_inputs_after_sequence_failure(project_path: Path) -> None:
    """Release held inputs after a failed sequence; never hides the failure itself."""
    try:
        result = send_command(project_path, "input_clear", timeout=5.0)
    except Exception as error:  # cleanup must not replace the real failure
        print(f"Warning: could not clear simulated inputs: {error}", file=sys.stderr)
        return
    if not result.get("success", False):
        message = result.get("message", "unknown error")
        print(f"Warning: could not clear simulated inputs: {message}", file=sys.stderr)


def sequence_screenshots(outcome: dict) -> list[str]:
    """The files the `screenshot` steps of a finished sequence wrote, in step order."""
    paths: list[str] = []
    for row in outcome.get("results") or []:
        if not isinstance(row, dict) or row.get("type") != "screenshot":
            continue
        result = row.get("result")
        if isinstance(result, dict) and result.get("path"):
            paths.append(str(result["path"]))
    return paths


def cmd_input_sequence(args: argparse.Namespace, project_path: Path) -> int:
    """Run an input sequence from a JSON file and wait for its result."""
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise UsageError("--timeout must be a positive number")
    sequence = load_sequence(Path(args.file))
    steps = sequence["steps"]
    as_json = getattr(args, "json", False)
    if not as_json:
        if sequence.get("description"):
            print(f"Sequence: {sequence['description']}")
        print(f"Running {len(steps)} steps...")

    try:
        started = send_command(
            project_path, "input_sequence", {"steps": steps, "timeout": args.timeout}
        )
        if not started.get("success", False):
            raise RuntimeError(started.get("message", "the sequence did not start"))
        data = started.get("data")
        sequence_id = data.get("sequence_id") if isinstance(data, dict) else None
        if not isinstance(sequence_id, str) or not sequence_id:
            raise RuntimeError("the game returned no sequence id")
        # The game enforces `timeout`; the margin covers its screenshots and this polling.
        result = wait_for_sequence_result(project_path, sequence_id, timeout=args.timeout + 15.0)
    except NoResponseError:
        clear_inputs_after_sequence_failure(project_path)
        raise
    except RuntimeError as error:
        clear_inputs_after_sequence_failure(project_path)
        print(f"Failed: {error}", file=sys.stderr)
        return EXIT_FAILED

    outcome = result.get("data") or {}
    passed = bool(result.get("success")) and outcome.get("status") == "passed"
    if not passed:
        clear_inputs_after_sequence_failure(project_path)
    if as_json:
        print(json.dumps(result, indent=2))
        return EXIT_OK if passed else EXIT_FAILED
    # Also after a failure: the captures taken before the failing step are there to open.
    for path in sequence_screenshots(outcome):
        print(f"Screenshot saved: {path}")
    if passed:
        print(f"Sequence passed: {outcome.get('completed_steps', 0)}/{len(steps)} steps")
    else:
        print(f"Failed: {result.get('message', 'the sequence failed')}", file=sys.stderr)
    return EXIT_OK if passed else EXIT_FAILED


def add_global_options(parser: argparse.ArgumentParser, top_level: bool = False) -> None:
    """--session and --json: accepted before the command and after it. --project: before
    it only.

    Every command parser carries the first two. There they have no default, so an option
    given before the command is not overwritten by the absence of one after it. --project
    names whose game is driven: an agent's allow rule that fixes the command
    (`devtools.py ping *`) cannot hold it, so a pre-approved call reaches this project's
    game and no other.
    """

    def default(value: object) -> object:
        return value if top_level else argparse.SUPPRESS

    if top_level:
        parser.add_argument(
            "--project", "-p", default=str(DEFAULT_PROJECT), help="Godot project folder"
        )
    parser.add_argument(
        "--session",
        default=default(os.environ.get(SESSION_ENV, "")),
        help=f"mailbox session (default: ${SESSION_ENV})",
    )
    parser.add_argument(
        "--json", action="store_true", default=default(False), help="print the reply as JSON"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive a running Godot debug build through the DevTools autoload.",
        epilog="Exit codes: 0 ok, 1 the command failed, 2 bad usage, 3 no answer from the game.",
    )
    add_global_options(parser, top_level=True)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Callable, help_text: str) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help_text)
        command.set_defaults(func=handler)
        add_global_options(command)
        return command

    add("ping", cmd_ping, "check that the game answers")
    command = add("wait", cmd_wait, "poll until the game answers")
    command.add_argument("--timeout", type=float, default=60.0, help="seconds (default 60)")
    command = add("screenshot", cmd_screenshot, "capture the viewport to a PNG")
    command.add_argument("--filename", "-f", help="file name in <user data>/screenshots/")
    command = add("scene-tree", cmd_scene_tree, "print the scene's node tree")
    command.add_argument("--depth", "-d", type=int, default=10, help="levels (default 10)")
    command = add("get-state", cmd_get_state, "print a node's properties")
    command.add_argument("--node", "-n", help="node path (default: the scene root)")
    command = add("set-state", cmd_set_state, "set a node property (needs allow_mutation)")
    command.add_argument("--node", "-n", required=True, help="node path")
    command.add_argument("--property", required=True, help='property, may be "position:x"')
    command.add_argument("--value", required=True, help='JSON or text, e.g. 5 or {"x":1,"y":2}')
    command = add("run-method", cmd_run_method, "call a node method (needs allow_mutation)")
    command.add_argument("--node", "-n", required=True, help="node path")
    command.add_argument("--method", "-m", required=True, help="method name")
    command.add_argument("--args", "-a", help="arguments as a JSON array")
    add("performance", cmd_performance, "print frame, memory and object counters")
    command = add("validate", cmd_validate, "validate one scene")
    command.add_argument("--scene", "-s", required=True, help="scene path (res://...)")
    command.add_argument("--strict", action="store_true", help="warnings fail too")
    command = add("validate-all", cmd_validate_all, "validate every scene of the project")
    command.add_argument("--strict", action="store_true", help="warnings fail too")
    command = add("call", cmd_call, "send any command, e.g. one the game registered")
    command.add_argument("action", help="command name")
    command.add_argument("pairs", nargs="*", metavar="key=value", help="arguments; JSON values")
    command.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    command = add("logs", cmd_logs, "print the DevTools log")
    command.add_argument("--tail", "-t", type=int, help="only the last N rows")
    command.add_argument("--category", "-c", help="only this category")
    command = add("quit", cmd_quit, "ask the game to quit")
    command.add_argument("--exit-code", type=int, default=0, help="the game's exit code")

    input_parser = commands.add_parser("input", help="simulate input actions")
    add_global_options(input_parser)
    inputs = input_parser.add_subparsers(dest="input_command", required=True)

    def add_input(name: str, handler: Callable, help_text: str) -> argparse.ArgumentParser:
        command = inputs.add_parser(name, help=help_text)
        command.set_defaults(func=handler)
        add_global_options(command)
        return command

    command = add_input("press", cmd_input_press, "press an action and keep it held")
    command.add_argument("action", help="action name")
    command.add_argument("--strength", type=float, help="0.0-1.0 (default 1.0)")
    command = add_input("release", cmd_input_release, "release a held action")
    command.add_argument("action", help="action name")
    command = add_input("tap", cmd_input_tap, "press an action, then release it")
    command.add_argument("action", help="action name")
    command.add_argument("--hold", type=float, default=0.0, help="seconds held (default: a frame)")
    command.add_argument("--strength", type=float, help="0.0-1.0 (default 1.0)")
    add_input("clear", cmd_input_clear, "release every action DevTools holds")
    command = add_input("list", cmd_input_list, "list the project's input actions")
    command.add_argument("--all", "-a", action="store_true", help="include built-in ui_* actions")
    command = add_input("sequence", cmd_input_sequence, "run a sequence from a JSON file")
    command.add_argument("file", help="sequence file (see test/sequences/)")
    command.add_argument(
        "--timeout", type=float, default=60.0, help="seconds the sequence may take (default 60)"
    )
    return parser


def plain_output() -> None:
    """Make printing unable to fail. Piped output on Windows is encoded with a legacy code
    page: a character outside it (a log message, a project name) is written as an escape
    such as `\\u2713`. This file imports nothing of the kit, so the rule of
    tools/ci/gate_support.py is repeated here."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    global _active_session

    plain_output()
    args = build_parser().parse_args(argv)
    _active_session = args.session
    try:
        return args.func(args, Path(args.project).resolve())
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except NoResponseError as error:
        print(f"No response: {error}", file=sys.stderr)
        return EXIT_NO_RESPONSE


if __name__ == "__main__":
    sys.exit(main())
