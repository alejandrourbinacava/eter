"""Efectos de sonido de transición.

Tres sonidos, generados una vez con la API de efectos de ElevenLabs a través de
ai33 y cacheados en `.cache/sfx/`. Cuestan 50 créditos por segundo, así que la
paleta entera sale por 550 y luego sirve para todos los vídeos: eso además le
da al canal una firma sonora reconocible, que es justo lo que se busca.

Qué hace cada uno, medido sobre su envolvente real:

  impacto   Pico a los 0,4 s y cola larga hasta el silencio. Va EN el corte.
            Es el que da peso al cambio de escena.
  riser     Arranca en silencio y crece hasta el final. Va ANTES del corte, de
            modo que su pico caiga justo donde entra la escena nueva.
  brillo    Golpe metálico seco con cola fría. Se reserva para los dos o tres
            giros grandes del guion.

La contención es la mitad del trabajo. Los efectos van solo en los cambios de
escena —unos veinticinco por vídeo—, nunca en los ciento ochenta cortes de
plano. Un golpe cada cuatro segundos convierte un documental en un tráiler.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .util import ffmpeg, http, log, probe_duration


def _fetch(name: str, prompt: str, seconds: int, dest: Path) -> Path | None:
    from .ai33 import _headers, wait_for_task

    r = http(
        "POST",
        f"{config.AI33_BASE}/v1/task/sound-effect",
        headers={**_headers(), "Content-Type": "application/json"},
        json={
            "text": prompt,
            "duration_seconds": seconds,
            "prompt_influence": 0.6,
            "loop": False,
            "model_id": "eleven_text_to_sound_v2",
        },
        timeout=90,
    )
    payload = r.json()
    if not payload.get("success"):
        log.warning("Efecto «%s» rechazado: %s", name, payload)
        return None

    task = wait_for_task(payload["task_id"], timeout=600)
    url = (task.get("metadata") or {}).get("audio_url")
    if not url:
        log.warning("Efecto «%s» sin audio", name)
        return None

    # Ojo: este CDN devuelve 403 al User-Agent por defecto de urllib, así que
    # la descarga tiene que ir por la sesión de util.http.
    from .util import download

    download(url, dest)
    log.info("Efecto «%s» generado (%d créditos)", name, task.get("credit_cost", 0))
    return dest


def palette() -> dict[str, Path]:
    """Los tres efectos, generándolos la primera vez."""
    cache: Path = config.SFX_CACHE
    cache.mkdir(parents=True, exist_ok=True)

    out: dict[str, Path] = {}
    for name, (prompt, seconds) in config.SFX_PROMPTS.items():
        dest = cache / f"{name}.mp3"
        if dest.exists() and dest.stat().st_size > 4000:
            out[name] = dest
            continue
        if not config.AI33_API_KEY:
            continue
        try:
            got = _fetch(name, prompt, seconds, dest)
        except Exception as exc:
            log.warning("No se pudo generar el efecto «%s»: %s", name, exc)
            got = None
        if got:
            out[name] = got
    return out


def plan_cues(scenes) -> list[tuple[float, str]]:
    """Dónde y qué suena. Devuelve (segundo, nombre_del_efecto).

    El instante de cada cambio de escena es la suma de las escenas anteriores
    más sus pausas, el mismo reloj que usa voice.mix para concatenar el audio.
    """
    cues: list[tuple[float, str]] = []
    clock = 0.0
    boundaries: list[float] = []
    for scene in scenes[:-1]:
        clock += scene.duration + config.SCENE_GAP
        boundaries.append(clock)

    total = len(boundaries)
    for i, at in enumerate(boundaries, start=1):
        # El impacto cae en el corte, siempre.
        cues.append((at, "impacto"))

        # El riser tiene que MORIR en el corte, así que empieza antes.
        if i % 3 == 0:
            riser = config.SFX_PROMPTS["riser"][1]
            start = at - riser + 0.15
            if start > 0.5:
                cues.append((start, "riser"))

        # El brillo se reserva para la grieta del principio y el cierre.
        if i == 1 or i == total:
            cues.append((at, "brillo"))

    return sorted(cues)


def build_bed(cues: list[tuple[float, str]], sounds: dict[str, Path],
              duration: float, dest: Path) -> Path | None:
    """Monta todos los efectos en una sola pista del largo del vídeo.

    Cada disparo entra como una entrada independiente de ffmpeg. Es válido
    repetir el mismo fichero varias veces en la línea de órdenes, y sale mucho
    más simple que partir la señal con `asplit`.
    """
    usable = [(at, sounds[name]) for at, name in cues if name in sounds and at < duration]
    if not usable:
        return None

    inputs: list[str] = []
    steps: list[str] = []
    for i, (at, path) in enumerate(usable):
        inputs += ["-i", str(path)]
        delay = int(at * 1000)
        steps.append(f"[{i}:a]adelay={delay}|{delay},aformat=sample_fmts=fltp:sample_rates=44100"
                     f":channel_layouts=stereo[s{i}]")

    chain = "".join(f"[s{i}]" for i in range(len(usable)))
    graph = (
        ";".join(steps)
        + f";{chain}amix=inputs={len(usable)}:duration=longest:normalize=0,"
        f"volume={config.SFX_DB}dB,apad[out]"
    )

    ffmpeg(
        inputs
        + [
            "-filter_complex", graph,
            "-map", "[out]", "-t", f"{duration:.3f}",
            "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
            str(dest),
        ]
    )
    log.info("Efectos de transición: %d disparos en %.1f min",
             len(usable), probe_duration(dest) / 60)
    return dest


def bed_for(scenes, workdir: Path, duration: float) -> Path | None:
    """Atajo: paleta, plan y mezcla en un paso."""
    if not config.SFX_ENABLED:
        return None
    sounds = palette()
    if not sounds:
        log.warning("Sin efectos de transición disponibles")
        return None
    cues = plan_cues(scenes)
    dest = workdir / "audio" / "sfx.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        return build_bed(cues, sounds, duration, dest)
    except RuntimeError as exc:
        log.warning("No se pudo montar la pista de efectos: %s", exc)
        return None
