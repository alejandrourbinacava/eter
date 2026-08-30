"""Configuración central del pipeline de Éter.

Todo lo ajustable vive aquí. Los secretos se leen del entorno (GitHub Secrets
en CI, fichero .env en local) y nunca se escriben en disco.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

# En local se lee .env; en CI las variables ya vienen del entorno.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass
BRAND_DIR = ROOT / "brand"
CONTENT_DIR = ROOT / "content"
BUILD_DIR = Path(os.getenv("ETER_BUILD_DIR", ROOT / "build"))
CACHE_DIR = Path(os.getenv("ETER_CACHE_DIR", ROOT / ".cache"))

TOPICS_FILE = CONTENT_DIR / "topics.yml"
PUBLISHED_FILE = CONTENT_DIR / "published.json"
VOICE_GUIDE = BRAND_DIR / "voice_guide.md"
REFERENCE_SCRIPT = BRAND_DIR / "reference_script.txt"

# --------------------------------------------------------------------------
# Secretos
# --------------------------------------------------------------------------

AI33_API_KEY = os.getenv("AI33_API_KEY", "")
# Se acepta CLAUDE_API_KEY como alias: es el nombre con el que mucha gente
# guarda esta clave, y fallar por el nombre del secreto es absurdo.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

# --------------------------------------------------------------------------
# Voz — ai33.pro / OpenSpeaker
# --------------------------------------------------------------------------

AI33_BASE = "https://api.ai33.pro"

# "Javier - Mature and commanding" (ElevenLabs vía ai33).
VOICE_ID = os.getenv("ETER_VOICE_ID", "elevenlabs_PToUZ7lhIUiz1SP94rGo")

# Éter narra a ~168 wpm. speed=1 en esta voz da exactamente ese ritmo.
VOICE_SPEED = float(os.getenv("ETER_VOICE_SPEED", "1"))

# Pausa insertada entre escenas al concatenar el audio (segundos).
SCENE_GAP = 0.45

# --------------------------------------------------------------------------
# Guion
# --------------------------------------------------------------------------

# El guion ES el producto: el ritmo, la tensión y que no suene a plantilla es
# donde se nota el modelo. Aquí no se ahorra.
SCRIPT_MODEL = os.getenv("ETER_SCRIPT_MODEL", "claude-opus-5")

# El plan también va con Opus por decisión del canal. Se probó Sonnet aquí
# —es rellenar un JSON con el guion ya escrito delante— y ahorraba unos 2 € al
# mes, que no compensa arriesgar la calidad de los títulos y las búsquedas.
PLAN_MODEL = os.getenv("ETER_PLAN_MODEL", "claude-opus-5")

# Objetivo de duración. Los vídeos reales del canal miden 13-15 min.
TARGET_MINUTES = float(os.getenv("ETER_TARGET_MINUTES", "14"))
WORDS_PER_MINUTE = 168
TARGET_WORDS = int(TARGET_MINUTES * WORDS_PER_MINUTE)

# Cada escena es un bloque de narración con su propio plano.
WORDS_PER_SCENE = 90

# --------------------------------------------------------------------------
# Vídeo
# --------------------------------------------------------------------------

WIDTH, HEIGHT = 1920, 1080
FPS = 30
CROSSFADE = 0.6  # fundido entre escenas (dentro de una escena el corte es seco)
VIDEO_CRF = 20
VIDEO_PRESET = "medium"

# --------------------------------------------------------------------------
# Ritmo de montaje
# --------------------------------------------------------------------------
# La base del vídeo son clips, nunca imágenes fijas, y ningún plano pasa de
# SHOT_MAX. Un vídeo de 14 minutos sale a unos 180 planos.

SHOT_MIN = 3.0
SHOT_MAX = 6.0

# Solo clips de vídeo. Con esto activado, el archivo fotográfico deja de ser
# el último recurso antes del plano procedural: una imagen fija con
# movimiento de cámara se distingue de un clip en cuanto se ve al lado de
# uno, y rompe el ritmo del montaje.
CLIPS_ONLY = os.getenv("ETER_CLIPS_ONLY", "1") not in ("0", "false", "no")

# Si ningún archivo tiene lo que se está diciendo, se genera el plano. La
# regla del canal es que en pantalla salga el sujeto del que habla la
# narración; un campo de estrellas genérico no vale. Ver videogen.py.
GENERATE_MISSING = os.getenv("ETER_GENERATE", "1") not in ("0", "false", "no")

# Tope de planos generados por vídeo. Cada uno tarda entre veinte y cuarenta
# segundos, así que sin límite un vídeo largo podría pasarse una hora
# generando. Pasado el tope se recurre al banco general y queda avisado.
MAX_GENERATED = int(os.getenv("ETER_MAX_GENERATED", "60"))

# Borrar los intermedios en cuanto dejan de hacer falta. Una producción de
# veinte minutos ocupa 6,6 GB entre material descargado y planos sueltos, y un
# runner de GitHub solo tiene unos 14 GB libres. En local interesa conservarlos
# para poder relanzar sin volver a descargar; en CI no hay segunda pasada.
PRUNE = os.getenv("ETER_PRUNE", "1" if os.getenv("CI") else "0") not in ("0", "false", "no")

# Patrón de duraciones que se recorre en bucle. Alternar largo y corto es lo
# que da sensación de ritmo; una duración constante se percibe como plantilla.
SHOT_RHYTHM = (5.5, 3.6, 4.8, 6.0, 3.2, 5.0, 4.2, 5.8, 3.8, 4.5)

# --------------------------------------------------------------------------
# Etalonaje
# --------------------------------------------------------------------------
# El montaje mezcla material de seis procedencias distintas —SVS de la NASA,
# vídeo institucional, bancos de stock, clips propios, generación por IA— y
# cada una llega con su propio contraste y su propia temperatura. Sin una capa
# final que los iguale se nota el remiendo en cada corte.
#
# Es lo mismo que hace un colorista: negros levantados, blancos contenidos,
# frío en las sombras y calor en las luces, y un grano finísimo por encima que
# disimula el salto de compresión entre fuentes.
GRADE_ENABLED = os.getenv("ETER_GRADE", "1") not in ("0", "false", "no")

# Intensidad del grano. A 0 se quita. Por encima de 12 se ve como ruido.
GRAIN = float(os.getenv("ETER_GRAIN", "7"))

# --------------------------------------------------------------------------
# Música
# --------------------------------------------------------------------------

# Medido en el máster del vídeo de la estrella de neutrones: a -25 la música
# quedaba 16,8 dB por debajo de la narración y casi no se oía. El estándar
# de documental son 11-13 dB, así que sube 5.
MUSIC_DB = float(os.getenv("ETER_MUSIC_DB", "-20"))  # bajo la voz

# --------------------------------------------------------------------------
# Transiciones
# --------------------------------------------------------------------------
# Paleta que se recorre en bucle en los cambios de ESCENA. Dentro de una escena
# el corte es seco: encadenar 180 planos con efectos sería insoportable.
#
# Domina el fundido cruzado porque es el que no se nota, que es lo que quieres
# la mayor parte del tiempo. `fadeblack` da un respiro y marca los cambios de
# bloque del guion. `dissolve` y `smoothleft` aportan variedad sin llamar la
# atención. Fuera quedan barridos, deslizamientos y pixelados: delatan la
# plantilla al instante.
TRANSITIONS = (
    "fade", "fade", "fadeblack", "fade", "dissolve",
    "fade", "smoothleft", "fade", "fadeblack", "fade",
    "circleopen", "fade", "fade", "smoothright", "fade",
)

# --------------------------------------------------------------------------
# Efectos de sonido de transición
# --------------------------------------------------------------------------
# Nivel por debajo de la voz. A -20 dB los disparos quedaban solo 3 o 4 dB por
# encima del resto de la mezcla —medido en el máster del primer vídeo largo— y
# no se oían. A -12 se perciben sin tapar la narración.
SFX_DB = float(os.getenv("ETER_SFX_DB", "-12"))

# El impacto tarda esto en llegar a su pico, medido sobre su envolvente. Se
# dispara con esa antelación para que el golpe caiga EN la palabra marcada.
IMPACT_PEAK = 0.40

# El impacto va sobre las frases que rematan; riser y brillo, en los cambios
# de escena. Nunca en los cortes de plano.
SFX_ENABLED = os.getenv("ETER_SFX", "1") not in ("0", "false", "no")

SFX_CACHE = CACHE_DIR / "sfx"

# Rótulos de frase clave sobre el plano, en las mismas frases de remate que
# llevan el golpe de sonido. Ver captions.py.
CAPTIONS_ENABLED = os.getenv("ETER_CAPTIONS", "1") not in ("0", "false", "no")

# Tres sonidos, cada uno con su función. Duración en segundos.
SFX_PROMPTS = {
    "impacto": (
        "Deep cinematic sub-bass impact hit, dark and heavy, with a long "
        "reverberant tail decaying into silence. No music, no melody, no drums. "
        "Documentary scene transition.", 4,
    ),
    "riser": (
        "Slow reverse riser swelling from silence, dark and tense, cut short at "
        "the peak. Airy, cinematic, unsettling. No music, no melody.", 3,
    ),
    "brillo": (
        "Sparse metallic shimmer, a single struck resonance with a long cold "
        "ringing tail. Mysterious, distant, space documentary sting. No melody.", 4,
    ),
    # Corto a propósito: en el gancho suena cada tres segundos y uno con cola
    # larga se solaparía consigo mismo hasta volverse ruido continuo.
    "corte": (
        "Very short dark air whoosh, a quick pass of pressure with almost no "
        "tail. Dry, subtle, cinematic edit transition. No music, no melody, "
        "no impact hit.", 2,
    ),
}

# Gancho: durante los primeros segundos suena un efecto en CADA corte de plano,
# que caen entre 3 y 6 s. Es una técnica de retención —el primer minuto decide
# si el espectador se queda— y funciona porque el sonido coincide con el corte
# de imagen, no con una rejilla ciega.
SFX_HOOK_SECONDS = float(os.getenv("ETER_SFX_HOOK", "60"))

# Se genera con Suno a través de ai33 si no hay nada en brand/music.
MUSIC_PROMPT = (
    "Dark cinematic space documentary underscore. Slow sustained synth pads in a "
    "minor key, deep sub bass drone, sparse piano notes with long reverb, a subtle "
    "pulsing heartbeat rhythm. Mysterious, vast, unsettling but restrained. No drum "
    "hits, no foreground melody, nothing that competes with a narrator. Ambient, "
    "atmospheric, continuous."
)
MUSIC_CACHE = CACHE_DIR / "music"

# --------------------------------------------------------------------------
# Publicación
# --------------------------------------------------------------------------

# "public" publica al instante; "private" sube en privado para revisión manual.
PRIVACY = os.getenv("ETER_PRIVACY", "public")
YT_CATEGORY_ID = "28"  # Ciencia y tecnología
YT_LANGUAGE = "es"

CHANNEL_NAME = "Éter"
CHANNEL_ID = "UCwj7Ry45KcedtaIcpSGeN-A"


def require(name: str, value: str) -> str:
    """Falla pronto y con un mensaje claro si falta un secreto."""
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno {name}. "
            f"Defínela en .env (local) o en Settings → Secrets → Actions (CI)."
        )
    return value
