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
# Get-KitPlatform, Get-KitArchitecture and Get-KitGodotBuild name the machine;
# a rule that differs by system takes the system as a parameter, so that every
# branch can be tested on any machine.
#
# Get-KitConfig throws one line that names what is wrong in kit.json. A missing
# file, a missing key, null and "" mean the default; a value of the wrong type
# is an error, never a silent default.

function Get-KitRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}

function Get-KitPlatform {
    # The system the tools run on: "windows", "macos" or "linux".
    # PowerShell's own variables: every 7.x has them ([OperatingSystem]::IsWindows()
    # needs 7.1). Windows PowerShell 5.1 has neither, and runs on Windows only.
    if ($PSVersionTable.PSEdition -eq "Desktop" -or $IsWindows) { return "windows" }
    if ($IsMacOS) { return "macos" }
    return "linux"
}

function Get-KitArchitecture {
    # This machine's processor: "x86_64", "arm64", or .NET's name for another.
    $name = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    if ($name -eq "x64") { return "x86_64" }
    return $name
}

function Get-KitGodotBuilds {
    # The official Godot downloads the kit knows: kit.json pins one checksum for
    # each, as godot.<build>_sha512.
    return @("windows_x86_64", "linux_x86_64", "linux_arm64", "macos_universal")
}

function Get-KitGodotBuild {
    # The official Godot download for a system (default: this one), as one of
    # Get-KitGodotBuilds; "" when Godot publishes none for it.
    # tools/kit_config.py godot_build() is the same rule.
    param([string]$Platform = "", [string]$Architecture = "")
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if (-not $Architecture) { $Architecture = Get-KitArchitecture }
    # The 64-bit x86 build, also on Windows for ARM.
    if ($Platform -ceq "windows") { return "windows_x86_64" }
    if ($Platform -ceq "macos") { return "macos_universal" }
    $build = "${Platform}_$Architecture"
    if ((Get-KitGodotBuilds) -ccontains $build) { return $build }
    return ""
}

function Get-KitInstallHint {
    # How to get a program the kit needs (uv, git) on -Platform (default: this
    # machine), as the text of a fix line.
    param(
        [Parameter(Mandatory = $true)][ValidateSet("uv", "git")][string]$Tool,
        [string]$Platform = ""
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    $hints = @{
        windows = @{
            uv  = "winget install astral-sh.uv   (or see https://docs.astral.sh/uv/)"
            git = "winget install Git.Git"
        }
        macos   = @{
            uv  = "brew install uv   (or see https://docs.astral.sh/uv/)"
            git = "xcode-select --install   (or brew install git)"
        }
        linux   = @{
            uv  = "install uv: https://docs.astral.sh/uv/getting-started/installation/"
            git = "install git with your package manager (apt install git, dnf install git)"
        }
    }
    return $hints[$Platform][$Tool]
}

function Get-KitPowerShellProblem {
    <#
    Why PowerShell -Version cannot run the kit's scripts on -Platform (default:
    this machine), as one line; "" when it can. The scripts are written for 7.0.
    Linux and macOS need 7.2:
    - Linux: 7.0 and 7.1 load OpenSSL 1.x to compute a checksum and abort ("No
      usable version of libssl was found") where only OpenSSL 3 is installed;
      Ubuntu 24.04 is one such system.
    - macOS: the scripts were never run there on 7.0 or 7.1, on any Mac, and
      neither has a build for Apple silicon (7.2 is the first).
    #>
    param(
        [Parameter(Mandatory = $true)][version]$Version,
        [string]$Platform = ""
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if ($Version.Major -lt 7) { return "the kit's scripts need 7 or newer" }
    if ($Platform -ceq "windows" -or $Version -ge [version]"7.2") { return "" }
    if ($Platform -ceq "macos") {
        return "on macOS the kit's scripts need 7.2 or newer: 7.0 and 7.1 are not tested there " +
            "and have no Apple silicon build"
    }
    return "on Linux the kit's scripts need 7.2 or newer: 7.0 and 7.1 cannot compute a checksum " +
        "where only OpenSSL 3 is installed"
}

function Get-KitPowerShellRefusal {
    <#
    One line for a script that must not start on a PowerShell that cannot run it:
    the version, what is wrong (Get-KitPowerShellProblem) and the fix; "" when it
    can run. tools/setup.ps1 and tools/install_godot.ps1 ask before they download
    anything: the checksum that follows would abort the process.
    -Version: a PowerShell version (default: the running one). -Platform: the
    system (default: this machine).
    #>
    param($Version = $PSVersionTable.PSVersion, [string]$Platform = "")
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if ($Version -is [string]) { $Version = [version]$Version }
    # Major.minor is all the rule reads, and all that Windows PowerShell 5.1 and
    # PowerShell 7 name alike.
    $problem = Get-KitPowerShellProblem -Version ([version]"$($Version.Major).$($Version.Minor)") -Platform $Platform
    if (-not $problem) { return "" }
    return "PowerShell ${Version}: $problem. Fix: $(Get-KitPowerShellFix -Platform $Platform)"
}

function Get-KitPowerShellFix {
    # How to get a PowerShell the kit's scripts run on, for -Platform (default:
    # this machine), as the text of a fix line.
    param([string]$Platform = "")
    if (-not $Platform) { $Platform = Get-KitPlatform }
    $pages = "https://learn.microsoft.com/powershell/scripting/install"
    switch -CaseSensitive ($Platform) {
        "windows" {
            return "winget install Microsoft.PowerShell   (then run the tools with pwsh, not powershell)"
        }
        "macos" { return "install the current PowerShell: $pages/installing-powershell-on-macos" }
        default { return "install the current PowerShell: $pages/installing-powershell-on-linux" }
    }
}

function Get-KitVenvTool {
    # Where `uv sync` puts a command of the project's virtual environment, as a
    # project-relative path, on -Platform (default: this machine).
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Platform = ""
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if ($Platform -ceq "windows") { return ".venv/Scripts/$Name.exe" }
    return ".venv/bin/$Name"
}

function Get-KitPathComparison {
    # How two paths of this machine compare: Windows ignores letter case. macOS
    # compares as Linux does, as tools/kit_config.py does there: a path spelled
    # in another letter case is "outside the project", never the wrong folder.
    if ((Get-KitPlatform) -eq "windows") { return [System.StringComparison]::OrdinalIgnoreCase }
    return [System.StringComparison]::Ordinal
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
        # build -> SHA-512 of its archive ("" when kit.json pins none).
        GodotSha512      = [ordered]@{}
        GodotSearchRoots = @()
        Gdunit4Version   = ""
        Gdunit4Sha256    = ""
        SourceExclude    = @("addons", "artifacts", "docs")
        SourceInclude    = @()
        TestRoot         = "test"
        GdscriptStrict   = $true
    }
    foreach ($build in (Get-KitGodotBuilds)) { $config.GodotSha512[$build] = "" }
    $file = Join-Path $root "kit.json"
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        $json = Read-KitJson -File $file
        $godot = Get-KitJsonSection $json "godot"
        $gdunit4 = Get-KitJsonSection $json "gdunit4"
        $source = Get-KitJsonSection $json "source"
        $tests = Get-KitJsonSection $json "tests"
        $gdscript = Get-KitJsonSection $json "gdscript"
        $config.GodotVersion = Get-KitJsonText $godot "godot" "version" $config.GodotVersion
        foreach ($build in (Get-KitGodotBuilds)) {
            $config.GodotSha512[$build] = Get-KitJsonText $godot "godot" "${build}_sha512"
        }
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

function Test-GodotConfigStringOpen {
    # True when a "string" is still open at the end of -Line, given whether one
    # was open at its start (-Open). A backslash inside a string takes the next
    # character with it; a ; outside one starts a comment.
    param([string]$Line, [bool]$Open = $false)
    if (-not $Open -and -not $Line.Contains('"')) { return $false }
    foreach ($mark in [regex]::Matches($Line, '\\.|"|;')) {
        $text = $mark.Value
        if ($text -eq '"') {
            $Open = -not $Open
        } elseif ($text -eq ';') {
            if (-not $Open) { break }
        } elseif (-not $Open -and $text[1] -eq '"') {
            # Outside a string a backslash escapes nothing.
            $Open = $true
        }
    }
    return $Open
}

function Get-GodotConfigSection {
    # key -> raw value for one [section] of a Godot config file (project.godot,
    # plugin.cfg). Godot writes a dictionary or array value over several lines;
    # they are joined. A string may run over several lines too (a description):
    # what is inside it is no header and no entry, whatever it looks like.
    # tools/devtools.py read_application_settings reads the same way.
    param([string[]]$Lines, [string]$Section)
    $values = @{}
    $inside = $false
    $key = ""
    $open = $false
    foreach ($line in $Lines) {
        $continued = $open
        $open = Test-GodotConfigStringOpen -Line $line -Open $open
        if ($continued) {
            if ($inside -and $key -and $line.Trim()) { $values[$key] += " " + $line.Trim() }
            continue
        }
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

function Get-KitInstalledFileSet {
    # Get-KitInstalledFiles as a set that compares paths as this machine does:
    # on Linux and macOS tools/Lint.gd is not the kit's tools/lint.gd. Assign
    # it, do not wrap the call in @( ). Throws as Get-KitInstalledFiles does.
    $files = [string[]]@(Get-KitInstalledFiles)
    $set = [System.Collections.Generic.HashSet[string]]::new(
        $files, [System.StringComparer]::FromComparison((Get-KitPathComparison))
    )
    return , $set
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

function Get-KitMainScene {
    # The scene the project starts with: application/run/main_scene, as
    # project.godot or an override.cfg beside it sets it, for every system or
    # for one feature (run/main_scene.<feature>). "" when neither names one:
    # such a project starts no game. Godot says so in a dialog and waits for a
    # click, with --headless too.
    param([string]$ProjectRoot = (Get-KitRoot))
    foreach ($name in @("override.cfg", "project.godot")) {
        $file = Join-Path $ProjectRoot $name
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        $application = Get-GodotConfigSection -Lines @(Get-Content -LiteralPath $file) -Section "application"
        foreach ($key in @($application.Keys | Sort-Object)) {
            if ($key -cne "run/main_scene" -and
                -not $key.StartsWith("run/main_scene.", [System.StringComparison]::Ordinal)) {
                continue
            }
            $scene = ([string]$application[$key]).Trim().Trim('"')
            if ($scene) { return $scene }
        }
    }
    return ""
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
    $comparison = Get-KitPathComparison
    return $full.Equals($root, $comparison) -or
        $full.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, $comparison)
}

function Resolve-KitLinkedPath {
    # -FullPath with every directory link on the way, junction or symbolic
    # link, replaced by its target: Python's Path.resolve(). From the first part
    # that does not exist on, the path is kept as written.
    param([Parameter(Mandatory = $true)][string]$FullPath)
    $full = [System.IO.Path]::GetFullPath($FullPath)
    $current = [System.IO.Path]::GetPathRoot($full)
    # On Linux and macOS a backslash is a letter of a name, not a separator.
    $separators = [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $parts = $full.Substring($current.Length).Split($separators, [System.StringSplitOptions]::RemoveEmptyEntries)
    $missing = $false
    foreach ($part in $parts) {
        $current = [System.IO.Path]::Combine($current, $part)
        if ($missing) { continue }
        # A target may be a link again: 40 in a row is what the systems follow.
        for ($hop = 0; $hop -lt 40; $hop++) {
            try {
                $attributes = [System.IO.File]::GetAttributes($current)
            } catch [System.IO.IOException] {
                # Nothing is there: no link is left to follow.
                $missing = $true
                break
            }
            if (-not ($attributes -band [System.IO.FileAttributes]::ReparsePoint)) { break }
            # Asked of PowerShell: .NET's ResolveLinkTarget needs PowerShell 7.2.
            $link = Get-Item -LiteralPath $current -Force
            # A reparse point that is no link (a cloud placeholder) stays.
            if ($link.LinkType -notin @("SymbolicLink", "Junction")) { break }
            $target = "$(@($link.Target)[0])"
            if (-not $target) { break }
            if (-not [System.IO.Path]::IsPathRooted($target)) {
                $target = Join-Path ([System.IO.Path]::GetDirectoryName($current)) $target
            }
            $current = [System.IO.Path]::GetFullPath($target)
        }
    }
    return $current
}

function ConvertTo-KitProjectPath {
    <#
    -FullPath as a path below Get-KitRoot, or "" when it lies outside the
    project. Links on the way are followed first (Resolve-KitLinkedPath), in
    the path and in the root: the project reached through a link is the
    project, and a link that leaves it names a path outside it.
    #>
    param([Parameter(Mandatory = $true)][string]$FullPath)
    $root = Get-KitRoot
    $target = Resolve-KitLinkedPath $FullPath
    $realRoot = (Resolve-KitLinkedPath $root).TrimEnd('\', '/')
    $inside = $realRoot + [System.IO.Path]::DirectorySeparatorChar
    $comparison = Get-KitPathComparison
    if ($target.TrimEnd('\', '/').Equals($realRoot, $comparison)) { return $root }
    if ($target.StartsWith($inside, $comparison)) { return (Join-Path $root $target.Substring($inside.Length)) }
    return ""
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
        $inProject = ConvertTo-KitProjectPath $full
        if (-not $inProject) { throw "Path is outside the project: $(Resolve-KitLinkedPath $full)" }
        $full = Get-KitActualPath $inProject
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
    # Sort-Object -Unique ignores letter case: on Linux a.gd and A.gd are two files.
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::FromComparison((Get-KitPathComparison))
    )
    return @($files | Where-Object { $seen.Add($_.FullName) } | Sort-Object FullName)
}
