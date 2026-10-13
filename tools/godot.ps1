<#
Runs Godot against this project (project.godot next to the tools folder),
forwarding every argument. Works from any working directory.

  pwsh ./tools/godot.ps1                      # run the game
  pwsh ./tools/godot.ps1 -e                   # open the editor
  pwsh ./tools/godot.ps1 --headless --import  # import assets, refresh the class cache
  pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd --user-args --all

The standard (non-Mono) build is used; tools/godot_resolver.ps1 lists where it
is looked for (GODOT_BIN, GODOT4_EXE, .tools/godot, kit.json search roots, PATH).

PowerShell consumes a bare `--` before a script sees it. Write `--user-args`
instead: this wrapper turns it into the `--` that separates Godot's arguments
from the script's.

Before forwarding (unless --import is already an engine argument) the project
is re-imported when the class cache is stale (tools/class_cache.ps1), and the
output folder of an export is created (tools/export_paths.ps1).

Exit code: Godot's. 1 when Godot could not be started at all (not found, no
project.godot, a failed import); the reason is printed.
#>
#Requires -Version 7.0

$ErrorActionPreference = "Stop"

function Stop-Wrapper {
    param([string]$Message)
    Write-Host "godot.ps1: $Message" -ForegroundColor Red
    exit 1
}

# Every argument goes to Godot, so a lone help switch would start the game.
if ($args.Count -eq 1 -and $args[0] -in @('-?', '-h', '/?')) {
    Write-Host "usage: pwsh ./tools/godot.ps1 [godot arguments] [--user-args <script arguments>]"
    Write-Host "  no arguments: the game; -e: the editor; --help: Godot's own options"
    exit 0
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
. (Join-Path $PSScriptRoot "godot_resolver.ps1")

try {
    $godot = Resolve-GodotPath
} catch {
    Stop-Wrapper $_.Exception.Message
}
$projectFile = Join-Path $projectRoot "project.godot"
if (-not (Test-Path -LiteralPath $projectFile)) {
    Stop-Wrapper "Godot project not found at '$projectFile'."
}

$godotArgs = @()
$foundUserArgs = $false
foreach ($argument in $args) {
    if ($argument -eq "--user-args") {
        if ($foundUserArgs) { Stop-Wrapper "--user-args may be supplied only once." }
        $godotArgs += "--"
        $foundUserArgs = $true
    } else {
        $godotArgs += $argument
    }
}

# A pull that adds or moves a class_name script leaves the gitignored class
# cache stale and every script naming that class fails to parse. Re-import first.
. (Join-Path $PSScriptRoot "class_cache.ps1")
if (-not (Test-GodotImportArg -GodotArgs $godotArgs) -and
    (Test-GodotClassCacheStale -ProjectRoot $projectRoot)) {
    try {
        Update-GodotClassCache -Godot $godot -ProjectRoot $projectRoot
    } catch {
        Stop-Wrapper $_.Exception.Message
    }
}

. (Join-Path $PSScriptRoot "export_paths.ps1")
Initialize-GodotExportDirectory -ProjectRoot $projectRoot -GodotArgs $godotArgs

# The call operator neither waits for a GUI-subsystem program nor reports its
# exit code, and that is what the plain Godot .exe is (the resolver hands it
# over only when no _console.exe sits beside it). It is started and waited for
# explicitly; anything else runs through the call operator, which keeps its
# output on PowerShell's pipeline.
if (Test-KitGuiSubsystem $godot) {
    exit (Invoke-KitAttachedProcess -FilePath $godot -Arguments $godotArgs -WorkingDirectory $projectRoot)
}

$global:LASTEXITCODE = $null
Push-Location -LiteralPath $projectRoot
try {
    & $godot @godotArgs
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($null -eq $exitCode) {
    # An exit code nobody reported must not read as success.
    Stop-Wrapper "'$godot' ended without an exit code."
}
exit $exitCode
