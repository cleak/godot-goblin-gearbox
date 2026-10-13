<#
Keeps Godot's global class cache (.godot/global_script_class_cache.cfg) in step
with the scripts on disk. The cache lives in the gitignored .godot folder, so a
fresh clone has none, and a pull or merge that adds, moves, renames or deletes
a `class_name` script leaves it stale. Every script that names the new class
then fails to parse ("Identifier X not declared in the current scope"), and so
does everything that loads it.

Test-GodotClassCacheStale compares the (class name, res:// path) pairs of every
`class_name` script on disk with the pairs the cache lists. It scans the folders
Godot scans: addons included, but no dot-folders, .gdignore'd folders or nested
projects (the folder rule of tools/kit_config.ps1, without source.exclude). It
does not follow directory links.
Update-GodotClassCache runs a headless `--import`, which rewrites the cache,
and throws with Godot's output if the import fails. It is an editor run: it
reads your editor settings (an import may need them: the path to Blender) and
uses your own Godot folders, also when a gate starts it.

Dot-source this file; it defines functions only.
#>

. (Join-Path $PSScriptRoot "godot_resolver.ps1")

function Get-GodotClassScriptEntries {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\', '/')
    # `class_name X`, with any annotations before it on the line, with or without
    # arguments: `@tool class_name X`, `@icon("res://i.svg") class_name X`.
    # tools/ci/project_refs.py CLASS_NAME is the same rule.
    $pattern = '^[ \t]*(?:@\w+(?:\([^)\n]*\))?[ \t]+)*class_name[ \t]+(?<name>\w+)'
    $entries = New-Object System.Collections.Generic.List[string]
    # Prune before descending: a hidden folder can hold whole checkouts (agent
    # worktrees). Links are not followed: one that points at an ancestor never ends.
    $pending = New-Object System.Collections.Generic.Stack[string]
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $dir = $pending.Pop()
        foreach ($sub in (New-Object System.IO.DirectoryInfo $dir).EnumerateDirectories()) {
            if ($sub.Name.StartsWith(".", [System.StringComparison]::Ordinal)) { continue }
            if ($sub.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
            if ([System.IO.File]::Exists((Join-Path $sub.FullName ".gdignore"))) { continue }
            if ([System.IO.File]::Exists((Join-Path $sub.FullName "project.godot"))) { continue }
            $pending.Push($sub.FullName)
        }
        foreach ($full in [System.IO.Directory]::EnumerateFiles($dir)) {
            # The extension in any letter case, as tools/kit_config.py reads it: a
            # "*.gd" pattern finds X.GD on Windows only.
            if (-not $full.EndsWith(".gd", [System.StringComparison]::OrdinalIgnoreCase)) { continue }
            # -CaseSensitive: GDScript's keywords are lower case.
            $match = Select-String -LiteralPath $full -Pattern $pattern -List -CaseSensitive
            if ($null -ne $match) {
                $name = $match.Matches[0].Groups["name"].Value
                $resPath = "res://" + $full.Substring($root.Length + 1).Replace('\', '/')
                $entries.Add("$name $resPath")
            }
        }
    }
    return $entries
}

function Get-GodotClassCacheEntries {
    param([Parameter(Mandatory = $true)][string]$CachePath)

    # Each cache entry lists "class" before "path"; pair them in file order.
    # Only GDScript entries count: the disk scan reads .gd files.
    $entries = New-Object System.Collections.Generic.List[string]
    $name = $null
    foreach ($line in Get-Content -LiteralPath $CachePath) {
        if ($line -match '^"class": &"(\w+)"') {
            $name = $Matches[1]
        } elseif ($line -match '^"path": "(res://[^"]+)"' -and $null -ne $name) {
            if ($Matches[1].EndsWith(".gd", [System.StringComparison]::OrdinalIgnoreCase)) {
                $entries.Add("$name $($Matches[1])")
            }
            $name = $null
        }
    }
    return $entries
}

function Get-GodotClassCacheDiff {
    # One line per difference: "missing from cache: ..." or "only in cache: ...".
    # A missing cache file is one line. Empty when the cache is fresh.
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $cache = Join-Path $ProjectRoot ".godot/global_script_class_cache.cfg"
    if (-not (Test-Path -LiteralPath $cache)) {
        return @("no class cache yet (.godot/global_script_class_cache.cfg)")
    }
    $cached = [string[]]@(Get-GodotClassCacheEntries -CachePath $cache | Sort-Object -Unique -CaseSensitive)
    $onDisk = [string[]]@(
        Get-GodotClassScriptEntries -ProjectRoot $ProjectRoot | Sort-Object -Unique -CaseSensitive
    )
    # Ordinal sets: class names and res:// paths are case-sensitive.
    $cachedSet = [System.Collections.Generic.HashSet[string]]::new($cached)
    $onDiskSet = [System.Collections.Generic.HashSet[string]]::new($onDisk)
    return @(
        foreach ($entry in $onDisk) {
            if (-not $cachedSet.Contains($entry)) { "missing from cache: $entry" }
        }
        foreach ($entry in $cached) {
            if (-not $onDiskSet.Contains($entry)) { "only in cache: $entry" }
        }
    )
}

function Test-GodotClassCacheStale {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    return @(Get-GodotClassCacheDiff -ProjectRoot $ProjectRoot).Count -gt 0
}

function Test-GodotImportArg {
    # True when --import is an engine argument (before the first -- or ++).
    param([string[]]$GodotArgs)

    foreach ($argument in $GodotArgs) {
        if ($argument -ceq "--" -or $argument -ceq "++") {
            return $false
        }
        if ($argument -ceq "--import") {
            return $true
        }
    }
    return $false
}

function Update-GodotClassCache {
    param(
        [Parameter(Mandatory = $true)][string]$Godot,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [int]$TimeoutSeconds = 1800
    )

    $cache = Join-Path $ProjectRoot ".godot/global_script_class_cache.cfg"
    if (Test-Path -LiteralPath $cache) {
        Write-Host "Class cache is stale (a class_name script was added, moved, renamed or" `
            "removed); importing the project..." -ForegroundColor DarkGray
    } else {
        Write-Host "No class cache yet (first run in this checkout); importing the project..." `
            -ForegroundColor DarkGray
    }
    $importArgs = @("--headless", "--import")
    $result = Invoke-KitProcess -FilePath $Godot -Arguments $importArgs `
        -WorkingDirectory $ProjectRoot -TimeoutSeconds $TimeoutSeconds
    # Godot writes to stderr even when it succeeds: on a cold cache the first pass can
    # parse scripts before their classes are registered ("Identifier X not declared")
    # and still exit 0. Judge by exit code, and run a second pass if the first was noisy.
    if ($result.Code -eq 0 -and $result.Text -match "SCRIPT ERROR") {
        $result = Invoke-KitProcess -FilePath $Godot -Arguments $importArgs `
            -WorkingDirectory $ProjectRoot -TimeoutSeconds $TimeoutSeconds
        if ($result.Code -eq 0 -and $result.Text -match "SCRIPT ERROR") {
            Write-Host "WARNING: --import still reports SCRIPT ERROR after a second pass:`n$($result.Text)" `
                -ForegroundColor Yellow
        }
    }
    if ($result.TimedOut) {
        throw "Godot --import did not finish in $TimeoutSeconds s while refreshing the class cache:`n$($result.Text)"
    }
    if ($result.Code -ne 0) {
        throw "Godot --import failed with exit code $($result.Code) while refreshing the class cache:`n$($result.Text)"
    }

    if (-not (Test-Path -LiteralPath $cache)) {
        # Seen on older Godot 4 releases: --import leaves no cache, an editor scan writes it.
        Write-Host "No class cache after --import; running a headless editor scan..." -ForegroundColor DarkGray
        [void](Invoke-KitProcess -FilePath $Godot -Arguments @("--headless", "--editor", "--quit-after", "1000") `
                -WorkingDirectory $ProjectRoot -TimeoutSeconds 120)
    }
    $diff = @(Get-GodotClassCacheDiff -ProjectRoot $ProjectRoot)
    if ($diff.Count -gt 0) {
        # Usually a class_name script that does not parse: Godot registers no class for it.
        Write-Host ("WARNING: class cache still differs from the scripts on disk after --import:`n  " +
            ($diff -join "`n  ")) -ForegroundColor Yellow
    }
}
