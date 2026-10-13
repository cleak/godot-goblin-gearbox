"""The Python and PowerShell halves of three rules give the same answer.

tools/ci/godot_binary.py mirrors tools/godot_resolver.ps1 (which Godot to run),
tools/ci/class_cache.py mirrors tools/class_cache.ps1 (is the class cache stale), and
tools/test_shards.py shard_env mirrors Get-KitUserDataEnvironment (where a test run keeps
user://). Each pair is written twice by hand, so each is run here on one project and
compared. A difference means `uv run python tools/test_shards.py` and
`pwsh ./tools/test.ps1` could run different engines, disagree on whether to import first,
or leave one of them writing into the developer's own user data.

The resolvers are compared for every system's download, on whatever machine this is: both
take the system as an argument.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _gate_fixtures import (
    FAKE_GODOT,
    NEEDS_PWSH,
    PWSH,
    WINDOWS,
    bare_path,
    cannot_be_listed,
    clean_env,
    fake_godot,
    not_as_root,
    python_program,
)

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import class_cache  # noqa: E402
import godot_binary  # noqa: E402
import kit_config  # noqa: E402
import test_shards  # noqa: E402

SHARED = ("kit_config.ps1", "godot_resolver.ps1", "class_cache.ps1")
pytestmark = NEEDS_PWSH

VERSIONS = {
    "4.7.2.stable.official.abc123": True,
    "4.7.stable.official.abc123": True,
    "4.7.2.stable.mono.official.abc123": False,
    "4.6.1.stable.official.def456": False,
    "4.70.0.stable.official.0": False,
    "4.7.beta2.official.123abc": False,
}
SYSTEMS = (
    ("windows", "x86_64"),
    ("windows", "arm64"),
    ("linux", "x86_64"),
    ("linux", "arm64"),
    ("linux", "riscv64"),
    ("macos", "x86_64"),
    ("macos", "arm64"),
)
BUNDLE_PROGRAM = ("Godot.app", "Contents", "MacOS", "Godot")


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


def _powershell(
    root: Path, script: str, call: str, env: dict[str, str], cwd: Path | None = None
) -> list[str]:
    done = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", f". $env:KIT_SCRIPT; {call}"],
        env=env | {"KIT_SCRIPT": str(root / "tools" / script)},
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _install(
    folder: Path, version: str, build: str, *, console: bool = True, layout: str = "folder"
) -> None:
    """An unzipped Godot as empty placeholder files. `layout`: "folder" (a folder named
    like the release), "zip" (Windows: that folder still ends in .exe, as the archive
    does) or "flat" (the program alone)."""
    name = f"Godot_v{version}-stable_{godot_binary.RELEASE_TAGS[build]}"
    if build == "macos_universal":
        _touch(folder.joinpath(name, *BUNDLE_PROGRAM))
    elif build != "windows_x86_64":
        _touch(folder / name if layout == "flat" else folder / name / name)
    else:
        inner = {"folder": folder / name, "zip": folder / f"{name}.exe", "flat": folder}[layout]
        _touch(inner / f"{name}.exe")
        if console and layout != "flat":
            _touch(inner / f"{name}_console.exe")


def _candidates(root: Path, env: dict[str, str], build: str) -> list[Path]:
    config = kit_config.load(root)
    minor = godot_binary.wanted_minor(config, env)
    return [
        path
        for folder in godot_binary.search_folders(config)
        for path in godot_binary.folder_candidates(folder, minor, config.godot_version, build)
    ]


@pytest.mark.parametrize("build", godot_binary.RELEASE_TAGS)
def test_both_resolvers_list_the_same_candidates_in_the_same_order(
    tmp_path: Path, monkeypatch, build: str
) -> None:
    _needs("godot_resolver.ps1", "Get-GodotCandidates")
    root = _project(tmp_path, search_roots=["extra", "%KIT_TEST_ELSEWHERE%"])
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("KIT_TEST_ELSEWHERE", str(elsewhere))
    tag = godot_binary.RELEASE_TAGS[build]
    _install(root / ".tools" / "godot", "4.7.2", build)
    _install(root / "extra", "4.7.2", build, layout="flat")
    _install(root / "extra", "4.7.10", build, layout="zip" if build.startswith("win") else "folder")
    # Mono: never a candidate.
    _touch(root / "extra" / f"Godot_v4.7.2-stable_mono_{tag}" / f"Godot_v4.7.2-stable_mono_{tag}")
    _touch(root / "extra" / f"Godot_v4.7.2-stable_mono_{tag}" / f"Godot_v4.7.2-stable_{tag}.exe")
    _install(root / "extra", "4.70.1", build)  # 4.70 is not 4.7
    _install(root / "extra", "4.6.3", build)  # another minor
    for other in godot_binary.RELEASE_TAGS:  # every other system's download of 4.7.3
        if other != build:
            _install(root / "extra", "4.7.3", other)
    _touch(root / "extra" / "Godot_v4.7.2-stable_win32.exe")  # not a build the kit targets
    _touch(root / "extra" / f"Godot_v4.7.2-stable_{tag}.zip")  # the archive, not the program
    _touch(root / "extra" / "Godot.app" / "Contents" / "MacOS" / "Godot")
    _install(elsewhere, "4.7.1", build, console=False)
    _install(root.parent, "4.7", build)  # beside the project: not searched
    # Windows: two programs to a download. macOS: a root's own Godot.app as well.
    expected = {"windows_x86_64": (6, 2), "macos_universal": (5, 2)}.get(build, (4, 1))
    for version, count in zip(("", "4.6"), expected, strict=True):
        env = clean_env(KIT_TEST_ELSEWHERE=str(elsewhere))
        if version:
            env["GODOT_VERSION"] = version
        call = f"Get-GodotCandidates -Build {build}"
        theirs = [Path(line) for line in _powershell(root, SHARED[1], call, env)]
        assert _candidates(root, env, build) == theirs
        assert len(theirs) == count, theirs


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


def _only_git(folder: Path) -> str:
    """A PATH with git and no Godot: on Linux git's own folder (/usr/bin) may hold one."""
    if WINDOWS:
        return bare_path(Path(GIT).parent)
    folder.mkdir()
    (folder / "git").symlink_to(GIT)
    return bare_path(folder)


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
    build = kit_config.godot_build("linux", "x86_64")
    # The project sits one folder below the top of its repository.
    repository = tmp_path / "games"
    main = _project(tmp_path)
    (repository / ".gitignore").write_text(".tools/\n", encoding="utf-8")
    _git(repository, "init", "--quiet")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "--message", "kit")
    _install(main / ".tools" / "godot", "4.7.2", build)
    env = clean_env()
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
    call = f"Get-GodotCandidates -Build {build}"
    theirs = [Path(line) for line in _powershell(root, SHARED[1], call, env)]
    assert _candidates(root, env, build) == theirs
    assert [path.name for path in theirs] == ["Godot_v4.7.2-stable_linux.x86_64"]
    assert plain[0] in theirs[0].parents

    # The message of a failed search names the extra folder, on every system.
    for release in (main / ".tools" / "godot").iterdir():
        shutil.rmtree(release)
    lonely = env | {"PATH": _only_git(tmp_path / "only_git")}
    monkeypatch.setenv("PATH", lonely["PATH"])
    searched = f"Searched: {root / '.tools' / 'godot'}; {plain[0]}; then godot/godot4"
    for system, processor in SYSTEMS:
        said = _powershell(
            root,
            SHARED[1],
            f"try {{ Resolve-GodotPath -Platform {system} -Architecture {processor} }} "
            "catch { $_.Exception.Message }",
            lonely,
        )
        with pytest.raises(godot_binary.GodotNotFoundError) as raised:
            godot_binary.resolve(
                kit_config.load(root), lonely, platform=system, architecture=processor
            )
        assert said == str(raised.value).splitlines(), (system, processor)
        assert searched in said[1]

    # No git on PATH: the usual folders, and no error.
    gitless = env | {"PATH": bare_path(tmp_path / "nothing")}
    monkeypatch.setenv("PATH", gitless["PATH"])
    assert _searched(root, gitless) == [root / ".tools" / "godot"]


def test_both_resolvers_swap_a_windowed_binary_for_its_console_launcher(tmp_path: Path) -> None:
    _needs("godot_resolver.ps1", "Get-GodotConsoleTwin")
    root = _project(tmp_path)
    _install(tmp_path, "4.7.2", "windows_x86_64")
    _install(tmp_path, "4.7.1", "windows_x86_64", console=False)
    _install(tmp_path, "4.7.2", "linux_x86_64")
    asked = [
        tmp_path / stem / name
        for stem in ("Godot_v4.7.2-stable_win64", "Godot_v4.7.1-stable_win64")
        for name in (f"{stem}.exe", f"{stem}_console.exe", f"{stem}.cmd")
    ]
    asked.append(tmp_path / "Godot_v4.7.2-stable_linux.x86_64" / "Godot_v4.7.2-stable_linux.x86_64")
    env = clean_env(KIT_ASKED=json.dumps([str(path) for path in asked]))
    call = "foreach ($path in ($env:KIT_ASKED | ConvertFrom-Json)) { Get-GodotConsoleTwin $path }"
    theirs = _powershell(root, SHARED[1], call, env)
    assert [str(godot_binary.console_twin(path)) for path in asked] == theirs


def test_both_resolvers_find_the_program_inside_a_macos_app(tmp_path: Path) -> None:
    _needs("godot_resolver.ps1", "Get-GodotBundleProgram")
    root = _project(tmp_path)
    program = _touch(tmp_path.joinpath("Applications", *BUNDLE_PROGRAM))
    asked = [program.parents[2], program, tmp_path / "Other.APP", tmp_path / "godot.exe"]
    for platform in ("macos", "linux", "windows"):
        env = clean_env(KIT_ASKED=json.dumps([str(path) for path in asked]))
        call = (
            "foreach ($path in ($env:KIT_ASKED | ConvertFrom-Json)) "
            f"{{ Get-GodotBundleProgram -Candidate $path -Platform {platform} }}"
        )
        theirs = [Path(line) for line in _powershell(root, SHARED[1], call, env)]
        assert [godot_binary.bundle_program(path, platform) for path in asked] == theirs, platform
    assert godot_binary.bundle_program(asked[0], "macos") == program


def test_both_resolvers_name_the_same_download_for_a_system(tmp_path: Path) -> None:
    _needs("godot_resolver.ps1", "Get-KitGodotRelease")
    root = _project(tmp_path)
    call = (
        f"foreach ($pair in '{'|'.join(' '.join(system) for system in SYSTEMS)}'.Split('|')) {{ "
        "$system, $processor = $pair.Split(' '); "
        "$build = Get-KitGodotBuild -Platform $system -Architecture $processor; "
        "$release = Get-KitGodotRelease -Version 4.7.2 -Build $build; "
        "'[' + $build + '] [' + $release.Tag + ']' }; "
        "(Get-KitGodotBuilds) -join ' '; "
        "(Get-KitPlatform) + ' ' + (Get-KitArchitecture) + ' ' + (Get-KitGodotBuild)"
    )
    theirs = _powershell(root, SHARED[1], call, clean_env())
    mine = []
    for system, processor in SYSTEMS:
        build = kit_config.godot_build(system, processor)
        mine.append(f"[{build}] [{godot_binary.RELEASE_TAGS.get(build, '')}]")
    mine.append(" ".join(kit_config.GODOT_BUILDS))
    here = (kit_config.host_platform(), kit_config.host_architecture(), kit_config.godot_build())
    mine.append(" ".join(here))
    assert mine == theirs
    assert list(godot_binary.RELEASE_TAGS) == list(kit_config.GODOT_BUILDS)


def test_both_resolvers_expand_a_search_root_the_same_way(tmp_path: Path, monkeypatch) -> None:
    _needs("godot_resolver.ps1", "Expand-KitSearchRoot")
    root = _project(tmp_path)
    monkeypatch.setenv("KIT_TEST_ROOT", "/set")
    monkeypatch.setenv("KIT_TEST_EMPTY", "")
    monkeypatch.delenv("KIT_TEST_UNSET", raising=False)
    entries = [
        "%KIT_TEST_ROOT%/a",
        "$KIT_TEST_ROOT/b",
        "${KIT_TEST_ROOT}c",
        "x/$KIT_TEST_ROOT/%KIT_TEST_ROOT%",
        "%KIT_TEST_UNSET%/d",
        "$KIT_TEST_UNSET/e",
        "${KIT_TEST_UNSET}",
        "%KIT_TEST_EMPTY%/f",
        "50%/and/100%",
        "$",
        "~",
        "~/g",
        "~\\h",
        "~i",
        "j/~",
        "plain",
        "",
    ]
    env = clean_env(KIT_ENTRIES=json.dumps(entries), KIT_TEST_ROOT="/set", KIT_TEST_EMPTY="")
    call = (
        "foreach ($entry in ($env:KIT_ENTRIES | ConvertFrom-Json)) "
        "{ '[' + (Expand-KitSearchRoot $entry) + ']' }"
    )
    theirs = _powershell(root, SHARED[1], call, env)
    assert [f"[{godot_binary.expand_search_root(entry)}]" for entry in entries] == theirs
    assert theirs[:3] == ["[/set/a]", "[/set/b]", "[/setc]"]


def test_both_resolvers_accept_and_refuse_the_same_builds(tmp_path: Path, monkeypatch) -> None:
    _needs("godot_resolver.ps1", "Get-GodotRejection")
    root = _project(tmp_path)
    fake = fake_godot(tmp_path / "bin")  # prints FAKE_GODOT_VERSION for --version
    loop = (
        f"foreach ($version in '{'|'.join(VERSIONS)}'.Split('|')) {{ "
        "$env:FAKE_GODOT_VERSION = $version; "
        "if (Get-GodotRejection $env:KIT_FAKE) { 'refused' } else { 'accepted' } }"
    )
    theirs = _powershell(root, SHARED[1], loop, clean_env(KIT_FAKE=str(fake)))
    mine = []
    for version in VERSIONS:
        monkeypatch.setenv("FAKE_GODOT_VERSION", version)
        mine.append("refused" if godot_binary.incompatible(fake, "4.7") else "accepted")
    assert mine == theirs
    assert mine == ["accepted" if good else "refused" for good in VERSIONS.values()]
    # And the whole resolution, by an explicit binary.
    env = clean_env(GODOT_BIN=str(fake), FAKE_GODOT_VERSION="4.7.2.stable.official.abc123")
    monkeypatch.setenv("FAKE_GODOT_VERSION", env["FAKE_GODOT_VERSION"])
    theirs = _powershell(root, SHARED[1], "Resolve-GodotPath", env)
    assert [godot_binary.resolve(kit_config.load(root), env)] == theirs == [str(fake)]
    if not WINDOWS:
        # A file nobody may execute: the same refusal, word for word.
        fake.chmod(0o644)
        call = "Get-GodotRejection $env:KIT_FAKE"
        theirs = _powershell(root, SHARED[1], call, clean_env(KIT_FAKE=str(fake)))
        assert [godot_binary.incompatible(fake, "4.7")] == theirs


def test_both_resolvers_order_names_by_the_value_of_their_letters(tmp_path: Path) -> None:
    """PowerShell's own sort ignores letter case; Python's does not. Linux keeps
    Godot_v... beside godot_v...: the two resolvers must still try them in one order."""
    _needs("godot_resolver.ps1", "Get-KitNamesDescending")
    root = _project(tmp_path, search_roots=["extra"])
    stem = "_v4.7.2-stable_win64"
    # Told apart by more than their letter case, so that every file system holds all three.
    names = [f"Godot{stem}_a.exe", f"godot{stem}_b.exe", f"GODOT{stem}_c.exe"]
    for name in names:
        _touch(root / "extra" / name)  # three flat files
        _touch(root / ".tools" / "godot" / f"Godot{stem}" / name)  # three in one release
    env = clean_env()
    call = "Get-GodotCandidates -Build windows_x86_64"
    theirs = [Path(line) for line in _powershell(root, SHARED[1], call, env)]
    assert _candidates(root, env, "windows_x86_64") == theirs
    ordered = [names[1], names[0], names[2]]  # g, then G with o, then G with O
    assert [path.name for path in theirs] == ordered * 2


def test_both_resolvers_read_the_variables_the_same_way(tmp_path: Path) -> None:
    _needs("godot_resolver.ps1", "Get-GodotNotFoundMessage")
    root = _project(tmp_path)
    config = kit_config.load(root)
    fake = fake_godot(tmp_path / "bin")
    nothing = bare_path(tmp_path / "nothing")

    def both(**variables: str) -> list[str]:
        env = clean_env(PATH=nothing, **variables)
        call = "try { Resolve-GodotPath } catch { $_.Exception.Message }"
        theirs = _powershell(root, SHARED[1], call, env)
        try:
            mine = godot_binary.resolve(config, env)
        except godot_binary.GodotNotFoundError as error:
            mine = str(error)
        assert mine.splitlines() == theirs, variables
        return theirs

    # Blanks around a value are not part of it; a value of blanks alone names nothing.
    assert both(GODOT_BIN=f"  {fake} ") == [str(fake)]
    assert both(GODOT_BIN="   ", GODOT4_EXE=f"{fake}\t") == [str(fake)]
    assert both(GODOT_BIN=str(fake), GODOT_VERSION="  ") == [str(fake)]
    assert "not found" in both(GODOT_BIN="  ")[0]
    # GODOT_VERSION asks for a release kit.json does not pin: both say what the installer
    # installs, and neither names the pinned file as the one to look for.
    for wanted in ("4.6", "4.7.1"):
        said = both(GODOT_VERSION=wanted)
        assert said[0].startswith(f"Godot {wanted} (standard build) not found."), said
        assert said[1] == (
            f"GODOT_VERSION asks for {wanted}; kit.json pins 4.7.2, which is what "
            "install_godot.ps1 installs."
        )
        assert said[2].startswith("Searched: ") and len(said) == 3
        assert "4.7.2-stable" not in said[0]
    # The pinned release, asked for by its minor or in full: no such line.
    for wanted in ("4.7", "4.7.2"):
        said = both(GODOT_VERSION=wanted)
        assert len(said) == 2 and said[1].startswith("Searched: "), said


@pytest.mark.parametrize("system", ["windows", "linux", "macos"])
def test_both_resolvers_name_the_file_to_look_for(tmp_path: Path, system: str) -> None:
    _needs("godot_resolver.ps1", "Get-GodotNotFoundMessage")
    root = _project(tmp_path)
    wanted = {"4.7": "4.7.2", "4.7.2": "4.7.2", "4.6": "4.6.x", "4.7.1": "4.7.1", "5.0": "5.0.x"}
    call = "; ".join(
        f"Get-GodotNotFoundMessage -Version '{version}' -Platform {system} -Architecture arm64"
        for version in wanted
    )
    theirs = _powershell(root, SHARED[1], call, clean_env())
    mine = [godot_binary.not_found_message(version, "4.7.2", system, "arm64") for version in wanted]
    assert mine == theirs
    if system == "linux":
        for line, version in zip(theirs, wanted.values(), strict=True):
            assert line.endswith(f"executable (Godot_v{version}-stable_linux.arm64)."), line


def test_both_resolvers_give_a_godot_on_path_its_whole_path(tmp_path: Path, monkeypatch) -> None:
    """A PATH entry such as "." names the program by the working directory."""
    _needs("godot_resolver.ps1", "Resolve-GodotPath")
    root = _project(tmp_path)
    here = tmp_path / "here"
    program = python_program(here, "godot", FAKE_GODOT)
    env = clean_env(PATH=os.pathsep.join(part for part in (".", bare_path()) if part))
    theirs = _powershell(root, SHARED[1], "Resolve-GodotPath", env, cwd=here)
    monkeypatch.chdir(here)
    assert [godot_binary.resolve(kit_config.load(root), env)] == theirs == [str(program)]


def test_both_resolvers_pass_over_a_folder_they_may_not_read(tmp_path: Path) -> None:
    _needs("godot_resolver.ps1", "Get-GodotCandidates")
    not_as_root()
    build = "linux_x86_64"
    root = _project(tmp_path, search_roots=["../locked", "../half", "../open"])
    locked, half, found = (root.parent / name for name in ("locked", "half", "open"))
    _install(locked, "4.7.2", build)  # the search root itself may not be read
    _install(half, "4.7.2", build)  # the release folder in it may not be read
    _install(half, "4.7.1", build, layout="flat")
    _install(found, "4.7.2", build)
    with cannot_be_listed(locked, half / "Godot_v4.7.2-stable_linux.x86_64"):
        env = clean_env()
        call = f"Get-GodotCandidates -Build {build}"
        theirs = [Path(line) for line in _powershell(root, SHARED[1], call, env)]
        assert _candidates(root, env, build) == theirs
        assert [path.relative_to(root.parent).parts[0] for path in theirs] == ["half", "open"]


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
    call = "Get-GodotClassCacheDiff -ProjectRoot $env:KIT_ROOT"
    env = clean_env(KIT_ROOT=str(root))
    assert class_cache.differences(config) == _powershell(root, SHARED[2], call, env)
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
    assert sorted(mine) == sorted(_powershell(root, SHARED[2], call, env))
    assert sorted(mine) == [
        "missing from cache: Board res://scripts/moved/board.gd",
        "missing from cache: LibRunner res://addons/lib/runner.gd",
        "missing from cache: OneLine res://scripts/one_line.gd",
        "missing from cache: TwoAnnotations res://scripts/two.gd",
        "missing from cache: WithIcon res://scripts/icon.gd",
        "only in cache: Board res://scripts/board.gd",
        "only in cache: Gone res://scripts/gone.gd",
    ]


@pytest.mark.parametrize("system", ["windows", "linux", "macos"])
def test_both_test_runners_move_user_data_the_same_way(tmp_path: Path, system: str) -> None:
    """tools/test.ps1 (Get-KitUserDataEnvironment) and tools/test_shards.py (shard_env) each
    give a test run a user:// of its own. One variable on each system, an absolute folder
    only: Godot ignores a relative one and would use the developer's."""
    _needs("godot_resolver.ps1", "Get-KitUserDataEnvironment")
    root = _project(tmp_path)
    call = (
        "try { Get-KitUserDataEnvironment -Folder $env:KIT_FOLDER -Platform $env:KIT_SYSTEM | "
        "ConvertTo-Json -Compress } catch { 'REFUSED ' + $_.Exception.Message }"
    )

    def powershell(folder: str) -> str:
        env = clean_env(KIT_FOLDER=folder, KIT_SYSTEM=system)
        [line] = _powershell(root, SHARED[1], call, env)
        return line

    absolute = str(tmp_path / "user data")
    moved = test_shards.shard_env(Path(absolute), system, {"PATH": "bin"})
    del moved["PATH"]
    assert moved == json.loads(powershell(absolute))
    assert list(moved.values()) == [absolute]

    for relative in ("user data", os.path.join("tmp", "user_0"), "."):
        refused = powershell(relative)
        assert refused.startswith("REFUSED ") and "must be an absolute path" in refused, refused
        assert f"'{relative}'" in refused
        with pytest.raises(ValueError, match="must be an absolute path") as mine:
            test_shards.shard_env(Path(relative), system, {})
        assert f"'{Path(relative)}'" in str(mine.value)
