"""Rótulos de frase clave: texto blanco luminoso sobre el plano.

Aparecen solo en las frases de remate que marca el guion —las mismas sobre las
que cae el golpe de sonido— y con el tiempo exacto que da la transcripción de
la locución, así que la palabra entra en pantalla justo cuando se dice.

No se usa `drawtext` de ffmpeg porque no sabe hacer halo. Cada rótulo se dibuja
con la tipografía de marca en un PNG transparente, igual que la miniatura, y se
superpone con una entrada y una salida en fundido. Así el texto del vídeo y el
de la miniatura son el mismo objeto gráfico.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import config
from .thumbnail import _font
from .util import log

# Proporciones pensadas para que el rótulo no compita con el plano.
MAX_WIDTH = 0.78          # del ancho del fotograma
LINE_HEIGHT = 0.085       # alto de línea
CENTER_Y = 0.78           # centro vertical: bajo, para no tapar el motivo
FADE = 0.45               # entrada y salida, en segundos
MAX_CHARS = 42            # por línea antes de partir
MIN_SECONDS = 1.6
MAX_SECONDS = 4.5


def render(phrase: str, dest: Path) -> Path | None:
    """Dibuja el rótulo en un PNG transparente del tamaño del fotograma."""
    texto = " ".join(phrase.split()).strip(" .,;:").upper()
    if len(texto) < 3:
        return None

    lineas = textwrap.wrap(texto, width=MAX_CHARS) or [texto]
    lienzo = Image.new("RGBA", (config.WIDTH, config.HEIGHT), (0, 0, 0, 0))

    # El cuerpo se ajusta a la línea más larga.
    alto = int(config.HEIGHT * LINE_HEIGHT)
    size = alto
    fuente = _font(size)
    mas_larga = max(lineas, key=len)
    while size > 20:
        fuente = _font(size)
        caja = fuente.getbbox(mas_larga)
        if (caja[2] - caja[0]) <= config.WIDTH * MAX_WIDTH:
            break
        size -= 4

    interlineado = int(size * 1.18)
    total = interlineado * len(lineas)
    y = int(config.HEIGHT * CENTER_Y) - total // 2

    capa = ImageDraw.Draw(lienzo)
    for i, linea in enumerate(lineas):
        caja = fuente.getbbox(linea)
        x = (config.WIDTH - (caja[2] - caja[0])) // 2 - caja[0]
        capa.text((x, y + i * interlineado - caja[1]), linea, font=fuente,
                  fill=(255, 255, 255, 255))

    # Halo en dos capas, como en la miniatura: una ancha que despega el texto
    # del plano y otra corta que perfila el trazo.
    halo = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
    for radio, fuerza in ((26, 0.55), (8, 0.75)):
        difuso = lienzo.filter(ImageFilter.GaussianBlur(radio))
        alfa = difuso.split()[-1].point(lambda v: int(v * fuerza))
        capa_halo = Image.new("RGBA", lienzo.size, (255, 255, 255, 0))
        capa_halo.putalpha(alfa)
        halo = Image.alpha_composite(halo, capa_halo)

    final = Image.alpha_composite(halo, lienzo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    final.save(dest, "PNG")
    return dest


def plan_captions(scenes) -> list[tuple[float, float, str]]:
    """(inicio, fin, frase) de cada rótulo, en el reloj del vídeo.

    Se apoya en `sfx._locate`, que casa la frase contra las palabras de la
    transcripción. Si no se puede situar, no se pone rótulo: preferible que
    falte a que aparezca desincronizado.
    """
    from .sfx import _locate

    fuera: list[tuple[float, float, str]] = []
    reloj = 0.0
    for scene in scenes:
        inicio_escena = reloj
        reloj += scene.duration + config.SCENE_GAP

        for frase in (scene.emphasis or [])[:1]:
            dentro = _locate(scene, frase)
            if dentro is None:
                continue
            # Duración proporcional a lo que se tarda en decirla.
            hablado = len(frase.split()) / (config.WORDS_PER_MINUTE / 60)
            largo = min(max(hablado + 0.9, MIN_SECONDS), MAX_SECONDS)
            arranque = inicio_escena + dentro
            if arranque > 0.3:
                fuera.append((arranque, arranque + largo, frase))
    return fuera


def build(scenes, workdir: Path) -> list[tuple[float, float, Path]]:
    """Genera los PNG y devuelve (inicio, fin, ruta) de cada uno."""
    if not config.CAPTIONS_ENABLED:
        return []

    carpeta = workdir / "captions"
    salida: list[tuple[float, float, Path]] = []
    for i, (inicio, fin, frase) in enumerate(plan_captions(scenes)):
        png = carpeta / f"caption_{i:03d}.png"
        if not png.exists() and render(frase, png) is None:
            continue
        if png.exists():
            salida.append((inicio, fin, png))

    if salida:
        log.info("Rótulos de frase clave: %d", len(salida))
    return salida


def overlay_filters(captions: list[tuple[float, float, Path]], entrada: str,
                    primer_indice: int) -> tuple[list[str], list[str], str]:
    """Cadena de superposición para assemble.render().

    Devuelve (entradas de ffmpeg, pasos del filtro, etiqueta de salida).
    """
    entradas: list[str] = []
    pasos: list[str] = []
    actual = entrada

    for i, (inicio, fin, png) in enumerate(captions):
        idx = primer_indice + i
        entradas += ["-loop", "1", "-t", f"{fin - inicio + 0.2:.3f}", "-i", str(png)]
        pasos.append(
            f"[{idx}:v]format=rgba,"
            f"fade=t=in:st=0:d={FADE}:alpha=1,"
            f"fade=t=out:st={max(fin - inicio - FADE, 0.1):.3f}:d={FADE}:alpha=1,"
            f"setpts=PTS-STARTPTS+{inicio:.3f}/TB[cap{i}]"
        )
        etiqueta = f"txt{i}"
        pasos.append(
            f"[{actual}][cap{i}]overlay=0:0:enable='between(t,{inicio:.3f},{fin:.3f})'"
            f"[{etiqueta}]"
        )
        actual = etiqueta

    return entradas, pasos, actual
