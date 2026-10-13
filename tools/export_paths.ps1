# Godot does not create the folder an export writes into: `--export-release`
# into a missing folder fails. Initialize-GodotExportDirectory creates the
# parent of the output path, taken from the command line or, when none is
# given, from the preset's export_path in export_presets.cfg.
#
# Dot-source this file; it defines functions only.

function Initialize-GodotExportDirectory {
    param([string]$ProjectRoot, [string[]]$GodotArgs)

    for ($exportIndex = 0; $exportIndex -lt $GodotArgs.Count; $exportIndex++) {
        if ($GodotArgs[$exportIndex] -eq "--") { break }
        if ($GodotArgs[$exportIndex] -notin @("--export-release", "--export-debug", "--export-pack")) {
            continue
        }
        if ($exportIndex + 1 -ge $GodotArgs.Count) { return }
        $presetName = $GodotArgs[$exportIndex + 1]
        $outputPath = ""
        if ($exportIndex + 2 -lt $GodotArgs.Count -and
            -not $GodotArgs[$exportIndex + 2].StartsWith("-")) {
            $outputPath = $GodotArgs[$exportIndex + 2]
        } else {
            $presetsFile = Join-Path $ProjectRoot "export_presets.cfg"
            if (-not (Test-Path -LiteralPath $presetsFile)) { return }
            $selectedName = ""
            foreach ($line in [System.IO.File]::ReadAllLines($presetsFile)) {
                if ($line -match '^\[') { $selectedName = "" }
                if ($line -match '^name="(.*)"$') { $selectedName = $Matches[1] }
                if ($selectedName -eq $presetName -and $line -match '^export_path="(.*)"$') {
                    $outputPath = $Matches[1]
                    break
                }
            }
        }
        if ($outputPath -eq "") { return }
        if (-not [System.IO.Path]::IsPathRooted($outputPath)) {
            $outputPath = Join-Path $ProjectRoot $outputPath
        }
        $parentPath = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($outputPath))
        [System.IO.Directory]::CreateDirectory($parentPath) | Out-Null
        return
    }
}
