# Material Creative Commons para el guion de Saturno.
#
#     powershell -ExecutionPolicy Bypass -File library\descargar-saturno.ps1
#
# Se ejecuta desde la raiz del proyecto. Salta lo que ya este bajado, asi que
# se puede relanzar sin miedo.
#
# Resolucion verificada uno a uno: %(height)s a secas devuelve la del formato
# por defecto, no la maxima, asi que se comprobo con la lista completa de
# formatos. Se descarto todo lo de NASA Solar System sobre Cassini porque esta
# en 720p, y uno de Titan que estaba en 360p.

$ErrorActionPreference = "Continue"

$clips = @(
    # 4K
    @{ id = "EF6mID4Z2HM"; ruta = "saturn/saturn-hubble-webb-2024" },
    @{ id = "SffvtXszZMM"; ruta = "saturn/hubble-spoke-season-pan" },
    @{ id = "LStuzpCOcK0"; ruta = "planets/hubble-grand-tour-solar-system" },
    @{ id = "Lr0v-I9qf-w"; ruta = "enceladus/enceladus-plume-torus" },
    @{ id = "gva1wHsOhok"; ruta = "planets/white-dwarf-planet-system" },
    # 1080p
    @{ id = "Sq-LSi5Y6H4"; ruta = "saturn/pan-over-saturn" },
    @{ id = "80gQBqg7-Fg"; ruta = "saturn/saturn-moon-animation" },
    @{ id = "utddQq6tIEw"; ruta = "planets/jupiter-global-map" },
    @{ id = "CQjZf2bW9XQ"; ruta = "planets/amazing-moons-sciencecast" },
    @{ id = "MXqwRYaa1qA"; ruta = "sun/sun-becoming-red-giant" }
)

$total = $clips.Count
$n = 0; $bajados = 0; $saltados = 0

foreach ($c in $clips) {
    $n++
    $destino = "library/" + $c.ruta
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
        # YouTube devuelve 403 tras varias descargas seguidas. Identificarse
        # como la app de Android sale por otra ruta de servidores; a cambio ese
        # cliente no ofrece los formatos mas altos y hay que conformarse con
        # 1080p.
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
