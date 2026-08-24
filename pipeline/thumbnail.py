"""Miniatura con la plantilla de Éter.

Medido sobre las cuatro miniaturas publicadas (PROHIBIDO, DIFÍCIL, IMPOSIBLE,
INEXPLICABLE), la plantilla es:

  Imagen a sangre        Ocupa el fotograma entero. NO hay banda negra a la
                         izquierda: el texto se superpone al motivo. La imagen
                         ya es oscura y cae a negro por los bordes, y ese
                         viñeteado es lo que deja respirar a la tipografía.

  Una sola palabra       Mayúsculas, condensada muy pesada, blanco puro con un
                         halo suave. Ocupa entre el 64 % y el 74 % del ancho y
                         entre el 23 % y el 41 % del alto.

  Colocación             Centro vertical en torno al 47 %. En horizontal, el
                         texto va DONDE NO ESTÁ EL MOTIVO: en PROHIBIDO y
                         DIFÍCIL el objeto está a la derecha y la palabra a la
                         izquierda; en INEXPLICABLE la antena está abajo a la
                         derecha. La excepción es cuando el motivo llena el
                         cuadro, como la galaxia de IMPOSIBLE: ahí se centra
                         encima. Eso lo decide `_subject_side` midiendo el
                         centro de masa del brillo.

Ese es el motivo de que aquí no se recorte ni se funda la imagen: la versión
anterior dejaba un tercio izquierdo en negro plano y se veía a la legua que no
era la misma plantilla.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import config
from .util import download, log

W, H = 1280, 720

# Proporciones medidas sobre las miniaturas reales del canal.
TEXT_MAX_WIDTH = 0.86      # tope de ancho antes de reducir el cuerpo
TEXT_HEIGHT = 0.30         # altura objetivo de la caja de texto
TEXT_CENTER_Y = 0.47       # centro vertical
TEXT_MARGIN = 0.042        # margen izquierdo de las palabras cortas
CENTER_ABOVE = 0.62        # por encima de este ancho, se centra

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
    canvas = _background(hero)
    _draw_word(canvas, word.upper())

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=92, optimize=True)
    if dest.stat().st_size > 1_900_000:  # YouTube rechaza por encima de 2 MB
        canvas.save(dest, "JPEG", quality=82, optimize=True)
    log.info("Miniatura: %s (%s, %.0f KB)", dest.name, word.upper(),
             dest.stat().st_size / 1024)
    return dest


def _background(hero: Path | None) -> Image.Image:
    """La imagen a sangre, oscurecida y viñeteada para que el texto lea."""
    if not hero or not Path(hero).exists():
        return Image.new("RGB", (W, H), (4, 6, 12))

    img = Image.open(hero).convert("RGB")

    # Encuadre de cobertura: llenar 16:9 recortando el sobrante.
    ratio = max(W / img.width, H / img.height)
    img = img.resize((max(int(img.width * ratio), W), max(int(img.height * ratio), H)),
                     Image.LANCZOS)
    left = max((img.width - W) // 2, 0)
    top = max((img.height - H) // 2, 0)
    img = img.crop((left, top, left + W, top + H))

    # El material de archivo suele venir más claro y más plano que los renders
    # del canal. Un punto de contraste y algo menos de luz lo acercan.
    img = ImageEnhance.Contrast(img).enhance(1.18)
    img = ImageEnhance.Brightness(img).enhance(0.82)
    img = ImageEnhance.Color(img).enhance(1.10)

    return Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), _vignette())


def _vignette() -> Image.Image:
    """Máscara radial: opaca en el centro, negra en los bordes."""
    mask = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(mask)
    # Una elipse generosa desenfocada da una caída suave y sin bandas.
    draw.ellipse(
        (-int(W * 0.16), -int(H * 0.24), int(W * 1.16), int(H * 1.24)),
        fill=255,
    )
    return mask.filter(ImageFilter.GaussianBlur(int(W * 0.10)))


def _fit(word: str) -> tuple[ImageFont.FreeTypeFont, tuple[int, int, int, int]]:
    """El cuerpo más grande que respeta el alto objetivo y el ancho máximo."""
    max_w = int(W * TEXT_MAX_WIDTH)
    target_h = int(H * TEXT_HEIGHT)

    size = 40
    best = _font(size)
    while size < 400:
        probe = _font(size + 4)
        box = probe.getbbox(word)
        if (box[2] - box[0]) > max_w or (box[3] - box[1]) > target_h:
            break
        size += 4
        best = probe
    return best, best.getbbox(word)


# Umbrales del centro de masa del brillo, calibrados sobre las cuatro
# miniaturas publicadas: PROHIBIDO 0,576, DIFÍCIL 0,666 e INEXPLICABLE 0,568
# llevan el texto a la izquierda; IMPOSIBLE, con 0,509, va centrado.
SIDE_THRESHOLD = 0.54


def _subject_side(canvas: Image.Image) -> str:
    """Dónde está el motivo: 'izquierda', 'derecha' o 'centro'.

    Devuelve el lado en que debe ir EL TEXTO, que es el contrario al motivo.

    Se pesa el brillo por columnas: sobre fondo negro, el centro de masa de la
    luz es el objeto. Se descarta el blanco puro y sin saturación para que la
    función se pueda validar contra miniaturas que ya llevan la palabra puesta;
    en el pipeline se invoca antes de dibujarla y no hay nada que descartar.

    No se mide cuánto ocupa el motivo, solo dónde está. Se probó lo primero y
    era ruido: IMPOSIBLE ocupa lo mismo que DIFÍCIL y se centra únicamente
    porque su galaxia está en el eje.
    """
    small = canvas.convert("RGB").resize((160, 90))
    px = small.load()

    total = weighted = 0.0
    for y in range(90):
        for x in range(160):
            r, g, b = px[x, y]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if lum < 30:                                    # fondo
                continue
            if lum > 215 and max(r, g, b) - min(r, g, b) < 25:   # texto blanco
                continue
            total += lum
            weighted += lum * x
    if total <= 0:
        return "centro"

    centroid = weighted / total / 160          # 0 = izquierda, 1 = derecha
    if centroid > SIDE_THRESHOLD:
        return "izquierda"
    if centroid < 1 - SIDE_THRESHOLD:
        return "derecha"
    return "centro"


def _draw_word(canvas: Image.Image, word: str) -> None:
    font, box = _fit(word)
    text_w, text_h = box[2] - box[0], box[3] - box[1]

    # Las palabras que casi llenan el ancho no tienen elección.
    if text_w >= W * CENTER_ABOVE:
        side = "centro"
    else:
        side = _subject_side(canvas)

    if side == "izquierda":
        x = int(W * TEXT_MARGIN) - box[0]
    elif side == "derecha":
        x = W - text_w - int(W * TEXT_MARGIN) - box[0]
    else:
        x = (W - text_w) // 2 - box[0]
    y = int(H * TEXT_CENTER_Y) - text_h // 2 - box[1]

    # Halo en dos capas, como en las miniaturas del canal: una ancha y tenue
    # que despega la palabra del fondo, y otra corta que perfila el trazo.
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((x, y), word, font=font, fill=(255, 255, 255, 210))
    for blur, strength in ((30, 0.62), (10, 0.80)):
        alpha = glow.filter(ImageFilter.GaussianBlur(blur)).split()[-1]
        canvas.paste((255, 255, 255), (0, 0), alpha.point(lambda v: int(v * strength)))

    ImageDraw.Draw(canvas).text((x, y), word, font=font, fill=(255, 255, 255))

    log.debug("  texto: %d px de alto (%.0f %%), ancho %.0f %%, %s",
              text_h, text_h / H * 100, text_w / W * 100, side)


def hero_frame(video: Path, dest: Path, at: float = 12.0) -> Path:
    """Si no hay una imagen mejor, se saca un fotograma del propio vídeo."""
    from .util import ffmpeg

    ffmpeg(["-ss", str(at), "-i", str(video), "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest
