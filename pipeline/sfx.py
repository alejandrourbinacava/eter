"""Efectos de sonido de transición.

Cuatro sonidos, generados una vez con la API de efectos de ElevenLabs a través
de ai33 y cacheados en `.cache/sfx/`. Cuestan 50 créditos por segundo, así que
la paleta entera sale por unos 650 y luego sirve para todos los vídeos: eso
además le da al canal una firma sonora reconocible.

Qué hace cada uno, medido sobre su envolvente real:

  corte     Whoosh corto y seco, casi sin cola. Suena en CADA corte de plano
            del arranque, que caen entre tres y seis segundos. Es el gancho: el
            primer minuto decide si el espectador se queda. Tiene que ser corto
            justamente porque se repite cada pocos segundos; uno con cola larga
            se solaparía consigo mismo hasta volverse ruido continuo.
  impacto   Pico a los 0,4 s y cola larga hasta el silencio. Va sobre la frase
            que remata un razonamiento, no en el cambio de escena: un golpe en
            cada corte se convierte en un tic, y uno sobre «Ni uno.» subraya.
            El guionista marca esa frase y la transcripción de la locución da
            el segundo exacto en que se dice.
  riser     Arranca en silencio y crece hasta el final. Va ANTES del corte de
            escena, de modo que su pico caiga donde entra la escena nueva.
  brillo    Golpe metálico seco con cola fría. Se reserva para los dos o tres
            giros grandes del guion.

Pasado el gancho manda la contención: fuera de esos primeros segundos no hay
efectos en los cortes de plano, que son ciento ochenta, y como mucho un impacto
por bloque de narración. Un golpe cada cuatro segundos durante catorce minutos
convierte un documental en un tráiler.
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


def _locate(scene, phrase: str) -> float | None:
    """Segundo, dentro de la escena, en que empieza a decirse `phrase`.

    Se casa contra la lista de palabras de la transcripción, no contra el texto:
    así el tiempo es el real y no una estimación por longitud.

    Se prueba la frase entera y luego prefijos cada vez más cortos. Hace falta
    porque la transcripción devuelve lo que se OYE, no lo que está escrito: el
    guion pone «a 30 kilómetros por segundo» y la voz dice «treinta», así que
    una frase larga con cifras nunca casa entera. Y da igual: lo único que
    importa es dónde EMPIEZA, que es donde cae el golpe.
    """
    words = scene.words or []
    if not words:
        return None

    def norm(text: str) -> str:
        return "".join(c for c in text.lower() if c.isalnum())

    target = [norm(w) for w in phrase.split() if norm(w)]
    if not target:
        return None

    seq = [norm(w["text"]) for w in words]
    for length in (len(target), 6, 4, 3):
        if length > len(target):
            continue
        probe = target[:length]
        for i in range(len(seq) - length + 1):
            if seq[i:i + length] == probe:
                return float(words[i]["start"])
    return None


def _hook_cues(scenes, starts: list[float]) -> list[tuple[float, str]]:
    """El gancho: un efecto en cada corte de plano del arranque.

    No se usa una rejilla fija de tres segundos, sino los cortes reales del
    montaje, que caen entre tres y seis. Sale la misma densidad y además el
    sonido coincide con el cambio de imagen, que es lo que hace que se perciba
    como intención y no como un metrónomo.

    `shots.plan` es determinista y solo depende de la duración de las escenas,
    así que aquí se puede calcular el mismo reparto que hará el montaje más
    tarde, sin haber renderizado nada todavía.
    """
    if config.SFX_HOOK_SECONDS <= 0:
        return []

    from . import shots as shots_mod

    plan = shots_mod.plan(scenes)
    by_scene: dict[int, list] = {}
    for shot in plan:
        by_scene.setdefault(shot.scene_index, []).append(shot)

    cues: list[tuple[float, str]] = []
    for scene, start in zip(scenes, starts):
        if start > config.SFX_HOOK_SECONDS:
            break
        clock = start
        for i, shot in enumerate(by_scene.get(scene.index, [])):
            clock += shot.duration
            if clock > config.SFX_HOOK_SECONDS:
                break
            # Mayoría de whooshes cortos, con algún golpe para que respire.
            kind = "impacto" if i and i % 4 == 3 else "corte"
            cues.append((clock, kind))
    return cues


def plan_cues(scenes) -> list[tuple[float, str]]:
    """Dónde y qué suena. Devuelve (segundo, nombre_del_efecto).

    El reloj es el mismo que usa voice.mix al concatenar: cada escena empieza
    en la suma de las anteriores más sus pausas.

    El reparto de papeles:

      corte    En cada corte de plano de los primeros SFX_HOOK_SECONDS. Es el
               gancho: el primer minuto decide si el espectador se queda.
      impacto  Sobre la frase que remata un razonamiento, no en el corte de
               escena. Un golpe en cada cambio se vuelve un tic; un golpe sobre
               «Ni uno.» subraya. Como el sonido tarda IMPACT_PEAK en llegar a
               su pico, se dispara antes para que el pico caiga en la palabra.
      riser     Muere en el corte de escena, uno de cada tres.
      brillo    La grieta del principio y el cierre.
    """
    cues: list[tuple[float, str]] = []

    # Instante en que arranca cada escena.
    starts: list[float] = []
    clock = 0.0
    for scene in scenes:
        starts.append(clock)
        clock += scene.duration + config.SCENE_GAP

    cues += _hook_cues(scenes, starts)

    # --- impacto: sobre las frases marcadas por el guionista ---------------
    for scene, start in zip(scenes, starts):
        for phrase in (scene.emphasis or [])[:1]:
            inside = _locate(scene, phrase)
            if inside is None:
                log.debug("  sin tiempo para «%s», se omite el golpe", phrase[:40])
                continue
            at = start + inside - config.IMPACT_PEAK
            if at > 0.5:
                cues.append((at, "impacto"))

    # --- riser y brillo: en los cambios de escena --------------------------
    boundaries = starts[1:]
    total = len(boundaries)
    for i, at in enumerate(boundaries, start=1):
        if i % 3 == 0:
            riser = config.SFX_PROMPTS["riser"][1]
            begin = at - riser + 0.15
            if begin > 0.5:
                cues.append((begin, "riser"))
        if i == 1 or i == total:
            cues.append((at, "brillo"))

    return _declutter(sorted(cues))


# Cuando dos efectos caen casi encima, gana el de más peso narrativo.
_PRIORITY = {"impacto": 4, "riser": 3, "brillo": 2, "corte": 1}
MIN_GAP = 1.2


def _declutter(cues: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Descarta disparos que se pisan entre sí.

    Un impacto y un riser separados por tres décimas no se leen como dos
    intenciones: se leen como barro. Cuando compiten, sobrevive el impacto,
    que es el que subraya lo que se está diciendo.
    """
    kept: list[tuple[float, str]] = []
    for at, kind in cues:
        clash = next(
            (j for j, (t, _) in enumerate(kept) if abs(t - at) < MIN_GAP), None
        )
        if clash is None:
            kept.append((at, kind))
            continue
        if _PRIORITY[kind] > _PRIORITY[kept[clash][1]]:
            log.debug("  «%s» desplaza a «%s» en %.2fs", kind, kept[clash][1], at)
            kept[clash] = (at, kind)
        else:
            log.debug("  «%s» descartado en %.2fs, choca con «%s»",
                      kind, at, kept[clash][1])
    return sorted(kept)


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
