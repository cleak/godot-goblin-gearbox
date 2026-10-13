"""tools/godot.ps1: Godot's exit code comes back, whichever of its two binaries runs."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from _gate_fixtures import (
    EMPTY_CLASS_CACHE,
    NEEDS_PWSH,
    REPO,
    clean_env,
    copy_tools,
    fake_godot,
    linux_only,
    lone_gui_godot,
    project_godot,
    real_godot,
    run_powershell,
    run_tool,
    user_data_env,
    windows_only,
    write,
    write_kit_json,
)

pytestmark = NEEDS_PWSH

QUIT_7 = 'extends SceneTree\n\n\nfunc _init() -> void:\n\tprint("QUIT_7_RAN")\n\tquit(7)\n'
SAID = (
    "extends SceneTree\n\n\nfunc _init() -> void:\n"
    '\tprint("SAID ", JSON.stringify(OS.get_cmdline_user_args()))\n\tquit(0)\n'
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    copy_tools(root, "godot.ps1", "export_paths.ps1")
    write_kit_json(root)
    write(root / "project.godot", project_godot(main_scene="res://main.tscn"))
    write(root / "quit_7.gd", QUIT_7)
    write(root / ".godot" / "global_script_class_cache.cfg", EMPTY_CLASS_CACHE)
    return root


def _log(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def test_exit_code_and_arguments_are_forwarded(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))

    ok = run_tool(root, "godot.ps1", "--headless", "--quit", env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "FAKE_GODOT --headless --quit" in ok.stdout  # Godot's output is shown

    failing = env | {"FAKE_GODOT_BREAK": "lint.gd", "FAKE_GODOT_EXIT": "7"}
    args = ("--headless", "--script", "res://lint.gd", "--user-args", "--all", "a b")
    failed = run_tool(root, "godot.ps1", *args, env=failing)
    assert failed.returncode == 7, failed.stdout + failed.stderr
    # --user-args became the bare -- that PowerShell would have swallowed.
    assert _log(log)[-1].split() == [
        "--headless",
        "--script",
        "res://lint.gd",
        "--",
        "--all",
        '"a',
        'b"',
    ]

    twice = run_tool(root, "godot.ps1", "--user-args", "a", "--user-args", "b", env=env)
    assert twice.returncode == 1 and "--user-args may be supplied only once" in twice.stdout

    # An argument with a space stays one argument, on either side of the --.
    spaced = run_tool(root, "godot.ps1", "--headless", "a b", "it's", "--user-args", "x y", env=env)
    assert spaced.returncode == 0, spaced.stdout + spaced.stderr
    assert _log(log)[-1] == '--headless "a b" it\'s -- "x y"'


def test_output_stays_on_the_pipeline_unless_an_argument_would_be_lost(tmp_path: Path) -> None:
    """`$said = & ./tools/godot.ps1 --version` in a session holds what Godot printed: the
    call operator keeps it on the pipeline. Before PowerShell 7.3 that operator loses a
    double quote inside an argument and drops an empty one: only such a run goes another
    way, and writes to the console."""
    root = _project(tmp_path)
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), KIT_PROBE_ROOT=str(root))
    held = run_powershell(
        "$said = & (Join-Path $env:KIT_PROBE_ROOT 'tools/godot.ps1') --headless --quit 'a b'; "
        "'held<' + ($said -join '|') + '>' + $LASTEXITCODE",
        env=env,
    )
    assert held.returncode == 0, held.stdout + held.stderr
    assert held.stdout.strip() == 'held<FAKE_GODOT --headless --quit "a b">0'

    plain = "@('--headless', 'a b', \"it's\", 'C:\\x y\\')"
    rule = run_powershell(
        f". '{REPO / 'tools' / 'godot_resolver.ps1'}'; "
        "@(foreach ($version in '7.0.13', '7.2.24', '7.3.0', '7.6.6') { "
        f"(Test-KitArgumentLoss -Arguments {plain} -Version $version); "
        f"(Test-KitArgumentLoss -Arguments ({plain} + 'say \"hi\"') -Version $version); "
        f"(Test-KitArgumentLoss -Arguments ({plain} + '') -Version $version) "
        "}) | ConvertTo-Json -Compress"
    )
    assert rule.returncode == 0, rule.stdout + rule.stderr
    lost_before_7_3 = [False, True, True]
    assert json.loads(rule.stdout) == lost_before_7_3 * 2 + [False] * 6
    # The check runs a PowerShell that never takes the other way: the wrapper must ask.
    wrapper = (REPO / "tools" / "godot.ps1").read_text(encoding="utf-8")
    assert "(Test-KitArgumentLoss -Arguments $godotArgs)" in wrapper


@pytest.mark.parametrize("switch", ["-?", "-h", "/?"])
def test_a_lone_help_switch_prints_usage_and_starts_no_game(tmp_path: Path, switch: str) -> None:
    """Every argument is Godot's, and Godot given `-?` opens the game window and stays."""
    root = _project(tmp_path)
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))
    asked = run_tool(root, "godot.ps1", switch, env=env)
    assert asked.returncode == 0, asked.stdout + asked.stderr
    assert asked.stdout.startswith("usage: pwsh ./tools/godot.ps1 [godot arguments]")
    assert "--help: Godot's own options" in asked.stdout
    assert _log(log) == []


@linux_only("a display is two environment variables there")
def test_a_window_without_a_display_is_one_line_and_starts_nothing(tmp_path: Path) -> None:
    """Over ssh Godot ends in a page of display driver errors: the wrapper says what to do."""
    root = _project(tmp_path)
    cache = root / ".godot" / "global_script_class_cache.cfg"
    cache.unlink()  # an import is due: it must not be started either
    log = tmp_path / "godot.log"
    blind = clean_env(
        GODOT_BIN=str(fake_godot(tmp_path / "bin")),
        FAKE_GODOT_LOG=str(log),
        FAKE_GODOT_CACHE=str(cache),
        DISPLAY="",
        WAYLAND_DISPLAY="",
    )
    for arguments in ((), ("-e",), ("--script", "res://quit_7.gd", "--user-args", "--headless")):
        refused = run_tool(root, "godot.ps1", *arguments, env=blind)
        assert refused.returncode == 1, refused.stdout + refused.stderr
        said = refused.stdout.strip().splitlines()
        assert len(said) == 1 and said[0].startswith("godot.ps1: no display: "), refused.stdout
        assert "Add --headless" in said[0] and "xvfb-run -a pwsh ./tools/godot.ps1" in said[0]
        assert refused.stderr.strip() == "" and _log(log) == []

    # Headless needs none; with a display the game is started.
    headless = run_tool(root, "godot.ps1", "--headless", "--quit", env=blind)
    assert headless.returncode == 0, headless.stdout + headless.stderr
    assert _log(log) == ["--headless --import", "--headless --quit"]
    log.unlink()
    for variable in ("DISPLAY", "WAYLAND_DISPLAY"):
        shown = run_tool(root, "godot.ps1", "-e", env=blind | {variable: ":99"})
        assert shown.returncode == 0, shown.stdout + shown.stderr
    assert _log(log) == ["-e", "-e"]


def test_a_project_without_a_main_scene_starts_no_game(tmp_path: Path) -> None:
    """Godot says "no main scene" in a dialog and waits for a click, with --headless too:
    in a gate or an agent's shell that is a hang. The wrapper says it in one line."""
    root = _project(tmp_path)
    write(root / "project.godot", project_godot())
    log = tmp_path / "godot.log"
    env = clean_env(
        GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log), DISPLAY=":99"
    )
    games = (
        (),
        ("--headless",),
        ("--headless", "--user-args", "--devtools-validate"),
        ("--rendering-method", "gl_compatibility", "--quit-after", "3", "--disable-vsync"),
    )
    # Said first: with a display or without, there is no game.
    blind = env | {"DISPLAY": "", "WAYLAND_DISPLAY": ""}
    for arguments in games:
        refused = run_tool(root, "godot.ps1", *arguments, env=blind)
        assert refused.returncode == 1, refused.stdout + refused.stderr
        said = refused.stdout.strip().splitlines()
        assert len(said) == 1 and said[0].startswith("godot.ps1: no main scene: "), refused.stdout
        assert "application/run/main_scene" in said[0] and "res://main.tscn" in said[0]
        assert refused.stderr.strip() == "" and _log(log) == []

    # Anything but the game is Godot's to answer: the editor, a script, a scene, an import,
    # and every argument the wrapper does not know.
    others = (
        ("-e",),
        ("--headless", "--script", "res://quit_7.gd"),
        ("--headless", "res://level.tscn"),
        ("--headless", "--import"),
        ("--headless", "--some-later-switch"),
    )
    for arguments in others:
        started = run_tool(root, "godot.ps1", *arguments, env=env)
        assert started.returncode == 0, started.stdout + started.stderr
        assert _log(log)[-1] == " ".join(arguments)

    # A main scene for one feature, or in override.cfg, is a main scene.
    feature = project_godot().replace(
        'config/name="Gate fixture"',
        'config/name="Gate fixture"\nrun/main_scene.mobile="res://touch.tscn"',
    )
    write(root / "project.godot", feature)
    assert run_tool(root, "godot.ps1", "--headless", env=env).returncode == 0
    write(root / "project.godot", project_godot())
    write(root / "override.cfg", '[application]\n\nrun/main_scene="res://main.tscn"\n')
    assert run_tool(root, "godot.ps1", "--headless", env=env).returncode == 0
    # An empty value names none.
    write(root / "override.cfg", '[application]\n\nrun/main_scene=""\n')
    assert run_tool(root, "godot.ps1", "--headless", env=env).returncode == 1


def test_a_line_inside_a_string_is_no_header_and_no_entry(tmp_path: Path) -> None:
    """A description may run over several lines, and one of them may look like a section
    header or an entry. The wrapper reads the main scene as tools/devtools.py does."""
    spec = importlib.util.spec_from_file_location("kit_devtools_reader", REPO / "tools/devtools.py")
    assert spec is not None and spec.loader is not None
    devtools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(devtools)
    scene = 'run/main_scene="res://main.tscn"'
    cases = {
        "header": ['config/description="A small game.\n[WIP]\nNot done yet."', scene],
        "entry": [
            'config/description="One\n[autoload]\nrun/main_scene=\\"res://no.tscn\\"\n"',
            scene,
        ],
        "only inside": ['config/description="One\nrun/main_scene=\\"res://no.tscn\\"\nend"'],
        "comment": ['; a comment with one " quote', scene, "; [debug]"],
        "semicolon": ['config/description="semi;colon" ; a "comment', scene],
        "after": [scene, 'config/description="then\n[application]\nrun/main_scene=\\"\\"\n"'],
    }
    expected = {}
    for name, entries in cases.items():
        text = project_godot().replace('config/name="Gate fixture"', "\n".join(entries))
        file = tmp_path / name / "project.godot"
        file.parent.mkdir()
        file.write_text(text, encoding="utf-8", newline="\n")
        expected[name] = devtools.read_application_settings(file).get("run/main_scene", "")
    assert expected["header"] == "res://main.tscn" and expected["only inside"] == ""

    asked = run_powershell(
        f". '{REPO / 'tools' / 'kit_config.ps1'}'; $scenes = [ordered]@{{}}; "
        "foreach ($folder in Get-ChildItem -LiteralPath $env:KIT_PROBE_ROOT -Directory) { "
        "$scenes[$folder.Name] = Get-KitMainScene -ProjectRoot $folder.FullName }; "
        "$scenes | ConvertTo-Json -Compress",
        env=clean_env(KIT_PROBE_ROOT=str(tmp_path)),
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    assert json.loads(asked.stdout) == expected

    # And the wrapper starts that game.
    root = _project(tmp_path)
    shutil.copyfile(tmp_path / "header" / "project.godot", root / "project.godot")
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))
    started = run_tool(root, "godot.ps1", "--headless", "--quit", env=env)
    assert started.returncode == 0, started.stdout + started.stderr
    assert _log(log) == ["--headless --quit"]


def test_stale_class_cache_is_refreshed_first(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write(root / "scripts" / "added.gd", "class_name Added\nextends RefCounted\n")
    log = tmp_path / "godot.log"
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")), FAKE_GODOT_LOG=str(log))

    result = run_tool(root, "godot.ps1", "--headless", "--quit", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Class cache is stale" in result.stdout
    assert _log(log)[0] == "--headless --import"
    assert _log(log)[-1] == "--headless --quit"

    # An explicit import is the refresh: no second one in front of it.
    log.unlink()
    imported = run_tool(root, "godot.ps1", "--headless", "--import", env=env)
    assert imported.returncode == 0, imported.stdout + imported.stderr
    assert _log(log) == ["--headless --import"]


def test_wrapper_problems_are_one_line_and_exit_1(tmp_path: Path) -> None:
    root = _project(tmp_path)
    missing = run_tool(root, "godot.ps1", env=clean_env(GODOT_BIN=str(tmp_path / "gone.exe")))
    assert missing.returncode == 1, missing.stdout + missing.stderr
    assert "godot.ps1: GODOT_BIN does not name an executable file" in missing.stdout
    assert missing.stderr.strip() == ""  # no PowerShell error record around it

    (root / "project.godot").unlink()
    env = clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")))
    no_project = run_tool(root, "godot.ps1", env=env)
    assert no_project.returncode == 1 and "Godot project not found" in no_project.stdout


@pytest.mark.real_godot
def test_real_godot_exit_code_through_the_console_wrapper(tmp_path: Path) -> None:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    root = _project(tmp_path)
    own = user_data_env(tmp_path / "user data")
    args = ("--headless", "--script", "res://quit_7.gd")
    result = run_tool(root, "godot.ps1", *args, env=clean_env(GODOT_BIN=godot, **own))
    assert result.returncode == 7, result.stdout + result.stderr
    assert "QUIT_7_RAN" in result.stdout

    # Naming the windowed .exe of the same install is the same thing.
    if godot.lower().endswith("_console.exe"):
        windowed = godot[: -len("_console.exe")] + ".exe"
        result = run_tool(root, "godot.ps1", *args, env=clean_env(GODOT_BIN=windowed, **own))
        assert result.returncode == 7, result.stdout + result.stderr
        assert "QUIT_7_RAN" in result.stdout

    # What follows --user-args reaches the script argument by argument.
    write(root / "said.gd", SAID)
    given = ["a b", "it's", 'say "hi"', "--headless", "res://x y.gd"]
    args = ("--headless", "--script", "res://said.gd", "--user-args", *given)
    result = run_tool(root, "godot.ps1", *args, env=clean_env(GODOT_BIN=godot, **own))
    assert result.returncode == 0, result.stdout + result.stderr
    said = [line[5:] for line in result.stdout.splitlines() if line.startswith("SAID ")]
    assert [json.loads(line) for line in said] == [given], result.stdout


@windows_only("Godot's windowed .exe, a GUI-subsystem program")
@pytest.mark.real_godot
def test_real_windowed_godot_alone_is_waited_for(tmp_path: Path) -> None:
    """The windowed .exe is a GUI-subsystem program: `& godot.exe` returns at once, exit code
    unset. The wrapper waits for it and passes its exit code on."""
    windowed = lone_gui_godot(tmp_path / "flat")
    if windowed is None:
        pytest.skip("no real Godot .exe + _console.exe pair resolves here")
    root = _project(tmp_path)
    result = run_tool(
        root,
        "godot.ps1",
        "--headless",
        "--script",
        "res://quit_7.gd",
        env=clean_env(GODOT_BIN=str(windowed), **user_data_env(tmp_path / "user data")),
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert "QUIT_7_RAN" in result.stdout  # it ran to the end before the wrapper returned
