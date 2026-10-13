"""tools/godot_resolver.ps1: where Godot is looked for, what is accepted, how it is run.

A rule that differs by system takes the system as a parameter (-Build, -Platform), so the
Windows, Linux and macOS branches all run here, whatever machine this is.
"""

import base64
import json
import sys
from pathlib import Path

import pytest
from _gate_fixtures import (
    NEEDS_PWSH,
    NO_STARTUP_PROFILE,
    PLATFORM,
    QUIET_DOTNET,
    REPO,
    USER_DATA_VARIABLE,
    WINDOWS,
    bare_path,
    clean_env,
    copy_tools,
    fake_godot,
    godot_data_root,
    posix_only,
    run_powershell,
    windows_only,
    write,
    write_kit_json,
)

sys.path.insert(0, str(REPO / "tools"))
import kit_config  # noqa: E402

pytestmark = NEEDS_PWSH

BUILDS = ("windows_x86_64", "linux_x86_64", "linux_arm64", "macos_universal")
TAGS = dict(zip(BUILDS, ("win64", "linux.x86_64", "linux.arm64", "macos.universal"), strict=True))
BUNDLE_PROGRAM = "Godot.app/Contents/MacOS/Godot"


def _project(tmp_path: Path, **godot: object) -> Path:
    root = tmp_path / "games" / "project"
    copy_tools(root)
    write_kit_json(root, godot={"version": "4.7.2", "search_roots": []} | godot)
    return root


def _call(root: Path, call: str, **env: str):
    return run_powershell(
        ". $env:KIT_RESOLVER; try { " + call + " } "
        "catch { Write-Output ('THROWS: ' + $_.Exception.Message); exit 1 }",
        env=clean_env(KIT_RESOLVER=str(root / "tools" / "godot_resolver.ps1"), **env),
    )


def _install(
    folder: Path,
    version: str,
    build: str = "windows_x86_64",
    *,
    console: bool = True,
    flat: bool = False,
    zip_named: bool = False,
    mono: bool = False,
) -> None:
    """An unzipped Godot (empty placeholder files) in one of the recognised layouts.

    `flat`: the program alone, in no folder of its own. `zip_named` (Windows): the folder
    keeps the archive's name, which ends in .exe. `mono`: the .NET build's names.
    """
    tag = TAGS[build].replace(".", "_") if mono and build != "macos_universal" else TAGS[build]
    name = f"Godot_v{version}-stable_{'mono_' if mono else ''}{tag}"
    if build == "macos_universal":
        app = "Godot_mono.app" if mono else "Godot.app"
        write(folder / name / app / "Contents" / "MacOS" / "Godot", "")
    elif build != "windows_x86_64":
        write(folder / name if flat else folder / name / name, "")
    elif flat:
        write(folder / f"{name}.exe", "")
    else:
        inner = folder / (f"{name}.exe" if zip_named else name)
        write(inner / f"{name}.exe", "")
        if console:
            write(inner / f"{name}_console.exe", "")


def _found(result, tmp_path: Path) -> list[str]:
    assert result.returncode == 0, result.stdout + result.stderr
    return [Path(line).relative_to(tmp_path).as_posix() for line in result.stdout.splitlines()]


def test_search_order_is_tools_then_configured_roots_and_no_other_folder(tmp_path: Path) -> None:
    root = _project(tmp_path, search_roots=["extra", "%KIT_TEST_ELSEWHERE%"])
    elsewhere = tmp_path / "elsewhere"
    _install(root / ".tools" / "godot", "4.7.2")
    _install(root / "extra", "4.7.2", flat=True)
    _install(root / "extra", "4.7.10", zip_named=True)
    _install(root / "extra", "4.7.2", mono=True)  # Mono: never a candidate
    _install(root / "extra", "4.70.1")  # 4.70 is not 4.7
    _install(root / "extra", "4.6.3")  # another minor
    _install(root / "extra", "4.7.2", "linux_x86_64")  # another system's download
    _install(elsewhere, "4.7.1", console=False)
    _install(root.parent, "4.7")

    call = "Get-GodotCandidates -Build windows_x86_64"
    result = _call(root, call, KIT_TEST_ELSEWHERE=str(elsewhere))
    assert _found(result, tmp_path) == [
        # 1. the project's own install; the console binary first
        "games/project/.tools/godot/Godot_v4.7.2-stable_win64/Godot_v4.7.2-stable_win64_console.exe",
        "games/project/.tools/godot/Godot_v4.7.2-stable_win64/Godot_v4.7.2-stable_win64.exe",
        # 2. kit.json search_roots, in order; console binaries before plain ones
        "games/project/extra/Godot_v4.7.10-stable_win64.exe/Godot_v4.7.10-stable_win64_console.exe",
        "games/project/extra/Godot_v4.7.2-stable_win64.exe",
        "games/project/extra/Godot_v4.7.10-stable_win64.exe/Godot_v4.7.10-stable_win64.exe",
        "elsewhere/Godot_v4.7.1-stable_win64/Godot_v4.7.1-stable_win64.exe",
        # The folder above the project is not searched: whatever lands there (a download,
        # a shared drive) must not run because of its name.
    ]

    # Who keeps Godot beside their projects says so in kit.json.
    write_kit_json(root, godot={"version": "4.7.2", "search_roots": [".."]})
    named = _call(root, call)
    assert [Path(line).name for line in named.stdout.split()][-2:] == [
        "Godot_v4.7-stable_win64_console.exe",
        "Godot_v4.7-stable_win64.exe",
    ]


@pytest.mark.parametrize("build", ["linux_x86_64", "linux_arm64"])
def test_a_linux_download_is_one_file_named_like_the_release(tmp_path: Path, build: str) -> None:
    root = _project(tmp_path, search_roots=["extra", "$KIT_TEST_ELSEWHERE"])
    elsewhere = tmp_path / "elsewhere"
    other = "linux_arm64" if build == "linux_x86_64" else "linux_x86_64"
    tag = TAGS[build]
    _install(root / ".tools" / "godot", "4.7.2", build)
    # The archive left beside the program is not the program.
    write(root / ".tools" / "godot" / f"Godot_v4.7.2-stable_{tag}.zip", "")
    write(root / ".tools" / "godot" / f"Godot_v4.7.2-stable_{tag}" / "notes.txt", "")
    _install(root / "extra", "4.7.2", build, flat=True)
    _install(root / "extra", "4.7.10", build)
    _install(root / "extra", "4.7.3", build, mono=True)  # Mono: never a candidate
    _install(root / "extra", "4.7.4", other)  # another processor's download
    _install(root / "extra", "4.7.5", "windows_x86_64")  # another system's
    _install(root / "extra", "4.70.1", build)
    _install(elsewhere, "4.7.1", build, flat=True)
    _install(root.parent, "4.7", build)

    result = _call(root, f"Get-GodotCandidates -Build {build}", KIT_TEST_ELSEWHERE=str(elsewhere))
    assert _found(result, tmp_path) == [
        f"games/project/.tools/godot/Godot_v4.7.2-stable_{tag}/Godot_v4.7.2-stable_{tag}",
        f"games/project/extra/Godot_v4.7.2-stable_{tag}",  # the pinned release first
        f"games/project/extra/Godot_v4.7.10-stable_{tag}/Godot_v4.7.10-stable_{tag}",
        f"elsewhere/Godot_v4.7.1-stable_{tag}",
    ]


def test_a_macos_download_is_godot_app(tmp_path: Path) -> None:
    root = _project(tmp_path, search_roots=["extra", "${KIT_TEST_ELSEWHERE}"])
    elsewhere = tmp_path / "elsewhere"
    build = "macos_universal"
    _install(root / ".tools" / "godot", "4.7.2", build)
    _install(root / "extra", "4.7.10", build)
    _install(root / "extra", "4.7.2", build, mono=True)  # Mono: never a candidate
    _install(root / "extra", "4.7.3", "linux_x86_64")  # another system's download
    write(root / "extra" / "Godot.app" / "Contents" / "MacOS" / "Godot", "")
    write(root / "extra" / "Other.app" / "Contents" / "MacOS" / "Godot", "")
    write(root / "extra" / "Godot_v4.7.4-stable_macos.universal" / "readme.txt", "")
    _install(elsewhere, "4.7.1", build)
    _install(root.parent, "4.7", build)

    result = _call(root, f"Get-GodotCandidates -Build {build}", KIT_TEST_ELSEWHERE=str(elsewhere))
    assert _found(result, tmp_path) == [
        f"games/project/.tools/godot/Godot_v4.7.2-stable_macos.universal/{BUNDLE_PROGRAM}",
        f"games/project/extra/Godot_v4.7.10-stable_macos.universal/{BUNDLE_PROGRAM}",
        f"games/project/extra/{BUNDLE_PROGRAM}",  # a root's own Godot.app: last
        f"elsewhere/Godot_v4.7.1-stable_macos.universal/{BUNDLE_PROGRAM}",
    ]

    # GODOT_BIN may name the app: the program is inside it. Only on macOS.
    app = root / "extra" / "Godot.app"
    for platform, given, expected in (
        ("macos", app, app / "Contents" / "MacOS" / "Godot"),
        ("macos", f"{app}/", app / "Contents" / "MacOS" / "Godot"),
        ("macos", app / "Contents" / "MacOS" / "Godot", app / "Contents" / "MacOS" / "Godot"),
        ("linux", app, app),
        ("windows", app, app),
    ):
        call = f"Get-GodotBundleProgram -Candidate $env:KIT_GIVEN -Platform {platform}"
        result = _call(root, call, KIT_GIVEN=str(given))
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(result.stdout.strip()) == Path(expected), (platform, given)

    # The whole resolution: what is probed is the program, and the app is what was named.
    probed = _call(root, "Resolve-GodotPath -Platform macos", GODOT_BIN=str(app))
    assert probed.returncode == 1 and "GODOT_BIN: " in probed.stdout
    assert str(app / "Contents" / "MacOS" / "Godot") in probed.stdout
    empty = write(tmp_path / "Empty.app" / "Info.plist", "").parent
    missing = _call(root, "Resolve-GodotPath -Platform macos", GODOT_BIN=str(empty))
    assert f"GODOT_BIN does not name an executable file: '{empty}'" in missing.stdout


def test_no_candidates_for_a_system_godot_publishes_no_build_for(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _install(root / ".tools" / "godot", "4.7.2", "linux_x86_64")
    builds = _call(
        root,
        "foreach ($pair in 'windows x86_64', 'windows arm64', 'linux x86_64', 'linux arm64', "
        "'linux riscv64', 'macos arm64', 'macos x86_64') { $system, $processor = $pair.Split(' '); "
        "'[' + (Get-KitGodotBuild -Platform $system -Architecture $processor) + ']' }",
    )
    assert builds.stdout.split() == [
        "[windows_x86_64]",
        "[windows_x86_64]",  # the 64-bit x86 build, also on Windows for ARM
        "[linux_x86_64]",
        "[linux_arm64]",
        "[]",
        "[macos_universal]",
        "[macos_universal]",
    ]
    none = _call(root, "@(Get-GodotCandidates -Build '').Count")
    assert none.returncode == 0 and none.stdout.strip() == "0", none.stdout + none.stderr
    # This machine, as the Python tools name it.
    here = _call(
        root, "(Get-KitPlatform) + ' ' + (Get-KitArchitecture) + ' ' + (Get-KitGodotBuild)"
    )
    assert here.stdout.split() == [
        kit_config.host_platform(),
        kit_config.host_architecture(),
        kit_config.godot_build(),
    ]
    assert kit_config.host_platform() == PLATFORM


def test_the_release_table_names_every_official_download(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _call(
        root,
        "Get-KitGodotBuilds | ForEach-Object { Get-KitGodotRelease -Version 4.7.2 -Build $_ } | "
        "ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    releases = {release["Build"]: release for release in json.loads(result.stdout)}
    assert list(releases) == list(BUILDS)
    base = "https://github.com/godotengine/godot/releases/download/4.7.2-stable/"
    for build, release in releases.items():
        assert release["Tag"] == TAGS[build]
        assert release["Name"] == f"Godot_v4.7.2-stable_{TAGS[build]}"
        assert release["Url"] == base + release["Archive"]
        assert release["ChecksumKey"] == f"godot.{build}_sha512"
        assert release["Executable"] in release["Files"]
    windows, linux, arm, macos = (releases[build] for build in BUILDS)
    assert windows["Archive"] == "Godot_v4.7.2-stable_win64.exe.zip"
    assert windows["Executable"] == "Godot_v4.7.2-stable_win64_console.exe"
    assert windows["Files"] == ["Godot_v4.7.2-stable_win64.exe", windows["Executable"]]
    assert linux["Archive"] == "Godot_v4.7.2-stable_linux.x86_64.zip"
    assert linux["Files"] == [linux["Executable"]] == ["Godot_v4.7.2-stable_linux.x86_64"]
    assert arm["Archive"] == "Godot_v4.7.2-stable_linux.arm64.zip"
    assert arm["Executable"] == "Godot_v4.7.2-stable_linux.arm64"
    assert macos["Archive"] == "Godot_v4.7.2-stable_macos.universal.zip"
    assert macos["Files"] == [macos["Executable"]] == [BUNDLE_PROGRAM]
    unknown = _call(root, "$null -eq (Get-KitGodotRelease -Version 4.7.2 -Build '')")
    assert unknown.stdout.strip() == "True"


def test_godot_version_override_changes_what_is_searched(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _install(root / ".tools" / "godot", "4.7.2")
    _install(root / ".tools" / "godot", "4.6.3")
    result = _call(root, "Get-GodotCandidates -Build windows_x86_64", GODOT_VERSION="4.6")
    assert [Path(line).name for line in result.stdout.split()] == [
        "Godot_v4.6.3-stable_win64_console.exe",
        "Godot_v4.6.3-stable_win64.exe",
    ]
    bad = _call(root, "Get-KitGodotVersion", GODOT_VERSION="latest")
    assert bad.returncode == 1 and "GODOT_VERSION must be a version like 4.7" in bad.stdout


def test_search_roots_expand_variables_and_the_home_folder(tmp_path: Path) -> None:
    root = _project(tmp_path)
    home = Path.home()
    cases = {
        "%KIT_TEST_ROOT%/a": "/set/a",
        "$KIT_TEST_ROOT/b": "/set/b",
        "${KIT_TEST_ROOT}c": "/setc",
        "%KIT_TEST_UNSET%/d": "%KIT_TEST_UNSET%/d",
        "$KIT_TEST_UNSET/e": "$KIT_TEST_UNSET/e",
        "~": str(home),
        "~/f": f"{home}/f",
        "~g": "~g",
        "h/~": "h/~",
        "plain": "plain",
    }
    call = (
        "foreach ($entry in ($env:KIT_ENTRIES | ConvertFrom-Json)) { Expand-KitSearchRoot $entry }"
    )
    env = {"KIT_ENTRIES": json.dumps(list(cases)), "KIT_TEST_ROOT": "/set"}
    result = _call(root, call, **env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == list(cases.values())


def test_explicit_override_wins_and_must_be_compatible(tmp_path: Path) -> None:
    root = _project(tmp_path)
    fake = fake_godot(tmp_path / "bin")

    accepted = _call(root, "Resolve-GodotPath", GODOT_BIN=str(fake))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert accepted.stdout.strip() == str(fake)

    second = _call(root, "Resolve-GodotPath", GODOT4_EXE=str(fake))
    assert second.stdout.strip() == str(fake)

    # An explicit binary that does not fit is an error, never silently replaced.
    wrong = _call(root, "Resolve-GodotPath", GODOT_BIN=str(fake), GODOT_VERSION="4.6")
    assert wrong.returncode == 1
    assert "GODOT_BIN" in wrong.stdout and "needs Godot 4.6 stable" in wrong.stdout

    unstable = _call(
        root, "Resolve-GodotPath", GODOT_BIN=str(fake), FAKE_GODOT_VERSION="4.7.dev.custom_build"
    )
    assert unstable.returncode == 1 and "needs Godot 4.7 stable" in unstable.stdout

    mono = _call(
        root,
        "Resolve-GodotPath",
        GODOT_BIN=str(fake),
        FAKE_GODOT_VERSION="4.7.2.stable.mono.official",
    )
    assert mono.returncode == 1 and "Mono build" in mono.stdout

    missing = _call(root, "Resolve-GodotPath", GODOT_BIN=str(tmp_path / "nope.exe"))
    assert missing.returncode == 1 and "does not name an executable file" in missing.stdout


@posix_only("the executable permission")
def test_a_file_nobody_may_execute_is_refused_with_the_fix(tmp_path: Path) -> None:
    root = _project(tmp_path)
    fake = fake_godot(tmp_path / "bin")
    fake.chmod(0o644)
    refused = _call(root, "Resolve-GodotPath", GODOT_BIN=str(fake))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"GODOT_BIN: '{fake}' is not executable. Fix: chmod +x '{fake}'" in refused.stdout
    # In a searched folder it is passed over, and the message still names it.
    folder = root / ".tools" / "godot" / "Godot_v4.7.2-stable_linux.x86_64"
    write(folder / folder.name, "#!/bin/sh\necho 4.7.2.stable.official.abc\n")
    skipped = _call(
        root, "Resolve-GodotPath -Platform linux -Architecture x86_64", PATH=bare_path()
    )
    assert skipped.returncode == 1, skipped.stdout + skipped.stderr
    assert f"Skipped: '{folder / folder.name}' is not executable." in skipped.stdout
    (folder / folder.name).chmod(0o755)
    found = _call(root, "Resolve-GodotPath -Platform linux -Architecture x86_64", PATH=bare_path())
    assert found.returncode == 0, found.stdout + found.stderr
    assert found.stdout.strip() == str(folder / folder.name)


def test_not_found_says_how_to_get_godot(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _call(root, "Resolve-GodotPath", PATH=bare_path(tmp_path / "nothing"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Godot 4.7 (standard build) not found" in result.stdout
    assert "tools/install_godot.ps1" in result.stdout
    assert "GODOT_BIN" in result.stdout
    assert str(root / ".tools" / "godot") in result.stdout  # where it looked


def test_not_found_names_what_to_do_on_each_system(tmp_path: Path) -> None:
    root = _project(tmp_path)
    said = {}
    for system, processor in (
        ("windows", "x86_64"),
        ("linux", "x86_64"),
        ("linux", "arm64"),
        ("macos", "arm64"),
        ("linux", "riscv64"),
    ):
        call = f"Get-GodotNotFoundMessage -Version 4.7 -Platform {system} -Architecture {processor}"
        result = _call(root, call)
        assert result.returncode == 0, result.stdout + result.stderr
        said[system, processor] = result.stdout.strip()
        assert "\n" not in said[system, processor]
    install = (
        "Godot 4.7 (standard build) not found. Install it with 'pwsh ./tools/install_godot.ps1'"
    )
    executable = ", or set GODOT_BIN to a Godot 4.7 executable"
    assert said["windows", "x86_64"] == f"{install}{executable}."
    assert said["linux", "x86_64"] == f"{install}{executable} (Godot_v4.7.2-stable_linux.x86_64)."
    assert said["linux", "arm64"] == f"{install}{executable} (Godot_v4.7.2-stable_linux.arm64)."
    app = "(Godot.app, or Godot.app/Contents/MacOS/Godot)"
    assert said["macos", "arm64"] == f"{install}{executable} {app}."
    # No download to offer: the installer is not named.
    assert said["linux", "riscv64"] == (
        "Godot 4.7 (standard build) not found, and Godot publishes no build for linux riscv64. "
        "Build or install Godot 4.7 yourself and set GODOT_BIN to its executable."
    )


def test_explicit_plain_exe_resolves_to_the_console_wrapper_beside_it(tmp_path: Path) -> None:
    """GODOT_BIN may name Godot's windowed .exe, which no shell waits for: the tools get the
    console wrapper that ships beside it."""
    root = _project(tmp_path)
    folder = tmp_path / "unzipped"
    plain = write(folder / "Godot_v4.7.2-stable_win64.exe", "")
    console = write(folder / "Godot_v4.7.2-stable_win64_console.exe", "")
    alone = write(tmp_path / "flat" / "Godot_v4.7.2-stable_win64.exe", "")
    script = write(folder / "godot.cmd", "")
    linux = write(folder / "Godot_v4.7.2-stable_linux.x86_64", "")
    cases = ((plain, console), (console, console), (alone, alone), (script, script), (linux, linux))
    for given, expected in cases:
        result = _call(root, "Get-GodotConsoleTwin $env:KIT_GIVEN", KIT_GIVEN=str(given))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == str(expected)

    # The override goes through it: what is probed, and reported, is the wrapper.
    probed = _call(root, "Resolve-GodotPath", GODOT_BIN=str(plain))
    assert probed.returncode == 1
    # An empty file: Windows cannot start it, Linux and macOS may not.
    assert ("GODOT_BIN: Could not run" if WINDOWS else "is not executable") in probed.stdout
    assert str(console) in probed.stdout


@windows_only("the GUI subsystem of a Windows program")
def test_gui_subsystem_programs_are_recognised(tmp_path: Path) -> None:
    """The call operator does not wait for a GUI-subsystem .exe: the tools must know one."""
    root = _project(tmp_path)
    home = Path(sys.base_prefix)
    console, windowed = home / "python.exe", home / "pythonw.exe"
    if not console.is_file() or not windowed.is_file():
        pytest.skip("this Python has no python.exe / pythonw.exe pair to tell apart")
    not_a_program = write(tmp_path / "notes.exe", "MZ is how a program starts; this is text\n")
    cases = {
        console: "False",
        windowed: "True",
        not_a_program: "False",
        fake_godot(tmp_path / "bin"): "False",
        tmp_path / "missing.exe": "False",
    }
    for path, expected in cases.items():
        result = _call(root, "Test-KitGuiSubsystem $env:KIT_GIVEN", KIT_GIVEN=str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == expected, path


@posix_only("a program that is no Windows program")
def test_no_linux_or_macos_program_is_a_gui_subsystem_program(tmp_path: Path) -> None:
    root = _project(tmp_path)
    for path in (Path(sys.executable).resolve(), fake_godot(tmp_path / "bin")):
        result = _call(root, "Test-KitGuiSubsystem $env:KIT_GIVEN", KIT_GIVEN=str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "False", path


def test_an_attached_process_is_waited_for_and_its_exit_code_comes_back(tmp_path: Path) -> None:
    """How a GUI-subsystem program is run: pythonw.exe on Windows, where there is one."""
    root = _project(tmp_path)
    windowed = Path(sys.base_prefix) / "pythonw.exe"
    program = windowed if windowed.is_file() else Path(sys.executable)
    done = tmp_path / "it's [odd] dir" / "done.txt"
    script = write(
        done.parent / "late exit.py",
        "import pathlib, sys, time\n\ntime.sleep(1)\n"
        "pathlib.Path(sys.argv[1]).write_text(pathlib.Path.cwd().name)\nsys.exit(7)\n",
    )
    result = _call(
        root,
        "$code = Invoke-KitAttachedProcess -FilePath $env:KIT_PROGRAM "
        "-Arguments @($env:KIT_SCRIPT, $env:KIT_DONE) -WorkingDirectory $env:KIT_WD; "
        "Write-Output ('code=' + $code + ' done=' + (Test-Path -LiteralPath $env:KIT_DONE))",
        KIT_PROGRAM=str(program),
        KIT_SCRIPT=str(script),
        KIT_DONE=str(done),
        KIT_WD=str(done.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "code=7 done=True" in result.stdout
    assert done.read_text() == "it's [odd] dir"


def test_process_helper_quotes_arguments_and_reports_exit_code(tmp_path: Path) -> None:
    """A path with a space must arrive as one argument (Start-Process quotes nothing).

    The working directory is taken literally too: Start-Process reads it as a wildcard
    pattern, and a folder with square brackets is then "not found".
    """
    root = _project(tmp_path)
    echo = write(
        tmp_path / "it's [wip] with space" / "echo args.py",
        "import os, sys\n\nprint(sys.argv[1:])\nprint('cwd=' + os.path.basename(os.getcwd()))\n"
        "sys.stderr.write('oops')\nsys.exit(7)\n",
    )
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -TimeoutSeconds 60 "
        "-WorkingDirectory $env:KIT_WD "
        "-Arguments @($env:KIT_ECHO, 'a b', 'plain', 'C:\\dir with space\\', 'say \"hi\"', ''); "
        "Write-Output $run.Stdout.Trim(); Write-Output ('err=' + $run.Stderr.Trim()); "
        "Write-Output ('code=' + $run.Code + ' timedout=' + $run.TimedOut)",
        KIT_ECHO=str(echo),
        KIT_PYTHON=sys.executable,
        KIT_WD=str(echo.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = ["a b", "plain", "C:\\dir with space\\", 'say "hi"', ""]
    assert repr(expected) in result.stdout
    assert "cwd=it's [wip] with space" in result.stdout
    assert "err=oops" in result.stdout
    assert "code=7 timedout=False" in result.stdout


def test_the_output_of_a_program_is_complete_when_it_is_read(tmp_path: Path) -> None:
    """On Linux and macOS a program's output arrives through a pipe and is copied as it
    comes: read the moment the program ends, the end of a long output is missing, and now
    and then all of a short one: a Godot that answered `--version` is refused for
    reporting ''."""
    root = _project(tmp_path)
    talk = write(
        tmp_path / "talk.py",
        "import sys\n\nprint('\\n'.join(str(line) for line in range(1, 20001)))\n"
        "sys.stderr.write('the last word')\n",
    )
    result = _call(
        root,
        "foreach ($turn in 1..3) { "
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -Arguments @($env:KIT_TALK) "
        "-TimeoutSeconds 60; $lines = @($run.Stdout -split '\\r?\\n' | Where-Object { $_ }); "
        "$said = $run.Stderr.Trim(); "
        "Write-Output ('lines=' + $lines.Count + ' last=' + $lines[-1] + ' err=' + $said) }",
        KIT_PYTHON=sys.executable,
        KIT_TALK=str(talk),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["lines=20000 last=20000 err=the last word"] * 3


def test_the_output_comes_back_as_the_program_wrote_it(tmp_path: Path) -> None:
    """No line end changed, no empty line dropped, no newline added, on every system: a
    gate that counts lines, or reads JSON, gets what the program said."""
    root = _project(tmp_path)
    talk = write(
        tmp_path / "bytes.py",
        "import sys\n\nsys.stdout.buffer.write(b'a\\r\\nb\\n\\n\\nlast')\n"
        "sys.stderr.buffer.write(b'\\n\\nerr \\xc3\\xbc\\n\\n')\n",
    )
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -Arguments @($env:KIT_TALK) "
        "-TimeoutSeconds 60; "
        "foreach ($text in $run.Stdout, $run.Stderr, $run.Text) { "
        "[System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($text)) }",
        KIT_PYTHON=sys.executable,
        KIT_TALK=str(talk),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    said = [base64.b64decode(line).decode("utf-8") for line in result.stdout.split()]
    assert said == ["a\r\nb\n\n\nlast", "\n\nerr ü\n\n", "a\r\nb\n\n\nlast\n\n\nerr ü"]


@pytest.mark.parametrize("timeout", ["", " -TimeoutSeconds 60"])
def test_output_a_child_keeps_open_does_not_hold_the_caller(tmp_path: Path, timeout: str) -> None:
    """A program may leave something running that still holds its output (a daemon, a
    server). On Linux and macOS that output is a pipe, which ends only when its last
    holder does: the wait for it is bounded, with a timeout and without one."""
    root = _project(tmp_path)
    script = write(
        tmp_path / "parent.py",
        "import subprocess, sys\n\nprint('first', flush=True)\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])\n"
        "print('done')\n",
    )
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -Arguments @($env:KIT_SCRIPT) "
        f"-DrainSeconds 1{timeout}; "
        "$said = @($run.Stdout -split '\\r?\\n') -join ','; "
        "Write-Output ('code=' + $run.Code + ' said=' + $said); "
        "Write-Output ('seconds=' + $run.Seconds)",
        KIT_PYTHON=sys.executable,
        KIT_SCRIPT=str(script),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "code=0 said=first,done," in result.stdout
    assert float(result.stdout.split("seconds=")[1].split()[0]) < 12


def test_below_normal_priority_reaches_the_grandchild(tmp_path: Path) -> None:
    """Godot's console wrapper starts the real Godot: that process must be polite too."""
    root = _project(tmp_path)
    report = write(
        tmp_path / "report.ps1",
        "param([string]$Label = 'child')\n"
        '"$Label=" + (Get-Process -Id $PID).PriorityClass\n'
        "if ($Label -eq 'child') {\n"
        "    & (Get-Process -Id $PID).Path -NoProfile -File $PSCommandPath -Label grandchild\n"
        "}\n",
    )
    result = _call(
        root,
        "$before = (Get-Process -Id $PID).PriorityClass; "
        "$run = Invoke-KitProcess -FilePath (Get-Process -Id $PID).Path -BelowNormal "
        "-TimeoutSeconds 60 -Arguments @('-NoProfile', '-File', $env:KIT_REPORT); "
        "Write-Output $run.Text; "
        "Write-Output ('restored=' + ((Get-Process -Id $PID).PriorityClass -eq $before))",
        KIT_REPORT=str(report),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "child=BelowNormal" in result.stdout
    assert "grandchild=BelowNormal" in result.stdout
    assert "restored=True" in result.stdout  # the caller's own priority is put back


@pytest.mark.parametrize("polite", ["", " -BelowNormal"])
def test_a_program_gets_variables_of_its_own_and_the_caller_keeps_its(
    tmp_path: Path, polite: str
) -> None:
    """-Environment is for the program alone: a variable that moves Godot's user data must
    not move this PowerShell's, nor that of the next program it starts."""
    root = _project(tmp_path)
    names = ("KIT_ONLY_THERE", "KIT_REPLACED", "KIT_KEPT")
    said = "', '".join(names)
    script = write(
        tmp_path / "environment.py",
        f"import json, os\n\nprint(json.dumps([os.environ.get(name) for name in ('{said}')]))\n",
    )
    result = _call(
        root,
        "$given = @{ KIT_ONLY_THERE = 'a b'; KIT_REPLACED = 'the program''s' }; "
        "foreach ($turn in 1..2) { "
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -Arguments @($env:KIT_SCRIPT) "
        f"-TimeoutSeconds 60{polite} -Environment $given; "
        "Write-Output $run.Stdout.Trim(); $given = @{} }; "
        "Write-Output ('mine=' + $env:KIT_REPLACED + ',' + [string]$env:KIT_ONLY_THERE)",
        KIT_PYTHON=sys.executable,
        KIT_SCRIPT=str(script),
        KIT_REPLACED="the caller's",
        KIT_KEPT="kept",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        '["a b", "the program\'s", "kept"]',
        '[null, "the caller\'s", "kept"]',  # the next program: nothing is left over
        "mine=the caller's,",
    ]


@pytest.mark.parametrize(
    ("platform", "variable"),
    [("windows", "APPDATA"), ("linux", "XDG_DATA_HOME"), ("macos", "HOME")],
)
def test_the_variable_that_moves_user_data_is_each_systems_own(
    tmp_path: Path, platform: str, variable: str
) -> None:
    """Godot reads APPDATA on Windows, XDG_DATA_HOME on Linux and, on macOS, nothing but
    HOME. A run that gets "a user folder of its own" gets the one its system reads."""
    root = _project(tmp_path)
    folder = tmp_path / "user data of the run"
    result = _call(
        root,
        f"Get-KitUserDataEnvironment -Folder $env:KIT_FOLDER -Platform {platform} | "
        "ConvertTo-Json -Compress",
        KIT_FOLDER=str(folder),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    moved = json.loads(result.stdout)
    assert moved == {variable: str(folder)}
    # Where that system's Godot then keeps user://, with the developer's own values around.
    mine = {"APPDATA": "C:/Users/me/AppData/Roaming", "HOME": "/home/me", "XDG_DATA_HOME": "/d"}
    assert Path(godot_data_root(mine | moved, platform)).is_relative_to(folder)
    # This machine's is the one the fixtures use for a real Godot.
    here = _call(
        root, "(Get-KitUserDataEnvironment -Folder $env:KIT_FOLDER).Keys", KIT_FOLDER=str(folder)
    )
    assert here.stdout.strip() == USER_DATA_VARIABLE
    # Godot ignores a relative XDG_DATA_HOME and uses the developer's folder: refused.
    relative = _call(root, f"Get-KitUserDataEnvironment -Folder 'user data' -Platform {platform}")
    assert relative.returncode == 1, relative.stdout + relative.stderr
    assert relative.stdout.strip() == (
        "THROWS: Get-KitUserDataEnvironment: -Folder must be an absolute path; it is 'user data'."
    )


def test_a_powershell_that_may_be_killed_is_told_to_keep_no_files(tmp_path: Path) -> None:
    """Every system: it writes no startup profile, which is the user's own file. Linux and
    macOS: .NET and PowerShell keep pipes in the temp folder, and a killed process leaves
    them. Windows has no such file."""
    root = _project(tmp_path)
    result = _call(
        root,
        "foreach ($system in 'windows', 'linux', 'macos', '') { "
        "Get-KitQuietPowerShellEnvironment -Platform $system | ConvertTo-Json -Compress }",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    profile = {"DOTNET_MultiCoreJitNoProfileGather": "1"}
    quiet = {"DOTNET_EnableDiagnostics": "0", "POWERSHELL_DIAGNOSTICS_OPTOUT": "1"}
    said = [json.loads(line) for line in result.stdout.splitlines()]
    here = profile if WINDOWS else quiet | profile
    assert said == [profile, quiet | profile, quiet | profile, here]
    # What the fixtures give every PowerShell they start.
    assert quiet == QUIET_DOTNET and profile == NO_STARTUP_PROFILE


@posix_only("a temp folder named by a relative path (Windows makes its own absolute)")
def test_a_relative_temp_folder_loses_no_output(tmp_path: Path) -> None:
    """TMPDIR=tmp is found from the folder the PowerShell process started in, not from the
    one a script has moved to since."""
    root = _project(tmp_path)
    (tmp_path / "started here" / "tmp").mkdir(parents=True)
    (tmp_path / "moved here").mkdir()
    result = run_powershell(
        ". $env:KIT_RESOLVER; Set-Location -LiteralPath $env:KIT_MOVED; "
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON "
        "-Arguments @('-c', 'print(42)') -TimeoutSeconds 60; "
        "Write-Output ('said=' + $run.Stdout.Trim() + ' code=' + $run.Code)",
        env=clean_env(
            KIT_RESOLVER=str(root / "tools" / "godot_resolver.ps1"),
            KIT_MOVED=str(tmp_path / "moved here"),
            KIT_PYTHON=sys.executable,
            TMPDIR="tmp",
        ),
        cwd=tmp_path / "started here",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "said=42 code=0"
    assert list((tmp_path / "started here" / "tmp").iterdir()) == []


def test_process_helper_kills_a_hung_process(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath (Get-Process -Id $PID).Path "
        "-Arguments @('-NoProfile', '-Command', 'Write-Output started; Start-Sleep 60') "
        "-TimeoutSeconds 3; "
        "Write-Output ('code=' + $run.Code + ' timedout=' + $run.TimedOut); "
        "Write-Output ('seconds=' + $run.Seconds)",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "code=124 timedout=True" in result.stdout
    seconds = float(result.stdout.split("seconds=")[1].split()[0])
    assert seconds < 30


def test_a_timeout_also_kills_what_the_process_started(tmp_path: Path) -> None:
    root = _project(tmp_path)
    alive = tmp_path / "grandchild.pid"
    script = write(
        tmp_path / "parent.py",
        "import subprocess, sys, time\n\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\ntime.sleep(60)\n",
    )
    result = _call(
        root,
        "$run = Invoke-KitProcess -FilePath $env:KIT_PYTHON -TimeoutSeconds 3 -BelowNormal "
        "-Arguments @($env:KIT_SCRIPT, $env:KIT_ALIVE); Start-Sleep -Milliseconds 500; "
        "$child = [int](Get-Content $env:KIT_ALIVE); "
        "$left = Get-Process -Id $child -ErrorAction SilentlyContinue; "
        "$alive = [bool]($left -and -not $left.HasExited); "
        "Write-Output ('timedout=' + $run.TimedOut + ' left=' + $alive)",
        KIT_PYTHON=sys.executable,
        KIT_SCRIPT=str(script),
        KIT_ALIVE=str(alive),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "timedout=True left=False" in result.stdout


DISPLAY_CASES = [
    # (platform, environment, arguments, a window is asked for and there is no display)
    ("linux", "@{}", "@()", True),
    ("linux", "@{}", "@('-e')", True),
    ("linux", "@{}", "@('--import')", True),
    ("linux", "@{}", "@('--script', 'res://x.gd', '--', '--headless')", True),
    ("linux", "@{}", "@('--display-driver', 'x11')", True),
    ("linux", "@{}", "@('--headless', '--import')", False),
    ("linux", "@{}", "@('--path', 'x', '--headless')", False),
    ("linux", "@{}", "@('--display-driver', 'headless', '--quit')", False),
    ("linux", "@{}", "@('--version')", False),
    ("linux", "@{}", "@('--help')", False),
    ("linux", "@{}", "@('--doctool', 'docs')", False),
    ("linux", "@{ DISPLAY = ':0' }", "@()", False),
    ("linux", "@{ WAYLAND_DISPLAY = 'wayland-0' }", "@('-e')", False),
    ("linux", "@{ DISPLAY = ''; WAYLAND_DISPLAY = '' }", "@()", True),
    ("windows", "@{}", "@()", False),
    ("macos", "@{}", "@('-e')", False),
]


def test_a_window_without_a_display_is_one_line_on_linux(tmp_path: Path) -> None:
    root = _project(tmp_path)
    calls = "; ".join(
        f"'[' + (Get-KitDisplayProblem -GodotArgs {arguments} -Platform {platform} "
        f"-Environment {environment}) + ']'"
        for platform, environment, arguments, _ in DISPLAY_CASES
    )
    result = _call(root, calls)
    assert result.returncode == 0, result.stdout + result.stderr
    said = result.stdout.splitlines()
    assert len(said) == len(DISPLAY_CASES), result.stdout
    for line, case in zip(said, DISPLAY_CASES, strict=True):
        if case[3]:
            assert line.startswith("[no display: DISPLAY and WAYLAND_DISPLAY are not set"), case
            assert "--headless" in line and "xvfb-run -a pwsh ./tools/godot.ps1" in line, case
        else:
            assert line == "[]", case
    # With no -Environment it reads this process's variables.
    mine = _call(
        root, "'[' + (Get-KitDisplayProblem -Platform linux) + ']'", DISPLAY="", WAYLAND_DISPLAY=""
    )
    assert mine.stdout.startswith("[no display"), mine.stdout + mine.stderr
    shown = _call(root, "'[' + (Get-KitDisplayProblem -Platform linux) + ']'", DISPLAY=":7")
    assert shown.stdout.strip() == "[]", shown.stdout + shown.stderr
