# GDScript style gate: gdformat (formatting) and gdlint (rules in .gdlintrc),
# both from gdtoolkit.
#
# Usage:
#   pwsh ./tools/lint_gdscript.ps1                # check every authored .gd file
#   pwsh ./tools/lint_gdscript.ps1 -Fix           # format in place, then lint
#   pwsh ./tools/lint_gdscript.ps1 -Path scripts  # one folder or file
#
# The files are the project's authored source (kit.json source.exclude /
# source.include; see tools/kit_config.ps1). A -Path given by name is always
# linted, even inside an excluded folder. -Path is project-relative or absolute.
#
# Where install.ps1 put the kit into a project (tools/kit_manifest.json exists),
# the files that manifest lists are neither linted nor formatted: they are the
# kit's and pass the kit's rules, whatever this project's .gdlintrc says. One
# line says how many were left out.
#
# Exit codes:
#   0 = clean
#   1 = lint or format issues, no files found, or an unreadable kit.json
#   2 = gdtoolkit missing (run: uv sync)
#
# Style only: the engine's strict type check is tools/typecheck.ps1.
#Requires -Version 7.0

# Only -Path is positional: a second path is an error, not a line length.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)][string]$Path = "",
    [switch]$Fix,
    [int]$LineLength = 100
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "kit_config.ps1")
$projectRoot = Get-KitRoot
try {
    [void](Get-KitConfig)
} catch {
    Write-Host "GDScript lint: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

function Resolve-Tool {
    param([string]$Name)
    # The project's pinned toolchain (uv sync -> .venv) wins over PATH.
    foreach ($candidate in @(
            (Join-Path $projectRoot ".venv/Scripts/$Name.exe"),
            (Join-Path $projectRoot ".venv/bin/$Name")
        )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Invoke-GdtoolkitBatches {
    param(
        [string]$Tool,
        [object[]]$ToolArgs,
        [string[]]$FilePaths
    )
    # Windows process creation has a finite command-line budget, so files go
    # in batches of relative paths. Batches are small and run in parallel, one
    # per core: gdtoolkit is single-threaded and slow on a large project.
    $maxBatchChars = 12000
    $maxBatchFiles = [Math]::Max(8, [Math]::Ceiling($FilePaths.Count / [Environment]::ProcessorCount))
    $batches = [System.Collections.Generic.List[object]]::new()
    $batch = @()
    $batchChars = 0
    foreach ($filePath in $FilePaths) {
        $cost = $filePath.Length + 3  # quotes, separator, and a small margin
        if ($batch.Count -gt 0 -and ($batch.Count -ge $maxBatchFiles -or
                $batchChars + $cost -gt $maxBatchChars)) {
            $batches.Add($batch)
            $batch = @()
            $batchChars = 0
        }
        $batch += $filePath
        $batchChars += $cost
    }
    if ($batch.Count -gt 0) { $batches.Add($batch) }

    # Each batch returns its output and exit code. Only failing batches print,
    # in batch order; clean ones would just repeat "no problems found".
    # The first batch runs alone: gdtoolkit builds its grammar cache on a machine's first
    # run (%LOCALAPPDATA%\gdtoolkit), and two processes building it at once fail with
    # "Cannot create a file when that file already exists".
    $first = $batches[0]
    $results = @(
        try {
            $output = & $Tool @ToolArgs @first 2>&1 | Out-String
            [PSCustomObject]@{ Index = 0; Output = $output; Code = $LASTEXITCODE }
        } catch {
            [PSCustomObject]@{ Index = 0; Output = "Could not run ${Tool}: $($_.Exception.Message)"; Code = 1 }
        }
    )
    if ($batches.Count -gt 1) {
        $indices = 1..($batches.Count - 1)
        $results += @($indices | ForEach-Object -ThrottleLimit ([Environment]::ProcessorCount) -Parallel {
                $index = $_
                $files = ($using:batches)[$index]
                try {
                    $output = & $using:Tool @using:ToolArgs @files 2>&1 | Out-String
                    [PSCustomObject]@{ Index = $index; Output = $output; Code = $LASTEXITCODE }
                } catch {
                    $message = "Could not run $($using:Tool): $($_.Exception.Message)"
                    [PSCustomObject]@{ Index = $index; Output = $message; Code = 1 }
                }
            } | Sort-Object Index)
    }
    $failed = 0
    foreach ($result in $results) {
        if ($result.Code -ne 0) {
            Write-Host $result.Output.TrimEnd()
            $failed = 1
        }
    }
    return $failed
}

$gdlint = Resolve-Tool "gdlint"
$gdformat = Resolve-Tool "gdformat"

if (-not $gdlint -or -not $gdformat) {
    Write-Host "gdtoolkit not found (need gdlint and gdformat)." -ForegroundColor Red
    if (Test-KitPythonToolsDeclared) {
        Write-Host "Install it with: uv sync   (the pinned gdtoolkit goes into ./.venv)" -ForegroundColor Yellow
    } else {
        Write-Host "pyproject.toml does not name gdtoolkit. Add it with: $(Get-KitPythonToolsFix)" -ForegroundColor Yellow
    }
    exit 2
}

try {
    $files = @(Get-KitSourceFiles -Path $Path -Extensions @(".gd"))
    $kitFiles = @(Get-KitInstalledFiles)
} catch {
    Write-Host "GDScript lint: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# In a project the kit was installed into, the kit's own scripts are left out:
# they follow the kit's .gdlintrc, not this project's, and -Fix must not rewrite
# them (install.ps1 would then call each one a conflict).
$leftOut = 0
if ($kitFiles.Count -gt 0) {
    $installed = [System.Collections.Generic.HashSet[string]]::new(
        [string[]]$kitFiles, [System.StringComparer]::OrdinalIgnoreCase
    )
    $own = @($files | Where-Object { -not $installed.Contains((ConvertTo-KitRelativePath $_.FullName)) })
    $leftOut = $files.Count - $own.Count
    $files = $own
}
$leftOutNote = "$leftOut kit file(s) left out: tools/kit_manifest.json lists them, and the kit's own rules cover them"

$paths = @(
    foreach ($file in $files) {
        # The "./" keeps a root-level -name.gd from being read as a command-line option.
        "./" + (ConvertTo-KitRelativePath $file.FullName)
    }
)

if ($paths.Count -eq 0) {
    $where = if ($Path) { "under '$Path'" } else { "in the project (see kit.json source.exclude)" }
    $kept = if ($leftOut -gt 0) { " ($leftOutNote)" } else { "" }
    Write-Host "No .gd files found to lint $where$kept." -ForegroundColor Red
    exit 1
}
if ($leftOut -gt 0) { Write-Host "$leftOutNote." -ForegroundColor DarkGray }

Write-Host "Linting $($paths.Count) GDScript file(s) with gdtoolkit..." -ForegroundColor Cyan

# Run from the project root so gdtoolkit finds .gdlintrc and the relative paths resolve.
$formatExit = 0
$lintExit = 0
Push-Location -LiteralPath $projectRoot
try {
    if ($Fix) {
        Write-Host "  gdformat (write in place, line-length $LineLength)..." -ForegroundColor Gray
        # gdformat writes through Python's text mode: on Windows every file it
        # rewrites comes back with CRLF line endings. Files that had none keep none.
        $lfFiles = @($files | Where-Object { -not [System.IO.File]::ReadAllText($_.FullName).Contains("`r`n") })
        $formatExit = Invoke-GdtoolkitBatches $gdformat @("--line-length", $LineLength) $paths
        foreach ($file in $lfFiles) {
            $text = [System.IO.File]::ReadAllText($file.FullName)
            if ($text.Contains("`r`n")) {
                [System.IO.File]::WriteAllText($file.FullName, $text.Replace("`r`n", "`n"))
            }
        }
    } else {
        Write-Host "  gdformat --check (line-length $LineLength)..." -ForegroundColor Gray
        $formatExit = Invoke-GdtoolkitBatches $gdformat @("--check", "--line-length", $LineLength) $paths
    }

    Write-Host "  gdlint (rules from .gdlintrc)..." -ForegroundColor Gray
    $lintExit = Invoke-GdtoolkitBatches $gdlint @() $paths
} finally {
    Pop-Location
}

Write-Host ""
if ($formatExit -eq 0 -and $lintExit -eq 0) {
    # The verdict line is all tools/check.ps1 shows of a passing gate.
    $kept = if ($leftOut -gt 0) { " ($($paths.Count) file(s); $leftOut kit file(s) left out)" } else { "" }
    Write-Host "GDScript lint + format: CLEAN$kept" -ForegroundColor Green
    exit 0
}

if ($formatExit -ne 0) {
    # The same files: a -Path that narrowed the check narrows the fix.
    $narrowed = if ($Path) { " -Path `"$Path`"" } else { "" }
    Write-Host "Formatting issues found. Run: pwsh ./tools/lint_gdscript.ps1$narrowed -Fix" -ForegroundColor Red
}
if ($lintExit -ne 0) {
    Write-Host "Lint issues found (see above)." -ForegroundColor Red
}
exit 1
