# Names the game: the first step after copying this template.
#
# Updates project.godot (works from any working directory):
#   config/name         the window and project title
#   config/description  an optional one-line blurb
#
# Running it again with the same arguments rewrites the same values.
# config/name also names the user:// data folder
# (%APPDATA%\Godot\app_userdata\<name>\ on Windows), so a rename starts with
# empty saves, settings and screenshots; the old folder is left in place.
#
# -RemoveKitTests also deletes the kit's own tests, tools/tests/ (pytest) and
# test/kit/ (gdUnit4), and nothing else: they test the kit's tools, not your
# game. The sample game's tests in test/unit/ stay.
#
# Usage:
#   pwsh ./tools/new_game.ps1 -Title "My Game"
#   pwsh ./tools/new_game.ps1 -Title "My Game" -Blurb "A short tagline."
#   pwsh ./tools/new_game.ps1 -Title "My Game" -RemoveKitTests
#Requires -Version 7.0

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Blurb = "",
    [switch]$RemoveKitTests
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)   # Godot cannot read project.godot with a BOM

function Stop-Rename {
    param([string]$Message)
    Write-Host "new_game: $Message" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Title)) {
    Stop-Rename "Title must contain at least one non-whitespace character."
}
if ($Title -match '[\r\n]') {
    Stop-Rename "Title must be a single line."
}
if ($Blurb -match '[\r\n]') {
    Stop-Rename "Blurb must be a single line."
}

Write-Host "Renaming project" -ForegroundColor Cyan
Write-Host "  Title: $Title"
if ($Blurb) { Write-Host "  Blurb: $Blurb" }

# project.godot: config/name + (optional) config/description.
# Use MatchEvaluator callbacks so a "$" or "\" in the title/blurb is taken
# literally - it can't be misread as a regex backreference/escape.
$projectFile = Join-Path $projectRoot "project.godot"
if (-not (Test-Path -LiteralPath $projectFile)) {
    Stop-Rename "Godot project not found at '$projectFile'."
}
$project = Get-Content -Raw -LiteralPath $projectFile
# The lookahead keeps a CRLF file's line endings as they are.
$namePattern = '(?m)^config/name=".*"(?=\r?$)'
$nameMatches = [regex]::Matches($project, $namePattern)
if ($nameMatches.Count -ne 1) {
    Stop-Rename "expected exactly one config/name entry in '$projectFile'; found $($nameMatches.Count)."
}
$nameEsc = $Title -replace '\\', '\\' -replace '"', '\"'
$descEsc = $Blurb -replace '\\', '\\' -replace '"', '\"'
$nameLine = 'config/name="' + $nameEsc + '"'
$descLine = 'config/description="' + $descEsc + '"'
$project = [regex]::Replace($project, $namePattern, { $nameLine })
if ($Blurb) {
    if ($project -match '(?m)^config/description=') {
        $project = [regex]::Replace($project, '(?m)^config/description=".*"(?=\r?$)', { $descLine })
    } else {
        # Insert a description line right after config/name.
        $project = [regex]::Replace(
            $project, '(?m)^(config/name=".*")(\r?\n)',
            { param($m) $m.Groups[1].Value + $m.Groups[2].Value + $descLine + $m.Groups[2].Value }
        )
    }
}
[System.IO.File]::WriteAllText($projectFile, $project, $utf8NoBom)
Write-Host "  [ok] project.godot" -ForegroundColor Green
if (-not $Blurb) {
    # Left as it was, and in a copy of the template that is the template's own.
    $kept = [regex]::Match($project, '(?m)^config/description="(.*)"(?=\r?$)').Groups[1].Value
    if ($kept) {
        Write-Host "  config/description still says `"$kept`"; -Blurb `"...`" replaces it." -ForegroundColor Yellow
    }
}

$kitTests = @("tools/tests", "test/kit")
if ($RemoveKitTests) {
    foreach ($relative in $kitTests) {
        $folder = Join-Path $projectRoot $relative
        if (Test-Path -LiteralPath $folder -PathType Container) {
            $count = @(Get-ChildItem -LiteralPath $folder -Recurse -File -Force).Count
            Remove-Item -LiteralPath $folder -Recurse -Force
            Write-Host "  [ok] removed $relative/ ($count files)" -ForegroundColor Green
        } else {
            Write-Host "  [ok] $relative/ was already gone" -ForegroundColor Green
        }
    }
} else {
    $present = @($kitTests | Where-Object { Test-Path -LiteralPath (Join-Path $projectRoot $_) -PathType Container })
    if ($present.Count -gt 0) {
        Write-Host "  The kit's own tests are still here ($(($present | ForEach-Object { "$_/" }) -join ', ')); -RemoveKitTests deletes them."
    }
}

Write-Host ""
Write-Host "Done. Next:" -ForegroundColor Cyan
Write-Host "  pwsh ./tools/setup.ps1    # toolchain, first import, prerequisite check"
Write-Host "  pwsh ./tools/check.ps1    # every gate: lint, type check, tests"
Write-Host "  pwsh ./tools/godot.ps1    # run the game (-e opens the editor)"
if (Test-Path -LiteralPath (Join-Path $projectRoot "export_presets.cfg")) {
    Write-Host "  export_presets.cfg has its own product name fields; update them too."
}
