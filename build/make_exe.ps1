# Build dist/Arc2Zen/ from app/ + src/ + the PyInstaller spec.
# Outputs a folder containing Arc2Zen.exe and its _internal/ runtime
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
    & pyinstaller --noconfirm --clean 'build/arc2zen.spec'
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

    if (-not (Test-Path 'dist/Arc2Zen/Arc2Zen.exe')) {
        throw "expected dist/Arc2Zen/Arc2Zen.exe to exist"
    }

    $size = (Get-ChildItem -Recurse 'dist/Arc2Zen' | Measure-Object -Property Length -Sum).Sum
    Write-Host ("==> done: dist/Arc2Zen/Arc2Zen.exe ({0:N1} MB unpacked)" -f ($size / 1MB))
} finally {
    Pop-Location
}
