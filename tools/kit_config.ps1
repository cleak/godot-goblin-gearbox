# Shared configuration for the kit's PowerShell tools: reads kit.json at the
# project root and lists the project's authored source files.
#
# Dot-source this file; it defines functions only.
#
# "Authored source" is every matching file under the project root, skipping:
#   - folders whose name starts with "." (.godot, .venv, .git, ...)
#   - folders that hold a .gdignore, or a project.godot (a nested project)
#   - directory links
#   - the folders listed in kit.json source.exclude
# and then adding back the folders listed in source.include. tools/kit_config.py
# and tools/kit_config.gd read kit.json the same way, with the same error texts,
# and apply the same rules; change the three together
# (tools/tests/test_kit_config_parity.py compares them). The keys and their
# defaults are listed at the top of tools/kit_config.py.
#
# Get-KitConfig throws one line that names what is wrong in kit.json. A missing
# file, a missing key, null and "" mean the default; a value of the wrong type
# is an error, never a silent default.

function Get-KitRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}

function ConvertTo-KitProjectFolder {
    # -Text as a project-relative folder with forward slashes: "res://test",
    # "test/", "\test" and "./test" all give "test". "" when it names the project
    # root itself or a place outside the project (too many "..", a drive letter).
    param([string]$Text = "")
    $Text = $Text.Replace('\', '/')
    if ($Text.StartsWith("res://", [System.StringComparison]::Ordinal)) { $Text = $Text.Substring(6) }
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($part in $Text.Split('/')) {
        if ($part -eq "" -or $part -ceq ".") { continue }
        if ($part -ceq "..") {
            if ($parts.Count -eq 0) { return "" }
            $parts.RemoveAt($parts.Count - 1)
        } elseif ($part.Contains(":")) {
            return ""
        } else {
            $parts.Add($part)
        }
    }
    return ($parts -join "/")
}

function Get-KitJsonValue {
    # The value of -Key in a JSON object, as a JsonElement; $null when the key is
    # absent or holds null. Names are matched exactly: "Source" is not "source".
    param($Section, [string]$Key)
    if ($null -eq $Section) { return $null }
    $found = $null
    foreach ($property in $Section.EnumerateObject()) {
        # No break: of a key written twice the last one counts, as in Python.
        if ($property.Name -ceq $Key) { $found = $property.Value }
    }
    if ($null -eq $found -or $found.ValueKind -eq [System.Text.Json.JsonValueKind]::Null) { return $null }
    return $found
}

function Get-KitJsonSection {
    param($Json, [string]$Name)
    $value = Get-KitJsonValue $Json $Name
    if ($null -eq $value) { return $null }
    if ($value.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
        throw "kit.json: `"$Name`" must be an object"
    }
    return $value
}

function Get-KitJsonText {
    param($Section, [string]$Name, [string]$Key, [string]$Default = "")
    $value = Get-KitJsonValue $Section $Key
    if ($null -eq $value) { return $Default }
    # A bare number is refused too: 4.10 would arrive as 4.1.
    if ($value.ValueKind -ne [System.Text.Json.JsonValueKind]::String) {
        throw "kit.json: `"$Name.$Key`" must be a string"
    }
    $text = $value.GetString()
    if ($text -eq "") { return $Default }
    return $text
}

function Get-KitJsonTexts {
    # The list comes back as one object: assign it, do not wrap the call in @( ).
    param($Section, [string]$Name, [string]$Key, [string[]]$Default = @())
    $value = Get-KitJsonValue $Section $Key
    if ($null -eq $value) { return , $Default }
    $problem = "kit.json: `"$Name.$Key`" must be a list of strings"
    if ($value.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) { throw $problem }
    $texts = New-Object System.Collections.Generic.List[string]
    foreach ($item in $value.EnumerateArray()) {
        if ($item.ValueKind -ne [System.Text.Json.JsonValueKind]::String) { throw $problem }
        $texts.Add($item.GetString())
    }
    return , $texts.ToArray()
}

function Get-KitJsonFlag {
    param($Section, [string]$Name, [string]$Key, [bool]$Default)
    $value = Get-KitJsonValue $Section $Key
    if ($null -eq $value) { return $Default }
    if ($value.ValueKind -eq [System.Text.Json.JsonValueKind]::True) { return $true }
    if ($value.ValueKind -eq [System.Text.Json.JsonValueKind]::False) { return $false }
    throw "kit.json: `"$Name.$Key`" must be true or false"
}

function ConvertTo-KitFolderList {
    # The list comes back as one object: assign it, do not wrap the call in @( ).
    param([string[]]$Names = @(), [string]$Name, [string]$Key)
    $folders = New-Object System.Collections.Generic.List[string]
    foreach ($entry in $Names) {
        $folder = ConvertTo-KitProjectFolder $entry
        if (-not $folder) {
            throw "kit.json: `"$Name.$Key`" must list folders inside the project, not `"$entry`""
        }
        $folders.Add($folder)
    }
    return , $folders.ToArray()
}

function Read-KitJson {
    # The object kit.json holds, as a JsonElement. System.Text.Json keeps to the
    # JSON standard and to the letter case of a key, as the Python and GDScript
    # loaders do; ConvertFrom-Json also takes comments, trailing commas and
    # single quotes, turns some strings into dates and merges "Key" with "key".
    param([Parameter(Mandatory = $true)][string]$File)
    try {
        $bytes = [System.IO.File]::ReadAllBytes($File)
        $offset = 0
        # Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes a byte order mark.
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            $offset = 3
        }
        $strictUtf8 = New-Object System.Text.UTF8Encoding $false, $true
        $text = $strictUtf8.GetString($bytes, $offset, $bytes.Length - $offset)
        $document = [System.Text.Json.JsonDocument]::Parse($text)
        try {
            # A copy that outlives the document.
            $root = $document.RootElement.Clone()
        } finally {
            $document.Dispose()
        }
    } catch {
        $reason = $_.Exception.Message
        if ($_.Exception.InnerException) { $reason = $_.Exception.InnerException.Message }
        throw "kit.json is not valid JSON: $(($reason -split "`r?`n")[0]) ($File)"
    }
    if ($root.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
        throw "kit.json must hold a JSON object ($File)"
    }
    return $root
}

function Get-KitConfig {
    $root = Get-KitRoot
    $config = [ordered]@{
        Root             = $root
        GodotVersion     = "4.7.2"
        GodotSha512      = ""
        GodotSearchRoots = @()
        Gdunit4Version   = ""
        Gdunit4Sha256    = ""
        SourceExclude    = @("addons", "artifacts", "docs")
        SourceInclude    = @()
        TestRoot         = "test"
        GdscriptStrict   = $true
    }
    $file = Join-Path $root "kit.json"
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        $json = Read-KitJson -File $file
        $godot = Get-KitJsonSection $json "godot"
        $gdunit4 = Get-KitJsonSection $json "gdunit4"
        $source = Get-KitJsonSection $json "source"
        $tests = Get-KitJsonSection $json "tests"
        $gdscript = Get-KitJsonSection $json "gdscript"
        $config.GodotVersion = Get-KitJsonText $godot "godot" "version" $config.GodotVersion
        $config.GodotSha512 = Get-KitJsonText $godot "godot" "windows_x86_64_sha512"
        # No @( ) around these calls: each returns its list as one object.
        $config.GodotSearchRoots = Get-KitJsonTexts $godot "godot" "search_roots"
        $config.Gdunit4Version = Get-KitJsonText $gdunit4 "gdunit4" "version"
        $config.Gdunit4Sha256 = Get-KitJsonText $gdunit4 "gdunit4" "source_archive_sha256"
        $exclude = Get-KitJsonTexts $source "source" "exclude" $config.SourceExclude
        $config.SourceExclude = ConvertTo-KitFolderList $exclude "source" "exclude"
        $include = Get-KitJsonTexts $source "source" "include"
        $config.SourceInclude = ConvertTo-KitFolderList $include "source" "include"
        $writtenRoot = Get-KitJsonText $tests "tests" "root" $config.TestRoot
        $config.TestRoot = ConvertTo-KitProjectFolder $writtenRoot
        if (-not $config.TestRoot) {
            throw "kit.json: `"tests.root`" must be a folder inside the project, not `"$writtenRoot`""
        }
        $config.GdscriptStrict = Get-KitJsonFlag $gdscript "gdscript" "strict" $config.GdscriptStrict
        foreach ($folder in $config.SourceInclude) {
            # A typo here would silently drop the folder from every check.
            if (-not (Test-Path -LiteralPath (Join-Path $root $folder) -PathType Container)) {
                throw "kit.json: `"source.include`" names a folder that does not exist: $folder"
            }
        }
    }
    # "4.7.2" -> "4.7": the major.minor a Godot binary must report.
    $parts = $config.GodotVersion.Split(".")
    $config.GodotMinor = if ($parts.Count -ge 2) { "$($parts[0]).$($parts[1])" } else { $config.GodotVersion }
    return [PSCustomObject]$config
}

function Get-GodotConfigSection {
    # key -> raw value for one [section] of a Godot config file (project.godot,
    # plugin.cfg). Godot writes a dictionary or array value over several lines;
    # they are joined.
    param([string[]]$Lines, [string]$Section)
    $values = @{}
    $inside = $false
    $key = ""
    foreach ($line in $Lines) {
        if ($line -match '^\[([A-Za-z_][\w./-]*)\]\s*$') {
            $inside = $Matches[1] -eq $Section
            $key = ""
        } elseif (-not $inside) {
            continue
        } elseif ($line -match '^([A-Za-z_][^=\s"]*)\s*=(.*)$') {
            $key = $Matches[1]
            $values[$key] = $Matches[2].Trim()
        } elseif ($key -and $line.Trim() -and -not $line.StartsWith(";")) {
            $values[$key] += " " + $line.Trim()
        }
    }
    return $values
}

function Get-KitStrictWarnings {
    # The GDScript warnings a strict project (kit.json gdscript.strict) promotes
    # to errors: [debug] gdscript/warnings/<name>=2 in project.godot. The engine's
    # type check is these and nothing else.
    return @(
        "untyped_declaration", "unsafe_property_access", "unsafe_method_access", "unsafe_cast",
        "unsafe_call_argument", "integer_division", "narrowing_conversion", "unused_variable",
        "unused_local_constant", "unused_private_class_variable", "unused_signal", "shadowed_variable",
        "shadowed_variable_base_class", "shadowed_global_identifier", "standalone_expression",
        "standalone_ternary"
    )
}

function Get-KitPythonTools {
    # The Python packages the kit's tools run on: the dev group of the kit's
    # pyproject.toml (tools/tests/test_install_kit.py compares the two).
    return @(
        "gdtoolkit==4.5.0", "pillow>=11,<13", "pre-commit>=4,<5", "pytest>=8.3,<9",
        "pytest-xdist>=3.6,<4", "ruff>=0.9,<1"
    )
}

function Get-KitPythonToolsFix {
    # The command that adds them to a pyproject.toml the kit did not write, where
    # `uv sync` installs only what that file names.
    return "uv add --dev " + ((Get-KitPythonTools | ForEach-Object { '"' + $_ + '"' }) -join " ")
}

function Test-KitPythonToolsDeclared {
    # False when the project's pyproject.toml exists and does not name gdtoolkit.
    $file = Join-Path (Get-KitRoot) "pyproject.toml"
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $true }
    return [System.IO.File]::ReadAllText($file) -match 'gdtoolkit'
}

function Get-KitWeakWarnings {
    # The strict warnings that -ProjectLines (the lines of project.godot) do not
    # promote to errors. Wrap the call in @( ): none comes back as nothing.
    param([string[]]$ProjectLines = @())
    $debug = Get-GodotConfigSection -Lines $ProjectLines -Section "debug"
    return @(Get-KitStrictWarnings | Where-Object { $debug["gdscript/warnings/$_"] -ne "2" })
}

function Get-KitInstalledFiles {
    <#
    The files install.ps1 put into this project and keeps up to date, as
    project-relative paths with forward slashes: the "files" of
    tools/kit_manifest.json. Empty in the kit's own repository, which has no
    manifest. They are the kit's, not the project's: its style rules do not judge
    them. Wrap the call in @( ). Throws one line when the manifest cannot be read.
    #>
    $file = Join-Path (Get-KitRoot) "tools/kit_manifest.json"
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return @() }
    try {
        $document = [System.Text.Json.JsonDocument]::Parse([System.IO.File]::ReadAllText($file))
    } catch {
        $reason = $_.Exception.Message
        if ($_.Exception.InnerException) { $reason = $_.Exception.InnerException.Message }
        throw "tools/kit_manifest.json is not valid JSON: $(($reason -split "`r?`n")[0]) Restore it from git."
    }
    try {
        $root = $document.RootElement
        $listed = if ($root.ValueKind -eq [System.Text.Json.JsonValueKind]::Object) {
            Get-KitJsonValue $root "files"
        }
        if ($null -eq $listed -or $listed.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
            throw "tools/kit_manifest.json has no `"files`" object. Restore it from git."
        }
        return @($listed.EnumerateObject() | ForEach-Object { $_.Name })
    } finally {
        $document.Dispose()
    }
}

function Test-KitDevToolsAutoload {
    # True when project.godot registers addons/agent_devtools/dev_tools.gd as the
    # autoload "DevTools". Written by hand the entry is the res:// path; the
    # editor's plugin switch stores the script's uid:// (the text of
    # dev_tools.gd.uid).
    $root = Get-KitRoot
    $script = "addons/agent_devtools/dev_tools.gd"
    $scriptFile = Join-Path $root $script
    $projectFile = Join-Path $root "project.godot"
    if (-not (Test-Path -LiteralPath $scriptFile -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) { return $false }
    $autoloads = Get-GodotConfigSection -Lines @(Get-Content -LiteralPath $projectFile) -Section "autoload"
    $entry = ([string]$autoloads["DevTools"]).Trim('"').TrimStart('*')
    if ($entry -ceq "res://$script") { return $true }
    $uidFile = "$scriptFile.uid"
    if (-not (Test-Path -LiteralPath $uidFile -PathType Leaf)) { return $false }
    $uid = "$(Get-Content -LiteralPath $uidFile -Raw)".Trim()
    return [bool]($uid -and $entry -ceq $uid)
}

function ConvertTo-KitRelativePath {
    # Project-relative path with forward slashes ("" for the root itself).
    param([Parameter(Mandatory = $true)][string]$FullPath)
    $root = (Get-KitRoot).TrimEnd('\', '/')
    $full = [System.IO.Path]::GetFullPath($FullPath).TrimEnd('\', '/')
    if ($full.Length -le $root.Length) { return "" }
    return $full.Substring($root.Length + 1).Replace('\', '/')
}

function Test-KitPathInProject {
    param([Parameter(Mandatory = $true)][string]$FullPath)
    $root = (Get-KitRoot).TrimEnd('\', '/')
    $full = [System.IO.Path]::GetFullPath($FullPath)
    return $full -eq $root -or $full.StartsWith(
        $root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Resolve-KitLinkedPath {
    # -FullPath (existing) with every directory link on the way, junction or
    # symbolic link, replaced by its target: Python's Path.resolve().
    param([Parameter(Mandatory = $true)][string]$FullPath)
    $full = [System.IO.Path]::GetFullPath($FullPath)
    $current = [System.IO.Path]::GetPathRoot($full)
    $parts = $full.Substring($current.Length).Split(
        [char[]]@('\', '/'), [System.StringSplitOptions]::RemoveEmptyEntries
    )
    foreach ($part in $parts) {
        $current = [System.IO.Path]::Combine($current, $part)
        $attributes = [System.IO.File]::GetAttributes($current)
        if (-not ($attributes -band [System.IO.FileAttributes]::ReparsePoint)) { continue }
        # $null for a reparse point that is no link (a cloud placeholder).
        $target = if ($attributes -band [System.IO.FileAttributes]::Directory) {
            [System.IO.Directory]::ResolveLinkTarget($current, $true)
        } else {
            [System.IO.File]::ResolveLinkTarget($current, $true)
        }
        if ($null -ne $target) { $current = $target.FullName }
    }
    return $current
}

function Get-KitActualPath {
    # -FullPath (inside the project, existing) spelled as it is on disk. Windows
    # finds "SRC" when the folder is "src", but res:// paths are case-sensitive.
    param([Parameter(Mandatory = $true)][string]$FullPath)
    $actual = (Get-KitRoot).TrimEnd('\', '/')
    foreach ($part in (ConvertTo-KitRelativePath $FullPath).Split('/')) {
        if (-not $part) { continue }
        $onDisk = @([System.IO.Directory]::GetFileSystemEntries($actual, $part))
        $actual = if ($onDisk.Count -eq 1) { $onDisk[0] } else { Join-Path $actual $part }
    }
    return $actual
}

function Test-KitSkippedDirectory {
    # True for a folder Godot does not scan, or that source.exclude names.
    param(
        [Parameter(Mandatory = $true)][System.IO.DirectoryInfo]$Directory,
        [string[]]$Exclude = @()
    )
    if ($Directory.Name.StartsWith(".", [System.StringComparison]::Ordinal)) { return $true }
    if ($Directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $true }
    if ([System.IO.File]::Exists((Join-Path $Directory.FullName ".gdignore"))) { return $true }
    if ([System.IO.File]::Exists((Join-Path $Directory.FullName "project.godot"))) { return $true }
    $relative = ConvertTo-KitRelativePath $Directory.FullName
    foreach ($excluded in $Exclude) {
        if ($relative -ieq $excluded) { return $true }
    }
    return $false
}

function Test-KitAtOrUnder {
    # Both project-relative with forward slashes; "" is the project root.
    param([string]$Folder = "", [string]$Parent = "")
    if (-not $Parent) { return $true }
    return $Folder -ieq $Parent -or
        $Folder.StartsWith("$Parent/", [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-KitFilesUnder {
    param(
        [Parameter(Mandatory = $true)][string]$Start,
        [string[]]$Extensions,
        [string[]]$Exclude = @()
    )
    $found = New-Object System.Collections.Generic.List[System.IO.FileInfo]
    if (-not (Test-Path -LiteralPath $Start -PathType Container)) { return $found }
    $pending = New-Object System.Collections.Generic.Stack[string]
    $pending.Push([System.IO.Path]::GetFullPath($Start))
    while ($pending.Count -gt 0) {
        $directory = New-Object System.IO.DirectoryInfo $pending.Pop()
        foreach ($sub in $directory.EnumerateDirectories()) {
            if (-not (Test-KitSkippedDirectory -Directory $sub -Exclude $Exclude)) {
                $pending.Push($sub.FullName)
            }
        }
        foreach ($file in $directory.EnumerateFiles()) {
            if ($Extensions -contains $file.Extension.ToLowerInvariant()) { $found.Add($file) }
        }
    }
    return $found
}

function Get-KitSourceFiles {
    <#
    The project's authored files with one of -Extensions (default .gd), sorted.
    -Path (a file or folder, absolute or project-relative) narrows the search. A
    file named this way is always returned. A folder gives its authored files:
    the source.exclude folders inside it stay out and the source.include folders
    inside it come back, unless the folder itself is at or under an excluded
    one: asking for an excluded folder by name checks all of it.
    Throws when -Path does not exist or lies outside the project.
    #>
    param(
        [string]$Path = "",
        [string[]]$Extensions = @(".gd")
    )
    $config = Get-KitConfig
    $root = $config.Root
    $files = New-Object System.Collections.Generic.List[System.IO.FileInfo]
    $named = ""
    if ($Path) {
        $full = if ([System.IO.Path]::IsPathRooted($Path)) {
            [System.IO.Path]::GetFullPath($Path)
        } else {
            [System.IO.Path]::GetFullPath((Join-Path $root $Path))
        }
        if (-not (Test-Path -LiteralPath $full)) { throw "Path does not exist: $full" }
        # Links on the way are followed first, as tools/kit_config.py does: the
        # files are listed where they really are, and a link that leaves the
        # project names a path outside it. (The walk below follows no link.)
        $target = Resolve-KitLinkedPath $full
        $realRoot = (Resolve-KitLinkedPath $root).TrimEnd('\', '/')
        $inside = $realRoot + [System.IO.Path]::DirectorySeparatorChar
        if ($target.TrimEnd('\', '/') -ieq $realRoot) {
            $full = $root
        } elseif ($target.StartsWith($inside, [System.StringComparison]::OrdinalIgnoreCase)) {
            $full = Join-Path $root $target.Substring($inside.Length)
        } else {
            throw "Path is outside the project: $target"
        }
        $full = Get-KitActualPath $full
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $item = Get-Item -LiteralPath $full
            if ($Extensions -contains $item.Extension.ToLowerInvariant()) { return @($item) }
            return @()
        }
        $named = ConvertTo-KitRelativePath $full
    }
    $start = if ($named) { Join-Path $root $named } else { $root }
    $namedIsExcluded = $false
    if ($named) {
        foreach ($excluded in $config.SourceExclude) {
            if (Test-KitAtOrUnder -Folder $named -Parent $excluded) { $namedIsExcluded = $true }
        }
    }
    if ($namedIsExcluded) {
        foreach ($file in (Get-KitFilesUnder -Start $start -Extensions $Extensions)) { $files.Add($file) }
    } else {
        foreach ($file in (Get-KitFilesUnder -Start $start -Extensions $Extensions -Exclude $config.SourceExclude)) {
            $files.Add($file)
        }
        foreach ($included in $config.SourceInclude) {
            if (-not (Test-KitAtOrUnder -Folder $included -Parent $named)) { continue }
            foreach ($file in (Get-KitFilesUnder -Start (Join-Path $root $included) -Extensions $Extensions)) {
                $files.Add($file)
            }
        }
    }
    return @($files | Sort-Object FullName -Unique)
}
