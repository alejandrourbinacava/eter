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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
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

SCRIPT_MODEL = os.getenv("ETER_SCRIPT_MODEL", "claude-opus-5")

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

# Patrón de duraciones que se recorre en bucle. Alternar largo y corto es lo
# que da sensación de ritmo; una duración constante se percibe como plantilla.
SHOT_RHYTHM = (5.5, 3.6, 4.8, 6.0, 3.2, 5.0, 4.2, 5.8, 3.8, 4.5)

# --------------------------------------------------------------------------
# Música
# --------------------------------------------------------------------------

MUSIC_DB = float(os.getenv("ETER_MUSIC_DB", "-25"))  # bajo la voz

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
