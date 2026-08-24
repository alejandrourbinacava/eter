"""Lecho musical: misterio, instrumental, por debajo de la voz.

Orden de preferencia:

  1. Un fichero en `brand/music/`. Si pones ahí tus propias pistas, mandan.
  2. Una pista generada con Suno a través de ai33 y cacheada en `.cache/music/`.

La música va a `config.MUSIC_DB` (-25 dB) y además pasa por un compresor con
cadena lateral: cada vez que entra la voz, la música cede unos decibelios sola.
Un nivel fijo suena bien en los silencios y estorba en la narración; el ducking
resuelve las dos cosas.

Se cachea a propósito: una pista sirve para varios vídeos, y así el canal tiene
una identidad sonora reconocible en vez de una música distinta cada día.
"""

from __future__ import annotations

import hashlib
import random

from . import config
from .util import download, http, log


def _suno(prompt: str) -> str | None:
    """Lanza la generación y devuelve la URL del audio terminado."""
    from .ai33 import _headers, wait_for_task

    r = http(
        "POST",
        f"{config.AI33_BASE}/v1s/task/music-generation",
        headers={**_headers(), "Content-Type": "application/json"},
        json={
            "create_mode": "simple",
            "gpt_description_prompt": prompt[:500],
            "make_instrumental": True,
        },
        timeout=90,
    )
    payload = r.json()
    if not payload.get("success"):
        log.warning("Suno rechazó la petición: %s", payload)
        return None

    task = wait_for_task(payload["task_id"], timeout=900)
    meta = task.get("metadata") or {}
    urls = meta.get("all_audio_urls") or []
    if meta.get("audio_url"):
        urls = [meta["audio_url"], *urls]
    for clip in (meta.get("suno_result") or {}).get("clips") or []:
        if clip.get("audio_url"):
            urls.append(clip["audio_url"])
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        log.warning("Suno terminó sin audio (%d créditos)", task.get("credit_cost", 0))
        return None
    log.info("Música generada (%d créditos)", task.get("credit_cost", 0))
    return urls[0]


def track(seed: str = "") -> "config.Path | None":  # type: ignore[name-defined]
    """Devuelve la pista a usar, generándola si hace falta."""
    from pathlib import Path

    # 1. Tus propias pistas.
    own_dir = config.BRAND_DIR / "music"
    if own_dir.exists():
        own = sorted(p for p in own_dir.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav"))
        if own:
            pick = random.Random(seed or "eter").choice(own)
            log.info("Música: %s (brand/music)", pick.name)
            return pick

    # 2. Caché de pistas generadas.
    cache: Path = config.MUSIC_CACHE
    cache.mkdir(parents=True, exist_ok=True)
    cached = sorted(p for p in cache.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav"))

    # Se reutiliza lo cacheado casi siempre; de vez en cuando se amplía el
    # repertorio para que no sea siempre exactamente la misma pista.
    if cached and (len(cached) >= 4 or random.Random(seed).random() > 0.35):
        pick = random.Random(seed or "eter").choice(cached)
        log.info("Música: %s (caché)", pick.name)
        return pick

    if not config.AI33_API_KEY:
        return cached[0] if cached else None

    try:
        url = _suno(config.MUSIC_PROMPT)
    except Exception as exc:
        log.warning("No se pudo generar música: %s", exc)
        return cached[0] if cached else None

    if not url:
        return cached[0] if cached else None

    name = hashlib.md5(url.encode()).hexdigest()[:10]
    dest = cache / f"eter_{name}.mp3"
    try:
        download(url.split("?")[0], dest)
    except Exception as exc:
        log.warning("Fallo al descargar la música: %s", exc)
        return cached[0] if cached else None

    log.info("Música: %s (nueva)", dest.name)
    return dest
