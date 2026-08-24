"""Montaje final: encadena los planos con fundidos y mezcla el audio maestro.

Alineación. Cada plano se genera con longitud `d_i + GAP + CROSSFADE` y se
encadena con `xfade` en el desplazamiento `sum(d_j + GAP)` para j < i. Con eso
el corte de cada plano cae exactamente donde arranca la narración de su escena
y el fundido entra sobre la pausa entre escenas, no sobre una palabra.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .util import ffmpeg, log, probe_duration


def render(scenes, audio: Path, dest: Path) -> Path:
    clips = [Path(s.clip_path) for s in scenes]
    if not clips:
        raise RuntimeError("No hay planos que montar")

    if len(clips) == 1:
        _mux(clips[0], audio, dest)
        return dest

    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", str(clip)]

    # Normalizar de nuevo por seguridad: xfade exige tamaño, fps y formato
    # idénticos en las dos entradas de cada transición.
    steps = []
    for i in range(len(clips)):
        steps.append(
            f"[{i}:v]scale={config.WIDTH}:{config.HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={config.WIDTH}:{config.HEIGHT},setsar=1,fps={config.FPS},format=yuv420p[v{i}]"
        )

    offset = 0.0
    current = "v0"
    for i in range(1, len(clips)):
        offset += scenes[i - 1].duration + config.SCENE_GAP
        label = f"x{i}"
        steps.append(
            f"[{current}][v{i}]xfade=transition=fade:duration={config.CROSSFADE}"
            f":offset={offset:.3f}[{label}]"
        )
        current = label

    # Un viñeteado muy leve y una entrada/salida a negro: es lo que separa
    # visualmente un montaje automático de uno que parece dirigido.
    total = offset + scenes[-1].duration + config.SCENE_GAP + config.CROSSFADE
    steps.append(
        f"[{current}]vignette=angle=PI/6,"
        f"fade=t=in:st=0:d=1.2,fade=t=out:st={max(total - 2.0, 0):.2f}:d=2.0[vout]"
    )

    log.info("Montando %d planos (%.1f min)", len(clips), total / 60)
    ffmpeg(
        inputs
        + ["-i", str(audio)]
        + [
            "-filter_complex", ";".join(steps),
            "-map", "[vout]",
            "-map", f"{len(clips)}:a",
            "-c:v", "libx264",
            "-preset", config.VIDEO_PRESET,
            "-crf", str(config.VIDEO_CRF),
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level", "4.1",
            "-g", str(config.FPS * 2),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",
            "-shortest",
            str(dest),
        ]
    )
    log.info("Vídeo listo: %s (%.1f MB, %.1f min)",
             dest.name, dest.stat().st_size / 1e6, probe_duration(dest) / 60)
    return dest


def _mux(clip: Path, audio: Path, dest: Path) -> None:
    ffmpeg([
        "-i", str(clip), "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", config.VIDEO_PRESET, "-crf", str(config.VIDEO_CRF),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-shortest", str(dest),
    ])
