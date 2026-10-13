"""tools/mcp/server.py: the DevTools commands as MCP tools over stdio JSON-RPC."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from _running_game import running_game

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
                "results": [
                    {"step": 0, "type": "screenshot", "status": "ok", "result": {"path": path}}
                ],
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
        mailbox = devtools.get_mailbox_paths(devtools.get_user_data_path(ROOT))["root"]
        self.assertIn(f"Its mailbox would be {mailbox}", gave_up["content"][0]["text"])
        # A game that died while starting is silent too: its own output is where to look.
        self.assertIn("If it was started, its output says why", gave_up["content"][0]["text"])

    def test_a_mailbox_nobody_made_or_nobody_can_write_is_an_error_result(self) -> None:
        """Neither ends the server: the request after it is answered."""
        ping = request("tools/call", {"name": "ping", "arguments": {}}, 1)
        wait = request("tools/call", {"name": "wait", "arguments": {"timeout": 0.2}}, 2)
        lines = [json.dumps(message) for message in (ping, wait, request("tools/list", None, 3))]
        with tempfile.TemporaryDirectory() as temporary:
            user_data = Path(temporary) / "user"
            mailbox = devtools.get_mailbox_paths(user_data)["root"]
            with patch.object(devtools, "get_user_data_path", return_value=user_data):
                nobody = io.StringIO()
                server.serve(ROOT, lines, nobody)
                self.assertFalse(user_data.exists())
                mailbox.mkdir(parents=True)
                refusal = PermissionError(13, "Permission denied")
                with patch.object(devtools, "atomic_write_json", side_effect=refusal):
                    locked = io.StringIO()
                    server.serve(ROOT, lines, locked)
        for out, said in (
            (nobody, f"its mailbox would be {mailbox}, and that folder does not exist"),
            (locked, f"cannot write the mailbox {mailbox}: Permission denied"),
        ):
            replies = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([reply["id"] for reply in replies], [1, 2, 3])
            self.assertTrue(replies[0]["result"]["isError"])
            self.assertIn(said, replies[0]["result"]["content"][0]["text"])
            self.assertTrue(replies[1]["result"]["isError"])
            self.assertEqual(len(replies[2]["result"]["tools"]), len(server.TOOLS))
        waited = json.loads(locked.getvalue().splitlines()[1])["result"]["content"][0]["text"]
        self.assertIn(f"cannot write the mailbox {mailbox}", waited)

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


def converse(command: list[str], messages: list[dict], **how: object) -> list[dict]:
    """Start a server, send it `messages` over stdin, and return what it answered."""
    completed = subprocess.run(
        command,
        input="".join(json.dumps(message) + "\n" for message in messages).encode("utf-8"),
        capture_output=True,
        timeout=180,
        check=False,
        **how,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert b"\r" not in completed.stdout
    return [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]


def test_the_example_starts_the_server_as_it_is_written() -> None:
    """An MCP client starts the server from the project root, without a shell: the same
    line has to work on every system."""
    example = json.loads((ROOT / "tools" / "mcp" / ".mcp.json.example").read_text("utf-8"))
    entry = example["mcpServers"]["godot-devtools"]
    assert list(entry["env"]) == [devtools.SESSION_ENV]
    script = entry["args"][-1]
    assert "\\" not in script and (ROOT / script) == SERVER_PATH
    program = shutil.which(entry["command"])
    if program is None:
        pytest.skip(f"needs {entry['command']} on PATH")
    replies = converse(
        [program, *entry["args"]],
        [
            request("initialize", {"protocolVersion": "2025-06-18"}, 1),
            request("tools/list", None, 2),
        ],
        cwd=ROOT,
        env=os.environ | entry["env"],
    )
    assert [reply["id"] for reply in replies] == [1, 2]
    assert replies[0]["result"]["serverInfo"]["name"] == "godot-devtools"
    assert len(replies[1]["result"]["tools"]) == len(server.TOOLS)


@pytest.mark.real_godot
def test_the_server_reaches_a_real_game_on_this_system(tmp_path: Path) -> None:
    """Over stdio, with the environment of the game: the server finds its mailbox."""

    def tool(number: int, name: str, arguments: dict | None = None) -> dict:
        return request("tools/call", {"name": name, "arguments": arguments or {}}, number)

    with running_game(tmp_path, "mcp") as game:
        replies = converse(
            [sys.executable, str(SERVER_PATH), "--project", str(game.project)],
            [
                request("initialize", {"protocolVersion": "2025-06-18"}, 1),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                tool(2, "wait", {"timeout": 90}),
                tool(3, "ping"),
                tool(4, "input_tap", {"action": "ui_accept"}),
                tool(5, "get_state", {"node_path": "."}),
                tool(6, "screenshot", {"filename": "nothing.png"}),
                tool(7, "quit"),
            ],
            env=game.env,
        )
        assert [reply["id"] for reply in replies] == [1, 2, 3, 4, 5, 6, 7], game.output()
        results = {reply["id"]: reply["result"] for reply in replies}
        assert not results[2]["isError"], results[2]["content"][0]["text"] + game.output()
        game_said = results[3]["structuredContent"]["data"]
        assert game_said["session"] == "mcp"
        assert Path(game_said["user_dir"]).is_relative_to(game.user_data)
        assert not results[4]["isError"]
        assert results[5]["structuredContent"]["data"]["presses"] == 1
        # A headless game renders no frame: an error result with the reason, and no image.
        assert results[6]["isError"] and len(results[6]["content"]) == 1
        assert "Nothing to capture" in results[6]["content"][0]["text"]
        assert not results[7]["isError"]
        assert game.process.wait(timeout=60) == 0, game.output()


if __name__ == "__main__":
    unittest.main()
