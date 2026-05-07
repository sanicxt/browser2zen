# Render app/assets/icon.svg into app/assets/icon.ico via ImageMagick.
# ImageMagick ships preinstalled on the GitHub Actions windows-latest
# runner. For local builds, install via `winget install ImageMagick.ImageMagick`
# or `choco install imagemagick`.
$ErrorActionPreference = 'Stop'

$root = Resolve-Path "$PSScriptRoot/.."
$svg  = Join-Path $root 'app/assets/icon.svg'
$ico  = Join-Path $root 'app/assets/icon.ico'

if (-not (Test-Path $svg)) {
    throw "icon.svg not found at $svg"
}

# Multi-resolution .ico (16/24/32/48/64/128/256). Windows picks the best
# size at runtime depending on context (taskbar, alt-tab, file icon).
$tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "browser2zen-iconset-$([guid]::NewGuid())")
try {
    $sizes = 16, 24, 32, 48, 64, 128, 256
    $pngs = @()
    foreach ($s in $sizes) {
        $out = Join-Path $tmp "icon-$s.png"
        & magick -density 600 -background none $svg -resize "${s}x${s}" $out
        if ($LASTEXITCODE -ne 0) { throw "ImageMagick rasterise to ${s}x${s} failed." }
        $pngs += $out
    }
    & magick @pngs $ico
    if ($LASTEXITCODE -ne 0) { throw "ImageMagick .ico assembly failed." }
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host ("wrote {0} ({1:N0} bytes)" -f $ico, (Get-Item $ico).Length)
