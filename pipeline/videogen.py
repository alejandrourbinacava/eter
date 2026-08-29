"""Generación del plano cuando ningún archivo tiene lo que se está diciendo.

Es el último recurso antes del campo de estrellas procedural, y existe porque
la regla del canal es que en pantalla salga aquello de lo que habla la
narración. Si el guion dice «el Sol está ahí», tiene que haber un Sol.

Busqué un generador de vídeo por IA gratuito y sin clave. **No existe.** Lo
comprobado, no lo supuesto:

  Hugging Face      El endpoint de inferencia devuelve 401 sin token, tanto en
                    api-inference como en el router nuevo.
  fal.ai            401.
  Google Labs/Flow  Sin API pública: interfaz web con sesión iniciada.
  ai33 (tu cuenta)  `/veo3/task/generate-video` responde 401 a las claves de
                    API, y su generación de imagen cobra y devuelve vacío.

Lo más barato que sí funciona es DeepInfra, que expone treinta modelos de texto
a vídeo con precio por segundo. El más económico sale a 0,25 céntimos por
segundo: un plano de seis segundos cuesta metro y medio de céntimo, y treinta
planos generados en un vídeo salen por menos de medio euro.

Sin clave de DeepInfra queda el plan B: Pollinations genera imágenes gratis y
sin registro, y de ahí se saca un plano con movimiento de cámara. No es vídeo
de verdad y se avisa en el log, pero enseña el sujeto correcto, que es lo que
importa.
"""

from __future__ import annotations

import os
import time
import urllib.parse
from pathlib import Path

from . import config
from .util import download, ffmpeg, http, log, probe_duration

DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "")

# 0,25 céntimos por segundo a 480p. Ver la cabecera.
DEEPINFRA_MODEL = os.getenv("ETER_VIDEO_MODEL", "FastVideo/FastWan-QAD-FP8-1.3B")
DEEPINFRA_URL = "https://api.deepinfra.com/v1/inference"

POLLINATIONS = "https://image.pollinations.ai/prompt/"

STYLE = (
    "photorealistic cinematic space documentary footage, deep black space, "
    "dramatic side lighting, ultra detailed, slow camera movement, "
    "no text, no letters, no watermark, no user interface"
)


def _prompt(subject: str) -> str:
    return f"{subject.strip()}, {STYLE}"


# --------------------------------------------------------------------------
# Vídeo de verdad
# --------------------------------------------------------------------------


def _deepinfra(subject: str, seconds: float, dest: Path) -> Path | None:
    r = http(
        "POST",
        f"{DEEPINFRA_URL}/{DEEPINFRA_MODEL}",
        headers={
            "Authorization": f"bearer {DEEPINFRA_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "prompt": _prompt(subject),
            "num_frames": max(int(seconds * 16), 16),
            "width": 832,
            "height": 480,
        },
        timeout=600,
    )
    payload = r.json()

    url = payload.get("video_url") or payload.get("output")
    if isinstance(url, list):
        url = url[0] if url else None
    if not url:
        log.warning("DeepInfra no devolvió vídeo: %s", str(payload)[:220])
        return None

    if str(url).startswith("data:"):
        import base64

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(str(url).split(",", 1)[1]))
    else:
        download(str(url), dest)
    log.info("Plano generado con IA (%s): %s", DEEPINFRA_MODEL.split("/")[-1], subject[:44])
    return dest


# --------------------------------------------------------------------------
# Plan B sin clave: imagen generada, con movimiento
# --------------------------------------------------------------------------


def _pollinations(subject: str, dest: Path, seed: int = 0) -> Path | None:
    """Imagen gratuita y sin registro. Devuelve la ruta del JPEG."""
    url = (
        POLLINATIONS
        + urllib.parse.quote(_prompt(subject)[:900])
        + f"?width=1280&height=720&nologo=true&model=flux&seed={seed}"
    )
    try:
        download(url, dest)
    except Exception as exc:
        log.warning("Pollinations falló: %s", exc)
        return None
    if dest.stat().st_size < 10_000:
        return None
    return dest


def _animate(image: Path, dest: Path, seconds: float, seed: int) -> Path:
    """Convierte la imagen en un plano con un empuje de cámara lento."""
    frames = max(int(seconds * config.FPS), 1)
    modo = seed % 3
    if modo == 0:
        z, x, y = "min(zoom+0.0015,1.32)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif modo == 1:
        z, x, y = ("if(lte(zoom,1.0),1.32,max(zoom-0.0015,1.0))",
                   "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)")
    else:
        z, x, y = "min(zoom+0.0008,1.18)", f"(iw-iw/zoom)*(on/{frames})", "ih/2-(ih/zoom/2)"

    ffmpeg([
        "-loop", "1", "-i", str(image), "-t", f"{seconds:.3f}",
        "-vf",
        (f"scale={config.WIDTH * 3}:-2:flags=lanczos,"
         f"crop={config.WIDTH * 3}:{config.HEIGHT * 3}:(in_w-out_w)/2:(in_h-out_h)/2,"
         f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}'"
         f":s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS},setsar=1,format=yuv420p"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-g", str(config.FPS * 2), "-keyint_min", str(config.FPS),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-video_track_timescale", "30000",
        str(dest),
    ])
    return dest


# --------------------------------------------------------------------------
# API del módulo
# --------------------------------------------------------------------------


def available() -> bool:
    """Siempre hay algo: con clave, vídeo; sin ella, imagen animada."""
    return True


def clip(subject: str, dest: Path, seconds: float = 8.0, seed: int = 0) -> Path | None:
    """Un plano que enseña `subject`. Devuelve None solo si todo falla."""
    if DEEPINFRA_API_KEY:
        try:
            got = _deepinfra(subject, seconds, dest)
            if got and probe_duration(got) >= config.SHOT_MIN:
                return got
        except Exception as exc:
            log.warning("Generación de vídeo fallida (%s), se prueba con imagen", exc)

    # Sin clave de DeepInfra esto genera una IMAGEN y le pone un empuje de
    # cámara. Enseña el sujeto correcto, pero es una foto animada, y con
    # CLIPS_ONLY el canal pidió justo lo contrario: en el último vídeo se
    # colaron once planos así. Con la clave puesta no se llega hasta aquí.
    if config.CLIPS_ONLY:
        log.warning(
            "Sin DEEPINFRA_API_KEY no hay generación de vídeo real; el plano de "
            "'%s' se queda sin generar en vez de meter una imagen animada",
            subject[:48])
        return None

    image = dest.with_suffix(".jpg")
    if _pollinations(subject, image, seed):
        log.info("Plano generado a partir de imagen de IA: %s", subject[:48])
        return _animate(image, dest, seconds, seed)

    return None
