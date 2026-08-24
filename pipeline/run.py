"""Orquestador. Un vídeo de Éter, de principio a fin.

    python -m pipeline.run                      # todo el ciclo y publicar
    python -m pipeline.run --dry-run            # renderiza pero no sube
    python -m pipeline.run --script-only        # solo el guion, para revisar la voz
    python -m pipeline.run --topic 3            # forzar un tema de la cola
    python -m pipeline.run --minutes 4          # prueba corta y barata

Cada ejecución trabaja en build/AAAA-MM-DD-slug/ y deja ahí todos los
intermedios. Si algo falla y se relanza, lo ya generado se reaprovecha.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import assemble, config, music, script_gen, thumbnail, topics, visuals, voice
from .util import log, require_binaries, setup_logging, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline de publicación de Éter")
    parser.add_argument("--dry-run", action="store_true", help="no subir a YouTube")
    parser.add_argument("--script-only", action="store_true", help="parar tras el guion")
    parser.add_argument("--no-render", action="store_true", help="parar tras la locución")
    parser.add_argument("--topic", type=int, help="índice del tema en topics.yml")
    parser.add_argument("--minutes", type=float, help="sobrescribe la duración objetivo")
    parser.add_argument("--privacy", choices=["public", "unlisted", "private"])
    args = parser.parse_args(argv)

    setup_logging()
    require_binaries()

    if args.minutes:
        config.TARGET_MINUTES = args.minutes
        config.TARGET_WORDS = int(args.minutes * config.WORDS_PER_MINUTE)
    if args.privacy:
        config.PRIVACY = args.privacy

    # ---- tema ------------------------------------------------------------
    if args.topic is not None:
        topic = topics.load_topics()[args.topic]
    else:
        topic = topics.next_topic()
    log.info("Tema: %s", topic["title_hint"])

    workdir = config.BUILD_DIR / f"{dt.date.today():%Y-%m-%d}-{_slug(topic['title_hint'])}"
    workdir.mkdir(parents=True, exist_ok=True)

    # ---- guion -----------------------------------------------------------
    plan_file = workdir / "plan.json"
    if plan_file.exists():
        log.info("Reutilizando el guion ya generado en %s", plan_file.name)
        plan = _load_plan(plan_file)
    else:
        history = [e.get("title", "") for e in topics.load_history()]
        plan = script_gen.build_plan(topic, history)
        write_json(plan_file, plan.to_dict())

    (workdir / "guion.txt").write_text(
        f"{plan.title}\n{'=' * len(plan.title)}\n\n{plan.narration}\n",
        encoding="utf-8",
    )
    log.info("Título: %s", plan.title)
    log.info("Miniatura: %s", plan.thumb_word)

    if args.script_only:
        log.info("Guion en %s", workdir / "guion.txt")
        return 0

    # ---- locución --------------------------------------------------------
    voice.narrate(plan.scenes, workdir)
    srt = voice.write_srt(plan.scenes, workdir / "subtitulos.srt")
    audio = voice.mix(plan.scenes, workdir, music.track(plan.title))
    write_json(plan_file, plan.to_dict())

    if args.no_render:
        return 0

    # ---- imagen ----------------------------------------------------------
    visuals.build_clips(plan.scenes, workdir)
    write_json(plan_file, plan.to_dict())

    video = assemble.render(plan.scenes, audio, workdir / "video.mp4")

    # ---- miniatura -------------------------------------------------------
    hero = _pick_hero(plan, workdir, video)
    thumb = thumbnail.build(plan.thumb_word, hero, workdir / "miniatura.jpg")

    description = _full_description(plan)
    (workdir / "descripcion.txt").write_text(description, encoding="utf-8")

    if args.dry_run:
        log.info("Modo prueba: no se sube. Revisa %s", workdir)
        return 0

    # ---- publicación -----------------------------------------------------
    from . import youtube

    video_id = youtube.upload(
        video,
        title=plan.title,
        description=description,
        tags=plan.tags,
        thumbnail=thumb,
        captions=srt,
    )
    topics.record(topic, plan, video_id)

    if topics.remaining() < topics.MIN_BACKLOG:
        try:
            topics.replenish()
        except Exception as exc:  # nunca tumbar una publicación exitosa
            log.warning("El reabastecimiento de temas falló: %s", exc)

    return 0


# --------------------------------------------------------------------------


def _slug(text: str) -> str:
    from .util import slugify

    return slugify(text, 40)


def _load_plan(path: Path) -> script_gen.VideoPlan:
    from .util import read_json

    data = read_json(path, {})
    plan = script_gen.VideoPlan(
        topic=data["topic"],
        title=data["title"],
        thumb_word=data["thumb_word"],
        description=data["description"],
        tags=data["tags"],
        narration=data["narration"],
    )
    plan.scenes = [script_gen.Scene(**s) for s in data["scenes"]]
    return plan


def _pick_hero(plan, workdir: Path, video: Path) -> Path | None:
    """La imagen de la miniatura, por orden de preferencia.

    1. Una imagen dedicada del archivo de la NASA para el tema del vídeo.
    2. La mejor imagen fija que ya se haya descargado para alguna escena.
    3. Un fotograma del montaje, solo como último recurso: puede arrastrar
       rótulos quemados del material de origen.
    """
    query = plan.topic.get("keyword") or (plan.scenes[0].visual_query if plan.scenes else "")
    if query:
        try:
            hero = visuals.hero_image(query, workdir / "hero.jpg")
            if hero:
                return hero
        except Exception as exc:
            log.debug("Búsqueda de imagen de miniatura fallida: %s", exc)

    raw = workdir / "raw"
    if raw.exists():
        candidates = sorted(
            (p for p in raw.iterdir() if p.suffix.lower() in (".jpg", ".png")),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if candidates:
            return candidates[0]

    try:
        return thumbnail.hero_frame(video, workdir / "hero.jpg", at=12.0)
    except Exception:
        return None


def _full_description(plan) -> str:
    tags = [t for t in plan.tags[:3]]
    hashtags = " ".join("#" + t.replace(" ", "").replace("-", "") for t in tags)
    parts = [plan.description.strip()]
    if hashtags:
        parts.append(hashtags)
    parts.append(
        "Material de archivo: NASA / ESA / JPL-Caltech (dominio público) y "
        "bancos de vídeo de licencia libre."
    )
    return "\n\n".join(p for p in parts if p)[:5000]


if __name__ == "__main__":
    sys.exit(main())
