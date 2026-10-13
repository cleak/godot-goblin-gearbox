# Finds the Godot executable for the kit's tools, and runs processes the way
# Godot on Windows needs.
#
# Dot-source this file; it defines functions only.
#
# Resolve-GodotPath, first compatible hit wins:
#   1) $env:GODOT_BIN   (explicit: an incompatible binary is an error, not a skip)
#   2) $env:GODOT4_EXE  (same rule)
#   3) <project>\.tools\godot\      (where tools/install_godot.ps1 puts it)
#   4) in a linked git worktree, the same folder of the main worktree: one
#      download serves every worktree (Get-KitMainWorktreeProject)
#   5) kit.json godot.search_roots  (absolute or project-relative; %VARS% expand).
#      A Godot unzipped next to your projects: "search_roots": [".."]
#   6) godot / godot4 on PATH. A Mono build there is skipped, not fatal:
#      winget's "godot" shim is commonly the Mono build.
# No folder is searched that you did not name: a file called like a Godot release
# in the folder above the project (a Downloads folder, a shared drive) is not run.
#
# Under a search root (3-5) these layouts are recognised:
#   <root>\Godot_v4.7.2-stable_win64\Godot_v4.7.2-stable_win64_console.exe
#   <root>\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64_console.exe
#   <root>\Godot_v4.7.2-stable_win64.exe      (flat file)
# The _console.exe is preferred: it blocks until Godot exits and forwards
# stdout/stderr, which the plain .exe (a GUI-subsystem program) does not. An
# override or PATH entry that names the plain .exe resolves to the _console.exe
# beside it. A plain .exe with no such neighbour is still accepted: the kit's
# tools wait for it themselves (Invoke-KitProcess, Invoke-KitAttachedProcess).
#
# Accepted version: $env:GODOT_VERSION (major.minor), else the major.minor of
# kit.json godot.version. Only the standard build is accepted: the kit is
# GDScript-only, and the Mono editor adds a [dotnet] block to project.godot.

. (Join-Path $PSScriptRoot "kit_config.ps1")

function ConvertTo-KitArgumentString {
    # One Windows command line from an argument list. Start-Process joins its
    # -ArgumentList with spaces and quotes nothing, so a path with a space
    # (C:\Users\Jane Doe\...) would arrive as two arguments.
    param([string[]]$Arguments)
    $quoted = foreach ($argument in $Arguments) {
        if ($argument -ne "" -and $argument -notmatch '[\s"]') {
            $argument
        } else {
            # Backslashes before a quote, and at the end, are doubled; quotes are escaped.
            $escaped = $argument -replace '(\\*)"', '$1$1\"'
            $escaped = $escaped -replace '(\\+)$', '$1$1'
            '"' + $escaped + '"'
        }
    }
    return ($quoted -join " ")
}

function Stop-KitProcessTree {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    if ($Process.HasExited) { return }
    $isWindowsHost = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    if ($isWindowsHost -and (Get-Command taskkill -ErrorAction SilentlyContinue)) {
        # /T: the console wrapper starts the real Godot as a child.
        taskkill /T /F /PID $Process.Id 2>$null | Out-Null
    } else {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-KitProcess {
    <#
    Runs a program to completion and returns its exit code and output.

    Godot's Windows binary is a GUI-subsystem program: `& godot ... 2>&1` can
    return before it exits and capture nothing, which reads as a pass. Start-Process
    with both streams redirected to files waits for it and keeps the output.

    Returns Code, Stdout, Stderr, Text (both), TimedOut, Seconds. On a timeout the
    whole process tree is killed and Code is 124.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0,
        [switch]$BelowNormal
    )
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    $startArgs = @{
        FilePath               = $FilePath
        PassThru               = $true
        NoNewWindow            = $true
        RedirectStandardOutput = $outFile
        RedirectStandardError  = $errFile
    }
    if ($Arguments.Count -gt 0) { $startArgs.ArgumentList = ConvertTo-KitArgumentString $Arguments }
    # Start-Process reads -WorkingDirectory as a wildcard pattern: a project in
    # "C:\games\[wip] demo" is "not found" unless the brackets are escaped.
    # (-FilePath is taken literally.)
    if ($WorkingDirectory) {
        $startArgs.WorkingDirectory = [System.Management.Automation.WildcardPattern]::Escape($WorkingDirectory)
    }
    $timedOut = $false
    $code = 1
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        # A process started by a below-normal parent is below normal from its first
        # instruction, and so is what it starts in turn (the console wrapper starts
        # the real Godot at once). Lowering the child after the fact is too late.
        $self = [System.Diagnostics.Process]::GetCurrentProcess()
        $ownPriority = $self.PriorityClass
        if ($BelowNormal) {
            try { $self.PriorityClass = "BelowNormal" } catch { }
        }
        try {
            $process = Start-Process @startArgs
        } finally {
            if ($BelowNormal) {
                try { $self.PriorityClass = $ownPriority } catch { }
            }
        }
        # Touching the handle keeps the exit code readable after the process ends.
        $null = $process.Handle
        if ($TimeoutSeconds -gt 0) {
            if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
                $timedOut = $true
                Stop-KitProcessTree $process
                [void]$process.WaitForExit(5000)
            }
        } else {
            $process.WaitForExit()
        }
        $code = if ($timedOut) { 124 } else { $process.ExitCode }
        # An empty file reads as nothing at all, not as "".
        $stdout = "$(Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue)"
        $stderr = "$(Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)"
    } finally {
        $watch.Stop()
        Remove-Item -LiteralPath $outFile, $errFile -ErrorAction SilentlyContinue
    }
    return [PSCustomObject]@{
        Code     = $code
        Stdout   = $stdout
        Stderr   = $stderr
        Text     = ($stdout.TrimEnd() + "`n" + $stderr.TrimEnd()).Trim()
        TimedOut = $timedOut
        Seconds  = [Math]::Round($watch.Elapsed.TotalSeconds, 1)
    }
}

function Get-KitGodotScriptProblem {
    <#
    Why Godot cannot start a `--script` tool: its own error lines, or "" when it
    can. Asked with --check-only, which compiles the script and what it is built
    on and returns at once. Ask before every such run: Godot reports a script
    that does not compile and then idles, with nothing to run and nothing to
    make it quit, until the caller's timeout.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Godot,
        # res://tools/x.gd
        [Parameter(Mandatory = $true)][string]$Script,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [int]$TimeoutSeconds = 120
    )
    $probe = Invoke-KitProcess -FilePath $Godot -WorkingDirectory $ProjectRoot -TimeoutSeconds $TimeoutSeconds `
        -Arguments @("--headless", "--check-only", "--script", $Script)
    if ($probe.TimedOut) { return "godot --check-only --script $Script did not finish in $TimeoutSeconds s." }
    if ($probe.Text -match "SCRIPT ERROR|Parse Error|Failed to load script|Can't load script") { return $probe.Text }
    if ($probe.Code -ne 0) { return "godot --check-only --script $Script exited $($probe.Code):`n$($probe.Text)".Trim() }
    return ""
}

function Test-KitGuiSubsystem {
    # True for a Windows GUI-subsystem program, which the plain Godot .exe is.
    # PowerShell's call operator does not wait for one and leaves $LASTEXITCODE
    # unset, so `& godot.exe ...; exit $LASTEXITCODE` exits 0 whatever Godot did.
    # Read from the PE header: optional header (PE signature + 24), Subsystem at + 68.
    param([Parameter(Mandatory = $true)][string]$FilePath)
    try {
        $stream = [System.IO.File]::Open($FilePath, "Open", "Read", "ReadWrite")
    } catch {
        return $false
    }
    try {
        $reader = New-Object System.IO.BinaryReader $stream
        if ($stream.Length -lt 0x40 -or $reader.ReadUInt16() -ne 0x5A4D) { return $false }  # "MZ"
        $stream.Position = 0x3C
        $header = $reader.ReadInt32()
        if ($header -le 0 -or $header + 0x5E -gt $stream.Length) { return $false }
        $stream.Position = $header
        if ($reader.ReadUInt32() -ne 0x4550) { return $false }  # "PE\0\0"
        $stream.Position = $header + 0x5C
        return $reader.ReadUInt16() -eq 2
    } finally {
        $stream.Dispose()
    }
}

function Invoke-KitAttachedProcess {
    <#
    Runs a program on this console, waits for it, and returns its exit code. Its
    output goes straight to the terminal (or to wherever this process's output is
    redirected); nothing is captured. This is how a GUI-subsystem program is run
    interactively: see Test-KitGuiSubsystem.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = ""
    )
    $info = New-Object System.Diagnostics.ProcessStartInfo $FilePath
    $info.Arguments = ConvertTo-KitArgumentString $Arguments
    $info.UseShellExecute = $false
    if ($WorkingDirectory) { $info.WorkingDirectory = $WorkingDirectory }
    $process = [System.Diagnostics.Process]::Start($info)
    try {
        $process.WaitForExit()
        return $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

function Get-KitGodotVersion {
    # The major.minor a Godot binary must report.
    if ($env:GODOT_VERSION) {
        $version = $env:GODOT_VERSION.Trim()
        if ($version -notmatch '^\d+(\.\d+)+$') {
            throw "GODOT_VERSION must be a version like 4.7; it is '$($env:GODOT_VERSION)'."
        }
        return $version
    }
    return (Get-KitConfig).GodotMinor
}

function Test-IsMonoGodotPath {
    param([string]$Candidate)
    $leaf = [System.IO.Path]::GetFileName($Candidate)
    return $leaf -match '(?i)(^|[_-])mono([_.-]|$)'
}

function Get-GodotVersionText {
    param([Parameter(Mandatory = $true)][string]$Candidate)
    try {
        $probe = Invoke-KitProcess -FilePath $Candidate -Arguments @("--version") -TimeoutSeconds 30
    } catch {
        throw "Could not run '$Candidate --version': $($_.Exception.Message)"
    }
    if ($probe.TimedOut) { throw "'$Candidate --version' did not finish in 30 s." }
    return [PSCustomObject]@{ Code = $probe.Code; Text = $probe.Text }
}

function Get-GodotRejection {
    # Why a binary cannot be used, or $null when it can.
    param([Parameter(Mandatory = $true)][string]$Candidate)
    if (Test-IsMonoGodotPath $Candidate) {
        return "'$Candidate' is a Mono build; the kit needs the standard build."
    }
    $version = Get-KitGodotVersion
    try {
        $probe = Get-GodotVersionText $Candidate
    } catch {
        return $_.Exception.Message
    }
    $versionPattern = '^' + [regex]::Escape($version) + '(?:\.|[- ])'
    if ($probe.Code -ne 0 -or $probe.Text -notmatch $versionPattern -or $probe.Text -notmatch '(?i)stable') {
        return "'$Candidate' reported '$($probe.Text)'; the kit needs Godot $version stable."
    }
    if ($probe.Text -match '(?i)mono') {
        return "'$Candidate' is a Mono build ('$($probe.Text)'); the kit needs the standard build."
    }
    return $null
}

function Get-KitMainWorktreeProject {
    <#
    The folder this project has in the main git worktree, when -ProjectRoot is in
    a linked one (`git worktree add`); "" otherwise: no git, no repository, the
    main worktree itself, or a bare repository. Never an error.
    tools/ci/godot_binary.py main_worktree_project() is the same rule.
    #>
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) { return "" }
    try {
        # Three lines: this worktree's git folder, the repository's, and the
        # project's path below the top of the worktree ("" at the top).
        $said = @(& git -C $ProjectRoot rev-parse --git-dir --git-common-dir --show-prefix 2>$null)
        if ($LASTEXITCODE -ne 0 -or $said.Count -lt 2) { return "" }
    } catch {
        return ""
    }
    $folders = foreach ($line in $said[0..1]) {
        $path = "$line".Trim()
        if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $ProjectRoot $path }
        [System.IO.Path]::GetFullPath($path).TrimEnd('\', '/')
    }
    $prefix = if ($said.Count -ge 3) { "$($said[2])".Trim() } else { "" }
    # In the main worktree both are one folder. A bare repository has no main worktree.
    if ($folders[0] -ieq $folders[1] -or [System.IO.Path]::GetFileName($folders[1]) -ine ".git") { return "" }
    return [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetDirectoryName($folders[1])) $prefix)).TrimEnd('\', '/')
}

function Get-GodotSearchRoots {
    # Folders searched for an unzipped Godot, in order.
    $config = Get-KitConfig
    $roots = @([System.IO.Path]::GetFullPath((Join-Path $config.Root ".tools/godot")))
    $mainProject = Get-KitMainWorktreeProject -ProjectRoot $config.Root
    if ($mainProject) { $roots += [System.IO.Path]::GetFullPath((Join-Path $mainProject ".tools/godot")) }
    foreach ($entry in $config.GodotSearchRoots) {
        $expanded = [System.Environment]::ExpandEnvironmentVariables($entry)
        if (-not $expanded) { continue }
        if (-not [System.IO.Path]::IsPathRooted($expanded)) { $expanded = Join-Path $config.Root $expanded }
        $roots += [System.IO.Path]::GetFullPath($expanded)
    }
    $seen = @{}
    $unique = @()
    foreach ($root in $roots) {
        $key = $root.TrimEnd('\', '/').ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $unique += $root
        }
    }
    return $unique
}

function Get-GodotCandidatesUnder {
    # Godot executables of -Version under one search root: the pinned release
    # first, then the newest patch; the _console.exe before the plain one.
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Version,
        [string]$Preferred = ""
    )
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return @() }
    $namePattern = '^Godot_v(?<version>\d+(?:\.\d+)+)-stable_win64'
    $entries = @(
        foreach ($entry in (Get-ChildItem -LiteralPath $Root -ErrorAction SilentlyContinue)) {
            if ($entry.Name -notmatch $namePattern) { continue }
            $found = $Matches["version"]
            if ($found -ne $Version -and -not $found.StartsWith("$Version.")) { continue }
            if (Test-IsMonoGodotPath $entry.Name) { continue }
            [PSCustomObject]@{
                Entry   = $entry
                Pinned  = [int]($found -eq $Preferred)
                Version = [version]$found
            }
        }
    )
    $entries = @($entries | Sort-Object -Property Pinned, Version -Descending)
    $consoleHits = @()
    $guiHits = @()
    foreach ($item in $entries) {
        $entry = $item.Entry
        $executables = @()
        if ($entry.PSIsContainer) {
            $executables = @(
                Get-ChildItem -LiteralPath $entry.FullName -Filter "*.exe" -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match $namePattern -and -not (Test-IsMonoGodotPath $_.Name) }
            )
        } elseif ($entry.Extension -ieq ".exe") {
            $executables = @($entry)
        }
        foreach ($executable in $executables) {
            if ($executable.Name -match '(?i)_console\.exe$') { $consoleHits += $executable.FullName }
            else { $guiHits += $executable.FullName }
        }
    }
    return @($consoleHits + $guiHits)
}

function Get-GodotCandidates {
    # Every installed Godot the search roots hold, best first (not yet probed).
    $config = Get-KitConfig
    $version = Get-KitGodotVersion
    $candidates = @()
    foreach ($root in (Get-GodotSearchRoots)) {
        $candidates += @(Get-GodotCandidatesUnder -Root $root -Version $version -Preferred $config.GodotVersion)
    }
    return @($candidates | Select-Object -Unique)
}

function Get-GodotConsoleTwin {
    # Godot for Windows ships X.exe with X_console.exe, a wrapper that waits for
    # it and forwards its output and exit code. Given the first, answers the
    # second when it sits beside it; any other path comes back unchanged.
    param([Parameter(Mandatory = $true)][string]$Candidate)
    if ($Candidate -match '(?i)^(?<stem>.+)(?<!_console)\.exe$') {
        $twin = $Matches["stem"] + "_console.exe"
        if (Test-Path -LiteralPath $twin -PathType Leaf) { return $twin }
    }
    return $Candidate
}

function Resolve-GodotPath {
    $version = Get-KitGodotVersion

    foreach ($override in @(
            [PSCustomObject]@{ Name = "GODOT_BIN"; Value = $env:GODOT_BIN },
            [PSCustomObject]@{ Name = "GODOT4_EXE"; Value = $env:GODOT4_EXE }
        )) {
        if ($override.Value) {
            if (-not (Test-Path -LiteralPath $override.Value -PathType Leaf)) {
                throw "$($override.Name) does not name an executable file: '$($override.Value)'."
            }
            $full = Get-GodotConsoleTwin ([System.IO.Path]::GetFullPath($override.Value))
            $rejection = Get-GodotRejection $full
            if ($rejection) { throw "$($override.Name): $rejection" }
            return $full
        }
    }

    $rejected = @()
    $isWindowsHost = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    if ($isWindowsHost) {
        foreach ($candidate in (Get-GodotCandidates)) {
            $rejection = Get-GodotRejection $candidate
            if (-not $rejection) { return $candidate }
            $rejected += $rejection
        }
    }

    foreach ($commandName in @("godot", "godot4")) {
        $commands = @(Get-Command $commandName -All -CommandType Application -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            $found = Get-GodotConsoleTwin $command.Source
            $rejection = Get-GodotRejection $found
            if (-not $rejection) { return $found }
            $rejected += $rejection
        }
    }

    $message = "Godot $version (standard build) not found. Install it with " +
        "'pwsh ./tools/install_godot.ps1', or set GODOT_BIN to a Godot $version executable."
    $pinned = (Get-KitConfig).GodotVersion
    if ($env:GODOT_VERSION -and -not "$pinned.".StartsWith("$version.")) {
        $message += "`nGODOT_VERSION asks for $version; kit.json pins $pinned, which is what install_godot.ps1 installs."
    }
    if ($isWindowsHost) {
        $message += "`nSearched: " + ((Get-GodotSearchRoots) -join "; ") + "; then godot/godot4 on PATH."
    }
    foreach ($rejection in $rejected) { $message += "`nSkipped: $rejection" }
    throw $message
}
