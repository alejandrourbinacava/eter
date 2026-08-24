"""Cliente de ai33.pro / OpenSpeaker.

Solo se usan endpoints documentados en la API pública (cabecera `xi-api-key`).

Nota verificada el 24/08/2026: `/veo3/task/generate-video` existe en la web pero
responde 401 a las claves de API, así que la generación de vídeo por IA no es
accesible desde aquí. Y `/v1i/task/generate-image` termina con status `done`,
cobra los créditos y devuelve `result_images` vacío en los tres modelos
probados (seedream-5-pro, gpt-image-2, recraft-v4.1). Por eso `generate_image`
existe pero está desactivado por defecto — ver visuals.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config
from .util import download, http, log

BASE = config.AI33_BASE


def _headers() -> dict:
    return {"xi-api-key": config.require("AI33_API_KEY", config.AI33_API_KEY)}


def credits() -> int:
    r = http("GET", f"{BASE}/v1/credits", headers=_headers())
    return int(r.json().get("credits", 0))


def health() -> dict:
    return http("GET", f"{BASE}/v1/health-check", headers=_headers()).json().get("data", {})


def wait_for_task(task_id: str, *, timeout: int = 900, interval: float = 4.0) -> dict:
    """Sondea /v1/task/{id} hasta que termina. Cuesta 1 token de rate-limit."""
    deadline = time.time() + timeout
    last_progress = -1
    while time.time() < deadline:
        task = http("GET", f"{BASE}/v1/task/{task_id}", headers=_headers()).json()
        status = task.get("status")
        progress = task.get("progress")
        if progress != last_progress:
            log.debug("  tarea %s: %s %s%%", task_id[:8], status, progress)
            last_progress = progress
        if status == "done":
            return task
        if status in ("error", "failed"):
            raise RuntimeError(f"Tarea {task_id} falló: {task.get('error_message')}")
        time.sleep(interval)
    raise TimeoutError(f"Tarea {task_id} no terminó en {timeout}s")


# --------------------------------------------------------------------------
# Texto a voz
# --------------------------------------------------------------------------


def tts(
    text: str,
    dest: Path,
    *,
    voice_id: str | None = None,
    speed: float | None = None,
    transcript: bool = False,
) -> list[dict]:
    """Sintetiza `text` y deja el MP3 en `dest`.

    Con `transcript=True` devuelve además la lista de palabras con sus tiempos
    de inicio y fin en segundos, con precisión de milisegundos. Cuesta unos 21
    créditos por escena y es lo que permite hacer caer un golpe de sonido justo
    sobre una palabra concreta, en vez de estimarla por regla de tres.
    """
    voice_id = voice_id or config.VOICE_ID
    speed = config.VOICE_SPEED if speed is None else speed

    r = http(
        "POST",
        f"{BASE}/v3/text-to-speech",
        headers=_headers(),
        files={
            "text": (None, text),
            "voice_id": (None, voice_id),
            "speed": (None, str(speed)),
            "with_transcript": (None, "true" if transcript else "false"),
        },
        timeout=120,
    )
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(f"TTS rechazado: {payload}")

    task = wait_for_task(payload["task_id"])
    meta = task.get("metadata") or {}
    url = meta.get("audio_url")
    if not url:
        raise RuntimeError(f"TTS sin audio_url: {json.dumps(task)[:400]}")

    # La URL lleva ?name=...&dl=1; el fichero está en la ruta base.
    download(url.split("?")[0], dest)
    log.debug("  voz: %s (%d créditos)", dest.name, task.get("credit_cost", 0))

    if not transcript:
        return []
    return _words(meta.get("json_url"))


def _words(json_url: str | None) -> list[dict]:
    """Descarga la transcripción y se queda con las palabras reales."""
    if not json_url:
        return []
    try:
        data = http("GET", json_url.split("?")[0]).json()
    except (RuntimeError, ValueError):
        log.debug("  sin transcripción utilizable")
        return []
    if isinstance(data, list):
        data = data[0] if data else {}
    return [
        {"text": w["text"], "start": float(w["start"]), "end": float(w["end"])}
        for w in (data.get("words") or [])
        if w.get("type") == "word" and w.get("text")
    ]


# --------------------------------------------------------------------------
# Imagen (ver aviso de la cabecera del módulo)
# --------------------------------------------------------------------------


def generate_image(
    prompt: str,
    dest: Path,
    *,
    model: str = "bytedance-seedream-5-pro",
    aspect_ratio: str = "16:9",
    resolution: str = "2K",
) -> Path | None:
    """Genera una imagen. Devuelve None si la API no entrega el resultado
    (comportamiento observado a 24/08/2026), para que el llamante degrade."""
    r = http(
        "POST",
        f"{BASE}/v1i/task/generate-image",
        headers=_headers(),
        files={
            "prompt": (None, prompt),
            "model_id": (None, model),
            "generations_count": (None, "1"),
            "model_parameters": (
                None,
                json.dumps({"aspect_ratio": aspect_ratio, "resolution": resolution}),
            ),
        },
        timeout=120,
    )
    payload = r.json()
    if not payload.get("success"):
        log.warning("Generación de imagen rechazada: %s", payload)
        return None

    task = wait_for_task(payload["task_id"])
    images = (task.get("metadata") or {}).get("result_images") or []
    if not images:
        log.warning(
            "ai33 terminó la imagen (%d créditos) pero devolvió result_images vacío. "
            "Se degrada a archivo real.",
            task.get("credit_cost", 0),
        )
        return None

    first = images[0]
    url = first if isinstance(first, str) else first.get("url")
    return download(url, dest)
