"""Generación de la imagen de la miniatura.

Las miniaturas del canal se apoyan en composiciones fotorrealistas hechas a
medida —una galaxia espiral con una nave y su estela, el limbo de la Tierra con
una antena y una onda—. El archivo de la NASA da imagen real y correcta, pero
no esa dramatización, así que para igualarlas hace falta generarla.

Proveedores, por orden de preferencia:

  openai   `gpt-image-1`. Requiere OPENAI_API_KEY. Es la vía que funciona.
  ai33     Está implementado en `ai33.generate_image` y DESACTIVADO. A fecha de
           24/08/2026 sus tareas terminan con status `done`, cobran los créditos
           y devuelven `result_images` vacío; comprobado con seedream-5-pro,
           gpt-image-2 y recraft-v4.1, y de nuevo dos horas más tarde.

Si no hay ninguna clave, `hero()` devuelve None y la miniatura tira del archivo
de la NASA como hasta ahora. El pipeline nunca se cae por esto.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from . import config
from .util import http, log

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("ETER_IMAGE_MODEL", "gpt-image-1")

# El estilo de la miniatura no lo decide el modelo de guion: es constante del
# canal y vive aquí. Se le pide expresamente que no escriba nada, porque el
# texto lo pone thumbnail.py con la tipografía de marca.
STYLE = (
    "Photorealistic cinematic space documentary key art, 16:9. Deep black "
    "background with the subject lit from one side, dramatic and high contrast, "
    "rich detail, subtle lens flare, NASA visualization aesthetic. The frame "
    "falls off to pure black towards the edges. Absolutely no text, no letters, "
    "no numbers, no watermark, no logo, no user interface."
)


def prompt_for(topic: dict, plan=None) -> str:
    """Construye el prompt a partir del tema del vídeo."""
    subject = getattr(plan, "thumb_prompt", "") if plan is not None else ""
    if not subject and plan is not None and getattr(plan, "scenes", None):
        subject = plan.scenes[0].visual_prompt or ""
    if not subject:
        # Último recurso. El ángulo está en español y los modelos de imagen
        # rinden peor, pero es preferible a no generar nada.
        subject = topic.get("angle") or topic.get("title_hint") or "deep space"
    return f"{subject.strip()} — {STYLE}"


def _openai(prompt: str, dest: Path) -> Path | None:
    r = http(
        "POST",
        "https://api.openai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "prompt": prompt[:4000],
            "size": "1536x1024",   # 3:2, se recorta a 16:9 en thumbnail.py
            "quality": "high",
            "n": 1,
        },
        timeout=300,
    )
    payload = r.json()
    items = payload.get("data") or []
    if not items:
        log.warning("OpenAI no devolvió imagen: %s", str(payload)[:300])
        return None

    first = items[0]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if first.get("b64_json"):
        dest.write_bytes(base64.b64decode(first["b64_json"]))
    elif first.get("url"):
        from .util import download

        download(first["url"], dest)
    else:
        log.warning("Respuesta de OpenAI sin b64_json ni url")
        return None

    log.info("Imagen de miniatura generada con %s", OPENAI_MODEL)
    return dest


def hero(topic: dict, dest: Path, plan=None) -> Path | None:
    """Genera la imagen de la miniatura, o None si no hay proveedor."""
    if not OPENAI_API_KEY:
        return None
    prompt = prompt_for(topic, plan)
    log.debug("  prompt de miniatura: %s", prompt[:140])
    try:
        return _openai(prompt, dest)
    except Exception as exc:
        log.warning("No se pudo generar la imagen de miniatura: %s", exc)
        return None


def available() -> bool:
    return bool(OPENAI_API_KEY)
