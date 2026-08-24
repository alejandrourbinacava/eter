"""Miniatura con la plantilla de Éter.

Fondo negro. El objeto del vídeo a la derecha, fundido a negro hacia el centro.
A la izquierda, una sola palabra en mayúsculas, condensada, muy pesada, blanca
y con halo. Reproduce las miniaturas `DIFÍCIL` y `PROHIBIDO` del canal.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config
from .util import download, log

W, H = 1280, 720

FONT_DIR = config.BRAND_DIR / "fonts"
FONT_FILE = FONT_DIR / "Anton-Regular.ttf"
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"


def _font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_FILE.exists():
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        log.info("Descargando la tipografía de marca (Anton, OFL)")
        download(FONT_URL, FONT_FILE)
    return ImageFont.truetype(str(FONT_FILE), size)


def build(word: str, hero: Path | None, dest: Path) -> Path:
    canvas = Image.new("RGB", (W, H), (0, 0, 0))

    if hero and Path(hero).exists():
        canvas.paste(_hero_layer(Path(hero)), (0, 0), _hero_layer(Path(hero)).split()[-1])

    _draw_word(canvas, word.upper())

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=92, optimize=True)
    size_kb = dest.stat().st_size / 1024
    if size_kb > 1900:  # YouTube rechaza por encima de 2 MB
        canvas.save(dest, "JPEG", quality=82, optimize=True)
    log.info("Miniatura: %s (%s, %.0f KB)", dest.name, word.upper(), dest.stat().st_size / 1024)
    return dest


def _hero_layer(hero: Path) -> Image.Image:
    """La imagen ocupa el 62 % derecho y se disuelve en negro hacia el centro."""
    img = Image.open(hero).convert("RGB")

    target_w = int(W * 0.68)
    ratio = max(target_w / img.width, H / img.height)
    img = img.resize((max(int(img.width * ratio), target_w), max(int(img.height * ratio), H)),
                     Image.LANCZOS)

    left = max((img.width - target_w) // 2, 0)
    top = max((img.height - H) // 2, 0)
    img = img.crop((left, top, left + target_w, top + H))

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.paste(img, (W - target_w, 0))

    # Máscara: opaca a la derecha, transparente en el tercio izquierdo. El
    # degradado es largo a propósito; si es corto se ve la costura vertical
    # cuando el material es claro en el borde.
    ramp = Image.new("L", (W, 1), 0)
    rp = ramp.load()
    fade_start, fade_end = int(W * 0.20), int(W * 0.66)
    for x in range(W):
        if x <= fade_start:
            value = 0
        elif x >= fade_end:
            value = 255
        else:
            t = (x - fade_start) / (fade_end - fade_start)
            value = int(255 * (t ** 2 * (3 - 2 * t)) ** 1.15)  # smoothstep sesgado
        rp[x, 0] = value
    mask = ramp.resize((W, H))

    alpha = layer.split()[-1].point(lambda v: 255 if v else 0)
    layer.putalpha(Image.composite(mask, Image.new("L", (W, H), 0), alpha))
    return layer


def _draw_word(canvas: Image.Image, word: str) -> None:
    margin = 46
    max_width = int(W * 0.60)
    max_height = int(H * 0.30)

    size = 260
    font = _font(size)
    while size > 60:
        font = _font(size)
        box = font.getbbox(word)
        if (box[2] - box[0]) <= max_width and (box[3] - box[1]) <= max_height:
            break
        size -= 6

    box = font.getbbox(word)
    x = margin - box[0]
    y = int(H * 0.40) - (box[3] - box[1]) // 2 - box[1]

    # Halo: dos capas desenfocadas, como en las miniaturas del canal.
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.text((x, y), word, font=font, fill=(255, 255, 255, 190))
    canvas.paste(
        (255, 255, 255),
        (0, 0),
        glow.filter(ImageFilter.GaussianBlur(26)).split()[-1].point(lambda v: int(v * 0.55)),
    )
    canvas.paste(
        (255, 255, 255),
        (0, 0),
        glow.filter(ImageFilter.GaussianBlur(9)).split()[-1].point(lambda v: int(v * 0.75)),
    )

    ImageDraw.Draw(canvas).text((x, y), word, font=font, fill=(255, 255, 255))


def hero_frame(video: Path, dest: Path, at: float = 12.0) -> Path:
    """Si no hay una imagen mejor, se saca un fotograma del propio vídeo."""
    from .util import ffmpeg

    ffmpeg(["-ss", str(at), "-i", str(video), "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest
