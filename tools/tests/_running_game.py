"""A real game for the DevTools tests: a throwaway project with the addon, run headless.

    with running_game(tmp_path, "session-1") as game:
        assert game.client("wait").returncode == 0

    with running_game(tmp_path, "session-2", build=sample_game) as game:   # the kit's game

The game keeps its user data (the mailbox too) below the test's own folder, on every
system. A test that uses it carries `@pytest.mark.real_godot`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from _gate_fixtures import (
    DEVTOOLS_AUTOLOAD,
    REPO,
    WINDOWS,
    clean_env,
    real_godot,
    user_data_env,
    write,
)

DEVTOOLS = REPO / "tools" / "devtools.py"
ADDON = Path("addons") / "agent_devtools"
# What the sample game is made of. The editor plugins project.godot names are not: a game
# loads none, and the import only says that one is missing.
SAMPLE_FILES = ("project.godot", "main.tscn", "icon.svg", "icon.svg.import")
SAMPLE_FOLDERS = ("scripts", str(ADDON))
# A name Godot has to change to make a folder of it: the client must change it the same way.
PROJECT_NAME = 'Loop: the "game"?...'

PROJECT = """config_version=5

[application]

config/name="{name}"
run/main_scene="res://main.tscn"

[autoload]

{autoloads}
"""

MAIN_SCENE = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://main.gd" id="1"]

[node name="Main" type="Node2D"]
script = ExtResource("1")
"""

MAIN_SCRIPT = """extends Node2D

var presses: int = 0


func _unhandled_input(event: InputEvent) -> void:
\tif event.is_action_pressed("ui_accept"):
\t\tpresses += 1
"""


@dataclass
class Game:
    project: Path
    session: str
    #: The environment of the game; a client needs the same one to find its user data.
    env: dict[str, str]
    user_data: Path
    process: subprocess.Popen[bytes]
    log: Path

    def client(self, *arguments: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        """Run tools/devtools.py against this game, as a shell would."""
        return subprocess.run(
            [
                sys.executable,
                str(DEVTOOLS),
                "--project",
                str(self.project),
                "--session",
                self.session,
                *arguments,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )

    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")


def stop(process: subprocess.Popen[bytes]) -> None:
    """End a game that did not quit. On Windows the console program is a wrapper around
    the game, so the tree is ended, by id."""
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        process.kill()
    process.wait(timeout=30)


def throwaway_game(project: Path, _godot: str, _env: dict[str, str]) -> None:
    """A scene that counts presses of ui_accept, under PROJECT_NAME."""
    autoloads = "\n".join(f'{name}="{path}"' for name, path in DEVTOOLS_AUTOLOAD.items())
    name = PROJECT_NAME.replace('"', '\\"')
    write(project / "project.godot", PROJECT.format(name=name, autoloads=autoloads))
    write(project / "main.tscn", MAIN_SCENE)
    write(project / "main.gd", MAIN_SCRIPT)
    shutil.copytree(REPO / ADDON, project / ADDON, ignore=shutil.ignore_patterns("*.uid"))


def sample_game(project: Path, godot: str, env: dict[str, str]) -> None:
    """The kit's sample game, as its files are, imported: its scripts name each other by
    class, which Godot looks up in the cache an import writes."""
    project.mkdir(parents=True)
    for name in SAMPLE_FILES:
        shutil.copyfile(REPO / name, project / name)
    for name in SAMPLE_FOLDERS:
        shutil.copytree(REPO / name, project / name)
    imported = subprocess.run(
        [godot, "--headless", "--path", str(project), "--import"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    cache = project / ".godot" / "global_script_class_cache.cfg"
    assert imported.returncode == 0 and cache.is_file(), imported.stdout + imported.stderr


@contextmanager
def running_game(
    folder: Path, session: str, build: Callable[[Path, str, dict[str, str]], None] = throwaway_game
) -> Iterator[Game]:
    """Start a headless game in `folder`/project with DevTools on `session`; it is ended
    when the block is left, however the test went. `build` writes the project."""
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    project = folder / "project"
    user_data = folder / "user data"
    env = clean_env(GODOT_AGENT_SESSION=session, **user_data_env(user_data))
    build(project, godot, env)
    log = folder / "game.log"
    with log.open("wb") as output:
        process = subprocess.Popen(
            [godot, "--headless", "--path", str(project)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            yield Game(project, session, env, user_data, process, log)
        finally:
            stop(process)
