#!/usr/bin/env python3
"""MCP server for the DevTools autoload: the commands of tools/devtools.py as MCP tools.

Speaks JSON-RPC 2.0 over stdio, one JSON object per line (the MCP stdio transport), with the
standard library only. Register it with an MCP client: copy tools/mcp/.mcp.json.example to
.mcp.json at the project root.

Each tool sends one command to the running game and returns the game's reply as JSON text
and as structured content; `screenshot` adds the image itself. A command that fails, and a
game that does not answer, come back as a tool result with isError set, so the model can read
why; only a malformed request is a protocol error.

One server talks to one game: the one started with the same GODOT_AGENT_SESSION. Start the
game yourself (pwsh ./tools/godot.ps1), then call `wait`. The CLI and this server can talk
to the same game at the same time: every command has its own reply file.

    uv run python tools/mcp/server.py [--project PATH]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import devtools  # noqa: E402 - tools/ is put on the path just above

SERVER_NAME = "godot-devtools"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
PARSE_ERROR = -32700
JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}

NODE = {"type": "string", "description": 'Node path: relative to the scene, or "/root/..."'}
ACTION = {"type": "string", "description": "Input action name (see input_actions)"}
STRENGTH = {"type": "number", "description": "0.0-1.0, default 1.0"}
STRICT = {"type": "boolean", "description": "Warnings fail too, default false"}


class ToolError(ValueError):
    """The request is malformed: a JSON-RPC error, not a tool result."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    #: The DevTools command sent with the arguments as they are; "" for a tool with its own code.
    action: str = ""
    timeout: float = devtools.DEFAULT_TIMEOUT_SECONDS
    changes_game: bool = False

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": not self.changes_game, "openWorldHint": False},
        }


TOOLS: tuple[Tool, ...] = (
    Tool(
        "wait",
        "Poll until the running game answers. Call it after starting the game.",
        {"timeout": {"type": "number", "description": "Seconds, default 60"}},
    ),
    Tool("ping", "Check that the game answers; says which game it is.", action="ping", timeout=5),
    Tool(
        "screenshot",
        "Capture the game viewport. Returns the PNG path and the image.",
        {"filename": {"type": "string", "description": "Basename ending in .png"}},
        action="screenshot",
    ),
    Tool(
        "scene_tree",
        "The current scene's nodes: name, type, path, script, position.",
        {"depth": {"type": "integer", "description": "Levels below the root, default 10"}},
        action="scene_tree",
    ),
    Tool(
        "get_state",
        "A node's script variables and stored properties as JSON.",
        {"node_path": NODE},
        action="get_state",
    ),
    Tool(
        "set_state",
        "Set a node property. Refused unless the project sets devtools/allow_mutation=true.",
        {
            "node_path": NODE,
            "property": {"type": "string", "description": 'May be indexed: "position:x"'},
            "value": {"description": 'JSON; vectors as {"x", "y"}, colors as {"r","g","b","a"}'},
        },
        ("node_path", "property", "value"),
        action="set_state",
        changes_game=True,
    ),
    Tool(
        "run_method",
        "Call a node method. Refused unless the project sets devtools/allow_mutation=true.",
        {"node_path": NODE, "method": {"type": "string"}, "args": {"type": "array"}},
        ("node_path", "method"),
        action="run_method",
        changes_game=True,
    ),
    Tool("performance", "FPS, frame times, memory, draw calls, node counts.", action="performance"),
    Tool(
        "validate_scene",
        "Load and instantiate one scene; report scripts that do not compile and broken "
        "references. Errors fail it; warnings (a sprite with no texture) only with strict.",
        {"path": {"type": "string", "description": "res:// path of the scene"}, "strict": STRICT},
        ("path",),
        action="validate_scene",
    ),
    Tool(
        "validate_all_scenes",
        "Validate every scene of the project.",
        {"strict": STRICT},
        action="validate_all_scenes",
        timeout=120,
    ),
    Tool(
        "input_actions",
        "List the project's input actions and their keys.",
        {"include_builtin": {"type": "boolean", "description": "Also list ui_* actions"}},
        action="input_actions",
    ),
    Tool(
        "input_press",
        "Press an input action and keep it held until input_release or input_clear.",
        {"action": ACTION, "strength": STRENGTH},
        ("action",),
        action="input_press",
        changes_game=True,
    ),
    Tool(
        "input_release",
        "Release a held input action.",
        {"action": ACTION},
        ("action",),
        action="input_release",
        changes_game=True,
    ),
    Tool(
        "input_tap",
        "Press an input action, then release it.",
        {"action": ACTION, "hold_seconds": {"type": "number"}, "strength": STRENGTH},
        ("action",),
        action="input_tap",
        changes_game=True,
    ),
    Tool(
        "input_clear",
        "Release every action DevTools holds. Call it before you leave the game.",
        action="input_clear",
        changes_game=True,
    ),
    Tool(
        "input_sequence",
        "Run scripted steps (press, release, tap, hold, wait, assert, screenshot, clear, call) "
        "and wait for the result. A failed step fails the sequence and releases held inputs. "
        "Steps are documented in addons/agent_devtools/dev_tools_input.gd.",
        {
            "steps": {"type": "array", "items": {"type": "object"}},
            "timeout": {
                "type": "number",
                "description": "Seconds the sequence may take, default 60",
            },
        },
        ("steps",),
        changes_game=True,
    ),
    Tool(
        "call",
        "Send any command by name, for example one the game registered with register_handler.",
        {"action": {"type": "string"}, "args": {"type": "object"}},
        ("action",),
        changes_game=True,
    ),
    Tool(
        "logs",
        "The last rows of the DevTools log.",
        {
            "tail": {"type": "integer", "description": "Rows, default 50"},
            "category": {"type": "string"},
        },
    ),
    Tool(
        "quit",
        "Ask the game to quit.",
        {"exit_code": {"type": "integer"}},
        action="quit",
        timeout=5,
        changes_game=True,
    ),
)
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def check_arguments(tool: Tool, arguments: Any) -> dict[str, Any]:
    """The arguments of a tools/call, checked against the tool's schema."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("arguments must be an object")
    unknown = sorted(set(arguments) - set(tool.properties))
    if unknown:
        raise ToolError(f"{tool.name}: unknown argument(s): {', '.join(unknown)}")
    missing = [name for name in tool.required if name not in arguments]
    if missing:
        raise ToolError(f"{tool.name}: missing argument(s): {', '.join(missing)}")
    for name, value in arguments.items():
        wanted = JSON_TYPES.get(tool.properties[name].get("type", ""))
        is_bool_as_number = isinstance(value, bool) and wanted is not bool
        if wanted is not None and (not isinstance(value, wanted) or is_bool_as_number):
            raise ToolError(f"{tool.name}: '{name}' must be {tool.properties[name]['type']}")
    return arguments


def tool_result(reply: dict, *extra_content: dict) -> dict[str, Any]:
    """A game reply as an MCP tool result."""
    return {
        "content": [{"type": "text", "text": json.dumps(reply, indent=2)}, *extra_content],
        "structuredContent": reply,
        "isError": not reply.get("success", False),
    }


def failure(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def run_wait(project: Path, arguments: dict) -> dict[str, Any]:
    timeout = float(arguments.get("timeout", 60.0))
    try:
        return tool_result(devtools.wait_for_game(project, timeout))
    except devtools.NoResponseError:
        mailbox = devtools.get_mailbox_paths(devtools.get_user_data_path(project))["root"]
        return failure(
            f"The game did not answer within {timeout:g}s. Is it running, with this "
            f"server's {devtools.SESSION_ENV}? Its mailbox would be {mailbox}. "
            "If it was started, its output says why."
        )


def run_sequence(project: Path, arguments: dict) -> dict[str, Any]:
    timeout = float(arguments.get("timeout", 60.0))
    started = devtools.send_command(
        project, "input_sequence", {"steps": arguments["steps"], "timeout": timeout}
    )
    sequence_id = (started.get("data") or {}).get("sequence_id")
    if not started.get("success") or not isinstance(sequence_id, str):
        return tool_result(started)
    try:
        result = devtools.wait_for_sequence_result(project, sequence_id, timeout + 15.0)
    except (devtools.NoResponseError, RuntimeError):
        devtools.clear_inputs_after_sequence_failure(project)
        raise
    if not result.get("success"):
        devtools.clear_inputs_after_sequence_failure(project)
    return tool_result(result)


def run_logs(project: Path, arguments: dict) -> dict[str, Any]:
    log_path = devtools.get_mailbox_paths(devtools.get_user_data_path(project))["log"]
    if not log_path.exists():
        return failure(f"No log at {log_path}")
    tail = max(1, int(arguments.get("tail", 50)))
    rows = devtools.read_log(log_path, tail, arguments.get("category"))
    text = "\n".join(line for line, _row in rows)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def screenshot_image(reply: dict) -> list[dict]:
    """The captured PNG as MCP image content; nothing when the capture failed."""
    path = (reply.get("data") or {}).get("path")
    if not reply.get("success") or not path or not Path(path).is_file():
        return []
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return [{"type": "image", "data": data, "mimeType": "image/png"}]


def call_tool(name: str, raw_arguments: Any, project: Path) -> dict[str, Any]:
    """Run one tool. Raises ToolError for a malformed call; game failures are results."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name}")
    arguments = check_arguments(tool, raw_arguments)
    try:
        if name == "wait":
            return run_wait(project, arguments)
        if name == "input_sequence":
            return run_sequence(project, arguments)
        if name == "logs":
            return run_logs(project, arguments)
        if name == "call":
            action, args = arguments["action"], arguments.get("args", {})
            return tool_result(devtools.send_command(project, action, args))
        reply = devtools.send_command(project, tool.action, arguments, timeout=tool.timeout)
        return tool_result(reply, *(screenshot_image(reply) if name == "screenshot" else []))
    except devtools.NoResponseError as error:
        return failure(f"No response: {error}")
    except (devtools.UsageError, RuntimeError) as error:
        return failure(str(error))


def handle(message: dict[str, Any], project: Path) -> dict[str, Any] | None:
    """One JSON-RPC message; the response, or None for a notification."""
    method = message.get("method")
    request_id = message.get("id")
    result: dict[str, Any]
    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        result = {
            "protocolVersion": requested
            if requested in PROTOCOL_VERSIONS
            else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Drives a running Godot debug build. Start the game first (it must share this "
                "server's GODOT_AGENT_SESSION), then call wait. Call input_clear before you stop."
            ),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [tool.descriptor() for tool in TOOLS]}
    elif method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise ToolError("tools/call needs a tool name")
        result = call_tool(params["name"], params.get("arguments"), project)
    elif request_id is None:
        return None  # a notification (notifications/initialized, cancelled, ...)
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": METHOD_NOT_FOUND, "message": f"method not found: {method}"},
        }
    return None if request_id is None else {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(project: Path, lines: Any = sys.stdin, out: Any = sys.stdout) -> int:
    """Answer JSON-RPC lines until the input ends."""
    for raw_line in lines:
        if not raw_line.strip():
            continue
        request_id: Any = None
        try:
            message = json.loads(raw_line)
            if not isinstance(message, dict):
                raise ToolError("a message must be a JSON object")
            request_id = message.get("id")
            response = handle(message, project)
        except json.JSONDecodeError as error:
            response = _error(None, PARSE_ERROR, f"invalid JSON: {error}")
        except ToolError as error:
            response = _error(request_id, INVALID_PARAMS, str(error))
        if response is not None:
            out.write(json.dumps(response, separators=(",", ":")) + "\n")
            out.flush()
    return 0


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP server for the Godot DevTools autoload.")
    parser.add_argument("--project", default=str(devtools.DEFAULT_PROJECT), help="project folder")
    args = parser.parse_args(argv)
    # MCP is UTF-8 with "\n" line ends, whatever the console's code page is.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    return serve(Path(args.project).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
