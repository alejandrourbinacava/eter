"""Tipografía en pantalla: subtítulos karaoke y palabras clave cinéticas.

El vídeo no llevaba texto quemado. Solo los rótulos PNG puntuales de
`captions`, seis o siete por vídeo. Un documental de divulgación de 2026 tiene
texto en pantalla casi todo el rato: es lo que permite verlo sin sonido, lo que
sostiene la retención en los minutos flojos y, sobre todo, lo que separa un
montaje que parece dirigido de uno que parece automático.

Todo el texto sale de UN SOLO fichero .ass. Es una decisión de arquitectura,
no de estilo: en `assemble` cada rótulo PNG cuesta un input `-loop 1 -i` y dos
pasos más en el filter_complex, así que meter cuatrocientos bloques de
subtítulo por esa vía es inviable. Un .ass renderiza texto ilimitado con un
único filtro y cero inputs.

Los tiempos por palabra ya existen: `voice.narrate` guarda
`audio/scene_NNN.words.json` con {text, start, end} de cada palabra, medido
sobre el audio de verdad. La sincronía no se estima, se lee.

    from . import subs
    ruta = subs.build(scenes, workdir, evitar=[(12.0, 15.5), ...])
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import config
from .util import log

# ---------------------------------------------------------------------------
# Bloques de subtítulo
# ---------------------------------------------------------------------------

# Cuántas palabras caben en un bloque. Por debajo de tres el texto salta tanto
# que cansa; por encima de cinco deja de leerse de un vistazo y se convierte en
# un párrafo. Cuatro o cinco es lo que usa todo el formato.
MAX_PALABRAS = 5
MIN_PALABRAS = 3

# Un silencio de más de esto corta el bloque aunque no esté lleno: respetar las
# pausas del locutor es lo que hace que el texto respire con la voz.
PAUSA = 0.42

# Hueco entre un bloque y el siguiente. Sin él, dos bloques consecutivos se
# pisan un fotograma y producen un parpadeo.
SEPARACION = 0.06

# Un bloque no se queda menos de esto en pantalla aunque se diga muy rápido.
MIN_BLOQUE = 0.55


def _bloques(words: list[dict]) -> list[list[dict]]:
    """Agrupa las palabras en bloques legibles, cortando por pausas."""
    fuera: list[list[dict]] = []
    actual: list[dict] = []
    for i, w in enumerate(words):
        actual.append(w)
        siguiente = words[i + 1] if i + 1 < len(words) else None
        lleno = len(actual) >= MAX_PALABRAS
        pausa = (siguiente is not None
                 and siguiente["start"] - w["end"] >= PAUSA
                 and len(actual) >= MIN_PALABRAS)
        final = siguiente is None
        # Un punto o un cierre de interrogación corta, si el bloque ya tiene
        # cuerpo: leer una frase partida a la mitad es peor que un bloque corto.
        cierre = (len(actual) >= MIN_PALABRAS
                  and w["text"].rstrip()[-1:] in ".?!:")
        if lleno or pausa or final or cierre:
            fuera.append(actual)
            actual = []
    if actual:
        fuera.append(actual)
    return fuera


# ---------------------------------------------------------------------------
# Palabras clave cinéticas
# ---------------------------------------------------------------------------

# Una cada cuarenta segundos largos. Más satura y deja de subrayar nada.
CADA_CLAVE = 34.0
MIN_LETRAS = 7

_VACIAS = {
    "entonces", "tambien", "porque", "aunque", "mientras", "cualquier",
    "cualquiera", "siempre", "despues", "todavia", "bastante", "realmente",
    "practicamente", "simplemente", "exactamente", "precisamente",
    "aproximadamente", "posiblemente", "probablemente", "entonces",
    "entienden", "nosotros", "vosotros", "ustedes", "consigue", "conseguido",
    "significa", "significaria", "parecia", "pareceria", "tendria",
    "estaria", "hubiera", "hubiese", "podria", "podrian", "deberia",
}


def _plano(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar."""
    limpio = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in limpio if not unicodedata.combining(c))


def _es_clave(palabra: str) -> bool:
    limpia = re.sub(r"[^\wáéíóúüñ]", "", palabra, flags=re.I)
    if len(limpia) < MIN_LETRAS:
        return False
    plano = _plano(limpia)
    if plano in _VACIAS:
        return False
    # Los verbos largos no subrayan nada; los sustantivos y adjetivos, sí.
    return not plano.endswith(("ando", "endo", "aron", "eron", "abamos"))


def _cifra(palabra: str) -> bool:
    """Una cifra con cuerpo. Es lo que más merece un remarque."""
    return bool(re.search(r"\d", palabra)) and len(re.sub(r"\D", "", palabra)) >= 2


# ---------------------------------------------------------------------------
# Escritura del .ass
# ---------------------------------------------------------------------------

def _tiempo(t: float) -> str:
    """Formato de tiempo de ASS: H:MM:SS.cc (centésimas)."""
    t = max(t, 0.0)
    h, resto = divmod(t, 3600)
    m, s = divmod(resto, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _texto(bruto: str) -> str:
    """Escapa lo que ASS interpreta como marcado."""
    return (bruto.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            .replace("\n", " ").strip())


# Los colores de ASS van en &HAABBGGRR: azul, verde y rojo AL REVÉS que en
# HTML, y con la transparencia delante. Un color puesto en orden RGB no falla,
# simplemente sale del color equivocado, que es peor.
_BLANCO = "&H00FFFFFF"
_ACENTO = "&H00FFD82B"   # 0x2BD8FF en RGB: el cian del canal
_BORDE = "&H00101010"
_SOMBRA = "&H90000000"


def _cabecera() -> str:
    # PlayRes tiene que coincidir con el tamaño real del render. Si no, libass
    # reescala por su cuenta y las posiciones absolutas dejan de cuadrar.
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.WIDTH}
PlayResY: {config.HEIGHT}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Anton,{_TAM_SUB},{_ACENTO},{_BLANCO},{_BORDE},{_SOMBRA},0,0,0,0,100,100,0.6,0,1,3.6,1.6,2,120,120,{_MARGEN},1
Style: Clave,Anton,{_TAM_CLAVE},{_BLANCO},{_BLANCO},{_BORDE},{_SOMBRA},0,0,0,0,100,100,4,0,1,0,0,5,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# Medido sobre el primer vídeo con subtítulos quemados: a 56 px sobre 1080 el
# texto queda como una línea fina en el borde y no compite con la imagen. La
# competencia los pone a casi el doble. 76 px ocupa lo que tiene que ocupar sin
# taparlo todo.
_TAM_SUB = 76
_TAM_CLAVE = 120

# Las bandas negras comen 100 px por abajo. El subtítulo va JUSTO encima de la
# banda, nunca a caballo: partido por el borde es ilegible.
_MARGEN = config.LETTERBOX + 42


def build(scenes, workdir: Path,
          evitar: list[tuple[float, float]] | None = None) -> Path | None:
    """Escribe `subs.ass` con el texto de todo el vídeo.

    `evitar` son los tramos que ya ocupa otro rótulo grande (los de
    `captions`): ahí el subtítulo se calla, porque dos bloques de texto a la
    vez no se leen, se estorban.
    """
    if not config.SUBS_ENABLED:
        return None

    evitar = evitar or []
    eventos: list[str] = []
    reloj = 0.0
    ultima_clave = -CADA_CLAVE
    n_bloques = n_claves = 0

    for scene in scenes:
        inicio = reloj
        reloj += scene.duration + config.SCENE_GAP
        palabras = getattr(scene, "words", None) or []
        if not palabras:
            continue

        for bloque in _bloques(palabras):
            t0 = inicio + bloque[0]["start"]
            t1 = inicio + max(bloque[-1]["end"], bloque[0]["start"] + MIN_BLOQUE)
            t1 = max(t1 - SEPARACION, t0 + 0.2)
            if any(t0 < b and t1 > a for a, b in evitar):
                continue

            # El karaoke de ASS va en CENTÉSIMAS de segundo, no en milisegundos
            # ni en segundos como el resto del fichero.
            partes = []
            for w in bloque:
                dura = max(int(round((w["end"] - w["start"]) * 100)), 1)
                partes.append(f"{{\\k{dura}}}{_texto(w['text'])}")
            texto = " ".join(partes)
            eventos.append(
                f"Dialogue: 0,{_tiempo(t0)},{_tiempo(t1)},Sub,,0,0,0,,"
                f"{{\\fad(70,70)}}{texto}"
            )
            n_bloques += 1

            # ¿Alguna palabra de este bloque merece salir grande?
            if t0 - ultima_clave < CADA_CLAVE:
                continue
            elegida = next((w for w in bloque if _cifra(w["text"])), None)
            elegida = elegida or next((w for w in bloque if _es_clave(w["text"])), None)
            if elegida is None:
                continue
            k0 = inicio + elegida["start"]
            k1 = k0 + 1.9
            if any(k0 < b and k1 > a for a, b in evitar):
                continue
            palabra = _texto(re.sub(r"[.,;:]$", "", elegida["text"])).upper()
            # Entra creciendo y con un rebote corto, y se va desvaneciendo. Los
            # tiempos de \t van en MILISEGUNDOS relativos al inicio del evento.
            eventos.append(
                f"Dialogue: 1,{_tiempo(k0)},{_tiempo(k1)},Clave,,0,0,0,,"
                f"{{\\an5\\pos({config.WIDTH // 2},{int(config.HEIGHT * 0.30)})"
                f"\\blur1.2\\fscx62\\fscy62\\alpha&HFF&"
                f"\\t(0,170,\\fscx106\\fscy106\\alpha&H00&\\blur0)"
                f"\\t(170,300,\\fscx100\\fscy100)"
                f"\\t(1500,1900,\\alpha&HFF&)}}{palabra}"
            )
            ultima_clave = t0
            n_claves += 1

    if not eventos:
        log.info("Sin transcripción por palabra: no se queman subtítulos.")
        return None

    destino = workdir / "subs.ass"
    destino.write_text(_cabecera() + "\n".join(eventos) + "\n", encoding="utf-8")
    log.info("Texto en pantalla: %d bloques de subtítulo y %d palabras clave",
             n_bloques, n_claves)
    return destino


def filtro(ruta: Path) -> str:
    """El filtro `ass=...` con la ruta escapada como espera ffmpeg.

    En Windows hay que convertir las barras y proteger los dos puntos de la
    unidad, porque dentro de un filter_complex los dos puntos separan
    argumentos y `C:` parte la ruta en dos.
    """
    p = str(ruta.resolve()).replace("\\", "/").replace(":", "\\:")
    fuentes = str((Path(__file__).resolve().parent.parent / "brand" / "fonts")
                  ).replace("\\", "/").replace(":", "\\:")
    return f"ass='{p}':fontsdir='{fuentes}'"
