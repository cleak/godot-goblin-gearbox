# Every gate, one command. Run it before you call a change done.
#
# Usage:
#   pwsh ./tools/check.ps1                   # every gate, then every test suite
#   pwsh ./tools/check.ps1 -SkipTests        # the gates without the tests
#   pwsh ./tools/check.ps1 -Changed          # the quick run: see below
#   pwsh ./tools/check.ps1 -Fix              # gdformat rewrites files, then everything runs
#   pwsh ./tools/check.ps1 -Path scripts/x.gd   # narrow the lint and type check to a path
#
# Gates, in this order. A failure never stops the run:
#   ascii          tools/test_scripts_ascii.ps1   PowerShell scripts are BOM-less ASCII
#   gdlint         tools/lint_gdscript.ps1        gdformat + gdlint
#   typecheck      tools/typecheck.ps1            import, then the engine's strict warnings
#   project-lint   tools/lint_project.gd          UIDs and scene references
#   shader-lint    tools/lint_shaders.gd          shaders parse and type-check (no GPU compile)
#   validate-all   tools/validate_all.gd          every script, scene and resource loads
#   scenes         godot --headless -- --devtools-validate   every scene instantiates: its
#                  scripts compile and attach, its signal connections hold. Needs the
#                  DevTools addon, its autoload and a main scene; starts the project
#                  for a moment.
#   test-lint      tools/lint_tests.ps1           gdUnit4 suites are well-formed
#   ruff           uv run ruff check tools        in a project the kit was installed
#                  into: only its own Python files under tools/, skipped with none
#   pytest         uv run pytest tools/tests      the kit's own tool tests, on up to four
#                  workers (-n <workers> --dist loadgroup)
#   architecture   tools/check_architecture.py
#   typed-ternary  tools/check_typed_ternaries.py
#   wall-clock     tools/check_test_wallclock.py
#   agents-md      tools/check_agents_md.py       AGENTS.md is well-formed
#   tests          tools/test.ps1, or `uv run python tools/test_shards.py` with -Changed
#
# -Changed is the run for while you work. It asks git what differs from the base
# branch (the rule of tools/test_shards.py: the remote's default branch, else
# origin/main, main, origin/master, master; committed, uncommitted and untracked
# files all count) and
#   - runs only the gdUnit4 suites that change can affect (none: "tests" is
#     skipped, since nothing was tested);
#   - skips pytest, the slowest gate by far, unless the change touches tools/,
#     addons/agent_devtools/, kit.json, pyproject.toml or uv.lock (when it runs,
#     one line names the file that made it).
# Outside a git repository pytest runs. Without -Changed every gate always runs.
# On the base branch itself (a main with no remote copy) what is committed
# cannot count as changed: the last lines of the run say so.
#
# No gate waits for ever: -GateTimeoutSeconds (900) is the longest one gate's
# process may run, each Godot that tools/typecheck.ps1 starts included. Past it the
# gate is killed with everything it started and its row says "timed out after
# <n> s"; 0 means no limit. The tests keep their own timeout (tools/test.ps1: 300 s,
# -TestTimeoutSeconds changes it). A project without a main scene skips "scenes":
# Godot would say so in a dialog and wait for a click, with --headless too.
#
# A gate whose tool is not in the project is reported as "skipped" with the
# reason. Only a failing gate's output is printed, each Godot error once and
# without its GDScript backtrace; every gate's full output is kept in
# artifacts/check/<gate>.log. The summary table comes last. A headless
# .gd tool that exits 3 broke itself (TOOL_ERROR): the table says so, since its
# verdict then says nothing about the project.
#
# The Godot gates (project-lint, shader-lint, validate-all, scenes) pass on the
# tool's own verdict line, never on exit 0 alone: the project's autoloads run in
# those processes too, and one that calls quit() ends the run with its own exit
# code. A tool script that does not compile is reported at once and not started
# (Godot would idle until the timeout).
#
# A project with a pyproject.toml and no .venv yet gets one `uv sync` first:
# the lint gate needs gdtoolkit from it.
#
# Exit codes: 0 = no gate failed, 1 = a gate failed, 2 = it could not run as
# asked: a bad -Path, an unreadable kit.json, or a PowerShell older than 7. An
# unknown switch is PowerShell's own error: exit 1, nothing runs.
#
# No "#Requires -Version 7" on purpose: started from Windows PowerShell 5.1 this
# script says to use pwsh in one line (the stock refusal is five and names no fix).

[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$SkipTests,
    [switch]$Changed,
    [string]$Path = "",
    # Longest one gate may run (the tests keep their own timeout).
    [int]$GateTimeoutSeconds = 900,
    # Passed to tools/test.ps1 when above 0.
    [int]$TestTimeoutSeconds = 0
)

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Host ("check: needs PowerShell 7, this is $($PSVersionTable.PSVersion). " +
        "Run it with pwsh (winget install Microsoft.PowerShell).")
    exit 2
}

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "godot_resolver.ps1")

try {
    $config = Get-KitConfig
} catch {
    Write-Host "check: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}
$projectRoot = $config.Root
$pwsh = (Get-Process -Id $PID).Path
$uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$uv = if ($uvCommand) { $uvCommand.Source } else { "" }
# What a Godot script prints when it did not run as written, whatever it exits with.
$godotFatalPattern = 'SCRIPT ERROR|Parse Error|Failed to load script'
$maxOutputLines = 200

if ($Path) {
    try {
        [void](Get-KitSourceFiles -Path $Path)
    } catch {
        Write-Host "check: -Path: $($_.Exception.Message)" -ForegroundColor Red
        exit 2
    }
}

$artifacts = Join-Path $projectRoot "artifacts"
$logDirectory = Join-Path $artifacts "check"
[System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null
# Keeps Godot from importing anything under artifacts/.
$gdignore = Join-Path $artifacts ".gdignore"
if (-not (Test-Path -LiteralPath $gdignore)) { [System.IO.File]::WriteAllText($gdignore, "") }
Get-ChildItem -LiteralPath $logDirectory -Filter "*.log" -File | Remove-Item -Force

$godot = ""
$godotProblem = ""
try {
    $godot = Resolve-GodotPath
    # Every gate uses this binary, and none searches for it again.
    $env:GODOT_BIN = $godot
} catch {
    $godotProblem = $_.Exception.Message
}

$results = New-Object System.Collections.Generic.List[object]

function Get-GateFindings {
    <#
    A failed gate's output without what only repeats: Godot prints a GDScript
    backtrace under every script error, and one script that does not compile is
    reported again by every script and scene that loads it. Each error (its line
    and the "at:" line under it) is kept once; the log file keeps everything.
    #>
    param([string]$Output)
    $kept = New-Object System.Collections.Generic.List[string]
    $seen = New-Object System.Collections.Generic.HashSet[string]
    $lines = @($Output -split "`r?`n")
    $inBacktrace = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^\s*GDScript backtrace') { $inBacktrace = $true; continue }
        if ($inBacktrace -and $line -match '^\s*\[\d+\]\s') { continue }
        $inBacktrace = $false
        if ($line -match '^\s*(SCRIPT )?ERROR:' -and $i + 1 -lt $lines.Count -and $lines[$i + 1] -match '^\s+at:') {
            if (-not $seen.Add($line.Trim() + "`n" + $lines[$i + 1].Trim())) { $i++; continue }
        }
        $kept.Add($line)
    }
    return , $kept.ToArray()
}

function Write-GateOutput {
    param([string]$Name, [string]$Detail, [string]$Output)
    $lines = Get-GateFindings $Output
    Write-Host "----- $Name`: $Detail (full output: artifacts/check/$Name.log) -----" -ForegroundColor Red
    if ($lines.Count -le $maxOutputLines) {
        Write-Host ($lines -join "`n")
    } else {
        $head = 80
        $tail = $maxOutputLines - $head
        Write-Host (($lines | Select-Object -First $head) -join "`n")
        Write-Host "... $($lines.Count - $maxOutputLines) line(s) left out ..." -ForegroundColor DarkGray
        Write-Host (($lines | Select-Object -Last $tail) -join "`n")
    }
    Write-Host "----- end $Name -----" -ForegroundColor Red
}

function Add-GateResult {
    param(
        [string]$Name,
        [ValidateSet("pass", "FAIL", "skipped")][string]$Status,
        [string]$Detail = "",
        [string]$Output = "",
        [double]$Seconds = -1
    )
    $results.Add([PSCustomObject]@{ Gate = $Name; Result = $Status; Seconds = $Seconds; Detail = $Detail })
    $took = if ($Seconds -ge 0) { " ({0:0.0}s)" -f $Seconds } else { "" }
    $color = switch ($Status) { "pass" { "Green" } "FAIL" { "Red" } default { "DarkGray" } }
    $suffix = if ($Status -eq "skipped" -and $Detail) { ": $Detail" } else { "" }
    Write-Host "$Name ... $Status$took$suffix" -ForegroundColor $color
    if ($Output.Trim()) {
        [System.IO.File]::WriteAllText((Join-Path $logDirectory "$Name.log"), $Output)
        if ($Status -eq "FAIL") { Write-GateOutput -Name $Name -Detail $Detail -Output $Output }
    }
}

function Get-LastLine {
    param([string]$Text)
    $lines = @($Text -split "`r?`n" | Where-Object { $_.Trim() })
    if ($lines.Count -eq 0) { return "" }
    return $lines[-1].Trim()
}

function Invoke-Gate {
    <#
    Runs one gate as a child process and records the result. It passes when the
    process exits 0 in time, its output holds no -FatalPattern match and, with
    -VerdictPattern, holds the line that tool prints when it has checked
    everything. -DetailPattern picks the summary line shown in the table; the
    default is the verdict line, else the last line of output.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = $GateTimeoutSeconds,
        [string]$FatalPattern = "",
        # Exit 0 alone proves nothing for a Godot run: the project's own code runs
        # in it too (autoloads, the main scene), and a quit() there ends the run
        # with that exit code, before or after the tool's.
        [string]$VerdictPattern = "",
        # The verdict line as the table names it when it is missing.
        [string]$VerdictName = "",
        [string]$DetailPattern = "",
        # The line of a failed run worth showing in the table (the last match).
        [string]$FailDetailPattern = "",
        [hashtable]$ExitDetails = @{},
        # The tool ends with a one-line verdict worth showing in the table.
        [switch]$LastLineOnFail,
        # What a clean run prints when it had nothing to check: that run is
        # "skipped" with -NothingDetail, never "pass".
        [string]$NothingPattern = "",
        [string]$NothingDetail = "",
        # Variables the gate gets on top of this process's own.
        [System.Collections.IDictionary]$Environment = @{}
    )
    try {
        $run = Invoke-KitProcess -FilePath $FilePath -Arguments $Arguments `
            -WorkingDirectory $projectRoot -TimeoutSeconds $TimeoutSeconds -Environment $Environment
    } catch {
        Add-GateResult -Name $Name -Status "FAIL" -Detail "could not start" -Output $_.Exception.Message
        return
    }
    # Colour codes (gdUnit4 prints them) are noise in a log.
    $output = $run.Text -replace '\x1b\[[0-9;]*[A-Za-z]', ''
    # A tool's verdict is the end of its stdout; stderr is progress and noise
    # (uv reports what it installed there).
    $lastLine = Get-LastLine ($run.Stdout -replace '\x1b\[[0-9;]*[A-Za-z]', '')
    if (-not $lastLine) { $lastLine = Get-LastLine $output }
    $fatal = if ($FatalPattern) { [regex]::Match($output, $FatalPattern) } else { $null }
    if ($run.TimedOut) {
        $status = "FAIL"
        $detail = "timed out after $TimeoutSeconds s"
    } elseif ($run.Code -ne 0) {
        $status = "FAIL"
        $detail = "exit $($run.Code)"
        $said = if ($FailDetailPattern) { [regex]::Matches($output, $FailDetailPattern) } else { @() }
        if ($ExitDetails.ContainsKey($run.Code)) {
            $detail += ": " + $ExitDetails[$run.Code]
        } elseif ($said.Count -gt 0) {
            $detail += ": " + $said[$said.Count - 1].Value.Trim()
        } elseif ($LastLineOnFail) {
            $detail += ": $lastLine"
        }
    } elseif ($fatal -and $fatal.Success) {
        $status = "FAIL"
        $detail = "exit 0, but '$($fatal.Value.Trim())' in the output"
    } elseif ($VerdictPattern -and $output -notmatch $VerdictPattern) {
        $status = "FAIL"
        $detail = "exit 0, but no '$VerdictName' line: Godot quit before the verdict (an autoload?)"
    } elseif ($NothingPattern -and $output -match $NothingPattern) {
        $status = "skipped"
        $detail = $NothingDetail
    } else {
        $status = "pass"
        $detail = ""
        foreach ($pattern in @($DetailPattern, $VerdictPattern)) {
            if ($detail -or -not $pattern) { continue }
            $found = [regex]::Matches($output, $pattern)
            if ($found.Count -gt 0) { $detail = $found[$found.Count - 1].Value.Trim() }
        }
        if (-not $detail) { $detail = $lastLine }
    }
    Add-GateResult -Name $Name -Status $status -Detail $detail -Output $output -Seconds $run.Seconds
}

function Invoke-ScriptGate {
    # A kit .ps1 gate, in its own PowerShell process.
    param([string]$Name, [string]$Script, [string[]]$Arguments = @(), [int]$TimeoutSeconds = $GateTimeoutSeconds,
        [string]$DetailPattern = "", [hashtable]$ExitDetails = @{})
    $file = Join-Path $PSScriptRoot $Script
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        Add-GateResult -Name $Name -Status "skipped" -Detail "tools/$Script not present"
        return
    }
    # A gate's PowerShell leaves nothing in the temp folder when its timeout kills it,
    # and never writes the user's PowerShell startup profile.
    Invoke-Gate -Name $Name -FilePath $pwsh `
        -Arguments (@("-NoProfile", "-NonInteractive", "-File", $file) + $Arguments) `
        -TimeoutSeconds $TimeoutSeconds -DetailPattern $DetailPattern -ExitDetails $ExitDetails `
        -LastLineOnFail -Environment (Get-KitQuietPowerShellEnvironment)
}

function Invoke-GodotGate {
    # A headless Godot tool script (tools/<name>.gd) whose last line is
    # <Verdict>_OK ... or <Verdict>_FAILED ...
    param([string]$Name, [string]$Script, [Parameter(Mandatory = $true)][string]$Verdict)
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $Script) -PathType Leaf)) {
        Add-GateResult -Name $Name -Status "skipped" -Detail "tools/$Script not present"
        return
    }
    if (-not $godot) {
        # The reason is printed once, at the top of the run.
        Add-GateResult -Name $Name -Status "FAIL" -Detail "Godot not found"
        return
    }
    # A tool that does not compile is not started: see Get-KitGodotScriptProblem.
    $asking = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $problem = Get-KitGodotScriptProblem -Godot $godot -Script "res://tools/$Script" -ProjectRoot $projectRoot
    } catch {
        $problem = "could not start Godot: $($_.Exception.Message)"
    }
    if ($problem) {
        Add-GateResult -Name $Name -Status "FAIL" -Output $problem -Seconds $asking.Elapsed.TotalSeconds `
            -Detail "tools/$Script (or a script it is built on) does not compile: it was not run"
        return
    }
    # The tools built on tools/headless_tool.gd: 1 problems found, 2 cannot run as
    # asked, 3 the tool's own code raised a script error.
    Invoke-Gate -Name $Name -FilePath $godot `
        -Arguments @("--headless", "--script", "res://tools/$Script") `
        -FatalPattern "$godotFatalPattern|\b${Verdict}_FAILED\b[^\r\n]*" `
        -VerdictPattern "(?m)^${Verdict}_OK\b[^\r\n]*" -VerdictName "${Verdict}_OK" -LastLineOnFail `
        -ExitDetails @{
            2 = "the tool could not run: bad arguments, or a kit.json it cannot use"
            3 = "the tool itself broke (TOOL_ERROR): its verdict says nothing about the project"
        }
}

function Get-KitChange {
    <#
    What differs from the base branch, asked of tools/ci/select_suites.py (the
    rule tools/test_shards.py uses). Files is $null when nobody can say: no git
    repository, no base branch, or the question could not be asked; Why says
    which. Note is what the list leaves out, for the user.
    #>
    $answer = [PSCustomObject]@{ Files = $null; Base = ""; Why = ""; Note = "" }
    $script = Join-Path $PSScriptRoot "ci/select_suites.py"
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
        $answer.Why = "tools/ci/select_suites.py not present"
        return $answer
    }
    if (-not $uv) {
        $answer.Why = "uv not found"
        return $answer
    }
    try {
        $asked = Invoke-KitProcess -FilePath $uv -WorkingDirectory $projectRoot -TimeoutSeconds 300 `
            -Arguments @("run", "--project", $projectRoot, "python", $script, "--changed-files")
        if ($asked.Code -ne 0) { throw "exit $($asked.Code): $($asked.Text)" }
        $said = $asked.Stdout | ConvertFrom-Json
    } catch {
        $answer.Why = "could not ask git: $($_.Exception.Message)"
        return $answer
    }
    $answer.Base = [string]$said.base
    $answer.Why = [string]$said.why
    $answer.Note = [string]$said.note
    if ($null -ne $said.files) { $answer.Files = @($said.files) }
    return $answer
}

# A change to one of these can break the kit's own tool tests (pytest tools/tests).
$kitFolders = @("tools/", "addons/agent_devtools/")
$kitFiles = @("kit.json", "pyproject.toml", "uv.lock")

function Get-KitFileChanged {
    # The first changed path that belongs to the kit's tools, or "".
    param([string[]]$Files = @())
    $comparison = Get-KitPathComparison
    foreach ($file in $Files) {
        foreach ($name in $kitFiles) {
            if ($file.Equals($name, $comparison)) { return $file }
        }
        foreach ($folder in $kitFolders) {
            if ($file.StartsWith($folder, $comparison)) { return $file }
        }
    }
    return ""
}

function Invoke-PythonGate {
    # `uv run <arguments>` from the project root; -Requires is a project-relative
    # path that must exist for the gate to apply.
    param([string]$Name, [string[]]$Arguments, [string]$Requires = "", [int]$TimeoutSeconds = $GateTimeoutSeconds,
        [string]$DetailPattern = "", [hashtable]$ExitDetails = @{})
    if ($Requires -and -not (Test-Path -LiteralPath (Join-Path $projectRoot $Requires))) {
        Add-GateResult -Name $Name -Status "skipped" -Detail "$Requires not present"
        return
    }
    if (-not $uv) {
        Add-GateResult -Name $Name -Status "skipped" -Detail "uv not found (https://docs.astral.sh/uv/)"
        return
    }
    Invoke-Gate -Name $Name -FilePath $uv -Arguments (@("run") + $Arguments) `
        -TimeoutSeconds $TimeoutSeconds -DetailPattern $DetailPattern -ExitDetails $ExitDetails `
        -LastLineOnFail
}

$started = [System.Diagnostics.Stopwatch]::StartNew()
Write-Host "check: $projectRoot" -ForegroundColor Cyan
if ($godot) {
    Write-Host "Godot: $godot" -ForegroundColor DarkGray
} else {
    Write-Host $godotProblem -ForegroundColor Red
}

# The lint gate takes gdlint and gdformat from .venv, and the first `uv run`
# further down would create that .venv anyway, one gate too late.
if ($uv -and (Test-Path -LiteralPath (Join-Path $projectRoot "pyproject.toml") -PathType Leaf) -and
    -not (Test-Path -LiteralPath (Join-Path $projectRoot ".venv") -PathType Container)) {
    Write-Host "No .venv yet: uv sync ..." -ForegroundColor DarkGray
    try {
        $sync = Invoke-KitProcess -FilePath $uv -Arguments @("sync") -WorkingDirectory $projectRoot `
            -TimeoutSeconds $GateTimeoutSeconds
        $syncProblem = if ($sync.Code -ne 0) { "exit $($sync.Code)`n$($sync.Text)" } else { "" }
    } catch {
        $syncProblem = $_.Exception.Message
    }
    if ($syncProblem) {
        # Not a gate of its own: the gates that need the .venv fail and say so.
        Write-Host "uv sync failed: $syncProblem" -ForegroundColor Red
    }
}

[string[]]$narrow = @()
if ($Path) { $narrow = @("-Path", $Path) }

Invoke-ScriptGate -Name "ascii" -Script "test_scripts_ascii.ps1"

[string[]]$lintArgs = $narrow
if ($Fix) { $lintArgs = @("-Fix") + $narrow }
Invoke-ScriptGate -Name "gdlint" -Script "lint_gdscript.ps1" -Arguments $lintArgs `
    -ExitDetails @{ 2 = "gdtoolkit missing (uv sync)" }

# typecheck.ps1 imports the project first, which the Godot gates after it rely on.
Invoke-ScriptGate -Name "typecheck" -Script "typecheck.ps1" -TimeoutSeconds 0 `
    -Arguments (@("-TimeoutSeconds", "$GateTimeoutSeconds") + $narrow) `
    -ExitDetails @{
        2 = "Godot not found"
        3 = "the checker itself broke (TOOL_ERROR in tools/typecheck.gd): no file was judged"
    }

Invoke-GodotGate -Name "project-lint" -Script "lint_project.gd" -Verdict "LINT_PROJECT"

if (@(Get-KitSourceFiles -Extensions @(".gdshader")).Count -eq 0) {
    Add-GateResult -Name "shader-lint" -Status "skipped" -Detail "no .gdshader files"
} else {
    Invoke-GodotGate -Name "shader-lint" -Script "lint_shaders.gd" -Verdict "LINT_SHADERS"
}

Invoke-GodotGate -Name "validate-all" -Script "validate_all.gd" -Verdict "VALIDATE_ALL"

# validate-all loads every scene; this one instantiates them, in the project as it
# starts (autoloads and all), which is where a script that does not attach or a
# connection to a method that is gone shows up. DevTools does the work and quits.
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "addons/agent_devtools") -PathType Container)) {
    Add-GateResult -Name "scenes" -Status "skipped" -Detail "addons/agent_devtools not present"
} elseif (-not (Test-KitDevToolsAutoload)) {
    Add-GateResult -Name "scenes" -Status "skipped" -Detail "the DevTools autoload is not registered in project.godot"
} elseif (-not (Get-KitMainScene -ProjectRoot $projectRoot)) {
    # Nothing to start: Godot would say so in a dialog and wait for a click.
    Add-GateResult -Name "scenes" -Status "skipped" `
        -Detail "no main scene: project.godot sets no application/run/main_scene, so no game starts"
} elseif (-not $godot) {
    Add-GateResult -Name "scenes" -Status "FAIL" -Detail "Godot not found"
} else {
    # Its own DevTools mailbox, so this run takes no command meant for a game
    # you have running and clears none of its files.
    $sessionBefore = $env:GODOT_AGENT_SESSION
    $env:GODOT_AGENT_SESSION = "check"
    try {
        Invoke-Gate -Name "scenes" -FilePath $godot `
            -Arguments @("--headless", "--path", $projectRoot, "--", "--devtools-validate") `
            -FatalPattern $godotFatalPattern `
            -FailDetailPattern 'DevTools: validation failed[^\r\n]*|ERROR: Failed loading scene[^\r\n]*' `
            -VerdictPattern 'DevTools: Scenes valid[^\r\n]*' -VerdictName "DevTools: Scenes valid"
    } finally {
        $env:GODOT_AGENT_SESSION = $sessionBefore
    }
}

Invoke-ScriptGate -Name "test-lint" -Script "lint_tests.ps1"

# In a project the kit was installed into, the kit's own Python files are left out,
# as tools/lint_gdscript.ps1 leaves out its scripts: the project's ruff rules, which
# may be stricter than the kit's, are for the project's files.
$pythonTools = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.py" -File -Recurse |
        ForEach-Object { ConvertTo-KitRelativePath $_.FullName })
$manifestProblem = ""
$kitInstalled = [System.Collections.Generic.HashSet[string]]::new()
try {
    $kitInstalled = Get-KitInstalledFileSet
} catch {
    $manifestProblem = $_.Exception.Message
}
$ownPython = @($pythonTools | Where-Object { -not $kitInstalled.Contains($_) })
if ($pythonTools.Count -eq 0) {
    Add-GateResult -Name "ruff" -Status "skipped" -Detail "no Python files under tools/"
} elseif ($manifestProblem) {
    Add-GateResult -Name "ruff" -Status "FAIL" -Detail $manifestProblem
} elseif ($kitInstalled.Count -eq 0) {
    Invoke-PythonGate -Name "ruff" -Arguments @("ruff", "check", "tools")
} elseif ($ownPython.Count -eq 0) {
    Add-GateResult -Name "ruff" -Status "skipped" `
        -Detail "tools/ holds only the kit's Python files (tools/kit_manifest.json lists them)"
} else {
    Invoke-PythonGate -Name "ruff" -Arguments (@("ruff", "check") + $ownPython)
}

# pytest tests the kit's own tools: minutes of work that a change to the game
# cannot break. -Changed leaves it out unless the change touches the tools.
$pytestSkip = ""
$changeNote = ""
if ($Changed) {
    $change = Get-KitChange
    # For the end of the run: it qualifies the pytest row and the tests row.
    $changeNote = $change.Note
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "tools/tests"))) {
        # Skipped below, as in a full run: there is nothing to run.
    } elseif ($null -eq $change.Files) {
        Write-Host "pytest: -Changed cannot tell what changed ($($change.Why)); running it." -ForegroundColor DarkGray
    } elseif (-not (Get-KitFileChanged -Files $change.Files)) {
        $pytestSkip = "-Changed: kit files unchanged vs $($change.Base) (" +
            (($kitFolders + $kitFiles) -join ", ") + ")"
    } else {
        # The slowest gate by far: say what made it run.
        Write-Host ("pytest: -Changed: $(Get-KitFileChanged -Files $change.Files) differs from " +
            "$($change.Base); running it.") -ForegroundColor DarkGray
    }
}
if ($pytestSkip) {
    Add-GateResult -Name "pytest" -Status "skipped" -Detail $pytestSkip
} else {
    # In parallel (pytest-xdist): half the cores, four at most, since the machine is
    # shared. Most of these tests start PowerShell. --dist loadgroup puts the ones
    # that start a real Godot on one worker, one at a time: tools/tests/conftest.py.
    [string[]]$pytestArgs = @("pytest", "tools/tests")
    $pytestWorkers = [Math]::Min(4, [Math]::Floor([Environment]::ProcessorCount / 2))
    if ($pytestWorkers -ge 2) { $pytestArgs += @("-n", "$pytestWorkers", "--dist", "loadgroup") }
    Invoke-PythonGate -Name "pytest" -Arguments $pytestArgs -Requires "tools/tests" `
        -DetailPattern '(?m)^.*\b(passed|failed|error|no tests ran)\b.*$' `
        -ExitDetails @{ 4 = "pytest refused its arguments: is pytest-xdist installed? (uv sync)" }
}
# Without its manifest the layering check has no rule to hold anything to: it
# prints "not configured" and exits 0, which is nothing checked, not a pass.
$layers = "tools/ci/architecture_modules.json"
if ((Test-Path -LiteralPath (Join-Path $projectRoot "tools/check_architecture.py") -PathType Leaf) -and
    -not (Test-Path -LiteralPath (Join-Path $projectRoot $layers) -PathType Leaf)) {
    Add-GateResult -Name "architecture" -Status "skipped" -Detail "not configured: no $layers (docs/architecture.md)"
} else {
    Invoke-PythonGate -Name "architecture" -Requires "tools/check_architecture.py" `
        -Arguments @("python", "tools/check_architecture.py")
}
Invoke-PythonGate -Name "typed-ternary" -Requires "tools/check_typed_ternaries.py" `
    -Arguments @("python", "tools/check_typed_ternaries.py")
Invoke-PythonGate -Name "wall-clock" -Requires "tools/check_test_wallclock.py" `
    -Arguments @("python", "tools/check_test_wallclock.py")
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "AGENTS.md") -PathType Leaf)) {
    Add-GateResult -Name "agents-md" -Status "skipped" -Detail "AGENTS.md not present"
} else {
    Invoke-PythonGate -Name "agents-md" -Requires "tools/check_agents_md.py" `
        -Arguments @("python", "tools/check_agents_md.py") `
        -ExitDetails @{ 2 = "tools/check_agents_md.py could not run as asked (usage)" }
}

# The test tools carry their own timeouts.
if ($SkipTests) {
    Add-GateResult -Name "tests" -Status "skipped" -Detail "-SkipTests"
} elseif ($Changed) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "test_shards.py") -PathType Leaf)) {
        Add-GateResult -Name "tests" -Status "FAIL" -Detail "-Changed needs tools/test_shards.py"
    } elseif (-not $uv) {
        Add-GateResult -Name "tests" -Status "FAIL" -Detail "-Changed needs uv (https://docs.astral.sh/uv/)"
    } else {
        # The runner's summary line is the verdict of a pass and of a failure (its
        # last line is a list of slow suites). A change that selects no suite ran
        # no test: skipped, not passed.
        $ran = '(?m)^.*\d+ suites?, \d+ test cases.*$'
        Invoke-Gate -Name "tests" -FilePath $uv -Arguments @("run", "python", "tools/test_shards.py") `
            -TimeoutSeconds 0 -DetailPattern $ran -FailDetailPattern $ran -LastLineOnFail `
            -NothingPattern '(?m)^No suites to run' -NothingDetail "-Changed: the change selects no suite"
    }
} else {
    [string[]]$testArgs = @()
    if ($TestTimeoutSeconds -gt 0) { $testArgs = @("-TimeoutSeconds", "$TestTimeoutSeconds") }
    Invoke-ScriptGate -Name "tests" -Script "test.ps1" -Arguments $testArgs -TimeoutSeconds 0 `
        -ExitDetails @{ 2 = "Godot not found" }
}
$started.Stop()

Write-Host ""
$gateWidth = [Math]::Max(4, ($results | ForEach-Object { $_.Gate.Length } | Measure-Object -Maximum).Maximum)
$format = "{0,-$gateWidth}  {1,-7}  {2,7}  {3}"
Write-Host ($format -f "Gate", "Result", "Seconds", "Detail")
Write-Host ($format -f "----", "------", "-------", "------")
foreach ($result in $results) {
    $seconds = if ($result.Seconds -ge 0) { "{0:0.0}" -f $result.Seconds } else { "-" }
    $detail = $result.Detail
    if ($detail.Length -gt 90) { $detail = $detail.Substring(0, 87) + "..." }
    $color = switch ($result.Result) { "pass" { "Green" } "FAIL" { "Red" } default { "DarkGray" } }
    Write-Host ($format -f $result.Gate, $result.Result, $seconds, $detail) -ForegroundColor $color
}

$failed = @($results | Where-Object { $_.Result -eq "FAIL" })
$passed = @($results | Where-Object { $_.Result -eq "pass" })
$skippedGates = @($results | Where-Object { $_.Result -eq "skipped" })
$counts = "$($passed.Count) passed, $($failed.Count) failed, $($skippedGates.Count) skipped in " +
    ("{0:0.0}s" -f $started.Elapsed.TotalSeconds)
Write-Host ""
if ($changeNote) {
    Write-Host "-Changed: $changeNote. A full check.ps1 covers the rest." -ForegroundColor Yellow
}
if ($failed.Count -gt 0) {
    Write-Host "check: FAILED ($(($failed | ForEach-Object { $_.Gate }) -join ', ')): $counts" -ForegroundColor Red
    exit 1
}
Write-Host "check: PASSED: $counts" -ForegroundColor Green
exit 0
