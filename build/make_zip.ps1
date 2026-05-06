# Package dist/Arc2Zen/ into a single zip a user can drop anywhere.
# The zip contains the Arc2Zen/ folder at the top level so an extracted
# folder is self-contained.
$ErrorActionPreference = 'Stop'

$root = Resolve-Path "$PSScriptRoot/.."
Push-Location $root
try {
    $version = $env:VERSION
    if (-not $version) { $version = '1.0.0' }
    $zipName = "Arc2Zen-$version-win-x64.zip"
    $zipPath = Join-Path 'dist' $zipName

    if (-not (Test-Path 'dist/Arc2Zen')) {
        throw 'dist/Arc2Zen not found; run build/make_exe.ps1 first.'
    }

    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    # Ship the INSTRUCTIONS.txt next to the .exe so users see it as soon
    # as they extract.
    if (Test-Path 'build/INSTRUCTIONS.txt') {
        Copy-Item 'build/INSTRUCTIONS.txt' 'dist/Arc2Zen/INSTRUCTIONS.txt' -Force
    }

    Write-Host '==> Compress-Archive'
    Compress-Archive -Path 'dist/Arc2Zen' -DestinationPath $zipPath -CompressionLevel Optimal

    $bytes = (Get-Item $zipPath).Length
    Write-Host ("==> done: $zipPath ({0:N1} MB)" -f ($bytes / 1MB))
} finally {
    Pop-Location
}
