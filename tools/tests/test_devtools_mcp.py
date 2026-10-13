"""tools/mcp/server.py: the DevTools commands as MCP tools over stdio JSON-RPC."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "tools" / "mcp" / "server.py"
DEV_COMMAND = ROOT / "addons" / "agent_devtools" / "dev_command.gd"
SPEC = importlib.util.spec_from_file_location("kit_devtools_mcp", SERVER_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery invariant
    raise RuntimeError(f"Unable to load {SERVER_PATH}")
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server  # dataclasses look their module up while it is executed
SPEC.loader.exec_module(server)
devtools = server.devtools


def addon_commands() -> set[str]:
    """The command names the addon declares (dev_command.gd)."""
    pattern = re.compile(r'^const \w+: String = "([a-z_]+)"', re.MULTILINE)
    return set(pattern.findall(DEV_COMMAND.read_text("utf-8")))


def request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call(name: str, arguments: dict | None = None) -> dict:
    params: dict = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return server.handle(request("tools/call", params), ROOT)["result"]


class ProtocolTests(unittest.TestCase):
    def test_initialize_echoes_a_supported_version_and_falls_back_to_the_newest(self) -> None:
        known = server.handle(request("initialize", {"protocolVersion": "2024-11-05"}), ROOT)
        unknown = server.handle(request("initialize", {"protocolVersion": "1999-01-01"}), ROOT)
        self.assertEqual(known["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(unknown["result"]["protocolVersion"], server.PROTOCOL_VERSIONS[0])
        self.assertEqual(known["result"]["serverInfo"]["name"], "godot-devtools")
        self.assertIn("tools", known["result"]["capabilities"])

    def test_notifications_get_no_answer_and_unknown_methods_an_error(self) -> None:
        self.assertIsNone(
            server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, ROOT)
        )
        self.assertEqual(server.handle(request("ping"), ROOT)["result"], {})
        unknown = server.handle(request("resources/list"), ROOT)
        self.assertEqual(unknown["error"]["code"], server.METHOD_NOT_FOUND)

    def test_every_tool_is_described_with_a_closed_schema(self) -> None:
        tools = server.handle(request("tools/list"), ROOT)["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(names), len(set(names)))
        for tool in tools:
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertLessEqual(set(schema["required"]), set(schema["properties"]))
            self.assertTrue(tool["description"])

    def test_every_addon_command_is_reachable_as_a_tool(self) -> None:
        sent = {tool.action for tool in server.TOOLS if tool.action} | {"input_sequence"}
        polled_by_input_sequence = {"input_sequence_status"}
        self.assertGreaterEqual(len(addon_commands()), 17)
        self.assertEqual(addon_commands() - sent, polled_by_input_sequence)
        self.assertEqual(sent - addon_commands(), set())

    def test_malformed_calls_are_protocol_errors_and_send_nothing(self) -> None:
        bad_calls = [
            {"name": "no_such_tool"},
            {"name": "ping", "arguments": {"extra": 1}},
            {"name": "ping", "arguments": []},
            {"name": "input_press"},
            {"name": "input_press", "arguments": {"action": 5}},
            {"name": "scene_tree", "arguments": {"depth": True}},
            {},
        ]
        with patch.object(devtools, "send_command") as send:
            out = io.StringIO()
            lines = [json.dumps(request("tools/call", bad)) for bad in bad_calls] + ["{not json"]
            server.serve(ROOT, lines, out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(replies), len(bad_calls) + 1)
        self.assertEqual(
            [reply["error"]["code"] for reply in replies],
            [server.INVALID_PARAMS] * len(bad_calls) + [server.PARSE_ERROR],
        )
        send.assert_not_called()


class ToolTests(unittest.TestCase):
    def test_a_tool_sends_its_command_and_returns_the_reply(self) -> None:
        reply = {"success": True, "message": "State retrieved", "data": {"hp": 3}}
        with patch.object(devtools, "send_command", return_value=reply) as send:
            result = call("get_state", {"node_path": "Player"})
        send.assert_called_once_with(ROOT, "get_state", {"node_path": "Player"}, timeout=30.0)
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], reply)
        self.assertEqual(json.loads(result["content"][0]["text"]), reply)

    def test_validation_takes_strict_and_engine_errors_reach_the_model(self) -> None:
        reply = {"success": True, "message": "Scenes valid", "data": {}, "errors": ["boom (a:1)"]}
        with patch.object(devtools, "send_command", return_value=reply) as send:
            result = call("validate_all_scenes", {"strict": True})
        send.assert_called_once_with(ROOT, "validate_all_scenes", {"strict": True}, timeout=120)
        self.assertEqual(result["structuredContent"]["errors"], ["boom (a:1)"])
        self.assertIn("boom (a:1)", result["content"][0]["text"])

    def test_a_failed_command_and_a_silent_game_are_error_results(self) -> None:
        refused = {"success": False, "message": "Refused: set_state needs allow_mutation"}
        arguments = {"node_path": ".", "property": "paused", "value": True}
        with patch.object(devtools, "send_command", return_value=refused):
            failed = call("set_state", arguments)
        with patch.object(devtools, "send_command", side_effect=devtools.NoResponseError("x")):
            silent = call("ping")
        self.assertTrue(failed["isError"])
        self.assertIn("Refused", failed["content"][0]["text"])
        self.assertTrue(silent["isError"])
        self.assertIn("No response", silent["content"][0]["text"])

    def test_screenshot_returns_the_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            # Where a game with the session "agent-7" writes: the reply names the file.
            png = Path(temporary) / "screenshots" / "agent-7" / "shot.png"
            png.parent.mkdir(parents=True)
            png.write_bytes(b"\x89PNG fake")
            reply = {"success": True, "message": "ok", "data": {"path": str(png)}}
            with patch.object(devtools, "send_command", return_value=reply):
                result = call("screenshot", {"filename": "shot.png"})
        image = result["content"][1]
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(image["data"]), b"\x89PNG fake")
        self.assertEqual(result["structuredContent"]["data"]["path"], str(png))

    def test_input_sequence_returns_the_path_of_each_screenshot_step(self) -> None:
        path = "C:/user/screenshots/agent-7/after.png"
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        passed = {
            "success": True,
            "message": "Sequence seq completed",
            "data": {
                "sequence_id": "seq",
                "status": "passed",
                "results": [{"step": 0, "type": "screenshot", "result": {"path": path}}],
            },
        }
        with (
            patch.object(devtools, "send_command", side_effect=[started, passed]),
            patch.object(devtools.time, "sleep"),
        ):
            result = call("input_sequence", {"steps": [{"type": "screenshot"}]})
        self.assertFalse(result["isError"])
        self.assertEqual(devtools.sequence_screenshots(result["structuredContent"]["data"]), [path])
        self.assertIn(path, result["content"][0]["text"])

    def test_call_passes_any_action_with_its_args(self) -> None:
        reply = {"success": True, "message": "Spawned", "data": None}
        with patch.object(devtools, "send_command", return_value=reply) as send:
            call("call", {"action": "spawn", "args": {"kind": "slime"}})
        send.assert_called_once_with(ROOT, "spawn", {"kind": "slime"})

    def test_wait_retries_until_the_game_answers_or_time_runs_out(self) -> None:
        pong = {"success": True, "message": "pong", "data": {"pid": 1}}
        silence = devtools.NoResponseError("x")
        with patch.object(devtools, "send_command", side_effect=[silence, silence, pong]) as send:
            answered = call("wait", {"timeout": 30})
        with patch.object(devtools, "send_command", side_effect=silence):
            gave_up = call("wait", {"timeout": 0.05})
        self.assertEqual(send.call_count, 3)
        self.assertFalse(answered["isError"])
        self.assertTrue(gave_up["isError"])

    def test_input_sequence_waits_for_the_end_and_clears_inputs_on_failure(self) -> None:
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        running = {"success": True, "data": {"sequence_id": "seq", "status": "running"}}
        failed = {
            "success": False,
            "message": "Assertion failed",
            "data": {"sequence_id": "seq", "status": "failed", "success": False},
        }
        cleared = {"success": True, "message": "cleared", "data": {}}
        replies = [started, running, failed, cleared]
        with (
            patch.object(devtools, "send_command", side_effect=replies) as send,
            patch.object(devtools.time, "sleep"),
        ):
            result = call("input_sequence", {"steps": [{"type": "clear"}], "timeout": 5})
        self.assertTrue(result["isError"])
        self.assertEqual(
            [sent.args[1] for sent in send.call_args_list],
            ["input_sequence", "input_sequence_status", "input_sequence_status", "input_clear"],
        )

    def test_logs_returns_the_tail_of_a_category(self) -> None:
        rows = [
            {"category": "system", "message": "DevTools initialized"},
            {"category": "input", "message": "Pressed action: a"},
            {"category": "input", "message": "Released action: a"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            log = devtools.get_mailbox_paths(Path(temporary))["log"]
            log.parent.mkdir(parents=True)
            log.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            with patch.object(devtools, "get_user_data_path", return_value=Path(temporary)):
                result = call("logs", {"category": "input", "tail": 1})
                everything = call("logs")
        self.assertEqual(json.loads(result["content"][0]["text"])["message"], "Released action: a")
        self.assertEqual(everything["content"][0]["text"].count("\n"), 2)


class StdioTests(unittest.TestCase):
    def test_the_server_answers_over_stdio_one_line_per_message(self) -> None:
        messages = [
            request("initialize", {"protocolVersion": "2025-06-18"}, 1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request("tools/list", None, 2),
        ]
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input="".join(json.dumps(message) + "\n" for message in messages).encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=True,
        )
        self.assertNotIn(b"\r", completed.stdout)
        replies = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertGreaterEqual(len(replies[1]["result"]["tools"]), 18)


if __name__ == "__main__":
    unittest.main()
