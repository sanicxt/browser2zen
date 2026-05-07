# Build dist/browser2zen/ from app/ + src/ + the PyInstaller spec.
# Outputs a folder containing browser2zen.exe and its _internal/ runtime
# (the ship artifact). No code-signing.
$ErrorActionPreference = 'Stop'

$root = Resolve-Path "$PSScriptRoot/.."
Push-Location $root
try {
    if (-not (Test-Path 'app/assets/icon.ico')) {
        Write-Host '==> generating icon.ico'
        & pwsh -NoProfile -File 'build/make_iconset.ps1'
    }

    Write-Host '==> pyinstaller'
    & pyinstaller --noconfirm --clean 'build/browser2zen.spec'
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

    if (-not (Test-Path 'dist/browser2zen/browser2zen.exe')) {
        throw "expected dist/browser2zen/browser2zen.exe to exist"
    }

    $size = (Get-ChildItem -Recurse 'dist/browser2zen' | Measure-Object -Property Length -Sum).Sum
    Write-Host ("==> done: dist/browser2zen/browser2zen.exe ({0:N1} MB unpacked)" -f ($size / 1MB))
} finally {
    Pop-Location
}
