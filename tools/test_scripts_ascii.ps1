# Guard: every PowerShell script in the project is BOM-less ASCII.
#
# Why: Windows PowerShell 5.1 reads a script without a BOM as ANSI
# (Windows-1252), so one non-ASCII byte (an em-dash, a smart quote) is
# mis-decoded and the script fails to parse with "Unexpected token" errors far
# from the cause. Pure ASCII parses the same on PowerShell 5.1 and 7, so even
# the wrong interpreter gets as far as the script's "#Requires -Version 7"
# message. A BOM is rejected too: it is what 5.1's `Set-Content -Encoding utf8`
# adds without asking, and other tools then see three stray bytes.
#
# Checked: the project's authored *.ps1 / *.psm1 / *.psd1 (kit.json
# source.exclude applies) and the agent and git hook folders that exist.
#
# Where install.ps1 put the kit into a project (tools/kit_manifest.json exists),
# a script that is not the kit's may also be UTF-8 with a BOM: with the BOM both
# PowerShells read its non-ASCII characters correctly, so the script is valid,
# and how you encode your own files is yours to decide. What fails there is
# what does break: non-ASCII bytes with no BOM, UTF-16, a NUL.
#
#   pwsh ./tools/test_scripts_ascii.ps1
#
# The bytes are checked, not the decoded text: a UTF-16 file (what Windows
# PowerShell 5.1's `>` and Out-File write) decodes to plain ASCII characters
# and would pass a character check.
#
# Exit codes: 0 = all ASCII, 1 = a script has a BOM, a NUL or a non-ASCII byte,
# no scripts were found, or kit.json is unreadable.
#Requires -Version 7.0

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "kit_config.ps1")
$projectRoot = Get-KitRoot
$extensions = @(".ps1", ".psm1", ".psd1")

# Hook folders live in dot-folders, which the authored-source walk skips.
$hookFolders = @(".claude/hooks", ".codex/hooks", ".cursor/hooks", ".githooks", ".github")
try {
    $scripts = @(Get-KitSourceFiles -Extensions $extensions)
    foreach ($folder in $hookFolders) {
        if (Test-Path -LiteralPath (Join-Path $projectRoot $folder) -PathType Container) {
            $scripts += @(Get-KitSourceFiles -Path $folder -Extensions $extensions)
        }
    }
    $kitFiles = @(Get-KitInstalledFiles)
} catch {
    Write-Host "ASCII guard: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
$scripts = @($scripts | Sort-Object FullName -Unique)

if ($scripts.Count -eq 0) {
    Write-Host "No PowerShell scripts found to check." -ForegroundColor Red
    exit 1
}

# Latin-1 maps every byte to the character of the same number, so a regex can
# search the bytes themselves.
$latin1 = [System.Text.Encoding]::GetEncoding(28591)
$strictUtf8 = New-Object System.Text.UTF8Encoding $false, $true
$nonAscii = [regex]::new('[^\x00-\x7F]')
$maxShown = 10

function Get-NonAsciiReport {
    # "line N: U+XXXX" for each non-ASCII character of UTF-8 text; "line N:
    # byte 0xNN" when the bytes are not UTF-8 at all (an ANSI code page).
    param([byte[]]$Bytes, [int]$Offset)
    $count = $Bytes.Length - $Offset
    if (-not $nonAscii.IsMatch($latin1.GetString($Bytes, $Offset, $count))) { return @() }
    try {
        $text = $strictUtf8.GetString($Bytes, $Offset, $count)
        $format = "line {0}: U+{1:X4}"
    } catch {
        $text = $latin1.GetString($Bytes, $Offset, $count)
        $format = "line {0}: byte 0x{1:X2} (not UTF-8)"
    }
    $lines = $text -split "`n"
    $found = @(
        for ($i = 0; $i -lt $lines.Count; $i++) {
            foreach ($hit in $nonAscii.Matches($lines[$i])) { $format -f ($i + 1), [int]$hit.Value[0] }
        }
    )
    if ($found.Count -le $maxShown) { return $found }
    return @($found | Select-Object -First $maxShown) + "... and $($found.Count - $maxShown) more"
}

$failures = 0
$withBom = 0
foreach ($file in $scripts) {
    $relative = ConvertTo-KitRelativePath $file.FullName
    $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
    # In a project the kit was installed into, only the kit's files are held to
    # the kit's own rule (no BOM at all).
    $bomAllowed = $kitFiles.Count -gt 0 -and $kitFiles -notcontains $relative
    $bad = @()
    if ($bytes.Length -ge 2 -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or
            ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) {
        $bad += "UTF-16 BOM present: the file is UTF-16, not ASCII"
    } elseif ([Array]::IndexOf($bytes, [byte]0) -ge 0) {
        $bad += "NUL byte present: not plain text (UTF-16 without a BOM?)"
    } elseif ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        if ($bomAllowed) {
            $withBom++
        } else {
            $bad += "UTF-8 BOM present"
            $bad += @(Get-NonAsciiReport -Bytes $bytes -Offset 3)
        }
    } else {
        $bad += @(Get-NonAsciiReport -Bytes $bytes -Offset 0)
    }
    if ($bad.Count -gt 0) {
        Write-Host "FAIL  $relative" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "        $_" -ForegroundColor Red }
        $failures++
    }
}

if ($failures -gt 0) {
    Write-Host ""
    Write-Host "$failures of $($scripts.Count) script(s) are not BOM-less ASCII." -ForegroundColor Red
    $ownFix = if ($kitFiles.Count -gt 0) { " A script of your own may be UTF-8 with a BOM instead." } else { "" }
    Write-Host "Fix: replace the listed characters (em-dashes, smart quotes) with ASCII; save as UTF-8 without a BOM.$ownFix" `
        -ForegroundColor Red
    exit 1
}
$own = if ($withBom -gt 0) { "; $withBom of your own are UTF-8 with a BOM" } else { "" }
Write-Host "ASCII guard: CLEAN ($($scripts.Count) PowerShell script(s)$own)" -ForegroundColor Green
exit 0
