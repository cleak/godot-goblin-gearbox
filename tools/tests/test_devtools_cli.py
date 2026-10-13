"""tools/devtools.py: mailbox names, user data folder, the command protocol, exit codes."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from _legacy_console import LegacyConsole

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVTOOLS_PATH = PROJECT_ROOT / "tools" / "devtools.py"
SPEC = importlib.util.spec_from_file_location("kit_devtools", DEVTOOLS_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery invariant
    raise RuntimeError(f"Unable to load {DEVTOOLS_PATH}")
devtools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(devtools)


def namespace(**values: object) -> SimpleNamespace:
    """Parsed arguments as a command function receives them."""
    return SimpleNamespace(json=False, **values)


def run_main(*argv: str) -> tuple[int, str, str]:
    """Run the CLI in-process: (exit code, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = devtools.main(list(argv))
        except SystemExit as exit_request:  # argparse exits by itself on bad usage
            code = int(exit_request.code or 0)
    return code, stdout.getvalue(), stderr.getvalue()


# Every command of the CLI, with the arguments it cannot do without.
EVERY_COMMAND: tuple[list[str], ...] = (
    ["ping"],
    ["wait"],
    ["screenshot"],
    ["scene-tree"],
    ["get-state"],
    ["set-state", "--node", ".", "--property", "score", "--value", "1"],
    ["run-method", "--node", ".", "--method", "reset"],
    ["performance"],
    ["validate", "--scene", "res://main.tscn"],
    ["validate-all"],
    ["call", "board"],
    ["logs"],
    ["quit"],
    ["input", "press", "jump"],
    ["input", "release", "jump"],
    ["input", "tap", "jump"],
    ["input", "clear"],
    ["input", "list"],
    ["input", "sequence", "steps.json"],
)


class FakeGame:
    """Answers command files in a folder the way the DevTools autoload does."""

    def __init__(
        self, user_data: Path, session: str, replies: dict[str, dict], poll: float = 0.005
    ) -> None:
        self.paths = devtools.get_mailbox_paths(user_data, session)
        self.replies = replies
        self.poll = poll
        self.seen: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeGame:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.poll)
            if not self.paths["commands"].is_dir():
                continue
            # Like the autoload: every whole command of this poll, in file name order.
            for path in sorted(self.paths["commands"].glob("*.json")):
                if path.name.startswith("."):
                    continue
                try:
                    command = json.loads(path.read_text(encoding="utf-8"))
                    path.unlink()
                except (OSError, json.JSONDecodeError):
                    continue
                self.seen.append(command)
                self._answer(path.stem, command)

    def _answer(self, command_id: str, command: dict) -> None:
        reply = dict(self.replies.get(command["action"], {"success": False, "message": "?"}))
        reply.setdefault("data", None)
        reply.update(action=command["action"], id=command_id, echo=command.get("args"))
        temporary = self.paths["results"] / f".{command_id}.tmp"
        temporary.write_text(json.dumps(reply), encoding="utf-8")
        temporary.replace(self.paths["results"] / f"{command_id}.json")


class MailboxNameTests(unittest.TestCase):
    def test_default_project_is_repository_root(self) -> None:
        self.assertEqual(devtools.DEFAULT_PROJECT, PROJECT_ROOT)
        self.assertTrue((devtools.DEFAULT_PROJECT / "project.godot").exists())

    def test_session_names_match_the_autoload(self) -> None:
        # The same vectors as test/kit/devtools/test_devtools_mailbox.gd.
        cases = {
            "": "",
            "agent-2": "agent-2",
            " feature/one?two ": "feature_one_two",
            "a b\tc": "a_b_c",
            "na\u00efve": "na_ve",
            "..\\..\\up": "_.._up",
            "..hidden..": "hidden",
            "...": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(devtools.sanitize_session_name(raw), expected, raw)

    def test_the_mailbox_folder_carries_the_session_only_when_one_is_set(self) -> None:
        # The same vectors as test/kit/devtools/test_devtools_mailbox.gd.
        self.assertEqual(devtools.mailbox_folder_name(""), "devtools")
        self.assertEqual(
            devtools.mailbox_folder_name(" feature/one?two "), "devtools_feature_one_two"
        )
        self.assertEqual(devtools.mailbox_folder_name("agent-2"), "devtools_agent-2")

    def test_a_session_has_one_folder_for_commands_results_and_log(self) -> None:
        root = Path("user-data")
        paths = devtools.get_mailbox_paths(root, "parallel.3")
        self.assertEqual(paths["root"], root / "devtools_parallel.3")
        self.assertEqual(paths["commands"], root / "devtools_parallel.3" / "commands")
        self.assertEqual(paths["results"], root / "devtools_parallel.3" / "results")
        self.assertEqual(paths["log"], root / "devtools_parallel.3" / "log.jsonl")

    def test_command_ids_are_file_safe_unique_and_sort_by_age(self) -> None:
        # The addon takes ids of letters, digits, "_" and "-", at most 64 (is_valid_id).
        ids = [devtools.new_command_id() for _ in range(200)]
        self.assertEqual(len(set(ids)), 200)
        for command_id in ids:
            self.assertRegex(command_id, r"^[0-9a-f]{32}$")
        with patch.object(devtools.time, "time_ns", side_effect=[1, 2]):
            older, newer = devtools.new_command_id(), devtools.new_command_id()
        self.assertLess(older, newer)

    def test_session_defaults_to_the_environment_and_the_flag_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = make_project(Path(temporary), 'config/name="Session Game"')
            seen: list[str] = []

            def record(_args: object, _project: Path) -> int:
                seen.append(devtools.get_mailbox_paths(Path("u"))["root"].name)
                return 0

            with (
                patch.dict(devtools.os.environ, {devtools.SESSION_ENV: "from-env"}),
                patch.object(devtools, "cmd_ping", record),
            ):
                run_main("--project", str(project), "ping")
                run_main("--project", str(project), "--session", "from-flag", "ping")
                run_main("--project", str(project), "ping", "--session", "after")
        self.assertEqual(seen, ["devtools_from-env", "devtools_from-flag", "devtools_after"])

    def test_global_options_work_before_and_after_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = make_project(Path(temporary), 'config/name="Options Game"')
            seen: list[tuple[str, bool, str]] = []

            def record(args: object, project_path: Path) -> int:
                root = devtools.get_mailbox_paths(Path("u"))["root"].name
                seen.append((root, args.json, project_path.name))
                return 0

            with (
                patch.dict(devtools.os.environ, {devtools.SESSION_ENV: ""}),
                patch.object(devtools, "cmd_ping", record),
                patch.object(devtools, "cmd_input_clear", record),
            ):
                codes = [
                    run_main("--project", str(project), "--session", "s", "--json", "ping")[0],
                    run_main("--project", str(project), "ping", "--session", "s", "--json")[0],
                    # An option before the command survives when none follows it.
                    run_main("--json", "--session", "s", "-p", str(project), "ping")[0],
                    run_main("-p", str(project), "input", "clear", "--json", "--session", "s")[0],
                    run_main("--project", str(project), "input", "--session", "s", "clear")[0],
                ]
        self.assertEqual(codes, [0, 0, 0, 0, 0])
        self.assertEqual(seen[:4], [("devtools_s", True, "project")] * 4)
        self.assertEqual(seen[4], ("devtools_s", False, "project"))

    def test_no_command_takes_the_project_after_its_name(self) -> None:
        """.claude/settings.json pre-approves `devtools.py <command> *` for agents. The
        folder that says whose game is driven must not fit into that wildcard: such a
        call drives this project's game and no other."""
        with tempfile.TemporaryDirectory() as temporary:
            other = str(make_project(Path(temporary), 'config/name="Another Game"'))
            for command in EVERY_COMMAND:
                # The command line is complete: the project is all that is wrong with it.
                devtools.build_parser().parse_args(command)
                for option in (["--project", other], ["-p", other], [f"--project={other}"]):
                    with (
                        self.subTest(command=command, option=option[0]),
                        patch.object(devtools, "send_command") as sent,
                    ):
                        code, _out, err = run_main(*command, *option)
                        self.assertEqual(code, 2)
                        self.assertIn("unrecognized arguments", err)
                        sent.assert_not_called()
            # Between `input` and its own command it is refused too.
            with patch.object(devtools, "send_command") as sent:
                self.assertEqual(run_main("input", "--project", other, "clear")[0], 2)
                self.assertEqual(run_main("input", f"--project={other}", "clear")[0], 2)
                sent.assert_not_called()

    def test_every_command_is_in_the_list_above(self) -> None:
        def choices(parser: object) -> dict:
            return next(a.choices for a in parser._actions if isinstance(a.choices, dict))

        top = choices(devtools.build_parser())
        named = {(name,) for name in top if name != "input"}
        named |= {("input", name) for name in choices(top["input"])}
        listed = {
            tuple(command[:2]) if command[0] == "input" else (command[0],)
            for command in EVERY_COMMAND
        }
        self.assertEqual(listed, named)


def make_project(folder: Path, *application_lines: str) -> Path:
    project = folder / "project"
    project.mkdir()
    text = "config_version=5\n\n[application]\n\n" + "\n".join(application_lines) + "\n"
    (project / "project.godot").write_text(text + '\n[rendering]\n\nconfig/name="not this"\n')
    return project


class UserDataPathTests(unittest.TestCase):
    def user_data(self, platform: str, env: dict[str, str], *lines: str) -> Path:
        with tempfile.TemporaryDirectory() as temporary:
            project = make_project(Path(temporary), *lines)
            with (
                patch.object(devtools.sys, "platform", platform),
                patch.dict(devtools.os.environ, env, clear=True),
            ):
                return devtools.get_user_data_path(project)

    def test_windows_uses_appdata_and_the_project_name(self) -> None:
        actual = self.user_data("win32", {"APPDATA": "C:/Roaming"}, 'config/name="My Game"')
        self.assertEqual(actual, Path("C:/Roaming") / "Godot" / "app_userdata" / "My Game")

    def test_only_the_application_section_names_the_project(self) -> None:
        actual = self.user_data(
            "win32", {"APPDATA": "C:/Roaming"}, 'config/description="x"', 'config/name="Real"'
        )
        self.assertEqual(actual.name, "Real")

    def test_names_are_made_folder_safe_like_godot_does(self) -> None:
        actual = self.user_data(
            "win32", {"APPDATA": "C:/Roaming"}, 'config/name="Quest: The \\"Return\\"?"'
        )
        self.assertEqual(actual.name, "Quest- The -Return--")

    def test_a_project_without_a_name_uses_godots_placeholder(self) -> None:
        actual = self.user_data("win32", {"APPDATA": "C:/Roaming"})
        self.assertEqual(actual.name, "[unnamed project]")

    def test_custom_user_dir_replaces_the_godot_folder(self) -> None:
        actual = self.user_data(
            "win32",
            {"APPDATA": "C:/Roaming"},
            'config/name="My Game"',
            "config/use_custom_user_dir=true",
            'config/custom_user_dir_name="Studio/MyGame"',
        )
        self.assertEqual(actual, Path("C:/Roaming") / "Studio" / "MyGame")

    def test_custom_user_dir_is_made_safe_the_way_godot_does(self) -> None:
        # What Godot 4.7.2 reports as OS.get_user_data_dir() for each name: ".." does not
        # climb out of the data folder.
        for custom, folders in {
            "../../Escape/x": ("-", "-", "Escape", "x"),
            "..\\\\..\\\\Back": ("-", "-", "Back"),
            "a:b*c?d": ("a-b-c-d",),
            " /lead/trail/ ": ("lead", "trail"),
            "dots.../x.": ("dots-.", "x"),
            "a//b": ("a", "b"),
        }.items():
            with self.subTest(custom=custom):
                actual = self.user_data(
                    "win32",
                    {"APPDATA": "C:/Roaming"},
                    'config/name="My Game"',
                    "config/use_custom_user_dir=true",
                    f'config/custom_user_dir_name="{custom}"',
                )
                self.assertEqual(actual, Path("C:/Roaming").joinpath(*folders))

    def test_a_value_that_spans_lines_hides_no_setting(self) -> None:
        # A description may hold line breaks, and a line in it may look like a header.
        actual = self.user_data(
            "win32",
            {"APPDATA": "C:/Roaming"},
            'config/name="Probe"',
            'config/description="Line one\n[autoload]\nconfig/name=\\"Wrong\\"\nline four"',
            "config/use_custom_user_dir=true",
            'config/custom_user_dir_name="ProbeData"',
        )
        self.assertEqual(actual, Path("C:/Roaming") / "ProbeData")

    def test_linux_honors_xdg_data_home(self) -> None:
        actual = self.user_data("linux", {"XDG_DATA_HOME": "/xdg"}, 'config/name="XDG Game"')
        self.assertEqual(actual, Path("/xdg") / "godot" / "app_userdata" / "XDG Game")

    def test_windows_fails_if_appdata_is_missing(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "APPDATA"):
            self.user_data("win32", {}, 'config/name="Windows Game"')

    def test_a_folder_without_project_godot_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code, _out, err = run_main("--project", temporary, "ping")
        self.assertEqual(code, devtools.EXIT_USAGE)
        self.assertIn("no project.godot", err)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.user_data = Path(self._temporary.name) / "user"
        self.user_data.mkdir()
        patcher = patch.object(devtools, "get_user_data_path", return_value=self.user_data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_atomic_json_write_uses_same_directory_replace(self) -> None:
        target = self.user_data / "command.json"
        real_replace = devtools.os.replace
        observed = {}

        def inspect_replace(source: str, destination: str) -> None:
            observed["source"] = Path(source)
            observed["payload"] = json.loads(Path(source).read_text(encoding="utf-8"))
            self.assertEqual(Path(source).parent, target.parent)
            self.assertFalse(target.exists())
            real_replace(source, destination)

        with patch.object(devtools.os, "replace", side_effect=inspect_replace) as replace:
            devtools.atomic_write_json(target, {"action": "ping", "args": {}})

        replace.assert_called_once()
        self.assertEqual(observed["payload"], {"action": "ping", "args": {}})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), observed["payload"])
        self.assertFalse(observed["source"].exists())

    def test_atomic_write_retries_while_another_process_holds_the_file(self) -> None:
        target = self.user_data / "command.json"
        real_replace = devtools.os.replace
        attempts = []

        def busy_twice(source: str, destination: str) -> None:
            attempts.append(source)
            if len(attempts) < 3:
                raise PermissionError("sharing violation")
            real_replace(source, destination)

        with (
            patch.object(devtools.os, "replace", side_effect=busy_twice),
            patch.object(devtools.time, "sleep"),
        ):
            devtools.atomic_write_json(target, {"action": "ping"})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"action": "ping"})
        self.assertEqual([p.name for p in self.user_data.iterdir()], [target.name])

    def test_a_command_gets_its_own_reply(self) -> None:
        with FakeGame(self.user_data, "s1", {"ping": {"success": True, "message": "pong"}}) as game:
            reply = devtools.send_command(PROJECT_ROOT, "ping", {"n": 1}, timeout=5, session="s1")
        self.assertEqual(reply["message"], "pong")
        self.assertEqual(game.seen[0]["args"], {"n": 1})
        self.assertEqual(reply["id"], game.seen[0]["id"])
        # Nothing is left behind: the game removed the command, the client its reply.
        self.assertEqual(list(game.paths["commands"].iterdir()), [])
        self.assertEqual(list(game.paths["results"].iterdir()), [])

    def test_clients_that_send_at_once_each_get_their_own_reply(self) -> None:
        # One slow poll, so the game finds several commands waiting, as a real frame does.
        replies = {"echo": {"success": True, "message": "ok"}}
        outcomes: dict[int, object] = {}

        def client(number: int) -> None:
            try:
                for turn in range(5):
                    reply = devtools.send_command(
                        PROJECT_ROOT, "echo", {"client": number, "turn": turn}, 10, "busy"
                    )
                    if reply["echo"] != {"client": number, "turn": turn}:
                        raise AssertionError(f"client {number} got {reply['echo']}")
                outcomes[number] = "ok"
            except Exception as error:  # reported by the assert below
                outcomes[number] = error

        with FakeGame(self.user_data, "busy", replies, poll=0.05) as game:
            clients = [threading.Thread(target=client, args=(n,)) for n in range(8)]
            for thread in clients:
                thread.start()
            for thread in clients:
                thread.join(timeout=30)
        self.assertEqual(outcomes, dict.fromkeys(range(8), "ok"))
        self.assertEqual(len(game.seen), 40)
        self.assertEqual(list(game.paths["results"].iterdir()), [])

    def test_a_reply_to_another_command_is_left_for_its_client(self) -> None:
        paths = devtools.get_mailbox_paths(self.user_data, "s2")
        paths["results"].mkdir(parents=True)
        other = paths["results"] / "another-clients-command.json"
        other.write_text('{"success": true, "message": "not yours"}', encoding="utf-8")
        with self.assertRaises(devtools.NoResponseError):
            devtools.send_command(PROJECT_ROOT, "scene_tree", timeout=0.2, session="s2")
        self.assertTrue(other.exists())

    def test_no_answer_raises_and_withdraws_the_command(self) -> None:
        paths = devtools.get_mailbox_paths(self.user_data, "s3")
        with self.assertRaisesRegex(devtools.NoResponseError, "GODOT_AGENT_SESSION='s3'"):
            devtools.send_command(PROJECT_ROOT, "ping", timeout=0.2, session="s3")
        self.assertEqual(list(paths["commands"].iterdir()), [])

    def test_a_half_written_reply_is_read_once_it_is_whole(self) -> None:
        results = self.user_data / "reply.json"
        results.write_text('{"success": tr', encoding="utf-8")
        self.assertIsNone(devtools.take_result(results))
        self.assertTrue(results.exists())
        results.write_text('{"success": true}', encoding="utf-8")
        self.assertEqual(devtools.take_result(results), {"success": True})
        self.assertFalse(results.exists())

    def test_exit_codes_tell_failure_from_silence(self) -> None:
        replies = {
            "ping": {"success": True, "message": "pong", "data": {"project": "Fake", "pid": 7}},
            "set_state": {"success": False, "message": "Refused: set_state needs allow_mutation"},
        }
        base = ("--project", str(PROJECT_ROOT), "--session", "codes")
        with FakeGame(self.user_data, "codes", replies):
            ok = run_main(*base, "ping")
            refused = run_main(
                *base, "set-state", "--node", ".", "--property", "paused", "--value", "true"
            )
            as_json = run_main(*base, "--json", "ping")
        with patch.object(devtools, "WAIT_ATTEMPT_SECONDS", 0.05):
            silent = run_main(*base, "wait", "--timeout", "0.2")
        usage = run_main(*base, "call")

        self.assertEqual(ok[0], devtools.EXIT_OK)
        self.assertIn("DevTools is running: Fake (pid 7", ok[1])
        self.assertEqual(refused[0], devtools.EXIT_FAILED)
        self.assertEqual(refused[1], "")
        self.assertIn("Failed: Refused: set_state needs allow_mutation", refused[2])
        self.assertEqual(json.loads(as_json[1])["message"], "pong")
        self.assertEqual(silent[0], devtools.EXIT_NO_RESPONSE)
        self.assertIn("No response", silent[2])
        self.assertEqual(usage[0], devtools.EXIT_USAGE)

    def test_ping_and_screenshot_say_where_the_sessions_captures_are(self) -> None:
        """The folder is the game's to choose (user://screenshots/<session>/ with a
        session): the client prints what the game said and derives nothing."""
        folder = f"{self.user_data.as_posix()}/screenshots/shots"
        described = {"project": "Fake", "pid": 7, "user_dir": "U", "screenshots": folder}
        shot = {"path": f"{folder}/menu.png", "size": {"width": 1152, "height": 648}}
        replies = {
            "ping": {"success": True, "message": "pong", "data": described},
            "screenshot": {"success": True, "message": "Screenshot captured", "data": shot},
        }
        base = ("--project", str(PROJECT_ROOT), "--session", "shots")
        with FakeGame(self.user_data, "shots", replies) as game:
            ping = run_main(*base, "ping")
            captured = run_main(*base, "screenshot", "--filename", "menu.png")
        self.assertEqual(ping[1].splitlines()[1:], ["User data: U", f"Screenshots: {folder}"])
        self.assertEqual(
            captured[1].splitlines(), [f"Screenshot saved: {folder}/menu.png", "Size: 1152x648"]
        )
        # The name alone is sent; the game puts it in its session's folder.
        self.assertEqual(game.seen[-1]["args"], {"filename": "menu.png"})

    def test_engine_errors_of_a_reply_and_of_the_game_go_to_stderr(self) -> None:
        raised = ["Invalid access to property or key 'missing' (res://main.gd:12)"]
        replies = {
            "ping": {"success": True, "message": "pong", "data": {"project": "Fake", "errors": 3}},
            "run_method": {"success": True, "message": "Method called", "errors": raised},
        }
        base = ("--project", str(PROJECT_ROOT), "--session", "errors")
        with FakeGame(self.user_data, "errors", replies):
            ping = run_main(*base, "ping")
            called = run_main(*base, "run-method", "--node", ".", "--method", "boom")
            as_json = run_main(*base, "run-method", "--node", ".", "--method", "boom", "--json")
        self.assertEqual(ping[0], devtools.EXIT_OK)
        self.assertIn("3 error(s) since it started", ping[2])
        self.assertIn("logs --category error", ping[2])
        self.assertIn("Engine errors while the command ran (1):", called[2])
        self.assertIn(raised[0], called[2])
        self.assertEqual(json.loads(as_json[1])["errors"], raised)
        self.assertEqual(as_json[2], "")

    def test_validation_prints_warnings_and_fails_only_when_the_game_says_so(self) -> None:
        warning = {"severity": "warning", "code": "missing_texture", "message": "no texture"}
        data = {"strict": False, "errors": 0, "warnings": 1, "issues": {"res://e.tscn": [warning]}}
        passed = {"success": True, "message": "Scenes valid: 1 checked, 0 errors, 1 warnings"}
        replies = {"validate_all_scenes": {**passed, "data": data}}
        base = ("--project", str(PROJECT_ROOT), "--session", "validate")
        with FakeGame(self.user_data, "validate", replies) as game:
            lenient = run_main(*base, "validate-all")
            run_main(*base, "validate-all", "--strict")
        self.assertEqual(lenient[0], devtools.EXIT_OK)
        self.assertIn("[OK] Scenes valid", lenient[1])
        self.assertIn("res://e.tscn:", lenient[1])
        self.assertIn("[WARN] missing_texture: no texture", lenient[1])
        sent = [
            command["args"] for command in game.seen if command["action"] == "validate_all_scenes"
        ]
        self.assertEqual(sent, [{"strict": False}, {"strict": True}])

    def test_wait_keeps_pinging_until_the_game_answers(self) -> None:
        game = FakeGame(self.user_data, "late", {"ping": {"success": True, "data": {"pid": 1}}})
        starter = threading.Timer(0.3, game.__enter__)
        starter.start()
        self.addCleanup(game.__exit__)
        with patch.object(devtools, "WAIT_ATTEMPT_SECONDS", 0.1):
            code, out, _err = run_main(
                "--project", str(PROJECT_ROOT), "--session", "late", "wait", "--timeout", "10"
            )
        starter.join()
        self.assertEqual(code, devtools.EXIT_OK)
        self.assertIn("DevTools is ready", out)

    def test_call_sends_key_value_arguments_to_any_action(self) -> None:
        replies = {"spawn": {"success": True, "message": "Spawned", "data": {"count": 2}}}
        with FakeGame(self.user_data, "call", replies) as game:
            code, out, _err = run_main(
                "--project",
                str(PROJECT_ROOT),
                "--session",
                "call",
                "call",
                "spawn",
                "kind=slime",
                "count=2",
                "at=[1, 2]",
                "boss=false",
            )
        self.assertEqual(code, devtools.EXIT_OK)
        self.assertEqual(game.seen[0]["action"], "spawn")
        self.assertEqual(
            game.seen[0]["args"], {"kind": "slime", "count": 2, "at": [1, 2], "boss": False}
        )
        self.assertIn('"count": 2', out)
        self.assertIn("Spawned", out)

    def test_logs_filter_by_category_and_tail(self) -> None:
        log = devtools.get_mailbox_paths(self.user_data, "logs")["log"]
        log.parent.mkdir(parents=True)
        rows = [
            {"timestamp": 0, "category": "system", "message": "DevTools initialized"},
            {"timestamp": 0, "category": "input", "message": "Pressed action: a"},
            {"timestamp": 0, "category": "input", "message": "Released action: a"},
        ]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        base = ("--project", str(PROJECT_ROOT), "--session", "logs", "logs")
        code, out, _err = run_main(*base, "--category", "input", "--tail", "1")
        self.assertEqual(code, devtools.EXIT_OK)
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("[input] Released action: a", out)
        missing = run_main("--project", str(PROJECT_ROOT), "--session", "none", "logs")
        self.assertEqual(missing[0], devtools.EXIT_FAILED)
        self.assertEqual(run_main(*base, "--tail", "0")[0], devtools.EXIT_USAGE)

    def test_the_log_tail_is_read_from_the_end_of_the_file(self) -> None:
        log = self.user_data / "log.jsonl"
        rows = [
            {"category": "input" if number % 2 else "system", "message": f"r\u00f6w {number}"}
            for number in range(50)
        ]
        text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n\nnot json\n"
        log.write_bytes(text.encode("utf-8"))
        everything = devtools.read_log(log)
        self.assertEqual([row for _line, row in everything], [*rows, None])
        # Blocks far smaller than a line: every line straddles several of them.
        with patch.object(devtools, "LOG_BLOCK_BYTES", 7):
            self.assertEqual(devtools.read_log(log), everything)
            last_inputs = devtools.read_log(log, tail=3, category="input")
            self.assertEqual(devtools.read_log(log, tail=2), everything[-2:])
        messages = [row["message"] for _line, row in last_inputs]
        self.assertEqual(messages, ["r\u00f6w 45", "r\u00f6w 47", "r\u00f6w 49"])
        with patch.object(Path, "read_text", side_effect=AssertionError("read the whole log")):
            self.assertEqual(len(devtools.read_log(log, tail=5)), 5)


class ArgumentTests(unittest.TestCase):
    def test_pairs_parse_json_values_and_keep_bare_text(self) -> None:
        self.assertEqual(
            devtools.parse_pairs(["kind=slime", "x=18", "on=false", "scale=0.25", 'v={"x": 1}']),
            {"kind": "slime", "x": 18, "on": False, "scale": 0.25, "v": {"x": 1}},
        )
        self.assertEqual(devtools.parse_pairs(["text=a=b"]), {"text": "a=b"})
        self.assertEqual(devtools.parse_pairs([]), {})

    def test_malformed_pairs_are_usage_errors(self) -> None:
        for bad in ("kind", "=value"):
            with self.assertRaises(devtools.UsageError):
                devtools.parse_pairs([bad])

    def test_input_strength_is_validated_before_sending(self) -> None:
        for strength in (1.5, -0.1, float("nan")):
            with (
                patch.object(devtools, "send_command") as send,
                self.assertRaises(devtools.UsageError),
            ):
                devtools.cmd_input_press(namespace(action="pause", strength=strength), PROJECT_ROOT)
            send.assert_not_called()

    def test_run_method_args_must_be_a_json_array(self) -> None:
        for bad in ('{"a": 1}', "[1,"):
            with (
                patch.object(devtools, "send_command") as send,
                self.assertRaises(devtools.UsageError),
            ):
                devtools.cmd_run_method(namespace(node=".", method="reset", args=bad), PROJECT_ROOT)
            send.assert_not_called()

    def test_set_state_sends_json_values_and_plain_text(self) -> None:
        sent = []

        def record(_project: Path, action: str, args: dict) -> dict:
            sent.append(args["value"])
            return {"success": True, "data": {"value": args["value"]}}

        with (
            patch.object(devtools, "send_command", side_effect=record),
            redirect_stdout(io.StringIO()),
        ):
            for raw in ("5", "-2.5", "true", '{"x": 1, "y": 2}', "idle", "#ff0000"):
                code = devtools.cmd_set_state(
                    namespace(node="Player", property="p", value=raw), PROJECT_ROOT
                )
                self.assertEqual(code, devtools.EXIT_OK)
        self.assertEqual(sent, [5, -2.5, True, {"x": 1, "y": 2}, "idle", "#ff0000"])

    def test_set_state_names_the_property_like_a_node_path(self) -> None:
        reply = {"success": True, "data": {"value": 5}}
        out = io.StringIO()
        with patch.object(devtools, "send_command", return_value=reply), redirect_stdout(out):
            devtools.cmd_set_state(namespace(node=".", property="count", value="5"), PROJECT_ROOT)
        self.assertEqual(out.getvalue(), ".:count = 5\n")


class SequenceTests(unittest.TestCase):
    def write_sequence(self, content: str) -> str:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "sequence.json"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def run_sequence(self, responses: list, timeout: float = 1.0) -> tuple[object, list[str]]:
        """Run cmd_input_sequence against scripted replies: (exit code or error, actions sent)."""
        args = namespace(
            file=self.write_sequence('{"steps": [{"type": "clear"}]}'), timeout=timeout
        )
        outcome: object
        with (
            patch.object(devtools, "send_command", side_effect=responses) as send,
            patch.object(devtools.time, "sleep"),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            try:
                outcome = devtools.cmd_input_sequence(args, PROJECT_ROOT)
            except devtools.NoResponseError as error:
                outcome = error
        return outcome, [call.args[1] for call in send.call_args_list]

    def test_unusable_sequence_files_are_usage_errors_and_send_nothing(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "no_such_sequence_file.json")
        files = [missing, *(self.write_sequence(text) for text in ("[]", "{", '{"steps": []}'))]
        for file in files:
            with (
                patch.object(devtools, "send_command") as send,
                self.assertRaises(devtools.UsageError),
            ):
                devtools.cmd_input_sequence(namespace(file=file, timeout=1.0), PROJECT_ROOT)
            send.assert_not_called()

    def test_a_nonfinite_or_negative_timeout_is_a_usage_error(self) -> None:
        file = self.write_sequence('{"steps": [{"type": "clear"}]}')
        for timeout in (float("nan"), float("inf"), 0.0, -1.0):
            with (
                patch.object(devtools, "send_command") as send,
                self.assertRaises(devtools.UsageError),
            ):
                devtools.cmd_input_sequence(namespace(file=file, timeout=timeout), PROJECT_ROOT)
            send.assert_not_called()

    def test_waits_for_the_terminal_status(self) -> None:
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        running = {"success": True, "data": {"sequence_id": "seq", "status": "running"}}
        passed = {
            "success": True,
            "message": "completed",
            "data": {
                "sequence_id": "seq",
                "status": "passed",
                "success": True,
                "completed_steps": 1,
            },
        }
        outcome, sent = self.run_sequence([started, running, passed])
        self.assertEqual(outcome, devtools.EXIT_OK)
        self.assertEqual(sent, ["input_sequence", "input_sequence_status", "input_sequence_status"])

    def test_each_screenshot_step_prints_where_its_file_is(self) -> None:
        """Only --json showed the paths: an agent told to open every screenshot had to guess
        the folder."""
        folder = "C:/Users/me/AppData/Roaming/Godot/app_userdata/Game/screenshots/agent-7"
        results = [
            {"step": 0, "type": "call", "status": "ok", "result": {"action": "reset"}},
            {
                "step": 1,
                "type": "screenshot",
                "status": "ok",
                "result": {"path": f"{folder}/a.png"},
            },
            {"step": 2, "type": "tap", "status": "ok", "result": {"action": "interact"}},
            {
                "step": 3,
                "type": "screenshot",
                "status": "ok",
                "result": {"path": f"{folder}/b.png"},
            },
            {"step": 4, "type": "screenshot", "status": "error", "error": "Failed to save"},
        ]
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        cleared = {"success": True, "message": "cleared", "data": {}}
        file = self.write_sequence('{"steps": [{"type": "clear"}]}')
        for status, code in (("passed", devtools.EXIT_OK), ("failed", devtools.EXIT_FAILED)):
            ended = {
                "success": status == "passed",
                "message": "Sequence seq failed at step 4: Failed to save",
                "data": {
                    "sequence_id": "seq",
                    "status": status,
                    "completed_steps": 1,
                    "results": results,
                },
            }
            for as_json in (False, True):
                out, err = io.StringIO(), io.StringIO()
                args = SimpleNamespace(json=as_json, file=file, timeout=1.0)
                with (
                    patch.object(devtools, "send_command", side_effect=[started, ended, cleared]),
                    patch.object(devtools.time, "sleep"),
                    redirect_stdout(out),
                    redirect_stderr(err),
                ):
                    self.assertEqual(devtools.cmd_input_sequence(args, PROJECT_ROOT), code)
                if as_json:
                    # One JSON document and nothing else: the paths are inside it.
                    self.assertEqual(json.loads(out.getvalue()), ended)
                    continue
                lines = out.getvalue().splitlines()
                saved = [f"Screenshot saved: {folder}/a.png", f"Screenshot saved: {folder}/b.png"]
                # Also for a sequence that failed later: those files are there to open.
                self.assertEqual([line for line in lines if "Screenshot" in line], saved)
                self.assertEqual(lines[-1] == "Sequence passed: 1/1 steps", status == "passed")
                self.assertEqual(
                    "Failed: Sequence seq failed" in err.getvalue(), status == "failed"
                )

    def test_a_failed_or_timed_out_sequence_exits_1_and_clears_inputs(self) -> None:
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        cleared = {"success": True, "message": "cleared", "data": {}}
        for status, message in (
            ("failed", "Assertion failed"),
            ("failed", "Failed to save screenshot"),
            ("timed_out", "Sequence timed out"),
        ):
            with self.subTest(status=status, message=message):
                ended = {
                    "success": False,
                    "message": message,
                    "data": {"sequence_id": "seq", "status": status, "success": False},
                }
                outcome, sent = self.run_sequence([started, ended, cleared])
                self.assertEqual(outcome, devtools.EXIT_FAILED)
                self.assertEqual(sent, ["input_sequence", "input_sequence_status", "input_clear"])

    def test_a_sequence_the_game_refuses_exits_1_and_clears_inputs(self) -> None:
        refused = {"success": False, "message": "Step 0: unknown action 'x'", "data": None}
        cleared = {"success": True, "message": "cleared", "data": {}}
        outcome, sent = self.run_sequence([refused, cleared])
        self.assertEqual(outcome, devtools.EXIT_FAILED)
        self.assertEqual(sent, ["input_sequence", "input_clear"])

    def test_silence_while_polling_clears_inputs_and_reports_no_response(self) -> None:
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        cleared = {"success": True, "message": "cleared", "data": {}}
        outcome, sent = self.run_sequence(
            [started, devtools.NoResponseError("status mailbox unavailable"), cleared]
        )
        self.assertIsInstance(outcome, devtools.NoResponseError)
        self.assertEqual(sent, ["input_sequence", "input_sequence_status", "input_clear"])

    def test_a_failing_cleanup_does_not_hide_the_failure(self) -> None:
        started = {"success": True, "message": "started", "data": {"sequence_id": "seq"}}
        ended = {
            "success": False,
            "message": "Assertion failed",
            "data": {"sequence_id": "seq", "status": "failed", "success": False},
        }
        outcome, sent = self.run_sequence([started, ended, devtools.NoResponseError("gone")])
        self.assertEqual(outcome, devtools.EXIT_FAILED)
        self.assertEqual(sent[-1], "input_clear")


def test_a_log_row_outside_the_console_code_page_is_printed_not_a_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    """An agent reads this tool through a pipe, which on Windows is cp1252: a game that
    logs `✓` must not end `logs` in a UnicodeEncodeError with nothing printed."""
    log = devtools.get_mailbox_paths(tmp_path, "glyphs")["log"]
    log.parent.mkdir(parents=True)
    row = {"timestamp": 0, "category": "game", "message": "spawned ✓ 日"}
    log.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(devtools, "get_user_data_path", lambda _project: tmp_path)
    console = LegacyConsole(monkeypatch)
    code = devtools.main(["--project", str(PROJECT_ROOT), "--session", "glyphs", "logs"])
    assert code == devtools.EXIT_OK
    assert "[game] spawned \\u2713 \\u65e5" in console.text()


def test_logs_with_nothing_to_show_say_so(tmp_path: Path, monkeypatch, capsys) -> None:
    log = devtools.get_mailbox_paths(tmp_path, "quiet")["log"]
    log.parent.mkdir(parents=True)
    log.write_text('{"timestamp": 0, "category": "input", "message": "x"}\n', encoding="utf-8")
    monkeypatch.setattr(devtools, "get_user_data_path", lambda _project: tmp_path)
    base = ["--project", str(PROJECT_ROOT), "--session", "quiet", "logs"]
    assert devtools.main([*base, "--category", "error"]) == devtools.EXIT_OK
    said = capsys.readouterr()
    assert said.out == "" and said.err.strip() == f"No 'error' rows in {log}"
    assert devtools.main(base) == devtools.EXIT_OK
    assert capsys.readouterr().err == ""


def test_validation_passes_on_the_games_error_count(tmp_path: Path, monkeypatch, capsys) -> None:
    """A scene is valid when its own scripts attach; a script it uses by class name may
    not compile. The count of errors the game has logged is where that shows."""
    replies = {
        "validate_all_scenes": {
            "success": True,
            "message": "Scenes valid: 1 checked, 0 errors, 0 warnings",
            "data": {"errors": 0, "warnings": 0, "issues": {}},
        },
        "ping": {"success": True, "message": "pong", "data": {"errors": 6}},
    }
    monkeypatch.setattr(devtools, "get_user_data_path", lambda _project: tmp_path)
    base = ["--project", str(PROJECT_ROOT), "--session", "broken"]
    with FakeGame(tmp_path, "broken", replies):
        assert devtools.main([*base, "validate-all"]) == devtools.EXIT_OK
        said = capsys.readouterr()
        assert "[OK] Scenes valid: 1 checked" in said.out
        assert "the game has logged 6 error(s) since it started" in said.err
        assert "logs --category error" in said.err
        # --json stays one document on stdout and nothing else.
        assert devtools.main([*base, "validate-all", "--json"]) == devtools.EXIT_OK
        said = capsys.readouterr()
        assert json.loads(said.out)["success"] is True and said.err == ""


if __name__ == "__main__":
    unittest.main()
