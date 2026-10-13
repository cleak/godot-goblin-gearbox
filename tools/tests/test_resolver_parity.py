"""The Python and PowerShell halves of two rules give the same answer.

tools/ci/godot_binary.py mirrors tools/godot_resolver.ps1 (which Godot to run), and
tools/ci/class_cache.py mirrors tools/class_cache.ps1 (is the class cache stale). Each
pair is written twice by hand, so each is run here on one project and compared. A
difference means `uv run python tools/test_shards.py` and `pwsh ./tools/test.ps1` could
run different engines, or disagree on whether to import first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import class_cache  # noqa: E402
import godot_binary  # noqa: E402
import kit_config  # noqa: E402

PWSH = shutil.which("pwsh")
SHARED = ("kit_config.ps1", "godot_resolver.ps1", "class_cache.ps1")
pytestmark = pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="needs PowerShell 7")

FAKE_GODOT = "@echo off\r\necho %FAKE_GODOT_VERSION%\r\n"
VERSIONS = {
    "4.7.2.stable.official.abc123": True,
    "4.7.stable.official.abc123": True,
    "4.7.2.stable.mono.official.abc123": False,
    "4.6.1.stable.official.def456": False,
    "4.70.0.stable.official.0": False,
    "4.7.beta2.official.123abc": False,
}


def _needs(script: str, function: str) -> None:
    """Skip while tools/<script> is not the kit's own (it must define `function`)."""
    path = TOOLS / script
    if not path.is_file() or f"function {function}" not in path.read_text(encoding="utf-8"):
        pytest.skip(f"tools/{script} has no {function}")


def _project(tmp_path: Path, **godot: object) -> Path:
    """A project with a copy of the PowerShell tools: they find their project from
    their own location."""
    root = tmp_path / "games" / "project"
    (root / "tools").mkdir(parents=True)
    for name in SHARED:
        shutil.copyfile(TOOLS / name, root / "tools" / name)
    kit = {"godot": {"version": "4.7.2", **godot}}
    (root / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    return root


def _environment(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("GODOT_BIN", "GODOT4_EXE", "GODOT_VERSION"):
        env.pop(name, None)
    return env | overrides


def _powershell(root: Path, script: str, call: str, env: dict[str, str]) -> list[str]:
    command = f". '{root / 'tools' / script}'; {call}"
    done = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _install(folder: Path, name: str, *, console: bool = True, layout: str = "folder") -> None:
    """An unzipped Godot as empty placeholder files. `layout`: "folder" (a folder named
    like the release), "zip" (that folder still ends in .exe, as the archive does) or
    "flat" (the .exe alone)."""
    inner = {"folder": folder / name, "zip": folder / f"{name}.exe", "flat": folder}[layout]
    inner.mkdir(parents=True, exist_ok=True)
    (inner / f"{name}.exe").write_text("", encoding="utf-8")
    if console and layout != "flat":
        (inner / f"{name}_console.exe").write_text("", encoding="utf-8")


def _candidates(root: Path, env: dict[str, str]) -> list[Path]:
    config = kit_config.load(root)
    minor = godot_binary.wanted_minor(config, env)
    return [
        path
        for folder in godot_binary.search_folders(config)
        for path in godot_binary.folder_candidates(folder, minor, config.godot_version)
    ]


def test_both_resolvers_list_the_same_candidates_in_the_same_order(
    tmp_path: Path, monkeypatch
) -> None:
    _needs("godot_resolver.ps1", "Get-GodotCandidates")
    root = _project(tmp_path, search_roots=["extra", "%KIT_TEST_ELSEWHERE%"])
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("KIT_TEST_ELSEWHERE", str(elsewhere))
    _install(root / ".tools" / "godot", "Godot_v4.7.2-stable_win64")
    _install(root / "extra", "Godot_v4.7.2-stable_win64", layout="flat")
    _install(root / "extra", "Godot_v4.7.10-stable_win64", layout="zip")
    _install(root / "extra", "Godot_v4.7.2-stable_mono_win64")  # Mono: never a candidate
    _install(root / "extra", "Godot_v4.70.1-stable_win64")  # 4.70 is not 4.7
    _install(root / "extra", "Godot_v4.6.3-stable_win64")  # another minor
    _install(root / "extra", "Godot_v4.7.2-stable_win32")  # not the build the kit targets
    _install(elsewhere, "Godot_v4.7.1-stable_win64", console=False)
    _install(root.parent, "Godot_v4.7-stable_win64")  # beside the project: not searched
    for version, expected in (("", 6), ("4.6", 2)):
        env = _environment(KIT_TEST_ELSEWHERE=str(elsewhere))
        if version:
            env["GODOT_VERSION"] = version
        theirs = [Path(line) for line in _powershell(root, SHARED[1], "Get-GodotCandidates", env)]
        assert _candidates(root, env) == theirs
        assert len(theirs) == expected, theirs


GIT = shutil.which("git")


def _git(folder: Path, *arguments: str) -> None:
    identity = ["-c", "user.name=kit test", "-c", "user.email=kit@test.invalid"]
    done = subprocess.run(
        [GIT, "-C", str(folder), *identity, *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _searched(root: Path, env: dict[str, str]) -> list[Path]:
    """Get-GodotSearchRoots of the tools in `root`, after checking Python lists the same."""
    theirs = [Path(line) for line in _powershell(root, SHARED[1], "Get-GodotSearchRoots", env)]
    assert godot_binary.search_folders(kit_config.load(root)) == theirs
    return theirs


@pytest.mark.skipif(GIT is None, reason="needs git")
def test_a_linked_worktree_also_searches_the_main_worktrees_download(
    tmp_path: Path, monkeypatch
) -> None:
    """`git worktree add` gives a checkout without .tools/godot/ (it is ignored, so it is
    never checked out): setup.ps1 found no Godot there, with one a folder away."""
    _needs("godot_resolver.ps1", "Get-KitMainWorktreeProject")
    # The project sits one folder below the top of its repository.
    repository = tmp_path / "games"
    main = _project(tmp_path)
    (repository / ".gitignore").write_text(".tools/\n", encoding="utf-8")
    _git(repository, "init", "--quiet")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "--message", "kit")
    _install(main / ".tools" / "godot", "Godot_v4.7.2-stable_win64")
    env = _environment()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(name, None)
        monkeypatch.delenv(name, raising=False)
    plain = [main / ".tools" / "godot"]
    assert _searched(main, env) == plain

    linked = tmp_path / "worktrees" / "feature"
    _git(repository, "worktree", "add", "--quiet", "--detach", str(linked))
    root = linked / "project"
    assert not (root / ".tools").exists()
    assert _searched(root, env) == [root / ".tools" / "godot", plain[0]]
    theirs = [Path(line) for line in _powershell(root, SHARED[1], "Get-GodotCandidates", env)]
    assert _candidates(root, env) == theirs
    assert [path.name for path in theirs] == [
        "Godot_v4.7.2-stable_win64_console.exe",
        "Godot_v4.7.2-stable_win64.exe",
    ]
    assert plain[0] in theirs[0].parents

    # The message of a failed search names the extra folder.
    for release in (main / ".tools" / "godot").iterdir():
        shutil.rmtree(release)
    lonely = env | {"PATH": str(Path(GIT).parent) + os.pathsep + str(Path(PWSH).parent)}
    monkeypatch.setenv("PATH", lonely["PATH"])
    said = _powershell(
        root, SHARED[1], "try { Resolve-GodotPath } catch { $_.Exception.Message }", lonely
    )
    with pytest.raises(godot_binary.GodotNotFoundError) as raised:
        godot_binary.resolve(kit_config.load(root), lonely)
    searched = f"Searched: {root / '.tools' / 'godot'}; {plain[0]}; then godot/godot4"
    assert searched in "\n".join(said) and searched in str(raised.value)

    # No git on PATH: the usual folders, and no error.
    gitless = env | {"PATH": str(Path(PWSH).parent)}
    monkeypatch.setenv("PATH", gitless["PATH"])
    assert _searched(root, gitless) == [root / ".tools" / "godot"]


def test_both_resolvers_swap_a_windowed_binary_for_its_console_launcher(tmp_path: Path) -> None:
    _needs("godot_resolver.ps1", "Get-GodotConsoleTwin")
    root = _project(tmp_path)
    _install(tmp_path, "Godot_v4.7.2-stable_win64")
    _install(tmp_path, "Godot_v4.7.1-stable_win64", console=False)
    for stem in ("Godot_v4.7.2-stable_win64", "Godot_v4.7.1-stable_win64"):
        for name in (f"{stem}.exe", f"{stem}_console.exe", f"{stem}.cmd"):
            asked = tmp_path / stem / name
            theirs = _powershell(root, SHARED[1], f"Get-GodotConsoleTwin '{asked}'", _environment())
            assert [str(godot_binary.console_twin(asked))] == theirs, name


def test_both_resolvers_accept_and_refuse_the_same_builds(tmp_path: Path, monkeypatch) -> None:
    _needs("godot_resolver.ps1", "Get-GodotRejection")
    root = _project(tmp_path)
    fake = tmp_path / "bin" / "fake_godot.cmd"  # prints FAKE_GODOT_VERSION for --version
    fake.parent.mkdir()
    fake.write_text(FAKE_GODOT, encoding="ascii", newline="")
    loop = (
        f"foreach ($version in '{'|'.join(VERSIONS)}'.Split('|')) {{ "
        "$env:FAKE_GODOT_VERSION = $version; "
        f"if (Get-GodotRejection '{fake}') {{ 'refused' }} else {{ 'accepted' }} }}"
    )
    theirs = _powershell(root, SHARED[1], loop, _environment())
    mine = []
    for version in VERSIONS:
        monkeypatch.setenv("FAKE_GODOT_VERSION", version)
        mine.append("refused" if godot_binary.incompatible(fake, "4.7") else "accepted")
    assert mine == theirs
    assert mine == ["accepted" if good else "refused" for good in VERSIONS.values()]
    # And the whole resolution, by an explicit binary.
    env = _environment(GODOT_BIN=str(fake), FAKE_GODOT_VERSION="4.7.2.stable.official.abc123")
    monkeypatch.setenv("FAKE_GODOT_VERSION", env["FAKE_GODOT_VERSION"])
    theirs = _powershell(root, SHARED[1], "Resolve-GodotPath", env)
    assert [godot_binary.resolve(kit_config.load(root), env)] == theirs == [str(fake)]


CACHE_ENTRY = (
    '{\n"base": &"RefCounted",\n"class": &"%s",\n"language": &"GDScript",\n"path": "%s"\n}'
)


def test_both_class_cache_checks_report_the_same_differences(tmp_path: Path) -> None:
    _needs("class_cache.ps1", "Get-GodotClassCacheDiff")
    root = _project(tmp_path)
    files = {
        "scripts/grid.gd": "class_name Grid\nextends RefCounted\n",
        "scripts/tool.gd": "@tool\nclass_name Painter\nextends Node\n",
        "scripts/one_line.gd": "@tool class_name OneLine extends Node\n",
        "scripts/icon.gd": '@icon("res://icon.svg") class_name WithIcon\nextends Node\n',
        "scripts/two.gd": '@tool @icon("res://i.svg")  class_name TwoAnnotations\n',
        "scripts/inner.gd": "extends Node\n\n\nclass Inner:\n\tvar class_name_like: int = 0\n",
        "scripts/moved/board.gd": "class_name Board\n",
        "scripts/plain.gd": "extends Node\n# class_name NotThis\n",
        "addons/lib/runner.gd": "class_name LibRunner\n",
        "export/.gdignore": "",
        "export/old.gd": "class_name Exported\n",
        ".hidden/x.gd": "class_name Hidden\n",
        "samples/demo/project.godot": "",
        "samples/demo/demo.gd": "class_name Demo\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    config = kit_config.load(root)
    call = f"Get-GodotClassCacheDiff -ProjectRoot '{root}'"
    assert class_cache.differences(config) == _powershell(root, SHARED[2], call, _environment())
    cached = {
        "Grid": "res://scripts/grid.gd",
        "Painter": "res://scripts/tool.gd",
        "Board": "res://scripts/board.gd",  # since moved
        "Gone": "res://scripts/gone.gd",  # since deleted
        "Native": "res://scripts/Native.cs",  # another language: not compared
    }
    body = ", ".join(CACHE_ENTRY % pair for pair in cached.items())
    cache = root / class_cache.CACHE
    cache.parent.mkdir()
    cache.write_text(f"list=Array[Dictionary]([{body}])\n", encoding="utf-8")
    mine = class_cache.differences(config)
    assert sorted(mine) == sorted(_powershell(root, SHARED[2], call, _environment()))
    assert sorted(mine) == [
        "missing from cache: Board res://scripts/moved/board.gd",
        "missing from cache: LibRunner res://addons/lib/runner.gd",
        "missing from cache: OneLine res://scripts/one_line.gd",
        "missing from cache: TwoAnnotations res://scripts/two.gd",
        "missing from cache: WithIcon res://scripts/icon.gd",
        "only in cache: Board res://scripts/board.gd",
        "only in cache: Gone res://scripts/gone.gd",
    ]
