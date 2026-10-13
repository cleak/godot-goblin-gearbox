<#
.SYNOPSIS
Install or upgrade godot-goblin-gearbox in an existing Godot 4.7 project.

.DESCRIPTION
Run it from a clone of the kit, pointing at the game:

    pwsh ./install.ps1 -Target C:\path\to\game -DryRun           # the plan; writes nothing
    pwsh ./install.ps1 -Target C:\path\to\game
    pwsh ./install.ps1 -Target C:\path\to\game -StrictWarnings   # also the strict type check

Commit the game first: without git there is no undo.

Kit files      tools/ and addons/agent_devtools/ are copied, and brought up to date when
               this runs again from a newer kit. tools/kit_manifest.json records what was
               installed; commit it, and let only this script change it. A kit file you
               changed is a conflict: listed, exit 1, nothing written (-Force overwrites
               it). A file of yours with the name of a kit file is never overwritten,
               -Force or not: listed, exit 1, nothing written. A file the kit no longer
               ships is removed unless you changed it. Your other files in tools/ are
               never touched. An older kit does not replace a newer one without -Force.
               Not installed, and never touched: the kit's own tests (tools/tests/,
               test/kit/) and the project data under tools/ci/ (architecture_modules.json,
               allowlists, test timings, suite lists).
               A game made from the kit's template has the kit's files and no manifest.
               Once: run the install.ps1 of the kit version in its VERSION, commit the
               manifest. A newer kit refuses until then.
Starter files  Written where the project has no such file: CLAUDE.md, the lint, editor,
               git and Python config with its lock file, the pre-commit hooks (they leave
               addons/ and tools/ alone), the CI workflow, the Copilot instructions, the
               Claude Code settings and docs/ (not docs/images/: the README's pictures).
               The manifest records each one. A later run updates one you have not
               changed; one you changed is kept and named when the kit's copy changed; one
               you deleted stays deleted. A file that was yours from the start is kept,
               also when it has the kit's text.
               Also: kit.json when there is none, and once a starter test when the test
               folder holds no gdUnit4 suite.
AGENTS.md      Yours is kept. The kit's goes beside it as AGENTS.kit.md, to merge by hand
               and delete; it comes back when the kit's AGENTS.md changes.
gdUnit4        addons/gdUnit4/ is copied only when the project has none, whole or not at
               all (through addons/.gdUnit4.kit-partial/, renamed when complete).
kit.json       The kit's Godot and gdUnit4 pins, source.include [], tests.root = the first
               of test/, tests/, spec/ that holds gdUnit4 suites (else test), and
               gdscript.strict false (true with -StrictWarnings).
project.godot  Edited as text, every other byte kept: the DevTools autoload, the two
               editor plugins, devtools/allow_mutation=false. -StrictWarnings also sets the
               16 strict gdscript/warnings under [debug] to 2 (error); a value it replaces
               is printed.
Git ignore     .gitignore gains /artifacts/, /.tools/, .venv/ and __pycache__/ where missing
               (and .godot/ when the project has no .gitignore at all), added at its end.
Lint rules     Your .gdlintrc is kept. It judges your scripts only: the lint leaves out
               the files tools/kit_manifest.json lists.
Removing it    tools/kit_manifest.json lists what to undo: "files" (the kit's), "seeded"
               (starter files) and "wrote": kit.json, gdUnit4, the starter test, each line
               added to .gitignore, and each project.godot edit (a section or line it
               added, a value it replaced as "name 1 -> 2"). Steps: README.md, "Remove it".
Stopped        A run that stops halfway says so. Run the same command again: the manifest
               already holds what it wrote.

Exit codes: 0 installed (or dry run); 1 refused, conflicts, stopped, or an incomplete kit copy.
#>

# No "#Requires -Version 7" on purpose: Windows PowerShell 5.1 gets one line that
# says to use pwsh (the stock refusal is five and names no fix).

[CmdletBinding()]
param(
    [string]$Target = "",
    [switch]$StrictWarnings,
    [switch]$Force,
    [switch]$DryRun
)

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Host ("install: needs PowerShell 7, this is $($PSVersionTable.PSVersion). " +
        "Run it with pwsh (winget install Microsoft.PowerShell).")
    exit 1
}

$ErrorActionPreference = "Stop"
$applying = $false   # true once this run has written to the target
trap {
    Write-Host "install: $($_.Exception.Message)" -ForegroundColor Red
    if ($applying) {
        Write-Host "install: stopped halfway. Fix that, then run the same command again." -ForegroundColor Red
    }
    exit 1
}

$kitRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/')
$utf8 = [System.Text.UTF8Encoding]::new($false, $true)   # no BOM; invalid bytes throw
$latin1 = [System.Text.Encoding]::GetEncoding(28591)       # one byte, one character
$manifestPath = "tools/kit_manifest.json"
$kitAgentsCopy = "AGENTS.kit.md"

# Folders the kit owns in the target: copied, and updated on later runs.
$ownedTrees = @("tools", "addons/agent_devtools")
# Never installed and never touched: the kit's self-tests, and files under tools/ that
# hold one project's own data.
$notInstalled = @(
    "tools/tests/", "tools/kit_manifest.json", "tools/ci/architecture_modules.json",
    "tools/ci/architecture_allowlist.txt", "tools/ci/test_wallclock_allowlist.txt",
    "tools/ci/typed_ternary_allowlist.txt", "tools/ci/test_timings.json", "tools/ci/slow_suites.txt",
    "tools/ci/always_run_suites.txt"
)
# Starter files: written where the target has none, recorded in the manifest, and kept
# up to date while the target's copy is unchanged.
$seedFiles = @(
    ".gdlintrc", ".editorconfig", ".gitattributes", ".pre-commit-config.yaml", ".python-version",
    "pyproject.toml", "uv.lock", "CLAUDE.md", ".github/workflows/ci.yml",
    ".github/copilot-instructions.md", ".claude/settings.json"
)
$seedTrees = @("docs")
# Not a starter file: pictures for the kit's own README, which is not installed.
$seedSkipped = @("docs/images/")
# What a complete kit copy holds beside the seeds.
$requiredFiles = @(
    "AGENTS.md", "kit.json", "VERSION", "tools/setup.ps1", "tools/check.ps1", "tools/godot.ps1",
    "tools/kit_config.ps1", "addons/agent_devtools/plugin.cfg", "addons/agent_devtools/dev_tools.gd",
    "addons/agent_devtools/dev_tools.gd.uid", "addons/gdUnit4/plugin.cfg"
)
$gitignoreEntries = @("/artifacts/", "/.tools/", ".venv/", "__pycache__/")
$testRootCandidates = @("test", "tests", "spec")
$devtoolsScript = "res://addons/agent_devtools/dev_tools.gd"
$pluginPaths = @("res://addons/agent_devtools/plugin.cfg", "res://addons/gdUnit4/plugin.cfg")
# The kit's own hooks lint tools/ and addons/agent_devtools/. In a project the kit is
# installed into, those files are the kit's and are left to the kit's rules, as
# tools/lint_gdscript.ps1 leaves them. {0}: the gdtoolkit release the kit pins.
$preCommitSeed = @'
# Style hooks for changed .gd files: gdlint (rules in .gdlintrc) and gdformat.
#   uv sync
#   uv run pre-commit install
#   uv run pre-commit run --all-files
#
# Style only. The engine's strict type check needs the Godot binary:
# pwsh ./tools/typecheck.ps1 (or pwsh ./tools/check.ps1 for every gate).
#
# addons/ and tools/ are left alone: they hold the kit and other people's code.
# A script of your own in tools/ is checked by pwsh ./tools/lint_gdscript.ps1.
repos:
  - repo: https://github.com/Scony/godot-gdscript-toolkit
    rev: {0}
    hooks:
      - id: gdlint
        exclude: ^(addons|tools)/
      - id: gdformat
        args: ["--line-length", "100"]
        exclude: ^(addons|tools)/
'@
$starterSuite = @'
extends GdUnitTestSuite
## Starter suite, so the test gates have one to run. Replace it with real tests.


func test_devtools_autoload_is_registered() -> void:
	assert_bool(ProjectSettings.has_setting("autoload/DevTools")).is_true()
'@
# Its .uid sidecar, so the first import leaves nothing new to commit.
$starterUid = "uid://qapyljxfsa0h"

function Stop-Install {
    param([string]$Message)
    Write-Host "install: $Message" -ForegroundColor Red
    exit 1
}

function Join-Relative {
    param([string]$Root, [string]$Relative)
    return [System.IO.Path]::Combine($Root, $Relative.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
}

function Get-ContentHash {
    # SHA-256 of a file with CRLF read as LF, so a checkout that converts line endings
    # is not mistaken for an edit. A file with a NUL byte is hashed as it is.
    param([string]$Path)
    return Get-BytesHash ([System.IO.File]::ReadAllBytes($Path))
}

function Get-BytesHash {
    param([byte[]]$Bytes)
    $text = $latin1.GetString($Bytes)
    if ($text.IndexOf([char]0) -lt 0 -and $text.Contains("`r`n")) {
        $Bytes = $latin1.GetBytes($text.Replace("`r`n", "`n"))
    }
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($sha256.ComputeHash($Bytes)).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Get-TreeFiles {
    # Files under <Root>/<Tree> as forward-slash paths relative to Root, in ordinal order.
    param([string]$Root, [string]$Tree)
    $start = Join-Relative $Root $Tree
    $found = [System.Collections.Generic.List[string]]::new()
    if (-not [System.IO.Directory]::Exists($start)) { return $found.ToArray() }
    $all = [System.IO.SearchOption]::AllDirectories
    foreach ($file in [System.IO.Directory]::EnumerateFiles($start, "*", $all)) {
        $relative = $file.Substring($Root.Length + 1).Replace('\', '/')
        if ($relative -match '(^|/)__pycache__/' -or $relative.EndsWith(".pyc")) { continue }
        $found.Add($relative)
    }
    $found.Sort([System.StringComparer]::Ordinal)
    return $found.ToArray()
}

function Write-TextFile {
    param([string]$Path, [string]$Text)
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($Path)) | Out-Null
    [System.IO.File]::WriteAllText($Path, $Text, $utf8)
}

function Copy-KitFile {
    param([string]$SourceRelative, [string]$Relative = $SourceRelative)
    $destination = Join-Relative $targetRoot $Relative
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($destination)) | Out-Null
    [System.IO.File]::Copy((Join-Relative $kitRoot $SourceRelative), $destination, $true)
}

function Read-JsonFile {
    # The file's JSON object as hashtables, or a refusal that names the file.
    param([string]$Path, [string]$Name, [string]$Fix)
    try {
        $parsed = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable -NoEnumerate
    } catch {
        Stop-Install "$Name is not valid JSON ($($_.Exception.Message)). $Fix"
    }
    # An empty file, a list, a string, null.
    if ($parsed -isnot [System.Collections.IDictionary]) { Stop-Install "$Name is not a JSON object. $Fix" }
    return $parsed
}

function Test-NotInstalled {
    # True for a path the kit never installs, updates or removes ($notInstalled). Names are
    # compared as Windows compares them.
    param([string]$Relative)
    $ignoreCase = [System.StringComparison]::OrdinalIgnoreCase
    foreach ($entry in $notInstalled) {
        if ($Relative.Equals($entry, $ignoreCase)) { return $true }
        if ($entry.EndsWith('/') -and $Relative.StartsWith($entry, $ignoreCase)) { return $true }
    }
    return $false
}

# --- project.godot as text ------------------------------------------------------------
# ProjectSettings.save() would drop every key that equals an engine default, so the file
# is edited as text and only the touched entries change.

# A string, an opening or closing bracket, or a line break: what decides where a value ends.
$valueToken = [regex]::new('"(?:[^"\\]|\\.)*"?|[(\[{]|[)\]}]|\n', [System.Text.RegularExpressions.RegexOptions]::Singleline)
# "[section]", alone on its line or with a ";" comment after it.
$sectionHeader = [regex]::new('^\s*\[([^\]]*)\]\s*(;.*)?$')

function Get-ValueEnd {
    # Index of the line break that ends the value starting at -From (the text length on
    # the last line). Line breaks inside strings, brackets and braces belong to the value.
    param([string]$Text, [int]$From)
    $depth = 0
    $token = $valueToken.Match($Text, $From)
    while ($token.Success) {
        $value = $token.Value
        if ($value -eq "`n") {
            if ($depth -le 0) { return $token.Index }
        } elseif ($value.Length -eq 1 -and "([{".Contains($value)) {
            $depth++
        } elseif ($value.Length -eq 1 -and ")]}".Contains($value)) {
            $depth--
        }
        $token = $token.NextMatch()
    }
    return $Text.Length
}

function Get-ProjectItems {
    # Section headers and "key=value" entries, in file order. Start..End spans the header
    # line or the whole entry (a value may run over several lines), without the line ending.
    param([string]$Text)
    $items = [System.Collections.Generic.List[object]]::new()
    $section = ""
    $at = 0
    while ($at -lt $Text.Length) {
        $lineEnd = $Text.IndexOf("`n", $at)
        if ($lineEnd -lt 0) { $lineEnd = $Text.Length }
        $line = $Text.Substring($at, $lineEnd - $at).TrimEnd("`r")
        $stripped = $line.Trim()
        $equals = $line.IndexOf('=')
        $header = $sectionHeader.Match($line)
        if ($header.Success) {
            $section = $header.Groups[1].Value
            $items.Add([PSCustomObject]@{
                    Header = $true; Section = $section; Key = ""; Start = $at; End = $at + $line.Length; Value = ""
                })
        } elseif ($equals -gt 0 -and -not $stripped.StartsWith(';')) {
            $lineEnd = Get-ValueEnd $Text ($at + $equals + 1)
            $end = $lineEnd
            if ($end -gt 0 -and $Text[$end - 1] -eq "`r") { $end-- }
            $valueStart = $at + $equals + 1
            $items.Add([PSCustomObject]@{
                    Header = $false; Section = $section; Key = $line.Substring(0, $equals).Trim()
                    Start = $at; End = $end; Value = $Text.Substring($valueStart, $end - $valueStart)
                })
        }
        $at = $lineEnd + 1
    }
    return , $items
}

function Get-ProjectValue {
    # The raw value text of [Section] Key, or $null. Names are matched by case, as Godot does.
    param([string]$Text, [string]$Section, [string]$Key)
    foreach ($item in (Get-ProjectItems $Text)) {
        if (-not $item.Header -and $item.Section -ceq $Section -and $item.Key -ceq $Key) { return $item.Value }
    }
    return $null
}

function Set-ProjectValue {
    # -Text with [Section] Key set to -Value: replaced in place, added after the section's
    # last entry, or in a new section placed in alphabetical order, as Godot writes them.
    param([string]$Text, [string]$Section, [string]$Key, [string]$Value)
    $eol = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $entry = "$Key=$Value"
    $inSection = $false
    $hasEntries = $false
    $insertAfter = -1
    $laterSection = -1
    foreach ($item in (Get-ProjectItems $Text)) {
        if ($item.Header) {
            $inSection = $item.Section -ceq $Section
            if ($inSection) {
                $insertAfter = $item.End
            } elseif ($laterSection -lt 0 -and [string]::CompareOrdinal($item.Section, $Section) -gt 0) {
                $laterSection = $item.Start
            }
        } elseif ($inSection) {
            if ($item.Key -ceq $Key) { return $Text.Substring(0, $item.Start) + $entry + $Text.Substring($item.End) }
            $insertAfter = $item.End
            $hasEntries = $true
        }
    }
    if ($insertAfter -ge 0) {
        $lead = if ($hasEntries) { $eol } else { $eol + $eol }
        return $Text.Substring(0, $insertAfter) + $lead + $entry + $Text.Substring($insertAfter)
    }
    $block = "[$Section]$eol$eol$entry$eol"
    if ($laterSection -ge 0) {
        return $Text.Substring(0, $laterSection) + $block + $eol + $Text.Substring($laterSection)
    }
    if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) { $Text += $eol }
    return $Text + $eol + $block
}

function Test-ProjectSection {
    param([string]$Text, [string]$Section)
    foreach ($item in (Get-ProjectItems $Text)) {
        if ($item.Header -and $item.Section -ceq $Section) { return $true }
    }
    return $false
}

function Set-ProjectEntry {
    # Sets [Section] Key in -Patch.Text and records the edit for the manifest: first
    # "[section] new section" when the section is not there yet, then -Entry.
    param($Patch, [string]$Section, [string]$Key, [string]$Value, [string]$Entry)
    if (-not (Test-ProjectSection $Patch.Text $Section)) { $Patch.Record.Add("[$Section] new section") }
    $Patch.Text = Set-ProjectValue $Patch.Text $Section $Key $Value
    $Patch.Record.Add($Entry)
}

function Get-ProjectPatch {
    # The project.godot text with the kit's entries, what changed (Changes: the report's
    # line; Record: one entry per edit, for the manifest's "wrote"), and a Problem when
    # the file holds something this script will not guess about.
    param([string]$Text, [bool]$Strict, [string]$DevToolsUid)
    $changes = [System.Collections.Generic.List[string]]::new()
    $record = [System.Collections.Generic.List[string]]::new()
    $result = [PSCustomObject]@{ Text = $Text; Changes = $changes; Record = $record; Problem = "" }

    $autoload = Get-ProjectValue $Text "autoload" "DevTools"
    if ($null -eq $autoload) {
        Set-ProjectEntry $result "autoload" "DevTools" "`"*$devtoolsScript`"" "[autoload] DevTools"
        $changes.Add("[autoload] DevTools")
    } else {
        $path = $autoload.Trim().Trim('"').TrimStart('*')
        if ($path -cne $devtoolsScript -and $path -cne $DevToolsUid) {
            $result.Problem = "project.godot already has an autoload named DevTools ($($autoload.Trim())). Rename it, then run again."
            return $result
        }
    }

    $enabled = Get-ProjectValue $result.Text "editor_plugins" "enabled"
    $inner = ""
    if ($null -ne $enabled) {
        $list = [regex]::Match($enabled.Trim(), '^PackedStringArray\((.*)\)$', "Singleline")
        if (-not $list.Success) {
            $result.Problem = "project.godot [editor_plugins] enabled is not a PackedStringArray(...). Enable the plugins in Project Settings > Plugins, then run again."
            return $result
        }
        $inner = $list.Groups[1].Value.Trim()
    }
    $have = @([regex]::Matches($inner, '"((?:[^"\\]|\\.)*)"') | ForEach-Object { $_.Groups[1].Value })
    $missing = @($pluginPaths | Where-Object { $have -cnotcontains $_ })
    if ($missing.Count -gt 0) {
        $added = ($missing | ForEach-Object { '"' + $_ + '"' }) -join ", "
        $all = if ($inner) { "$inner, $added" } else { $added }
        $entry = "[editor_plugins] " + (($missing | ForEach-Object { ($_ -split '/')[3] }) -join ", ")
        Set-ProjectEntry $result "editor_plugins" "enabled" "PackedStringArray($all)" $entry
        $changes.Add($entry)
    }

    if ($null -eq (Get-ProjectValue $result.Text "devtools" "allow_mutation")) {
        Set-ProjectEntry $result "devtools" "allow_mutation" "false" "[devtools] allow_mutation=false"
        $changes.Add("[devtools] allow_mutation=false")
    }

    if ($Strict) {
        $added = 0
        foreach ($warning in $strictWarningNames) {
            $key = "gdscript/warnings/$warning"
            $current = Get-ProjectValue $result.Text "debug" $key
            if ($null -eq $current) {
                # The report counts these; the record names each one.
                Set-ProjectEntry $result "debug" $key "2" "[debug] $warning added"
                $added++
            } elseif ($current.Trim() -ne "2") {
                $entry = "[debug] $warning $($current.Trim()) -> 2"
                Set-ProjectEntry $result "debug" $key "2" $entry
                $changes.Add($entry)
            }
        }
        if ($added -gt 0) { $changes.Add("[debug] $added strict warning(s) set to error") }
    }
    return $result
}

function Get-GitIgnorePatch {
    # The kit's entries that .gitignore lacks, and the text that adds them at its end.
    # -Bytes is the file (empty when there is none). The file is only added to: its own
    # bytes may be in any single-byte encoding and are never written again.
    param([byte[]]$Bytes)
    $text = $latin1.GetString($Bytes).TrimStart([char]0xEF, [char]0xBB, [char]0xBF)   # a UTF-8 BOM
    $have = @($text -split "\r?\n" | ForEach-Object { $_.Trim().Trim('/') })
    $missing = @($gitignoreEntries | Where-Object { $have -notcontains $_.Trim('/') })
    # A project with no .gitignore at all also gets Godot's own cache folder.
    if ($Bytes.Length -eq 0) { $missing = @(".godot/") + $missing }
    $append = ""
    if ($missing.Count -gt 0) {
        $eol = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
        if ($Bytes.Length -gt 0) {
            if (-not $text.EndsWith("`n")) { $append += $eol }
            $append += $eol
        }
        $append += "# godot-goblin-gearbox" + $eol + ($missing -join $eol) + $eol
    }
    return [PSCustomObject]@{ Append = $append; Missing = $missing }
}

function Test-InsidePath {
    # True when -Relative ("tools/ci/x.txt") names a path below -Root: forward slashes only,
    # no empty, "." or ".." part, no drive or stream colon.
    param([string]$Root, [string]$Relative)
    if (-not $Relative -or $Relative.IndexOfAny([char[]]@('\', ':')) -ge 0) { return $false }
    foreach ($part in $Relative.Split('/')) {
        if ($part.Trim() -eq "" -or $part.Trim('.', ' ') -eq "") { return $false }
    }
    $full = [System.IO.Path]::GetFullPath((Join-Relative $Root $Relative))
    return $full.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-HasSuite {
    # A gdUnit4 suite under -Folder: a test_*.gd, or a script that extends GdUnitTestSuite.
    param([string]$Folder)
    if (-not [System.IO.Directory]::Exists($Folder)) { return $false }
    $all = [System.IO.SearchOption]::AllDirectories
    foreach ($file in [System.IO.Directory]::EnumerateFiles($Folder, "*.gd", $all)) {
        if ([System.IO.Path]::GetFileName($file) -like "test_*.gd") { return $true }
        if ([System.IO.File]::ReadAllText($file) -match '(?m)^extends\s+GdUnitTestSuite\b') { return $true }
    }
    return $false
}

function Write-Row {
    param([string]$Label, [string]$Text, [string]$Color = "")
    $line = "  {0,-14} {1}" -f $Label, $Text
    if ($Color) { Write-Host $line -ForegroundColor $Color } else { Write-Host $line }
}

function Write-Detail {
    param([string]$Mark, [string[]]$Paths)
    foreach ($path in $Paths) { Write-Host ("  {0,-14} {1} {2}" -f "", $Mark, $path) -ForegroundColor DarkGray }
}

# --- The kit copy ---------------------------------------------------------------------

$absent = @($seedFiles + $requiredFiles | Where-Object { -not [System.IO.File]::Exists((Join-Relative $kitRoot $_)) })
foreach ($tree in $seedTrees + $ownedTrees) {
    if (@(Get-TreeFiles $kitRoot $tree).Count -eq 0) { $absent += "$tree/" }
}
if ($absent.Count -gt 0) {
    Stop-Install "this kit copy is incomplete ($kitRoot): no $($absent -join ', '). Clone or download the kit again."
}
$kitConfig = Read-JsonFile (Join-Relative $kitRoot "kit.json") "the kit's kit.json" "Clone or download the kit again."
$kitVersion = [System.IO.File]::ReadAllText((Join-Relative $kitRoot "VERSION")).Trim()
$kitGodot = [string]$kitConfig.godot.version
$kitGodotMinor = ($kitGodot -split '\.')[0..1] -join '.'
$kitGdunit = [string]$kitConfig.gdunit4.version
$devtoolsUid = [System.IO.File]::ReadAllText((Join-Relative $kitRoot "addons/agent_devtools/dev_tools.gd.uid")).Trim()
# The 16 strict warnings have one home: the list tools/doctor.ps1 and tools/typecheck.ps1 read.
. (Join-Relative $kitRoot "tools/kit_config.ps1")
$strictWarningNames = @(Get-KitStrictWarnings)
$kitHooks = [System.IO.File]::ReadAllText((Join-Relative $kitRoot ".pre-commit-config.yaml"))
$gdtoolkitRelease = [regex]::Match($kitHooks, '(?m)^\s+rev:\s*(\S+)\s*$').Groups[1].Value
if (-not $gdtoolkitRelease) {
    Stop-Install "this kit copy is incomplete ($kitRoot): .pre-commit-config.yaml names no gdtoolkit release (rev:). Clone or download the kit again."
}
$preCommitText = $preCommitSeed.Replace("{0}", $gdtoolkitRelease).Replace("`r`n", "`n") + "`n"

# --- The target -----------------------------------------------------------------------

if (-not $Target.Trim()) {
    Stop-Install "name the game's folder:  pwsh ./install.ps1 -Target C:\path\to\game"
}
$targetRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Target)
$targetRoot = [System.IO.Path]::GetFullPath($targetRoot).TrimEnd('\', '/')
$projectFile = Join-Relative $targetRoot "project.godot"
if (-not [System.IO.File]::Exists($projectFile)) {
    Stop-Install "no project.godot in '$targetRoot'. Point -Target at a Godot project; for a new game use the kit itself as the template."
}
$ignoreCase = [System.StringComparison]::OrdinalIgnoreCase
if ($targetRoot.Equals($kitRoot, $ignoreCase) -or
    $targetRoot.StartsWith($kitRoot + [System.IO.Path]::DirectorySeparatorChar, $ignoreCase)) {
    Stop-Install "-Target is the kit itself, or a folder inside it. Point it at your own project."
}
try {
    $projectText = $utf8.GetString([System.IO.File]::ReadAllBytes($projectFile))
} catch {
    Stop-Install "project.godot is not UTF-8 text. Save it again from the Godot editor, then run again."
}
$configVersion = "$(Get-ProjectValue $projectText '' 'config_version')".Trim()
if ($configVersion -ne "5") {
    $found = if ($configVersion) { "config_version=$configVersion" } else { "no config_version" }
    Stop-Install "project.godot has $found; a Godot 4 project has config_version=5. The kit needs Godot $kitGodotMinor."
}

$notes = [System.Collections.Generic.List[string]]::new()
$features = "$(Get-ProjectValue $projectText 'application' 'config/features')"
$projectMinor = [regex]::Match($features, '"(\d+\.\d+)"').Groups[1].Value
if ($projectMinor -and $projectMinor -ne $kitGodotMinor) {
    $notes.Add("project.godot is a Godot $projectMinor project; the kit is built and tested for $kitGodotMinor (kit.json pins $kitGodot).")
} elseif (-not $projectMinor) {
    $notes.Add("project.godot does not say which Godot it is for; the kit is built and tested for $kitGodotMinor.")
}
$isMono = $features.Contains('"C#"') -or ($null -ne (Get-ProjectValue $projectText "dotnet" "project/assembly_name")) -or
    @([System.IO.Directory]::EnumerateFiles($targetRoot, "*.csproj")).Count -gt 0
if ($isMono) {
    $notes.Add("this is a C# project: the kit's tools run the standard (non-Mono) Godot and check GDScript only.")
}

$previousFiles = @{}
$previousSeeded = @{}
$previousWrote = @{}
$previousUnfinished = @{}   # what a run that stopped halfway was writing: path -> hash
$installedVersion = ""
$manifestFull = Join-Relative $targetRoot $manifestPath
$hadManifest = [System.IO.File]::Exists($manifestFull)
if ($hadManifest) {
    $restore = "Restore it from git, then run again."
    $recorded = Read-JsonFile $manifestFull "the target's $manifestPath" $restore
    # Half a manifest read as "nothing on record" would make every kit file the project's.
    $installedVersion = "$($recorded.kit_version)".Trim()
    if (-not $installedVersion) { Stop-Install "the target's $manifestPath has no kit_version. $restore" }
    foreach ($key in @("files", "seeded", "wrote")) {
        if ($recorded[$key] -isnot [System.Collections.IDictionary]) {
            Stop-Install "the target's $manifestPath has no `"$key`" object. $restore"
        }
    }
    # An older kit would take the newer one's files for the project's own edits.
    if ($installedVersion -ne $kitVersion -and -not $Force) {
        $versions = "the project has kit $installedVersion, this kit is $kitVersion"
        $mine = $null
        $theirs = $null
        if (-not ([version]::TryParse($kitVersion, [ref]$mine) -and [version]::TryParse($installedVersion, [ref]$theirs))) {
            Stop-Install "$versions`: which is newer cannot be told. Run again with -Force to install this one."
        }
        if ($mine -lt $theirs) { Stop-Install "$versions. Pull the kit, or run again with -Force to go back." }
    }
    $previousWrote = $recorded.wrote
    if ($recorded.unfinished -is [System.Collections.IDictionary]) {
        foreach ($key in $recorded.unfinished.Keys) { $previousUnfinished[[string]$key] = [string]$recorded.unfinished[$key] }
    }
    foreach ($key in $recorded.files.Keys) {
        # The manifest decides which files a later run removes: it may only name the project's.
        if (-not (Test-InsidePath $targetRoot ([string]$key))) {
            Stop-Install "the target's $manifestPath lists '$key', which is not a file inside the project. $restore"
        }
        $previousFiles[[string]$key] = [string]$recorded.files[$key]
    }
    foreach ($key in $recorded.seeded.Keys) { $previousSeeded[[string]$key] = [string]$recorded.seeded[$key] }
}

$targetConfigFile = Join-Relative $targetRoot "kit.json"
$hasKitJson = [System.IO.File]::Exists($targetConfigFile)
$testRootName = "test"
$targetStrict = [bool]$StrictWarnings
if ($hasKitJson) {
    $targetConfig = Read-JsonFile $targetConfigFile "the target's kit.json" "Repair it, or delete it to get a new one."
    if ($targetConfig.tests -and $targetConfig.tests.root) {
        $testRootName = ([string]$targetConfig.tests.root -replace '^res://', '').Trim('/', '\')
        if (-not $testRootName -or $testRootName -match '(^|[\\/])\.\.([\\/]|$)' -or
            [System.IO.Path]::IsPathRooted($testRootName)) {
            Stop-Install "the target's kit.json tests.root must be a folder inside the project, not '$($targetConfig.tests.root)'."
        }
    }
    $targetStrict = -not ($targetConfig.gdscript -and $targetConfig.gdscript.strict -eq $false)
    $saysStrict = $targetConfig.gdscript -is [System.Collections.IDictionary] -and $targetConfig.gdscript.Contains("strict")
    $weak = @(Get-KitWeakWarnings ($projectText -split "\r?\n"))
    if (-not $saysStrict -and -not $StrictWarnings -and $weak.Count -gt 0) {
        $notes.Add("kit.json is yours and has no gdscript.strict, so the strict type check is on: " +
            "$($weak.Count) of $($strictWarningNames.Count) strict warnings are not errors yet. " +
            'To start with it off, add:  "gdscript": {"strict": false}')
    }
} else {
    foreach ($candidate in $testRootCandidates) {
        if (Test-HasSuite (Join-Relative $targetRoot $candidate)) { $testRootName = $candidate; break }
    }
}
$starterRelative = "$testRootName/unit/test_kit_smoke.gd"
$starterText = ($starterSuite -replace "`r`n", "`n") + "`n"
$starterRecord = [string]$previousWrote["starter_test"]
# Written once: a starter test you deleted stays deleted. A record from a run that stopped
# before it wrote the file does not count.
$needsStarter = -not (Test-HasSuite (Join-Relative $targetRoot $testRootName)) -and
    (-not $starterRecord -or $previousUnfinished.ContainsKey($starterRecord))
if ($needsStarter) { $starterRecord = $starterRelative }

$patch = Get-ProjectPatch -Text $projectText -Strict ([bool]$StrictWarnings) -DevToolsUid $devtoolsUid
if ($patch.Problem) { Stop-Install $patch.Problem }

$gitignoreFile = Join-Relative $targetRoot ".gitignore"
$gitignoreBytes = [byte[]]::new(0)
if ([System.IO.File]::Exists($gitignoreFile)) { $gitignoreBytes = [System.IO.File]::ReadAllBytes($gitignoreFile) }
if ([System.Array]::IndexOf($gitignoreBytes, [byte]0) -ge 0) {
    Stop-Install ".gitignore is not UTF-8 text (it holds NUL bytes, as UTF-16 does), so git does not read it either. Save it as UTF-8, then run again."
}
$ignorePatch = Get-GitIgnorePatch $gitignoreBytes

# --- Plan: kit files ------------------------------------------------------------------

$owned = [ordered]@{}                                            # relative path -> hash
$writes = [System.Collections.Generic.List[object]]::new()
$conflicts = [System.Collections.Generic.List[string]]::new()   # kit files the project changed
$clashes = [System.Collections.Generic.List[string]]::new()     # the project's files under a kit name
$script:unchanged = 0

function Add-OwnedFile {
    # -MergeCopy: a file the user deletes once merged; it is not put back until it changes.
    param([string]$Relative, [string]$SourceRelative = $Relative, [switch]$MergeCopy)
    $destination = Join-Relative $targetRoot $Relative
    $hash = Get-ContentHash (Join-Relative $kitRoot $SourceRelative)
    $owned[$Relative] = $hash
    $recorded = $previousFiles[$Relative]
    if (-not [System.IO.File]::Exists($destination)) {
        if ($MergeCopy -and $recorded -eq $hash) { $script:unchanged++; return }
        $writes.Add([PSCustomObject]@{ Source = $SourceRelative; Relative = $Relative; Why = "new" })
        return
    }
    $current = Get-ContentHash $destination
    if ($current -eq $hash) { $script:unchanged++; return }
    if (-not $recorded) {
        # No record says the kit put it there: it is the project's, and stays, -Force or not.
        $owned.Remove($Relative)
        $clashes.Add($Relative)
    } elseif ($Force -or $recorded -eq $current) {
        $writes.Add([PSCustomObject]@{ Source = $SourceRelative; Relative = $Relative; Why = "update" })
    } else {
        $conflicts.Add($Relative)
    }
}

foreach ($tree in $ownedTrees) {
    foreach ($relative in (Get-TreeFiles $kitRoot $tree)) {
        if (-not (Test-NotInstalled $relative)) { Add-OwnedFile $relative }
    }
}

# Kit files with no record of who put them there: a game made from the kit's template. A
# kit file a newer kit changed would look like the game's own, and one it dropped would
# stay: the kit version the game started from has to write the manifest first.
$fromTemplate = -not $hadManifest -and $script:unchanged -gt 0
$templateVersion = ""
$targetVersionFile = Join-Relative $targetRoot "VERSION"
if ($fromTemplate -and [System.IO.File]::Exists($targetVersionFile)) {
    $templateVersion = [regex]::Match([System.IO.File]::ReadAllText($targetVersionFile), '^\s*(\d+(\.\d+)+\S*)\s*$').Groups[1].Value
}
if ($templateVersion -and $templateVersion -ne $kitVersion) {
    Stop-Install ("the game has the kit's files and no $manifestPath`: it was made from the kit's template, " +
        "at kit $templateVersion by its VERSION file. Once: run the install.ps1 of kit $templateVersion on it and " +
        "commit the manifest. Then run this one.")
}

# AGENTS.md: the kit's when the project has none; otherwise the project's stays and the
# kit's lands beside it, unless the project still has the kit's current text.
$seeded = [ordered]@{}
$kitAgentsHash = Get-ContentHash (Join-Relative $kitRoot "AGENTS.md")
$targetAgents = Join-Relative $targetRoot "AGENTS.md"
$agents = "seed"
if ([System.IO.File]::Exists($targetAgents)) {
    $startedFrom = $previousSeeded["AGENTS.md"]
    if ((Get-ContentHash $targetAgents) -eq $kitAgentsHash) { $startedFrom = $kitAgentsHash }
    if ($startedFrom) { $seeded["AGENTS.md"] = $startedFrom }
    $agents = if ($startedFrom -eq $kitAgentsHash) { "current" } elseif ($startedFrom) { "changed" } else { "yours" }
    if ($agents -ne "current") { Add-OwnedFile $kitAgentsCopy "AGENTS.md" -MergeCopy }
} elseif ($previousSeeded["AGENTS.md"]) {
    # You deleted it: it stays deleted, and a copy of the kit's beside it stays as it is.
    $agents = "deleted"
    $seeded["AGENTS.md"] = $previousSeeded["AGENTS.md"]
    if ($previousFiles[$kitAgentsCopy]) { $owned[$kitAgentsCopy] = $previousFiles[$kitAgentsCopy] }
} else {
    $seeded["AGENTS.md"] = $kitAgentsHash
}

# Files an earlier kit installed and this one does not ship. Only what the kit installs
# is ever removed: a path it never installs is the project's, whatever the manifest says.
$removals = [System.Collections.Generic.List[string]]::new()
$leftBehind = [System.Collections.Generic.List[string]]::new()
foreach ($relative in @($previousFiles.Keys | Sort-Object)) {
    $inOwnedTree = @($ownedTrees | Where-Object { $relative.StartsWith("$_/") }).Count -gt 0
    if ($owned.Contains($relative) -or -not ($inOwnedTree -or $relative -eq $kitAgentsCopy)) { continue }
    if (Test-NotInstalled $relative) { continue }
    $path = Join-Relative $targetRoot $relative
    if (-not [System.IO.File]::Exists($path)) { continue }
    if ((Get-ContentHash $path) -eq $previousFiles[$relative]) { $removals.Add($relative) } else { $leftBehind.Add($relative) }
}

# --- Plan: gdUnit4, seeds -------------------------------------------------------------

$gdunitFolder = Join-Relative $targetRoot "addons/gdUnit4"
# gdUnit4 is copied here and renamed when every file has arrived, so addons/gdUnit4 is
# never half there. Godot does not scan a folder whose name starts with a dot.
$gdunitPartial = "addons/.gdUnit4.kit-partial"
$stalePartial = [System.IO.Directory]::Exists((Join-Relative $targetRoot $gdunitPartial))
$installGdunit = -not [System.IO.Directory]::Exists($gdunitFolder)
$gdunitFiles = @()
$gdunitRow = ""
if ($installGdunit) {
    $gdunitFiles = @(Get-TreeFiles $kitRoot "addons/gdUnit4")
    $gdunitRow = "$kitGdunit, $($gdunitFiles.Count) files"
} else {
    $plugin = Join-Relative $gdunitFolder "plugin.cfg"
    $found = ""
    if ([System.IO.File]::Exists($plugin)) {
        $found = "$(Get-ProjectValue ([System.IO.File]::ReadAllText($plugin)) 'plugin' 'version')".Trim().Trim('"')
    }
    if ($found -eq $kitGdunit) {
        $gdunitRow = "yours kept: $found, the version the kit pins"
    } else {
        $shown = if ($found) { $found } else { "no version in addons/gdUnit4/plugin.cfg" }
        $gdunitRow = "yours kept: $shown; the kit pins $kitGdunit"
        $notes.Add("gdUnit4: the kit is tested with $kitGdunit and tools/doctor.ps1 fails on another version. Switch with 'uv run python tools/install_gdunit4.py --force', or set gdunit4.version in kit.json to yours.")
    }
}

# Starter files. The manifest's "seeded" holds, per file, the hash of the kit's copy the
# project's was last compared with: a file that still has it is unchanged and follows
# the kit; any other is the project's and stays.
$seedNew = [System.Collections.Generic.List[string]]::new()
$seedUpdated = [System.Collections.Generic.List[string]]::new()
$seedKept = [System.Collections.Generic.List[string]]::new()
$seedBehind = [System.Collections.Generic.List[string]]::new()
$seedSame = 0
$seedCandidates = @($seedFiles)
foreach ($tree in $seedTrees) {
    foreach ($relative in (Get-TreeFiles $kitRoot $tree)) {
        if (@($seedSkipped | Where-Object { $relative.StartsWith($_) }).Count -eq 0) { $seedCandidates += $relative }
    }
}
$ownPyproject = [System.IO.File]::Exists((Join-Relative $targetRoot "pyproject.toml"))
$kitsPyproject = -not $ownPyproject
# What this run writes beside the kit's files, path -> hash: the manifest's "unfinished"
# while the run is under way.
$unfinished = [ordered]@{}
foreach ($key in $previousUnfinished.Keys) { $unfinished[$key] = $previousUnfinished[$key] }
foreach ($relative in $seedCandidates) {
    $before = $previousSeeded[$relative]
    # Written by a run that stopped halfway: the kit's, though "seeded" does not say so yet.
    $begun = $previousUnfinished[$relative]
    # uv.lock belongs to the kit's pyproject.toml; beside another one it would be wrong.
    if ($relative -eq "uv.lock" -and -not $kitsPyproject) {
        if ($before) { $seeded[$relative] = $before }
        continue
    }
    $kitHash = if ($relative -eq ".pre-commit-config.yaml") {
        Get-BytesHash ($utf8.GetBytes($preCommitText))
    } else {
        Get-ContentHash (Join-Relative $kitRoot $relative)
    }
    $path = Join-Relative $targetRoot $relative
    $follows = $true
    if (-not [System.IO.File]::Exists($path)) {
        if ($before) {
            # You deleted it: it stays deleted.
            $follows = $false
            $kitHash = $before
        } else {
            $seedNew.Add($relative)
            $unfinished[$relative] = $kitHash
        }
    } else {
        $current = Get-ContentHash $path
        if ($current -eq $kitHash) {
            $seedSame++
            # The kit's text, and no record that the kit wrote it: yours from the start.
            if (-not ($before -or $begun -or $fromTemplate)) { $kitHash = $null }
        } elseif (($before -and $current -eq $before) -or ($begun -and $current -eq $begun)) {
            $seedUpdated.Add($relative)
            $unfinished[$relative] = $kitHash
        } else {
            $follows = $false
            $seedKept.Add($relative)
            # Said once: the next run compares with this copy of the kit's.
            if ($before -and $before -ne $kitHash) { $seedBehind.Add($relative) } else { $kitHash = $before }
        }
    }
    if ($kitHash) { $seeded[$relative] = $kitHash }
    if ($relative -eq "pyproject.toml") { $kitsPyproject = $follows }
}
if ($agents -eq "seed") {
    $seedNew.Add("AGENTS.md")
    $unfinished["AGENTS.md"] = $kitAgentsHash
}
if ($needsStarter) { $unfinished[$starterRelative] = Get-BytesHash ($utf8.GetBytes($starterText)) }
# A starter file this kit no longer ships stays, and stays on record while it is there:
# without a hash once that has been said.
$seedDropped = [System.Collections.Generic.List[string]]::new()
foreach ($relative in @($previousSeeded.Keys | Sort-Object)) {
    if ($seeded.Contains($relative) -or $seedCandidates -contains $relative) { continue }
    if (-not (Test-InsidePath $targetRoot $relative)) { continue }
    if (-not [System.IO.File]::Exists((Join-Relative $targetRoot $relative))) { continue }
    if ($previousSeeded[$relative]) { $seedDropped.Add($relative) }
    $seeded[$relative] = ""
}
if ($ownPyproject -and [System.IO.File]::ReadAllText((Join-Relative $targetRoot "pyproject.toml")) -notmatch 'gdtoolkit') {
    $notes.Add("pyproject.toml is yours and has no gdtoolkit. Add the kit's Python tools:  $(Get-KitPythonToolsFix)")
}
if ($StrictWarnings -and $hasKitJson -and -not $targetStrict) {
    $notes.Add('kit.json is yours and has gdscript.strict false. Set it:  "gdscript": {"strict": true}')
}

# What the installer wrote beside the kit's files, over every run: what removing the kit
# has to undo. project.godot: one entry per edit, oldest first: "[section] new section",
# "[autoload] DevTools", "[editor_plugins] <plugins>", "[devtools] allow_mutation=false",
# "[debug] <warning> added", "[debug] <warning> <old value> -> 2".
function Join-Record {
    param($Before, [string[]]$Now = @())
    $all = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in @($Before) + $Now) {
        if ($entry -and -not $all.Contains([string]$entry)) { $all.Add([string]$entry) }
    }
    return , $all.ToArray()
}
# One entry per strict warning, the first: it says what the project had before the kit.
$recordedWarnings = @(@($previousWrote["project_godot"]) | ForEach-Object { (([string]$_) -split ' ')[0..1] -join ' ' })
$projectRecord = @($patch.Record | Where-Object {
        -not $_.StartsWith("[debug] ") -or $recordedWarnings -cnotcontains (($_ -split ' ')[0..1] -join ' ')
    })
$wrote = [ordered]@{
    kit_json      = [bool]$previousWrote["kit_json"] -or -not $hasKitJson
    gdunit4       = [bool]$previousWrote["gdunit4"] -or $installGdunit
    starter_test  = $starterRecord
    project_godot = Join-Record $previousWrote["project_godot"] $projectRecord
    gitignore     = Join-Record $previousWrote["gitignore"] $ignorePatch.Missing
}
$manifest = [ordered]@{
    kit_version = $kitVersion
    hash        = "sha256 of the file with CRLF read as LF"
    seeded      = $seeded
    wrote       = $wrote
    files       = $owned
}
$manifestText = (($manifest | ConvertTo-Json -Depth 4) -replace "`r`n", "`n") + "`n"
$manifestChanged = -not $hadManifest -or
    ([System.IO.File]::ReadAllText($manifestFull) -replace "`r`n", "`n") -ne $manifestText

# --- Report ---------------------------------------------------------------------------

$mode = if ($DryRun) { "   (dry run: nothing is written)" } else { "" }
Write-Host "godot-goblin-gearbox $kitVersion -> $targetRoot$mode" -ForegroundColor Cyan
Write-Host ""

if ($clashes.Count -gt 0) {
    Write-Host "install: $($clashes.Count) file(s) of yours have the name of a kit file:" -ForegroundColor Red
    foreach ($relative in $clashes) { Write-Host "  $relative" -ForegroundColor Red }
    Write-Host "Nothing was written. Rename or move them, then run again." -ForegroundColor Red
    if ($fromTemplate -and -not $templateVersion) {
        # No VERSION says which kit the game started from (with one, the run stopped above
        # or is that kit's own).
        Write-Host ("A game made from the kit's template has no $manifestPath, so a kit file an upgrade " +
            "changes looks like yours. Once: run the install.ps1 of the kit version the game started " +
            "from, commit the manifest. Then run this one.") -ForegroundColor Yellow
    }
}
if ($conflicts.Count -gt 0) {
    Write-Host "install: $($conflicts.Count) kit file(s) in the target differ from what the kit installed:" -ForegroundColor Red
    foreach ($relative in $conflicts) { Write-Host "  $relative" -ForegroundColor Red }
    Write-Host "Nothing was written. Move your changes out of those files, or run again with -Force to overwrite them." -ForegroundColor Red
}
if ($clashes.Count + $conflicts.Count -gt 0) { exit 1 }

$new = @($writes | Where-Object { $_.Why -eq "new" } | ForEach-Object { $_.Relative })
$updated = @($writes | Where-Object { $_.Why -eq "update" } | ForEach-Object { $_.Relative })
$kitRow = "$($new.Count) new, $($updated.Count) updated, $($script:unchanged) unchanged"
if ($removals.Count -gt 0) { $kitRow += ", $($removals.Count) removed" }
Write-Row "kit files" "$kitRow   ($($ownedTrees -join '/, ')/)"
if ($DryRun) { Write-Detail "+" $new }
Write-Detail "~" $updated
Write-Detail "-" $removals
foreach ($relative in $leftBehind) {
    Write-Detail "!" @("$relative   (no longer part of the kit; you changed it, so it stays and is yours)")
}
Write-Row "gdUnit4" $(if ($installGdunit) { "new: $gdunitRow" } else { $gdunitRow })
if ($stalePartial) { Write-Detail "-" @("$gdunitPartial/   (left by a copy that stopped halfway)") }
if ($hasKitJson) {
    Write-Row "kit.json" "yours kept"
} else {
    Write-Row "kit.json" "new: tests.root=$testRootName, gdscript.strict=$("$targetStrict".ToLowerInvariant())"
}
Write-Row "starter files" "$($seedNew.Count) new, $($seedUpdated.Count) updated, $seedSame unchanged, $($seedKept.Count) yours kept"
Write-Detail "+" $seedNew
Write-Detail "~" $seedUpdated
foreach ($relative in $seedKept) {
    if ($seedBehind.Contains($relative)) {
        Write-Detail "!" @("$relative   (yours kept; the kit's changed since you got it)")
    } elseif ($DryRun -or -not $hadManifest) {
        Write-Detail "=" @("$relative   (yours kept; the kit's copy is not installed)")
    }
}
foreach ($relative in $seedDropped) {
    Write-Detail "!" @("$relative   (no longer part of the kit; yours to keep or delete)")
}
# The row points at the kit's copy: said while that file is there.
$agentsCopyThere = [System.IO.File]::Exists((Join-Relative $targetRoot $kitAgentsCopy)) -or
    @($writes | Where-Object { $_.Relative -eq $kitAgentsCopy }).Count -gt 0
if ($agentsCopyThere) {
    switch ($agents) {
        "yours" { Write-Row "AGENTS.md" "yours kept; the kit's is $kitAgentsCopy, to merge by hand" "Yellow" }
        "changed" { Write-Row "AGENTS.md" "yours kept; the kit's changed since you got it: see $kitAgentsCopy" "Yellow" }
    }
}
if ($needsStarter) { Write-Row "starter test" "$starterRelative   (no gdUnit4 suite under $testRootName/)" }
Write-Row "project.godot" $(if ($patch.Changes.Count) { $patch.Changes -join "; " } else { "no change" })
Write-Row ".gitignore" $(if ($ignorePatch.Missing.Count) { "+ " + ($ignorePatch.Missing -join " ") } else { "no change" })
Write-Row "manifest" $(if ($manifestChanged) { "$manifestPath   (commit it)" } else { "no change" })

foreach ($note in $notes) {
    Write-Host ""
    Write-Host "note: $note" -ForegroundColor Yellow
}

$seedWrites = @($seedNew) + @($seedUpdated)
$pending = $writes.Count + $removals.Count + $gdunitFiles.Count + $seedWrites.Count + $patch.Changes.Count +
    $ignorePatch.Missing.Count + [int]$manifestChanged + [int](-not $hasKitJson) + [int]$needsStarter +
    [int]$stalePartial
Write-Host ""
if ($DryRun) {
    Write-Host "Dry run: nothing was written." -ForegroundColor Yellow
    exit 0
}
if ($pending -eq 0) {
    Write-Host "Up to date: nothing to write." -ForegroundColor Green
    exit 0
}

# --- Apply ----------------------------------------------------------------------------
# A run that stops halfway is finished by running it again: kit files are compared by
# hash and gdUnit4 arrives whole or not at all. The manifest is written twice. First with
# what this run writes beside the kit's files ("wrote", and "unfinished" for the starter
# files on their way), so that record is never lost; "files" and "seeded" stay as they
# were, so the next run still knows an update from a change of yours. Last, complete.

if ($manifestChanged) {
    $started = [ordered]@{
        kit_version = $(if ($hadManifest) { $installedVersion } else { $kitVersion })
        hash        = $manifest.hash
        unfinished  = $unfinished
        seeded      = $previousSeeded
        wrote       = $wrote
        files       = $previousFiles
    }
    Write-TextFile $manifestFull ((($started | ConvertTo-Json -Depth 4) -replace "`r`n", "`n") + "`n")
}
$applying = $true
foreach ($write in $writes) { Copy-KitFile $write.Source $write.Relative }
foreach ($relative in $removals) { [System.IO.File]::Delete((Join-Relative $targetRoot $relative)) }
if ($stalePartial) { [System.IO.Directory]::Delete((Join-Relative $targetRoot $gdunitPartial), $true) }
if ($installGdunit) {
    foreach ($relative in $gdunitFiles) {
        Copy-KitFile $relative ($gdunitPartial + $relative.Substring("addons/gdUnit4".Length))
    }
    [System.IO.Directory]::Move((Join-Relative $targetRoot $gdunitPartial), $gdunitFolder)
}
foreach ($relative in $seedWrites) {
    if ($relative -eq ".pre-commit-config.yaml") {
        Write-TextFile (Join-Relative $targetRoot $relative) $preCommitText
    } else {
        Copy-KitFile $relative
    }
}
if (-not $hasKitJson) {
    $pins = @(
        $kitGodot, [string]$kitConfig.godot.windows_x86_64_sha512, $kitGdunit,
        [string]$kitConfig.gdunit4.source_archive_sha256, $testRootName
    ) | ForEach-Object { ConvertTo-Json $_ }
    $kitJson = @"
{
  "godot": {
    "version": $($pins[0]),
    "windows_x86_64_sha512": $($pins[1]),
    "search_roots": []
  },
  "gdunit4": {
    "version": $($pins[2]),
    "source_archive_sha256": $($pins[3])
  },
  "source": {
    "exclude": ["addons", "artifacts", "docs"],
    "include": []
  },
  "tests": {
    "root": $($pins[4])
  },
  "gdscript": {
    "strict": $("$targetStrict".ToLowerInvariant())
  }
}
"@
    Write-TextFile $targetConfigFile (($kitJson -replace "`r`n", "`n") + "`n")
}
if ($needsStarter) {
    Write-TextFile (Join-Relative $targetRoot $starterRelative) $starterText
    Write-TextFile (Join-Relative $targetRoot "$starterRelative.uid") "$starterUid`n"
}
if ($patch.Changes.Count -gt 0) { Write-TextFile $projectFile $patch.Text }
if ($ignorePatch.Missing.Count -gt 0) { [System.IO.File]::AppendAllText($gitignoreFile, $ignorePatch.Append, $utf8) }
if ($manifestChanged) { Write-TextFile $manifestFull $manifestText }

Write-Host "Installed. Next, in $targetRoot`:" -ForegroundColor Green
Write-Host "  pwsh ./tools/setup.ps1 -InstallGodot   # Python tools, Godot $kitGodot, first import, doctor"
Write-Host "  pwsh ./tools/check.ps1                 # every gate"
if (-not $targetStrict -and -not $StrictWarnings) {
    Write-Host "The strict type check is off (kit.json gdscript.strict). When the code is typed:"
    Write-Host "  pwsh `"$(Join-Path $kitRoot 'install.ps1')`" -Target `"$targetRoot`" -StrictWarnings"
    Write-Host '  then set "gdscript": {"strict": true} in kit.json.'
}
exit 0
