# Prerequisite check: is this machine and checkout ready for the kit's tools?
#
#   pwsh ./tools/doctor.ps1
#
# One line per check, and under every failure the command that fixes it:
#   [ok]    fine
#   [FAIL]  required; the tools will not work until it is fixed
#   [warn]  optional; something is unavailable, the gates still run
#
# Checked: PowerShell (7.0 or newer on Windows, 7.2 or newer on Linux and
# macOS: Get-KitPowerShellProblem), kit.json, project.godot and its strict
# [debug] warnings, Godot (path and version), that those warnings reach the
# addon folders in kit.json source.include, uv, gdlint/gdformat in .venv,
# gdUnit4 against the kit.json pin, the DevTools autoload and its mutation
# switch, the class cache, AGENTS.md sample sections, git.
#
# kit.json gdscript.strict = false (a project that has not adopted the strict
# warnings): the two strict-warning checks print [warn], not [FAIL].
#
# Exit codes: 0 = nothing required is missing, 1 = at least one [FAIL].
#
# No "#Requires -Version 7" here on purpose: started from Windows PowerShell
# 5.1 this script still runs, says to use pwsh, and stops there.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "godot_resolver.ps1")
. (Join-Path $PSScriptRoot "class_cache.ps1")

$script:okCount = 0
$script:warnCount = 0
$script:failCount = 0

function Write-Check {
    param(
        [ValidateSet("ok", "FAIL", "warn")][string]$Status,
        [string]$Text,
        [string]$Fix = "",
        [string]$Note = ""
    )
    switch ($Status) {
        "ok" { $script:okCount++; $label = "[ok]  "; $color = "Green" }
        "warn" { $script:warnCount++; $label = "[warn]"; $color = "Yellow" }
        default { $script:failCount++; $label = "[FAIL]"; $color = "Red" }
    }
    Write-Host "$label $Text" -ForegroundColor $color
    if ($Note) { Write-Host "       $Note" -ForegroundColor DarkGray }
    if ($Fix -and $Status -ne "ok") { Write-Host "       fix: $Fix" }
}

function Get-WarningDirectoryRules {
    # Folder ("res://addons") -> 0 (no warnings) or 1 (warnings), as Godot will
    # apply them: gdscript/warnings/directory_rules, whose default is
    # {"res://addons": 0}. A project file from before that setting may still say
    # gdscript/warnings/exclude_addons=false, which Godot reads as "addons too".
    param([hashtable]$Debug)
    $rules = @{ "res://addons" = 0 }
    $written = $Debug["gdscript/warnings/directory_rules"]
    if ($written) {
        $rules = @{}
        foreach ($pair in [regex]::Matches($written, '"(res://[^"]*)"\s*:\s*(\d+)')) {
            $rules[$pair.Groups[1].Value.TrimEnd('/')] = [int]$pair.Groups[2].Value
        }
    } elseif ($Debug["gdscript/warnings/exclude_addons"] -eq "false") {
        $rules["res://addons"] = 1
    }
    return $rules
}

function Test-WarningsReach {
    # True when Godot applies warnings to scripts in -Folder: the rule of the
    # longest folder that holds it decides; with no rule, warnings apply.
    param([hashtable]$Rules, [string]$Folder)
    $decision = 1
    $longest = -1
    foreach ($rule in $Rules.Keys) {
        if (($Folder -eq $rule -or $Folder.StartsWith("$rule/")) -and $rule.Length -gt $longest) {
            $longest = $rule.Length
            $decision = $Rules[$rule]
        }
    }
    return $decision -ne 0
}

$projectRoot = Get-KitRoot
Write-Host "doctor: $projectRoot" -ForegroundColor Cyan

# --- PowerShell ---
$psVersion = $PSVersionTable.PSVersion
# Major.minor is all the rule reads, and all that Windows PowerShell 5.1 and
# PowerShell 7 name alike.
$psProblem = Get-KitPowerShellProblem -Version ([version]"$($psVersion.Major).$($psVersion.Minor)")
if (-not $psProblem) {
    Write-Check ok "PowerShell $psVersion"
} else {
    Write-Check FAIL "PowerShell $psVersion ($psProblem)" -Fix (Get-KitPowerShellFix)
}
if ($psVersion.Major -lt 7) {
    Write-Host ""
    Write-Host "doctor: NOT READY: run this again with pwsh; nothing else was checked." -ForegroundColor Red
    exit 1
}

# --- kit.json ---
$config = $null
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "kit.json") -PathType Leaf)) {
    Write-Check FAIL "kit.json is missing" -Fix "restore kit.json from the kit (it pins Godot, gdUnit4 and the source folders)"
} else {
    try {
        $config = Get-KitConfig
        $typing = if ($config.GdscriptStrict) { "strict typing on" } else { "strict typing off" }
        Write-Check ok "kit.json (Godot $($config.GodotVersion), tests in $($config.TestRoot)/, $typing)"
    } catch {
        Write-Check FAIL $_.Exception.Message -Fix "correct kit.json: the line above names what is wrong in it"
    }
}
if ($null -eq $config) {
    Write-Host ""
    Write-Host "doctor: kit.json must be readable before anything else can be checked." -ForegroundColor Red
    exit 1
}

# --- project.godot ---
$projectFile = Join-Path $projectRoot "project.godot"
$projectLines = $null
if (Test-Path -LiteralPath $projectFile -PathType Leaf) {
    $projectLines = @(Get-Content -LiteralPath $projectFile)
    Write-Check ok "project.godot"
} else {
    Write-Check FAIL "project.godot is missing: the tools folder must sit in a Godot project" `
        -Fix "copy the kit into the folder that holds project.godot"
}

# --- strict warnings ---
# The engine's type check is these warnings promoted to errors (2). Without them
# tools/typecheck.ps1 passes code it should reject.
$strictWarnings = @(Get-KitStrictWarnings)
if ($null -ne $projectLines) {
    $weak = @(Get-KitWeakWarnings -ProjectLines $projectLines)
    $setThem = "in project.godot under [debug], set gdscript/warnings/<name>=2 for: " + ($weak -join ", ")
    if ($weak.Count -eq 0) {
        Write-Check ok "strict GDScript warnings ($($strictWarnings.Count) set to error)"
    } elseif ($config.GdscriptStrict) {
        Write-Check FAIL "strict GDScript warnings: $($weak.Count) of $($strictWarnings.Count) are not errors" `
            -Fix $setThem
    } else {
        Write-Check warn ("strict GDScript warnings: $($weak.Count) of $($strictWarnings.Count) are not errors " +
            "(kit.json gdscript.strict = false: the type check catches compile errors only)") `
            -Fix "to adopt them, set gdscript.strict to true in kit.json and, $setThem"
    }
}

# --- Godot ---
$godotVersion = ""
try {
    $godot = Resolve-GodotPath
    $godotVersion = (Get-GodotVersionText $godot).Text
    if ($env:GODOT_VERSION -or $godotVersion.StartsWith("$($config.GodotVersion).")) {
        Write-Check ok "Godot $godotVersion" -Note $godot
    } else {
        # Any patch of the pinned minor runs the tools; the pinned one is what
        # install_godot.ps1 gives everyone else.
        Write-Check warn "Godot $godotVersion (kit.json pins $($config.GodotVersion))" -Note $godot `
            -Fix "pwsh ./tools/install_godot.ps1"
    }
    if (Test-KitGuiSubsystem $godot) {
        # The kit's tools wait for it themselves; a shell, an editor task or
        # another script that starts this binary gets no wait and no exit code.
        Write-Check warn "that Godot is the windowed .exe and no _console.exe sits beside it" `
            -Fix "pwsh ./tools/install_godot.ps1   (or unzip Godot's *_console.exe next to it)"
    }
} catch {
    $reason = @($_.Exception.Message -split "`n")
    # The message's own install hint is the fix line below.
    $headline = $reason[0] -replace '(?<=not found\.) Install it with .*$', ''
    $fix = "pwsh ./tools/install_godot.ps1   (or set GODOT_BIN to a Godot $(Get-KitGodotVersion) executable)"
    # No official build for this machine: there is nothing to download.
    if (-not (Get-KitGodotBuild)) { $fix = "set GODOT_BIN to a Godot $(Get-KitGodotVersion) executable" }
    Write-Check FAIL $headline -Fix $fix -Note (($reason | Select-Object -Skip 1) -join "`n       ")
}

# --- strict warnings under res://addons ---
# Godot applies no GDScript warnings under res://addons by default, so an addon
# folder listed in kit.json source.include is loaded by tools/typecheck.ps1 but
# not checked (typecheck.ps1 fails on that) until project.godot includes it.
if ($null -ne $projectLines) {
    $included = @($config.SourceInclude)
    $debug = Get-GodotConfigSection -Lines $projectLines -Section "debug"
    $rules = Get-WarningDirectoryRules -Debug $debug
    $unreached = @($included | Where-Object { -not (Test-WarningsReach -Rules $rules -Folder "res://$_") })
    if ($unreached.Count -gt 0) {
        $wanted = @{}
        foreach ($rule in $rules.Keys) { $wanted[$rule] = $rules[$rule] }
        foreach ($folder in $unreached) { $wanted["res://$folder"] = 1 }
        $line = "gdscript/warnings/directory_rules={" +
            (($wanted.Keys | Sort-Object | ForEach-Object { "`"$_`": $($wanted[$_])" }) -join ", ") + "}"
        $status = if ($config.GdscriptStrict) { "FAIL" } else { "warn" }
        Write-Check $status ("kit.json source.include: Godot applies no GDScript warnings in " +
            "$($unreached -join ', '), so the type check cannot check it") `
            -Fix "in project.godot under [debug], set  $line"
    } elseif ($included.Count -gt 0) {
        Write-Check ok "strict warnings reach source.include ($($included -join ', '))"
    }
}

# --- uv ---
$uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($uv) {
    $uvVersion = (& $uv.Source --version 2>$null | Select-Object -First 1)
    Write-Check ok "$uvVersion"
} else {
    Write-Check FAIL "uv not found (it installs and runs the Python tools)" `
        -Fix (Get-KitInstallHint uv)
}

# --- gdtoolkit ---
$missingTools = @(
    foreach ($tool in @("gdlint", "gdformat")) {
        $inVenv = Join-Path $projectRoot (Get-KitVenvTool $tool)
        if (-not (Test-Path -LiteralPath $inVenv -PathType Leaf)) { $tool }
    }
)
if ($missingTools.Count -eq 0) {
    Write-Check ok "gdlint and gdformat in .venv"
} elseif (Test-KitPythonToolsDeclared) {
    Write-Check FAIL "$($missingTools -join ' and ') not in .venv" -Fix "uv sync"
} else {
    # A pyproject.toml the kit did not write: `uv sync` installs only what it names.
    Write-Check FAIL "$($missingTools -join ' and ') not in .venv: pyproject.toml does not name gdtoolkit" `
        -Fix (Get-KitPythonToolsFix)
}

# --- gdUnit4 ---
$gdunitPlugin = Join-Path $projectRoot "addons/gdUnit4/plugin.cfg"
if (-not (Test-Path -LiteralPath $gdunitPlugin -PathType Leaf)) {
    Write-Check FAIL "gdUnit4 is not installed (addons/gdUnit4)" -Fix "uv run python tools/install_gdunit4.py"
} else {
    $plugin = Get-GodotConfigSection -Lines @(Get-Content -LiteralPath $gdunitPlugin) -Section "plugin"
    $installed = ([string]$plugin["version"]).Trim('"')
    if (-not $config.Gdunit4Version) {
        Write-Check warn "gdUnit4 $installed, but kit.json gdunit4.version pins nothing" `
            -Fix "set gdunit4.version in kit.json to $installed"
    } elseif ($installed -eq $config.Gdunit4Version) {
        Write-Check ok "gdUnit4 $installed (matches kit.json)"
    } else {
        Write-Check FAIL "gdUnit4 $installed is installed; kit.json pins $($config.Gdunit4Version)" `
            -Fix ("keep yours: set gdunit4.version to `"$installed`" in kit.json. Or replace it (local edits " +
                "in addons/gdUnit4 are lost): uv run python tools/install_gdunit4.py --force")
    }
}

# --- DevTools autoload ---
$devtoolsScript = "addons/agent_devtools/dev_tools.gd"
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "addons/agent_devtools") -PathType Container)) {
    Write-Check warn "DevTools addon not installed (addons/agent_devtools): tools/devtools.py cannot drive the game" `
        -Fix "copy addons/agent_devtools from the kit"
} elseif ($null -ne $projectLines) {
    # As the res:// path or as the script's uid:// (see Test-KitDevToolsAutoload).
    if (Test-KitDevToolsAutoload) {
        Write-Check ok "DevTools autoload registered"
    } else {
        Write-Check FAIL "DevTools autoload is not registered in project.godot" `
            -Fix "add under [autoload]:  DevTools=`"*res://$devtoolsScript`""
    }
    # DevTools refuses set_state and run_method unless this is true. Left on, any
    # client of a running game can change it.
    $devtools = Get-GodotConfigSection -Lines $projectLines -Section "devtools"
    if ($devtools["allow_mutation"] -eq "true") {
        Write-Check warn "devtools/allow_mutation=true in project.godot: DevTools clients may set state and call methods in the running game" `
            -Fix "under [devtools] set allow_mutation=false when you are done"
    }
}

# --- class cache ---
if ($null -ne $projectLines) {
    $cacheDiff = @(Get-GodotClassCacheDiff -ProjectRoot $projectRoot)
    if ($cacheDiff.Count -eq 0) {
        Write-Check ok "class cache is fresh"
    } else {
        $shown = @($cacheDiff | Select-Object -First 5)
        if ($cacheDiff.Count -gt $shown.Count) { $shown += "... and $($cacheDiff.Count - $shown.Count) more" }
        Write-Check FAIL "class cache is stale ($($cacheDiff.Count) difference(s)): scripts naming those classes will not parse" `
            -Fix "pwsh ./tools/godot.ps1 --headless --import" -Note ($shown -join "`n       ")
    }
}

# --- AGENTS.md ---
# The template's AGENTS.md marks the sections that describe the sample game.
$agentsFile = Join-Path $projectRoot "AGENTS.md"
if (Test-Path -LiteralPath $agentsFile -PathType Leaf) {
    $agentsText = "$(Get-Content -LiteralPath $agentsFile -Raw)"
    if ($agentsText.Contains("(sample)")) {
        Write-Check warn ("AGENTS.md still says (sample): replace those sections, then delete the lines " +
            "at its top that explain the mark") -Fix "docs/customizing-agents-md.md"
    }
    # install.ps1 keeps a project's own AGENTS.md and puts the kit's beside it.
    if ($agentsText -notmatch '(?m)^# Kit\s*$' -and
        (Test-Path -LiteralPath (Join-Path $projectRoot "AGENTS.kit.md") -PathType Leaf)) {
        Write-Check warn "AGENTS.md has no '# Kit' part: agents are not told the kit's commands" `
            -Fix "copy from '# Kit' down out of AGENTS.kit.md (docs/customizing-agents-md.md)"
    }
}

# --- git ---
$needsGit = "check.ps1 -Changed and pre-commit hooks need it"
$git = @(Get-Command git -CommandType Application -ErrorAction SilentlyContinue)
# macOS: a git that is there may be Apple's placeholder for the Command Line Tools.
$gitProblem = if ($git.Count -gt 0) { Get-KitGitProblem -GitPath $git[0].Source } else { "git not found" }
if ($gitProblem) {
    Write-Check warn "${gitProblem}: $needsGit" -Fix (Get-KitInstallHint git)
} else {
    Write-Check ok "git"
}

Write-Host ""
$summary = "$($script:okCount) ok, $($script:warnCount) warning(s), $($script:failCount) failed"
if ($script:failCount -gt 0) {
    Write-Host "doctor: NOT READY: $summary" -ForegroundColor Red
    exit 1
}
Write-Host "doctor: READY: $summary" -ForegroundColor Green
exit 0
