"""The kit's PowerShell scripts run on every PowerShell 7, from 7.0 on.

`#Requires -Version 7.0` says so in each script. A call that only a later release has
breaks that promise where nobody looks: the developer's PowerShell is new, and the script
fails on a machine that kept an older one. PowerShell 7.0 is .NET Core 3.1; each name below
came later. tools/doctor.ps1 asks for 7.0 on Windows and for 7.2 on Linux and macOS, where
7.0 and 7.1 fail for reasons no script can change (Get-KitPowerShellProblem).

This test reads the scripts. The proof is a run: put an old `pwsh` first on PATH and run
this folder (`uv run pytest tools/tests`); every gate test then starts that PowerShell.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from _gate_fixtures import NEEDS_PWSH, REPO, clean_env, copy_tools, run_powershell

sys.path.insert(0, str(REPO / "tools"))
import kit_config  # noqa: E402

# What a script must not call: the release that brought it, and what to write.
LATER = {
    "[System.OperatingSystem]::": "7.1: $IsWindows, $IsMacOS (Get-KitPlatform)",
    "[OperatingSystem]::": "7.1: $IsWindows, $IsMacOS (Get-KitPlatform)",
    "WaitForExitAsync": "7.1: Invoke-KitProcess waits and reads the output",
    "ToHexString": "7.1: Get-FileHash, or BitConverter",
    ".UnixMode": "7.1: chmod; a refused start is Test-KitNotExecutable",
    "ResolveLinkTarget": "7.2: Get-Item's LinkType and Target (Resolve-KitLinkedPath)",
    "CreateSymbolicLink": "7.2: New-Item -ItemType SymbolicLink",
    "]::ProcessPath": "7.2: (Get-Process -Id $PID).Path",
    "$PSStyle": "7.2: Write-Host -ForegroundColor",
    "UnixFileMode": "7.3: chmod; a refused start is Test-KitNotExecutable",
    "$PSNativeCommandArgumentPassing": "7.3: Invoke-KitProcess passes arguments as they are",
    "$PSNativeCommandUseErrorActionPreference": "7.3: test $LASTEXITCODE",
}
REQUIRES = re.compile(r"^#Requires -Version (\S+)", re.MULTILINE)
FLOOR = "7.0"


def _later_calls(script: str) -> list[str]:
    """The names of LATER in the code of a script: a comment line may explain one."""
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    code = "\n".join(lines)
    return [name for name in LATER if name in code]


def test_no_script_calls_what_powershell_7_0_does_not_have() -> None:
    scripts = sorted([*REPO.glob("*.ps1"), *(REPO / "tools").glob("*.ps1")])
    assert len(scripts) > 10, scripts
    found = []
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        name = script.relative_to(REPO).as_posix()
        found += [f"{name}: {call} needs PowerShell {LATER[call]}" for call in _later_calls(text)]
        # The floor is one number, the same in every script that states it.
        found += [
            f"{name}: #Requires -Version {floor}, not {FLOOR}"
            for floor in REQUIRES.findall(text)
            if floor != FLOOR
        ]
    assert not found, "\n".join(found)


def test_the_check_reads_code_and_not_comments() -> None:
    script = (
        "# [System.OperatingSystem]::IsWindows() needs 7.1\n"
        "    # $PSStyle too\n"
        "if ([System.IO.File]::GetUnixFileMode($path)) { $process.WaitForExitAsync() }\n"
    )
    assert _later_calls(script) == ["WaitForExitAsync", "UnixFileMode"]
    assert REQUIRES.findall('# no "#Requires -Version 7" here\n#Requires -Version 7.4\n') == ["7.4"]


def test_the_shared_files_state_no_floor_of_their_own() -> None:
    """They are dot-sourced, also by the scripts that start under Windows PowerShell 5.1
    to say "use pwsh": a #Requires line in them would stop those first."""
    for script in ("godot_resolver.ps1", "kit_config.ps1", "class_cache.ps1"):
        assert not REQUIRES.search((REPO / "tools" / script).read_text(encoding="utf-8")), script


@NEEDS_PWSH
def test_the_system_is_named_as_python_names_it(tmp_path: Path) -> None:
    """Get-KitPlatform is the first thing every tool asks, in whatever PowerShell runs it."""
    root = tmp_path / "project"
    copy_tools(root)
    result = run_powershell(
        ". $env:KIT_CONFIG; (Get-KitPlatform) + ' ' + (Get-KitArchitecture) + ' ' + "
        "$PSVersionTable.PSVersion.Major",
        env=clean_env(KIT_CONFIG=str(root / "tools" / "kit_config.ps1")),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    here = f"{kit_config.host_platform()} {kit_config.host_architecture()} 7"
    assert result.stdout.strip() == here
