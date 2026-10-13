# Lints the gdUnit4 suites under the test root (kit.json tests.root, default
# "test").
#
# Usage:
#   pwsh ./tools/lint_tests.ps1
#
# Which script is a suite is asked of tools/ci/suites.py, the rule the test
# runner (tools/test_shards.py) uses: a script that reaches GdUnitTestSuite
# through what it extends, whatever its file name. So this lint and the runner
# cannot disagree, and a base class may live anywhere in the project.
#
# Errors:
#   - a script with test_ functions that extends no suite (the runner fails on
#     it too)
#   - `while true:` in a suite (a test must not be able to hang the run)
#   - a gdUnit4 suite outside the test root that no suite under it extends: no
#     runner would ever run it
# Warnings:
#   - a suite with no test_ function, its own or inherited
#   - a test_ function with no assertion. A call to assert_*( , verify*( or
#     fail( counts, so name a shared assertion helper assert_<something>.
#
# Comments and the insides of strings are never read as code: a commented-out
# assertion asserts nothing, and a "#" or a bracket in a string changes nothing.
# Wall-clock waits in tests are a check of their own: tools/check_test_wallclock.py.
#
# Exit codes:
#   0 = clean
#   1 = warnings only
#   2 = errors, no suite found (a broken layout must not pass), uv or
#       tools/ci/suites.py missing, or a kit.json that cannot be used
#Requires -Version 7.0

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "godot_resolver.ps1")
try {
    $config = Get-KitConfig
} catch {
    Write-Host "Test lint: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}
$projectRoot = $config.Root
$testRootName = $config.TestRoot
$script:exitCode = 0
$script:warnings = @()
$script:errors = @()

function Add-Warning($file, $line, $message) {
    $script:warnings += [PSCustomObject]@{ File = $file; Line = $line; Message = $message }
    if ($script:exitCode -lt 1) { $script:exitCode = 1 }
}

function Add-Error($file, $line, $message) {
    $script:errors += [PSCustomObject]@{ File = $file; Line = $line; Message = $message }
    $script:exitCode = 2
}

function Get-LineIndent([string]$Line) {
    return $Line.Length - $Line.TrimStart().Length
}

function Get-BracketDelta([string]$Code) {
    return ([regex]::Matches($Code, '[(\[{]')).Count - ([regex]::Matches($Code, '[)\]}]')).Count
}

# One pass, leftmost match first: a long string, a short string, or a comment.
# Whichever starts first owns its text, so a "#" in a string is not a comment
# and a quote in a comment opens no string.
$script:literalPattern = [regex]::new(
    '(?s)"""(?:\\.|[^\\])*?"""|''''''(?:\\.|[^\\])*?''''''' +
    '|"(?:\\.|[^"\\\r\n])*"|''(?:\\.|[^''\\\r\n])*''' +
    '|(?<comment>#[^\r\n]*)'
)

function ConvertTo-CodeOnly {
    # The script with every comment removed and every string emptied, line for
    # line (a string that spans lines keeps its line breaks).
    param([string]$Text)
    $evaluator = [System.Text.RegularExpressions.MatchEvaluator] {
        param($match)
        if ($match.Groups["comment"].Success) { return "" }
        $breaks = $match.Value.Length - $match.Value.Replace("`n", "").Length
        return '""' + ("`n" * $breaks)
    }
    return $script:literalPattern.Replace($Text, $evaluator)
}

function Get-TestFunctions {
    # Each test_ function of a script: name, line number, body text. -Lines is
    # code only (ConvertTo-CodeOnly). The body runs to the next line that is
    # indented no deeper than `func`, outside every bracket and not a backslash
    # continuation.
    param([string[]]$Lines)
    $index = 0
    while ($index -lt $Lines.Count) {
        if ($Lines[$index] -notmatch '^(\s*)(?:static\s+)?func\s+(test_\w+)\s*\(') {
            $index++
            continue
        }
        $name = $Matches[2]
        $indent = $Matches[1].Length
        $start = $index
        $body = New-Object System.Collections.Generic.List[string]
        # The signature may span lines; it ends where its brackets close. What
        # follows the colon on that line is the body of a one-line test.
        $depth = 0
        $closed = $false
        while (-not $closed -and $index -lt $Lines.Count) {
            $line = $Lines[$index]
            for ($column = 0; $column -lt $line.Length -and -not $closed; $column++) {
                $char = $line[$column]
                if ('([{'.Contains($char)) {
                    $depth++
                } elseif (')]}'.Contains($char)) {
                    $depth--
                    $closed = $depth -eq 0
                }
            }
            if ($closed -and $line.Substring($column) -match '^\s*(?:->[^:]*)?:(.*)$') {
                $body.Add($Matches[1])
            }
            $index++
        }
        $depth = 0
        $continued = $false
        while ($index -lt $Lines.Count) {
            $next = $Lines[$index]
            if ($next.Trim() -ne "" -and $depth -le 0 -and -not $continued -and
                (Get-LineIndent $next) -le $indent) {
                break
            }
            $body.Add($next)
            $depth += Get-BracketDelta $next
            $continued = $next.TrimEnd().EndsWith('\')
            $index++
        }
        [PSCustomObject]@{ Name = $name; Line = $start + 1; Body = ($body -join "`n") }
    }
}

function Invoke-ProjectPython {
    # `uv run python <script>` with the project as uv's project, whatever the
    # caller's working directory is: uv would otherwise look for a pyproject.toml
    # (and create a .venv) where the caller happens to stand.
    param([string]$Script)
    return Invoke-KitProcess -FilePath $script:uv -WorkingDirectory $projectRoot `
        -Arguments @("run", "--project", $projectRoot, "python", $Script) -TimeoutSeconds 300
}

Write-Host "Linting gdUnit4 suites under $testRootName/ ..." -ForegroundColor Cyan

$uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$suiteRules = Join-Path $PSScriptRoot "ci/suites.py"
if (-not (Test-Path -LiteralPath $suiteRules -PathType Leaf)) {
    Write-Host "Test lint: tools/ci/suites.py is missing (it says which scripts are suites); restore tools/ci from the kit." `
        -ForegroundColor Red
    exit 2
}
if (-not $uvCommand) {
    Write-Host "Test lint: uv not found (it runs tools/ci/suites.py, which says which scripts are suites): https://docs.astral.sh/uv/" `
        -ForegroundColor Red
    exit 2
}
$script:uv = $uvCommand.Source

$listing = Invoke-ProjectPython $suiteRules
$scripts = $null
if ($listing.Code -eq 0) {
    try {
        $scripts = @(($listing.Stdout | ConvertFrom-Json).scripts)
    } catch {
        $scripts = $null
    }
}
if ($null -eq $scripts) {
    Write-Host "Test lint: tools/ci/suites.py failed (exit $($listing.Code)):" -ForegroundColor Red
    Write-Host $listing.Text -ForegroundColor DarkRed
    exit 2
}

$suites = @($scripts | Where-Object { $_.kind -eq "suite" -or $_.kind -eq "base" })
$strays = @($scripts | Where-Object { $_.kind -eq "not_a_suite" })
# The runners start gdUnit4 on the test root alone: a suite anywhere else never
# runs, and the run would be green without it.
$outside = @(($listing.Stdout | ConvertFrom-Json).outside)
$outsideProblem = "a gdUnit4 suite outside tests.root '$testRootName' never runs: move it there, or set tests.root in kit.json"
if ($suites.Count + $strays.Count -eq 0) {
    foreach ($lost in $outside) { Write-Host "${lost}: $outsideProblem" -ForegroundColor Red }
    Write-Host "No gdUnit4 suites found under $testRootName/ (kit.json tests.root): a suite is a script that extends GdUnitTestSuite." `
        -ForegroundColor Red
    exit 2
}
foreach ($lost in $outside) { Add-Error $lost 1 $outsideProblem }

foreach ($stray in $strays) {
    Add-Error $stray.path 1 "has test functions, so it must 'extends GdUnitTestSuite' (it has: $($stray.extends))"
}

# A call through self, or on a helper object, is still that call.
$assertionPattern = '(?<!\w)_?assert_\w+\s*\(|(?<![\w.])verify\w*\s*\(|(?<![\w.])fail\s*\('
foreach ($suite in $suites) {
    $relative = $suite.path
    $text = [System.IO.File]::ReadAllText((Join-Path $projectRoot $relative)).Replace("`r`n", "`n")
    $lines = @((ConvertTo-CodeOnly $text) -split "`n")

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '\bwhile\s*\(?\s*true\s*\)?\s*:') {
            Add-Error $relative ($i + 1) "'while true:' can hang the run; bound the loop"
        }
    }

    if ($suite.kind -eq "suite" -and @($suite.tests).Count -eq 0 -and -not $suite.inherits_tests) {
        Add-Warning $relative 1 "no test functions (names must start with 'test_')"
    }
    foreach ($test in @(Get-TestFunctions -Lines $lines)) {
        $calls = $test.Body -replace '\bself\s*\.\s*', ''
        if ($calls -notmatch $assertionPattern) {
            Add-Warning $relative $test.Line "test '$($test.Name)' has no assertion"
        }
    }
}

Write-Host ""
if ($script:errors.Count -gt 0) {
    Write-Host "ERRORS ($($script:errors.Count)):" -ForegroundColor Red
    foreach ($err in $script:errors) {
        Write-Host "  $($err.File):$($err.Line): $($err.Message)" -ForegroundColor Red
    }
    Write-Host ""
}

if ($script:warnings.Count -gt 0) {
    Write-Host "WARNINGS ($($script:warnings.Count)):" -ForegroundColor Yellow
    foreach ($warn in $script:warnings) {
        Write-Host "  $($warn.File):$($warn.Line): $($warn.Message)" -ForegroundColor Yellow
    }
    Write-Host ""
}

if ($script:exitCode -eq 0) {
    Write-Host "Test lint: CLEAN ($($suites.Count) file(s))" -ForegroundColor Green
} else {
    Write-Host "Test lint found issues." -ForegroundColor $(if ($script:exitCode -eq 2) { "Red" } else { "Yellow" })
}

exit $script:exitCode
