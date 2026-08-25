"""Muestra rápida: produce solo unas escenas y mide lo que suele salir mal.

Existe porque montar el vídeo entero para ver si un filtro funciona cuesta
ochenta minutos, y todos los defectos que han aparecido hasta ahora —planos
congelados, material fuera de tema, rótulos desincronizados, efectos
inaudibles— se ven igual de bien en dos minutos.

    python scripts/preview.py guion.md --plan plan.yml            # 3 escenas
    python scripts/preview.py guion.md --plan plan.yml --scenes 6

Reutiliza la locución que ya esté en caché, así que no gasta créditos si el
vídeo completo se produjo antes.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from pipeline import assemble, captions, config, music, sfx, visuals, voice  # noqa: E402
from pipeline.util import log, probe_duration, require_binaries, setup_logging  # noqa: E402
from scripts.from_script import build_plan, parse_markdown, split_scenes  # noqa: E402


def motion(path: Path, tmp: Path) -> float:
    """Diferencia entre el primer y el último fotograma. 0 = congelado."""
    from PIL import Image, ImageChops

    d = probe_duration(path)
    if d < 1:
        return 0.0
    marcos = []
    for i, t in enumerate((0.4, max(d - 0.4, 0.6))):
        f = tmp / f"m{i}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", str(path), "-frames:v", "1", "-vf", "scale=160:90", str(f)],
                       capture_output=True)
        if not f.exists():
            return 0.0
        marcos.append(Image.open(f).convert("L"))
    hist = ImageChops.difference(*marcos).histogram()
    total = sum(hist)
    return sum(i * n for i, n in enumerate(hist)) / total if total else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Muestra rápida con diagnóstico")
    ap.add_argument("script", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--scenes", type=int, default=3, help="cuántas escenas producir")
    ap.add_argument("--from-scene", type=int, default=0)
    args = ap.parse_args(argv)

    setup_logging()
    require_binaries()

    title, sections = parse_markdown(args.script)
    bloques = split_scenes(sections, config.WORDS_PER_SCENE)
    visual = yaml.safe_load(args.plan.read_text(encoding="utf-8")) if args.plan else {}
    plan = build_plan(title, bloques, visual or {})

    inicio = args.from_scene
    plan.scenes = plan.scenes[inicio:inicio + args.scenes]
    for nuevo, escena in enumerate(plan.scenes):
        escena.index = nuevo

    workdir = config.BUILD_DIR / "preview"
    workdir.mkdir(parents=True, exist_ok=True)

    # La locución del vídeo completo, si existe, para no repagar el TTS.
    completo = config.BUILD_DIR / "que-pasaria-si-el-sol-se-apagara-de-repe" / "audio"
    if completo.exists():
        (workdir / "audio").mkdir(exist_ok=True)
        for i in range(args.scenes):
            for suf in (".mp3", ".words.json"):
                origen = completo / f"scene_{inicio + i:03d}{suf}"
                if origen.exists():
                    destino = workdir / "audio" / f"scene_{i:03d}{suf}"
                    if not destino.exists():
                        destino.write_bytes(origen.read_bytes())

    log.info("Muestra: escenas %d-%d de %d", inicio, inicio + args.scenes - 1, len(bloques))

    voice.narrate(plan.scenes, workdir)
    total = (sum(s.duration for s in plan.scenes)
             + config.SCENE_GAP * (len(plan.scenes) - 1))
    bed = sfx.bed_for(plan.scenes, workdir, total)
    audio = voice.mix(plan.scenes, workdir, music.track(plan.title), bed)

    visuals.build_clips(plan.scenes, workdir)
    rotulos = captions.build(plan.scenes, workdir)
    video = assemble.render(plan.scenes, audio, workdir / "muestra.mp4", rotulos)

    # ---- diagnóstico -----------------------------------------------------
    planos = sorted((workdir / "shots").glob("*.mp4"))
    tmp = workdir / "tmp"
    tmp.mkdir(exist_ok=True)
    valores = [motion(p, tmp) for p in planos]
    quietos = [v for v in valores if v < 3]

    print()
    print("=" * 58)
    print(f"  muestra          {probe_duration(video) / 60:.1f} min, {len(planos)} planos")
    if valores:
        print(f"  movimiento       mediana {statistics.median(valores):5.1f}   "
              f"quietos {len(quietos)} de {len(planos)} ({len(quietos) / len(planos) * 100:.0f} %)")
    print(f"  rótulos          {len(rotulos)}")
    print(f"  efectos          {len(sfx.plan_cues(plan.scenes))} disparos")
    print(f"  fichero          {video}")
    print("=" * 58)
    if quietos and len(quietos) / len(planos) > 0.1:
        print("  AVISO: más de un plano de cada diez está casi congelado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
