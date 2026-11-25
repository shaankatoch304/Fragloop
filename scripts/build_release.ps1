# Build the Windows exe, installer and portable zip.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# studio.py owns the version; fail early if the other manifests drift.
$match = Select-String -Path studio.py -Pattern '^APP_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$'
if (-not $match) { throw 'APP_VERSION not found in studio.py' }
$version = $match.Matches[0].Groups[1].Value
foreach ($file in 'version_info.txt', 'installer\Fragloop.iss') {
    if (-not (Select-String -Path $file -Pattern ([regex]::Escape($version)) -Quiet)) {
        throw "$file is not on version $version"
    }
}

Get-Process Fragloop -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

python -m PyInstaller --noconfirm Fragloop.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
Copy-Item ffmpeg.exe, ffprobe.exe dist\Fragloop\ -Force

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
}
if (-not (Test-Path $iscc)) { throw 'ISCC.exe not found. Install Inno Setup 6.' }
& $iscc installer\Fragloop.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }

$stage = "release\Fragloop-$version-win64-portable"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item dist\Fragloop\* $stage -Recurse -Force
Copy-Item README.md, LICENSE $stage -Force

$zip = "release\Fragloop-$version-win64-portable.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$stage\*" -DestinationPath $zip -CompressionLevel Optimal

Write-Host ''
Write-Host 'Release artifacts:'
Get-ChildItem release -Filter "*$version*" |
    Select-Object Name, @{ n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } } |
    Format-Table -AutoSize
