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


# Los archivos científicos mezclan imagen buena con láminas y esquemas, así que
# se les aplica el control estricto. Los bancos de stock y tu biblioteca son
# material curado: ahí solo interesa detectar rótulos, no exigir que el plano
# sea oscuro.
_STRICT_PREFIXES = ("svs_", "nasa-video_", "nasa-img_")


def _needs_strict_qc(path: Path) -> bool:
    return path.name.startswith(_STRICT_PREFIXES)


@dataclass
class Shot:
    scene_index: int
    index: int
    duration: float
    # Búsqueda concreta de ESTE plano, no de su escena. Una escena ocupa unos
    # ocho planos y rota entre las búsquedas que le dio el guionista.
    query: str = ""
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

    # Cuántas fuentes distintas se intentan reunir por búsqueda antes de empezar
    # a repartir. Con una sola, los planos consecutivos salen de ventanas
    # contiguas del mismo clip y se parecen tanto que el corte no se nota.
    SOURCES_PER_QUERY = 3

    # Con 0.22 un solo clip podía cubrir 42 planos de 194. Medido en el vídeo
    # de la estrella de neutrones: el más repetido salía 23 veces y ni
    # siquiera rozaba el tope. Con 0.06 son 11 de 194.
    def set_screen(self, screen) -> None:
        """screen(list[Path]) -> set[int]: índices que no pegan con el tema.

        Las fuentes se acumulan y, cada TANDA, se miran todas de una vez antes
        de que ninguna reparta planos. Mirar el vídeo terminado llegaba tarde:
        rechazaba dos horas de trabajo sin arreglar nada.
        """
        self._screen = screen

    def _criba_pendientes(self, forzar: bool = False) -> None:
        from . import quality

        if not self._screen or not self._pendientes:
            return
        if not forzar and len(self._pendientes) < quality.TANDA:
            return
        lote = self._pendientes[:quality.TANDA] if not forzar else self._pendientes
        fuera = self._screen([s.path for s in lote])
        for i, fuente in enumerate(lote):
            if i in fuera:
                self._all.remove(fuente) if fuente in self._all else None
                for lista in self._by_query.values():
                    if fuente in lista:
                        lista.remove(fuente)
        self._pendientes = self._pendientes[len(lote):]

    def __init__(self, fetch, max_share: float = 0.06) -> None:
        # fetch(query) -> (Path, is_image) | None
        self._fetch = fetch
        self._by_query: dict[str, list[Source]] = {}
        self._all: list[Source] = []
        self._exhausted: set[str] = set()
        self._max_share = max_share
        self._budget: dict[int, int] = {}
        self._total_shots = 0
        self._last: Source | None = None
        self._screen = None
        self._pendientes: list[Source] = []

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
            clean = inspect_media.clean_windows(
                path, duration, config.SHOT_MIN, strict=_needs_strict_qc(path)
            )
            if not clean:
                log.debug("  %s descartado: ningún tramo limpio", path.name[:40])
                return None

        source = Source(
            path=path, duration=duration, is_image=is_image,
            clean=clean, free=[list(w) for w in clean],
        )
        self._by_query.setdefault(query, []).append(source)
        self._all.append(source)
        # Entra en la cola de cribado. Se mira cuando haya tanda completa, no
        # de una en una: una llamada por clip serían casi doscientas por vídeo.
        if self._screen is not None:
            self._pendientes.append(source)
            self._criba_pendientes()
        return source

    def _serve(self, source: Source, want: float) -> tuple[Path, float, bool] | None:
        if not self._charge(source):
            return None
        start = source.take(want)
        if start is None:
            self._budget[id(source)] -= 1
            return None
        self._last = source
        return source.path, start, source.is_image

    def segment(self, query: str, want: float) -> tuple[Path, float, bool] | None:
        """Un trozo sin usar de `want` segundos para esta búsqueda."""
        # 1. Reunir variedad antes de repartir: mientras esta búsqueda no tenga
        #    unas cuantas fuentes propias, se trae otra.
        mine = self._by_query.setdefault(query, [])
        if len(mine) < self.SOURCES_PER_QUERY and query not in self._exhausted:
            got = self._fetch(query)
            if got:
                self._add(query, *got)
            else:
                self._exhausted.add(query)
            mine = self._by_query.get(query, [])

        # 2. Repartir entre las fuentes de esta búsqueda, evitando repetir la
        #    del plano anterior: dos ventanas contiguas del mismo clip se
        #    parecen tanto que el corte pasa desapercibido.
        for candidates in (
            [s for s in mine if s is not self._last],
            mine,
        ):
            for source in sorted(candidates, key=lambda s: self._budget.get(id(s), 0)):
                served = self._serve(source, want)
                if served:
                    return served

        # 3. Se han agotado los huecos de esta búsqueda: pedir MÁS material
        #    suyo, sin tope. Aquí es donde entra la generación por IA, porque
        #    `fetch` la lleva al final de su cascada. Antes esto solo se
        #    intentaba mientras la búsqueda tuviera menos de SOURCES_PER_QUERY
        #    fuentes, así que la IA no llegaba a ejecutarse nunca y el reparto
        #    se iba directo al banco general: 87 veces en un vídeo, y cero
        #    planos generados.
        if query not in self._exhausted:
            got = self._fetch(query)
            if got:
                nueva = self._add(query, *got)
                if nueva is not None:
                    served = self._serve(nueva, want)
                    if served:
                        return served
            else:
                self._exhausted.add(query)

        # 4. Otra vuelta sobre las fuentes DE ESTA BÚSQUEDA antes que tocar las
        #    de otra escena. Repetir un encuadre del tema correcto es mucho menos
        #    grave que enseñar algo que no tiene que ver con lo que se dice: si
        #    la narración habla del Sol, en pantalla tiene que haber Sol aunque
        #    el plano se parezca a otro anterior.
        for source in sorted(mine, key=lambda s: s.laps):
            start = source.rewind(want)
            if start is not None:
                self._budget[id(source)] = self._budget.get(id(source), 0) + 1
                self._last = source
                return source.path, start, source.is_image

        # 5. Solo ahora, material de otras búsquedas.
        log.debug("  «%s» sin material propio, se recurre al banco general", query[:38])
        pool = [s for s in self._all if not s.is_image and s not in mine]
        for candidates in ([s for s in pool if s is not self._last], pool):
            for source in sorted(candidates, key=lambda s: self._budget.get(id(s), 0)):
                served = self._serve(source, want)
                if served:
                    return served

        # 6. Imágenes de relleno, si es que hay.
        for source in sorted(
            (s for s in self._all if s.is_image), key=lambda s: self._budget.get(id(s), 0)
        ):
            served = self._serve(source, want)
            if served:
                return served

        # 7. Última vuelta sobre lo que haya, ya sin tope.
        for source in sorted(self._all, key=lambda s: (s.is_image, s.laps)):
            start = source.rewind(want)
            if start is not None:
                self._budget[id(source)] = self._budget.get(id(source), 0) + 1
                self._last = source
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
# Ninguno es «flat». Un empuje plano deja el plano exactamente igual de quieto
# que la fuente, y sobre material lento —las simulaciones de ESO son zooms
# suavísimos— el resultado se lee como una foto: medido, un 31 % del montaje
# quedaba en tramos sin vida pese a ser todo clips.
#
# El recorrido sube a un 12-14 % del encuadre. Por encima se nota el barrido y
# marea; por debajo no basta para despegar un plano lento.
_PUSHES = (
    ("in", 1.00, 1.13),
    ("out", 1.14, 1.00),
    ("in", 1.03, 1.14),
    ("out", 1.12, 1.01),
    ("in", 1.01, 1.12),
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
    """Campo de estrellas en deriva. Que no se caiga el render, nada más.

    El `-t` es obligatorio aquí: `zoompan` emite `d` fotogramas por cada
    fotograma de entrada, así que sobre una fuente sintética de 165 fotogramas
    con d=165 salían 27.225, o sea 907 segundos. Colado como primer plano del
    vídeo, el `-shortest` del montaje final hacía que la pieza entera fuese ese
    campo de estrellas.
    """
    seed = shot.scene_index * 100 + shot.index
    drift = 0.0012 + 0.0004 * (seed % 3)
    ffmpeg([
        "-f", "lavfi",
        "-i", f"nullsrc=s={config.WIDTH}x{config.HEIGHT}:r={config.FPS}:d={shot.duration:.3f}",
        "-t", f"{shot.duration:.3f}",
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


MAX_DURATION_DRIFT = 0.35

# Por debajo de esto el plano YA RENDERIZADO se lee como una foto. Comprobar la
# fuente no basta: un clip puede moverse de media y aun así el trozo de cinco
# segundos que se corta estar parado. Esta es la única medida que mira lo que
# de verdad va a ver el espectador.
# En la escala de movimiento percibido (ver inspect_media), no en la
# antigua de primer-contra-último fotograma.
MIN_SHOT_MOTION = 2.0


def shot_motion(path: Path) -> float:
    """Movimiento percibido del plano ya renderizado.

    Antes comparaba el primer fotograma con el último, que mide el
    desplazamiento total del plano y no lo que ve el ojo: un plano que deriva
    lentísimo de un extremo a otro puntuaba alto y en pantalla parecía una
    foto. Ahora es la mediana de las diferencias entre fotogramas separados
    medio segundo, la misma escala que usa inspect_media.
    """
    from . import inspect_media

    duracion = probe_duration(path)
    if duracion < 1:
        return 0.0
    central, _ = inspect_media.perceived_motion(path, duracion)
    return central


def render_shot(shot: Shot, dest: Path, autocrop: str = "") -> Path:
    if shot.source is None:
        _filler_shot(shot, dest)
    elif shot.is_image:
        _image_shot(shot, dest)
    else:
        _video_shot(shot, dest, autocrop)

    # Guardia barata que cuesta un ffprobe y ahorra un desastre silencioso. Un
    # plano que sale con la duración equivocada no se nota al mirarlo: se nota
    # tres pasos después, cuando el montaje entero mide lo que no debe.
    actual = probe_duration(dest)
    if abs(actual - shot.duration) > MAX_DURATION_DRIFT:
        log.warning(
            "El plano %s salió de %.1f s en vez de %.1f s; se recorta",
            dest.name, actual, shot.duration,
        )
        trimmed = dest.with_suffix(".trim.mp4")
        ffmpeg(["-i", str(dest), "-t", f"{shot.duration:.3f}", "-c", "copy", str(trimmed)])
        trimmed.replace(dest)

    shot.path = dest
    return dest


# --------------------------------------------------------------------------
# Montaje de la escena
# --------------------------------------------------------------------------


def concat_scene(shot_paths: list[Path], dest: Path, listing: Path) -> Path:
    """Une los planos de una escena con corte seco.

    Usa el FILTRO `concat`, no el demuxer, y regenera las marcas de tiempo a
    partir del índice de fotograma. Es más caro —recodifica— pero es la única
    forma que da un resultado correcto.

    El demuxer `concat` con `-c copy` es instantáneo y produce un fichero cuya
    duración declarada es la correcta, así que parece que funciona. Pero deja
    marcas de tiempo irregulares en las costuras, y eso rompe `xfade` más
    adelante sin dar ni un aviso: encadenar dos escenas con un desplazamiento de
    5 s devolvía 48,1 s en vez de 43,2. Recodificar el mismo material con el
    demuxer tampoco vale, porque descarta fotogramas en las costuras —una escena
    de 35,0 s salía de 29,7—. El filtro trabaja sobre fotogramas ya decodificados
    y da la duración exacta.
    """
    listing.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in shot_paths), encoding="utf-8"
    )

    inputs: list[str] = []
    prep: list[str] = []
    for i, path in enumerate(shot_paths):
        inputs += ["-i", str(path)]
        prep.append(f"[{i}:v]fps={config.FPS},format=yuv420p,setsar=1[c{i}]")

    chain = "".join(f"[c{i}]" for i in range(len(shot_paths)))
    graph = (
        ";".join(prep)
        + f";{chain}concat=n={len(shot_paths)}:v=1:a=0,setpts=N/FRAME_RATE/TB[out]"
    )

    ffmpeg(
        inputs
        + [
            "-filter_complex", graph,
            "-map", "[out]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "30000",
            str(dest),
        ]
    )

    # Los planos sueltos ya están dentro de la escena.
    if config.PRUNE:
        for path in shot_paths:
            path.unlink(missing_ok=True)

    return dest
