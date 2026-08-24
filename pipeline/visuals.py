"""Obtención y preparación del plano de cada escena.

Cascada de fuentes, de mejor a peor. La primera que devuelva algo utilizable
gana; si todas fallan, hay un plano procedural para que el render nunca se
caiga por falta de imagen.

  1. Biblioteca    — tus propios clips en `library/`, incluidos los que generes
                     a mano en Google Labs/Flow, Runway o donde sea. Ver
                     `library/README.md`. Tienen prioridad sobre todo lo demás.
  2. Pexels        — vídeo de stock cinematográfico. Clave gratuita.
  3. Pixabay       — ídem. Clave gratuita.
  4. NASA imagen   — el archivo fotográfico grande (Hubble, JWST, Cassini, JPL).
                     Sin clave. Se convierte en plano con movimiento lento.
  5. NASA vídeo    — metraje de misión y visualizaciones científicas. Sin clave.
  6. IA por API    — adaptador listo, apagado por defecto: ver ai33.generate_image.
  7. Procedural    — campo de estrellas con deriva, generado por ffmpeg.

Por qué la imagen va antes que el vídeo. Medido sobre este mismo archivo: la
biblioteca de vídeo de la NASA está dominada por piezas de comunicación —
cabeceras animadas, rótulos «LIVE INTERACTIVE», salas de control, ruedas de
prensa— que en pantalla delatan al instante que el montaje es automático. El
archivo fotográfico, en cambio, es material de instrumento en alta resolución y
con un movimiento lento encima da exactamente el plano de documental que usa el
canal. El vídeo de la NASA queda como red de seguridad y con un filtro estricto.

Ningún material entra en el vídeo sin ser de dominio público (NASA/ESA), de
licencia libre para uso comercial (Pexels, Pixabay) o tuyo (biblioteca).
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from . import config
from .util import download, ffmpeg, http, log, probe_streams

# Los MP4 originales de la NASA llegan a 1,7 GB. Se corta la descarga.
MAX_CLIP_BYTES = 90 * 1024 * 1024
MIN_CLIP_SECONDS = 3.0

USE_AI_IMAGES = False  # ver la nota de cabecera en ai33.py


class AssetPool:
    """Evita que el mismo plano aparezca dos veces en el mismo vídeo."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, key: str) -> bool:
        if key in self.used:
            return False
        self.used.add(key)
        return True


# --------------------------------------------------------------------------
# Fuentes
# --------------------------------------------------------------------------


LIBRARY_DIR = config.ROOT / "library"
_STOPWORDS = {"the", "a", "of", "in", "on", "and", "with", "from", "closeup", "close", "up",
              "shot", "view", "cinematic", "slow"}


def _tokens(text: str) -> set[str]:
    import re as _re

    return {t for t in _re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOPWORDS}


def library_index() -> list[tuple[Path, set[str]]]:
    """Indexa `library/` por las palabras del nombre de fichero.

    `saturn-rings-cassini_02.mp4` queda etiquetado {saturn, rings, cassini}.
    Las subcarpetas también cuentan como etiquetas, así que
    `library/black-hole/orbit.mp4` etiqueta {black, hole, orbit}.
    """
    if not LIBRARY_DIR.exists():
        return []
    index = []
    for path in sorted(LIBRARY_DIR.rglob("*")):
        if path.suffix.lower() not in (".mp4", ".mov", ".webm", ".m4v"):
            continue
        rel = path.relative_to(LIBRARY_DIR)
        tags = _tokens(str(rel.with_suffix("")))
        tags -= {str(n) for n in range(100)}
        index.append((path, tags))
    return index


def _library(query: str, pool: AssetPool) -> Path | None:
    """Mejor clip propio no usado todavía, por solape de etiquetas."""
    wanted = _tokens(query)
    if not wanted:
        return None
    best, best_score = None, 0
    for path, tags in library_index():
        if f"library:{path}" in pool.used:
            continue
        score = len(wanted & tags)
        if score > best_score:
            best, best_score = path, score
    if best is None:
        return None
    pool.take(f"library:{best}")
    return best


def _pexels(query: str, pool: AssetPool) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    try:
        r = http(
            "GET",
            "https://api.pexels.com/videos/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            params={"query": query, "per_page": 15, "orientation": "landscape", "size": "medium"},
        )
    except RuntimeError:
        return None
    for video in r.json().get("videos", []):
        if not pool.take(f"pexels:{video['id']}"):
            continue
        if video.get("duration", 0) < MIN_CLIP_SECONDS:
            continue
        files = [f for f in video.get("video_files", []) if (f.get("width") or 0) >= 1280]
        if not files:
            continue
        files.sort(key=lambda f: abs((f.get("width") or 0) - 1920))
        return files[0]["link"]
    return None


def _pixabay(query: str, pool: AssetPool) -> str | None:
    if not config.PIXABAY_API_KEY:
        return None
    try:
        r = http(
            "GET",
            "https://pixabay.com/api/videos/",
            params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": 20, "safesearch": "true"},
        )
    except RuntimeError:
        return None
    for hit in r.json().get("hits", []):
        if not pool.take(f"pixabay:{hit['id']}"):
            continue
        if hit.get("duration", 0) < MIN_CLIP_SECONDS:
            continue
        streams = hit.get("videos", {})
        for quality in ("large", "medium", "small"):
            url = (streams.get(quality) or {}).get("url")
            if url:
                return url
    return None


_NASA_SEARCH = "https://images-api.nasa.gov/search"

# El archivo de la NASA está lleno de material de comunicación institucional que
# no sirve como plano de recurso: rótulos, presentadores, salas de control.
_NASA_NOISE = (
    "this week @nasa", "news conference", "briefing", "press", "interview",
    "town hall", "administrator", "ceremony", "podcast", "live", "interactive",
    "b-roll package", "soundbite", "sound bite", "highlights", "recap", "episode",
    "panel", "q&a", "ask nasa", "chat with", "tour of", "meet the", "career",
    "anniversary", "celebrat", "award", "graduation", "welcome", "message from",
    "state of nasa", "budget", "testimony", "hearing", "training", "crew arrival",
    "landing coverage", "launch coverage", "post-launch", "prelaunch", "news release",
    "science update", "media", "teleconference", "webinar", "expedition crew",
)

# Al revés: identificadores que casi siempre son visualización o imagen limpia.
_NASA_GOOD = ("pia", "visualization", "simulation", "animation", "flyover", "flythrough",
              "artist", "concept", "rendering", "hubble", "webb", "jwst", "cassini",
              "juno", "voyager", "chandra", "spitzer")

# Láminas científicas, esquemas y material de rueda de prensa: llevan texto
# quemado y rompen la regla de «nunca gráficos con texto en pantalla».
_FIGURE_WORDS = ("diagram", "chart", "graph", "plot", "figure", "poster", "infographic",
                 "comparison", "labeled", "labelled", "annotated", "schematic", "map of",
                 "timeline", "cutaway", "spectrum", "spectra", "light curve", "data set",
                 "logo", "patch", "insignia", "slide", "presentation", "graphic", "caption",
                 "side by side", "before and after", "montage", "collage", "mosaic of",
                 "illustration showing", "key features", "how it works")


def _looks_like_space(path: Path) -> bool:
    """Rechaza láminas, esquemas y documentos por su histograma.

    Truco barato y sorprendentemente fiable en este dominio: una fotografía
    astronómica real es casi toda negra, mientras que una figura científica
    tiene fondo blanco o gris y grandes zonas planas muy claras. Con esto se
    caen las láminas con rótulos que la búsqueda por metadatos no detecta.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("L").resize((160, 90))
            pixels = list(img.getdata())
    except Exception:
        return True  # ante la duda, no descartar material bueno

    total = len(pixels)
    if not total:
        return False
    mean = sum(pixels) / total
    near_white = sum(1 for p in pixels if p > 235) / total
    dark = sum(1 for p in pixels if p < 60) / total

    if mean > 125:
        return False          # imagen globalmente clara: fondo de documento
    if near_white > 0.16:
        return False          # grandes planos blancos: paneles y cajas de texto
    if dark < 0.25:
        return False          # sin negro de fondo no es un plano de espacio
    return True


def _nasa_assets(query: str, media: str) -> list[dict]:
    try:
        r = http("GET", _NASA_SEARCH, params={"q": query, "media_type": media, "page_size": 24})
    except RuntimeError:
        return []
    items = r.json().get("collection", {}).get("items", [])
    keep = []
    for item in items:
        data = (item.get("data") or [{}])[0]
        title = (data.get("title") or "").lower()
        nasa_id = (data.get("nasa_id") or "").lower()
        description = (data.get("description") or "")[:300].lower()
        haystack = f"{title} {nasa_id}"
        if any(noise in haystack for noise in _NASA_NOISE):
            continue
        if any(word in f"{haystack} {description}" for word in _FIGURE_WORDS):
            continue
        # Las visualizaciones e imágenes de instrumento primero.
        score = sum(good in haystack for good in _NASA_GOOD)
        keep.append((score, item))
    keep.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in keep]


def _nasa_video(query: str, pool: AssetPool) -> str | None:
    for item in _nasa_assets(query, "video"):
        nasa_id = (item.get("data") or [{}])[0].get("nasa_id", "")
        if not pool.take(f"nasa:{nasa_id}"):
            continue
        try:
            assets = http("GET", item["href"]).json()
        except (RuntimeError, KeyError, ValueError):
            continue
        mp4s = [u for u in assets if u.lower().endswith(".mp4")]
        if not mp4s:
            continue
        # Preferir las variantes ligeras: el ~orig puede pesar más de un giga.
        mp4s.sort(key=lambda u: (0 if "~medium" in u else 1 if "~small" in u else 2))
        return mp4s[0].replace("http://", "https://")
    return None


def _nasa_image(query: str, pool: AssetPool) -> str | None:
    for item in _nasa_assets(query, "image"):
        nasa_id = (item.get("data") or [{}])[0].get("nasa_id", "")
        if not pool.take(f"nasa-img:{nasa_id}"):
            continue
        try:
            assets = http("GET", item["href"]).json()
        except (RuntimeError, KeyError, ValueError):
            continue
        images = [u for u in assets if u.lower().endswith((".jpg", ".png"))]
        if not images:
            continue
        images.sort(key=lambda u: (0 if "~orig" in u else 1 if "~large" in u else 2))
        return images[0].replace("http://", "https://")
    return None


# --------------------------------------------------------------------------
# Conversión a plano
# --------------------------------------------------------------------------


def _has_video_stream(path: Path) -> bool:
    return any(s.get("codec_type") == "video" for s in probe_streams(path).get("streams", []))


def _autocrop(src: Path) -> str:
    """Detecta bandas negras y devuelve el filtro `crop=` que las elimina.

    Buena parte del material de archivo llega con buzón, con barras laterales o
    con marcos gráficos. Escalar sin quitarlos deja bandas dentro del encuadre,
    que es de las cosas que más delatan un montaje automático.
    """
    import re
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-ss", "2", "-t", "4", "-i", str(src),
         "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", proc.stderr)
    if not matches:
        return ""
    w, h, x, y = (int(v) for v in matches[-1])
    if w < 320 or h < 180:
        return ""
    streams = [s for s in probe_streams(src).get("streams", []) if s.get("codec_type") == "video"]
    if streams:
        full_w, full_h = streams[0].get("width", 0), streams[0].get("height", 0)
        # Un recorte que se come más de un tercio de la imagen no es una banda,
        # es una detección mala.
        if full_w and (w * h) < 0.55 * full_w * full_h:
            return ""
        if full_w and w >= full_w and h >= full_h:
            return ""
    return f"crop={w}:{h}:{x}:{y},"


def _clip_from_video(src: Path, dest: Path, duration: float, seed: int) -> None:
    """Recorta, encuadra a 16:9 y da un empuje lento de zoom para que no se
    note el bucle si el material es corto."""
    zoom = 1.0 + 0.04 * (seed % 3)
    ffmpeg([
        "-stream_loop", "-1", "-i", str(src),
        "-an", "-t", f"{duration:.3f}",
        "-vf",
        (
            _autocrop(src)
            + f"scale={config.WIDTH * 2}:{config.HEIGHT * 2}:force_original_aspect_ratio=increase,"
            f"crop={config.WIDTH * 2}:{config.HEIGHT * 2},"
            f"scale={int(config.WIDTH * zoom)}:{int(config.HEIGHT * zoom)},"
            f"crop={config.WIDTH}:{config.HEIGHT},"
            f"fps={config.FPS},format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(dest),
    ])


def _clip_from_image(src: Path, dest: Path, duration: float, seed: int) -> None:
    """Movimiento cinematográfico lento sobre una imagen fija.

    Cuatro trayectorias que se alternan por escena para que veinte planos
    seguidos no se muevan todos igual.
    """
    frames = max(int(duration * config.FPS), 1)
    rate = 0.00055
    mode = seed % 4
    if mode == 0:      # zoom de entrada al centro
        z = f"min(zoom+{rate},1.30)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == 1:    # zoom de salida
        z = f"if(lte(zoom,1.0),1.30,max(zoom-{rate},1.0))"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == 2:    # deriva lateral con zoom leve
        z = f"min(zoom+{rate * 0.6},1.18)"
        x, y = f"(iw-iw/zoom)*(on/{frames})", "ih/2-(ih/zoom/2)"
    else:              # deriva vertical descendente
        z = f"min(zoom+{rate * 0.6},1.18)"
        x, y = "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(on/{frames})"

    ffmpeg([
        "-loop", "1", "-i", str(src),
        "-t", f"{duration:.3f}",
        "-vf",
        (
            # El sobremuestreo previo es lo que evita el temblor típico de zoompan.
            f"scale={config.WIDTH * 3}:-2:flags=lanczos,"
            f"crop={config.WIDTH * 3}:{config.HEIGHT * 3}:(in_w-out_w)/2:(in_h-out_h)/2,"
            f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}'"
            f":s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS},"
            f"format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        str(dest),
    ])


def _procedural(dest: Path, duration: float, seed: int) -> None:
    """Último recurso: campo de estrellas en deriva. Nunca falla."""
    ffmpeg([
        "-f", "lavfi",
        "-i", f"nullsrc=s={config.WIDTH}x{config.HEIGHT}:r={config.FPS}:d={duration:.3f}",
        "-vf",
        (
            f"geq=random(1)*255:128:128,"
            f"lutyuv=y='if(gt(val,252),val,0)',"
            f"boxblur=1:1,"
            f"zoompan=z='min(zoom+0.0009,1.4)':d={int(duration * config.FPS)}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS},"
            f"colorbalance=bs=0.12,format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        str(dest),
    ])
    log.warning("Escena resuelta con plano procedural (semilla %d)", seed)


# --------------------------------------------------------------------------
# Orquestación por escena
# --------------------------------------------------------------------------


def build_clips(scenes, workdir: Path, pool: AssetPool | None = None) -> list:
    pool = pool or AssetPool()
    clips_dir = workdir / "clips"
    raw_dir = workdir / "raw"
    clips_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    stats = {"biblioteca": 0, "pexels": 0, "pixabay": 0, "nasa-video": 0,
             "nasa-image": 0, "ai": 0, "procedural": 0}
    if library_index():
        log.info("Biblioteca local: %d clips propios disponibles", len(library_index()))

    for scene in scenes:
        dest = clips_dir / f"scene_{scene.index:03d}.mp4"
        if dest.exists():
            scene.clip_path = str(dest)
            continue

        # Longitud exigida por el encadenado de assemble.render(): la escena,
        # su pausa posterior y el solape del fundido.
        needed = scene.duration + config.SCENE_GAP + config.CROSSFADE
        seed = int(hashlib.md5(f"{scene.index}{scene.visual_query}".encode()).hexdigest()[:8], 16)
        source = _resolve(scene, pool, raw_dir, stats)

        try:
            if source is None:
                _procedural(dest, needed, seed)
                stats["procedural"] += 1
            elif source.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v"):
                _clip_from_video(source, dest, needed, seed)
            else:
                _clip_from_image(source, dest, needed, seed)
        except RuntimeError:
            log.warning("Escena %02d: el material falló al procesar, se usa procedural", scene.index)
            _procedural(dest, needed, seed)
            stats["procedural"] += 1

        scene.clip_path = str(dest)

    log.info("Planos por fuente: %s", ", ".join(f"{k}={v}" for k, v in stats.items() if v))
    return scenes


def _resolve(scene, pool: AssetPool, raw_dir: Path, stats: dict) -> Path | None:
    """Busca material para una escena bajando por la cascada de fuentes."""
    query = scene.visual_query or "deep space"
    variants = [query, " ".join(query.split()[:2]), query.split()[0]]

    # Tus propios clips van primero: son los que llevan tu criterio.
    own = _library(query, pool)
    if own is not None:
        stats["biblioteca"] += 1
        log.debug("  escena %02d ← biblioteca %s", scene.index, own.name)
        return own

    # Bancos de vídeo: es el único material con movimiento real y limpio.
    for variant in dict.fromkeys(v for v in variants if v):
        for name, finder in (("pexels", _pexels), ("pixabay", _pixabay)):
            url = finder(variant, pool)
            if not url:
                continue
            dest = raw_dir / f"{name}_{scene.index:03d}.mp4"
            try:
                download(url, dest, max_bytes=MAX_CLIP_BYTES)
            except Exception as exc:
                log.debug("  descarga fallida (%s): %s", name, exc)
                continue
            # Una descarga truncada puede dejar un fichero sin pista de vídeo.
            if dest.stat().st_size > 200_000 and _has_video_stream(dest):
                stats[name] += 1
                log.debug("  escena %02d <- %s '%s'", scene.index, name, variant)
                return dest

    # El archivo fotográfico sale mejor en pantalla que el vídeo institucional.
    for variant in dict.fromkeys(v for v in variants if v):
        # Varios intentos por búsqueda: el filtro de láminas descarta bastantes
        # y no queremos rendirnos al primer descarte.
        for _ in range(5):
            url = _nasa_image(variant, pool)
            if not url:
                break
            dest = raw_dir / f"nasa_{scene.index:03d}{Path(url).suffix or '.jpg'}"
            try:
                download(url, dest, max_bytes=MAX_CLIP_BYTES)
            except Exception:
                continue
            if dest.stat().st_size <= 30_000:
                continue
            if not _looks_like_space(dest):
                log.debug("  descartada lámina o esquema: %s", url.rsplit("/", 1)[-1][:60])
                continue
            stats["nasa-image"] += 1
            log.debug("  escena %02d <- nasa-image '%s'", scene.index, variant)
            return dest

    # Red de seguridad: vídeo de la NASA ya filtrado de piezas de comunicación.
    for variant in dict.fromkeys(v for v in variants if v):
        url = _nasa_video(variant, pool)
        if not url:
            continue
        dest = raw_dir / f"nasa-video_{scene.index:03d}.mp4"
        try:
            download(url, dest, max_bytes=MAX_CLIP_BYTES)
        except Exception:
            continue
        if dest.stat().st_size > 200_000 and _has_video_stream(dest):
            stats["nasa-video"] += 1
            log.debug("  escena %02d <- nasa-video '%s'", scene.index, variant)
            return dest


    if USE_AI_IMAGES and scene.visual_prompt:
        from . import ai33

        dest = raw_dir / f"ai_{scene.index:03d}.jpg"
        got = ai33.generate_image(scene.visual_prompt, dest)
        if got:
            stats["ai"] += 1
            return got

    return None


def hero_image(query: str, dest: Path) -> Path | None:
    """Busca una imagen fija limpia y en alta resolución para la miniatura.

    Va aparte de la cascada de escenas a propósito: la miniatura es lo primero
    que ve el espectador y no puede salir de un fotograma cualquiera del
    montaje, que puede llevar rótulos quemados del material de origen.
    """
    pool = AssetPool()
    for variant in dict.fromkeys([query, " ".join(query.split()[:2]), query.split()[0]]):
        for _ in range(6):
            url = _nasa_image(variant, pool)
            if not url:
                break
            try:
                download(url, dest, max_bytes=MAX_CLIP_BYTES)
            except Exception:
                continue
            if dest.stat().st_size <= 120_000:  # descartar miniaturas de catálogo
                continue
            if not _looks_like_space(dest):
                continue
            log.info("Imagen de miniatura: archivo NASA '%s'", variant)
            return dest
    return None


def pick_music(workdir: Path) -> Path | None:
    """Elige un lecho musical de brand/music si el usuario ha dejado alguno.

    No se descarga música de terceros: el riesgo de Content ID no compensa.
    """
    music_dir = config.BRAND_DIR / "music"
    if not music_dir.exists():
        return None
    tracks = sorted(p for p in music_dir.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav"))
    if not tracks:
        return None
    return random.Random(workdir.name).choice(tracks)
