"""Control de calidad del material: qué trozos de un clip se pueden usar.

El problema que resuelve este módulo es el que más delata a un montaje
automático. Los archivos gratuitos de espacio están mezclados con piezas
divulgativas: gráficas sobre fondo blanco, láminas con etiquetas, rótulos a
pantalla completa. Filtrar por metadatos no basta — una visualización titulada
«What Webb Learns from Light» son cuarenta segundos de imagen preciosa del JWST
y quince de gráficos con la palabra SPECTRA en grande.

Así que no se acepta ni se rechaza el clip entero: se muestrea un fotograma
cada pocos segundos, se marca cada tramo como limpio o sucio, y el banco de
planos solo sirve trozos de los tramos limpios.

Tres pruebas, las tres baratas y sin más dependencia que Pillow:

`_text_rows`      El texto deja una firma reconocible al binarizar: filas con
                  varias rachas cortas de píxeles claros alternando con hueco.
                  Un campo de estrellas da rachas de uno o dos píxeles sueltas;
                  una línea de rótulo da cinco o más trazos en la misma fila, y
                  varias filas seguidas así.

`_flat_rows`      Rachas largas de valor casi constante y no negro: barras de
                  espectro, ejes, bordes de panel. Es lo que caza las gráficas
                  sobre fondo oscuro, que el brillo no distingue del espacio.

`_is_space_like`  Una foto astronómica real es casi toda negra; una lámina tiene
                  fondo claro. Esta SOLO se aplica a los archivos científicos
                  (`strict=True`). Con material de banco de stock es
                  contraproducente: premia lo oscuro, así que de un clip de
                  textura de hielo solo sobreviven los tramos casi negros y el
                  montaje entero sale en negro. Pasó, y por eso está separada.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .util import ffmpeg, log

SAMPLE_EVERY = 3.0  # segundos entre fotogramas muestreados


# --------------------------------------------------------------------------
# Pruebas sobre un fotograma
# --------------------------------------------------------------------------


def _is_space_like(pixels: list[int]) -> bool:
    total = len(pixels)
    if not total:
        return False
    mean = sum(pixels) / total
    near_white = sum(1 for p in pixels if p > 235) / total
    dark = sum(1 for p in pixels if p < 60) / total
    if mean > 125:
        return False
    if near_white > 0.16:
        return False
    if dark < 0.25:
        return False
    return True


def _text_rows(pixels: list[int], width: int, height: int) -> int:
    """Filas con la firma de rachas claras cortas que deja una línea de texto.

    Un campo de estrellas da rachas de uno o dos píxeles, dispersas. Una línea
    de rótulo o de etiquetas da cinco o más trazos en la misma fila.
    """
    busy = 0
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        runs = run_len = 0
        for value in row:
            if value > 175:
                run_len += 1
            else:
                if 2 <= run_len <= 24:
                    runs += 1
                run_len = 0
        if 2 <= run_len <= 24:
            runs += 1
        if runs >= 5:
            busy += 1
    return busy


def _flat_rows(pixels: list[int], width: int, height: int) -> int:
    """Filas con una racha larga de valor casi constante y que no es negro.

    Es la firma de las gráficas sobre fondo oscuro, que el test de brillo no
    detecta: barras de espectro, ejes, bordes de panel, tablas. La imagen
    astronómica real tiene ruido y degradado, así que casi nunca produce
    tramos planos largos fuera del fondo negro.
    """
    flat = 0
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        best = run = 1
        for x in range(1, width):
            if abs(row[x] - row[x - 1]) <= 4:
                run += 1
                if run > best:
                    best = run
            else:
                run = 1
        if best > width * 0.45 and sum(row) / width > 40:
            flat += 1
    return flat


def frame_is_clean(path: Path, strict: bool = True) -> bool:
    """¿Este fotograma es utilizable?

    `strict` decide si además se exige que parezca una imagen astronómica.
    Solo tiene sentido para los archivos científicos, donde el material bueno
    está mezclado con láminas y esquemas. Aplicarlo a un clip de banco de stock
    es contraproducente: como el test premia lo oscuro, de un clip de textura de
    hielo solo sobreviven los tramos casi negros y el montaje sale en negro.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            small = img.convert("L").resize((192, 108))
            pixels = list(small.getdata())
    except Exception:
        return True  # ante la duda, no tirar material bueno

    if strict and not _is_space_like(pixels):
        return False
    # Umbrales medidos sobre material real del SVS: los fotogramas de gráficas
    # dan entre 26 y 58; los de imagen astronómica, entre 0 y 2.
    if _text_rows(pixels, 192, 108) >= 8:
        return False
    if _flat_rows(pixels, 192, 108) >= 45:
        return False
    # Un plano en negro no aporta nada aunque pase los filtros.
    if sum(pixels) / len(pixels) < 8:
        return False
    return True


def image_is_clean(path: Path) -> bool:
    """Igual que `frame_is_clean`, para una imagen fija de archivo."""
    return frame_is_clean(path, strict=True)


# --------------------------------------------------------------------------
# Tramos limpios de un clip
# --------------------------------------------------------------------------


def _difference(a: Path, b: Path) -> float:
    """Diferencia media entre dos fotogramas. 0 = idénticos."""
    try:
        from PIL import Image, ImageChops

        with Image.open(a) as ia, Image.open(b) as ib:
            ga, gb = ia.convert("L"), ib.convert("L")
            hist = ImageChops.difference(ga, gb).histogram()
    except Exception:
        return 99.0  # ante la duda, darlo por bueno

    total = sum(hist)
    if not total:
        return 0.0
    return sum(i * n for i, n in enumerate(hist)) / total


# Por debajo de esto, dos fotogramas separados por SAMPLE_EVERY segundos son
# prácticamente el mismo: el clip está congelado y en pantalla se lee como una
# foto. Calibrado sobre los planos del primer vídeo largo, donde el 19 % daba
# menos de 3 y cuatro daban exactamente 0.
MIN_MOTION = 2.5


def clean_windows(video: Path, duration: float, min_len: float,
                  strict: bool = True) -> list[list[float]]:
    """Devuelve los intervalos [inicio, fin] del clip que son utilizables.

    Si el muestreo falla por lo que sea, se devuelve el clip entero: es
    preferible arriesgar un plano feo a quedarse sin material.
    """
    if duration <= 0:
        return []

    with tempfile.TemporaryDirectory(prefix="eter_qc_") as tmp:
        out = Path(tmp)
        try:
            ffmpeg([
                "-i", str(video),
                "-vf", f"fps=1/{SAMPLE_EVERY},scale=192:108",
                "-frames:v", "240",
                str(out / "f%04d.png"),
            ])
        except RuntimeError:
            return [[0.0, duration]]

        frames = sorted(out.glob("f*.png"))
        if not frames:
            return [[0.0, duration]]

        verdicts = [frame_is_clean(f, strict=strict) for f in frames]

        # Y además tiene que MOVERSE. Un clip congelado es una imagen
        # disfrazada, y el canal pidió clips. Cada veredicto se compara con el
        # fotograma siguiente; si no cambia nada, ese tramo no vale.
        for i in range(len(frames)):
            if not verdicts[i]:
                continue
            vecino = frames[i + 1] if i + 1 < len(frames) else frames[i - 1]
            if vecino is frames[i] or _difference(frames[i], vecino) < MIN_MOTION:
                verdicts[i] = False

    # Cada veredicto cubre su bucket de SAMPLE_EVERY segundos.
    windows: list[list[float]] = []
    start: float | None = None
    for i, ok in enumerate(verdicts):
        t0 = i * SAMPLE_EVERY
        if ok and start is None:
            start = t0
        elif not ok and start is not None:
            windows.append([start, t0])
            start = None
    if start is not None:
        windows.append([start, min(len(verdicts) * SAMPLE_EVERY, duration)])

    windows = [w for w in windows if w[1] - w[0] >= min_len]
    clean = sum(w[1] - w[0] for w in windows)
    log.debug(
        "  control de calidad %s: %.0f s utilizables de %.0f (%d tramos)",
        video.name[:36], clean, duration, len(windows),
    )
    return windows
