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

Linux: a run that opens a window needs a display. Without DISPLAY and
WAYLAND_DISPLAY (an ssh session, a CI runner) the wrapper says so in one line
and starts nothing: add --headless, or run it under xvfb-run.

A project without a main scene (application/run/main_scene) has no game to
run: the wrapper says so in one line and starts nothing. Godot itself would
say it in a dialog and wait for a click, with --headless too.

Exit code: Godot's. 1 when Godot could not be started at all (not found, no
project.godot, no display, no main scene, a failed import); the reason is
printed.
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

# Before anything is started: without a main scene the game ends in a dialog that
# waits for a click, and without a display in a page of display driver errors.
$sceneProblem = Get-KitMainSceneProblem -GodotArgs $godotArgs -ProjectRoot $projectRoot
if ($sceneProblem) { Stop-Wrapper $sceneProblem }
$displayProblem = Get-KitDisplayProblem -GodotArgs $godotArgs
if ($displayProblem) { Stop-Wrapper $displayProblem }

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

# Started and waited for explicitly where the call operator cannot be trusted:
# - Windows: it neither waits for a GUI-subsystem program nor reports its exit
#   code, and that is what the plain Godot .exe is (the resolver hands it over
#   only when no _console.exe sits beside it).
# - PowerShell before 7.3, for an argument it would change: it hands a program
#   its arguments unescaped, so a double quote inside one is lost and an empty
#   one is dropped.
# Anything else runs through the call operator, which keeps its output on
# PowerShell's pipeline: `$said = & ./tools/godot.ps1 --version` in a session.
if ((Test-KitGuiSubsystem $godot) -or (Test-KitArgumentLoss -Arguments $godotArgs)) {
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
