# Engine-level GDScript type check.
#
# gdlint (tools/lint_gdscript.ps1) checks style only. The strict warnings in
# project.godot ([debug] gdscript/warnings/*, promoted to errors) are enforced
# by the engine alone. This script loads every authored .gd file in one headless
# Godot process (tools/typecheck.gd): a load compiles the script with the same
# analysis as `--check-only`, so untyped declarations, integer division,
# shadowing and the rest fail here, in seconds, not one Godot start per file.
#
# Usage:
#   pwsh ./tools/typecheck.ps1                  # every authored .gd file
#   pwsh ./tools/typecheck.ps1 -Path scripts    # one folder or file
#   pwsh ./tools/typecheck.ps1 -NoImport        # skip the import pass
#
# The files are the project's authored source (kit.json source.exclude /
# source.include; see tools/kit_config.ps1). A file or an excluded folder given
# to -Path by name is always checked.
#
# Exit codes:
#   0 = every script compiles clean
#   1 = a script failed or was not checked (one the checker is built on
#       included: then no other file is checked), project.godot leaves a strict
#       warning below error, no files were found, the import failed, or
#       kit.json cannot be used
#   2 = Godot not found
#   3 = the checker itself broke (tools/typecheck.gd printed TOOL_ERROR, or
#       does not load); its verdicts mean nothing
#
# Notes:
# - An `--import` pass runs first so new and moved scripts are registered;
#   otherwise res:// cannot resolve them and the check passes without them.
# - Godot's output is captured through redirected files (Invoke-KitProcess);
#   `& godot 2>file` captures nothing from the GUI binary and reads as a pass.
# - Godot applies no GDScript warnings under res://addons by default. A file
#   that loads with the warnings off was not checked, and that is a failure
#   here: project.godot must include the folder ([debug]
#   gdscript/warnings/directory_rules), or the folder must leave kit.json
#   source.include.
# - Strict means all 16 warnings (Get-KitStrictWarnings). Godot rejects the
#   probe script as soon as one of them is an error, so a project that promotes
#   only some would read as checked: project.godot is read for the rest.
# - kit.json gdscript.strict = false (a project that has not adopted the strict
#   warnings): such files pass, checked for compile errors only, and one line
#   says so. Parse errors and whatever project.godot does promote still fail.
#Requires -Version 7.0

# Only -Path is positional: a second path is an error, not a timeout.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)][string]$Path = "",
    [switch]$NoImport,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "godot_resolver.ps1")
. (Join-Path $PSScriptRoot "class_cache.ps1")
$projectRoot = Get-KitRoot
try {
    $config = Get-KitConfig
} catch {
    Write-Host "Type-check: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
try {
    $godot = Resolve-GodotPath
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 2
}

$errorPattern = 'SCRIPT ERROR|Parse Error|treated as error|Failed to load script|Cannot find'
$strictOffNote = "strict typing is off (kit.json gdscript.strict = false)"
$strictOnHint = "To turn it on: set gdscript.strict to true in kit.json, then pwsh ./tools/doctor.ps1 " +
    "lists the project.godot lines to add."

function Invoke-GodotCheck {
    param([string[]]$GodotArgs)
    return Invoke-KitProcess -FilePath $godot -Arguments $GodotArgs `
        -WorkingDirectory $projectRoot -TimeoutSeconds $TimeoutSeconds
}

function Write-UncheckedReport {
    # Files Godot compiled with no strict warning in force: where, and the fix.
    # -WarningsSet: project.godot promotes all of them, so a folder rule is why.
    param([string[]]$Paths, [switch]$WarningsSet)
    Write-Host "Not checked: $($Paths.Count) file(s) loaded with no strict GDScript warning in force:" `
        -ForegroundColor Red
    foreach ($path in ($Paths | Select-Object -First 10)) { Write-Host "  $path" -ForegroundColor Red }
    if ($Paths.Count -gt 10) { Write-Host "  ... and $($Paths.Count - 10) more" -ForegroundColor Red }
    $inAddons = @($Paths | Where-Object { $_ -match '^res://addons/' })
    if ($inAddons.Count -gt 0) {
        $addonFolders = @(
            foreach ($path in $inAddons) {
                if ($path -match '^res://addons/([^/]+)/') { $Matches[1] }
            }
        ) | Sort-Object -Unique -CaseSensitive
        if (-not $addonFolders) { $addonFolders = @("<folder>") }
        $entries = @($addonFolders | ForEach-Object { "`"res://addons/$_`": 1" }) -join ", "
        Write-Host "Godot applies no warnings under res://addons unless project.godot includes the folder." `
            -ForegroundColor Yellow
        Write-Host "Fix: project.godot, [debug]: add $entries to gdscript/warnings/directory_rules;" `
            -ForegroundColor Yellow
        Write-Host "  with no rules there yet:  gdscript/warnings/directory_rules={`"res://addons`": 0, $entries}" `
            -ForegroundColor Yellow
        Write-Host "Or take the folder out of kit.json source.include." -ForegroundColor Yellow
    }
    if ($inAddons.Count -lt $Paths.Count -and $WarningsSet) {
        Write-Host "Outside res://addons a rule in project.godot turns the warnings off for the folder:" `
            -ForegroundColor Yellow
        Write-Host "Fix: [debug] gdscript/warnings/directory_rules, set the folder to 1 or drop its entry." `
            -ForegroundColor Yellow
    }
}

function Write-BrokenRunner {
    <#
    tools/typecheck.gd, or a script it is built on, does not compile: -Problem is
    what Godot said. Each error is two lines, the text and then where:
        SCRIPT ERROR: Parse Error: Function "f()" has no static return type. (...)
           at: GDScript::reload (res://tools/headless_tool.gd:115)
    Prints a FAIL per file in the form of the per-file pass and returns how many
    files it named; 0 when Godot named none.
    #>
    param([string]$Problem)
    $where = '(?m)^SCRIPT ERROR: (?<what>[^\r\n]+)\r?\n\s+at: [^\r\n(]*\((?<file>res://[^\r\n:]+):(?<line>\d+)\)'
    $broken = [ordered]@{}
    foreach ($found in [regex]::Matches($Problem, $where)) {
        # The scripts that depend on the broken one: an echo, not a cause.
        if ($found.Groups["what"].Value -match 'Failed to compile depended scripts') { continue }
        $file = $found.Groups["file"].Value
        if (-not $broken.Contains($file)) { $broken[$file] = @() }
        $broken[$file] += "$($found.Groups['what'].Value.Trim()) (${file}:$($found.Groups['line'].Value))"
    }
    foreach ($file in $broken.Keys) {
        Write-Host "  FAIL  $file" -ForegroundColor Red
        foreach ($message in $broken[$file]) { Write-Host "        $message" -ForegroundColor DarkRed }
    }
    return $broken.Count
}

try {
    $files = @(Get-KitSourceFiles -Path $Path -Extensions @(".gd"))
} catch {
    Write-Host "Type-check: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ($files.Count -eq 0) {
    $where = if ($Path) { "under '$Path'" } else { "in the project (see kit.json source.exclude)" }
    Write-Host "No .gd files found to type-check $where." -ForegroundColor Red
    exit 1
}

# Register new/changed scripts so res:// can resolve them.
$importErrors = ""
if (-not $NoImport) {
    # A fresh clone has no class cache; its first import can be noisy and still
    # succeed, which the strict pass below would report as a failure.
    if (Test-GodotClassCacheStale -ProjectRoot $projectRoot) {
        try {
            Update-GodotClassCache -Godot $godot -ProjectRoot $projectRoot -TimeoutSeconds $TimeoutSeconds
        } catch {
            Write-Host $_.Exception.Message -ForegroundColor Red
            exit 1
        }
    }
    Write-Host "Importing project (registering scripts)..." -ForegroundColor DarkGray
    $importResult = Invoke-GodotCheck -GodotArgs @("--headless", "--import")
    if ($importResult.Code -ne 0) {
        Write-Host "Godot import failed before type-checking (exit $($importResult.Code)):" -ForegroundColor Red
        Write-Host $importResult.Text -ForegroundColor DarkRed
        exit 1
    }
    # An import that exits 0 can still report script errors (an autoload that
    # does not compile). The per-file pass below names the file; if it finds
    # nothing, these errors fail the gate at the end.
    if ($importResult.Text -match $errorPattern) { $importErrors = $importResult.Text }
}

Write-Host "Type-checking $($files.Count) GDScript file(s) in one Godot process..." -ForegroundColor Cyan

# The runner is a script of the project too. Godot does not quit when it fails
# to compile (it idles until the timeout), so it is asked first.
$runnerProblem = Get-KitGodotScriptProblem -Godot $godot -Script "res://tools/typecheck.gd" `
    -ProjectRoot $projectRoot -TimeoutSeconds $TimeoutSeconds
if ($runnerProblem) {
    $named = Write-BrokenRunner -Problem $runnerProblem
    Write-Host ""
    if ($named -eq 0) {
        Write-Host "Type-check: the checker itself broke (tools/typecheck.gd does not load):" -ForegroundColor Red
        Write-Host $runnerProblem -ForegroundColor DarkRed
        exit 3
    }
    # tools/typecheck.gd, or a script it extends or preloads.
    Write-Host "Type-check FAILED: $named checker file(s) do not compile; no other file was checked." `
        -ForegroundColor Red
    exit 1
}

# Strict means every one of these is an error; see the notes at the top.
$weakWarnings = @()
if ($config.GdscriptStrict) {
    $projectFile = Join-Path $projectRoot "project.godot"
    $projectLines = @(Get-Content -LiteralPath $projectFile -ErrorAction SilentlyContinue)
    $weakWarnings = @(Get-KitWeakWarnings -ProjectLines $projectLines)
}

$listFile = [System.IO.Path]::GetTempFileName()
$paths = @(foreach ($file in $files) { "res://" + (ConvertTo-KitRelativePath $file.FullName) })
Set-Content -LiteralPath $listFile -Value $paths -Encoding utf8NoBOM
try {
    $result = Invoke-GodotCheck -GodotArgs @(
        "--headless", "--script", "res://tools/typecheck.gd", "--", $listFile
    )
} finally {
    Remove-Item -LiteralPath $listFile -ErrorAction SilentlyContinue
}

$failed = @()
$unchecked = @()
$done = $null
foreach ($line in ($result.Text -split "`n")) {
    $line = $line.TrimEnd()
    if ($line -match '^TC FAIL (.+)$') {
        $failed += $Matches[1]
        Write-Host "  FAIL  $($Matches[1])" -ForegroundColor Red
    } elseif ($line -match '^TC unchecked (.+)$') {
        $unchecked += $Matches[1]
    } elseif ($line -match '^TC_ERR (.+)$') {
        Write-Host "        $($Matches[1])" -ForegroundColor DarkRed
    } elseif ($line -match '^TC_DONE checked=(\d+) failed=(\d+) unchecked=(\d+)') {
        $done = [int]$Matches[1] + [int]$Matches[2] + [int]$Matches[3]
    }
}

Write-Host ""
if ($result.TimedOut) {
    Write-Host "Type-check runner did not finish in $TimeoutSeconds s:" -ForegroundColor Red
    Write-Host $result.Text -ForegroundColor DarkRed
    exit 1
}
# The runner's own code raised a script error (a Godot version it does not fit,
# a damaged tools/ folder): no verdict above can be trusted.
if ($result.Code -eq 3 -or $result.Text -match '(?m)^TOOL_ERROR ') {
    Write-Host "Type-check: the checker itself broke (tools/typecheck.gd, exit $($result.Code)):" `
        -ForegroundColor Red
    Write-Host $result.Text -ForegroundColor DarkRed
    exit 3
}
if ($null -eq $done) {
    Write-Host "Type-check runner did not finish (exit=$($result.Code)):" -ForegroundColor Red
    Write-Host $result.Text -ForegroundColor DarkRed
    exit 1
}
if ($done -ne $files.Count) {
    Write-Host "Type-check runner gave a verdict for $done of $($files.Count) files:" -ForegroundColor Red
    Write-Host $result.Text -ForegroundColor DarkRed
    exit 1
}
# gdscript.strict = false: a file loaded with the warnings off is not a failure.
$notChecked = @(if ($config.GdscriptStrict) { $unchecked })
$nothingFound = $failed.Count -eq 0 -and $notChecked.Count -eq 0 -and $weakWarnings.Count -eq 0
if ($nothingFound -and $result.Code -eq 0) {
    if ($importErrors) {
        Write-Host "Every file type-checks, but the import before it reported errors:" -ForegroundColor Red
        Write-Host $importErrors -ForegroundColor DarkRed
        exit 1
    }
    $clean = "Type-check: CLEAN ($($files.Count) of $($files.Count) files)"
    if ($unchecked.Count -gt 0) {
        Write-Host ("Type-check: ${strictOffNote}: $($unchecked.Count) of $($files.Count) file(s) " +
            "were checked for compile errors only. $strictOnHint") -ForegroundColor Yellow
        # The verdict line is all tools/check.ps1 shows of a passing gate.
        $clean += "; $($unchecked.Count) for compile errors only: strict typing is off"
    }
    Write-Host $clean -ForegroundColor Green
    exit 0
}
if ($nothingFound) {
    Write-Host "Type-check runner exited $($result.Code) without a failing file:" -ForegroundColor Red
    Write-Host $result.Text -ForegroundColor DarkRed
    exit 1
}
if ($notChecked.Count -gt 0) {
    Write-UncheckedReport -Paths $notChecked -WarningsSet:($weakWarnings.Count -eq 0)
}
if ($weakWarnings.Count -gt 0) {
    $total = @(Get-KitStrictWarnings).Count
    Write-Host ("Not strict: project.godot leaves $($weakWarnings.Count) of the $total strict GDScript " +
        "warnings below error: $($weakWarnings -join ', ')") -ForegroundColor Red
    Write-Host "Fix: pwsh ./tools/doctor.ps1 prints the [debug] gdscript/warnings line to add." `
        -ForegroundColor Yellow
}
if ($notChecked.Count -gt 0 -or $weakWarnings.Count -gt 0) {
    Write-Host "A project that does not want the strict warnings: set gdscript.strict to false in kit.json." `
        -ForegroundColor Yellow
    Write-Host ""
}
$verdict = @()
if ($failed.Count -gt 0) {
    $verdict += if ($config.GdscriptStrict) {
        "$($failed.Count) file(s) violate the strict GDScript warnings"
    } else {
        "$($failed.Count) file(s) do not compile"
    }
}
if ($notChecked.Count -gt 0) { $verdict += "$($notChecked.Count) file(s) were not checked (warnings off)" }
if ($weakWarnings.Count -gt 0) {
    $verdict += "$($weakWarnings.Count) strict warning(s) are not errors in project.godot"
}
if (-not $config.GdscriptStrict -and $unchecked.Count -gt 0) { $verdict += $strictOffNote }
Write-Host "Type-check FAILED: $($verdict -join '; ')." -ForegroundColor Red
exit 1
