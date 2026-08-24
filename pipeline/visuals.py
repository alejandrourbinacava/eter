"""Búsqueda y descarga del material de archivo.

Este módulo consigue las FUENTES; el troceado en planos de seis segundos y el
ritmo de montaje viven en `shots.py`.

La base del vídeo son clips, y cada plano se enruta según lo que pida.

ESPECÍFICO — un objeto con nombre propio: Encélado, la Europa Clipper, Sgr A*.
  Va a los archivos científicos, que son los únicos que tienen la cosa concreta:
  biblioteca propia, NASA SVS, biblioteca general de la NASA y, como relleno, su
  archivo fotográfico.

GENÉRICO — lo que se ve, sin nombrarlo: «icy moon surface», «accretion disk».
  Va a los bancos de stock, que tienen decenas de miles de planos de espacio
  buenos pero anónimos.

El guionista entrega las dos búsquedas por escena (`visual_query` y
`visual_generic`) precisamente para poder separarlas aquí. Nunca al revés:
mandar un nombre propio a un banco de stock es lo peor que se puede hacer,
porque no devuelve cero sino lo que más se le parece por letras. Medido con
estas mismas claves: «great red spot» devuelve un pájaro carpintero, «kinman
dwarf» un hámster y «europa clipper» un velero.

Tres filtros sostienen la calidad:

  `_is_space_clip`   cruza la descripción del clip de banco contra tres listas:
                     espacio, texturas terrestres que sirven de análogo (hielo
                     agrietado, lava, hidrotermal) y exclusiones. Es lo que mata
                     al hámster sin tirar el lago helado, que ilustra Encélado
                     mejor que cualquier render genérico.
  `_svs`             restringe el SVS a `Visualization`, `Animation` y `B-Roll`,
                     y exige solape de palabras con la búsqueda. Sin lo primero
                     entran piezas divulgativas con presentador y rótulos; sin
                     lo segundo, diagramas de satélites para consultas de Europa.
  `inspect_media`    muestrea fotogramas y marca los tramos limpios de cada
                     clip, porque una misma pieza puede tener cuarenta segundos
                     de imagen preciosa y quince de gráficas con texto.

Ningún material entra en el vídeo sin ser de dominio público (NASA/ESA), de
licencia libre para uso comercial (Pexels, Pixabay) o tuyo (biblioteca).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import random
import re
from pathlib import Path

from . import config, inspect_media, shots
from .util import download, ffmpeg, http, log, probe_duration, probe_streams

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


# Los bancos de stock NUNCA devuelven cero resultados: si no tienen lo que pides
# devuelven lo que más se parezca por letras. Comprobado con estas claves:
# «great red spot» devuelve un pájaro carpintero (great spotted woodpecker),
# «kinman dwarf» un hámster enano, «europa clipper» un velero de cinco mástiles
# y «wow signal» un teléfono GSM. Un clip solo se acepta si su descripción entra
# en alguna de las dos listas de abajo y no cae en la de exclusión.

# Espacio propiamente dicho.
_SPACE_WORDS = {
    "space", "cosmic", "cosmos", "galaxy", "galactic", "nebula", "star", "stars",
    "starry", "starfield", "stellar", "planet", "planetary", "moon", "lunar",
    "orbit", "orbital", "astronaut", "universe", "celestial", "solar", "sun",
    "asteroid", "comet", "meteor", "meteorite", "aurora", "satellite", "spaceship",
    "spacecraft", "rocket", "milky", "astronomy", "astronomical", "telescope",
    "supernova", "blackhole", "eclipse", "constellation", "interstellar",
    "earth", "mars", "jupiter", "saturn", "venus", "mercury", "neptune", "uranus",
    "pluto", "deepspace", "crater", "plasma",
    "planets", "moons", "world", "worlds", "alien", "extraterrestrial",
    "orbiting", "corona", "coronal", "photosphere", "granulation", "sunrise",
    "protostar", "giant", "dwarf", "quasar", "pulsar", "accretion", "horizon",
}

# Texturas y fenómenos terrestres que sirven de análogo. Un plano de hielo
# agrietado ilustra Encélado mejor que cualquier render genérico, y sin esta
# lista se caían casi todos: «cracked ice texture» devolvía lagos helados
# perfectos que el filtro tiraba por no decir «space».
_TEXTURE_WORDS = {
    "ice", "icy", "frozen", "freeze", "glacier", "glacial", "iceberg", "arctic",
    "snow", "frost", "crack", "cracked", "crevasse", "cave",
    "lava", "magma", "volcanic", "volcano", "eruption", "molten", "ember",
    "water", "ocean", "sea", "wave", "underwater", "abyss", "depth", "deep",
    "cloud", "storm", "lightning", "fog", "mist", "smoke", "vapor", "steam",
    "particle", "particles", "energy", "abstract", "swirl", "vortex", "spiral",
    "geyser", "hydrothermal", "vent", "sand", "dune", "rock", "stone", "canyon",
    "night", "sky", "dark", "darkness", "void", "horizon", "atmosphere", "surface",
}

# Señales de que el clip no es un plano de recurso, pase lo que pase.
_REJECT_WORDS = {
    # bichos y gente
    "woodpecker", "bird", "hamster", "dog", "cat", "fish", "jellyfish", "shark",
    "turtle", "whale", "stingray", "crab", "coral", "reef", "seal", "lion",
    "aquarium", "zoo", "wildlife", "animal", "pet", "insect", "butterfly",
    "man", "woman", "girl", "boy", "people", "person", "child", "family",
    "couple", "portrait", "model", "dancer", "worker", "chef", "doctor",
    "diver", "scuba", "snorkel", "surfer", "swimmer", "swimming",
    # sitios y objetos de la Tierra que rompen la ilusión
    "city", "street", "traffic", "car", "building", "office", "kitchen", "food",
    "restaurant", "shop", "market", "beach", "boat", "ship", "sailing", "yacht",
    "lighthouse", "forest", "tree", "flower", "garden", "farm", "sport",
    "phone", "laptop", "computer", "screen", "keyboard", "money", "gsm",
    "houseplant", "pot", "potted", "vase", "indoor", "studio", "desk", "table",
    "monstera", "bouquet", "decor", "interior",
    # Estética «tecnológica» que los bancos devuelven en cuanto pides algo
    # abstracto: lluvia de código verde, interfaces, circuitos. Es lo que se
    # coló pidiendo «particles» y «energy».
    "matrix", "digital", "code", "coding", "binary", "hud", "interface",
    "circuit", "chip", "server", "hologram", "futuristic", "cyber", "tech",
    "technology", "network", "blockchain", "bitcoin", "dashboard", "ui",
}


# Material de diseño, no metraje: capas para superponer, fondos de bucle,
# destellos de lente. Los bancos los devuelven en cuanto pides algo con «light»
# o «flare», y en pantalla son un degradado abstracto que no ilustra nada.
_DESIGN_WORDS = {
    "overlay", "leak", "leaks", "bokeh", "gradient", "backdrop", "background",
    "wallpaper", "screensaver", "template", "transition", "vj", "loopable",
    "seamless", "motiongraphics", "lensflare", "glitch", "grain", "texture",
    "mockup", "presentation", "intro", "outro", "lowerthird",
}

# Palabras demasiado genéricas para demostrar por sí solas que un clip trata de
# lo que se está diciendo.
_WEAK_WORDS = {"light", "dark", "deep", "closeup", "macro", "abstract", "slow",
               "beautiful", "stunning", "amazing", "background", "aerial",
               "view", "footage", "video", "shot", "scene", "time", "lapse"}


def _matches_query(text: str, query: str) -> bool:
    """¿Este clip trata REALMENTE de lo que se ha buscado?

    Es la regla que faltaba y la que más se nota. Antes bastaba con que la
    descripción tuviera vocabulario de espacio, así que para «sun flare lens»
    entraba un destello de lente abstracto mientras la narración decía «el Sol
    está ahí». Ahora hace falta que comparta al menos una palabra con peso: si
    buscas el Sol, el clip tiene que hablar del Sol.
    """
    wanted = _tokens(query) - _WEAK_WORDS
    if not wanted:
        return True
    return bool(_tokens(text) & wanted)


def _is_space_clip(text: str, query: str = "") -> bool:
    """¿La descripción del clip sirve como plano de este canal?

    Las exclusiones se levantan para lo que pide la propia búsqueda. Un bosque
    normalmente no pinta nada en un documental espacial, pero si el guion habla
    del fin de la fotosíntesis y la búsqueda dice «forest canopy», el bosque es
    justo lo que hace falta. Lo mismo con «diver» en una escena de respiraderos
    hidrotermales.
    """
    words = _tokens(text)
    if words & _DESIGN_WORDS:
        return False
    prohibidas = _REJECT_WORDS - _tokens(query)
    if words & prohibidas:
        return False
    if not _matches_query(text, query):
        return False
    return bool(words & (_SPACE_WORDS | _TEXTURE_WORDS | _tokens(query)))


def _pexels(query: str, pool: AssetPool) -> str | None:
    if not config.PEXELS_API_KEY:
        return None
    try:
        r = http(
            "GET",
            "https://api.pexels.com/videos/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            params={"query": query, "per_page": 20, "orientation": "landscape"},
        )
    except RuntimeError:
        return None
    for video in r.json().get("videos", []):
        if not pool.take(f"pexels:{video['id']}"):
            continue
        if video.get("duration", 0) < MIN_CLIP_SECONDS:
            continue
        # La descripción real del clip va en el slug de su URL.
        slug = str(video.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
        if not _is_space_clip(f"{slug} {video.get('alt', '')}", query):
            log.debug("  pexels descartado por fuera de tema: %s", slug[:52])
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
            params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": 30,
                    "safesearch": "true"},
        )
    except RuntimeError:
        return None
    for hit in r.json().get("hits", []):
        if not pool.take(f"pixabay:{hit['id']}"):
            continue
        if hit.get("duration", 0) < MIN_CLIP_SECONDS:
            continue
        if not _is_space_clip(f"{hit.get('tags', '')} {hit.get('pageURL', '')}", query):
            log.debug("  pixabay descartado por fuera de tema: %s", str(hit.get("tags"))[:52])
            continue
        streams = hit.get("videos", {})
        for quality in ("large", "medium", "small"):
            url = (streams.get(quality) or {}).get("url")
            if url:
                return url
    return None


_SVS_API = "https://svs.gsfc.nasa.gov/api"
# Solo CGI científico. Ver la explicación en _svs().
# SOLO «Animation». Medido sobre siete búsquedas reales: de 227 resultados solo
# 7 son Animation, pero son los únicos limpios de verdad —«Red Giant Sun»,
# «Supernova explosion animation», «Cosmic Caverns in the Cat's Paw Nebula»—.
# «Visualization» es mayoritariamente producto de datos con leyenda y fecha
# quemadas, y el filtro por título solo caza una parte: seguían colándose un
# mapamundi con puntos y un diagrama orbital rotulado. El SVS deja de ser fuente
# de volumen (los bancos aportan más de 190 clips por vídeo) y pasa a ser fuente
# de precisión para lo que solo existe en el archivo científico.
_SVS_TYPES = {"Animation"}

# El grueso de lo que el SVS etiqueta como «Visualization» son productos de
# DATOS, no metraje: «ICESat-2 Land Ice Height Change (2020-2025)», «GRACE and
# GRACE-FO polar ice mass loss», «Map of the August 12 2026 Eclipse». Llevan
# leyenda, escala de color y fecha quemadas en la imagen por definición, y son
# los que colaron un gráfico entero del hielo de Groenlandia en el minuto 12.
#
# No hay forma de verlos por píxeles: el rótulo es gris, pequeño y de bajo
# contraste, y el detector de texto puntúa cero incluso a 640x360. La única
# señal fiable es el título.
_SVS_DATA_WORDS = (
    "data", "dataset", "mass loss", "mass change", "height change", "sea level",
    "index", "trend", "anomaly", "time series", "measurements", "observations",
    "map of", "maps of", "coverage", "concentration", "monthly", "annual",
    "daily", "record", "maximum", "minimum", "average", "statistics", "survey",
    "fleet", "constellation", "orbit tracks", "ground track", "swath",
    "graphics", "briefing", "chart", "plot", "diagram", "infographic",
    "comparison", "timeline", "model output", "simulation output", "forecast",
    "icesat", "grace", "modis", "viirs", "goes-", "smap", "airs",
)
# Rangos de años en el título: casi siempre delatan una serie temporal.
_SVS_YEAR_RANGE = re.compile(r"\b(19|20)\d{2}\s*[-–—]\s*(19|20)?\d{2}\b")
_SVS_RES = re.compile(r"(?:^|[^0-9])(\d{3,4})(?:p\d*|x\d+p\d+)?\.mp4$", re.I)


def _svs_height(url: str) -> int:
    """Las resoluciones del SVS vienen en el nombre y sin un patrón único:
    `_1080p30.mp4`, `-1080.mp4`, `2048p30.mp4`, `3840x2160p60.mp4`, `_4K.mp4`."""
    if re.search(r"[_-]4k\.mp4$", url, re.I):
        return 2160
    m = re.search(r"(\d{3,4})x(\d{3,4})p\d+\.mp4$", url, re.I)
    if m:
        return int(m.group(2))
    m = _SVS_RES.search(url)
    return int(m.group(1)) if m else 0


def _svs(query: str, pool: AssetPool) -> str | None:
    """Scientific Visualization Studio de la NASA.

    Es el mejor archivo gratuito para este canal: son animaciones científicas
    puras, sin ruedas de prensa ni rótulos, y muchas duran entre 30 y 90
    segundos, así que de un solo activo salen diez o quince planos de 6 s.

    Dos trampas de la API, las dos comprobadas a mano:

    Primera, ignora `q` en silencio y devuelve el archivo entero; el parámetro
    que filtra es `search`.

    Segunda, y más importante, `result_type` separa el material limpio del
    divulgativo. `Visualization` y `Animation` son CGI científico puro.
    `Produced Video` son piezas con presentador, gráficas y rótulos: de ahí
    salió el plano con la palabra SPECTRA a pantalla completa que obligó a
    montar este filtro. `Infographic`, `Interactive` y `Gallery` sobran por
    definición. Solo uno de cada ocho resultados sirve, así que se pide una
    página grande.
    """
    try:
        r = http("GET", f"{_SVS_API}/search/", params={"search": query, "limit": 60})
    except RuntimeError:
        return None

    wanted = _tokens(query)
    for result in r.json().get("results", []):
        if result.get("result_type") not in _SVS_TYPES:
            continue
        page_id = result.get("id")
        if page_id is None or not pool.take(f"svs:{page_id}"):
            continue
        title = (result.get("title") or "").lower()
        if any(noise in title for noise in _NASA_NOISE):
            continue
        # Los productos de datos llevan leyenda y fecha quemadas. Ver arriba.
        if result.get("result_type") != "Animation":
            if any(w in title for w in _SVS_DATA_WORDS) or _SVS_YEAR_RANGE.search(title):
                log.debug("  svs descartado por ser producto de datos: %s", title[:56])
                continue
        # El buscador del SVS puntúa flojo y cuela resultados sin relación:
        # para «europa jupiter moon» devolvía primero un diagrama orbital de la
        # flota de satélites. Se exige que comparta alguna palabra de verdad.
        haystack = _tokens(f"{result.get('title', '')} {result.get('description', '')[:400]}")
        if wanted and not (wanted & haystack):
            continue
        try:
            detail = http("GET", f"{_SVS_API}/{page_id}/").json()
        except RuntimeError:
            continue
        urls = set(re.findall(r"https?://[^\"\\ ]+?\.mp4", json.dumps(detail)))
        # 1080p es el punto dulce: calidad de sobra y una décima parte del 4K.
        ranked = sorted(
            ((_svs_height(u), u) for u in urls),
            key=lambda hu: (abs(hu[0] - 1080), -hu[0]),
        )
        for height, url in ranked:
            if height >= 720:
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
    return inspect_media.image_is_clean(path)


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
# Inspección del material
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


# --------------------------------------------------------------------------
# Orquestación
# --------------------------------------------------------------------------


def _fetch_source(query: str, generic: str, pool: AssetPool, raw_dir: Path, stats: dict):
    """Baja UN material nuevo. Devuelve (ruta, es_imagen) o None.

    El enrutado es lo importante. Un plano de Éter puede pedir dos cosas muy
    distintas y cada una tiene su fuente:

    ESPECÍFICO — «Enceladus geysers», «Europa Clipper», «Sagittarius A star».
      Va a los archivos científicos, que son los únicos que tienen la cosa
      concreta. Si no la tienen, no la tiene nadie.

    GENÉRICO — «icy moon surface», «spacecraft in deep space», «accretion disk».
      Va a los bancos de stock, que tienen decenas de miles de planos de
      espacio bonitos pero anónimos.

    Nunca al revés. Mandar un nombre propio a un banco de stock es la peor
    opción posible, porque no devuelve cero: devuelve lo que más se le parece
    por letras. Con esta misma clave, «great red spot» devuelve un pájaro
    carpintero y «kinman dwarf» un hámster.
    """
    counter = len(list(raw_dir.glob("*")))
    specific = list(dict.fromkeys([q for q in (query, " ".join(query.split()[:2])) if q]))
    broad = list(dict.fromkeys([g for g in (generic, " ".join(generic.split()[:2])) if g]))

    # 1. Tus propios clips: se prueban con las dos búsquedas.
    for term in specific + broad:
        own = _library(term, pool)
        if own is not None:
            stats["biblioteca"] += 1
            return own, False

    # 2. Archivos científicos, con el sujeto concreto.
    for variant in specific:
        url = _svs(variant, pool)
        if not url:
            continue
        dest = raw_dir / f"svs_{counter:03d}.mp4"
        try:
            download(url, dest, max_bytes=MAX_CLIP_BYTES)
        except Exception as exc:
            log.debug("  descarga fallida (svs): %s", exc)
            continue
        if dest.stat().st_size > 200_000 and _has_video_stream(dest):
            stats["svs"] += 1
            log.debug("  fuente <- svs '%s'", variant)
            return dest, False

    # 3. Bancos de stock, con la descripción genérica.
    for variant in broad:
        for name, finder in (("pexels", _pexels), ("pixabay", _pixabay)):
            url = finder(variant, pool)
            if not url:
                continue
            dest = raw_dir / f"{name}_{counter:03d}.mp4"
            try:
                download(url, dest, max_bytes=MAX_CLIP_BYTES)
            except Exception as exc:
                log.debug("  descarga fallida (%s): %s", name, exc)
                continue
            # Una descarga truncada puede dejar un fichero sin pista de vídeo.
            if dest.stat().st_size > 200_000 and _has_video_stream(dest):
                stats[name] += 1
                log.debug("  fuente <- %s '%s'", name, variant)
                return dest, False

    # 4. Biblioteca general de vídeo de la NASA. Va la última de las fuentes de
    #    vídeo porque es la más floja: aunque el filtro quita las ruedas de
    #    prensa, lo que queda son piezas institucionales, no plano de recurso.
    for variant in specific:
        url = _nasa_video(variant, pool)
        if not url:
            continue
        dest = raw_dir / f"nasa-video_{counter:03d}.mp4"
        try:
            download(url, dest, max_bytes=MAX_CLIP_BYTES)
        except Exception:
            continue
        if dest.stat().st_size > 200_000 and _has_video_stream(dest):
            stats["nasa-video"] += 1
            log.debug("  fuente <- nasa-video '%s'", variant)
            return dest, False

    # 5. Generarlo. La regla del canal es que en pantalla salga aquello de lo
    #    que habla la narración, así que antes de rendirse se fabrica el plano.
    if config.GENERATE_MISSING:
        from . import videogen

        dest = raw_dir / f"ia_{counter:03d}.mp4"
        sujeto = generic or query
        got = videogen.clip(sujeto, dest, seconds=10.0, seed=counter)
        if got and got.exists() and _has_video_stream(got):
            stats["generado"] += 1
            log.info("  fuente <- IA '%s'", sujeto)
            return got, False

    # 6. Imagen fija de archivo, solo como relleno y con el sujeto concreto:
    #    para un objeto con nombre propio suele ser lo único que existe.
    if config.CLIPS_ONLY:
        return None

    for variant in specific:
        for _ in range(4):
            url = _nasa_image(variant, pool)
            if not url:
                break
            dest = raw_dir / f"nasa-img_{counter:03d}{Path(url).suffix or '.jpg'}"
            try:
                download(url, dest, max_bytes=MAX_CLIP_BYTES)
            except Exception:
                continue
            if dest.stat().st_size > 30_000 and _looks_like_space(dest):
                stats["nasa-imagen"] += 1
                log.debug("  fuente <- nasa-imagen '%s' (relleno)", variant)
                return dest, True

    return None


def build_clips(scenes, workdir: Path, pool: AssetPool | None = None) -> list:
    """Monta cada escena como una secuencia de planos cortos.

    Devuelve las escenas con `clip_path` apuntando al vídeo de la escena, que es
    lo que espera assemble.render().
    """
    pool = pool or AssetPool()
    raw_dir = workdir / "raw"
    shots_dir = workdir / "shots"
    scenes_dir = workdir / "scenes"
    for d in (raw_dir, shots_dir, scenes_dir):
        d.mkdir(parents=True, exist_ok=True)

    stats = {"biblioteca": 0, "pexels": 0, "pixabay": 0, "svs": 0,
             "nasa-video": 0, "nasa-imagen": 0}

    if library_index():
        log.info("Biblioteca local: %d clips propios disponibles", len(library_index()))

    plan = shots.plan(scenes)

    # Cada plano recibe una de las búsquedas genéricas de su escena, rotando.
    # `subjects` recuerda a qué sujeto concreto pertenece cada una, para poder
    # enrutarla luego a los archivos científicos.
    subjects: dict[str, str] = {}
    by_scene = {s.index: s for s in scenes}
    for shot in plan:
        scene = by_scene[shot.scene_index]
        options = list(scene.visual_generic) or [scene.visual_query or "deep space stars"]
        shot.query = options[shot.index % len(options)]
        subjects.setdefault(shot.query, scene.visual_query or shot.query)

    bank = shots.ClipBank(
        lambda q: _fetch_source(subjects.get(q, q), q, pool, raw_dir, stats)
    )
    bank.set_total_shots(len(plan))

    log.info("Montaje: %d planos de %.0f-%.0f s para %d escenas, %d búsquedas distintas",
             len(plan), config.SHOT_MIN, config.SHOT_MAX, len(scenes), len(subjects))

    autocrops: dict[str, str] = {}
    fillers = 0

    for shot in plan:
        dest = shots_dir / f"{shot.scene_index:03d}_{shot.index:02d}.mp4"
        if dest.exists():
            shot.path = dest
            continue

        got = bank.segment(shot.query, shot.duration)
        if got:
            shot.source, shot.source_start, shot.is_image = got
        else:
            fillers += 1

        crop = ""
        if shot.source is not None and not shot.is_image:
            key = str(shot.source)
            if key not in autocrops:
                autocrops[key] = _autocrop(shot.source)
            crop = autocrops[key]

        try:
            shots.render_shot(shot, dest, crop)
        except RuntimeError:
            log.debug("  plano %s falló al procesar, se rellena", dest.stem)
            shot.source = None
            shots.render_shot(shot, dest)
            fillers += 1

    # Un plano por escena, con corte seco entre ellos.
    for scene in scenes:
        paths = [s.path for s in plan if s.scene_index == scene.index and s.path]
        dest = scenes_dir / f"scene_{scene.index:03d}.mp4"
        if not dest.exists():
            shots.concat_scene(paths, dest, scenes_dir / f"scene_{scene.index:03d}.txt")
        scene.clip_path = str(dest)

    # Los originales ya no hacen falta: cada plano se ha recortado y
    # recodificado. Son con diferencia lo que más pesa de la producción.
    if config.PRUNE:
        liberado = sum(f.stat().st_size for f in raw_dir.rglob("*") if f.is_file())
        shutil.rmtree(raw_dir, ignore_errors=True)
        log.info("Material original descartado: %.1f GB liberados", liberado / 1e9)

    videos, images = bank.stats
    used, worst = bank.diversity_report()
    log.info("Fuentes: %s", ", ".join(f"{k}={v}" for k, v in stats.items() if v) or "ninguna")
    log.info("Banco: %d clips y %d imágenes de relleno; %d planos procedurales",
             videos, images, fillers)
    log.info("Variedad: %d materiales distintos; el más repetido cubre %d de %d planos",
             used, worst, len(plan))

    if images and not videos:
        log.warning(
            "No se ha encontrado ni un clip de vídeo. Añade PEXELS_API_KEY y "
            "PIXABAY_API_KEY, o clips propios en library/."
        )
    # Regla empírica: por debajo de un material por cada seis planos el montaje
    # se nota repetido y se va de tema, porque acaba tirando de lo que haya.
    elif videos and len(plan) > videos * 6:
        log.warning(
            "Poca variedad: %d clips para %d planos. Los archivos gratuitos de la "
            "NASA no dan abasto con cortes de 6 s. Añade PEXELS_API_KEY y "
            "PIXABAY_API_KEY (gratis) o clips propios en library/.",
            videos, len(plan),
        )
    return scenes


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
