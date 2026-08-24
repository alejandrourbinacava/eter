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

Dos pruebas, las dos baratas y sin dependencias más allá de Pillow:

`_is_space_like`  Una foto astronómica real es casi toda negra. Una gráfica o
                  una lámina tiene fondo claro y grandes zonas planas.

`_has_text`       El texto deja una firma muy reconocible al binarizar: filas
                  con muchas rachas cortas de píxeles claros alternando con
                  hueco. Un campo de estrellas da rachas de uno o dos píxeles
                  dispersas; una línea de rótulo da seis o más rachas anchas en
                  la misma fila, y varias filas seguidas así.
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


def frame_is_clean(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as img:
            small = img.convert("L").resize((192, 108))
            pixels = list(small.getdata())
    except Exception:
        return True  # ante la duda, no tirar material bueno

    if not _is_space_like(pixels):
        return False
    # Umbrales medidos sobre material real del SVS: los fotogramas de gráficas
    # dan entre 26 y 58; los de imagen astronómica, entre 0 y 2.
    if _text_rows(pixels, 192, 108) >= 8:
        return False
    if _flat_rows(pixels, 192, 108) >= 45:
        return False
    return True


def image_is_clean(path: Path) -> bool:
    """Igual que `frame_is_clean`, para una imagen fija descargada."""
    return frame_is_clean(path)


# --------------------------------------------------------------------------
# Tramos limpios de un clip
# --------------------------------------------------------------------------


def clean_windows(video: Path, duration: float, min_len: float) -> list[list[float]]:
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

        verdicts = [frame_is_clean(f) for f in frames]

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
