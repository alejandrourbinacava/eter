"""Motor de planos: convierte cada escena en una secuencia de cortes cortos.

El canal se monta con clips, no con imágenes fijas, y ningún plano pasa de seis
segundos. Un vídeo de catorce minutos son unos 180 planos, así que la pregunta
no es «qué clip pongo en esta escena» sino «de dónde saco 180 trozos distintos
sin que se note».

La respuesta son los archivos de animación científica. Una visualización del
SVS de la NASA dura entre 30 y 90 segundos: troceada da diez o quince planos de
seis segundos que no se parecen entre sí. Por eso `ClipBank` descarga cada
fuente una sola vez y va sirviendo ventanas consecutivas y sin solape.

Ritmo. Las duraciones recorren `config.SHOT_RHYTHM` en bucle, alternando planos
largos y cortos. Dentro de una escena los cortes son secos; el fundido se
reserva para el salto entre escenas, que es donde la narración respira.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from . import config, inspect_media
from .util import ffmpeg, log, probe_duration


@dataclass
class Shot:
    scene_index: int
    index: int
    duration: float
    source: Path | None = None
    source_start: float = 0.0
    is_image: bool = False
    path: Path | None = None


@dataclass
class Source:
    """Un material descargado y los tramos suyos que se pueden usar.

    `clean` son los intervalos que han pasado el control de calidad de
    `inspect_media`; `free` es lo que queda por consumir de ellos. Servir solo
    de ahí es lo que impide que se cuele el rótulo de un vídeo divulgativo.
    """

    path: Path
    duration: float
    is_image: bool
    clean: list[list[float]] = field(default_factory=list)
    free: list[list[float]] = field(default_factory=list)
    laps: int = 0

    def take(self, want: float, margin: float = 0.4) -> float | None:
        """Inicio del siguiente trozo libre y limpio, o None si se acabó."""
        if self.is_image:
            self.laps += 1
            return 0.0
        for window in self.free:
            if window[1] - window[0] >= want + margin:
                start = window[0]
                window[0] = start + want + margin
                return start
        return None

    def rewind(self, want: float) -> float | None:
        """Otra vuelta sobre los tramos limpios, desfasada para no repetir
        exactamente los mismos encuadres."""
        if self.is_image:
            self.laps += 1
            return 0.0
        usable = [w for w in self.clean if w[1] - w[0] >= want]
        if not usable:
            return None
        self.laps += 1
        window = usable[self.laps % len(usable)]
        span = window[1] - window[0] - want
        offset = window[0] + ((self.laps * (want / 2 + 1.1)) % span if span > 0.1 else 0.0)
        return offset


class ClipBank:
    """Fuentes descargadas, indexadas por la búsqueda que las trajo.

    El reparto lleva un tope de reutilización. Sin él, en cuanto se agotan las
    fuentes propias de una búsqueda, el primer clip disponible acaba llenando
    medio vídeo: sale un montaje monótono y, peor, fuera de tema. Con el tope,
    cuando ninguna fuente puede aportar más planos el llamante se entera y
    puede avisar en vez de disimular.
    """

    def __init__(self, fetch, max_share: float = 0.22) -> None:
        # fetch(query) -> (Path, is_image) | None
        self._fetch = fetch
        self._by_query: dict[str, list[Source]] = {}
        self._all: list[Source] = []
        self._exhausted: set[str] = set()
        self._max_share = max_share
        self._budget: dict[int, int] = {}
        self._total_shots = 0

    def set_total_shots(self, total: int) -> None:
        """Fija cuántos planos hay que servir, para calcular el tope."""
        self._total_shots = total

    def _cap(self) -> int:
        if not self._total_shots:
            return 10**6
        return max(3, int(self._total_shots * self._max_share))

    def _charge(self, source: Source) -> bool:
        used = self._budget.get(id(source), 0)
        if used >= self._cap():
            return False
        self._budget[id(source)] = used + 1
        return True

    def _add(self, query: str, path: Path, is_image: bool) -> Source | None:
        try:
            duration = 0.0 if is_image else probe_duration(path)
        except RuntimeError:
            return None
        if not is_image and duration < config.SHOT_MIN:
            return None

        clean: list[list[float]] = []
        if not is_image:
            clean = inspect_media.clean_windows(path, duration, config.SHOT_MIN)
            if not clean:
                log.debug("  %s descartado: ningún tramo limpio", path.name[:40])
                return None

        source = Source(
            path=path, duration=duration, is_image=is_image,
            clean=clean, free=[list(w) for w in clean],
        )
        self._by_query.setdefault(query, []).append(source)
        self._all.append(source)
        return source

    def segment(self, query: str, want: float) -> tuple[Path, float, bool] | None:
        """Un trozo sin usar de `want` segundos para esta búsqueda."""
        # 1. Ventana libre en una fuente que ya tenemos para esta búsqueda.
        for source in self._by_query.get(query, []):
            if not self._charge(source):
                continue
            start = source.take(want)
            if start is not None:
                return source.path, start, source.is_image
            self._budget[id(source)] -= 1

        # 2. Traer una fuente nueva para esta búsqueda.
        if query not in self._exhausted:
            got = self._fetch(query)
            if got:
                path, is_image = got
                source = self._add(query, path, is_image)
                if source is not None and self._charge(source):
                    start = source.take(want)
                    if start is not None:
                        return source.path, start, source.is_image
                    self._budget[id(source)] -= 1
            else:
                self._exhausted.add(query)

        # 3. Cualquier fuente de vídeo del banco con hueco libre.
        for source in sorted(self._all, key=lambda s: (s.is_image, self._budget.get(id(s), 0))):
            if source.is_image or not self._charge(source):
                continue
            start = source.take(want)
            if start is not None:
                return source.path, start, False
            self._budget[id(source)] -= 1

        # 4. Segunda vuelta desfasada sobre lo que haya, ya sin tope: llegados
        #    aquí, repetir encuadres es mejor que dejar el plano en negro.
        for source in sorted(self._all, key=lambda s: (s.is_image, s.laps)):
            start = source.rewind(want)
            if start is not None:
                self._budget[id(source)] = self._budget.get(id(source), 0) + 1
                return source.path, start, source.is_image

        return None

    def diversity_report(self) -> tuple[int, int]:
        """(fuentes usadas, planos servidos por la fuente más repetida)."""
        used = {k: v for k, v in self._budget.items() if v}
        return len(used), (max(used.values()) if used else 0)

    @property
    def stats(self) -> tuple[int, int]:
        videos = sum(1 for s in self._all if not s.is_image)
        return videos, len(self._all) - videos


# --------------------------------------------------------------------------
# Reparto de duraciones
# --------------------------------------------------------------------------


def split_duration(total: float, seed: int) -> list[float]:
    """Trocea el hueco de una escena en planos de SHOT_MIN a SHOT_MAX."""
    if total <= config.SHOT_MAX:
        return [round(total, 3)]

    rhythm = config.SHOT_RHYTHM
    out: list[float] = []
    remaining = total
    i = seed
    while remaining > 0:
        want = rhythm[i % len(rhythm)]
        i += 1
        # Si al cortar aquí el resto quedaría por debajo del mínimo, se cierra.
        if remaining - want < config.SHOT_MIN:
            if remaining <= config.SHOT_MAX:
                out.append(remaining)
            else:
                half = remaining / 2  # queda en [SHOT_MIN, SHOT_MAX] por construcción
                out.extend([half, half])
            break
        out.append(want)
        remaining -= want
    return [round(d, 3) for d in out]


def plan(scenes) -> list[Shot]:
    """Reparte todas las escenas en planos.

    El hueco de cada escena incluye su pausa posterior y el solape del fundido
    que assemble.render() consume al encadenar con la escena siguiente. Sin ese
    margen extra, xfade se queda sin metraje y el montaje sale corto.
    """
    shots: list[Shot] = []
    for scene in scenes:
        slot = scene.duration + config.SCENE_GAP + config.CROSSFADE
        for i, duration in enumerate(split_duration(slot, scene.index)):
            shots.append(Shot(scene_index=scene.index, index=i, duration=duration))
    return shots


# --------------------------------------------------------------------------
# Render de un plano
# --------------------------------------------------------------------------

# Empujes de cámara que se alternan para que dos planos seguidos no se muevan
# igual. Un movimiento lento sobre metraje ya animado da profundidad; uno
# rápido marea.
_PUSHES = (
    ("in", 1.00, 1.07),
    ("out", 1.08, 1.00),
    ("flat", 1.03, 1.03),
    ("in", 1.02, 1.10),
    ("out", 1.06, 1.01),
)


def _video_shot(shot: Shot, dest: Path, autocrop: str = "") -> None:
    name, z0, z1 = _PUSHES[(shot.scene_index + shot.index) % len(_PUSHES)]
    frames = max(int(shot.duration * config.FPS), 1)
    w2, h2 = config.WIDTH * 2, config.HEIGHT * 2

    if name == "flat":
        motion = f"scale={int(config.WIDTH * z0)}:{int(config.HEIGHT * z0)}"
    else:
        # Rampa lineal de escala a lo largo del plano.
        motion = (
            f"scale={w2}:{h2},"
            f"zoompan=z='{z0}+({z1}-{z0})*on/{frames}':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS}"
        )

    ffmpeg([
        "-ss", f"{shot.source_start:.3f}",
        "-i", str(shot.source),
        "-an", "-t", f"{shot.duration:.3f}",
        "-vf",
        (
            autocrop
            + f"scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
            + motion
            + f",scale={config.WIDTH}:{config.HEIGHT},setsar=1,"
            f"fps={config.FPS},format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-g", str(config.FPS * 2), "-keyint_min", str(config.FPS),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-video_track_timescale", "30000",
        str(dest),
    ])


def _image_shot(shot: Shot, dest: Path) -> None:
    """Solo como relleno de emergencia: la base del vídeo son clips."""
    frames = max(int(shot.duration * config.FPS), 1)
    mode = (shot.scene_index + shot.index) % 4
    rate = 0.0016  # más vivo que antes: el plano dura seis segundos, no treinta
    if mode == 0:
        z, x, y = f"min(zoom+{rate},1.34)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mode == 1:
        z, x, y = (f"if(lte(zoom,1.0),1.34,max(zoom-{rate},1.0))",
                   "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)")
    elif mode == 2:
        z, x, y = f"min(zoom+{rate * 0.5},1.18)", f"(iw-iw/zoom)*(on/{frames})", "ih/2-(ih/zoom/2)"
    else:
        z, x, y = f"min(zoom+{rate * 0.5},1.18)", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(on/{frames})"

    ffmpeg([
        "-loop", "1", "-i", str(shot.source), "-t", f"{shot.duration:.3f}",
        "-vf",
        (
            f"scale={config.WIDTH * 3}:-2:flags=lanczos,"
            f"crop={config.WIDTH * 3}:{config.HEIGHT * 3}:(in_w-out_w)/2:(in_h-out_h)/2,"
            f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}'"
            f":s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS},"
            f"setsar=1,format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-g", str(config.FPS * 2), "-keyint_min", str(config.FPS),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-video_track_timescale", "30000",
        str(dest),
    ])


def _filler_shot(shot: Shot, dest: Path) -> None:
    """Campo de estrellas en deriva. Que no se caiga el render, nada más."""
    seed = shot.scene_index * 100 + shot.index
    drift = 0.0012 + 0.0004 * (seed % 3)
    ffmpeg([
        "-f", "lavfi",
        "-i", f"nullsrc=s={config.WIDTH}x{config.HEIGHT}:r={config.FPS}:d={shot.duration:.3f}",
        "-vf",
        (
            f"geq=random({seed % 97 + 1})*255:128:128,"
            f"lutyuv=y='if(gt(val,252),val,0)',boxblur=1:1,"
            f"zoompan=z='min(zoom+{drift},1.5)':d={int(shot.duration * config.FPS)}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={config.WIDTH}x{config.HEIGHT}:fps={config.FPS},"
            f"colorbalance=bs=0.12,setsar=1,format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-g", str(config.FPS * 2), "-keyint_min", str(config.FPS),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-video_track_timescale", "30000",
        str(dest),
    ])


def render_shot(shot: Shot, dest: Path, autocrop: str = "") -> Path:
    if shot.source is None:
        _filler_shot(shot, dest)
    elif shot.is_image:
        _image_shot(shot, dest)
    else:
        _video_shot(shot, dest, autocrop)
    shot.path = dest
    return dest


# --------------------------------------------------------------------------
# Montaje de la escena
# --------------------------------------------------------------------------


def concat_scene(shot_paths: list[Path], dest: Path, listing: Path) -> Path:
    """Une los planos de una escena con corte seco.

    Se intenta primero copiando los flujos, que es casi instantáneo. Todos los
    planos se codifican con los mismos parámetros justo para que esto funcione;
    si aun así el demuxer se queja, se recodifica.
    """
    listing.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in shot_paths), encoding="utf-8"
    )
    try:
        ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)])
    except RuntimeError:
        log.debug("  concat sin recodificar falló, se recodifica la escena")
        ffmpeg([
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(dest),
        ])
    return dest
