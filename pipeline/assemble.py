"""Montaje final: encadena los planos con fundidos y mezcla el audio maestro.

Alineación. Cada plano se genera con longitud `d_i + GAP + CROSSFADE` y se
encadena con `xfade` en el desplazamiento `sum(d_j + GAP)` para j < i. Con eso
el corte de cada plano cae exactamente donde arranca la narración de su escena
y el fundido entra sobre la pausa entre escenas, no sobre una palabra.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import captions as captions_mod
from . import motion as motion_mod
from . import config
from .util import ffmpeg, log, probe_duration


def render(scenes, audio: Path, dest: Path, captions=None, graphics=None,
           subs=None) -> Path:
    captions = captions or []
    graphics = graphics or []
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

    # Los desplazamientos se calculan sobre la duración REAL de cada escena, no
    # sobre la teórica. Cada plano se cuantiza a fotograma entero, así que una
    # escena de 35,65 s acaba midiendo 35,60: si a xfade le pides un offset que
    # su entrada no alcanza, recorta la salida, y el error se va acumulando por
    # la cadena hasta dejar el montaje en un tercio de su duración.
    lengths = [probe_duration(c) for c in clips]

    offset = 0.0
    current = "v0"
    used: list[str] = []
    for i in range(1, len(clips)):
        # Encadenar aquí deja el acumulado justo en offset + CROSSFADE, que es
        # exactamente lo que xfade necesita para el solape siguiente.
        offset += lengths[i - 1] - config.CROSSFADE
        label = f"x{i}"
        kind = config.TRANSITIONS[(i - 1) % len(config.TRANSITIONS)]
        steps.append(
            f"[{current}][v{i}]xfade=transition={kind}:duration={config.CROSSFADE}"
            f":offset={offset:.3f}[{label}]"
        )
        used.append(kind)
        current = label

    # Un viñeteado muy leve y una entrada/salida a negro: es lo que separa
    # visualmente un montaje automático de uno que parece dirigido.
    total = offset + lengths[-1]
    grade = ""
    if config.GRADE_ENABLED:
        grade = (
            # Negros levantados y blancos contenidos: la curva de cine, que
            # quita el aspecto de vídeo crudo.
            "curves=all='0/0.025 0.25/0.22 0.5/0.5 0.75/0.78 1/0.97',"
            # Sombras frías y luces cálidas, el contraste de color que usa
            # todo el documental de divulgación.
            "colorbalance=rs=-0.05:gs=-0.01:bs=0.07:rm=0.02:bm=-0.02:"
            "rh=0.05:gh=0.01:bh=-0.04,"
            "eq=contrast=1.07:saturation=1.10:gamma=0.98,"
            # Aberración cromática de dos píxeles. A este tamaño no se ve
            # conscientemente y lee como óptica de verdad; a partir de seis
            # lee como plantilla.
            "rgbashift=rh=2:bh=-2,"
        )
        if config.GRAIN > 0:
            # Grano SOLO en luminancia (c0 es el plano Y). El de antes metía
            # ruido también en crominancia y, sobre los negros del espacio,
            # eso son manchas de color que además castigan al códec.
            grade += f"noise=c0s={config.GRAIN + 2:.0f}:c0f=t+u,"

    # Halación: las altas luces sangran en cálido. Es la diferencia entre
    # «vídeo» y «cine» en material espacial, donde todo son puntos brillantes
    # sobre negro. Se aísla lo que pasa de 200, se desenfoca mucho, se tiñe y
    # se suma en modo pantalla.
    if config.HALATION > 0:
        steps.append(
            f"[{current}]{grade}split[hal_b][hal_h];"
            f"[hal_h]lutyuv=y='if(gt(val,200),val,16)',gblur=sigma=26,"
            f"colorchannelmixer=rr=1.0:gg=0.58:bb=0.36[hal_g];"
            f"[hal_b][hal_g]blend=all_mode=screen:all_opacity={config.HALATION:.2f}[graded]"
        )
        grade = ""
        current = "graded"

    # Viñeta que respira: la fija se lee como filtro, la que oscila muy
    # despacio se lee como cámara.
    steps.append(
        f"[{current}]{grade}vignette=angle='PI/5+0.02*sin(2*PI*t/9)',"
        + (f"drawbox=x=0:y=0:w=iw:h={config.LETTERBOX}:c=black@1:t=fill,"
           f"drawbox=x=0:y=ih-{config.LETTERBOX}:w=iw:h={config.LETTERBOX}:"
           f"c=black@1:t=fill," if config.LETTERBOX else "")
        + f"fade=t=in:st=0:d=1.2,fade=t=out:st={max(total - 2.0, 0):.2f}:d=2.0[base]"
    )
    current = "base"

    # El texto va DESPUÉS del etalonaje y de las bandas: si entrara antes, el
    # grano y la halación se le comerían el filo y las bandas lo recortarían.
    if subs:
        from . import subs as subs_mod
        steps.append(f"[base]{subs_mod.filtro(subs)}[contexto]")
        current = "contexto"

    # Los rótulos van DESPUÉS del viñeteado y del fundido de entrada: si
    # entraran antes, el fundido a negro del arranque se los llevaría por
    # delante y el halo quedaría apagado.
    extra_inputs: list[str] = []
    if captions:
        extra_inputs, caption_steps, current = captions_mod.overlay_filters(
            captions, current, len(clips) + 1
        )
        steps.extend(caption_steps)

    # Los gráficos de dato van encima de los rótulos y comparten su numeración
    # de entradas, así que arrancan donde acaban aquéllos.
    if graphics:
        mg_inputs, mg_steps, current = motion_mod.overlay_filters(
            graphics, current, len(clips) + 1 + len(captions)
        )
        extra_inputs += mg_inputs
        steps.extend(mg_steps)

    steps.append(f"[{current}]null[vout]")

    reparto = ", ".join(f"{k}x{v}" for k, v in Counter(used).most_common())
    log.info("Montando %d escenas (%.1f min); transiciones: %s",
             len(clips), total / 60, reparto or "ninguna")
    ffmpeg(
        inputs
        + ["-i", str(audio)]
        + extra_inputs
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
