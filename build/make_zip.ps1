# Package dist/browser2zen/ into a single zip a user can drop anywhere.
# The zip contains the browser2zen/ folder at the top level so an extracted
# folder is self-contained.
$ErrorActionPreference = 'Stop'

$root = Resolve-Path "$PSScriptRoot/.."
Push-Location $root
try {
    $version = $env:VERSION
    if (-not $version) { $version = '1.0.0' }
    $zipName = "browser2zen-$version-win-x64.zip"
    $zipPath = Join-Path 'dist' $zipName

    if (-not (Test-Path 'dist/browser2zen')) {
        throw 'dist/browser2zen not found; run build/make_exe.ps1 first.'
    }

    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    # Ship the INSTRUCTIONS.txt next to the .exe so users see it as soon
    # as they extract.
    if (Test-Path 'build/INSTRUCTIONS.txt') {
        Copy-Item 'build/INSTRUCTIONS.txt' 'dist/browser2zen/INSTRUCTIONS.txt' -Force
    }

    Write-Host '==> Compress-Archive'
    Compress-Archive -Path 'dist/browser2zen' -DestinationPath $zipPath -CompressionLevel Optimal

    $bytes = (Get-Item $zipPath).Length
    Write-Host ("==> done: $zipPath ({0:N1} MB)" -f ($bytes / 1MB))
} finally {
    Pop-Location
}
