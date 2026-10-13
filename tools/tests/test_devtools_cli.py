"""tools/devtools.py: mailbox names, user data folder, the command protocol, exit codes."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _gate_fixtures import (
    PLATFORM,
    clean_env,
    linux_only,
    not_as_root,
    real_godot,
    takes_no_new_file,
    user_data_env,
)
from _legacy_console import LegacyConsole
from _running_game import running_game, sample_game

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVTOOLS_PATH = PROJECT_ROOT / "tools" / "devtools.py"
SMOKE_SEQUENCE = PROJECT_ROOT / "test" / "sequences" / "smoke.json"
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
        # Like the autoload: the game makes its mailbox when it starts, and nobody else does.
        self.paths["commands"].mkdir(parents=True, exist_ok=True)
        self.paths["results"].mkdir(parents=True, exist_ok=True)
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


# What Godot 4.7.2 reports as OS.get_user_data_dir(), measured on Windows and on Linux and
# the same on both: (config/name, config/custom_user_dir_name or None when
# use_custom_user_dir is off, the folders below the data root). GODOT_DIR stands for
# Godot/app_userdata, which is godot/app_userdata on Linux.
# test_godot_names_its_user_data_folder_as_the_client_does asks the real Godot again.
GODOT_DIR = "<godot>"
USER_DIR_CASES: tuple[tuple[str, str | None, tuple[str, ...]], ...] = (
    ("My Game", None, (GODOT_DIR, "My Game")),
    ('Quest: The "Return"?', None, (GODOT_DIR, "Quest- The -Return--")),
    ("x<y>|z*", None, (GODOT_DIR, "x-y--z-")),
    ("a/b\\c", None, (GODOT_DIR, "a-b-c")),
    (" spaced ", None, (GODOT_DIR, "spaced")),
    ("\tcontrol\n", None, (GODOT_DIR, "control")),
    # Only spaces and control characters are trimmed: a no-break space stays.
    ("\u00a0wide\u00a0", None, (GODOT_DIR, "\u00a0wide\u00a0")),
    ("na\u00efve \u65e5", None, (GODOT_DIR, "na\u00efve \u65e5")),
    ("Trailing dots...", None, (GODOT_DIR, "Trailing dots")),
    ("dots. . .", None, (GODOT_DIR, "dots. . ")),
    ("..hidden", None, (GODOT_DIR, "..hidden")),
    ("a..b", None, (GODOT_DIR, "a..b")),
    (".", None, (GODOT_DIR, "dot")),
    (" . ", None, (GODOT_DIR, "dot")),
    ("..", None, (GODOT_DIR, "twodots")),
    ("", None, (GODOT_DIR, "[unnamed project]")),
    ("...", None, (GODOT_DIR, "[unnamed project]")),
    ("My Game", "Studio/MyGame", ("Studio", "MyGame")),
    # ".." does not climb out of the data folder.
    ("My Game", "../../Escape/x", ("-", "-", "Escape", "x")),
    ("My Game", "..\\..\\Back", ("-", "-", "Back")),
    ("My Game", "a:b*c?d", ("a-b-c-d",)),
    ("My Game", " /lead/trail/ ", ("lead", "trail")),
    ("My Game", "dots.../x.", ("dots-.", "x")),
    ("My Game", "a//b", ("a", "b")),
    ("My Game", "a///b", ("a", "b")),
    ("My Game", "a/./b", ("a", "b")),
    ("My Game", "...", ("-",)),
    # No custom name, or one that is nothing but dots: the project's name, in the root.
    ("My Game", "", ("My Game",)),
    ("My Game", ".", ("My Game",)),
    # A lone slash is the data root itself.
    ("My Game", "/", ()),
    # A project without a usable name gets no custom folder.
    ("", "Custom", (GODOT_DIR, "[unnamed project]")),
    ("...", "Custom", (GODOT_DIR, "[unnamed project]")),
)
GODOT_DIRS = {"windows": "Godot", "macos": "Godot", "linux": "godot"}


def application_settings(name: str, custom: str | None) -> dict[str, str]:
    """The [application] settings of a USER_DIR_CASES row, as devtools reads them."""
    settings = {"config/name": name}
    if custom is not None:
        settings |= {"config/use_custom_user_dir": "true", "config/custom_user_dir_name": custom}
    return settings


def expected_folder(root: Path, platform: str, folders: tuple[str, ...]) -> Path:
    if folders[:1] == (GODOT_DIR,):
        return root.joinpath(GODOT_DIRS[platform], "app_userdata", *folders[1:])
    return root.joinpath(*folders)


class UserDataPathTests(unittest.TestCase):
    def user_data(self, platform: str, env: dict[str, str], *lines: str) -> Path:
        with tempfile.TemporaryDirectory() as temporary:
            project = make_project(Path(temporary), *lines)
            return devtools.get_user_data_path(project, platform, env)

    def assert_same_folder(self, actual: Path, expected: Path) -> None:
        """Letter for letter: on Windows two paths that differ only in case are equal, and
        `godot` against `Godot` is what tells Linux from the others."""
        self.assertEqual(actual.as_posix(), expected.as_posix())

    def test_windows_uses_appdata_and_the_project_name(self) -> None:
        actual = self.user_data("windows", {"APPDATA": "C:/Roaming"}, 'config/name="My Game"')
        self.assert_same_folder(actual, Path("C:/Roaming") / "Godot" / "app_userdata" / "My Game")

    def test_only_the_application_section_names_the_project(self) -> None:
        actual = self.user_data(
            "windows", {"APPDATA": "C:/Roaming"}, 'config/description="x"', 'config/name="Real"'
        )
        self.assertEqual(actual.name, "Real")

    def test_a_name_in_the_project_file_is_unescaped_then_made_folder_safe(self) -> None:
        actual = self.user_data(
            "windows", {"APPDATA": "C:/Roaming"}, 'config/name="Quest: The \\"Return\\"?..."'
        )
        self.assertEqual(actual.name, "Quest- The -Return--")
        custom = self.user_data(
            "linux",
            {"XDG_DATA_HOME": "/xdg"},
            'config/name="My Game"',
            "config/use_custom_user_dir=true",
            'config/custom_user_dir_name="..\\\\..\\\\Back"',
        )
        self.assert_same_folder(custom, Path("/xdg") / "-" / "-" / "Back")

    def test_a_project_without_a_name_uses_godots_placeholder(self) -> None:
        actual = self.user_data("windows", {"APPDATA": "C:/Roaming"})
        self.assertEqual(actual.name, "[unnamed project]")

    def test_the_folder_is_named_the_way_godot_names_it_on_every_system(self) -> None:
        roots = {
            "windows": ({"APPDATA": "C:/Roaming"}, Path("C:/Roaming")),
            "linux": ({"XDG_DATA_HOME": "/xdg", "HOME": "/home/me"}, Path("/xdg")),
            "macos": ({"HOME": "/Users/me"}, Path("/Users/me/Library/Application Support")),
        }
        for platform, (env, root) in roots.items():
            for name, custom, folders in USER_DIR_CASES:
                with self.subTest(platform=platform, name=name, custom=custom):
                    actual = devtools.user_data_folder(
                        application_settings(name, custom), platform, env
                    )
                    self.assert_same_folder(actual, expected_folder(root, platform, folders))

    def test_a_value_that_spans_lines_hides_no_setting(self) -> None:
        # A description may hold line breaks, and a line in it may look like a header.
        actual = self.user_data(
            "windows",
            {"APPDATA": "C:/Roaming"},
            'config/name="Probe"',
            'config/description="Line one\n[autoload]\nconfig/name=\\"Wrong\\"\nline four"',
            "config/use_custom_user_dir=true",
            'config/custom_user_dir_name="ProbeData"',
        )
        self.assert_same_folder(actual, Path("C:/Roaming") / "ProbeData")

    def test_a_quoted_value_is_read_as_godot_reads_it(self) -> None:
        # (the value as project.godot holds it, the name Godot reads from it)
        for written, name in (
            ('"plain"', "plain"),
            # A tab and a line break stand in the file as they are.
            ('"tab\there"', "tab\there"),
            ('"line1\nline2"', "line1\nline2"),
            ('"quote \\" and backslash \\\\"', 'quote " and backslash \\'),
            ('"a\\tb\\nc\\qd"', "a\tb\ncqd"),
            ('"caf\\u00e9 \\U01f600 \\ud83d\\ude00"', "café \U0001f600 \U0001f600"),
            ('"semi;colon" ; a "comment', "semi;colon"),
            ('"[application]"', "[application]"),
            ('"a=b"', "a=b"),
        ):
            with self.subTest(written=written), tempfile.TemporaryDirectory() as temporary:
                project = make_project(
                    Path(temporary),
                    f"config/name={written}",
                    '; a comment with one " quote',
                    "config/use_custom_user_dir=true",
                    'config/custom_user_dir_name="After"',
                )
                settings = devtools.read_application_settings(project / "project.godot")
                self.assertEqual(settings["config/name"], name)
                # What follows the value is still read.
                self.assertEqual(settings["config/use_custom_user_dir"], "true")
                self.assertEqual(settings["config/custom_user_dir_name"], "After")

    def test_each_system_reads_its_own_variable_and_no_other(self) -> None:
        everything = {
            "APPDATA": "C:/Roaming",
            "XDG_DATA_HOME": "/xdg",
            "HOME": "/home/me",
            "XDG_CONFIG_HOME": "/config",
        }
        self.assert_same_folder(devtools.godot_data_root("windows", everything), Path("C:/Roaming"))
        self.assert_same_folder(devtools.godot_data_root("linux", everything), Path("/xdg"))
        # macOS reads no XDG variable: only HOME moves the folder.
        self.assert_same_folder(
            devtools.godot_data_root("macos", everything),
            Path("/home/me") / "Library" / "Application Support",
        )

    def test_linux_falls_back_on_home_the_way_godot_does(self) -> None:
        # Measured with Godot 4.7.2: a relative or empty XDG_DATA_HOME is ignored.
        home = Path("/home/me") / ".local" / "share"
        for env, expected in (
            ({"XDG_DATA_HOME": "/xdg", "HOME": "/home/me"}, Path("/xdg")),
            ({"XDG_DATA_HOME": "rel/data", "HOME": "/home/me"}, home),
            ({"XDG_DATA_HOME": "", "HOME": "/home/me"}, home),
            ({"HOME": "/home/me"}, home),
            ({"HOME": "/home/me", "XDG_CONFIG_HOME": "/config"}, home),
            # A process with no HOME at all: Godot uses its settings folder.
            ({"XDG_CONFIG_HOME": "/config"}, Path("/config")),
            ({"XDG_DATA_HOME": "rel/data", "XDG_CONFIG_HOME": "/config"}, Path("/config")),
        ):
            with self.subTest(env=env):
                self.assert_same_folder(devtools.godot_data_root("linux", env), expected)

    def test_an_environment_that_names_no_folder_is_a_usage_error(self) -> None:
        for platform, env, named in (
            ("windows", {"HOME": "C:/Users/me", "XDG_DATA_HOME": "C:/xdg"}, "APPDATA is not set"),
            ("macos", {"XDG_DATA_HOME": "/xdg", "APPDATA": "/a"}, "HOME is not set"),
            ("linux", {"APPDATA": "/a"}, "XDG_DATA_HOME is not an absolute path and HOME"),
            ("linux", {"XDG_DATA_HOME": "rel", "XDG_CONFIG_HOME": "rel"}, "XDG_DATA_HOME"),
            ("linux", {"HOME": "", "XDG_CONFIG_HOME": "/config"}, "HOME is not set"),
        ):
            with (
                self.subTest(platform=platform, env=env),
                self.assertRaises(devtools.UsageError) as raised,
            ):
                devtools.godot_data_root(platform, env)
            self.assertIn(named, str(raised.exception))
            self.assertIn("user data folder cannot be found", str(raised.exception))

    def test_the_client_says_in_one_line_that_it_cannot_find_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = make_project(Path(temporary), 'config/name="Lost Game"')
            with patch.dict(devtools.os.environ, {}, clear=True):
                for command in (["ping"], ["logs"], ["wait", "--timeout", "1"]):
                    code, out, err = run_main("--project", str(project), *command)
                    self.assertEqual(code, devtools.EXIT_USAGE, command)
                    self.assertEqual(out, "")
                    self.assertEqual(len(err.splitlines()), 1, err)
                    self.assertRegex(err, r"^Error: .* is not set, so the game's user data")
                    self.assertIn("Run this in the environment the game was started in", err)

    def test_this_system_is_named_as_the_rest_of_the_kit_names_it(self) -> None:
        for reported, platform in (
            ("win32", "windows"),
            ("darwin", "macos"),
            ("linux", "linux"),
            ("freebsd14", "linux"),
        ):
            with patch.object(devtools.sys, "platform", reported):
                self.assertEqual(devtools.host_platform(), platform)

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

    def test_atomic_write_writes_again_when_a_starting_game_clears_the_folder(self) -> None:
        """The game empties its command folder as it starts, temporary files included."""
        target = self.user_data / "command.json"
        real_replace = devtools.os.replace
        sources = []

        def cleared_twice(source: str, destination: str) -> None:
            sources.append(Path(source).name)
            if len(sources) < 3:
                Path(source).unlink()
            real_replace(source, destination)

        with patch.object(devtools.os, "replace", side_effect=cleared_twice):
            devtools.atomic_write_json(target, {"action": "ping"})
        self.assertEqual(len(set(sources)), 3)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"action": "ping"})
        self.assertEqual([p.name for p in self.user_data.iterdir()], [target.name])

    def test_atomic_write_gives_up_on_a_file_that_keeps_vanishing(self) -> None:
        target = self.user_data / "command.json"
        real_replace = devtools.os.replace
        attempts = []

        def always_cleared(source: str, destination: str) -> None:
            attempts.append(source)
            Path(source).unlink()
            real_replace(source, destination)

        with (
            patch.object(devtools.os, "replace", side_effect=always_cleared),
            self.assertRaises(FileNotFoundError),
        ):
            devtools.atomic_write_json(target, {"action": "ping"})
        self.assertEqual(len(attempts), devtools.WRITE_ATTEMPTS)
        self.assertEqual(list(self.user_data.iterdir()), [])

        attempts.clear()
        with (
            patch.object(devtools.os, "replace", side_effect=always_cleared),
            self.assertRaises(FileNotFoundError),
        ):
            devtools.atomic_write_json(self.user_data / "gone" / "command.json", {"action": "ping"})
        self.assertEqual(attempts, [])

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
        paths["root"].mkdir()  # a game ran on this session, and is gone
        with self.assertRaisesRegex(devtools.NoResponseError, "GODOT_AGENT_SESSION='s3'") as raised:
            devtools.send_command(PROJECT_ROOT, "ping", timeout=0.2, session="s3")
        self.assertNotIsInstance(raised.exception, devtools.NoMailboxError)
        # Where the client looked: the game's own folder differs when its environment does.
        self.assertIn(f"Its mailbox would be {paths['root']}", str(raised.exception))
        self.assertEqual(list(paths["commands"].iterdir()), [])

    def test_a_session_no_game_has_used_is_answered_at_once_and_leaves_no_folder(self) -> None:
        """Only the game makes a mailbox: a client with a mistyped session, or with another
        environment than the game's, writes nothing anywhere."""
        missing = self.user_data / "not" / "made" / "yet"
        base = ("--project", str(PROJECT_ROOT), "--session", "nobody")
        for user_data in (self.user_data, missing):
            root = devtools.get_mailbox_paths(user_data, "nobody")["root"]
            with (
                patch.object(devtools, "get_user_data_path", return_value=user_data),
                patch.object(devtools, "atomic_write_json") as written,
                patch.object(devtools, "take_result", return_value=None) as polled,
            ):
                with self.assertRaises(devtools.NoMailboxError):
                    devtools.send_command(PROJECT_ROOT, "ping", timeout=0.2, session="nobody")
                ping = run_main(*base, "ping")
                sequence = run_main(*base, "input", "sequence", str(SMOKE_SEQUENCE))
            # Nothing is written and no reply is waited for.
            written.assert_not_called()
            polled.assert_not_called()
            for code, _out, err in (ping, sequence):
                self.assertEqual(code, devtools.EXIT_NO_RESPONSE)
                self.assertEqual(len(err.splitlines()), 1, err)
                self.assertIn("No response: no game has run with GODOT_AGENT_SESSION='nobody'", err)
                self.assertIn(f"its mailbox would be {root}, and that folder does not exist", err)
            self.assertEqual(ping[1], "")
        self.assertEqual(list(self.user_data.iterdir()), [])

    def test_wait_makes_no_mailbox_while_it_waits_for_the_game_to_make_one(self) -> None:
        base = ("--project", str(PROJECT_ROOT), "--session", "unborn")
        code, out, err = run_main(*base, "wait", "--timeout", "0.3")
        self.assertEqual((code, out), (devtools.EXIT_NO_RESPONSE, ""))
        root = devtools.get_mailbox_paths(self.user_data, "unborn")["root"]
        self.assertEqual(
            err,
            "No response: the game did not answer within 0.3s "
            f"(GODOT_AGENT_SESSION='unborn', mailbox {root}). "
            "If it was started, its output says why\n",
        )
        self.assertEqual(list(self.user_data.iterdir()), [])

    def test_a_mailbox_that_cannot_be_written_is_one_line_and_a_usage_error(self) -> None:
        paths = devtools.get_mailbox_paths(self.user_data, "locked")
        paths["commands"].mkdir(parents=True)
        base = ("--project", str(PROJECT_ROOT), "--session", "locked")
        refusal = PermissionError(13, "Permission denied", str(paths["commands"] / "x.tmp"))
        full = OSError(28, "No space left on device")
        for failure, reason in ((refusal, "Permission denied"), (full, "No space left on device")):
            with patch.object(devtools, "atomic_write_json", side_effect=failure):
                for command in (["ping"], ["wait", "--timeout", "30"], ["input", "clear"]):
                    code, out, err = run_main(*base, *command)
                    self.assertEqual((code, out), (devtools.EXIT_USAGE, ""), command)
                    self.assertEqual(
                        err, f"Error: cannot write the mailbox {paths['root']}: {reason}\n"
                    )

    def test_the_closing_commands_give_a_gone_game_five_seconds(self) -> None:
        """`input clear` and `quit` end every run of the loop, also after the game died:
        neither waits the 30 s another command may take."""
        asked: dict[str, float] = {}

        def silent(
            _project: Path,
            action: str,
            _args: dict | None = None,
            timeout: float = devtools.DEFAULT_TIMEOUT_SECONDS,
        ) -> dict:
            asked[action] = timeout
            raise devtools.NoResponseError(f"no reply to '{action}' after {timeout:g}s")

        base = ("--project", str(PROJECT_ROOT), "--session", "gone")
        with patch.object(devtools, "send_command", silent):
            for command in (["ping"], ["input", "clear"], ["quit"], ["performance"]):
                code, _out, err = run_main(*base, *command)
                self.assertEqual(code, devtools.EXIT_NO_RESPONSE, command)
                self.assertEqual(len(err.splitlines()), 1, err)
        self.assertEqual(asked, {"ping": 5.0, "input_clear": 5.0, "quit": 5.0, "performance": 30.0})

    def test_a_write_that_fails_leaves_no_temporary_file(self) -> None:
        target = self.user_data / "command.json"
        with (
            patch.object(devtools.json, "dump", side_effect=OSError(28, "No space left on device")),
            self.assertRaises(OSError),
        ):
            devtools.atomic_write_json(target, {"action": "ping"})
        self.assertEqual(list(self.user_data.iterdir()), [])

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
            # A capture is written after its step: the game marks the row of one that was
            # not, and keeps the name of the file that is missing.
            {
                "step": 4,
                "type": "screenshot",
                "status": "error",
                "error": "Failed to save screenshot c.png: Can't open file",
                "result": {"path": f"{folder}/c.png"},
            },
            {"step": 5, "type": "screenshot", "status": "error", "error": "Nothing to capture"},
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
                # Never "saved" for a file that is not there.
                self.assertIn(f"Screenshot not written: {folder}/c.png\n", err.getvalue())
                self.assertEqual(err.getvalue().count("Screenshot not written"), 1)
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


def test_logs_say_why_the_game_dropped_a_command(tmp_path: Path, monkeypatch, capsys) -> None:
    """The reason is in the row's data: without it `logs --category error` names no file."""
    log = devtools.get_mailbox_paths(tmp_path, "dropped")["log"]
    log.parent.mkdir(parents=True)
    dropped = "command file is not a JSON object: neverdone.json"
    rows = [
        {"category": "error", "message": "Failed to process command", "data": {"error": dropped}},
        {"category": "error", "message": "boom (res://a.gd:3)", "data": {"kind": "script"}},
        {"category": "system", "message": "DevTools initialized", "data": None},
    ]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(devtools, "get_user_data_path", lambda _project: tmp_path)
    assert devtools.main(["--project", str(PROJECT_ROOT), "--session", "dropped", "logs"]) == 0
    lines = [line.split("] ", 1)[1] for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        f"[error] Failed to process command: {dropped}",
        "[error] boom (res://a.gd:3)",
        "[system] DevTools initialized",
    ]


def test_a_command_folder_this_user_may_not_write_to_is_named_in_one_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The game of another user (a container, sudo): no traceback, and not the exit code
    of a command the game refused."""
    not_as_root()
    paths = devtools.get_mailbox_paths(tmp_path, "theirs")
    paths["commands"].mkdir(parents=True)
    paths["results"].mkdir()
    monkeypatch.setattr(devtools, "get_user_data_path", lambda _project: tmp_path)
    base = ["--project", str(PROJECT_ROOT), "--session", "theirs"]
    with takes_no_new_file(paths["commands"]):
        codes = [devtools.main([*base, "ping"]), devtools.main([*base, "wait", "--timeout", "30"])]
    said = capsys.readouterr()
    line = f"Error: cannot write the mailbox {paths['root']}: Permission denied\n"
    assert codes == [devtools.EXIT_USAGE] * 2
    assert (said.out, said.err) == ("", line * 2)
    assert list(paths["commands"].iterdir()) == []


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


# Prints OS.get_user_data_dir() for the project as it is, then for each row of cases.json.
USER_DIR_PROBE = """extends SceneTree


func _init() -> void:
\tvar found: Array = [OS.get_user_data_dir()]
\tvar cases: Array = JSON.parse_string(FileAccess.get_file_as_string("res://cases.json"))
\tfor case: Array in cases:
\t\tProjectSettings.set_setting("application/config/name", case[0])
\t\tProjectSettings.set_setting("application/config/use_custom_user_dir", case[1] != null)
\t\tProjectSettings.set_setting(
\t\t\t"application/config/custom_user_dir_name", "" if case[1] == null else case[1]
\t\t)
\t\tfound.append(OS.get_user_data_dir())
\tprint("USER_DIRS " + JSON.stringify(found))
\tquit(0)
"""
PROBE_PROJECT_NAME = 'Probe: the "file"?...'


def godot_user_dirs(project: Path, env: dict[str, str]) -> list[Path]:
    """What a real Godot started with `env` calls the user data folder of `project`: as the
    project file names it, then for each row of USER_DIR_CASES."""
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    completed = subprocess.run(
        [godot, "--headless", "--path", str(project), "--script", "res://probe.gd"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("USER_DIRS ")]
    assert completed.returncode == 0 and len(lines) == 1, completed.stdout + completed.stderr
    return [Path(folder) for folder in json.loads(lines[0].removeprefix("USER_DIRS "))]


def probe_project(folder: Path) -> Path:
    project = make_project(folder, 'config/name="' + PROBE_PROJECT_NAME.replace('"', '\\"') + '"')
    (project / "probe.gd").write_text(USER_DIR_PROBE, encoding="utf-8", newline="\n")
    rows = [[name, custom] for name, custom, _folders in USER_DIR_CASES]
    (project / "cases.json").write_text(json.dumps(rows), encoding="utf-8")
    return project


@pytest.mark.real_godot
def test_godot_names_its_user_data_folder_as_the_client_does(tmp_path: Path) -> None:
    """The client and the game meet in a folder each of them works out alone. The variables
    the other systems read are set too, and must move nothing."""
    project = probe_project(tmp_path)
    decoy = str(tmp_path / "decoy")
    own = user_data_env(tmp_path / "user data")
    env = clean_env(**({"APPDATA": decoy, "XDG_DATA_HOME": decoy, "HOME": decoy} | own))
    found = godot_user_dirs(project, env)

    root = devtools.godot_data_root(PLATFORM, env)
    assert root.is_relative_to(tmp_path / "user data")
    assert found[0] == devtools.get_user_data_path(project, env=env)
    assert found[0] == expected_folder(root, PLATFORM, (GODOT_DIR, "Probe- the -file--"))
    assert len(found) == len(USER_DIR_CASES) + 1
    for (name, custom, folders), folder in zip(USER_DIR_CASES, found[1:], strict=True):
        case = f"name {name!r}, custom folder {custom!r}"
        assert folder == expected_folder(root, PLATFORM, folders), case
        settings = application_settings(name, custom)
        assert folder == devtools.user_data_folder(settings, PLATFORM, env), case
    # Nothing went to the folders the other systems' variables name.
    assert not (tmp_path / "decoy" / GODOT_DIRS[PLATFORM]).exists()


@linux_only("XDG_DATA_HOME, and what Godot does without a usable one")
@pytest.mark.real_godot
@pytest.mark.parametrize(
    "variables",
    [
        {"XDG_DATA_HOME": "relative/data", "HOME": "<home>"},
        {"XDG_DATA_HOME": "", "HOME": "<home>"},
        {"HOME": "<home>"},
        {"XDG_CONFIG_HOME": "<home>/settings"},
    ],
    ids=["relative", "empty", "unset", "no-home"],
)
def test_godot_and_the_client_fall_back_on_the_same_folder(
    tmp_path: Path, variables: dict[str, str]
) -> None:
    project = probe_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = clean_env()
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "HOME"):
        env.pop(name, None)
    env |= {name: value.replace("<home>", str(home)) for name, value in variables.items()}
    found = godot_user_dirs(project, env)
    assert found[0] == devtools.get_user_data_path(project, env=env)
    assert found[0].is_relative_to(home)


# Saves a project.godot per row of cases.json (name, custom folder or null) below
# res://written/<row>/, and prints OS.get_user_data_dir() for each.
WRITTEN_PROBE = """extends SceneTree


func _init() -> void:
\tvar found: Array = []
\tvar cases: Array = JSON.parse_string(FileAccess.get_file_as_string("res://cases.json"))
\tfor row: int in range(cases.size()):
\t\tvar case: Array = cases[row]
\t\tProjectSettings.set_setting("application/config/name", case[0])
\t\tProjectSettings.set_setting("application/config/use_custom_user_dir", case[1] != null)
\t\tProjectSettings.set_setting(
\t\t\t"application/config/custom_user_dir_name", "" if case[1] == null else case[1]
\t\t)
\t\tvar folder: String = ProjectSettings.globalize_path("res://written/%d" % row)
\t\tDirAccess.make_dir_recursive_absolute(folder)
\t\tif ProjectSettings.save_custom(folder.path_join("project.godot")) != OK:
\t\t\tquit(1)
\t\t\treturn
\t\tfound.append(OS.get_user_data_dir())
\tprint("USER_DIRS " + JSON.stringify(found))
\tquit(0)
"""
# Names whose text in a project file is not the name: (config/name, custom folder or None).
WRITTEN_NAMES: tuple[tuple[str, str | None], ...] = (
    ('tab\tinside "q"', None),
    ("line1\nline2", None),
    ("back\\slash\\", None),
    ('semi; "colon" = [x]', None),
    ("café 日 \U0001f600", None),
    ("\\u0041 \\n is no escape", None),
    ("My Game", 'cus\ttom "dir"\\x'),
)


@pytest.mark.real_godot
def test_the_client_reads_a_project_file_as_godot_wrote_it(tmp_path: Path) -> None:
    """Godot writes a name with only `"` and `\\` escaped: a tab or a line break stands in
    the file as it is. The client reads the folder's name out of that file."""
    project = make_project(tmp_path, 'config/name="Writer"')
    (project / "probe.gd").write_text(WRITTEN_PROBE, encoding="utf-8", newline="\n")
    (project / "cases.json").write_text(json.dumps(WRITTEN_NAMES), encoding="utf-8")
    env = clean_env(**user_data_env(tmp_path / "user data"))
    found = godot_user_dirs(project, env)
    assert len(found) == len(WRITTEN_NAMES)
    for row, ((name, custom), folder) in enumerate(zip(WRITTEN_NAMES, found, strict=True)):
        written = project / "written" / str(row)
        settings = devtools.read_application_settings(written / "project.godot")
        assert settings["config/name"] == name
        assert settings.get("config/custom_user_dir_name", "") == (custom or "")
        assert folder == devtools.get_user_data_path(written, env=env), repr((name, custom))


@pytest.mark.real_godot
def test_the_sample_sequence_passes_against_the_sample_game(tmp_path: Path) -> None:
    """test/sequences/smoke.json is the first thing an agent is told to run. A headless
    game has no frame to capture, so every step before its screenshot runs here; with a
    display the last step is the picture."""
    steps = json.loads(SMOKE_SEQUENCE.read_text(encoding="utf-8"))["steps"]
    last = len(steps) - 1
    assert [step["type"] for step in steps].index("screenshot") == last
    sequence = tmp_path / "smoke_before_the_screenshot.json"
    sequence.write_text(json.dumps({"steps": steps[:last]}), encoding="utf-8")
    with running_game(tmp_path, "smoke", build=sample_game) as game:
        ready = game.client("wait", "--timeout", "90")
        assert ready.returncode == 0, ready.stdout + ready.stderr + game.output()
        ran = game.client("input", "sequence", str(sequence))
        assert ran.returncode == 0, ran.stdout + ran.stderr + game.output()
        assert f"Sequence passed: {last}/{last} steps" in ran.stdout
        # The file as it is: it gets as far as the capture, which says why it cannot.
        whole = game.client("input", "sequence", str(SMOKE_SEQUENCE))
        assert whole.returncode == 1, whole.stdout + whole.stderr
        failed = f"failed at step {last} of {last + 1} (counted from 0): Nothing to capture"
        assert failed in whole.stderr and "Screenshot" not in whole.stdout
        valid = game.client("validate-all", "--strict")
        assert valid.returncode == 0, valid.stdout + valid.stderr
        assert game.client("input", "clear").returncode == 0
        assert game.client("quit").returncode == 0
        assert game.process.wait(timeout=60) == 0, game.output()


@pytest.mark.real_godot
def test_the_client_drives_a_real_game_on_this_system(tmp_path: Path) -> None:
    """The runtime loop end to end, headless: the client finds the mailbox of a real game,
    drives it, and the game ends when it is told to."""
    sequence = tmp_path / "press.json"
    steps = [
        {"type": "tap", "action": "ui_accept"},
        {"type": "assert", "node": ".", "property": "presses", "equals": 2},
    ]
    sequence.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    with running_game(tmp_path, "loop") as game:
        ready = game.client("wait", "--timeout", "90")
        assert ready.returncode == 0, ready.stdout + ready.stderr + game.output()
        ping = game.client("ping", "--json")
        assert ping.returncode == 0, ping.stdout + ping.stderr
        told = json.loads(ping.stdout)["data"]
        # The folder the game uses is the one the client worked out, below the test's own.
        user_dir = Path(told["user_dir"])
        assert user_dir == devtools.get_user_data_path(game.project, env=game.env)
        assert user_dir.is_relative_to(game.user_data)
        assert user_dir.name == "Loop- the -game--"
        assert Path(told["mailbox"]) == user_dir / "devtools_loop"
        assert Path(told["screenshots"]) == user_dir / "screenshots" / "loop"
        assert told["session"] == "loop" and told["headless"] is True

        tapped = game.client("input", "tap", "ui_accept")
        assert (tapped.returncode, tapped.stdout) == (0, "Tapped: ui_accept\n"), tapped.stderr
        state = json.loads(game.client("get-state", "--json").stdout)
        assert state["data"]["presses"] == 1
        ran = game.client("input", "sequence", str(sequence))
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert "Sequence passed: 2/2 steps" in ran.stdout
        valid = game.client("validate-all")
        assert valid.returncode == 0, valid.stdout + valid.stderr
        assert "[OK] Scenes valid: 1 checked, 0 errors, 0 warnings" in valid.stdout
        # A headless game renders no frame: the capture fails and says why.
        shot = game.client("screenshot", "--filename", "nothing.png")
        assert shot.returncode == 1 and shot.stdout == ""
        assert "Nothing to capture: this run renders no frames (headless)" in shot.stderr
        assert game.client("input", "clear").returncode == 0

        quit_reply = game.client("quit", "--exit-code", "7")
        assert quit_reply.returncode == 0, quit_reply.stdout + quit_reply.stderr
        assert game.process.wait(timeout=60) == 7, game.output()
        # Nothing waits in the mailbox, and a client that comes late is told where it looked.
        mailbox = devtools.get_user_data_path(game.project, env=game.env) / "devtools_loop"
        assert list((mailbox / "commands").iterdir()) == []
        assert list((mailbox / "results").iterdir()) == []
        late = game.client("wait", "--timeout", "1")
        assert late.returncode == devtools.EXIT_NO_RESPONSE
        assert f"(GODOT_AGENT_SESSION='loop', mailbox {mailbox})" in late.stderr
        assert list((mailbox / "commands").iterdir()) == []


if __name__ == "__main__":
    unittest.main()
