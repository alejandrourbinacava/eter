# Baja de una vez todo el material Creative Commons del guion del agujero
# negro. Se ejecuta desde la raiz del proyecto:
#
#     powershell -ExecutionPolicy Bypass -File library\descargar.ps1
#
# Los que ya esten bajados se saltan, asi que se puede relanzar sin miedo.
# Salen en .webm cuando el 4K de YouTube viene en VP9 o AV1; el montaje los
# acepta igual que un .mp4.

$ErrorActionPreference = "Continue"

$clips = @(
    @{ id = "TF8THY5spmo"; ruta = "black-hole/stars-orbiting-galactic-center" },
    @{ id = "Rsx0AGQhQvs"; ruta = "lensing/strong-gravitational-lensing-render" },
    @{ id = "tUOJ0mfHGWw"; ruta = "lensing/quasar-gravitational-lensing" },
    @{ id = "bBCArmUPgCw"; ruta = "stellar-collapse/neutron-star-merger-kilonova" },
    @{ id = "6uVNBGgHApU"; ruta = "milky-way/zoom-galactic-center-region" },
    @{ id = "H3KNj8GYeVk"; ruta = "galaxy/spiral-galaxy-m88-pan" },
    @{ id = "Bf6A-FNW2xY"; ruta = "deep-field/hubble-ultra-deep-field-webb" },
    @{ id = "3NeIVjfuKQY"; ruta = "black-hole/supermassive-black-hole-simulation" },
    @{ id = "Zmdcew3g9ME"; ruta = "black-hole/material-orbiting-accretion-disk" },
    @{ id = "1agm33iEAuo"; ruta = "spacetime/warped-spacetime-colliding" },
    @{ id = "I_88S8DWbcU"; ruta = "black-hole/two-black-holes-merging" },
    @{ id = "ljUixb41cvo"; ruta = "event-horizon/event-horizon-telescope" }
)

$total = $clips.Count
$n = 0
$bajados = 0
$saltados = 0

foreach ($c in $clips) {
    $n++
    $destino = "library/" + $c.ruta

    # Si ya existe con cualquier extension, no se vuelve a bajar.
    $carpeta = Split-Path $destino -Parent
    $nombre = Split-Path $destino -Leaf
    $ya = @()
    if (Test-Path $carpeta) {
        $ya = Get-ChildItem $carpeta -File | Where-Object { $_.BaseName -eq $nombre }
    }
    if ($ya.Count -gt 0) {
        Write-Host "[$n/$total] ya lo tienes: $nombre" -ForegroundColor DarkGray
        $saltados++
        continue
    }

    Write-Host "[$n/$total] bajando $nombre ..." -ForegroundColor Cyan
    yt-dlp -q --no-warnings -f "bv*[height<=2160]+ba/bv*[height<=2160]/b" `
        -o "$destino.%(ext)s" "https://youtu.be/$($c.id)"
    if (-not $?) {
        # YouTube devuelve 403 tras varias descargas seguidas. Se esquiva
        # pidiendole a yt-dlp que se identifique como la app de Android, que
        # usa otra ruta de servidores. A cambio no ofrece los formatos mas
        # altos, asi que este intento se conforma con 1080p.
        Write-Host "     403; reintento como cliente Android" -ForegroundColor Yellow
        yt-dlp -q --no-warnings --extractor-args "youtube:player_client=android" `
            -f "bv*[height<=1080]+ba/b[height<=1080]" `
            -o "$destino.%(ext)s" "https://youtu.be/$($c.id)"
    }
    if ($?) { $bajados++ } else {
        Write-Host "     fallo con $($c.id)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Listo: $bajados bajados, $saltados ya estaban." -ForegroundColor Green
Write-Host "Comprueba que el montaje los ve con:" -ForegroundColor Green
Write-Host "  python -c ""import sys;sys.path.insert(0,'.');from pipeline import visuals;print(len(visuals.library_index()),'clips indexados')"""
