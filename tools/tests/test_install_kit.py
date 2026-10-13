"""install.ps1, run for real: from a temp copy of the kit into temp Godot projects.

Both folders have a space, an apostrophe and square brackets in their names, and the
script is started from a third folder. The last test needs a Godot binary and is skipped
without one.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from _gate_fixtures import (
    NEEDS_PWSH,
    PWSH,
    REPO,
    STRICT_WARNINGS,
    WINDOWS,
    clean_env,
    not_as_root,
    old_powershell,
    posix_only,
    real_godot,
    run_tool,
    takes_no_new_file,
    user_data_env,
    venv_tool,
    windows_only,
)

pytestmark = NEEDS_PWSH

NOT_COPIED = {".git", ".godot", ".venv", ".tools", "artifacts", ".pytest_cache", ".ruff_cache"}
SEEDS = (
    ".gdlintrc",
    ".editorconfig",
    ".gitattributes",
    ".pre-commit-config.yaml",
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "CLAUDE.md",
    ".github/workflows/ci.yml",
    ".github/copilot-instructions.md",
    ".claude/settings.json",
    "AGENTS.md",
)
PROJECT_DATA = "tools/ci/architecture_modules.json"
HOOKS = ".pre-commit-config.yaml"
LITTER = (".DS_Store", "Thumbs.db", "desktop.ini")

# The description holds a line that looks like a section header: it is inside a string.
PROJECT = """; Engine configuration file.

config_version=5

[application]

config/name="Target"
config/description="Line one
[autoload]
line three"
config/features=PackedStringArray("4.7", "Forward Plus")
{debug}
[rendering]

textures/canvas_textures/default_texture_filter=0
"""
AUTOLOAD = '[autoload]\n\nDevTools="*res://addons/agent_devtools/dev_tools.gd"\n\n'
DEVTOOLS = "[devtools]\n\nallow_mutation=false\n\n"
PLUGINS = (
    "[editor_plugins]\n\nenabled=PackedStringArray("
    '"res://addons/agent_devtools/plugin.cfg", "res://addons/gdUnit4/plugin.cfg")\n\n'
)
GITIGNORE = "# godot-goblin-gearbox\n.godot/\n/artifacts/\n/.tools/\n.venv/\n__pycache__/\n"
SCENE = (
    "[gd_scene load_steps=2 format=3]\n\n"
    '[ext_resource type="Script" path="res://player.gd" id="1"]\n\n'
    '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n'
)
TYPED_SCRIPT = "extends Node2D\n\nvar speed: float = 200.0\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _copy_kit(destination: Path, *, whole_gdunit: bool = False) -> Path:
    """The kit as a stranger has it: no caches, no local toolchain.

    gdUnit4 is about a thousand files; unless a test runs it, plugin.cfg stands for it.
    """
    gdunit = REPO / "addons" / "gdUnit4"

    def skipped(folder: str, names: list[str]) -> set[str]:
        drop = {name for name in names if name in NOT_COPIED or name == "__pycache__"}
        if not whole_gdunit and Path(folder) == gdunit:
            drop |= {name for name in names if name not in ("plugin.cfg", "LICENSE")}
        return drop

    shutil.copytree(REPO, destination, ignore=skipped)
    return destination


def _project(*, debug: str = "") -> str:
    return PROJECT.format(debug=f"\n{debug}" if debug else "")


def _target(root: Path, project: str | None = None) -> Path:
    _write(root / "project.godot", _project() if project is None else project)
    _write(root / "main.tscn", SCENE)
    _write(root / "player.gd", TYPED_SCRIPT)
    return root


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    return _copy_kit(tmp_path / "kit's [copy]")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return _target(tmp_path / "it's a [wip] game")


def _install(
    kit: Path, target: Path | str, *args: str, limit: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Run <kit>/install.ps1 from a folder that is neither the kit nor the target.

    `limit` (Linux, macOS): no file the run writes grows past that many bytes. The write
    that would is cut there and fails, as every write does on a full disk.
    """
    env = clean_env()
    before_start = None
    if limit is not None:
        import resource
        import signal

        def cap_file_size() -> None:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # the write fails; the run goes on
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

        before_start = cap_file_size
        # .NET maps its own code through a file larger than any limit: not under one.
        env["DOTNET_EnableWriteXorExecute"] = "0"
        # PowerShell's own caches are files too: the limit must not cut the developer's.
        env["XDG_CACHE_HOME"] = str(kit.parent / "caches of the limited run")
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(kit / "install.ps1")]
        + ["-Target", str(target), *args],
        cwd=kit.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
        preexec_fn=before_start,
    )


def _installed(kit: Path, target: Path, *args: str) -> str:
    result = _install(kit, target, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.strip() == ""
    return result.stdout


def _refused(kit: Path, target: Path | str, *args: str) -> str:
    """Exit 1 with one `install:` line and nothing on stderr; returns stdout."""
    result = _install(kit, target, *args)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stderr.strip() == ""
    assert "install: " in result.stdout
    return result.stdout


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every file (its bytes) and folder (None) under root."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _stamps(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file()
    }


@contextlib.contextmanager
def _unreadable(path: Path) -> Iterator[None]:
    """No other process can read `path` meanwhile: locked on Windows, no permission elsewhere."""
    if WINDOWS:
        import msvcrt

        with path.open("rb") as held:
            msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0)
    try:
        yield
    finally:
        path.chmod(mode)


@contextlib.contextmanager
def _read_only(*paths: Path) -> Iterator[None]:
    """The read-only mark on Windows, no write permission elsewhere."""
    modes = [stat.S_IMODE(path.stat().st_mode) for path in paths]
    for path in paths:
        path.chmod(stat.S_IREAD)
    try:
        yield
    finally:
        for path, mode in zip(paths, modes, strict=True):
            path.chmod(mode)


def _folder_link(link: Path, to: Path) -> None:
    """A link to a folder: a junction on Windows, where a symbolic link needs a privilege."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(to), str(link))
    else:
        link.symlink_to(to, target_is_directory=True)


# Why a test of letter case is skipped: the file system decides, not the system's name.
APART = "needs a file system that keeps Tools and tools apart (Linux)"
AS_ONE = "needs a file system that takes Tools for tools (Windows, macOS)"


def _keeps_letter_case_apart(folder: Path) -> bool:
    """True when `Tools` and `tools` are two names in `folder` (Linux; not Windows, and
    not macOS as it is installed)."""
    probe = folder / "Letter Case Probe"
    probe.mkdir()
    try:
        return not (folder / "letter case probe").exists()
    finally:
        probe.rmdir()


def _kit_files(kit: Path) -> set[str]:
    """What the kit owns in a target: tools/ and the DevTools addon, minus what stays behind."""
    found = set()
    for tree in ("tools", "addons/agent_devtools"):
        for path in (kit / tree).rglob("*"):
            relative = path.relative_to(kit).as_posix()
            if path.is_file() and not relative.startswith("tools/tests/"):
                found.add(relative)
    return found - {PROJECT_DATA}


def _manifest(target: Path) -> dict:
    return json.loads(_read(target / "tools" / "kit_manifest.json"))


def _lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _section_keys(text: str, section: str) -> list[str]:
    body = re.search(rf"^\[{section}\]\n(.*?)(?=^\[\w+\]\n|\Z)", text, re.M | re.S)
    assert body is not None, f"no [{section}] in:\n{text}"
    return re.findall(r"^([\w/]+)=", body[1], re.M)


def _without_kit_entries(text: str, record: list[str]) -> str:
    """project.godot with `wrote.project_godot` undone as docs/tools.md says, entry
    by entry. The kit's sections come after [application], whose description holds a line
    that looks like a header: the last match is the section."""
    lines = text.split("\n")

    def span(section: str) -> tuple[int, int]:
        start = max(index for index, line in enumerate(lines) if line == f"[{section}]")
        later = (i for i in range(start + 1, len(lines)) if re.fullmatch(r"\[\w+\]", lines[i]))
        return start, next(later, len(lines))

    def entry_line(section: str, key: str) -> int:
        start, end = span(section)
        (found,) = [i for i in range(start, end) if lines[i].startswith(f"{key}=")]
        return found

    made = []
    for entry in record:
        section, what = re.fullmatch(r"\[(\w+)\] (.+)", entry).groups()
        replaced = re.fullmatch(r"(\w+) (\S+) -> 2", what)
        if what == "new section":
            made.append(section)
        elif section == "autoload":
            assert what == "DevTools"
            del lines[entry_line(section, "DevTools")]
        elif section == "devtools":
            assert what == "allow_mutation=false"
            del lines[entry_line(section, "allow_mutation")]
        elif section == "editor_plugins":
            at = entry_line(section, "enabled")
            for plugin in what.split(", "):
                path = f'"res://addons/{plugin}/plugin.cfg"'
                assert path in lines[at]
                lines[at] = lines[at].replace(f", {path}", "").replace(f"{path}, ", "")
                lines[at] = lines[at].replace(path, "")
        elif replaced:
            at = entry_line(section, f"gdscript/warnings/{replaced[1]}")
            lines[at] = f"gdscript/warnings/{replaced[1]}={replaced[2]}"
        else:
            assert section == "debug" and what.endswith(" added"), entry
            at = entry_line(section, f"gdscript/warnings/{what.removesuffix(' added')}")
            assert lines[at].endswith("=2")
            del lines[at]
    for section in made:
        start, end = span(section)
        assert set(lines[start + 1 : end]) <= {"", "enabled=PackedStringArray()"}, section
        del lines[start:end]
    return "\n".join(lines)


def test_blank_project_gets_the_kit(kit: Path, target: Path) -> None:
    assert (kit / "tools" / "tests").is_dir() and (kit / PROJECT_DATA).is_file()
    # What a file manager left in the kit's folders is not the kit's.
    litter = [
        f"{folder}/{name}" for folder in ("tools/ci", "docs", "addons/gdUnit4") for name in LITTER
    ]
    for relative in litter:
        _write(kit / relative, "left by a file manager\n")
    out = _installed(kit, target)
    assert not [path.name for path in target.rglob("*") if path.name in LITTER]
    for relative in litter:
        (kit / relative).unlink()

    # The kit's files, byte for byte, .uid sidecars included; not its tests or project data.
    owned = _kit_files(kit)
    assert "tools/check.ps1" in owned and "tools/headless_tool.gd.uid" in owned
    assert "addons/agent_devtools/dev_tools.gd.uid" in owned
    for relative in owned:
        assert (target / relative).read_bytes() == (kit / relative).read_bytes(), relative
    assert not (target / "tools" / "tests").exists()
    assert not (target / "test" / "kit").exists()
    assert not (target / PROJECT_DATA).exists()
    assert not list(target.rglob("__pycache__"))
    assert (target / "addons" / "gdUnit4" / "plugin.cfg").is_file()

    manifest = _manifest(target)
    assert manifest["kit_version"] == _read(kit / "VERSION").strip()
    assert set(manifest["files"]) == owned
    assert manifest["files"]["tools/check.ps1"] == _lf_sha256(kit / "tools" / "check.ps1")
    # The kit's licence travels with its tools.
    assert _read(target / "tools" / "LICENSE") == _read(kit / "LICENSE")
    assert _read(kit / "addons" / "agent_devtools" / "LICENSE") == _read(kit / "LICENSE")

    docs = [
        path.relative_to(kit).as_posix() for path in (kit / "docs").rglob("*") if path.is_file()
    ]
    # The README's pictures stay behind: the kit's README is not installed.
    pictures = [relative for relative in docs if relative.startswith("docs/images/")]
    assert pictures and not (target / "docs" / "images").exists()
    docs = [relative for relative in docs if relative not in pictures]
    for relative in (*SEEDS, *docs):
        if relative != HOOKS:  # written for a project the kit is a guest in: its own test
            assert (target / relative).read_bytes() == (kit / relative).read_bytes(), relative
    # Every starter file is recorded with the hash it was written with.
    assert set(manifest["seeded"]) == {*SEEDS, *docs}
    assert manifest["seeded"]["CLAUDE.md"] == _lf_sha256(kit / "CLAUDE.md")
    assert manifest["seeded"][HOOKS] == _lf_sha256(target / HOOKS)
    starter = _read(target / "test" / "unit" / "test_kit_smoke.gd")
    assert starter.startswith("extends GdUnitTestSuite\n") and "\r" not in starter
    assert re.fullmatch(r"uid://[a-y0-8]+\n", _read(target / "test/unit/test_kit_smoke.gd.uid"))
    # What removing the kit has to undo.
    assert manifest["wrote"] == {
        "kit_json": True,
        "gdunit4": True,
        "starter_test": "test/unit/test_kit_smoke.gd",
        "project_godot": [
            "[autoload] new section",
            "[autoload] DevTools",
            "[editor_plugins] new section",
            "[editor_plugins] agent_devtools, gdUnit4",
            "[devtools] new section",
            "[devtools] allow_mutation=false",
        ],
        "gitignore": [".godot/", "/artifacts/", "/.tools/", ".venv/", "__pycache__/"],
    }
    assert "unfinished" not in manifest  # only while a run is under way
    assert list(manifest) == ["kit_version", "hash", "seeded", "wrote", "files"]

    config = json.loads(_read(target / "kit.json"))
    pins = json.loads(_read(kit / "kit.json"))
    assert config["godot"] == pins["godot"] | {"search_roots": []}
    assert config["gdunit4"] == pins["gdunit4"]
    assert config["source"] == {"exclude": ["addons", "artifacts", "docs"], "include": []}
    assert config["tests"] == {"root": "test"}
    assert config["gdscript"] == {"strict": False}

    # project.godot: three entries in new sections, every other byte as it was.
    before = _project()
    cut = before.index("[rendering]")
    after = _read(target / "project.godot")
    assert after == before[:cut] + AUTOLOAD + DEVTOOLS + PLUGINS + before[cut:]
    assert _without_kit_entries(after, manifest["wrote"]["project_godot"]) == before
    assert _read(target / ".gitignore") == GITIGNORE  # none before: Godot's cache folder too

    assert f"{len(owned)} new, 0 updated, 0 unchanged" in out
    assert "note:" not in out
    assert "pwsh ./tools/setup.ps1 -InstallGodot" in out
    assert "pwsh ./tools/check.ps1" in out
    assert "gdscript.strict=false" in out and "-StrictWarnings" in out


def test_second_run_writes_nothing(kit: Path, target: Path) -> None:
    _installed(kit, target)
    files, stamps = _tree(target), _stamps(target)
    out = _installed(kit, target)
    assert "Up to date: nothing to write." in out
    assert f"0 new, 0 updated, {len(_kit_files(kit))} unchanged" in out
    assert _tree(target) == files
    assert _stamps(target) == stamps  # not even rewritten with the same bytes

    # A checkout that turned LF into CRLF is still the kit's file, and is left as it is.
    for relative in ("tools/check.ps1", "tools/headless_tool.gd", "tools/kit_manifest.json"):
        path = target / relative
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    files = _tree(target)
    assert "Up to date: nothing to write." in _installed(kit, target)
    assert _tree(target) == files


def test_a_newer_kit_updates_its_files_and_leaves_yours(kit: Path, target: Path) -> None:
    # The project already has a tools/ folder with a script of its own.
    mine = _write(target / "tools" / "build.ps1", "Write-Host 'my build'\n")
    _installed(kit, target)
    assert _read(mine) == "Write-Host 'my build'\n"
    assert "tools/build.ps1" not in _manifest(target)["files"]

    # The next kit: one file changed, one added, two gone; one of the gone was edited here.
    changed = kit / "tools" / "check.ps1"
    changed.write_bytes(changed.read_bytes() + b"# a newer check\n")
    _write(kit / "tools" / "brand_new.ps1", "Write-Host 'new tool'\n")
    for gone in ("validate_all.gd", "validate_all.gd.uid", "lint_shaders.gd"):
        (kit / "tools" / gone).unlink()
    edited = target / "tools" / "lint_shaders.gd"
    edited.write_bytes(edited.read_bytes() + b"# my change\n")
    # And a starter file gone: the project's copy is the project's.
    (kit / "docs" / "gotchas.md").unlink()
    page = (target / "docs" / "gotchas.md").read_bytes()
    _write(kit / "VERSION", "9.9.9\n")

    out = _installed(kit, target)
    assert "1 new, 1 updated" in out and "2 removed" in out
    assert "~ tools/check.ps1" in out and "- tools/validate_all.gd" in out
    assert "! tools/lint_shaders.gd" in out and "you changed it" in out
    assert (target / "tools" / "check.ps1").read_bytes() == changed.read_bytes()
    assert (target / "tools" / "brand_new.ps1").is_file()
    assert not (target / "tools" / "validate_all.gd").exists()
    assert not (target / "tools" / "validate_all.gd.uid").exists()
    assert edited.read_bytes().endswith(b"# my change\n")  # kept, and no longer the kit's
    manifest = _manifest(target)
    assert manifest["kit_version"] == "9.9.9"
    assert set(manifest["files"]) == _kit_files(kit)
    assert _read(mine) == "Write-Host 'my build'\n"
    # The dropped starter file stays, on record (for whoever removes the kit) with no hash.
    assert "! docs/gotchas.md   (no longer part of the kit; yours to keep or delete)" in out
    assert (target / "docs" / "gotchas.md").read_bytes() == page
    assert manifest["seeded"]["docs/gotchas.md"] == ""
    assert manifest["seeded"]["docs/tools.md"] == _lf_sha256(kit / "docs" / "tools.md")
    # Said once. The record goes when the file does.
    out = _installed(kit, target)
    assert "Up to date" in out and "docs/gotchas.md" not in out
    assert "docs/gotchas.md" in _manifest(target)["seeded"]
    (target / "docs" / "gotchas.md").unlink()
    _installed(kit, target)
    assert "docs/gotchas.md" not in _manifest(target)["seeded"]
    assert not (target / "docs" / "gotchas.md").exists()


def test_a_newer_kit_updates_the_starter_files_you_left_alone(kit: Path, target: Path) -> None:
    mine = _write(target / ".editorconfig", "root = true\n# mine from the start\n")
    _installed(kit, target)
    edited = target / "docs" / "devtools.md"
    edited.write_bytes(edited.read_bytes() + b"\nMy own note.\n")
    edited_text = _read(edited)

    for name in ("docs/tools.md", "docs/devtools.md", "CLAUDE.md", ".editorconfig"):
        (kit / name).write_bytes((kit / name).read_bytes() + b"\nNewer kit text.\n")
    before = _tree(target)
    out = _installed(kit, target, "-DryRun")
    assert _tree(target) == before
    for line in (
        "~ docs/tools.md",
        "~ CLAUDE.md",
        "! docs/devtools.md   (yours kept; the kit's changed since you got it)",
        "= .editorconfig   (yours kept; the kit's copy is not installed)",
        "starter files  0 new, 2 updated, ",
        ", 2 yours kept",
    ):
        assert line in out, line

    out = _installed(kit, target)
    assert _read(target / "docs" / "tools.md") == _read(kit / "docs" / "tools.md")
    assert _read(target / "CLAUDE.md") == _read(kit / "CLAUDE.md")
    assert _read(edited) == edited_text and _read(mine) == "root = true\n# mine from the start\n"
    assert "! docs/devtools.md" in out
    assert ".editorconfig" not in _manifest(target)["seeded"]
    # Said once; the edited file is compared with the kit's next change from here on.
    # -Force is for the kit's files: it overwrites no starter file you changed.
    out = _installed(kit, target, "-Force")
    assert "Up to date" in out and "docs/devtools.md" not in out
    assert _read(edited) == edited_text and _read(mine) == "root = true\n# mine from the start\n"

    # A pyproject.toml the project changed keeps its own lock file.
    project = target / "pyproject.toml"
    project.write_bytes(project.read_bytes() + b"# uv add was here\n")
    lock = (target / "uv.lock").read_bytes()
    (kit / "uv.lock").write_bytes((kit / "uv.lock").read_bytes() + b"# newer\n")
    assert "Up to date" in _installed(kit, target)
    assert (target / "uv.lock").read_bytes() == lock


def test_a_file_with_the_kits_text_that_was_yours_from_the_start_stays_yours(
    kit: Path, target: Path
) -> None:
    # The same bytes as the kit's starter file, written by the project's owner: no run
    # may take it for the kit's and bring it "up to date".
    mine = _write(target / ".python-version", _read(kit / ".python-version"))
    _write(target / "pyproject.toml", '[project]\nname = "mine"\nversion = "1"\n')
    out = _installed(kit, target)
    assert "~ .python-version" not in out
    seeded = _manifest(target)["seeded"]
    assert ".python-version" not in seeded and "pyproject.toml" not in seeded
    assert "CLAUDE.md" in seeded

    text = _read(mine)
    _write(kit / ".python-version", "3.99\n")
    _write(kit / "VERSION", "9.9.9\n")
    out = _installed(kit, target, "-Force")
    assert ".python-version" not in out
    assert _read(mine) == text
    assert ".python-version" not in _manifest(target)["seeded"]


def test_a_starter_file_you_deleted_stays_deleted(kit: Path, target: Path) -> None:
    _installed(kit, target)
    gone = ("CLAUDE.md", "AGENTS.md", "docs/gotchas.md", "uv.lock", ".github/workflows/ci.yml")
    starter = ("test/unit/test_kit_smoke.gd", "test/unit/test_kit_smoke.gd.uid")
    for relative in (*gone, *starter):
        (target / relative).unlink()
    files = _tree(target)
    out = _installed(kit, target)
    assert "Up to date: nothing to write." in out and "starter test" not in out
    assert _tree(target) == files

    # Also when the kit's copy changes. The record stays: put the file back and it follows.
    for relative in ("CLAUDE.md", "AGENTS.md", "docs/gotchas.md"):
        (kit / relative).write_bytes((kit / relative).read_bytes() + b"\nNewer kit text.\n")
    out = _installed(kit, target)
    for relative in (*gone, *starter, "AGENTS.kit.md"):
        assert not (target / relative).exists(), relative
        assert relative not in out, relative
    manifest = _manifest(target)
    assert set(gone) <= set(manifest["seeded"])
    assert manifest["wrote"]["starter_test"] == "test/unit/test_kit_smoke.gd"


def test_an_older_kit_does_not_replace_a_newer_one(kit: Path, target: Path) -> None:
    _installed(kit, target)
    manifest = _manifest(target)
    version = _read(kit / "VERSION").strip()
    refusals = {
        "99.0.0": f"install: the project has kit 99.0.0, this kit is {version}. Pull the kit",
        # Not a version this script can compare: it does not guess which is newer.
        "0.9.0-rc1": f"install: the project has kit 0.9.0-rc1, this kit is {version}: "
        "which is newer cannot be told. Run again with -Force",
    }
    for recorded, message in refusals.items():
        manifest["kit_version"] = recorded
        _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
        before = _tree(target)
        for args in ((), ("-DryRun",)):
            out = _refused(kit, target, *args)
            assert message in out
            assert len(out.strip().splitlines()) == 1, out
            assert _tree(target) == before
    _installed(kit, target, "-Force")
    assert _manifest(target)["kit_version"] == version


def test_a_kit_file_you_changed_is_a_conflict(kit: Path, target: Path) -> None:
    _installed(kit, target)
    edited = target / "tools" / "check.ps1"
    edited.write_bytes(edited.read_bytes() + b"# my local tweak\n")
    other = kit / "tools" / "doctor.ps1"
    other.write_bytes(other.read_bytes() + b"# newer doctor\n")
    before = _tree(target)

    out = _refused(kit, target)
    assert "1 kit file(s) in the target differ" in out
    assert "\n  tools/check.ps1\n" in out and "-Force" in out
    assert "tools/doctor.ps1" not in out
    assert _tree(target) == before  # nothing written: not even the update that had no conflict

    out = _installed(kit, target, "-Force")
    assert "~ tools/check.ps1" in out
    assert edited.read_bytes() == (kit / "tools" / "check.ps1").read_bytes()
    assert (target / "tools" / "doctor.ps1").read_bytes() == other.read_bytes()


def test_a_file_of_yours_with_a_kit_name_is_never_overwritten(kit: Path, target: Path) -> None:
    _write(target / "tools" / "check.ps1", "Write-Host 'my own check'\n")
    before = _tree(target)
    for args in ((), ("-Force",), ("-DryRun",), ("-Force", "-DryRun")):
        out = _refused(kit, target, *args)
        assert (
            "install: 1 file(s) of yours have the name of a kit file:\n  tools/check.ps1\n" in out
        )
        assert "Rename or move them, then run again." in out
        assert "-Force" not in out and "template" not in out
        assert _tree(target) == before

    # The same on an upgrade, when a newer kit ships a name the project already uses.
    (target / "tools" / "check.ps1").unlink()
    _installed(kit, target)
    mine = _write(target / "tools" / "my_build.ps1", "Write-Host 'mine'\n")
    _write(kit / "tools" / "my_build.ps1", "Write-Host 'the kit grew this name'\n")
    before = _tree(target)
    out = _refused(kit, target, "-Force")
    assert "\n  tools/my_build.ps1\n" in out and "of yours" in out
    assert _tree(target) == before and _read(mine) == "Write-Host 'mine'\n"


def test_a_game_made_from_the_template_is_told_how_to_upgrade(kit: Path, tmp_path: Path) -> None:
    """It has the kit's files and no manifest: a file the newer kit changed cannot be told
    from one the owner wrote, so it is left alone and the way out is printed."""
    game = _copy_kit(tmp_path / "template game")
    shutil.rmtree(game / "tools" / "tests")
    version = _read(game / "VERSION").strip()
    # The same version first. A kit tool the owner edited stops it like any file of theirs,
    # without the advice to do what they are doing.
    tool = game / "tools" / "check.ps1"
    shipped = tool.read_bytes()
    tool.write_bytes(shipped + b"# my gate\n")
    out = _refused(kit, game)
    assert "1 file(s) of yours have the name of a kit file:\n  tools/check.ps1\n" in out
    assert "the kit's template" not in out
    tool.write_bytes(shipped)
    # Every kit file is recorded, nothing is rewritten; the starter files are the kit's.
    out = _installed(kit, game)
    assert "0 new, 0 updated" in out and "tools/kit_manifest.json   (commit it)" in out
    assert {"CLAUDE.md", ".python-version", "docs/tools.md"} <= set(_manifest(game)["seeded"])
    assert "Up to date" in _installed(kit, game)
    (game / "tools" / "kit_manifest.json").unlink()

    # A newer kit that changes none of the game's kit files (one dropped, one new, a page
    # edited) would install, drop nothing and leave the game on no version at all.
    _write(kit / "VERSION", "9.9.9\n")
    (kit / "tools" / "lint_shaders.gd").unlink()
    _write(kit / "tools" / "brand_new.ps1", "Write-Host 'new tool'\n")
    (kit / "CLAUDE.md").write_bytes((kit / "CLAUDE.md").read_bytes() + b"\nNewer kit text.\n")
    before = _tree(game)
    for args in (("-DryRun",), ("-Force",)):
        out = _refused(kit, game, *args)
        assert "install: the game has the kit's files and no tools/kit_manifest.json" in out
        assert f"at kit {version} by its VERSION file" in out
        assert f"run the install.ps1 of kit {version} on it and commit the manifest" in out
        assert len(out.strip().splitlines()) == 1, out
        assert _tree(game) == before

    # Without VERSION nothing says where the game started: said when a kit file differs.
    (game / "VERSION").unlink()
    changed = kit / "tools" / "doctor.ps1"
    changed.write_bytes(changed.read_bytes() + b"# a newer doctor\n")
    before = _tree(game)
    out = _refused(kit, game, "-Force")
    assert "1 file(s) of yours have the name of a kit file:\n  tools/doctor.ps1\n" in out
    assert "A game made from the kit's template has no tools/kit_manifest.json" in out
    assert "run the install.ps1 of the kit version the game started from" in out
    assert _tree(game) == before


@pytest.mark.parametrize(
    "key", ["tools/..\\..\\victim.txt", "tools/../../victim.txt", "tools\\..\\..\\victim.txt"]
)
def test_a_manifest_that_names_a_file_outside_the_project_is_refused(
    kit: Path, target: Path, key: str
) -> None:
    # A manifest from a cloned repository is someone else's text: it must not pick what
    # gets deleted. The entry carries the outside file's own hash, as "unmodified" needs.
    _installed(kit, target)
    victim = _write(target.parent / "victim.txt", "not the kit's\n")
    manifest = _manifest(target)
    manifest["files"][key] = _lf_sha256(victim)
    _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
    before = _tree(target)
    for args in ((), ("-DryRun",), ("-Force",)):
        out = _refused(kit, target, *args)
        assert f"install: the target's tools/kit_manifest.json lists '{key}'" in out
        assert "not a file inside the project" in out
        assert len(out.strip().splitlines()) == 1, out
        assert _read(victim) == "not the kit's\n"
        assert _tree(target) == before


def test_a_manifest_entry_removes_nothing_the_kit_does_not_install(kit: Path, target: Path) -> None:
    # Only the kit's own files are ever removed. An entry with the file's true hash is what
    # "the kit installed it and you did not change it" looks like: it must not be enough.
    mine = {
        "tools/tests/test_mine.py": "def test_mine():\n    assert True\n",
        PROJECT_DATA: '{"modules": {}}\n',
        "tools/ci/slow_suites.txt": "res://test/unit/test_slow.gd\n",
        "README.md": "# My game\n",
        "src/game.gd": "extends Node\n",
    }
    for relative, text in mine.items():
        _write(target / relative, text)
    # The kit's tests folder under another spelling: the same folder to Windows, a second
    # folder where letter case counts. Never removed there either.
    if _keeps_letter_case_apart(target):
        mine["tools/Tests/test_mine.py"] = mine["tools/tests/test_mine.py"]
        _write(target / "tools/Tests/test_mine.py", mine["tools/Tests/test_mine.py"])
    _installed(kit, target)
    manifest = _manifest(target)
    for relative in mine:
        listed = relative.replace("tools/tests/", "tools/Tests/")
        manifest["files"][listed] = _lf_sha256(target / relative)
    assert "tools/Tests/test_mine.py" in manifest["files"]
    _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
    for args in (("-DryRun",), ("-Force",)):
        out = _installed(kit, target, *args)
        assert "removed" not in out
        for relative, text in mine.items():
            assert _read(target / relative) == text, relative
            assert relative not in out, relative
    assert set(_manifest(target)["files"]) == _kit_files(kit)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is not a JSON object"),
        ("[]\n", "is not a JSON object"),
        ('"x"\n', "is not a JSON object"),
        ("null\n", "is not a JSON object"),
        ("{}\n", "has no kit_version"),
        ('{"kit_version": "0.1.0", "seeded": {}, "wrote": {}}\n', 'has no "files" object'),
    ],
)
def test_a_manifest_that_is_not_one_is_refused(
    kit: Path, target: Path, text: str, message: str
) -> None:
    # Read as "nothing on record", it would make every kit file there the project's own.
    _write(target / "tools" / "kit_manifest.json", text)
    before = _tree(target)
    out = _refused(kit, target, "-Force")
    assert f"install: the target's tools/kit_manifest.json {message}. Restore it from git" in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(target) == before


def test_a_gdunit4_copy_that_stops_halfway_is_finished_by_the_next_run(
    kit: Path, target: Path
) -> None:
    not_as_root()
    # plugin.cfg is copied before src/: a folder cut short still names the pinned version.
    last = _write(kit / "addons" / "gdUnit4" / "src" / "zz_last.gd", "extends RefCounted\n")
    with _unreadable(last):
        out = _refused(kit, target)
    assert "install: stopped halfway. Fix that, then run the same command again." in out
    # One line that names the file, on every system, then the one that says what to do.
    said = [line for line in out.splitlines() if line.startswith("install: ")]
    assert len(said) == 2 and "zz_last.gd" in said[0], out
    assert not (target / "addons" / "gdUnit4").exists()  # never a folder that is half there
    # The manifest already says what the run set out to write; no file is the kit's yet.
    manifest = _manifest(target)
    assert manifest["files"] == {} and manifest["seeded"] == {}
    assert manifest["wrote"]["gdunit4"] is True and manifest["wrote"]["kit_json"] is True
    assert {"CLAUDE.md", "AGENTS.md", "test/unit/test_kit_smoke.gd"} <= set(manifest["unfinished"])
    assert not (target / "CLAUDE.md").exists() and not (target / "kit.json").exists()

    out = _installed(kit, target)
    assert "gdUnit4        new: " in out and "3 files" in out
    assert "starter test   test/unit/test_kit_smoke.gd" in out
    manifest = _manifest(target)
    assert "unfinished" not in manifest and set(manifest["files"]) == _kit_files(kit)
    assert (target / "test" / "unit" / "test_kit_smoke.gd").is_file()
    for relative in ("LICENSE", "plugin.cfg", "src/zz_last.gd"):
        copied = target / "addons" / "gdUnit4" / relative
        assert copied.read_bytes() == (kit / "addons" / "gdUnit4" / relative).read_bytes()
    assert sorted(path.name for path in (target / "addons").iterdir()) == [
        "agent_devtools",
        "gdUnit4",
    ]
    assert "Up to date" in _installed(kit, target)

    # What a killed run leaves behind is cleared, also beside a gdUnit4 of the project's own.
    _write(target / "addons" / ".gdUnit4.kit-partial" / "plugin.cfg", "half\n")
    assert "gdUnit4.kit-partial" in _installed(kit, target, "-DryRun")
    assert (target / "addons" / ".gdUnit4.kit-partial").is_dir()
    assert "Up to date" not in _installed(kit, target)
    assert not (target / "addons" / ".gdUnit4.kit-partial").exists()
    assert "Up to date" in _installed(kit, target)


def test_a_run_that_stops_halfway_loses_no_record(kit: Path, target: Path) -> None:
    """The manifest is what removing the kit goes by, and what tells the kit's files from
    yours: a run that stops after it wrote files must leave both true for the next run."""
    debug = "[debug]\n\ngdscript/warnings/untyped_declaration=1\n"
    project = _write(target / "project.godot", _project(debug=debug))
    ignore = _write(target / ".gitignore", ".godot/\n")
    # Yours from the start, with the kit's text: not the kit's after a stopped run either.
    mine = _write(target / ".python-version", _read(kit / ".python-version"))
    wrote = {
        "kit_json": True,
        "gdunit4": True,
        "starter_test": "test/unit/test_kit_smoke.gd",
        "project_godot": [
            "[autoload] new section",
            "[autoload] DevTools",
            "[editor_plugins] new section",
            "[editor_plugins] agent_devtools, gdUnit4",
            "[devtools] new section",
            "[devtools] allow_mutation=false",
        ],
        "gitignore": ["/artifacts/", "/.tools/", ".venv/", "__pycache__/"],
    }

    # A first install that cannot write the starter test: kit and starter files and kit.json
    # are written by then; project.godot and .gitignore are not.
    not_as_root()
    (target / "test").mkdir()
    with takes_no_new_file(target / "test"):
        out = _refused(kit, target)
    assert "install: stopped halfway. Fix that, then run the same command again." in out
    assert (target / "kit.json").is_file() and (target / "CLAUDE.md").is_file()
    assert "DevTools=" not in _read(project) and _read(ignore) == ".godot/\n"
    manifest = _manifest(target)
    assert manifest["wrote"] == wrote
    assert manifest["files"] == {} and manifest["seeded"] == {}
    assert "CLAUDE.md" in manifest["unfinished"] and ".python-version" not in manifest["unfinished"]

    _installed(kit, target)
    manifest = _manifest(target)
    assert manifest["wrote"] == wrote and "unfinished" not in manifest
    assert set(manifest["files"]) == _kit_files(kit)
    # The starter files the stopped run wrote are the kit's; the one that was there is not.
    assert {"CLAUDE.md", "AGENTS.md", "docs/tools.md", "uv.lock"} <= set(manifest["seeded"])
    assert ".python-version" not in manifest["seeded"]
    assert "Up to date" in _installed(kit, target)

    # An upgrade that cannot write its last starter file: the kit's files and the other
    # starter files are written by then; project.godot is not.
    tool = kit / "tools" / "check.ps1"
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    (kit / "CLAUDE.md").write_bytes((kit / "CLAUDE.md").read_bytes() + b"\nNewer kit text.\n")
    _write(kit / "docs" / "new_page.md", "# New in this kit\n")
    _write(kit / "docs" / "zz" / "late.md", "# The last one written\n")
    _write(kit / ".python-version", "3.99\n")
    _write(kit / "VERSION", "9.9.9\n")
    old = manifest["files"]["tools/check.ps1"]
    (target / "docs" / "zz").mkdir()
    with takes_no_new_file(target / "docs" / "zz"):
        out = _refused(kit, target, "-StrictWarnings")
    assert "install: stopped halfway." in out
    assert (target / "tools" / "check.ps1").read_bytes() == tool.read_bytes()
    assert (target / "docs" / "new_page.md").is_file()
    assert not (target / "docs" / "zz" / "late.md").exists()
    manifest = _manifest(target)
    # Still the old kit on record, so the files not yet updated are not taken for edits.
    assert manifest["kit_version"] == _read(REPO / "VERSION").strip()
    assert manifest["files"]["tools/check.ps1"] == old
    assert "docs/new_page.md" not in manifest["seeded"]
    assert set(manifest["unfinished"]) == {"CLAUDE.md", "docs/new_page.md", "docs/zz/late.md"}
    strict = ["[debug] untyped_declaration 1 -> 2"]
    strict += [f"[debug] {name} added" for name in _strict_names(kit)[1:]]
    wrote["project_godot"] += strict
    assert manifest["wrote"] == wrote  # though project.godot does not have them yet
    assert "untyped_declaration=1\n" in _read(project)

    # Finished by a kit that moved on meanwhile: what the stopped run wrote is the kit's
    # to update, though "files" and "seeded" never recorded it.
    for relative in ("CLAUDE.md", "docs/new_page.md", "tools/doctor.ps1"):
        (kit / relative).write_bytes((kit / relative).read_bytes() + b"\n# Newer still.\n")
    _write(kit / "VERSION", "9.9.10\n")
    out = _installed(kit, target, "-StrictWarnings")
    assert "install:" not in out and "[debug] untyped_declaration 1 -> 2" in out
    for line in ("~ tools/doctor.ps1", "~ CLAUDE.md", "~ docs/new_page.md"):
        assert line in out, line
    manifest = _manifest(target)
    assert manifest["kit_version"] == "9.9.10" and "unfinished" not in manifest
    assert manifest["wrote"] == wrote
    assert manifest["files"]["tools/check.ps1"] == _lf_sha256(tool)
    for relative in ("CLAUDE.md", "docs/new_page.md", "docs/zz/late.md"):
        assert (target / relative).read_bytes() == (kit / relative).read_bytes()
        assert manifest["seeded"][relative] == _lf_sha256(kit / relative)
    assert ".python-version" not in manifest["seeded"] and _read(mine) != "3.99\n"
    assert _without_kit_entries(_read(project), wrote["project_godot"]) == _project(debug=debug)
    assert "Up to date" in _installed(kit, target, "-StrictWarnings")


@posix_only("a limit on the size of the files of one process (RLIMIT_FSIZE)")
@pytest.mark.parametrize(
    "big",
    [
        "project.godot",  # the project's own, rewritten
        ".gitignore",  # the project's own, added to: the last write but the manifest
        "tools/big_table.gd",  # a new kit file
        "docs/big.md",  # a new starter file
        "tools/check.ps1",  # a kit file an update replaces
        "CLAUDE.md",  # a starter file an update replaces
    ],
)
def test_a_write_that_fails_part_way_leaves_every_file_whole(
    kit: Path, target: Path, tmp_path: Path, big: str
) -> None:
    """A full disk cuts a file where the space ends. No file of the project is ever such a
    file: each is whole, as it was or as the kit writes it, and the next run finishes."""
    limit = 2 * max(path.stat().st_size for path in kit.rglob("*") if path.is_file())
    padding = "# " + "x" * (limit + 1000) + "\n"
    if big in ("tools/check.ps1", "CLAUDE.md"):
        _installed(kit, target)
    if big == "project.godot":
        _write(target / big, _project() + f'\n[zz]\n\nnotes="{padding}"\n')
    elif big == ".gitignore":
        # Twenty bytes short of the limit: lines added in place would be cut in the first.
        _write(target / big, ".godot/\n# " + "x" * (limit - 31) + "\n")
        assert (target / big).stat().st_size == limit - 20
    else:
        source = kit / big
        _write(source, (_read(source) if source.exists() else "") + padding)
    twin = tmp_path / "twin"
    shutil.copytree(target, twin)
    before = _tree(target)

    stopped = _install(kit, target, limit=limit)
    out = stopped.stdout
    assert stopped.returncode == 1 and stopped.stderr.strip() == "", out + stopped.stderr
    assert f"install: '{big}' was not written: " in out
    assert "install: stopped halfway. Fix that, then run the same command again." in out
    after = _tree(target)
    _installed(kit, twin)
    whole = _tree(twin)
    for name, data in after.items():
        if name != "tools/kit_manifest.json":
            assert name in whole, name  # no temporary file stays
            assert data == whole[name] or (name in before and data == before[name]), name
    assert after.get(big) == before.get(big)
    assert "unfinished" in _manifest(target)
    if big == ".gitignore":
        assert b"\nDevTools=" in after["project.godot"]  # written before the run stopped

    _installed(kit, target)
    assert _tree(target) == whole
    assert "Up to date" in _installed(kit, target)


@posix_only("permission bits")
def test_a_file_of_yours_that_is_rewritten_keeps_its_permissions(kit: Path, target: Path) -> None:
    """A file written beside its place and renamed would have the permissions of a new file."""
    project = target / "project.godot"
    ignore = _write(target / ".gitignore", ".godot/\n")
    project.chmod(0o640)
    ignore.chmod(0o600)
    _installed(kit, target)
    assert "\nDevTools=" in _read(project) and "/artifacts/" in _read(ignore)
    manifest = target / "tools" / "kit_manifest.json"
    manifest.chmod(0o660)
    tool = kit / "tools" / "check.ps1"
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    tool.chmod(0o755)
    assert "[debug]" in _installed(kit, target, "-StrictWarnings")
    modes = {path.name: stat.S_IMODE(path.stat().st_mode) for path in (project, ignore, manifest)}
    assert modes == {"project.godot": 0o640, ".gitignore": 0o600, "kit_manifest.json": 0o660}
    # A kit file has the kit's.
    assert stat.S_IMODE((target / "tools" / "check.ps1").stat().st_mode) == 0o755


def test_a_run_that_stops_after_project_godot_is_finished_by_the_next(
    kit: Path, target: Path, tmp_path: Path
) -> None:
    project = target / "project.godot"
    ignore = _write(target / ".gitignore", ".godot/\n")
    twin = tmp_path / "twin"
    shutil.copytree(target, twin)
    # In the way of the name .gitignore is written under before it takes its own: the one
    # write after project.godot's, on every system.
    in_the_way = target / ".gitignore.kit-new"
    _write(in_the_way / "mine.txt", "mine\n")
    out = _refused(kit, target)
    said = [line for line in out.splitlines() if line.startswith("install: ")]
    assert len(said) == 2 and said[0].startswith("install: '.gitignore' was not written: "), out
    assert said[1] == "install: stopped halfway. Fix that, then run the same command again."
    assert "\nDevTools=" in _read(project) and _read(ignore) == ".godot/\n"
    manifest = _manifest(target)
    assert "[autoload] DevTools" in manifest["wrote"]["project_godot"]
    assert manifest["wrote"]["gitignore"] == ["/artifacts/", "/.tools/", ".venv/", "__pycache__/"]
    assert manifest["files"] == {} and "CLAUDE.md" in manifest["unfinished"]

    shutil.rmtree(in_the_way)
    out = _installed(kit, target)
    assert "project.godot  no change" in out and ".gitignore     + /artifacts/" in out
    _installed(kit, twin)
    assert _tree(target) == _tree(twin)
    assert "Up to date" in _installed(kit, target)


def test_what_a_killed_run_left_is_deleted_by_the_next(kit: Path, target: Path) -> None:
    """A run killed while it writes leaves the file it was writing under its temporary
    name, beside the place it was for."""
    _write(target / ".gitignore", ".godot/\n")
    _installed(kit, target)
    left = [
        ".project.godot.kit-new",
        ".gitignore.kit-new",
        "tools/.check.ps1.kit-new",
        "tools/ci/.suites.py.kit-new",
        "docs/.tools.md.kit-new",
        "test/unit/.test_kit_smoke.gd.kit-new",
    ]
    # Not looked for outside the folders the kit writes into, nor behind a link.
    mine = _write(target / "levels" / ".one.tscn.kit-new", "mine\n")
    theirs = _write(target.parent / "shared docs" / ".logo.png.kit-new", "theirs\n")
    shutil.rmtree(target / ".github")
    _folder_link(target / ".github", theirs.parent)
    installed = _tree(target)
    for relative in left:
        _write(target / relative, "half a fi")
    (target / left[2]).chmod(stat.S_IREAD)  # as copied from a read-only kit

    out = _installed(kit, target, "-DryRun")
    assert f"left over      {len(left)} temporary file(s) of a run that was cut short" in out
    for relative in left:
        assert f"- {relative}\n" in out and (target / relative).is_file(), relative
    assert "levels" not in out and "logo" not in out
    out = _installed(kit, target)
    assert f"left over      {len(left)} temporary file(s)" in out and "Up to date" not in out
    assert _tree(target) == installed
    assert _read(mine) == "mine\n" and _read(theirs) == "theirs\n"
    assert "Up to date" in _installed(kit, target)

    # An update with one in its way writes the file whole.
    tool = kit / "tools" / "check.ps1"
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    _write(target / "tools" / ".check.ps1.kit-new", "half a fi")
    assert "~ tools/check.ps1" in _installed(kit, target)
    assert (target / "tools" / "check.ps1").read_bytes() == tool.read_bytes()
    assert not [path for path in (target / "tools").rglob("*.kit-new")]


@windows_only("a file another program has open cannot be replaced there")
def test_a_kit_file_a_program_has_open_is_not_changed_under_it(kit: Path, target: Path) -> None:
    """Open for reading, as a running tool has its script: it could be written into, and
    is not. The new file cannot take the name until the old one is closed."""
    _installed(kit, target)
    tool = kit / "tools" / "check.ps1"
    old = tool.read_bytes()
    tool.write_bytes(old + b"# a newer check\n")
    with (target / "tools" / "check.ps1").open("rb") as held:
        out = _refused(kit, target)
        said = [line for line in out.splitlines() if line.startswith("install: ")]
        assert len(said) == 2 and "'tools/check.ps1' was not written: " in said[0], out
        assert said[1] == "install: stopped halfway. Fix that, then run the same command again."
        assert held.read() == old
    assert not list((target / "tools").glob(".*.kit-new"))
    assert "~ tools/check.ps1" in _installed(kit, target)
    assert (target / "tools" / "check.ps1").read_bytes() == tool.read_bytes()
    assert "Up to date" in _installed(kit, target)


def test_starter_files_a_later_kit_dropped_are_listed_in_one_order(kit: Path, target: Path) -> None:
    """By code point, not by the language settings of the machine: two people who share a
    project would rewrite each other's manifest."""
    _installed(kit, target)
    dropped = ["zeta.md", "docs/_old.md", "docs/Old2.md", "Beta.md", ".alpha.md"]
    manifest = _manifest(target)
    for relative in dropped:
        _write(target / relative, "# Was a starter file\n")
        manifest["seeded"][relative] = "0" * 64
    _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
    out = _installed(kit, target)
    assert out.count("(no longer part of the kit; yours to keep or delete)") == len(dropped)
    seeded = _manifest(target)["seeded"]
    assert list(seeded)[-len(dropped) :] == [
        ".alpha.md",
        "Beta.md",
        "docs/Old2.md",
        "docs/_old.md",
        "zeta.md",
    ]
    assert all(seeded[relative] == "" for relative in dropped)
    assert "Up to date" in _installed(kit, target)


def _one_line_refusal(kit: Path, target: Path, *args: str) -> str:
    """Refused in one line, on a dry run too, with the project left as it was."""
    before, stamps = _tree(target), _stamps(target)
    for switches in (args, (*args, "-DryRun")):
        out = _refused(kit, target, *switches)
        assert len(out.strip().splitlines()) == 1, out
        assert "stopped halfway" not in out
        assert _tree(target) == before
        assert _stamps(target) == stamps  # opened to see whether it can be written, not written
    return out


def test_a_file_it_cannot_write_stops_the_run_before_the_first_write(
    kit: Path, target: Path
) -> None:
    not_as_root()
    ignore = _write(target / ".gitignore", ".godot/\n")
    with _read_only(ignore):
        out = _one_line_refusal(kit, target)
    assert "install: 1 file(s) in the project cannot be written (read-only" in out
    assert "'.gitignore'. Make them writable, then run again." in out
    _installed(kit, target)

    # An upgrade: a kit file it would replace, one it would delete, a starter file, and
    # project.godot. One it does not touch may be read-only.
    tool = kit / "tools" / "check.ps1"
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    (kit / "tools" / "lint_shaders.gd").unlink()
    (kit / "CLAUDE.md").write_bytes((kit / "CLAUDE.md").read_bytes() + b"\nNewer kit text.\n")
    written = (
        "tools/kit_manifest.json",
        "tools/check.ps1",
        "tools/lint_shaders.gd",
        "CLAUDE.md",
        "project.godot",
    )
    with _read_only(*(target / name for name in (*written, "tools/doctor.ps1", "player.gd"))):
        out = _one_line_refusal(kit, target, "-StrictWarnings")
        assert "install: 5 file(s) in the project cannot be written" in out
        assert "doctor.ps1" not in out and "player.gd" not in out
        for name in written:
            assert f"'{name}'" in out, name
    out = _installed(kit, target, "-StrictWarnings")
    assert "~ tools/check.ps1" in out and "- tools/lint_shaders.gd" in out and "~ CLAUDE.md" in out


def test_a_project_godot_that_cannot_be_read_is_not_called_bad_text(
    kit: Path, target: Path
) -> None:
    """Open in another program, or no permission: the run gives the system's reason. It
    said "not UTF-8 text" and to save the file again from the editor."""
    not_as_root()
    before = _tree(target)
    for args in ((), ("-DryRun",)):
        with _unreadable(target / "project.godot"):
            out = _refused(kit, target, *args)
        said = out.strip()
        assert len(said.splitlines()) == 1, out
        assert said.startswith("install: project.godot cannot be read: "), out
        assert said.endswith("or make it readable, then run again."), out
        assert "UTF-8" not in out
        assert _tree(target) == before
    _installed(kit, target)


def test_a_game_in_a_folder_of_its_repository_is_told_what_is_read_at_the_root(
    kit: Path, tmp_path: Path
) -> None:
    """GitHub reads .github/ and pre-commit its config at the repository's root alone:
    below it the workflow, the Copilot pointer and the hooks do nothing, and nothing said
    so."""
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    game = _target(repository / "games" / "one")
    wanted = (
        "note: the project is the folder 'games/one' of a git repository. GitHub and "
        "pre-commit read .github/workflows/ci.yml, .github/copilot-instructions.md, "
        ".pre-commit-config.yaml at the repository's root only: move them there and "
        "put 'games/one/' in front of the paths they name; the workflow also needs "
        "'working-directory: games/one' under 'defaults: run:'."
    )
    assert wanted in _installed(kit, game, "-DryRun")
    assert wanted in _installed(kit, game)
    # Said when the files are written, not at every update.
    assert "of a git repository" not in _installed(kit, game)
    # A file of the three that was the project's from the start is not the kit's to place.
    own = _target(repository / "games" / "two")
    for name in (".pre-commit-config.yaml", ".github/workflows/ci.yml"):
        _write(own / name, "# mine\n")
    out = _installed(kit, own, "-DryRun")
    assert "pre-commit read .github/copilot-instructions.md at the repository's root" in out
    # A project that is its repository's root gets no such note.
    (game / ".git").mkdir()
    shutil.rmtree(game / ".github")
    (game / "tools" / "kit_manifest.json").unlink()
    assert "of a git repository" not in _install(kit, game, "-DryRun").stdout


def test_a_read_only_kit_copy_installs_files_that_can_be_updated(kit: Path, target: Path) -> None:
    """A kit kept where nothing is writable (a mounted image, a package store): the copy
    would take the mark along, and the next update could not replace the file."""
    tool = kit / "tools" / "check.ps1"
    page = kit / "docs" / "tools.md"
    with _read_only(tool, page):
        _installed(kit, target)
        for relative in ("tools/check.ps1", "docs/tools.md"):
            assert os.access(target / relative, os.W_OK), relative
            assert stat.S_IMODE((target / relative).stat().st_mode) & stat.S_IWUSR, relative
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    assert "~ tools/check.ps1" in _installed(kit, target)


@pytest.mark.parametrize("linked", ["tools", "addons", "addons/agent_devtools", "docs", ".github"])
def test_no_file_is_written_through_a_link(kit: Path, target: Path, linked: str) -> None:
    """Git does not look behind a link, so `git status` would show nothing of an install
    that went through one, and the files would be someone else's folder."""
    elsewhere = target.parent / "shared by two games"
    _write(elsewhere / "mine.txt", "not the kit's\n")
    _folder_link(target / linked, elsewhere)
    out = _one_line_refusal(kit, target)
    assert "install: the kit writes no file through a link, and the project has 1 on the way" in out
    assert f"'{linked}' (to '" in out and "Put a real folder or file there" in out
    assert _tree(elsewhere) == {"mine.txt": b"not the kit's\n"}


def test_a_link_the_run_does_not_write_through_is_left_alone(kit: Path, target: Path) -> None:
    # The project's own gdUnit4, shared between games through a link: kept, as any gdUnit4.
    shared = target.parent / "shared gdUnit4"
    pinned = json.loads(_read(kit / "kit.json"))["gdunit4"]["version"]
    _write(shared / "plugin.cfg", f'[plugin]\n\nname="gdUnit4"\nversion="{pinned}"\n')
    _folder_link(target / "addons" / "gdUnit4", shared)
    out = _installed(kit, target)
    assert f"gdUnit4        yours kept: {pinned}, the version the kit pins" in out
    assert _tree(shared) == {"plugin.cfg": (shared / "plugin.cfg").read_bytes()}
    assert _manifest(target)["wrote"]["gdunit4"] is False
    assert "Up to date" in _installed(kit, target)


@posix_only("a symbolic link to a file, made without a privilege")
def test_a_link_where_a_file_goes_is_never_written_through(kit: Path, target: Path) -> None:
    # A starter file of the project's own that is a link is the project's: kept.
    shared = _write(target.parent / "shared.editorconfig", "root = true\n")
    (target / ".editorconfig").symlink_to(shared)
    # Links in the test folder are not followed, as the gates follow none: not round and
    # round the one that leads back, and not to the suite that lives elsewhere.
    (target / "test").mkdir()
    (target / "test" / "again").symlink_to(target / "test", target_is_directory=True)
    elsewhere = _write(
        target.parent / "their suites" / "test_theirs.gd", "extends GdUnitTestSuite\n"
    )
    (target / "test" / "theirs").symlink_to(elsewhere.parent, target_is_directory=True)
    # Where a kit file goes, where a starter file goes, where kit.json goes: each a link
    # that points nowhere, which a write would follow. Last, the file the run edits.
    nowhere = target.parent / "nowhere"
    real = target.parent / "real project.godot"
    for relative in ("tools/check.ps1", "addons/gdUnit4", "CLAUDE.md", "kit.json", "project.godot"):
        link = target / relative
        to = nowhere
        if relative == "project.godot":
            to = link.rename(real)
        made = not link.parent.exists()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(to)
        out = _one_line_refusal(kit, target)
        assert "the project has 1 on the way" in out and f"'{relative}' (to '{to}')" in out, out
        assert not nowhere.exists()
        link.unlink()
        if relative == "project.godot":
            real.rename(link)
        elif made:
            link.parent.rmdir()
    out = _installed(kit, target)
    assert "= .editorconfig   (yours kept; the kit's copy is not installed)" in out
    assert _read(shared) == "root = true\n" and (target / ".editorconfig").is_symlink()
    assert "starter test   test/unit/test_kit_smoke.gd" in out


def test_a_link_to_the_kit_is_the_kit(kit: Path, tmp_path: Path) -> None:
    before = _tree(kit)
    _folder_link(tmp_path / "my game", kit)
    out = _refused(kit, tmp_path / "my game")
    assert "install: -Target is the kit itself, or a folder inside it." in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(kit) == before


def test_the_kit_under_other_letters_is_the_kit_where_the_file_system_says_so(
    kit: Path, tmp_path: Path
) -> None:
    refusal = "install: -Target is the kit itself, or a folder inside it."
    other = tmp_path / kit.name.upper()
    assert str(other) != str(kit)
    if not _keeps_letter_case_apart(tmp_path):
        inside = _target(kit / "sample")
        assert refusal in _refused(kit, other / "sample")
        shutil.rmtree(inside)
        return
    # Two folders: a game in the one is no game inside the kit.
    game = _target(other / "game")
    _installed(kit, game)
    # A volume that takes one name for the other answers to the script's name in other
    # letters (macOS as it is installed): stood in for by a file of that name.
    _write(kit / "INSTALL.PS1", "")
    before = _tree(game)
    out = _refused(kit, game)
    assert refusal in out and len(out.strip().splitlines()) == 1, out
    assert _tree(game) == before


@pytest.mark.parametrize(
    ("theirs", "kits"),
    [
        ("Tools/build.ps1", "tools"),
        ("Addons/mine/plugin.cfg", "addons"),
        ("addons/GdUnit4/plugin.cfg", "addons/gdUnit4"),
        ("Docs/design.md", "docs"),
        ("agents.md", "AGENTS.md"),
        ("tools/Check.ps1", "tools/check.ps1"),
    ],
)
def test_a_name_in_another_letter_case_is_not_given_a_twin(
    kit: Path, target: Path, theirs: str, kits: str
) -> None:
    """`Tools/` in the project and `tools/` from the kit are two folders on Linux and one
    on Windows and macOS: a project with both cannot be checked out there."""
    if not _keeps_letter_case_apart(target.parent):
        pytest.skip(APART)
    _write(target / theirs, "mine\n")
    named = "/".join(theirs.split("/")[: kits.count("/") + 1])
    out = _one_line_refusal(kit, target)
    assert "install: 1 name(s) in the project differ from the kit's in letter case only: " in out
    assert f"'{named}' (the kit writes '{kits}')" in out and "Rename yours, then run again." in out
    # Renamed, it installs. (Not the script: under the kit's name it is a file of yours
    # with the name of a kit file.)
    if kits != "tools/check.ps1":
        (target / named).rename(target / kits)
        _installed(kit, target)


def test_a_project_with_both_spellings_keeps_both(kit: Path, target: Path) -> None:
    if not _keeps_letter_case_apart(target.parent):
        pytest.skip(APART)
    theirs = {"Tools/build.ps1": "mine\n", "tools/mine.ps1": "mine too\n", "Docs/x.md": "# X\n"}
    # Suites under both: the folder with the name as the kit spells it is the test root.
    theirs |= {
        "Tests/test_a.gd": "extends GdUnitTestSuite\n",
        "tests/b.gd": "extends GdUnitTestSuite\n",
    }
    for relative, text in theirs.items():
        _write(target / relative, text)
    (target / "docs").mkdir()
    _installed(kit, target)
    assert json.loads(_read(target / "kit.json"))["tests"] == {"root": "tests"}
    for relative, text in theirs.items():
        assert _read(target / relative) == text, relative
    assert (target / "tools" / "check.ps1").is_file() and (target / "docs" / "tools.md").is_file()
    assert sorted(path.name for path in (target / "Tools").iterdir()) == ["build.ps1"]
    assert "Up to date" in _installed(kit, target)


def test_where_letter_case_is_ignored_the_kit_goes_into_your_folder(
    kit: Path, target: Path
) -> None:
    if _keeps_letter_case_apart(target.parent):
        pytest.skip(AS_ONE)
    mine = _write(target / "Tools" / "build.ps1", "mine\n")
    _write(target / "Docs" / "design.md", "# Mine\n")
    # And says so, with the rename: AGENTS.md names docs/ and tools/ as the kit spells them,
    # so the agents-md gate fails there until then, and Linux refuses the project.
    note = (
        "note: 2 name(s) in the project differ from the kit's in letter case only: "
        "'Docs' (the kit writes 'docs'), 'Tools' (the kit writes 'tools'). This file system "
        "takes the two for one name, so the kit's files go into yours. Rename yours, in two "
        "steps (git mv Docs Docs-x, then git mv Docs-x docs): until then the agents-md gate fails"
    )
    assert note in _installed(kit, target, "-DryRun")
    assert note in _installed(kit, target)
    assert _read(mine) == "mine\n"
    assert sorted(path.name for path in target.iterdir() if path.name.lower() == "tools") == [
        "Tools"
    ]
    assert (target / "Tools" / "check.ps1").is_file() and (target / "Docs" / "tools.md").is_file()
    # The manifest spells its paths as the kit does, whatever the folder is called.
    manifest = _manifest(target)
    assert "tools/check.ps1" in manifest["files"] and "docs/tools.md" in manifest["seeded"]
    # Said at every run until the folders have the kit's names.
    again = _installed(kit, target)
    assert "Up to date" in again and note in again
    for theirs, kits in (("Docs", "docs"), ("Tools", "tools")):
        (target / theirs).rename(target / f"{theirs}-x")
        (target / f"{theirs}-x").rename(target / kits)
    renamed = _installed(kit, target)
    assert "Up to date" in renamed and "letter case" not in renamed


@pytest.mark.parametrize(
    ("theirs", "is_folder", "message"),
    [
        ("docs", False, "'docs' in the project is a file where the kit needs a folder (for docs/"),
        ("test", False, "'test' in the project is a file where the kit needs a folder (for test/"),
        ("CLAUDE.md", True, "'CLAUDE.md' in the project is a folder where the kit writes a file"),
        ("kit.json", True, "'kit.json' in the project is a folder where the kit writes a file"),
    ],
)
def test_a_file_where_the_kit_needs_a_folder_is_named_before_the_first_write(
    kit: Path, target: Path, theirs: str, is_folder: bool, message: str
) -> None:
    if is_folder:
        (target / theirs).mkdir()
    else:
        _write(target / theirs, "mine\n")
    out = _one_line_refusal(kit, target)
    assert f"install: {message}" in out and "Rename or move it, then run again." in out


def test_the_manifest_reads_the_same_on_every_system(kit: Path, target: Path) -> None:
    """A project is shared through git by people on Windows and on Linux: the manifest one
    of them commits is the other's, with whatever line ends their checkout gives the files."""
    _installed(kit, target)
    text = _read(target / "tools" / "kit_manifest.json")
    manifest = json.loads(text)
    assert "\\" not in text and "\r" not in text and text.endswith("}\n")
    names = [*manifest["files"], *manifest["seeded"]]
    assert all(re.fullmatch(r"[\w.-]+(/[\w.-]+)*", name) for name in names), names
    # In one order on every system: each of the kit's folders by code point.
    tools = [name for name in manifest["files"] if name.startswith("tools/")]
    assert tools == sorted(tools) and len(tools) > 20
    # As the other system checks it out: every text file with the other line ends.
    other = b"\n" if b"\r\n" in (target / "tools" / "check.ps1").read_bytes() else b"\r\n"
    for name in (*names, "tools/kit_manifest.json", "project.godot", ".gitignore", "kit.json"):
        data = (target / name).read_bytes()
        if b"\0" not in data:
            (target / name).write_bytes(data.replace(b"\r\n", b"\n").replace(b"\n", other))
    files = _tree(target)
    assert "Up to date: nothing to write." in _installed(kit, target)
    assert _tree(target) == files
    # And an update from there is an update, not a conflict.
    tool = kit / "tools" / "check.ps1"
    tool.write_bytes(tool.read_bytes() + b"# a newer check\n")
    (kit / "CLAUDE.md").write_bytes((kit / "CLAUDE.md").read_bytes() + b"\nNewer kit text.\n")
    out = _installed(kit, target)
    assert "~ tools/check.ps1" in out and "~ CLAUDE.md" in out
    after = _read(target / "tools" / "kit_manifest.json")
    assert "\\" not in after and "\r" not in after

    # A backslash is no separator in a manifest, on any system: such a path is refused,
    # not read as a file named `tools\build.ps1` here and as tools/build.ps1 there.
    manifest = json.loads(after)
    manifest["files"]["tools\\build.ps1"] = "0" * 64
    _write(target / "tools" / "kit_manifest.json", json.dumps(manifest, indent=2) + "\n")
    out = _one_line_refusal(kit, target)
    assert "lists 'tools\\build.ps1', which is not a file inside the project" in out


def test_a_gitignore_keeps_its_own_bytes(kit: Path, target: Path) -> None:
    # Added to, its own bytes kept: bytes that are not UTF-8 and a BOM both survive.
    mine = b"\xef\xbb\xbf/artifacts/\n# f\xfcr mich\n.godot/"
    (target / ".gitignore").write_bytes(mine)
    out = _installed(kit, target)
    assert ".gitignore     + /.tools/ .venv/ __pycache__/" in out
    assert (target / ".gitignore").read_bytes() == (
        mine + b"\n\n# godot-goblin-gearbox\n/.tools/\n.venv/\n__pycache__/\n"
    )
    assert "Up to date" in _installed(kit, target)


def test_a_utf16_gitignore_is_refused(kit: Path, target: Path) -> None:
    # What `echo .godot/ > .gitignore` writes in Windows PowerShell 5; git cannot read it.
    (target / ".gitignore").write_bytes(".godot/\r\n".encode("utf-16"))
    before = _tree(target)
    out = _refused(kit, target)
    assert "install: .gitignore is not UTF-8 text" in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(target) == before


def test_your_own_gdlintrc_judges_your_scripts_and_not_the_kits(kit: Path, target: Path) -> None:
    """max-line-length: 80 would fail the style gate on 104 lines of the kit's
    tools/*.gd, and `check.ps1 -Fix` would rewrite them into conflicts for the next upgrade."""
    gdtoolkit = Path(sys.executable).parent
    assert (gdtoolkit / venv_tool(REPO, "gdlint").name).is_file(), "run uv sync"
    _write(target / ".gdlintrc", "max-line-length: 80\n")
    out = _installed(kit, target)
    assert "note:" not in out
    assert "= .gdlintrc   (yours kept; the kit's copy is not installed)" in out
    assert _read(target / ".gdlintrc") == "max-line-length: 80\n"
    listed = [name for name in _manifest(target)["files"] if name.endswith(".gd")]
    kit_scripts = [name for name in listed if name.startswith("tools/")]
    assert len(kit_scripts) >= 8, kit_scripts
    # At least one of them is longer than this project allows.
    assert any(len(line) > 80 for name in kit_scripts for line in _read(target / name).splitlines())

    env = clean_env(PATH=str(gdtoolkit) + os.pathsep + os.environ.get("PATH", ""))
    before = _tree(target)
    for args in ((), ("-Fix",)):
        lint = run_tool(target, "lint_gdscript.ps1", *args, env=env)
        assert lint.returncode == 0, lint.stdout + lint.stderr
        # player.gd and the starter test; addons/ is excluded by the kit.json it got.
        assert "Linting 2 GDScript file(s)" in lint.stdout
        assert f"{len(kit_scripts)} kit file(s) left out: tools/kit_manifest.json" in lint.stdout
    assert _tree(target) == before  # -Fix rewrote nothing
    assert "Up to date" in _installed(kit, target)

    # The project's own script is still held to its own rule.
    _write(target / "player.gd", TYPED_SCRIPT + f'const NAME: String = "{"x" * 70}"\n')
    own = run_tool(target, "lint_gdscript.ps1", env=env)
    assert own.returncode == 1 and "player.gd:4: Error: Max allowed line length (80)" in own.stdout


def test_the_seeded_hooks_leave_the_kits_files_alone(kit: Path, target: Path) -> None:
    """pre-commit runs gdlint and gdformat on the files of a commit with the project's
    .gdlintrc: in an installed project that must not reach what the kit owns."""
    _installed(kit, target)
    seeded = _read(target / HOOKS)
    assert "\r" not in seeded and seeded.isascii()
    ids = r"^\s+- id: gd(?:lint|format)$"
    kit_text = _read(kit / HOOKS)
    for name, text in (("rev", r"^\s+rev: (\S+)$"), ("repo", r"^\s+- repo: (\S+)$"), ("ids", ids)):
        assert re.findall(text, seeded, re.M) == re.findall(text, kit_text, re.M) != [], name
    assert 'args: ["--line-length", "100"]' in seeded
    excludes = re.findall(r"^\s+exclude: (\S+)$", seeded, re.M)
    assert len(excludes) == 2 == len(re.findall(ids, seeded, re.M))
    owned = [name for name in _manifest(target)["files"] if name.endswith(".gd")]
    assert "tools/typecheck.gd" in owned and "addons/agent_devtools/dev_tools.gd" in owned
    for pattern in excludes:
        # pre-commit searches each file's path (forward slashes) for the pattern.
        assert all(re.search(pattern, name) for name in owned)
        assert re.search(pattern, "addons/gdUnit4/src/GdUnitTestSuite.gd")
        for mine in ("player.gd", "scripts/tools/x.gd", "test/unit/test_kit_smoke.gd"):
            assert not re.search(pattern, mine), mine

    # A project with hooks of its own keeps them.
    other = _target(target.parent / "second game")
    _write(other / HOOKS, "repos: []\n")
    _installed(kit, other)
    assert _read(other / HOOKS) == "repos: []\n"


def test_keys_and_sections_are_matched_as_godot_matches_them(kit: Path, target: Path) -> None:
    # Case matters to Godot, and a header may carry a comment.
    project = _project().replace(
        "[rendering]",
        '[autoload] ; my singletons\n\ndevtools="*res://game.gd"\n\n'
        "[Devtools]\n\nallow_mutation=true\n\n[rendering]",
    )
    _write(target / "project.godot", project)
    _installed(kit, target)
    after = _read(target / "project.godot")
    assert (
        '[autoload] ; my singletons\n\ndevtools="*res://game.gd"\n'
        'DevTools="*res://addons/agent_devtools/dev_tools.gd"\n\n'
        "[Devtools]\n\nallow_mutation=true\n\n" + DEVTOOLS + PLUGINS + "[rendering]"
    ) in after
    assert len(re.findall(r"^\[autoload\]", after, re.M)) == 2  # one is in the description
    assert "Up to date" in _installed(kit, target)


def test_your_agents_md_is_kept_and_the_kits_lands_beside_it(kit: Path, target: Path) -> None:
    mine = _write(target / "AGENTS.md", "# My rules\n")
    out = _installed(kit, target)
    assert "AGENTS.md      yours kept; the kit's is AGENTS.kit.md" in out
    assert _read(mine) == "# My rules\n"
    beside = target / "AGENTS.kit.md"
    assert _read(beside) == _read(kit / "AGENTS.md")
    assert "AGENTS.kit.md" in _manifest(target)["files"]
    assert "Up to date" in _installed(kit, target)

    # Merged and deleted: it stays away until the kit's AGENTS.md changes, and no line
    # points at a file that is not there.
    beside.unlink()
    out = _installed(kit, target)
    assert "Up to date" in out and "AGENTS" not in out
    assert not beside.exists()
    _write(kit / "AGENTS.md", "# AGENTS.md\n\nNewer rules.\n")
    _installed(kit, target)
    assert _read(beside) == "# AGENTS.md\n\nNewer rules.\n"
    assert _read(mine) == "# My rules\n"


def test_an_agents_md_from_the_kit_is_yours_after_the_first_run(kit: Path, target: Path) -> None:
    _installed(kit, target)
    agents = target / "AGENTS.md"
    assert _read(agents) == _read(kit / "AGENTS.md")
    _write(agents, "# AGENTS.md\n\nFilled in for my game.\n")
    assert "Up to date" in _installed(kit, target)
    assert not (target / "AGENTS.kit.md").exists()

    _write(kit / "AGENTS.md", "# AGENTS.md\n\nNewer rules.\n")
    out = _installed(kit, target)
    assert "the kit's changed since you got it: see AGENTS.kit.md" in out
    assert _read(target / "AGENTS.kit.md") == "# AGENTS.md\n\nNewer rules.\n"
    assert _read(agents) == "# AGENTS.md\n\nFilled in for my game.\n"
    out = _installed(kit, target)
    assert "Up to date" in out and "see AGENTS.kit.md" in out
    (target / "AGENTS.kit.md").unlink()
    out = _installed(kit, target)
    assert "Up to date" in out and "AGENTS" not in out


def test_crlf_project_godot_keeps_its_line_endings(kit: Path, target: Path) -> None:
    before = _project().replace("\n", "\r\n")
    _write(target / "project.godot", before)
    _write(target / ".gitignore", ".godot/\r\nartifacts\r\n")
    _installed(kit, target, "-StrictWarnings")
    after = _read(target / "project.godot")
    assert re.search(r"(?<!\r)\n", after) is None  # no bare LF crept in
    strict = "[debug]\n\n" + "".join(f"gdscript/warnings/{name}=2\n" for name in _strict_names(kit))
    lf = _project()
    cut = lf.index("[rendering]")
    expected = lf[:cut] + AUTOLOAD + strict + "\n" + DEVTOOLS + PLUGINS + lf[cut:]
    assert after == expected.replace("\n", "\r\n")
    # .gitignore: only what was missing, in the file's own line endings.
    assert _read(target / ".gitignore") == (
        ".godot/\r\nartifacts\r\n\r\n# godot-goblin-gearbox\r\n"
        "/.tools/\r\n.venv/\r\n__pycache__/\r\n"
    )


def _strict_names(kit: Path) -> list[str]:
    """The strict warnings in the order install.ps1 writes them: the one list the gates
    read too (Get-KitStrictWarnings), which is what the kit's own project.godot sets."""
    names = _kit_strict_warnings()
    assert len(names) == 16 and tuple(names) == STRICT_WARNINGS
    written = {
        key.removeprefix("gdscript/warnings/")
        for key in _section_keys(_read(kit / "project.godot"), "debug")
        if key != "gdscript/warnings/directory_rules"
    }
    assert written == set(names)
    return names


@functools.cache
def _kit_strict_warnings() -> list[str]:
    command = f". '{REPO / 'tools' / 'kit_config.ps1'}'; Get-KitStrictWarnings"
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    return asked.stdout.split()


def test_the_uv_add_line_names_the_packages_of_the_kits_own_pyproject() -> None:
    """install.ps1, doctor.ps1 and lint_gdscript.ps1 print it for a project whose
    pyproject.toml is its own. One list (Get-KitPythonTools), held to the kit's file."""
    import tomllib

    wanted = tomllib.loads(_read(REPO / "pyproject.toml"))["dependency-groups"]["dev"]
    command = f". '{REPO / 'tools' / 'kit_config.ps1'}'; Get-KitPythonToolsFix"
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    assert asked.stdout.strip() == "uv add --dev " + " ".join(f'"{name}"' for name in wanted)


def test_strict_warnings_sets_the_sixteen_and_says_what_it_replaced(
    kit: Path, target: Path
) -> None:
    debug = (
        "[debug]\n\n"
        'gdscript/warnings/directory_rules={\n"res://addons": 0,\n"res://addons/mine": 1\n}\n'
        "gdscript/warnings/untyped_declaration=1\n"
        "gdscript/warnings/unused_signal=2\n"
        "settings/stdout/verbose_stdout=true\n"
    )
    before = _project(debug=debug)
    _write(target / "project.godot", before)
    out = _installed(kit, target, "-StrictWarnings")
    assert "[debug] untyped_declaration 1 -> 2" in out
    assert "[debug] 14 strict warning(s) set to error" in out
    assert json.loads(_read(target / "kit.json"))["gdscript"] == {"strict": True}
    assert "gdscript.strict" not in out.split("Installed.")[1]  # no "turn it on" hint

    after = _read(target / "project.godot")
    values = dict(re.findall(r"^gdscript/warnings/(\w+)=(\d+)$", after, re.M))
    assert values == dict.fromkeys(_strict_names(kit), "2")
    # The project's own [debug] lines stay where they were; the new ones follow them.
    kept = debug.replace("untyped_declaration=1", "untyped_declaration=2")
    assert kept in after
    added = [name for name in _strict_names(kit) if f"warnings/{name}=" not in debug]
    cut = before.index("[rendering]")
    assert after == (
        before[: before.index("[debug]")]
        + AUTOLOAD
        + kept
        + "".join(f"gdscript/warnings/{name}=2\n" for name in added)
        + "\n"
        + DEVTOOLS
        + PLUGINS
        + before[cut:]
    )
    # The record names every line: the 14 it added, the one it replaced, and not the
    # project's own unused_signal=2. Undone entry by entry, the file is the project's again.
    record = _manifest(target)["wrote"]["project_godot"]
    assert record[6:] == ["[debug] untyped_declaration 1 -> 2"] + [
        f"[debug] {name} added" for name in added
    ]
    assert len(added) == 14 and "unused_signal" not in added
    assert "[debug] new section" not in record
    assert _without_kit_entries(after, record) == before
    assert "Up to date" in _installed(kit, target, "-StrictWarnings")
    assert _read(target / "project.godot") == after


def test_strict_warnings_later_patches_the_project_and_points_at_kit_json(
    kit: Path, target: Path
) -> None:
    _installed(kit, target)
    assert "[debug]" not in _read(target / "project.godot")
    kit_json = (target / "kit.json").read_bytes()
    out = _installed(kit, target, "-StrictWarnings")
    assert "[debug] 16 strict warning(s) set to error" in out
    assert 'kit.json is yours and has gdscript.strict false. Set it:  "gdscript"' in out
    assert (target / "kit.json").read_bytes() == kit_json  # never rewritten
    project = target / "project.godot"
    assert len(re.findall(r"^gdscript/warnings/\w+=2$", _read(project), re.M)) == 16
    record = _manifest(target)["wrote"]["project_godot"]
    names = _strict_names(kit)
    assert record[6:] == ["[debug] new section"] + [f"[debug] {name} added" for name in names]

    # Loosened by hand and set again: the record keeps one entry per warning, the first,
    # which is what the project had before the kit.
    text = _read(project).replace("warnings/unused_variable=2", "warnings/unused_variable=1")
    _write(project, text.replace("gdscript/warnings/unsafe_cast=2\n", ""))
    out = _installed(kit, target, "-StrictWarnings")
    assert "[debug] unused_variable 1 -> 2; [debug] 1 strict warning(s) set to error" in out
    assert _manifest(target)["wrote"]["project_godot"] == record
    assert _without_kit_entries(_read(project), record) == _project()


def test_dry_run_prints_the_plan_and_writes_nothing(kit: Path, target: Path) -> None:
    before = _tree(target)
    out = _installed(kit, target, "-DryRun", "-StrictWarnings")
    assert _tree(target) == before  # no file, no folder
    assert "(dry run: nothing is written)" in out and "Dry run: nothing was written." in out
    for line in (
        "+ tools/check.ps1",
        "+ addons/agent_devtools/dev_tools.gd.uid",
        "+ AGENTS.md",
        "+ .github/workflows/ci.yml",
        "kit.json       new: tests.root=test, gdscript.strict=true",
        "starter test   test/unit/test_kit_smoke.gd",
        "[autoload] DevTools; [editor_plugins] agent_devtools, gdUnit4; [devtools] allow_mutation",
        "[debug] 16 strict warning(s) set to error",
        ".gitignore     + .godot/ /artifacts/ /.tools/ .venv/ __pycache__/",
        "tools/kit_manifest.json   (commit it)",
    ):
        assert line in out, line
    assert "tools/tests/" not in out and PROJECT_DATA not in out

    # Also a dry run of an upgrade with a conflict: reported, exit 1, nothing written.
    _installed(kit, target)
    edited = target / "tools" / "check.ps1"
    edited.write_bytes(edited.read_bytes() + b"# mine\n")
    before = _tree(target)
    assert "tools/check.ps1" in _refused(kit, target, "-DryRun")
    assert _tree(target) == before


def test_what_the_project_already_has_is_kept(kit: Path, tmp_path: Path) -> None:
    uid = _read(kit / "addons" / "agent_devtools" / "dev_tools.gd.uid").strip()
    project = _project().replace(
        "[rendering]",
        f'[autoload]\n\nGame="*res://game.gd"\nDevTools="*{uid}"\n\n'
        '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/mine/plugin.cfg")\n\n'
        "[rendering]",
    )
    target = _target(tmp_path / "it's a [wip] game", project)
    own = {
        "addons/gdUnit4/plugin.cfg": '[plugin]\n\nname="gdUnit4"\nversion="5.0.3"\n',
        "pyproject.toml": '[project]\nname = "mine"\nversion = "1"\n',
        ".gdlintrc": "max-line-length: 120\n",
        "kit.json": '{"tests": {"root": "res://spec/"}, "gdscript": {"strict": false}}\n',
        "spec/suites/board_spec.gd": "extends GdUnitTestSuite\n",
        ".github/workflows/ci.yml": "name: mine\n",
        "docs/tools.md": "# My tools\n",
    }
    for relative, text in own.items():
        _write(target / relative, text)
    out = _installed(kit, target)
    for relative, text in own.items():
        assert _read(target / relative) == text, relative
    assert not (target / "addons" / "gdUnit4" / "LICENSE").exists()
    assert not (target / "uv.lock").exists()  # the kit's lock belongs to the kit's pyproject
    pinned = json.loads(_read(kit / "kit.json"))["gdunit4"]["version"]
    assert f"gdUnit4        yours kept: 5.0.3; the kit pins {pinned}" in out
    assert "uv run python tools/install_gdunit4.py --force" in out
    assert "pyproject.toml is yours and has no gdtoolkit" in out
    assert 'uv add --dev "gdtoolkit==' in out and '"ruff' in out
    assert "kit.json       yours kept" in out
    # tests.root is read from the project's kit.json; its suite needs no starter beside it.
    assert not (target / "spec" / "unit").exists() and "starter test" not in out
    assert "= docs/tools.md   (yours kept; the kit's copy is not installed)" in out
    # What it did not write is not on the list of what to undo.
    wrote = _manifest(target)["wrote"]
    assert wrote["gdunit4"] is False and wrote["kit_json"] is False and wrote["starter_test"] == ""
    assert wrote["project_godot"] == [
        "[editor_plugins] agent_devtools, gdUnit4",
        "[devtools] new section",
        "[devtools] allow_mutation=false",
    ]
    assert _without_kit_entries(_read(target / "project.godot"), wrote["project_godot"]) == project
    assert "gdscript.strict" not in out.split("Installed.")[0]  # its kit.json says: no note
    after = _read(target / "project.godot")
    assert f'DevTools="*{uid}"\n' in after and after.count("DevTools=") == 1
    assert (
        'enabled=PackedStringArray("res://addons/mine/plugin.cfg", '
        '"res://addons/agent_devtools/plugin.cfg", "res://addons/gdUnit4/plugin.cfg")\n' in after
    )
    assert after.count("[autoload]") == 2  # the one in the description, and the section
    assert "Up to date" in _installed(kit, target)


@pytest.mark.parametrize("written", ["res://spec/suites/", "spec\\suites", "./spec/suites"])
def test_tests_root_is_read_as_the_tools_read_it(kit: Path, target: Path, written: str) -> None:
    # A backslash is a separator in kit.json on every system (tools/kit_config.*): the
    # starter test must not land in a folder named `spec\suites` on Linux.
    _write(target / "kit.json", json.dumps({"tests": {"root": written}}) + "\n")
    out = _installed(kit, target)
    assert "starter test   spec/suites/unit/test_kit_smoke.gd" in out
    assert (target / "spec" / "suites" / "unit" / "test_kit_smoke.gd").is_file()
    assert sorted(path.name for path in target.iterdir() if "spec" in path.name) == ["spec"]
    assert _manifest(target)["wrote"]["starter_test"] == "spec/suites/unit/test_kit_smoke.gd"


def test_a_kit_json_of_yours_that_does_not_say_strict_gets_a_note(kit: Path, target: Path) -> None:
    # No gdscript.strict means strict: the first check would fail on 16 warnings.
    _write(target / "kit.json", '{"tests": {"root": "test"}}\n')
    out = _installed(kit, target, "-DryRun")
    assert (
        "note: kit.json is yours and has no gdscript.strict, so the strict type check is on: "
        "16 of 16 strict warnings are not errors yet. To start with it off, add:  "
        '"gdscript": {"strict": false}'
    ) in out
    # Not when the run sets them, and not when they are set.
    assert "gdscript.strict" not in _installed(kit, target, "-DryRun", "-StrictWarnings")
    strict = "[debug]\n\n" + "".join(f"gdscript/warnings/{name}=2\n" for name in STRICT_WARNINGS)
    _write(target / "project.godot", _project(debug=strict))
    assert "gdscript.strict" not in _installed(kit, target, "-DryRun")


def test_the_manifest_lists_everything_removing_the_kit_has_to_undo(
    kit: Path, target: Path
) -> None:
    """docs/tools.md, "Removing the kit", followed to the letter after an install and an
    upgrade: nothing
    of the kit's is left, nothing of the project's is lost."""
    debug = "[debug]\n\ngdscript/warnings/untyped_declaration=1\ngdscript/warnings/unsafe_cast=2\n"
    plugins = '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/mine/plugin.cfg")\n\n'
    project = _write(
        target / "project.godot",
        _project(debug=debug).replace("[rendering]", plugins + "[rendering]"),
    )
    ignore = _write(target / ".gitignore", ".godot/\n*.tmp\n")
    _write(target / "tools" / "build.ps1", "Write-Host 'my build'\n")
    before = _tree(target)

    _installed(kit, target)
    _write(target / "enemy.gd", "extends Node\n")  # the project goes on
    # The next kit: a tool and a starter file gone, one of each new; now with strict warnings.
    for gone in ("tools/lint_shaders.gd", "docs/gotchas.md"):
        (kit / gone).unlink()
    _write(kit / "tools" / "brand_new.ps1", "Write-Host 'new tool'\n")
    _write(kit / "docs" / "new_page.md", "# New in this kit\n")
    _write(kit / "VERSION", "9.9.9\n")
    _installed(kit, target, "-StrictWarnings")

    manifest = _manifest(target)
    wrote = manifest["wrote"]
    assert "unfinished" not in manifest  # else: run the installer again first
    # files: the kit's. Then the manifest.
    for relative in manifest["files"]:
        (target / relative).unlink()
    (target / "tools" / "kit_manifest.json").unlink()
    # wrote: kit.json, gdUnit4, the starter test with its .uid.
    assert wrote["kit_json"] is True and wrote["gdunit4"] is True
    (target / "kit.json").unlink()
    shutil.rmtree(target / "addons" / "gdUnit4")
    (target / wrote["starter_test"]).unlink()
    (target / (wrote["starter_test"] + ".uid")).unlink()
    # wrote.gitignore: those lines, the "# godot-goblin-gearbox" line and the blank line above it.
    lines = _read(ignore).split("\n")
    at = lines.index("# godot-goblin-gearbox")
    end = at + 1 + len(wrote["gitignore"])
    assert lines[at - 1] == "" and lines[at + 1 : end] == wrote["gitignore"]
    del lines[at - 1 : end]
    _write(ignore, "\n".join(lines))
    # wrote.project_godot: entry by entry.
    _write(project, _without_kit_entries(_read(project), wrote["project_godot"]))
    # seeded: the starter files, the one the newer kit dropped among them.
    assert "docs/gotchas.md" in manifest["seeded"] and "docs/new_page.md" in manifest["seeded"]
    for relative in manifest["seeded"]:
        (target / relative).unlink()
    # Folders left empty.
    for folder in sorted((path for path in target.rglob("*") if path.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()

    assert _tree(target) == before | {"enemy.gd": b"extends Node\n"}


@pytest.mark.parametrize(
    ("folder", "suite", "root", "starter"),
    [
        ("test", "test/unit/test_board.gd", "test", False),
        ("tests", "tests/test_board.gd", "tests", False),
        # A gdUnit4 suite under another name is a suite: its folder is the root.
        ("spec", "spec/BoardTest.gd", "spec", False),
        ("tests", "tests/helpers/fixtures.gd", "test", True),  # no suite there
        # In the project's spelling, on every system: the tools read tests.root as written.
        ("Tests", "Tests/test_board.gd", "Tests", False),
        ("Test", "Test/unit/test_board.gd", "Test", False),
        ("SPEC", "SPEC/BoardTest.gd", "SPEC", False),
    ],
)
def test_tests_root_is_where_the_suites_are(
    kit: Path, target: Path, folder: str, suite: str, root: str, starter: bool
) -> None:
    text = "extends GdUnitTestSuite\n" if "ixtures" not in suite else "extends RefCounted\n"
    _write(target / suite, text)
    out = _installed(kit, target)
    assert json.loads(_read(target / "kit.json"))["tests"] == {"root": root}
    assert (target / root / "unit" / "test_kit_smoke.gd").exists() == starter
    assert ("starter test" in out) == starter


REFUSALS = {
    "no project.godot": (
        lambda target: (target / "project.godot").unlink(),
        "no project.godot in '",
    ),
    "a Godot 3 project": (
        lambda target: _write(target / "project.godot", "config_version=4\n\n[application]\n"),
        "project.godot has config_version=4; a Godot 4 project has config_version=5",
    ),
    "another DevTools autoload": (
        lambda target: _write(
            target / "project.godot", _project() + '\n[autoload]\n\nDevTools="*res://mine.gd"\n'
        ),
        'project.godot already has an autoload named DevTools ("*res://mine.gd"). Rename it',
    ),
    "plugins not a list": (
        lambda target: _write(
            target / "project.godot", _project() + '\n[editor_plugins]\n\nenabled="all"\n'
        ),
        "project.godot [editor_plugins] enabled is not a PackedStringArray(...)",
    ),
    "a broken manifest": (
        lambda target: _write(target / "tools" / "kit_manifest.json", "{ not json"),
        "the target's tools/kit_manifest.json is not valid JSON",
    ),
    "a broken kit.json": (
        lambda target: _write(target / "kit.json", "{ not json"),
        "the target's kit.json is not valid JSON",
    ),
    "a test root outside the project": (
        lambda target: _write(target / "kit.json", '{"tests": {"root": "../elsewhere"}}\n'),
        "the target's kit.json tests.root must be a folder inside the project, not '../elsewhere'",
    ),
    "project.godot is not UTF-8": (
        lambda target: (target / "project.godot").write_bytes(b"config_version=5\n\xff\xfe\n"),
        "project.godot is not UTF-8 text",
    ),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_a_target_it_cannot_install_into_is_refused_in_one_line(
    kit: Path, target: Path, case: str
) -> None:
    arrange, message = REFUSALS[case]
    arrange(target)
    before = _tree(target)
    for args in ((), ("-DryRun",), ("-Force", "-StrictWarnings")):
        out = _refused(kit, target, *args)
        assert f"install: {message}" in out
        assert len(out.strip().splitlines()) == 1, out
        assert _tree(target) == before


def test_the_kit_itself_and_a_missing_target_are_refused(kit: Path, tmp_path: Path) -> None:
    before = _tree(kit)
    assert "install: -Target is the kit itself, or a folder inside it." in _refused(kit, kit)
    inside = _target(kit / "sample")
    assert "install: -Target is the kit itself, or a folder inside it." in _refused(kit, inside)
    shutil.rmtree(inside)
    assert _tree(kit) == before
    assert "install: no project.godot in '" in _refused(kit, tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists()
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(kit / "install.ps1")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1 and "install: name the game's folder" in result.stdout
    unknown = _install(kit, tmp_path / "nowhere", "-NoSuchSwitch")
    assert unknown.returncode != 0 and "NoSuchSwitch" in unknown.stderr
    assert unknown.stdout.strip() == ""


@posix_only("SIGINT, which is Ctrl+C there, and a file that waits for its writer (a FIFO)")
def test_a_run_stopped_with_ctrl_c_says_so_and_the_next_finishes_it(
    kit: Path, target: Path
) -> None:
    """Ctrl+C is no error: PowerShell ends the script and runs no trap. The run exited 0
    and said nothing. A kit file that is a FIFO holds the copy until this test lets go,
    so the installer is stopped while it writes."""
    held = kit / "addons" / "gdUnit4" / "LICENSE"  # the first file of the gdUnit4 copy
    text = held.read_bytes()
    held.unlink()
    os.mkfifo(held)
    partial = target / "addons" / ".gdUnit4.kit-partial"
    process = subprocess.Popen(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(kit / "install.ps1")]
        + ["-Target", str(target)],
        cwd=kit.parent,
        env=clean_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.monotonic() + 120
        while not partial.is_dir() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert partial.is_dir(), "the installer never came to the gdUnit4 copy"
        time.sleep(0.5)  # the copy has opened the FIFO, or is about to
        process.send_signal(signal.SIGINT)
        time.sleep(0.5)
        # Let the copy end. No reader (ENXIO): the run was stopped before it opened the file.
        with contextlib.suppress(OSError):
            os.close(os.open(held, os.O_WRONLY | os.O_NONBLOCK))
        out, _ = process.communicate(timeout=120)
    finally:
        process.kill()
    assert process.returncode == 1, out
    assert out.rstrip().endswith(
        "install: interrupted: stopped halfway. Run the same command again."
    ), out
    assert "Installed." not in out

    held.unlink()
    held.write_bytes(text)
    assert "Installed." in _installed(kit, target)
    assert "Up to date" in _installed(kit, target)


def test_a_powershell_that_cannot_compute_a_checksum_is_refused_before_the_first(
    kit: Path, target: Path
) -> None:
    """PowerShell 7.0 and 7.1 abort where they hash, on a Linux with OpenSSL 3 alone: the
    installer hashes every file it compares. It asks first, as setup.ps1 does."""
    old_powershell(kit, "it cannot compute a checksum")
    before = _tree(target)
    for args in ((), ("-DryRun",), ("-Force",)):
        out = _refused(kit, target, *args)
        said = out.strip().splitlines()
        assert len(said) == 1, out
        assert said[0].startswith("install: PowerShell 7."), out
        assert ": it cannot compute a checksum. Fix: " in said[0]
        assert _tree(target) == before


def test_get_help_shows_the_description() -> None:
    # A line of the help block that starts with a dot is read as a help keyword, and one
    # unknown keyword hides the whole block.
    asked = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", f"Get-Help '{REPO / 'install.ps1'}'"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    for shown in ("SYNOPSIS", "DESCRIPTION", "-DryRun", "Starter files", "Removing it", "Stopped"):
        assert shown in asked.stdout, shown


@pytest.mark.parametrize("missing", ["AGENTS.md", "CLAUDE.md", "docs", "addons/gdUnit4", "uv.lock"])
def test_an_incomplete_kit_copy_is_refused_up_front(kit: Path, target: Path, missing: str) -> None:
    path = kit / missing
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    before = _tree(target)
    out = _refused(kit, target)
    assert "install: this kit copy is incomplete" in out
    assert f"no {missing}" in out and "Clone or download the kit again." in out
    assert len(out.strip().splitlines()) == 1, out
    assert _tree(target) == before


def test_a_relative_target_is_read_from_the_callers_folder(kit: Path, target: Path) -> None:
    # _install runs from the folder that holds both; the script lives elsewhere. PowerShell
    # reads either slash as a separator, on every system.
    for written in (f"./{target.name}", f".\\{target.name}", f"{target.name}/"):
        out = _installed(kit, written, "-DryRun")
        assert f"-> {target}   (dry run" in out, written


def test_other_godot_versions_and_csharp_are_warned_about(kit: Path, target: Path) -> None:
    project = _project().replace('"4.7", "Forward Plus"', '"4.6", "C#", "Forward Plus"')
    _write(target / "project.godot", project)
    out = _installed(kit, target)
    assert "note: project.godot is a Godot 4.6 project; the kit is built and tested for 4.7" in out
    assert "note: this is a C# project" in out and "GDScript only" in out
    assert json.loads(_read(target / "kit.json"))["godot"]["version"].startswith("4.7.")


def test_the_ci_workflow_runs_scripts_and_switches_that_exist() -> None:
    # The workflow is copied into projects and cannot be run here: pin what it calls.
    text = (REPO / ".github" / "workflows" / "ci.yml").read_bytes().decode("ascii")
    assert "\t" not in text and "\r" not in text.replace("\r\n", "\n")
    started = r'^ +pwsh \./(tools/(\w+)\.ps1)((?: -\w+(?: \d+)?)*) \| tee "\$RUNNER_TEMP/\2\.out"$'
    runs = re.findall(started, text, re.M)
    assert [script for script, _, _ in runs] == ["tools/setup.ps1", "tools/check.ps1"]
    assert text.count("run:") == len(runs) + 1  # the two steps and `defaults: run:`
    assert text.count("pwsh ./") == len(runs)
    for script, _name, arguments in runs:
        source = _read(REPO / script)
        for name, value in re.findall(r"-(\w+)(?: (\d+))?", arguments):
            kind = "int" if value else "switch"
            assert re.search(rf"\[{kind}\]\${name}\b", source), f"{script} -{name}"
    # Each step asks for the line its script ends on: the scripts say these words.
    assert '"setup: ready"*) ;;' in text and '"setup: ready.' in _read(REPO / "tools/setup.ps1")
    assert '"check: PASSED"*) ;;' in text and '"check: PASSED: ' in _read(REPO / "tools/check.ps1")
    # A gate that hangs to its limit still ends inside the job's own.
    minutes = int(re.search(r"^    timeout-minutes: (\d+)$", text, re.M).group(1))
    gate_seconds = int(re.search(r"-GateTimeoutSeconds (\d+)", text).group(1))
    assert minutes * 60 >= 2 * gate_seconds + 600
    uses = re.findall(r"uses: ([\w./-]+)@(\S+)(.*)", text)
    assert [name for name, _, _ in uses] == [
        "actions/checkout",
        "astral-sh/setup-uv",
        "actions/cache",
        "actions/upload-artifact",
    ]
    # Pinned by commit (a tag can be moved), with the release it is in a comment.
    for name, ref, comment in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{name}@{ref}"
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), f"{name}: {comment!r}"
    # One job on every system the kit is run on: the same two commands, in pwsh on each. A
    # red check on one system does not cancel the other, and each keeps its own Godot
    # cache (per system and processor) and its own artifact, also when the job is
    # cancelled for running out of time.
    for needed in (
        "runs-on: ${{ matrix.os }}",
        "os: [ubuntu-latest, windows-latest]",
        "fail-fast: false",
        "shell: bash",
        "path: .tools/godot",
        "key: godot-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('kit.json') }}",
        "if: ${{ failure() || cancelled() }}",
        "name: check-artifacts-${{ matrix.os }}",
        "path: artifacts/",
    ):
        assert needed in text, needed
    assert text.count("runs-on:") == 1 and text.count("shell:") == 1
    # For the whole job, so for the PowerShell of each step: none rewrites the startup
    # profile, which two that end together can leave mixed.
    job_env = r"^    env:\n(?:      #.*\n)*      DOTNET_MultiCoreJitNoProfileGather: 1$"
    assert re.search(job_env, text.replace("\r\n", "\n"), re.M)


def _ci_steps() -> dict[str, str]:
    """The bash text of each `run: |` step of the CI workflow, by the script it starts."""
    text = (REPO / ".github" / "workflows" / "ci.yml").read_bytes().decode("ascii")
    steps = {}
    for block in re.findall(r"^ +run: \|\n((?:(?: {10}.*)?\n)+)", text.replace("\r\n", "\n"), re.M):
        body = "\n".join(line[10:] for line in block.splitlines()) + "\n"
        steps[re.search(r"tools/(\w+)\.ps1", body).group(1)] = body
    return steps


def _git_bash() -> str | None:
    """The bash a CI step runs in: on Windows the one Git for Windows brings (never the
    `bash.exe` in System32, which starts another system)."""
    if not WINDOWS:
        return shutil.which("bash")
    git = shutil.which("git")
    # git.exe is cmd/git.exe of the Git folder, or mingw64/bin/git.exe inside its own bash.
    for folder in Path(git).resolve().parents if git else ():
        bash = folder / "bin" / "bash.exe"
        if bash.is_file() and (folder / "cmd" / "git.exe").is_file():
            return str(bash)
    return None


@pytest.mark.parametrize(
    ("said", "code", "passes"),
    [
        # A PowerShell that aborts as it starts: two lines of its own, nothing run, exit 0.
        ("An error has occurred that was not properly handled.", 0, False),
        ("", 0, False),
        ("{name}: {good} and more", 0, True),
        ("{name}: {good}\nand a line after it", 0, False),
        ("check: FAILED (gdlint): 13 passed, 1 failed, 1 skipped in 9.9s", 1, False),
        ("{name}: {good}", 3, False),
    ],
)
def test_a_ci_step_passes_on_the_last_line_of_its_script_alone(
    tmp_path: Path, said: str, code: int, passes: bool
) -> None:
    """With a mixed startup profile `pwsh` runs nothing and exits 0: a step that trusted the
    exit code was green. Each step's own bash text is run here with a stand-in `pwsh`."""
    bash = _git_bash()
    if bash is None:
        pytest.skip("needs bash (on Windows: Git for Windows)")
    steps = _ci_steps()
    assert sorted(steps) == ["check", "setup"]
    stand_in = tmp_path / "bin" / "pwsh"
    stand_in.parent.mkdir()
    for name, good in (("setup", "ready"), ("check", "PASSED")):
        lines = said.format(name=name, good=good).splitlines()
        echoes = "".join(f"printf '%s\\n' '{line}'\n" for line in lines)
        stand_in.write_text(f"#!/bin/sh\n{echoes}exit {code}\n", encoding="ascii", newline="\n")
        stand_in.chmod(0o755)
        step = tmp_path / f"{name}.sh"
        step.write_text(steps[name], encoding="ascii", newline="\n")
        env = clean_env(RUNNER_TEMP=str(tmp_path))
        env["PATH"] = str(stand_in.parent) + os.pathsep + env["PATH"]
        ran = subprocess.run(
            [bash, "--noprofile", "--norc", "-eo", "pipefail", str(step)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert (ran.returncode == 0) == passes, (
            f"{name}: {ran.returncode}\n{ran.stdout}{ran.stderr}"
        )
        if not passes and code == 0:
            assert f"::error::{name}.ps1 did not end with '{name}: {good}'" in ran.stdout


@pytest.mark.real_godot
def test_the_installed_project_loads_and_runs_its_starter_test(tmp_path: Path) -> None:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot binary: set GODOT_BIN, or run pwsh ./tools/setup.ps1 -InstallGodot")
    kit = _copy_kit(tmp_path / "kit's [copy]", whole_gdunit=True)
    target = _target(tmp_path / "it's a [wip] game")
    _installed(kit, target, "-StrictWarnings")
    # Godot's user data (logs, caches) goes to the temp folder, not among the developer's own.
    env = clean_env(GODOT_BIN=godot, **user_data_env(tmp_path / "user data"))

    imported = subprocess.run(
        [godot, "--headless", "--path", str(target), "--import"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    log = imported.stdout + imported.stderr
    assert imported.returncode == 0, log
    assert not re.search(r"^\s*(SCRIPT )?ERROR", log, re.M), log
    assert (target / ".godot" / "global_script_class_cache.cfg").is_file()

    for script, verdict in (
        ("typecheck.ps1", "Type-check: CLEAN"),
        ("test.ps1", "1 test cases | 0 errors | 0 failures"),
    ):
        result = run_tool(target, script, env=env, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        assert verdict in result.stdout, result.stdout
    listed = run_tool(
        target, "godot.ps1", "--headless", "--script", "res://tools/add_input_action.gd",
        "--user-args", "--list", env=env,
    )  # fmt: skip
    assert listed.returncode == 0 and "INPUT_ACTIONS 0" in listed.stdout, listed.stdout

    # Godot wrote .uid files for the project's own scripts; the kit's files are untouched.
    assert "Up to date" in _installed(kit, target, "-StrictWarnings")
    assert (target / "player.gd.uid").is_file()
    # And its user data where this test put it: the project is named "Target".
    assert list((tmp_path / "user data").rglob("app_userdata/Target")), "user data went elsewhere"
