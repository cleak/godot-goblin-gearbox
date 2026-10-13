# Finds the Godot executable for the kit's tools, and runs processes the way
# Godot needs on each system.
#
# Dot-source this file; it defines functions only.
#
# Resolve-GodotPath, first compatible hit wins:
#   1) $env:GODOT_BIN   (explicit: an incompatible binary is an error, not a skip)
#   2) $env:GODOT4_EXE  (same rule)
#   3) <project>/.tools/godot/      (where tools/install_godot.ps1 puts it)
#   4) in a linked git worktree, the same folder of the main worktree: one
#      download serves every worktree (Get-KitMainWorktreeProject)
#   5) kit.json godot.search_roots  (absolute or project-relative; %VAR%, $VAR,
#      ${VAR} and a leading ~ expand). A Godot unzipped next to your projects:
#      "search_roots": [".."]
#   6) godot / godot4 on PATH. A Mono build there is skipped, not fatal:
#      winget's "godot" shim is commonly the Mono build.
# No folder is searched that you did not name: a file called like a Godot release
# in the folder above the project (a Downloads folder, a shared drive) is not run.
#
# Under a search root (3-5) a release is found by the name of the official
# download for this system (Get-KitGodotRelease), as it unzips:
#   Windows       <root>\Godot_v4.7.2-stable_win64\Godot_v4.7.2-stable_win64_console.exe
#                 <root>\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64_console.exe
#                 <root>\Godot_v4.7.2-stable_win64.exe      (flat file)
#   Linux         <root>/Godot_v4.7.2-stable_linux.x86_64/Godot_v4.7.2-stable_linux.x86_64
#                 <root>/Godot_v4.7.2-stable_linux.x86_64   (flat file; arm64: linux.arm64)
#   macOS         <root>/Godot_v4.7.2-stable_macos.universal/Godot.app
#                 <root>/Godot.app                          (tried last)
# On Windows the _console.exe is preferred: it blocks until Godot exits and
# forwards stdout/stderr, which the plain .exe (a GUI-subsystem program) does
# not. An override or PATH entry that names the plain .exe resolves to the
# _console.exe beside it. A plain .exe with no such neighbour is still accepted:
# the kit's tools wait for it themselves (Invoke-KitProcess,
# Invoke-KitAttachedProcess). On macOS an override may name Godot.app: the
# program is Godot.app/Contents/MacOS/Godot.
#
# Accepted version: $env:GODOT_VERSION (major.minor), else the major.minor of
# kit.json godot.version. Only the standard build is accepted: the kit is
# GDScript-only, and the Mono editor adds a [dotnet] block to project.godot.
#
# tools/ci/godot_binary.py is the same rule in Python: change the two together
# (tools/tests/test_resolver_parity.py compares them).

. (Join-Path $PSScriptRoot "kit_config.ps1")

function ConvertTo-KitArgumentString {
    # One command line from an argument list, quoted by the rules of Windows.
    # Start-Process joins its -ArgumentList with spaces and quotes nothing, so a
    # path with a space (C:\Users\Jane Doe\...) would arrive as two arguments.
    # On Linux and macOS .NET splits the line again by the same rules.
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
    if ((Get-KitPlatform) -eq "windows" -and (Get-Command taskkill -ErrorAction SilentlyContinue)) {
        # /T: the console wrapper starts the real Godot as a child.
        taskkill /T /F /PID $Process.Id 2>$null | Out-Null
        return
    }
    try {
        $Process.Kill($true)  # the process and what it started
    } catch {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-KitPosixProcess {
    # Invoke-KitProcess on Linux and macOS: see there.
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0,
        [switch]$BelowNormal,
        [int]$DrainSeconds = 10,
        [System.Collections.IDictionary]$Environment = @{}
    )
    if ($BelowNormal) {
        # A process here may lower its own priority but never raise it again, so
        # this one keeps its own and `nice` starts the program lowered. Without
        # `nice` on PATH the program runs at this process's priority.
        $nice = Get-Command nice -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($nice) {
            $Arguments = @("-n", "10", $FilePath) + $Arguments
            $FilePath = $nice.Source
        }
    }
    # Start-Process is not used here: it copies the output line by line, drops
    # every empty line, and a wait on it returns before the last line is copied.
    $info = New-Object System.Diagnostics.ProcessStartInfo $FilePath
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }  # each one as it is
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    if ($WorkingDirectory) { $info.WorkingDirectory = $WorkingDirectory }
    foreach ($name in $Environment.Keys) { $info.Environment[[string]$name] = [string]$Environment[$name] }
    # GetFullPath: a temp folder named by a relative path (TMPDIR=tmp) is found
    # from .NET's current directory, which is not always PowerShell's.
    $files = @(
        [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempFileName()),
        [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempFileName())
    )
    $sinks = @()
    $process = $null
    $complete = $false
    $timedOut = $false
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        foreach ($file in $files) {
            # Not buffered: what was copied is in the file, also while a copy runs.
            $sinks += [System.IO.FileStream]::new($file, [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite, 1)
        }
        try {
            $process = [System.Diagnostics.Process]::Start($info)
        } catch {
            throw $_.Exception.GetBaseException()  # the system's own error, not PowerShell's wrapper
        }
        # The bytes, copied by .NET's own threads: no line is read, joined or dropped.
        $copies = [System.Threading.Tasks.Task[]]@(
            $process.StandardOutput.BaseStream.CopyToAsync($sinks[0]),
            $process.StandardError.BaseStream.CopyToAsync($sinks[1])
        )
        if ($TimeoutSeconds -gt 0) {
            if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
                $timedOut = $true
                Stop-KitProcessTree $process
                [void]$process.WaitForExit(5000)
            }
        } else {
            $process.WaitForExit()
        }
        # A copy ends when the last holder of the pipe has closed it: at once when
        # that was the program, and not before something it left running ends too.
        # That is waited for -DrainSeconds; the program's own output is copied by
        # then. A copy that failed (a full disk) throws here: no output is lost quietly.
        $complete = [System.Threading.Tasks.Task]::WaitAll($copies, $DrainSeconds * 1000)
        $code = if ($timedOut) { 124 } else { $process.ExitCode }
        if ($complete) { foreach ($sink in $sinks) { $sink.Dispose() } }
        # An empty file reads as nothing at all, not as "".
        $stdout = "$(Get-Content -LiteralPath $files[0] -Raw -ErrorAction SilentlyContinue)"
        $stderr = "$(Get-Content -LiteralPath $files[1] -Raw -ErrorAction SilentlyContinue)"
    } finally {
        $watch.Stop()
        if ($null -eq $process) {
            foreach ($sink in $sinks) { $sink.Dispose() }
        } elseif ($complete) {
            $process.Dispose()
        }
        # Else something still writes: the pipes stay open for it, as a file would.
        Remove-Item -LiteralPath $files -ErrorAction SilentlyContinue
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

function Invoke-KitProcess {
    <#
    Runs a program to completion and returns its exit code and output.

    Godot's Windows binary is a GUI-subsystem program: `& godot ... 2>&1` can
    return before it exits and capture nothing, which reads as a pass. Start-Process
    with both streams redirected to files waits for it and keeps the output.

    Returns Code, Stdout, Stderr, Text (both), TimedOut, Seconds. On a timeout the
    whole process tree is killed and Code is 124. Stdout and Stderr are what the
    program wrote, read as UTF-8: no line end is changed, no empty line dropped.

    -BelowNormal: the program and what it starts run at below-normal priority
    (Linux and macOS: nice 10).

    -Environment: variables the program gets on top of this process's own, name
    to value. This process keeps its own values.

    Linux and macOS:
    - Code is 0-255, as the system reports it: `exit 300` is 44, `exit -1` is 255.
    - A program the system refuses to start throws that system error
      (Test-KitNotExecutable tells "permission denied" apart).
    - Output that something the program started still holds open is waited for
      -DrainSeconds after the program ends, then left behind. Windows does not
      wait for it at all.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 0,
        [switch]$BelowNormal,
        [int]$DrainSeconds = 10,
        [System.Collections.IDictionary]$Environment = @{}
    )
    if ((Get-KitPlatform) -ne "windows") { return Invoke-KitPosixProcess @PSBoundParameters }
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
        # A process started by a below-normal parent is below normal from its
        # first instruction, and so is what it starts in turn (the console
        # wrapper starts the real Godot at once). Lowering the child after the
        # fact is too late.
        $self = [System.Diagnostics.Process]::GetCurrentProcess()
        $ownPriority = $self.PriorityClass
        if ($BelowNormal) {
            try { $self.PriorityClass = "BelowNormal" } catch { }
        }
        # Start-Process takes no environment before PowerShell 7.4: the program
        # inherits this process's, which holds -Environment while it starts.
        $ownValues = @{}
        foreach ($name in $Environment.Keys) {
            $ownValues[[string]$name] = [System.Environment]::GetEnvironmentVariable([string]$name)
            [System.Environment]::SetEnvironmentVariable([string]$name, [string]$Environment[$name])
        }
        try {
            $process = Start-Process @startArgs
        } finally {
            foreach ($name in $ownValues.Keys) {
                if ($null -eq $ownValues[$name]) {
                    # Not SetEnvironmentVariable($name, $null): PowerShell hands a
                    # method "" for $null, and .NET 9 keeps a variable set to "".
                    Remove-Item -LiteralPath "Env:\$name" -ErrorAction SilentlyContinue
                } else {
                    [System.Environment]::SetEnvironmentVariable($name, $ownValues[$name])
                }
            }
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

function Get-KitUserDataEnvironment {
    <#
    The variable that makes a Godot started with it keep user:// data (saves,
    settings, logs) below -Folder and not among the developer's own, as a table
    for Invoke-KitProcess -Environment. Godot 4 keeps app_userdata/<project name>
      Windows  under %APPDATA%\Godot
      Linux    under $XDG_DATA_HOME/godot when that is an absolute path, else
               under ~/.local/share/godot
      macOS    under ~/Library/Application Support/Godot. Only HOME moves it,
               and with it everything else that Godot looks for in the home
               folder.
    -Folder must be an absolute path: Godot ignores a relative XDG_DATA_HOME
    and would use the developer's folder. -Platform: the system (default: this
    one).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Folder,
        [string]$Platform = ""
    )
    if (-not [System.IO.Path]::IsPathFullyQualified($Folder)) {
        throw "Get-KitUserDataEnvironment: -Folder must be an absolute path; it is '$Folder'."
    }
    if (-not $Platform) { $Platform = Get-KitPlatform }
    $variable = switch -CaseSensitive ($Platform) {
        "windows" { "APPDATA" }
        "macos" { "HOME" }
        default { "XDG_DATA_HOME" }
    }
    return @{ $variable = $Folder }
}

function Get-KitQuietPowerShellEnvironment {
    <#
    The variables for a PowerShell a tool starts and may kill (a gate on its
    timeout), as a table for Invoke-KitProcess -Environment: it leaves nothing
    behind, and writes nothing of the user's own PowerShell.
    -Platform: the system (default: this one).

    Every system: DOTNET_MultiCoreJitNoProfileGather=1. A PowerShell rewrites
    its startup profile when it ends, in place: StartupProfileData-NonInteractive
    in the user's PowerShell cache folder (~/.cache/powershell on Linux and
    macOS, %LOCALAPPDATA%\Microsoft\PowerShell on Windows). On Linux two that
    end at the same moment can leave it mixed, and the next PowerShell of that
    user prints "An error has occurred that was not properly handled" and exits
    0 without running anything (the cure: delete that file). With the variable
    a PowerShell reads the profile and writes none. PowerShell 7.2 and newer
    know it; 7.0 and 7.1 run the kit on Windows only, where the file was never
    seen mixed.

    Linux and macOS: DOTNET_EnableDiagnostics=0, POWERSHELL_DIAGNOSTICS_OPTOUT=1.
    A running PowerShell keeps files in the temp folder: .NET's two pipes and a
    socket for a debugger (clr-debug-pipe-<pid>-*, dotnet-diagnostic-<pid>-*)
    and its own pipe for Enter-PSHostProcess (CoreFxPipe_PSHost.*). Killed, it
    leaves them there. With these variables it makes none; a PowerShell that
    does not know the second (7.2) still makes its own pipe. Windows keeps no
    such file.
    #>
    param([string]$Platform = "")
    if (-not $Platform) { $Platform = Get-KitPlatform }
    $quiet = @{ DOTNET_MultiCoreJitNoProfileGather = "1" }
    if ($Platform -cne "windows") {
        $quiet.DOTNET_EnableDiagnostics = "0"
        $quiet.POWERSHELL_DIAGNOSTICS_OPTOUT = "1"
    }
    return $quiet
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

function Get-KitDisplayProblem {
    <#
    Why Godot, started with -GodotArgs, cannot open its window on this machine,
    as one line; "" when it can, or when it opens none (--headless, --version,
    --help, --doctool).

    Linux only: without DISPLAY and WAYLAND_DISPLAY (an ssh session, a CI
    runner) Godot prints a page of display driver errors and exits 1. Windows
    and macOS name their display in no variable: nothing is checked there.

    -Environment: the variables to read (default: this process's).
    #>
    param(
        [string[]]$GodotArgs = @(),
        [string]$Platform = "",
        [System.Collections.IDictionary]$Environment = $null
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if ($Platform -cne "linux") { return "" }
    for ($index = 0; $index -lt $GodotArgs.Count; $index++) {
        $argument = $GodotArgs[$index]
        if ($argument -ceq "--" -or $argument -ceq "++") { break }
        if ($argument -cin @("--headless", "--version", "--help", "-h", "/?", "--doctool")) { return "" }
        if ($argument -ceq "--display-driver" -and $index + 1 -lt $GodotArgs.Count -and
            $GodotArgs[$index + 1] -ceq "headless") {
            return ""
        }
    }
    foreach ($name in @("DISPLAY", "WAYLAND_DISPLAY")) {
        $value = if ($null -ne $Environment) { $Environment[$name] } else {
            [System.Environment]::GetEnvironmentVariable($name)
        }
        if ($value) { return "" }
    }
    return "no display: DISPLAY and WAYLAND_DISPLAY are not set, so Godot cannot open a window. " +
        "Add --headless, or give it a display: xvfb-run -a pwsh ./tools/godot.ps1 ..."
}

function Get-KitMainSceneProblem {
    <#
    Why Godot, started with -GodotArgs, has no game to run, as one line; "" when
    it has one, or when the arguments ask for anything but the game.

    A project without application/run/main_scene starts no game. Godot says so
    in a dialog and waits for a click, with --headless too: Windows always,
    Linux where a display and zenity, kdialog or xmessage are there. Nobody
    clicks in a gate or an agent's shell.

    Only a run this function knows to be the game is judged: no argument, or
    none but the switches below, which leave what runs alone. The editor, a
    --script, a scene, an export, --version and every argument it does not know
    are Godot's to answer.
    #>
    param(
        [string[]]$GodotArgs = @(),
        [string]$ProjectRoot = (Get-KitRoot)
    )
    $plain = @(
        "--headless", "-v", "--verbose", "-q", "--quiet", "--no-header", "-f", "--fullscreen",
        "-m", "--maximized", "-w", "--windowed", "-t", "--always-on-top", "--single-window",
        "-d", "--debug", "--disable-vsync", "--print-fps", "--disable-render-loop",
        "--disable-crash-handler", "--debug-collisions", "--debug-paths", "--debug-navigation", "--quit"
    )
    $valued = @(
        "--display-driver", "--rendering-driver", "--rendering-method", "--audio-driver", "--resolution",
        "--position", "--screen", "--quit-after", "--fixed-fps", "--max-fps", "--time-scale", "--log-file",
        "-l", "--language", "--xr-mode", "--remote-debug"
    )
    for ($index = 0; $index -lt $GodotArgs.Count; $index++) {
        $argument = $GodotArgs[$index]
        if ($argument -ceq "--" -or $argument -ceq "++") { break }
        if ($argument -cin $valued) { $index++; continue }
        if ($argument -cnotin $plain) { return "" }
    }
    if (Get-KitMainScene -ProjectRoot $ProjectRoot) { return "" }
    return "no main scene: project.godot sets no application/run/main_scene, so there is no game to run. " +
        "Set it in the editor (Project Settings > Application > Run > Main Scene), " +
        "or name a scene: pwsh ./tools/godot.ps1 res://main.tscn"
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

function Get-KitGitProblem {
    <#
    Why the git at -GitPath is no git, as one line; "" when it is one, or when
    nothing can be told. macOS only: every Mac has /usr/bin/git, and without
    Apple's Command Line Tools it is a placeholder that runs no git command and
    asks, in a dialog, to install them. `xcode-select -p` exits 0 when they are
    there. Read from Apple's documentation: never run on a Mac.
    -Platform: the system (default: this one). -XcodeSelect: the program to ask.
    #>
    param(
        [string]$GitPath = "",
        [string]$Platform = "",
        [string]$XcodeSelect = "/usr/bin/xcode-select"
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if ($Platform -cne "macos" -or $GitPath -cne "/usr/bin/git") { return "" }
    try {
        $asked = Invoke-KitProcess -FilePath $XcodeSelect -Arguments @("-p") -TimeoutSeconds 30
    } catch {
        return ""   # nobody to ask
    }
    if ($asked.Code -eq 0 -and -not $asked.TimedOut) { return "" }
    return "git is Apple's placeholder (/usr/bin/git): the Command Line Tools are not installed"
}

function Test-KitArgumentLoss {
    # True when PowerShell's call operator (& program ...) would change one of
    # -Arguments on its way to the program. Before 7.3 it hands them over
    # unescaped: a double quote inside one is lost and an empty one is dropped.
    # 7.3 and newer pass every argument whole. -Version: the PowerShell
    # (default: this one).
    param(
        [string[]]$Arguments = @(),
        [version]$Version = $PSVersionTable.PSVersion
    )
    if ($Version -ge [version]"7.3") { return $false }
    foreach ($argument in $Arguments) {
        if ($argument -eq "" -or $argument.Contains('"')) { return $true }
    }
    return $false
}

function Test-KitNotExecutable {
    # True when -Failure, what Invoke-KitProcess threw, is Linux or macOS
    # refusing to start a file this user may not execute (EACCES): a download
    # unpacked by a tool that drops the permission. The system is asked by
    # starting the file; no PowerShell 7.0 call reads the permission itself.
    # Windows has no such permission. tools/ci/godot_binary.py is_executable()
    # is the same question.
    param([Parameter(Mandatory = $true)][System.Exception]$Failure)
    return (Get-KitPlatform) -ne "windows" -and
        $Failure -is [System.ComponentModel.Win32Exception] -and $Failure.NativeErrorCode -eq 13
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
    $version = "$($env:GODOT_VERSION)".Trim()
    if ($version) {
        if ($version -notmatch '^\d+(\.\d+)+$') {
            throw "GODOT_VERSION must be a version like 4.7; it is '$($env:GODOT_VERSION)'."
        }
        return $version
    }
    return (Get-KitConfig).GodotMinor
}

function Get-KitGodotRelease {
    <#
    The official download of Godot -Version for one build (default: this
    machine's), or $null when -Build is "" (Godot publishes none for the system):
      Build        windows_x86_64, linux_x86_64, linux_arm64 or macos_universal
      Tag          what the release's file names end in: win64, linux.x86_64, ...
      Name         Godot_v<version>-stable_<tag>: the folder it is installed in
      Archive      the zip's file name
      Url          where the godotengine/godot release keeps it
      Executable   the program to run, relative to the unzipped archive
      Files        what the archive must hold, relative to it
      ChecksumKey  the kit.json key of its SHA-512
    tools/ci/godot_binary.py RELEASE_TAGS is the same table.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [string]$Build = (Get-KitGodotBuild)
    )
    $tags = @{
        windows_x86_64  = "win64"
        linux_x86_64    = "linux.x86_64"
        linux_arm64     = "linux.arm64"
        macos_universal = "macos.universal"
    }
    if (-not $Build -or -not $tags.ContainsKey($Build)) { return $null }
    $name = "Godot_v$Version-stable_$($tags[$Build])"
    $archive = "$name.zip"
    $executable = $name
    $files = @($name)
    if ($Build -eq "windows_x86_64") {
        $archive = "$name.exe.zip"
        $executable = "${name}_console.exe"
        $files = @("$name.exe", $executable)
    } elseif ($Build -eq "macos_universal") {
        $executable = "Godot.app/Contents/MacOS/Godot"
        $files = @($executable)
    }
    return [PSCustomObject]@{
        Build       = $Build
        Tag         = $tags[$Build]
        Name        = $name
        Archive     = $archive
        Url         = "https://github.com/godotengine/godot/releases/download/$Version-stable/$archive"
        Executable  = $executable
        Files       = $files
        ChecksumKey = "godot.${Build}_sha512"
    }
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
        if (Test-KitNotExecutable $_.Exception) {
            throw "'$Candidate' is not executable. Fix: chmod +x '$Candidate'"
        }
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
    if ($folders[0].Equals($folders[1], (Get-KitPathComparison)) -or
        [System.IO.Path]::GetFileName($folders[1]) -ine ".git") {
        return ""
    }
    return [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetDirectoryName($folders[1])) $prefix)).TrimEnd('\', '/')
}

function Expand-KitSearchRoot {
    # One kit.json godot.search_roots entry with %NAME%, ${NAME} and $NAME
    # replaced by that environment variable (left as written when it is not
    # set) and a leading ~ by the home folder, on every system.
    # tools/ci/godot_binary.py expand_search_root() is the same rule.
    param([string]$Entry = "")
    $expanded = [regex]::Replace($Entry, '%([^%\\/]+)%|\$\{(\w+)\}|\$(\w+)', {
            param($found)
            $name = $found.Groups[1].Value + $found.Groups[2].Value + $found.Groups[3].Value
            $value = [System.Environment]::GetEnvironmentVariable($name)
            if ($value) { $value } else { $found.Value }
        })
    if ($expanded -match '^~(?=$|[\\/])') {
        $expanded = [System.Environment]::GetFolderPath("UserProfile") + $expanded.Substring(1)
    }
    return $expanded
}

function Get-GodotSearchRoots {
    # Folders searched for an unzipped Godot, in order.
    $config = Get-KitConfig
    $roots = @([System.IO.Path]::GetFullPath((Join-Path $config.Root ".tools/godot")))
    $mainProject = Get-KitMainWorktreeProject -ProjectRoot $config.Root
    if ($mainProject) { $roots += [System.IO.Path]::GetFullPath((Join-Path $mainProject ".tools/godot")) }
    foreach ($entry in $config.GodotSearchRoots) {
        $expanded = Expand-KitSearchRoot $entry
        if (-not $expanded) { continue }
        if (-not [System.IO.Path]::IsPathRooted($expanded)) { $expanded = Join-Path $config.Root $expanded }
        $roots += [System.IO.Path]::GetFullPath($expanded)
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::FromComparison((Get-KitPathComparison))
    )
    $unique = @()
    foreach ($root in $roots) {
        if ($seen.Add($root.TrimEnd('\', '/'))) { $unique += $root }
    }
    return $unique
}

function Get-KitNamesDescending {
    # -Items (each has a Name), the last name first. Names compare by the value
    # of their letters, as Python compares them: Sort-Object ignores letter case,
    # and Linux keeps Godot_v... beside godot_v...
    param([object[]]$Items = @())
    $ordered = [System.Collections.Generic.List[object]]::new()
    foreach ($item in $Items) { $ordered.Add($item) }
    $ordered.Sort([System.Comparison[object]] {
            param($left, $right)
            [string]::CompareOrdinal($right.Name, $left.Name)
        })
    return $ordered.ToArray()
}

function Get-GodotCandidatesUnder {
    # Godot executables of -Version under one search root, for one build
    # (default: this machine's): the pinned release first, then the newest
    # patch; on Windows the _console.exe before the plain one.
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Version,
        [string]$Preferred = "",
        [string]$Build = (Get-KitGodotBuild)
    )
    $release = Get-KitGodotRelease -Version $Version -Build $Build
    if ($null -eq $release -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return @() }
    $namePattern = '^Godot_v(?<version>\d+(?:\.\d+)+)-stable_' + [regex]::Escape($release.Tag)
    $entries = @(
        foreach ($entry in (Get-ChildItem -LiteralPath $Root -ErrorAction SilentlyContinue)) {
            if ($entry.Name -notmatch $namePattern) { continue }
            $found = $Matches["version"]
            if ($found -ne $Version -and -not $found.StartsWith("$Version.")) { continue }
            if (Test-IsMonoGodotPath $entry.Name) { continue }
            [PSCustomObject]@{
                Entry   = $entry
                Name    = $entry.Name
                Pinned  = [int]($found -eq $Preferred)
                Version = [version]$found
            }
        }
    )
    # -Stable: equal releases stay in the order of their names.
    $entries = @(Get-KitNamesDescending $entries | Sort-Object -Property Pinned, Version -Descending -Stable)
    $consoleHits = @()
    $plainHits = @()
    foreach ($item in $entries) {
        $entry = $item.Entry
        if ($Build -eq "macos_universal") {
            # Godot.app is a folder; the program is inside it.
            $program = Join-Path $entry.FullName "Godot.app/Contents/MacOS/Godot"
            if ($entry.PSIsContainer -and (Test-Path -LiteralPath $program -PathType Leaf)) {
                $plainHits += [System.IO.Path]::GetFullPath($program)
            }
            continue
        }
        $files = @($entry)
        if ($entry.PSIsContainer) {
            $files = @(Get-KitNamesDescending @(
                    Get-ChildItem -LiteralPath $entry.FullName -File -ErrorAction SilentlyContinue))
        }
        foreach ($file in $files) {
            if ($file.PSIsContainer -or (Test-IsMonoGodotPath $file.Name)) { continue }
            if ($Build -eq "windows_x86_64") {
                if ($file.Name -notmatch $namePattern -or $file.Extension -ine ".exe") { continue }
                if ($file.Name -match '(?i)_console\.exe$') { $consoleHits += $file.FullName }
                else { $plainHits += $file.FullName }
            } elseif ($file.Name -match ($namePattern + '$')) {
                # The whole name: the archive beside it (....zip) is not the program.
                $plainHits += $file.FullName
            }
        }
    }
    if ($Build -eq "macos_universal") {
        $program = Join-Path $Root "Godot.app/Contents/MacOS/Godot"
        if (Test-Path -LiteralPath $program -PathType Leaf) { $plainHits += [System.IO.Path]::GetFullPath($program) }
    }
    return @($consoleHits + $plainHits)
}

function Get-GodotCandidates {
    # Every installed Godot the search roots hold, best first (not yet probed).
    param([string]$Build = (Get-KitGodotBuild))
    $config = Get-KitConfig
    $version = Get-KitGodotVersion
    $candidates = @()
    foreach ($root in (Get-GodotSearchRoots)) {
        $candidates += @(Get-GodotCandidatesUnder -Root $root -Version $version -Preferred $config.GodotVersion `
                -Build $Build)
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

function Get-GodotBundleProgram {
    # macOS: Godot.app is a folder. Given one, answers the program inside it
    # (Contents/MacOS/Godot); any other path comes back unchanged.
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [string]$Platform = ""
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    $bundle = $Candidate.TrimEnd('\', '/')
    if ($Platform -ceq "macos" -and $bundle -match '(?i)\.app$') {
        return (Join-Path $bundle "Contents/MacOS/Godot")
    }
    return $Candidate
}

function Get-GodotNotFoundMessage {
    # What Resolve-GodotPath says when nothing fits: the fix for -Platform first.
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [string]$Platform = "",
        [string]$Architecture = ""
    )
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if (-not $Architecture) { $Architecture = Get-KitArchitecture }
    $pinned = (Get-KitConfig).GodotVersion
    $release = Get-KitGodotRelease -Version $pinned -Build (Get-KitGodotBuild -Platform $Platform -Architecture $Architecture)
    if ($null -eq $release) {
        return "Godot $Version (standard build) not found, and Godot publishes no build for $Platform " +
            "$Architecture. Build or install Godot $Version yourself and set GODOT_BIN to its executable."
    }
    # The file to look for: the pinned release, or a patch of another -Version.
    $file = $release.Name
    if (-not "$pinned.".StartsWith("$Version.", [System.StringComparison]::Ordinal)) {
        $patch = if ($Version.Split('.').Count -lt 3) { ".x" } else { "" }
        $file = "Godot_v${Version}${patch}-stable_$($release.Tag)"
    }
    $named = switch ($Platform) {
        "linux" { " ($file)" }
        "macos" { " (Godot.app, or Godot.app/Contents/MacOS/Godot)" }
        default { "" }
    }
    return "Godot $Version (standard build) not found. Install it with " +
        "'pwsh ./tools/install_godot.ps1', or set GODOT_BIN to a Godot $Version executable$named."
}

function Resolve-GodotPath {
    # -Platform and -Architecture: the system to resolve for (default: this one).
    param([string]$Platform = "", [string]$Architecture = "")
    if (-not $Platform) { $Platform = Get-KitPlatform }
    if (-not $Architecture) { $Architecture = Get-KitArchitecture }
    $build = Get-KitGodotBuild -Platform $Platform -Architecture $Architecture
    $version = Get-KitGodotVersion

    # Blanks around the value are not part of it (cmd keeps the one before `&&`
    # in `set GODOT_BIN=... && ...`); a value of blanks alone names nothing.
    foreach ($override in @(
            [PSCustomObject]@{ Name = "GODOT_BIN"; Value = "$($env:GODOT_BIN)".Trim() },
            [PSCustomObject]@{ Name = "GODOT4_EXE"; Value = "$($env:GODOT4_EXE)".Trim() }
        )) {
        if ($override.Value) {
            $named = Get-GodotBundleProgram -Candidate $override.Value -Platform $Platform
            if (-not (Test-Path -LiteralPath $named -PathType Leaf)) {
                throw "$($override.Name) does not name an executable file: '$($override.Value)'."
            }
            $full = Get-GodotConsoleTwin ([System.IO.Path]::GetFullPath($named))
            $rejection = Get-GodotRejection $full
            if ($rejection) { throw "$($override.Name): $rejection" }
            return $full
        }
    }

    $rejected = @()
    foreach ($candidate in (Get-GodotCandidates -Build $build)) {
        $rejection = Get-GodotRejection $candidate
        if (-not $rejection) { return $candidate }
        $rejected += $rejection
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

    $message = Get-GodotNotFoundMessage -Version $version -Platform $Platform -Architecture $Architecture
    $pinned = (Get-KitConfig).GodotVersion
    $asked = "$($env:GODOT_VERSION)".Trim()
    if ($asked -and -not "$pinned.".StartsWith("$version.", [System.StringComparison]::Ordinal)) {
        $message += "`nGODOT_VERSION asks for $version; kit.json pins $pinned, which is what install_godot.ps1 installs."
    }
    $message += "`nSearched: " + ((Get-GodotSearchRoots) -join "; ") + "; then godot/godot4 on PATH."
    foreach ($rejection in $rejected) { $message += "`nSkipped: $rejection" }
    throw $message
}
