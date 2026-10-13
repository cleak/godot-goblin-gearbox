"""The GDScript gates: which files they pick, and that defects fail them."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _gate_fixtures import (
    PWSH,
    REPO,
    WINDOWS,
    clean_env,
    copy_tools,
    project_godot,
    real_godot,
    run_powershell,
    run_tool,
    write,
    write_kit_json,
)

VENV_SCRIPTS = Path(sys.executable).parent
GDFORMAT = VENV_SCRIPTS / "gdformat.exe"
GDLINT = VENV_SCRIPTS / "gdlint.exe"
SYSTEM32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"

pytestmark = pytest.mark.skipif(not WINDOWS or PWSH is None, reason="Windows PowerShell 7 gates")

CLEAN = "extends RefCounted\n"
INVALID = "this is invalid\n"


def _lint_env() -> dict[str, str]:
    """The fixture project has no .venv, so gdtoolkit comes from PATH."""
    return clean_env(PATH=str(VENV_SCRIPTS) + os.pathsep + os.environ.get("PATH", ""))


def test_pre_commit_hooks_lint_the_kits_addon_and_leave_other_addons_alone() -> None:
    """The hooks exclude other people's addons, not the kit's own."""
    text = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    excludes = re.findall(r"^\s+exclude: (\S+)$", text, re.MULTILINE)
    assert len(excludes) == 2 and len(re.findall(r"^\s+- id: gd(?:lint|format)$", text, re.M)) == 2
    for pattern in excludes:
        # pre-commit searches each file's path (forward slashes) for the pattern.
        skipped = {
            path: bool(re.search(pattern, path))
            for path in (
                "addons/gdUnit4/src/GdUnitTestSuite.gd",
                "addons/third_party/plugin.gd",
                "addons/agent_devtools_extra/plugin.gd",
                "addons/agent_devtools/dev_tools.gd",
                "scripts/game/game.gd",
                "tools/addons/x.gd",
            )
        }
        assert skipped == {
            "addons/gdUnit4/src/GdUnitTestSuite.gd": True,
            "addons/third_party/plugin.gd": True,
            "addons/agent_devtools_extra/plugin.gd": True,
            "addons/agent_devtools/dev_tools.gd": False,
            "scripts/game/game.gd": False,
            "tools/addons/x.gd": False,
        }
    # The same folders the other gates take from kit.json.
    config = json.loads((REPO / "kit.json").read_text(encoding="utf-8"))
    assert config["source"]["exclude"][0] == "addons"
    assert config["source"]["include"] == ["addons/agent_devtools"]


def _source_tree(root: Path) -> None:
    """Authored files in folders of no particular name, and every kind of skipped folder."""
    write_kit_json(
        root,
        source={"exclude": ["addons", "vendor/third_party"], "include": ["addons/mine"]},
    )
    write(root / "characters" / "hero.gd", CLEAN)
    write(root / "addons" / "mine" / "plugin.gd", CLEAN)
    write(root / "addons" / "theirs" / "invalid.gd", INVALID)
    write(root / "vendor" / "third_party" / "invalid.gd", INVALID)
    write(root / ".hidden" / "invalid.gd", INVALID)
    write(root / "ignored" / ".gdignore", "")
    write(root / "ignored" / "invalid.gd", INVALID)
    write(root / "nested" / "project.godot", "config_version=5\n")
    write(root / "nested" / "invalid.gd", INVALID)


def test_lint_gate_passes_on_a_machine_that_never_ran_gdtoolkit(tmp_path: Path) -> None:
    """gdtoolkit builds its grammar cache on first use; parallel batches must not race for it."""
    assert GDFORMAT.is_file() and GDLINT.is_file(), "run uv sync"
    root = tmp_path / "project"
    copy_tools(root, "lint_gdscript.ps1")
    write(root / ".gdlintrc", (REPO / ".gdlintrc").read_text())
    write_kit_json(root)
    for index in range(64):  # several batches on any machine
        write(root / "scripts" / f"case_{index:02d}.gd", CLEAN)
    cache = tmp_path / "no cache yet"
    cache.mkdir()
    env = _lint_env()
    env["LOCALAPPDATA"] = str(cache)

    result = run_tool(root, "lint_gdscript.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GDScript lint + format: CLEAN" in result.stdout
    assert list((cache / "gdtoolkit").rglob("*.pickle")), "the cache was not built where expected"


def test_lint_gate_picks_authored_files_and_batches_long_argv(tmp_path: Path) -> None:
    assert GDFORMAT.is_file() and GDLINT.is_file(), "run uv sync"
    root = tmp_path / "project"
    copy_tools(root, "lint_gdscript.ps1")
    write(root / ".gdlintrc", (REPO / ".gdlintrc").read_text())
    _source_tree(root)
    # Far more than one command line holds: the files must go in batches.
    long_dir = root / "scripts" / ("long_authored_script_path_" + "x" * 60)
    for index in range(360):
        write(long_dir / f"case_{index:03d}.gd", CLEAN)
    env = _lint_env()

    result = run_tool(root, "lint_gdscript.ps1", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Linting 362 GDScript file(s)" in result.stdout  # 360 + hero + the included addon
    assert "GDScript lint + format: CLEAN" in result.stdout

    selected = run_tool(root, "lint_gdscript.ps1", "-Path", "characters", env=env)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert "Linting 1 GDScript file(s)" in selected.stdout

    # A path given by name is linted even though source.exclude covers it.
    excluded = run_tool(root, "lint_gdscript.ps1", "-Path", "addons/theirs/invalid.gd", env=env)
    assert excluded.returncode == 1, excluded.stdout + excluded.stderr
    assert "Linting 1 GDScript file(s)" in excluded.stdout

    write(root / "-fixture.gd", CLEAN)
    leading_dash = run_tool(root, "lint_gdscript.ps1", "-Path", "./-fixture.gd", env=env)
    assert leading_dash.returncode == 0, leading_dash.stdout + leading_dash.stderr
    assert "Linting 1 GDScript file(s)" in leading_dash.stdout

    # The first and last batches are bad, the ones between clean. The batches run in
    # parallel: both tools must still finish every file, report every failure, and
    # print only the failing batches.
    bad = "extends RefCounted\n\nfunc BadName() -> void:\n    pass\n"
    write(long_dir / "case_000.gd", bad)
    write(long_dir / "case_359.gd", bad)
    broken = run_tool(root, "lint_gdscript.ps1", env=env)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "Formatting issues found" in broken.stdout
    assert "Lint issues found" in broken.stdout
    for name in ("case_000.gd", "case_359.gd"):
        assert f"would reformat ./scripts/{long_dir.name}/{name}" in broken.stdout
        assert f"{name}:3: Error: Function name" in broken.stdout
    assert "no problems found" not in broken.stdout

    fixed = run_tool(
        root,
        "lint_gdscript.ps1",
        "-Path",
        str((long_dir / "case_000.gd").relative_to(root)),
        "-Fix",
        env=env,
    )
    assert fixed.returncode == 1, fixed.stdout + fixed.stderr  # formatted; the name still fails
    assert "\tpass" in (long_dir / "case_000.gd").read_text()
    assert "Lint issues found" in fixed.stdout
    # gdformat on Windows writes CRLF; a file that had LF endings still has them.
    assert b"\r" not in (long_dir / "case_000.gd").read_bytes()


def test_lint_gate_fails_loudly_on_nothing_to_lint(tmp_path: Path) -> None:
    root = tmp_path / "project"
    copy_tools(root, "lint_gdscript.ps1")
    write_kit_json(root)
    write(root / "addons" / "theirs" / "plugin.gd", CLEAN)
    (root / "empty").mkdir()
    env = _lint_env()

    nothing = run_tool(root, "lint_gdscript.ps1", env=env)
    assert nothing.returncode == 1, nothing.stdout + nothing.stderr
    assert "No .gd files found" in nothing.stdout

    empty = run_tool(root, "lint_gdscript.ps1", "-Path", "empty", env=env)
    assert empty.returncode == 1 and "No .gd files found" in empty.stdout

    missing = run_tool(root, "lint_gdscript.ps1", "-Path", "no/such/folder", env=env)
    assert missing.returncode == 1 and "does not exist" in missing.stdout

    outside = run_tool(root, "lint_gdscript.ps1", "-Path", str(tmp_path), env=env)
    assert outside.returncode == 1 and "outside the project" in outside.stdout

    # Without gdtoolkit the gate reports the missing tool; it does not pass.
    bare = run_tool(root, "lint_gdscript.ps1", env=clean_env(PATH=str(SYSTEM32)))
    assert bare.returncode == 2, bare.stdout + bare.stderr
    assert "uv sync" in bare.stdout


LONG_LINE = 'extends RefCounted\n\nconst NAME: String = "%s"\n' % ("x" * 60)
UNFORMATTED = "extends RefCounted\n\nfunc run() -> void:\n    pass\n"


def test_lint_gate_leaves_the_kits_installed_files_to_the_kits_rules(tmp_path: Path) -> None:
    """A project with its own .gdlintrc (max-line-length: 80) got 104 findings in the
    kit's tools/*.gd, and -Fix reformatted them: a conflict on every upgrade."""
    assert GDFORMAT.is_file() and GDLINT.is_file(), "run uv sync"
    root = tmp_path / "project"
    copy_tools(root, "lint_gdscript.ps1")
    write_kit_json(root)
    write(root / ".gdlintrc", "max-line-length: 80\n")
    write(root / "scripts" / "mine.gd", CLEAN)
    kit_script = write(root / "tools" / "kit_tool.gd", LONG_LINE)
    reformatted = write(root / "tools" / "Kit_Format.gd", UNFORMATTED)
    env = _lint_env()

    # The kit's own repository has no manifest: every file is held to the rules.
    alone = run_tool(root, "lint_gdscript.ps1", env=env)
    assert alone.returncode == 1, alone.stdout + alone.stderr
    assert "Linting 3 GDScript file(s)" in alone.stdout and "left out" not in alone.stdout
    assert "kit_tool.gd:3: Error: Max allowed line length (80) exceeded" in alone.stdout
    assert "would reformat ./tools/Kit_Format.gd" in alone.stdout

    # Installed: the manifest's files are left out, by name, whatever their letter case.
    manifest = {"files": {"tools/kit_tool.gd": "0", "tools/kit_format.gd": "0", "tools/x.ps1": "0"}}
    write(root / "tools" / "kit_manifest.json", json.dumps(manifest))
    installed = run_tool(root, "lint_gdscript.ps1", env=env)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert "Linting 1 GDScript file(s)" in installed.stdout
    notes = [line for line in installed.stdout.splitlines() if "left out" in line]
    assert notes == [
        "2 kit file(s) left out: tools/kit_manifest.json lists them, "
        "and the kit's own rules cover them.",
        # The verdict line: all that check.ps1 shows of a gate that passed.
        "GDScript lint + format: CLEAN (1 file(s); 2 kit file(s) left out)",
    ]
    fixed = run_tool(root, "lint_gdscript.ps1", "-Fix", env=env)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert reformatted.read_text() == UNFORMATTED and kit_script.read_text() == LONG_LINE

    # A file of the project's own in tools/ is still held to the project's rules.
    write(root / "tools" / "my_tool.gd", LONG_LINE)
    mine = run_tool(root, "lint_gdscript.ps1", env=env)
    assert mine.returncode == 1 and "my_tool.gd:3: Error: Max allowed line" in mine.stdout
    assert "kit_tool.gd" not in mine.stdout
    (root / "tools" / "my_tool.gd").unlink()

    # Nothing but kit files under the path: said, and not a pass.
    only_kit = run_tool(root, "lint_gdscript.ps1", "-Path", "tools/kit_tool.gd", env=env)
    assert only_kit.returncode == 1, only_kit.stdout + only_kit.stderr
    assert "No .gd files found to lint under 'tools/kit_tool.gd' (1 kit file(s) left out" in (
        only_kit.stdout
    )

    # A manifest nobody can read is one line, not a silent "lint everything".
    for text, reason in (("{ not json", "is not valid JSON"), ("[]", 'has no "files" object')):
        write(root / "tools" / "kit_manifest.json", text)
        broken = run_tool(root, "lint_gdscript.ps1", env=env)
        assert broken.returncode == 1, broken.stdout + broken.stderr
        assert f"GDScript lint: tools/kit_manifest.json {reason}" in broken.stdout
        assert "Linting" not in broken.stdout and broken.stderr.strip() == ""


# The checker and what it is built on. They sit in the project's tools/ folder, so they
# are authored files too and every count below includes them.
TYPE_TOOLS = ("typecheck.gd", "headless_tool.gd", "error_capture.gd", "kit_config.gd")

# Godot applies no warnings under res://addons until a rule includes the folder.
MINE_IS_CHECKED = {"res://addons": 0, "res://addons/mine": 1}


def _type_project(
    root: Path,
    *,
    autoloads: dict[str, str] | None = None,
    directory_rules: dict[str, int] | None = None,
    strict: bool = True,
) -> dict[str, str]:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot resolves here: run tools/install_godot.ps1 or set GODOT_BIN")
    copy_tools(root, "typecheck.ps1", *TYPE_TOOLS)
    settings = project_godot(autoloads=autoloads, strict=strict, directory_rules=directory_rules)
    write(root / "project.godot", settings)
    return clean_env(GODOT_BIN=godot)


@pytest.mark.real_godot
def test_type_gate_picks_authored_files_and_pins_failures_to_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    env = _type_project(root, directory_rules=MINE_IS_CHECKED)
    _source_tree(root)

    result = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    # hero, the included addon, and the four tool scripts.
    assert "Type-checking 6 GDScript file(s)" in result.stdout
    assert "Type-check: CLEAN (6 of 6 files)" in result.stdout

    selected = run_tool(root, "typecheck.ps1", "-NoImport", "-Path", "characters", env=env)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert "Type-checking 1 GDScript file(s)" in selected.stdout

    # One process checks every file, yet each strict-warning failure is pinned to
    # its own file and fails the gate.
    write(root / "scripts" / "untyped.gd", "extends RefCounted\n\n\nvar x = 5\n")
    broken = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "Type-checking 7 GDScript file(s)" in broken.stdout
    assert "FAIL  res://scripts/untyped.gd" in broken.stdout
    assert 'Variable "x" has no static type' in broken.stdout
    assert "hero" not in broken.stdout

    missing = run_tool(root, "typecheck.ps1", "-NoImport", "-Path", "no/such/folder", env=env)
    assert missing.returncode == 1 and "does not exist" in missing.stdout


UNTYPED = (
    "extends RefCounted\n\n\n"
    "func half(value: int) -> int:\n\tvar seeded = value\n\treturn seeded / 2\n"
)


@pytest.mark.real_godot
def test_type_gate_fails_files_godot_loaded_with_the_warnings_off(tmp_path: Path) -> None:
    """A file under res://addons loads clean whatever it holds: that is not a pass.

    Without this the gate prints "CLEAN (2 of 2 files)" for an addon it never checked.
    """
    root = tmp_path / "project"
    env = _type_project(root)  # no directory rule: Godot's default
    _source_tree(root)
    write(root / "addons" / "mine" / "plugin.gd", UNTYPED)

    unchecked = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert unchecked.returncode == 1, unchecked.stdout + unchecked.stderr
    assert "Not checked: 1 file(s)" in unchecked.stdout
    assert "res://addons/mine/plugin.gd" in unchecked.stdout
    assert '"res://addons/mine": 1' in unchecked.stdout  # the rule to add
    assert "Type-check FAILED: 1 file(s) were not checked (warnings off)." in unchecked.stdout
    assert "CLEAN" not in unchecked.stdout

    # With the rule the same file is checked, and fails on what it holds.
    write(root / "project.godot", project_godot(directory_rules=MINE_IS_CHECKED))
    checked = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "FAIL  res://addons/mine/plugin.gd" in checked.stdout
    assert 'Variable "seeded" has no static type' in checked.stdout
    assert "Integer division" in checked.stdout
    assert "Not checked" not in checked.stdout

    # Named by -Path, an addon nobody included is loaded but still not checked.
    theirs = run_tool(root, "typecheck.ps1", "-NoImport", "-Path", "addons/theirs", env=env)
    assert theirs.returncode == 1, theirs.stdout + theirs.stderr
    assert "FAIL  res://addons/theirs/invalid.gd" in theirs.stdout  # it does not even parse


@pytest.mark.real_godot
def test_type_gate_fails_a_project_with_no_strict_warnings(tmp_path: Path) -> None:
    """Without the [debug] block nothing is an error, so a clean load proves nothing."""
    root = tmp_path / "project"
    env = _type_project(root, strict=False)
    write_kit_json(root)
    write(root / "scripts" / "untyped.gd", UNTYPED)
    result = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"Not checked: {1 + len(TYPE_TOOLS)} file(s)" in result.stdout
    assert "res://scripts/untyped.gd" in result.stdout
    assert "tools/doctor.ps1" in result.stdout
    assert "gdscript.strict to false" in result.stdout  # the way out for a project without them
    assert "directory_rules" not in result.stdout  # the addon hint is for addon files only


@pytest.mark.real_godot
def test_type_gate_fails_on_import_errors_no_checked_file_explains(tmp_path: Path) -> None:
    """An import can exit 0 and still report a script that does not load.

    When that script is one of the checked files the per-file verdict names it; when it is
    not (here: an autoload in an excluded folder), the import's own errors fail the gate.
    """
    root = tmp_path / "project"
    env = _type_project(root, autoloads={"Boot": "*res://vendor/boot.gd"})
    write_kit_json(root, source={"exclude": ["vendor"], "include": []})
    write(root / "vendor" / "boot.gd", INVALID)
    write(root / "scripts" / "fine.gd", CLEAN)

    result = run_tool(root, "typecheck.ps1", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Every file type-checks, but the import before it reported errors" in result.stdout
    assert "res://vendor/boot.gd" in result.stdout
    assert "CLEAN" not in result.stdout


UNDECLARED = "extends RefCounted\n\n\nfunc run() -> int:\n\treturn missing_thing + 1\n"
BOOT = "extends Node\n\nvar level: int = 3\n"
USES_BOOT = "extends Node\n\n\nfunc level() -> int:\n\treturn Boot.level\n"
MISUSES_BOOT = "extends Node\n\n\nfunc level() -> String:\n\treturn Boot.level\n"


@pytest.mark.real_godot
@pytest.mark.parametrize("autoloads", [None, {"Boot": "*res://scripts/boot.gd"}])
def test_type_gate_fails_an_undeclared_identifier(
    tmp_path: Path, autoloads: dict[str, str] | None
) -> None:
    """With no autoloads, and with one: an unknown name is a failure."""
    root = tmp_path / "project"
    env = _type_project(root, autoloads=autoloads)
    write_kit_json(root)
    write(root / "scripts" / "boot.gd", BOOT)
    write(root / "scripts" / "undeclared.gd", UNDECLARED)

    result = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL  res://scripts/undeclared.gd" in result.stdout
    assert 'Identifier "missing_thing" not declared' in result.stdout


@pytest.mark.real_godot
def test_type_gate_checks_a_script_that_names_an_autoload(tmp_path: Path) -> None:
    """An autoload's name and type resolve in the checker's process (Godot 4.7, a script
    loaded from _initialize on), so such a script gets a real verdict, never
    "skipped": that is a file nobody checked, in a run that says CLEAN."""
    root = tmp_path / "project"
    env = _type_project(root, autoloads={"Boot": "*res://scripts/boot.gd"})
    write_kit_json(root)
    write(root / "scripts" / "boot.gd", BOOT)
    write(root / "scripts" / "uses_boot.gd", USES_BOOT)

    clean = run_tool(root, "typecheck.ps1", env=env)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    files = 2 + len(TYPE_TOOLS)
    assert f"Type-check: CLEAN ({files} of {files} files)" in clean.stdout
    assert "skip" not in clean.stdout

    # The autoload's members are typed too: a wrong use of one is an error.
    write(root / "scripts" / "uses_boot.gd", MISUSES_BOOT)
    wrong = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert wrong.returncode == 1, wrong.stdout + wrong.stderr
    assert "FAIL  res://scripts/uses_boot.gd" in wrong.stdout
    assert "Cannot return" in wrong.stdout
    assert "FAIL  res://scripts/boot.gd" not in wrong.stdout


@pytest.mark.real_godot
def test_strict_off_checks_for_compile_errors_only(tmp_path: Path) -> None:
    """kit.json gdscript.strict = false: a project that has not adopted the strict
    warnings passes on files Godot loaded with them off, and is told so once."""
    root = tmp_path / "project"
    env = _type_project(root, strict=False)
    write_kit_json(root, gdscript={"strict": False})
    write(root / "scripts" / "untyped.gd", UNTYPED)

    relaxed = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert relaxed.returncode == 0, relaxed.stdout + relaxed.stderr
    files = 1 + len(TYPE_TOOLS)
    # The verdict line says it too: it is all check.ps1 shows of a gate that passed.
    verdict = f"Type-check: CLEAN ({files} of {files} files); {files} for compile errors only: "
    assert relaxed.stdout.rstrip().splitlines()[-1] == verdict + "strict typing is off"
    notes = [line for line in relaxed.stdout.splitlines() if "strict typing is off" in line]
    assert len(notes) == 2, relaxed.stdout
    assert "kit.json gdscript.strict = false" in notes[0]
    assert f"{files} of {files} file(s) were checked for compile errors only" in notes[0]
    assert "set gdscript.strict to true" in notes[0] and "doctor.ps1" in notes[0]
    assert "Not checked" not in relaxed.stdout

    # A script that does not compile is still a failure.
    write(root / "scripts" / "broken.gd", INVALID)
    broken = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "FAIL  res://scripts/broken.gd" in broken.stdout
    assert "untyped.gd" not in broken.stdout
    assert "Type-check FAILED: 1 file(s) do not compile; strict typing is off" in broken.stdout
    (root / "scripts" / "broken.gd").unlink()

    # So is whatever project.godot itself promotes to an error.
    write(root / "project.godot", project_godot(strict=True))
    promoted = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert promoted.returncode == 1, promoted.stdout + promoted.stderr
    assert "FAIL  res://scripts/untyped.gd" in promoted.stdout
    assert "strict typing is off" not in promoted.stdout  # every file was checked in full

    # The default is strict: the key left out, or set to true.
    write(root / "project.godot", project_godot(strict=False))
    for sections in ({}, {"gdscript": {"strict": True}}):
        write_kit_json(root, **sections)
        strict = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
        assert strict.returncode == 1 and "Not checked" in strict.stdout, strict.stdout


@pytest.mark.real_godot
def test_type_gate_says_when_the_checker_itself_broke(tmp_path: Path) -> None:
    """A script error in tools/typecheck.gd ends its run early with every file unjudged:
    exit 3 and no verdict, never CLEAN."""
    root = tmp_path / "project"
    env = _type_project(root)
    write_kit_json(root)
    write(root / "scripts" / "fine.gd", CLEAN)
    runner = root / "tools" / "typecheck.gd"
    source = runner.read_text(encoding="utf-8")
    marker = "\tvar running: PackedStringArray = _script_chain()\n"
    assert marker in source
    seeded = marker + '\tvar nothing: Dictionary = {}\n\tprint(nothing["missing"])\n'
    runner.write_text(source.replace(marker, seeded), encoding="utf-8", newline="\n")

    result = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Type-check: the checker itself broke" in result.stdout
    assert "TOOL_ERROR typecheck" in result.stdout
    assert "CLEAN" not in result.stdout


SLOPPY = "\n\nfunc sloppy(value):\n\treturn value\n"


@pytest.mark.real_godot
def test_type_gate_names_a_checker_script_that_does_not_compile(tmp_path: Path) -> None:
    """tools/typecheck.gd extends tools/headless_tool.gd, an authored file like any other.
    With a strict violation in it Godot cannot load the checker, and does not quit either:
    the gate sat out its whole timeout (15 minutes) and named no file."""
    root = tmp_path / "project"
    env = _type_project(root)
    write_kit_json(root)
    write(root / "scripts" / "fine.gd", CLEAN)
    for name in ("headless_tool.gd", "kit_config.gd"):
        tool = root / "tools" / name
        intact = tool.read_bytes()
        with tool.open("a", encoding="utf-8", newline="\n") as script:
            script.write(SLOPPY)
        # A regression costs this timeout, not the default one.
        result = run_tool(root, "typecheck.ps1", "-NoImport", "-TimeoutSeconds", "40", env=env)
        assert result.returncode == 1, result.stdout + result.stderr
        assert f"FAIL  res://tools/{name}" in result.stdout
        assert 'Function "sloppy()" has no static return type' in result.stdout
        assert f"(res://tools/{name}:" in result.stdout
        assert result.stdout.count("  FAIL  ") == 1  # not the scripts that depend on it
        verdict = "Type-check FAILED: 1 checker file(s) do not compile; no other file was checked."
        assert verdict in result.stdout
        assert "did not finish" not in result.stdout and "CLEAN" not in result.stdout
        tool.write_bytes(intact)

    # Godot names no file: nothing is known but that the checker is broken.
    (root / "tools" / "typecheck.gd").unlink()
    gone = run_tool(root, "typecheck.ps1", "-NoImport", "-TimeoutSeconds", "40", env=env)
    assert gone.returncode == 3, gone.stdout + gone.stderr
    assert "Type-check: the checker itself broke (tools/typecheck.gd does not load)" in gone.stdout


@pytest.mark.real_godot
def test_type_gate_fails_a_strict_project_that_promotes_only_some_warnings(tmp_path: Path) -> None:
    """One warning as an error is enough for Godot to reject the checker's probe script, so
    every file counted as checked: "CLEAN" for untyped code, in a project tools/doctor.ps1
    calls not ready."""
    root = tmp_path / "project"
    env = _type_project(root, strict=False)
    write_kit_json(root)
    one_warning = project_godot(strict=False) + "[debug]\n\ngdscript/warnings/integer_division=2\n"
    write(root / "project.godot", one_warning)
    write(root / "scripts" / "untyped.gd", "extends RefCounted\n\n\nfunc same(a):\n\treturn a\n")

    result = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    weak = [line for line in result.stdout.splitlines() if line.startswith("Not strict: ")]
    assert len(weak) == 1, result.stdout
    assert (
        "leaves 15 of the 16 strict GDScript warnings below error: untyped_declaration, " in weak[0]
    )
    assert "integer_division" not in weak[0]
    assert "tools/doctor.ps1" in result.stdout and "gdscript.strict to false" in result.stdout
    assert (
        "Type-check FAILED: 15 strict warning(s) are not errors in project.godot." in result.stdout
    )
    assert "CLEAN" not in result.stdout and "Not checked" not in result.stdout

    # A project that says it is not strict is held to what it does promote, no more.
    write_kit_json(root, gdscript={"strict": False})
    relaxed = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert relaxed.returncode == 0, relaxed.stdout + relaxed.stderr
    assert "Not strict" not in relaxed.stdout


def _run_checker(root: Path, env: dict[str, str], *paths: str) -> subprocess.CompletedProcess[str]:
    """tools/typecheck.gd on its own, as tools/typecheck.ps1 starts it."""
    listed = root / "list.txt"
    listed.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8", newline="\n")
    env = env | {"KIT_PROBE_ROOT": str(root), "KIT_PROBE_LIST": str(listed)}
    return run_powershell(
        ". (Join-Path $env:KIT_PROBE_ROOT 'tools/godot_resolver.ps1'); "
        "$run = Invoke-KitProcess -FilePath $env:GODOT_BIN -Arguments @('--headless', "
        "'--script', 'res://tools/typecheck.gd', '--', $env:KIT_PROBE_LIST) "
        "-WorkingDirectory $env:KIT_PROBE_ROOT -TimeoutSeconds 120; "
        "Write-Output $run.Text; exit $run.Code",
        env=env,
    )


@pytest.mark.real_godot
def test_the_checker_alone_fails_an_unchecked_file_only_in_a_strict_project(tmp_path: Path) -> None:
    """The exit codes tools/typecheck.gd documents, without the wrapper that reads its
    lines: 1 for a file Godot loaded with the warnings off, 0 when gdscript.strict is false."""
    root = tmp_path / "project"
    env = _type_project(root, strict=False)  # nothing is an error: every file is unchecked
    write(root / "scripts" / "fine.gd", CLEAN)
    for strict, code in ((True, 1), (False, 0)):
        write_kit_json(root, gdscript={"strict": strict})
        result = _run_checker(root, env, "res://scripts/fine.gd")
        assert result.returncode == code, result.stdout + result.stderr
        assert "TC unchecked res://scripts/fine.gd" in result.stdout
        assert "TC_DONE checked=0 failed=0 unchecked=1" in result.stdout

    # A file that does not compile is 1 either way.
    write(root / "scripts" / "broken.gd", INVALID)
    broken = _run_checker(root, env, "res://scripts/fine.gd", "res://scripts/broken.gd")
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert "TC FAIL res://scripts/broken.gd" in broken.stdout


@pytest.mark.real_godot
def test_the_checker_loads_no_script_from_outside_the_project(tmp_path: Path) -> None:
    """Loading a script runs its static initialisers, so a listed path that leaves the
    project is refused before any file is loaded."""
    root = tmp_path / "project"
    env = _type_project(root)
    marker = tmp_path / "loaded.txt"
    marks = (
        "extends RefCounted\n\n\nstatic func _static_init() -> void:\n"
        f'\tvar file: FileAccess = FileAccess.open("{marker.as_posix()}", FileAccess.WRITE)\n'
        '\tfile.store_string("loaded")\n'
    )
    # Not write(): the script holds the temp folder's name, which need not be ASCII.
    (tmp_path / "outside.gd").write_text(marks, encoding="utf-8", newline="\n")
    outside = (
        "../outside.gd",
        "res://../outside.gd",
        "res://scripts/../../outside.gd",
        f"{root}/../outside.gd",
        str(tmp_path / "outside.gd"),
    )
    result = _run_checker(root, env, "res://tools/kit_config.gd", *outside)
    assert result.returncode == 2, result.stdout + result.stderr
    for path in outside:
        assert f"typecheck: path is outside the project: {path}" in result.stdout
    assert "TC " not in result.stdout and "TC_DONE" not in result.stdout
    assert not marker.exists()

    # The control: the same script inside the project is loaded, and leaves the marker.
    (root / "scripts").mkdir()
    (root / "scripts" / "inside.gd").write_text(marks, encoding="utf-8", newline="\n")
    inside = _run_checker(root, env, "res://scripts/../scripts/inside.gd")
    assert inside.returncode == 0, inside.stdout + inside.stderr
    assert "TC ok res://scripts/inside.gd" in inside.stdout
    assert marker.exists()


@pytest.mark.real_godot
def test_the_checker_refuses_a_kit_json_it_cannot_use(tmp_path: Path) -> None:
    """Run on its own (not through typecheck.ps1, which reads kit.json first), the
    GDScript side stops too: defaults would silently change which files count."""
    root = tmp_path / "project"
    env = _type_project(root)
    write(root / "kit.json", '{"gdscript": {"strict": "no"}}')
    result = _run_checker(root, env, "res://tools/kit_config.gd")
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'typecheck: kit.json: "gdscript.strict" must be true or false' in result.stdout
    assert "TC_DONE" not in result.stdout

    through_gate = run_tool(root, "typecheck.ps1", "-NoImport", env=env)
    assert through_gate.returncode == 1
    assert 'Type-check: kit.json: "gdscript.strict" must be true or false' in through_gate.stdout
