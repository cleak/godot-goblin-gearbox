# Runs the gdUnit4 tests in one headless Godot process, with a timeout.
#
# Usage:
#   pwsh ./tools/test.ps1                                  # every suite under the test root
#   pwsh ./tools/test.ps1 -Test res://test/unit            # one folder
#   pwsh ./tools/test.ps1 -Test res://test/unit/test_x.gd  # one suite
#   pwsh ./tools/test.ps1 -FailFast                        # stop at the first failing test
#   pwsh ./tools/test.ps1 -TimeoutSeconds 900
#
# -Test takes a res:// path, a project-relative path or an absolute one inside
# the project (spelled through a directory link to the project too). The default
# test root is kit.json tests.root ("test"). Reports go to
# artifacts/gdunit-reports (gitignored, hidden from Godot by artifacts/.gdignore).
#
# Every test case runs, as with tools/test_shards.py: one failure does not hide
# the next. -FailFast gives gdUnit4's own default back (it stops at the first).
# The run gets an empty user:// of its own (a temp folder, removed afterwards):
# Godot alone is started with the variable that moves it, APPDATA on Windows,
# XDG_DATA_HOME on Linux, HOME on macOS (Get-KitUserDataEnvironment).
# Not so the import that comes first when the class cache is stale: it is an
# editor run and needs your editor settings (the path to Blender, for one), so
# it uses your own Godot folders, as opening the editor does.
#
# Exit codes:
#   0   = all tests passed
#   1   = a test failed, a script did not load, no tests were found, the test
#         path is wrong, or kit.json is unreadable
#   2   = Godot not found
#   124 = timeout (the process tree was killed)
#   Any other code is Godot's own (a crash, a script that called quit(n)).
#Requires -Version 7.0

# Only -Test is positional: `test.ps1 test/unit` names a path, not a timeout.
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)][string]$Test = "",
    [int]$TimeoutSeconds = 300,
    [switch]$FailFast
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "godot_resolver.ps1")
. (Join-Path $PSScriptRoot "class_cache.ps1")
try {
    $config = Get-KitConfig
} catch {
    Write-Host "Tests: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
$projectRoot = $config.Root
# -Test: "res://test/unit", "test/unit" and an absolute path all name a project
# path; gdUnit4 gets the res:// form. (kit.json tests.root arrives checked and
# project-relative from Get-KitConfig.)
$source = if ($Test) { "-Test" } else { "kit.json tests.root" }
if (-not $Test) { $Test = $config.TestRoot }
$given = $Test -replace '^res://', ''
$localTest = if ([System.IO.Path]::IsPathRooted($given)) {
    [System.IO.Path]::GetFullPath($given)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $given))
}
if (-not (Test-KitPathInProject $localTest)) {
    # The project reached by another spelling: through a directory link, or its
    # real path when this script was started through one.
    $localTest = ConvertTo-KitProjectPath $localTest
}
if (-not $localTest) {
    Write-Host "Tests: $source '$Test' is not a path inside the project." -ForegroundColor Red
    exit 1
}
$Test = "res://" + (ConvertTo-KitRelativePath $localTest)

try {
    $godot = Resolve-GodotPath
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 2
}

Write-Host "Running gdUnit4 tests (timeout: ${TimeoutSeconds}s)" -ForegroundColor Cyan
Write-Host "  Test path: $Test" -ForegroundColor DarkGray

# gdUnit4 exits 0 when it is pointed at nothing; say what is wrong instead.
if (-not (Test-Path -LiteralPath $localTest)) {
    Write-Host "Test path not found: $localTest ($source)." -ForegroundColor Red
    exit 1
}
$runnerScript = Join-Path $projectRoot "addons/gdUnit4/bin/GdUnitCmdTool.gd"
if (-not (Test-Path -LiteralPath $runnerScript -PathType Leaf)) {
    Write-Host "gdUnit4 is not installed (addons/gdUnit4/bin/GdUnitCmdTool.gd is missing)." -ForegroundColor Red
    exit 1
}

# gdUnit4's command-line runner names its own classes (GdUnitTestCIRunner, ...). They
# resolve only through Godot's class cache, which is gitignored: on a fresh clone, or
# after a pull that moved a class_name script, the run dies with
#   Parse Error: Could not find type "GdUnitTestCIRunner"
# so the cache is rebuilt first whenever it is missing or stale.
if (Test-GodotClassCacheStale -ProjectRoot $projectRoot) {
    try {
        Update-GodotClassCache -Godot $godot -ProjectRoot $projectRoot
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        exit 1
    }
}

# Reports go under artifacts/ and not gdUnit4's default res://reports: Godot would
# try to import the HTML and PNG files it finds there. The .gdignore keeps Godot
# out of artifacts/ altogether.
$artifacts = Join-Path $projectRoot "artifacts"
[System.IO.Directory]::CreateDirectory($artifacts) | Out-Null
$gdignore = Join-Path $artifacts ".gdignore"
if (-not (Test-Path -LiteralPath $gdignore)) {
    [System.IO.File]::WriteAllText($gdignore, "")
}

# --ignoreHeadlessMode: gdUnit4 6 refuses to run headless without it.
$gdunitArgs = @(
    "--headless",
    "-s", "res://addons/gdUnit4/bin/GdUnitCmdTool.gd",
    "--ignoreHeadlessMode",
    "-rd", "res://artifacts/gdunit-reports",
    "-a", $Test
)
# -c (continue): without it gdUnit4 stops at the first failing test.
if ($FailFast) {
    Write-Host "  Mode: stop at the first failure" -ForegroundColor DarkGray
} else {
    $gdunitArgs += "-c"
}

# A user:// of its own for the run, as tools/test_shards.py gives each shard: a
# test that writes a save must not read or replace the ones you play with.
# GetFullPath: the temp folder may be named by a relative path (TMPDIR=tmp), and
# Godot ignores a relative XDG_DATA_HOME.
$userData = [System.IO.Path]::GetFullPath(
    (Join-Path ([System.IO.Path]::GetTempPath()) ("godot_test_user_" + [guid]::NewGuid().ToString("N"))))
[System.IO.Directory]::CreateDirectory($userData) | Out-Null
try {
    # Below-normal priority (children inherit it): a long run must not starve the
    # editor, a running game or other agents on this machine.
    $result = Invoke-KitProcess -FilePath $godot -Arguments $gdunitArgs `
        -WorkingDirectory $projectRoot -TimeoutSeconds $TimeoutSeconds -BelowNormal `
        -Environment (Get-KitUserDataEnvironment -Folder $userData)
} finally {
    Remove-Item -LiteralPath $userData -Recurse -Force -ErrorAction SilentlyContinue
}

if ($result.Stdout) { Write-Host $result.Stdout.TrimEnd() }
if ($result.Stderr) { Write-Host $result.Stderr.TrimEnd() -ForegroundColor Yellow }

if ($result.TimedOut) {
    Write-Host "TIMEOUT: tests exceeded ${TimeoutSeconds}s; the process was killed." -ForegroundColor Red
    exit 124
}

# gdUnit4 colours its output; the checks below read the text without the codes.
$plain = $result.Text -replace '\x1b\[[0-9;]*[A-Za-z]', ''

# gdUnit4 can exit 0 after a discovery or parse error, and after finding no tests.
# Those lines are failures, so a broken suite cannot pass.
$fatal = [regex]::Match($plain, 'No test cases found|SCRIPT ERROR|Parse Error|Failed to load script')
if ($fatal.Success) {
    Write-Host "Test run failed: '$($fatal.Value)' in the output above." -ForegroundColor Red
    exit 1
}

# gdUnit4 exit codes: 0 = pass, 100 = failures, 101 = passed with warnings.
$summary = [regex]::Match($plain, 'Overall Summary:([^\r\n]*)').Groups[1].Value.Trim(' ', '|')
if ($result.Code -eq 100) {
    Write-Host "Tests: FAILED. $summary" -ForegroundColor Red
    exit 1
}
if ($result.Code -ne 0 -and $result.Code -ne 101) {
    Write-Host "Tests: FAILED (Godot exited $($result.Code))." -ForegroundColor Red
    exit $result.Code
}
# A pass needs gdUnit4's own summary: a Godot that exits 0 without running the
# suites must not count.
if (-not $summary) {
    Write-Host "Tests: FAILED. Godot exited $($result.Code) but gdUnit4 printed no 'Overall Summary'." `
        -ForegroundColor Red
    exit 1
}
if ($result.Code -eq 101) {
    Write-Host "Tests: PASSED with warnings. $summary" -ForegroundColor Yellow
} else {
    Write-Host "Tests: PASSED. $summary" -ForegroundColor Green
}
exit 0
