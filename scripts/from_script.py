"""Produce un vídeo a partir de un guion ya escrito, en Markdown.

Es la vía para los guiones que escribes tú, sin pasar por el generador. Hace
todo lo demás igual que el pipeline normal: locución, planos, montaje,
miniatura y, si se pide, subida.

    python scripts/from_script.py guion.md --plan content/plans/sol.yml
    python scripts/from_script.py guion.md --scenes      # solo listar escenas

El troceado en escenas es determinista: respeta los separadores `---` del
Markdown como cortes duros y, dentro de cada sección, agrupa frases completas
hasta llegar a WORDS_PER_SCENE. Por eso los índices de escena son estables
entre ejecuciones y el fichero de plan visual puede referirse a ellos.

El plan visual es un YAML con esta forma:

    title: Qué Pasaría Si El Sol Se Apagara De Repente
    thumb_word: OSCURIDAD
    keyword: sun solar surface
    scenes:
      0:
        query: dying sun in space          # sujeto concreto, para archivos
        generic: [solar surface closeup, ...]  # para bancos de stock
        emphasis: "Pero imagina que ocurre."    # frase de remate, literal
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from pipeline import (assemble, captions, config, motion, music, sfx,  # noqa: E402
                      thumbnail, visuals, voice)
from pipeline.script_gen import Scene, VideoPlan  # noqa: E402
from pipeline.util import (assert_no_text_lost, log, require_binaries,  # noqa: E402
                           sentences, setup_logging, slugify, write_json)

def parse_markdown(path: Path) -> tuple[str, list[str]]:
    """Devuelve (título, bloques). Los `---` son cortes duros de sección."""
    raw = path.read_text(encoding="utf-8")

    title = ""
    m = re.search(r"^#\s+(.+)$", raw, re.M)
    if m:
        title = m.group(1).strip()
        raw = raw[: m.start()] + raw[m.end():]

    sections = [s.strip() for s in re.split(r"^\s*---\s*$", raw, flags=re.M)]
    return title, [s for s in sections if s]


def split_scenes(sections: list[str], words_per_scene: int) -> list[str]:
    """Trocea en escenas sin cruzar nunca un separador de sección."""
    scenes: list[str] = []
    for section in sections:
        text = re.sub(r"\s+", " ", section.replace("\n\n", " ")).strip()
        frases = sentences(text)
        assert_no_text_lost(text, frases)

        current: list[str] = []
        count = 0
        for sentence in frases:
            current.append(sentence)
            count += len(sentence.split())
            if count >= words_per_scene:
                scenes.append(" ".join(current))
                current, count = [], 0
        if current:
            # Una cola corta se pega a la escena anterior de ESTA sección.
            if scenes and count < words_per_scene * 0.45 and len(scenes) and current:
                scenes[-1] += " " + " ".join(current)
            else:
                scenes.append(" ".join(current))
    return scenes


FALLBACK_GENERIC = ["deep space stars", "cosmic nebula", "dark planet in space",
                    "galaxy in deep space"]


def build_plan(title: str, blocks: list[str], visual: dict) -> VideoPlan:
    per_scene = {int(k): v for k, v in (visual.get("scenes") or {}).items()}
    scenes = []
    for i, block in enumerate(blocks):
        meta = per_scene.get(i, {})
        emphasis = str(meta.get("emphasis") or "").strip()
        scenes.append(
            Scene(
                index=i,
                narration=block,
                visual_query=(meta.get("query") or visual.get("keyword") or "deep space"),
                visual_generic=list(meta.get("generic") or FALLBACK_GENERIC),
                visual_prompt=meta.get("prompt", ""),
                # Solo cuenta si aparece literal: si no, no se puede localizar
                # en la transcripción y el golpe caería a ojo.
                emphasis=[emphasis] if emphasis and emphasis in block else [],
            )
        )

    return VideoPlan(
        topic={"title_hint": title, "keyword": visual.get("keyword", "deep space")},
        title=visual.get("title") or title,
        thumb_word=(visual.get("thumb_word") or "ÉTER").upper(),
        thumb_prompt=visual.get("thumb_prompt", ""),
        description=visual.get("description", ""),
        tags=list(visual.get("tags") or []),
        narration=" ".join(blocks),
        scenes=scenes,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Producir un vídeo desde un guion en Markdown")
    ap.add_argument("script", type=Path)
    ap.add_argument("--plan", type=Path, help="YAML con el plan visual")
    ap.add_argument("--scenes", action="store_true", help="listar escenas y salir")
    ap.add_argument("--words", type=int, default=config.WORDS_PER_SCENE)
    ap.add_argument("--no-upload", action="store_true", default=True)
    args = ap.parse_args(argv)

    setup_logging()

    title, sections = parse_markdown(args.script)
    blocks = split_scenes(sections, args.words)
    total_words = sum(len(b.split()) for b in blocks)

    if args.scenes:
        print(f"# {title}")
        print(f"# {len(sections)} secciones -> {len(blocks)} escenas, "
              f"{total_words} palabras (~{total_words / config.WORDS_PER_MINUTE:.1f} min)\n")
        for i, b in enumerate(blocks):
            print(f"[{i:2d}] ({len(b.split()):3d} pal.) {b}\n")
        return 0

    require_binaries()
    visual = yaml.safe_load(args.plan.read_text(encoding="utf-8")) if args.plan else {}
    plan = build_plan(title, blocks, visual or {})

    workdir = config.BUILD_DIR / slugify(plan.title, 40)
    workdir.mkdir(parents=True, exist_ok=True)
    log.info("«%s»: %d escenas, %d palabras (~%.1f min)",
             plan.title, len(blocks), total_words,
             total_words / config.WORDS_PER_MINUTE)

    voice.narrate(plan.scenes, workdir)
    srt = voice.write_srt(plan.scenes, workdir / "subtitulos.srt")
    duration = (sum(s.duration for s in plan.scenes)
                + config.SCENE_GAP * (len(plan.scenes) - 1))
    bed = sfx.bed_for(plan.scenes, workdir, duration)
    audio = voice.mix(plan.scenes, workdir, music.track(plan.title), bed)

    visuals.build_clips(plan.scenes, workdir)
    rotulos = captions.build(plan.scenes, workdir)
    graficos = motion.build(plan.scenes, workdir)
    video = assemble.render(plan.scenes, audio, workdir / "video.mp4", rotulos, graficos)

    hero = None
    raw = workdir / "raw"
    if raw.exists():
        imgs = sorted((p for p in raw.iterdir() if p.suffix.lower() in (".jpg", ".png")),
                      key=lambda p: p.stat().st_size, reverse=True)
        hero = imgs[0] if imgs else None
    if hero is None:
        hero = thumbnail.hero_frame(video, workdir / "hero.jpg", at=min(60.0, duration / 3))
    thumbnail.build(plan.thumb_word, hero, workdir / "miniatura.jpg")

    write_json(workdir / "plan.json", plan.to_dict())
    log.info("Listo. Vídeo, miniatura y subtítulos en %s", workdir)
    log.info("Subtítulos: %s", srt.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
