# One-shot bootstrap for a fresh clone. Safe to run again: each step does
# nothing when its work is already done.
#
# Usage:
#   pwsh ./tools/setup.ps1                 # Python tools, first import, prerequisite check
#   pwsh ./tools/setup.ps1 -InstallGodot   # also download the pinned Godot when none is found
#   pwsh ./tools/setup.ps1 -PreCommit      # also install the git pre-commit hooks
#
# Steps:
#   1. uv sync              .venv with gdlint, gdformat, pytest, ruff
#   2. Godot                found, or installed by tools/install_godot.ps1 (-InstallGodot)
#   3. headless import      .godot/ with the imported assets and the class cache
#   4. pre-commit install   with -PreCommit, inside a git repository
#   5. tools/doctor.ps1     the verdict
#
# Exit codes: 0 = ready, 1 = a step failed or doctor found a problem. On a
# PowerShell the scripts cannot run on (Get-KitPowerShellProblem) no step runs:
# one line says why and names the fix.
#
# No "#Requires -Version 7" on purpose: started from Windows PowerShell 5.1 this
# script says to use pwsh in one line (the stock refusal is five and names no fix).

[CmdletBinding()]
param(
    [switch]$InstallGodot,
    [switch]$PreCommit
)

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Host ("setup: needs PowerShell 7, this is $($PSVersionTable.PSVersion). " +
        "Run it with pwsh (winget install Microsoft.PowerShell).")
    exit 1
}

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "godot_resolver.ps1")
. (Join-Path $PSScriptRoot "class_cache.ps1")

# Before anything is installed: a PowerShell that cannot compute a checksum
# aborts in the middle of the Godot install.
$refusal = Get-KitPowerShellRefusal
if ($refusal) {
    Write-Host "setup: $refusal" -ForegroundColor Red
    exit 1
}

$projectRoot = Get-KitRoot
try {
    [void](Get-KitConfig)
} catch {
    # Every step below reads kit.json; without it they would fail one by one.
    Write-Host "setup: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
$pwsh = (Get-Process -Id $PID).Path
$problems = @()

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Invoke-InProject {
    # Runs a command from the project root, output straight to the console
    # (Out-Host: otherwise it would become part of this function's result).
    param([string]$FilePath, [string[]]$Arguments)
    Push-Location -LiteralPath $projectRoot
    try {
        & $FilePath @Arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

# --- 1. Python tools ---
Write-Step "uv sync"
$uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $uvCommand) {
    Write-Host "uv not found. Fix: $(Get-KitInstallHint uv)   Then run setup again." -ForegroundColor Red
    $problems += "uv is not installed"
} else {
    $code = Invoke-InProject $uvCommand.Source @("sync")
    if ($code -ne 0) { $problems += "uv sync failed (exit $code)" }
}

# --- 2. Godot ---
Write-Step "Godot"
$godot = ""
try {
    $godot = Resolve-GodotPath
} catch {
    $notFound = $_.Exception.Message
    if ($InstallGodot) {
        $code = Invoke-InProject $pwsh @("-NoProfile", "-NonInteractive", "-File", (Join-Path $PSScriptRoot "install_godot.ps1"))
        if ($code -eq 0) {
            try {
                $godot = Resolve-GodotPath
            } catch {
                Write-Host $_.Exception.Message -ForegroundColor Red
                $problems += "Godot was installed but does not resolve"
            }
        } else {
            $problems += "tools/install_godot.ps1 failed (exit $code)"
        }
    } else {
        Write-Host $notFound -ForegroundColor Red
        Write-Host "Run 'pwsh ./tools/setup.ps1 -InstallGodot' to download it into .tools/godot/." -ForegroundColor Yellow
        $problems += "Godot not found"
    }
}
if ($godot) { Write-Host "Godot: $godot" }

# --- 3. First import ---
Write-Step "Import"
if (-not $godot) {
    Write-Host "Skipped: needs Godot." -ForegroundColor Yellow
} elseif (Test-GodotClassCacheStale -ProjectRoot $projectRoot) {
    try {
        Update-GodotClassCache -Godot $godot -ProjectRoot $projectRoot
        Write-Host "Imported."
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        $problems += "the first import failed"
    }
} else {
    Write-Host "Already imported; the class cache is fresh."
}

# --- 4. pre-commit hooks ---
if ($PreCommit) {
    Write-Step "pre-commit install"
    $insideRepository = $false
    if (Get-Command git -CommandType Application -ErrorAction SilentlyContinue) {
        $answer = & git -C $projectRoot rev-parse --is-inside-work-tree 2>$null
        $insideRepository = $LASTEXITCODE -eq 0 -and "$answer".Trim() -eq "true"
    }
    if (-not $insideRepository) {
        Write-Host "Skipped: '$projectRoot' is not inside a git repository (run git init first)." -ForegroundColor Yellow
    } elseif (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".pre-commit-config.yaml") -PathType Leaf)) {
        Write-Host "Skipped: no .pre-commit-config.yaml in the project." -ForegroundColor Yellow
    } elseif (-not $uvCommand) {
        Write-Host "Skipped: needs uv." -ForegroundColor Yellow
    } else {
        $code = Invoke-InProject $uvCommand.Source @("run", "pre-commit", "install")
        if ($code -ne 0) { $problems += "pre-commit install failed (exit $code)" }
    }
}

# --- 5. Verdict ---
Write-Step "doctor"
& (Join-Path $PSScriptRoot "doctor.ps1")
$doctorCode = $LASTEXITCODE

Write-Host ""
if ($problems.Count -gt 0) {
    Write-Host "setup: FAILED:" -ForegroundColor Red
    foreach ($problem in $problems) { Write-Host "  - $problem" -ForegroundColor Red }
    exit 1
}
if ($doctorCode -ne 0) {
    Write-Host "setup: doctor reports problems (above)." -ForegroundColor Red
    exit 1
}
Write-Host "setup: ready. Next: pwsh ./tools/check.ps1" -ForegroundColor Green
exit 0
