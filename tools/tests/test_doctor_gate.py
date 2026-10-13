"""tools/doctor.ps1: a ready project passes; each missing prerequisite fails with its fix."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from _gate_fixtures import (
    DEVTOOLS_AUTOLOAD,
    PWSH,
    REPO,
    WINDOWS,
    clean_env,
    copy_tools,
    fake_godot,
    lone_gui_godot,
    project_godot,
    ready_project,
    run_tool,
    write,
    write_kit_json,
)

pytestmark = pytest.mark.skipif(
    not WINDOWS or PWSH is None or shutil.which("uv") is None,
    reason="Windows PowerShell 7 tools with uv installed",
)


DEVTOOLS = DEVTOOLS_AUTOLOAD


def _ready_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "project"
    copy_tools(root, "doctor.ps1")
    ready_project(root)
    return root, clean_env(GODOT_BIN=str(fake_godot(tmp_path / "bin")))


def test_ready_project_passes(tmp_path: Path) -> None:
    root, env = _ready_project(tmp_path)
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[FAIL]" not in result.stdout and "[warn]" not in result.stdout
    assert "[ok]   Godot 4.7.2.stable.fake" in result.stdout
    assert env["GODOT_BIN"] in result.stdout  # the path is printed
    assert "[ok]   strict GDScript warnings (16 set to error)" in result.stdout
    assert "[ok]   gdUnit4 6.2.2 (matches kit.json)" in result.stdout
    assert "doctor: READY" in result.stdout


def _weak_warning(root: Path) -> None:
    project = root / "project.godot"
    text = project.read_text()
    text = text.replace("gdscript/warnings/unsafe_cast=2", "gdscript/warnings/unsafe_cast=1")
    project.write_text(text.replace("gdscript/warnings/untyped_declaration=2\n", ""))


def _no_strict_block(root: Path) -> None:
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS, strict=False))


def _old_gdunit(root: Path) -> None:
    write(root / "addons" / "gdUnit4" / "plugin.cfg", '[plugin]\n\nversion="5.0.0"\n')


def _no_gdunit(root: Path) -> None:
    shutil.rmtree(root / "addons" / "gdUnit4")


def _no_autoload(root: Path) -> None:
    write(root / "project.godot", project_godot())


def _stale_cache(root: Path) -> None:
    write(root / "scripts" / "added.gd", "class_name Added\nextends RefCounted\n")


def _no_cache(root: Path) -> None:
    shutil.rmtree(root / ".godot")


def _no_gdformat(root: Path) -> None:
    (root / ".venv" / "Scripts" / "gdformat.exe").unlink()


def _own_pyproject(root: Path) -> None:
    """The project's own pyproject.toml: `uv sync` installs what it names, not gdtoolkit."""
    write(root / "pyproject.toml", '[project]\nname = "mine"\nversion = "1"\n')
    _no_gdformat(root)


def _broken_kit_json(root: Path) -> None:
    write(root / "kit.json", "{ not json")


def _no_kit_json(root: Path) -> None:
    (root / "kit.json").unlink()


DEFECTS: list[tuple[Callable[[Path], None], str, str]] = [
    (_weak_warning, "strict GDScript warnings: 2 of 16 are not errors", "untyped_declaration"),
    (_no_strict_block, "strict GDScript warnings: 16 of 16 are not errors", "[debug]"),
    (
        _old_gdunit,
        "gdUnit4 5.0.0 is installed; kit.json pins 6.2.2",
        'keep yours: set gdunit4.version to "5.0.0" in kit.json. Or replace it (local edits in '
        "addons/gdUnit4 are lost): uv run python tools/install_gdunit4.py --force",
    ),
    (_no_gdunit, "gdUnit4 is not installed", "install_gdunit4.py"),
    (_no_autoload, "DevTools autoload is not registered", "[autoload]"),
    (_stale_cache, "class cache is stale (1 difference(s))", "godot.ps1 --headless --import"),
    (_no_cache, "class cache is stale", "godot.ps1 --headless --import"),
    (_no_gdformat, "gdformat not in .venv", "fix: uv sync"),
    (
        _own_pyproject,
        "gdformat not in .venv: pyproject.toml does not name gdtoolkit",
        'fix: uv add --dev "gdtoolkit==',
    ),
    (_broken_kit_json, "kit.json is not valid JSON", "kit.json"),
    (_no_kit_json, "kit.json is missing", "kit.json"),
]


@pytest.mark.parametrize(
    ("seed", "failure", "fix"), DEFECTS, ids=[seed.__name__.lstrip("_") for seed, _, _ in DEFECTS]
)
def test_each_missing_prerequisite_fails_with_its_fix(
    tmp_path: Path, seed: Callable[[Path], None], failure: str, fix: str
) -> None:
    root, env = _ready_project(tmp_path)
    seed(root)
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    failed = [index for index, line in enumerate(lines) if line.startswith("[FAIL]")]
    assert len(failed) == 1, result.stdout
    assert failure in lines[failed[0]]
    fix_lines = [line for line in lines[failed[0] + 1 :] if line.strip().startswith("fix:")]
    assert fix_lines and fix in fix_lines[0], result.stdout
    assert "doctor: NOT READY" in result.stdout or "kit.json must be readable" in result.stdout


def test_missing_godot_names_the_installer(tmp_path: Path) -> None:
    root, env = _ready_project(tmp_path)
    result = run_tool(root, "doctor.ps1", env=env | {"GODOT_BIN": str(tmp_path / "gone.exe")})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[FAIL] GODOT_BIN does not name an executable file" in result.stdout
    assert "fix: pwsh ./tools/install_godot.ps1" in result.stdout


def test_optional_parts_warn_without_failing(tmp_path: Path) -> None:
    root, env = _ready_project(tmp_path)
    shutil.rmtree(root / "addons" / "agent_devtools")
    write(root / "project.godot", project_godot())
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[warn] DevTools addon not installed" in result.stdout
    assert "1 warning(s), 0 failed" in result.stdout


def test_another_patch_of_the_pinned_minor_is_a_warning(tmp_path: Path) -> None:
    root, env = _ready_project(tmp_path)
    result = run_tool(root, "doctor.ps1", env=env | {"FAKE_GODOT_VERSION": "4.7.1.stable.fake"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[warn] Godot 4.7.1.stable.fake (kit.json pins 4.7.2)" in result.stdout


INCLUDED_ADDON = {"exclude": ["addons", "artifacts", "docs"], "include": ["addons/agent_devtools"]}
RULE_FIX = (
    "fix: in project.godot under [debug], set  gdscript/warnings/directory_rules="
    '{"res://addons": 0, "res://addons/agent_devtools": 1}'
)


def test_included_addon_needs_a_warning_rule(tmp_path: Path) -> None:
    """Godot applies no warnings under res://addons: listing a folder in source.include
    does not make the type check check it."""
    root, env = _ready_project(tmp_path)
    write_kit_json(root, source=INCLUDED_ADDON)

    unreached = run_tool(root, "doctor.ps1", env=env)
    assert unreached.returncode == 1, unreached.stdout + unreached.stderr
    assert (
        "[FAIL] kit.json source.include: Godot applies no GDScript warnings in" in unreached.stdout
    )
    assert RULE_FIX in unreached.stdout

    # Written the way the editor writes it: one entry per line.
    rules = {"res://addons": 0, "res://addons/agent_devtools": 1}
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS, directory_rules=rules))
    reached = run_tool(root, "doctor.ps1", env=env)
    assert reached.returncode == 0, reached.stdout + reached.stderr
    assert "[ok]   strict warnings reach source.include (addons/agent_devtools)" in reached.stdout

    # A rule for a folder that merely starts with the same letters does not count.
    near = {"res://addons": 0, "res://addons/agent_devtools_extra": 1}
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS, directory_rules=near))
    assert run_tool(root, "doctor.ps1", env=env).returncode == 1

    # The older all-or-nothing switch turns the warnings on for every addon.
    legacy = project_godot(autoloads=DEVTOOLS) + "gdscript/warnings/exclude_addons=false\n"
    write(root / "project.godot", legacy)
    assert run_tool(root, "doctor.ps1", env=env).returncode == 0


def test_strict_off_turns_the_strict_warning_checks_into_warnings(tmp_path: Path) -> None:
    """kit.json gdscript.strict = false: a project that has not adopted the strict
    warnings is ready; doctor still says what is not checked and how to adopt them."""
    root, env = _ready_project(tmp_path)
    write(root / "project.godot", project_godot(autoloads=DEVTOOLS, strict=False))

    strict = run_tool(root, "doctor.ps1", env=env)
    assert strict.returncode == 1 and "[FAIL] strict GDScript warnings: 16 of 16" in strict.stdout
    assert "strict typing on" in strict.stdout

    write_kit_json(root, source=INCLUDED_ADDON, gdscript={"strict": False})
    relaxed = run_tool(root, "doctor.ps1", env=env)
    assert relaxed.returncode == 0, relaxed.stdout + relaxed.stderr
    assert "[FAIL]" not in relaxed.stdout
    assert "[ok]   kit.json (Godot 4.7.2, tests in test/, strict typing off)" in relaxed.stdout
    assert "[warn] strict GDScript warnings: 16 of 16 are not errors" in relaxed.stdout
    assert "kit.json gdscript.strict = false" in relaxed.stdout
    assert "fix: to adopt them, set gdscript.strict to true in kit.json" in relaxed.stdout
    assert "[warn] kit.json source.include: Godot applies no GDScript warnings in" in relaxed.stdout
    assert RULE_FIX in relaxed.stdout
    assert "doctor: READY: " in relaxed.stdout and "2 warning(s), 0 failed" in relaxed.stdout


@pytest.mark.parametrize(
    ("kit", "message"),
    [
        ('{"gdscript": {"strict": "off"}}', 'kit.json: "gdscript.strict" must be true or false'),
        ('{"source": {"exclude": "addons"}}', 'kit.json: "source.exclude" must be a list'),
        (
            '{"tests": {"root": "../elsewhere"}}',
            'kit.json: "tests.root" must be a folder inside the project, not "../elsewhere"',
        ),
        (
            '{"source": {"include": ["addons/typo"]}}',
            'kit.json: "source.include" names a folder that does not exist: addons/typo',
        ),
    ],
)
def test_a_kit_json_value_that_cannot_be_used_is_one_failure(
    tmp_path: Path, kit: str, message: str
) -> None:
    root, env = _ready_project(tmp_path)
    write(root / "kit.json", kit)
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[FAIL] {message}" in result.stdout
    assert "kit.json must be readable before anything else" in result.stdout
    assert result.stdout.count("[FAIL]") == 1 and "[ok]   project.godot" not in result.stdout


def test_devtools_autoload_may_be_stored_as_a_uid(tmp_path: Path) -> None:
    """Enabling the plugin in the editor stores the autoload as the script's uid://."""
    root, env = _ready_project(tmp_path)
    write(root / "addons" / "agent_devtools" / "dev_tools.gd.uid", "uid://qba8jpa33rfr\n")
    write(root / "project.godot", project_godot(autoloads={"DevTools": "*uid://qba8jpa33rfr"}))
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[ok]   DevTools autoload registered" in result.stdout

    # Another script's uid is not the DevTools autoload.
    write(root / "project.godot", project_godot(autoloads={"DevTools": "*uid://someoneelse"}))
    other = run_tool(root, "doctor.ps1", env=env)
    assert other.returncode == 1 and "[FAIL] DevTools autoload is not registered" in other.stdout


def test_sample_sections_in_agents_md_are_a_warning(tmp_path: Path) -> None:
    root, env = _ready_project(tmp_path)
    write(root / "AGENTS.md", "# AGENTS.md\n\n## Mission (sample)\n\nA lamp game.\n")
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    warned = (
        "[warn] AGENTS.md still says (sample): replace those sections, then delete the lines "
        "at its top that explain the mark\n       fix: docs/customizing-agents-md.md"
    )
    assert warned in result.stdout and "1 warning(s), 0 failed" in result.stdout

    write(root / "AGENTS.md", "# AGENTS.md\n\n## Mission\n\nMy own game.\n")
    edited = run_tool(root, "doctor.ps1", env=env)
    assert edited.returncode == 0 and "[warn]" not in edited.stdout, edited.stdout


def test_an_agents_md_without_the_kit_part_is_a_warning(tmp_path: Path) -> None:
    """install.ps1 keeps a project's own AGENTS.md: until the kit's part is merged in,
    no agent is told about check.ps1 or DevTools."""
    root, env = _ready_project(tmp_path)
    write(root / "AGENTS.md", "# My rules\n\nBe kind.\n")
    write(root / "AGENTS.kit.md", "# AGENTS.md\n\n# Kit\n\n## Commands\n")
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[warn] AGENTS.md has no '# Kit' part" in result.stdout
    assert "fix: copy from '# Kit' down out of AGENTS.kit.md" in result.stdout

    write(root / "AGENTS.md", "# My rules\n\nBe kind.\n\n# Kit\n\n## Commands\n")
    merged = run_tool(root, "doctor.ps1", env=env)
    assert merged.returncode == 0 and "[warn]" not in merged.stdout, merged.stdout
    # Merged another way and the copy deleted: nothing to say.
    write(root / "AGENTS.md", "# My rules\n\nBe kind.\n")
    (root / "AGENTS.kit.md").unlink()
    gone = run_tool(root, "doctor.ps1", env=env)
    assert gone.returncode == 0 and "[warn]" not in gone.stdout, gone.stdout


def test_mutation_left_on_is_a_warning(tmp_path: Path) -> None:
    root, env = _ready_project(tmp_path)
    settings = project_godot(autoloads=DEVTOOLS)
    write(root / "project.godot", settings + "\n[devtools]\n\nallow_mutation=true\n")
    result = run_tool(root, "doctor.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[warn] devtools/allow_mutation=true in project.godot" in result.stdout
    assert "fix: under [devtools] set allow_mutation=false" in result.stdout

    write(root / "project.godot", settings + "\n[devtools]\n\nallow_mutation=false\n")
    off = run_tool(root, "doctor.ps1", env=env)
    assert off.returncode == 0 and "[warn]" not in off.stdout, off.stdout


@pytest.mark.real_godot
def test_lone_windowed_godot_is_a_warning(tmp_path: Path) -> None:
    windowed = lone_gui_godot(tmp_path / "flat")
    if windowed is None:
        pytest.skip("no real Godot .exe + _console.exe pair resolves here")
    root, env = _ready_project(tmp_path)
    result = run_tool(root, "doctor.ps1", env=env | {"GODOT_BIN": str(windowed)})
    assert result.returncode == 0, result.stdout + result.stderr  # usable, so not a failure
    assert (
        "[warn] that Godot is the windowed .exe and no _console.exe sits beside it" in result.stdout
    )
    assert str(windowed) in result.stdout


def test_windows_powershell_5_gets_told_to_use_pwsh(tmp_path: Path) -> None:
    """doctor must run under 5.1, say what is wrong, and stop: the other checks need
    PowerShell 7 to read kit.json."""
    legacy = shutil.which("powershell")
    if legacy is None:
        pytest.skip("Windows PowerShell 5.1 is not installed")
    root, env = _ready_project(tmp_path)
    doctor = str(root / "tools" / "doctor.ps1")
    result = subprocess.run(
        [legacy, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", doctor],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[FAIL] PowerShell 5." in result.stdout
    assert "winget install Microsoft.PowerShell" in result.stdout
    assert "doctor: NOT READY: run this again with pwsh; nothing else was checked." in result.stdout
    assert "[ok]" not in result.stdout and result.stdout.count("[FAIL]") == 1


@pytest.mark.parametrize(
    ("script", "code"), [("tools/setup.ps1", 1), ("tools/check.ps1", 2), ("install.ps1", 1)]
)
def test_the_entry_points_say_so_in_one_line_under_windows_powershell_5(
    script: str, code: int
) -> None:
    """The three scripts a README sends a stranger to. `#Requires -Version 7` would print
    five lines of error record and no fix; they must parse under 5.1 to say it themselves."""
    legacy = shutil.which("powershell")
    if legacy is None:
        pytest.skip("Windows PowerShell 5.1 is not installed")
    result = subprocess.run(
        [legacy, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO / script)],
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    name = Path(script).stem
    said = result.stdout.strip().splitlines()
    assert len(said) == 1 and said[0].startswith(f"{name}: needs PowerShell 7, this is 5."), said
    assert "Run it with pwsh (winget install Microsoft.PowerShell)." in said[0]
    assert result.stderr.strip() == ""
