# Installs the Godot build pinned in kit.json into .tools/godot/.
#
# Usage:
#   pwsh ./tools/install_godot.ps1           # install; does nothing when already there
#   pwsh ./tools/install_godot.ps1 -Force    # download and install again
#   pwsh ./tools/install_godot.ps1 -Source ~/Downloads/Godot_v4.7.2-stable_linux.x86_64.zip
#
# What it does:
# - Takes the version from kit.json godot.version and downloads the official
#   standard build for this machine from the godotengine/godot GitHub release:
#     Windows x86_64   Godot_v<version>-stable_win64.exe.zip
#     Linux x86_64     Godot_v<version>-stable_linux.x86_64.zip
#     Linux arm64      Godot_v<version>-stable_linux.arm64.zip
#     macOS            Godot_v<version>-stable_macos.universal.zip
#   -Source names another URL or a local zip (a mirror, an offline machine).
# - Checks the archive's SHA-512 against kit.json godot.<build>_sha512 (build:
#   windows_x86_64, linux_x86_64, linux_arm64, macos_universal) before anything
#   is unpacked. A mismatch is refused, and so is an empty pin: nothing
#   unverified is installed unless -SkipChecksum says so.
# - Extracts to .tools/godot/Godot_v<version>-stable_<tag>/ (gitignored), the
#   first place tools/godot_resolver.ps1 looks after GODOT_BIN and GODOT4_EXE,
#   makes the program executable (Linux, macOS), and runs its --version: an
#   install that does not answer with the pinned version is removed again.
#
# -Platform (windows, linux, macos) and -Architecture (x86_64, arm64) name
# another system's download. The program must still run on this machine.
#
# To move to another Godot version: change godot.version and the four SHA-512
# values (from the release's SHA512-SUMS.txt) in kit.json, then run this again.
#
# Exit codes: 0 = installed or already installed, 1 = refused or failed.
#Requires -Version 7.0

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipChecksum,
    [string]$Source = "",
    [string]$Platform = "",
    [string]$Architecture = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # the progress bar slows a download many times over
. (Join-Path $PSScriptRoot "godot_resolver.ps1")

function Stop-Install {
    param([string]$Message)
    Write-Host "install_godot: $Message" -ForegroundColor Red
    exit 1
}

function Test-InstalledGodot {
    # True when the binary runs and reports exactly -Version.
    param([string]$Executable, [string]$Version)
    try {
        $probe = Get-GodotVersionText $Executable
    } catch {
        return $false
    }
    return $probe.Code -eq 0 -and $probe.Text.StartsWith("$Version.", [System.StringComparison]::Ordinal)
}

function Find-ArchivePayload {
    # The folder under -Staging that holds every one of -Files (paths relative
    # to it), or "" when there is none. The official archives hold them at
    # their top level.
    param([string]$Staging, [string[]]$Files)
    $leaf = [System.IO.Path]::GetFileName($Files[0])
    $depth = $Files[0].Split('/').Count
    foreach ($hit in (Get-ChildItem -LiteralPath $Staging -Recurse -File | Where-Object { $_.Name -ceq $leaf })) {
        $folder = $hit.FullName
        for ($step = 0; $step -lt $depth; $step++) { $folder = [System.IO.Path]::GetDirectoryName($folder) }
        $missing = @($Files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $folder $_) -PathType Leaf) })
        if ($missing.Count -eq 0) { return $folder }
    }
    return ""
}

function Set-KitExecutable {
    # Linux, macOS: the owner, the group and everyone else may execute -FilePath.
    # An unzip that does not carry the permission over leaves a program that
    # cannot start. Done by the system's chmod: PowerShell 7.0 has no call for it.
    param([string]$FilePath)
    if ((Get-KitPlatform) -eq "windows") { return }
    $chmod = Get-Command chmod -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $program = if ($chmod) { $chmod.Source } else {
        # A PATH without the system's folders.
        @("/bin/chmod", "/usr/bin/chmod") | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
    }
    if (-not $program) {
        Stop-Install "chmod not found, so '$FilePath' cannot be made executable; nothing was installed."
    }
    $run = Invoke-KitProcess -FilePath $program -Arguments @("a+x", $FilePath) -TimeoutSeconds 30
    if ($run.Code -ne 0) {
        Stop-Install "chmod a+x '$FilePath' failed ($($run.Text)); nothing was installed."
    }
}

# Before anything is downloaded: PowerShell 7.0 and 7.1 abort at the checksum
# where only OpenSSL 3 is installed, and no script can catch that.
$refusal = Get-KitPowerShellRefusal
if ($refusal) { Stop-Install $refusal }

try {
    $config = Get-KitConfig
} catch {
    Stop-Install $_.Exception.Message
}
$version = $config.GodotVersion
if ($version -notmatch '^\d+\.\d+(\.\d+)?$') {
    Stop-Install "kit.json godot.version '$version' is not a release version like 4.7.2."
}
if (-not $Platform) { $Platform = Get-KitPlatform }
if (-not $Architecture) { $Architecture = Get-KitArchitecture }
if ($Platform -cnotin @("windows", "linux", "macos")) {
    Stop-Install "-Platform must be windows, linux or macos; it is '$Platform'."
}
$release = Get-KitGodotRelease -Version $version -Build (Get-KitGodotBuild -Platform $Platform -Architecture $Architecture)
if ($null -eq $release) {
    Stop-Install ("Godot publishes no build for $Platform $Architecture. Build or install Godot $version " +
        "yourself and set GODOT_BIN to its executable.")
}

$name = $release.Name
$archiveName = $release.Archive
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $config.Root ".tools/godot"))
$destination = Join-Path $installRoot $name
$program = [System.IO.Path]::GetFullPath((Join-Path $destination $release.Executable))
$holds = $release.Files -join " and "

$present = @($release.Files | Where-Object { Test-Path -LiteralPath (Join-Path $destination $_) -PathType Leaf })
if (-not $Force -and $present.Count -eq $release.Files.Count) {
    if (Test-InstalledGodot -Executable $program -Version $version) {
        Write-Host "Godot $version is already installed: $program" -ForegroundColor Green
        exit 0
    }
    Write-Host "The Godot in '$destination' does not run as $version; installing it again." -ForegroundColor Yellow
}

$pin = "kit.json $($release.ChecksumKey)"
$expected = $config.GodotSha512[$release.Build].Trim().ToLowerInvariant()
if ($SkipChecksum) {
    Write-Host "WARNING: -SkipChecksum: the archive will be installed without verification." -ForegroundColor Yellow
} elseif (-not $expected) {
    Stop-Install ("$pin is empty, so the download cannot be verified. " +
        "Add the SHA-512 of $archiveName (the release's SHA512-SUMS.txt lists it), " +
        "or pass -SkipChecksum to install it unverified.")
} elseif ($expected -notmatch '^[0-9a-f]{128}$') {
    Stop-Install "$pin is not a SHA-512 (128 hex digits)."
}

if (-not $Source) { $Source = $release.Url }

# Everything happens in a scratch folder next to the destination; the install
# appears only when the verified archive is fully extracted.
$work = Join-Path $installRoot ("_install_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[System.IO.Directory]::CreateDirectory($work) | Out-Null
try {
    $archive = Join-Path $work $archiveName
    if ($Source -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]+://') {
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
            Stop-Install "-Source is neither a URL nor an existing file: '$Source'."
        }
        Write-Host "Copying $Source ..." -ForegroundColor Cyan
        Copy-Item -LiteralPath $Source -Destination $archive
    } else {
        Write-Host "Downloading $Source ..." -ForegroundColor Cyan
        try {
            Invoke-WebRequest -Uri $Source -OutFile $archive -MaximumRetryCount 3 -RetryIntervalSec 2
        } catch {
            Stop-Install "download failed: $($_.Exception.Message)"
        }
    }
    $megabytes = [Math]::Round((Get-Item -LiteralPath $archive).Length / 1MB, 1)
    Write-Host "  $megabytes MB" -ForegroundColor DarkGray

    if (-not $SkipChecksum) {
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            Stop-Install ("SHA-512 mismatch for $archiveName; nothing was installed.`n" +
                "  expected ($pin): $expected`n" +
                "  actual: $actual")
        }
        Write-Host "  SHA-512 matches kit.json." -ForegroundColor DarkGray
    }

    # Extract entry by entry: no entry may land outside the staging folder.
    $staging = Join-Path $work "staging"
    [System.IO.Directory]::CreateDirectory($staging) | Out-Null
    $stagingPrefix = $staging.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    try {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    } catch {
        Stop-Install "'$archiveName' is not a readable zip archive: $($_.Exception.Message)"
    }
    try {
        foreach ($entry in $zip.Entries) {
            if (-not $entry.Name) { continue }  # a folder entry
            $target = [System.IO.Path]::GetFullPath((Join-Path $staging $entry.FullName))
            if (-not $target.StartsWith($stagingPrefix, (Get-KitPathComparison))) {
                Stop-Install "archive entry '$($entry.FullName)' points outside the install folder; nothing was installed."
            }
            [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target)) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally {
        $zip.Dispose()
    }

    $payload = Find-ArchivePayload -Staging $staging -Files $release.Files
    if (-not $payload) {
        Stop-Install "the archive does not hold $holds; nothing was installed."
    }
    Set-KitExecutable (Join-Path $payload $release.Executable)

    # Rename the old install aside first: a rename fails as a whole when that
    # Godot is still running, where a delete would stop halfway.
    $retired = ""
    if (Test-Path -LiteralPath $destination) {
        $retired = Join-Path $work "previous"
        try {
            Move-Item -LiteralPath $destination -Destination $retired
        } catch {
            Stop-Install "could not replace '$destination' (is that Godot running?): $($_.Exception.Message)"
        }
    }
    try {
        Move-Item -LiteralPath $payload -Destination $destination
    } catch {
        if ($retired) { Move-Item -LiteralPath $retired -Destination $destination }
        Stop-Install "could not move the new install into '$destination': $($_.Exception.Message)"
    }

    if (-not (Test-InstalledGodot -Executable $program -Version $version)) {
        # Leave no broken install behind; put the previous one back.
        Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
        if ($retired) { Move-Item -LiteralPath $retired -Destination $destination }
        Stop-Install "the archive's $($release.Executable) does not run as Godot $version here; nothing was installed."
    }
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Godot $version installed: $program" -ForegroundColor Green
foreach ($override in @("GODOT_BIN", "GODOT4_EXE")) {
    $value = [System.Environment]::GetEnvironmentVariable($override)
    if ($value) {
        Write-Host "Note: $override is set ('$value') and wins over this install." -ForegroundColor Yellow
    }
}
exit 0
