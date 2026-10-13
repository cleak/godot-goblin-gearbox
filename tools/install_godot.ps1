# Installs the Godot build pinned in kit.json into .tools/godot/.
#
# Usage:
#   pwsh ./tools/install_godot.ps1           # install; does nothing when already there
#   pwsh ./tools/install_godot.ps1 -Force    # download and install again
#   pwsh ./tools/install_godot.ps1 -Source D:\downloads\Godot_v4.7.2-stable_win64.exe.zip
#
# What it does:
# - Takes the version from kit.json godot.version and downloads the official
#   Windows x86_64 standard build from the godotengine/godot GitHub release.
#   -Source names another URL or a local zip (a mirror, an offline machine).
# - Checks the archive's SHA-512 against kit.json godot.windows_x86_64_sha512.
#   A mismatch is refused, and so is an empty pin: nothing unverified is
#   installed unless -SkipChecksum says so.
# - Extracts to .tools/godot/Godot_v<version>-stable_win64/ (gitignored), the
#   first place tools/godot_resolver.ps1 looks after GODOT_BIN and GODOT4_EXE.
#
# To move to another Godot version: change godot.version and the SHA-512 (from
# the release's SHA512-SUMS.txt) in kit.json, then run this again.
#
# Exit codes: 0 = installed or already installed, 1 = refused or failed.
#Requires -Version 7.0

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipChecksum,
    [string]$Source = ""
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

try {
    $config = Get-KitConfig
} catch {
    Stop-Install $_.Exception.Message
}
$version = $config.GodotVersion
if ($version -notmatch '^\d+\.\d+(\.\d+)?$') {
    Stop-Install "kit.json godot.version '$version' is not a release version like 4.7.2."
}
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    Stop-Install "only the Windows x86_64 build is installed by this script. Install Godot $version yourself and set GODOT_BIN."
}

$name = "Godot_v$version-stable_win64"
$archiveName = "$name.exe.zip"
$installRoot = [System.IO.Path]::GetFullPath((Join-Path $config.Root ".tools/godot"))
$destination = Join-Path $installRoot $name
$console = Join-Path $destination "${name}_console.exe"
$editor = Join-Path $destination "$name.exe"

if (-not $Force -and (Test-Path -LiteralPath $console -PathType Leaf) -and
    (Test-Path -LiteralPath $editor -PathType Leaf)) {
    if (Test-InstalledGodot -Executable $console -Version $version) {
        Write-Host "Godot $version is already installed: $console" -ForegroundColor Green
        exit 0
    }
    Write-Host "The Godot in '$destination' does not run as $version; installing it again." -ForegroundColor Yellow
}

$expected = $config.GodotSha512.Trim().ToLowerInvariant()
if ($SkipChecksum) {
    Write-Host "WARNING: -SkipChecksum: the archive will be installed without verification." -ForegroundColor Yellow
} elseif (-not $expected) {
    Stop-Install ("kit.json godot.windows_x86_64_sha512 is empty, so the download cannot be verified. " +
        "Add the SHA-512 of $archiveName (the release's SHA512-SUMS.txt lists it), " +
        "or pass -SkipChecksum to install it unverified.")
} elseif ($expected -notmatch '^[0-9a-f]{128}$') {
    Stop-Install "kit.json godot.windows_x86_64_sha512 is not a SHA-512 (128 hex digits)."
}

if (-not $Source) {
    $Source = "https://github.com/godotengine/godot/releases/download/$version-stable/$archiveName"
}

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
                "  expected (kit.json): $expected`n" +
                "  actual:              $actual")
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
            if (-not $target.StartsWith($stagingPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                Stop-Install "archive entry '$($entry.FullName)' points outside the install folder; nothing was installed."
            }
            [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target)) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally {
        $zip.Dispose()
    }

    # The official archive holds the two executables at its top level.
    $found = Get-ChildItem -LiteralPath $staging -Recurse -File -Filter "${name}_console.exe" | Select-Object -First 1
    if (-not $found -or -not (Test-Path -LiteralPath (Join-Path $found.DirectoryName "$name.exe") -PathType Leaf)) {
        Stop-Install "the archive does not hold $name.exe and ${name}_console.exe; nothing was installed."
    }
    $payload = $found.DirectoryName

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

    if (-not (Test-InstalledGodot -Executable $console -Version $version)) {
        # Leave no broken install behind; put the previous one back.
        Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
        if ($retired) { Move-Item -LiteralPath $retired -Destination $destination }
        Stop-Install "the archive's ${name}_console.exe does not run as Godot $version; nothing was installed."
    }
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Godot $version installed: $console" -ForegroundColor Green
foreach ($override in @("GODOT_BIN", "GODOT4_EXE")) {
    $value = [System.Environment]::GetEnvironmentVariable($override)
    if ($value) {
        Write-Host "Note: $override is set ('$value') and wins over this install." -ForegroundColor Yellow
    }
}
exit 0
