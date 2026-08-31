"""Portero de calidad: mira el vídeo terminado y decide si vale.

Existe porque las heurísticas de píxeles no bastan. `inspect_media` sabe medir
si un fotograma tiene texto o si se mueve, pero no sabe que unos patines rosas
no pintan nada en un documental sobre agujeros negros. Esa clase de fallo se ha
escapado cinco veces seguidas y siempre lo ha descubierto el espectador después
de dos horas de render.

Aquí se monta una hoja de contactos con fotogramas repartidos por todo el
montaje y se le pregunta al mismo modelo que escribió el guion cuáles no pegan
con el tema. Si pasan del tope, la producción falla y el vídeo no se publica.

Cuesta una llamada de visión por vídeo, unos céntimos, y es la diferencia entre
enterarse antes o después de subirlo.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path

from . import config
from .util import log

# Cuántos fotogramas se le enseñan y cómo se colocan.
MUESTRAS = 24
REJILLA = "6x4"
ANCHO = 320

# Por encima de este porcentaje de fotogramas fuera de tema, el vídeo no sale.
# Con 24 muestras, 12 % son tres. Se empezó en 20 y el primer montaje que pasó
# por aquí sacó un 17 %: el portero acertó —señaló unos granos de café, unos
# cubos azules y una pared agrietada— pero el listón le dejaba pasar. Tres
# fotogramas malos de veinticuatro ya se notan viendo el vídeo.
TOPE = 12.0

SYSTEM = """Eres el control de calidad de Éter, un canal de documentales \
espaciales. Miras hojas de contactos de un montaje ya terminado y señalas los \
fotogramas que no pegan con el tema del vídeo. Respondes solo con JSON."""


def _hoja(video: Path, dest: Path) -> Path | None:
    """Hoja de contactos con MUESTRAS fotogramas repartidos por el vídeo.

    En una sola pasada de ffmpeg: `select` se queda con uno de cada N
    fotogramas y `tile` los pega. Montarla desde 24 ficheros sueltos no vale,
    porque `tile` opera sobre un flujo y no sobre entradas separadas.
    """
    from .util import probe_duration

    try:
        duracion = probe_duration(video)
    except RuntimeError:
        return None
    if duracion <= 0:
        return None

    total = max(int(duracion * config.FPS), 1)
    cada = max(total // (MUESTRAS + 1), 1)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video),
         "-vf", f"select='not(mod(n,{cada}))',scale={ANCHO}:-1,tile={REJILLA}",
         "-frames:v", "1", "-vsync", "0", str(dest)],
        capture_output=True,
    )
    return dest if dest.exists() and dest.stat().st_size > 10_000 else None


def revisar(video: Path, tema: str, workdir: Path) -> dict:
    """Devuelve {'fuera': [índices], 'porcentaje': float, 'motivos': [...]}.

    Ante cualquier fallo devuelve un veredicto vacío: el portero nunca tumba
    una producción por un problema suyo, solo por lo que ve.
    """
    vacio = {"fuera": [], "porcentaje": 0.0, "motivos": [], "revisado": False}
    if not config.QUALITY_GATE:
        return vacio

    hoja = workdir / "control.png"
    if _hoja(video, hoja) is None:
        log.warning("Control de calidad: no se pudo montar la hoja de contactos")
        return vacio

    datos = base64.standard_b64encode(hoja.read_bytes()).decode()
    prompt = f"""Esta hoja de contactos son {MUESTRAS} fotogramas de un \
documental de {config.CHANNEL_NAME}, tomados en orden y repartidos por todo el \
montaje. Se leen de izquierda a derecha y de arriba abajo, numerados de 0 a \
{MUESTRAS - 1}.

El tema del vídeo es: «{tema}»

Señala los fotogramas que NO pegan con un documental espacial sobre ese tema.

Cuenta como fuera de tema:
- Escenas terrestres cotidianas: deporte, oficinas, ciudades, gente trabajando.
- Archivo médico o de laboratorio: células, órganos, tubos de ensayo, batas.
- Naturaleza de la Tierra sin relación: árboles, flores, insectos, playas.
- Rótulos, logotipos o cortinillas de otra marca incrustados en el clip.
- Fondos abstractos de banco de vídeo que no muestran nada concreto.

NO cuenta como fuera de tema:
- Planos de la Tierra vista desde el espacio.
- Naves, satélites, telescopios, astronautas.
- Cualquier objeto astronómico, real o recreado.
- Los rótulos blancos del propio canal sobre el plano.

Devuelve solo este JSON, sin texto alrededor:
{{"fuera": [números], "motivos": ["número: qué se ve"]}}"""

    from .script_gen import client, texto_de

    try:
        resp = client().messages.create(
            model=config.SCRIPT_MODEL,
            max_tokens=1500,
            system=SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": datos}},
                {"type": "text", "text": prompt},
            ]}],
        )
        crudo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto_de(resp).strip())
        veredicto = json.loads(crudo)
    except Exception as exc:  # noqa: BLE001
        log.warning("Control de calidad no concluyente (%s); se deja pasar", exc)
        return vacio

    fuera = [n for n in veredicto.get("fuera", []) if isinstance(n, int)]
    porcentaje = len(fuera) / MUESTRAS * 100
    motivos = [str(m) for m in veredicto.get("motivos", [])][:12]

    log.info("Control de calidad: %d de %d fotogramas fuera de tema (%.0f %%)",
             len(fuera), MUESTRAS, porcentaje)
    for m in motivos:
        log.info("   %s", m)

    return {"fuera": fuera, "porcentaje": porcentaje, "motivos": motivos,
            "revisado": True}


def exigir(video: Path, tema: str, workdir: Path) -> dict:
    """Como revisar(), pero levanta si el vídeo no llega al mínimo."""
    v = revisar(video, tema, workdir)
    if v["revisado"] and v["porcentaje"] > TOPE:
        raise RuntimeError(
            f"Montaje rechazado por el control de calidad: "
            f"{v['porcentaje']:.0f} % de los fotogramas están fuera de tema "
            f"(tope {TOPE:.0f} %).\n" + "\n".join(f"  {m}" for m in v["motivos"])
        )
    return v


# --------------------------------------------------------------------------
# Cribado de fuentes, antes de montar
# --------------------------------------------------------------------------
# Mirar el vídeo terminado llega tarde: rechaza dos horas de trabajo y no
# arregla nada. Y la lista de palabras prohibidas nunca gana, porque cada
# montaje trae basura nueva con etiquetas distintas —un cartel holandés de
# prohibido el paso, una microscopía, una ciudad poligonal morada—.
#
# Aquí se mira cada clip descargado ANTES de que entre en el montaje, en
# tandas, con un fotograma por clip. Lo que no pega se descarta y el banco
# busca otra cosa. Sale a una llamada por cada TANDA clips.

TANDA = 12


def _tira(videos: list[Path], dest: Path) -> tuple[Path, list[int]] | None:
    """Rejilla con un fotograma de cada clip. Devuelve (imagen, índices).

    Los índices dicen a qué clip corresponde cada casilla, porque un clip que
    no da fotograma se salta y desplazaría la numeración.

    En rejilla y no en fila: doce fotogramas en tira dan una imagen de
    3840x270 donde cada uno queda diminuto, y probado así no reconoció una
    aspiradora a pantalla completa. Y con `tile` sobre una secuencia de
    imágenes, no con `xstack`, que espera coordenadas en píxeles y no índices
    de cuadrícula: pasarle índices devuelve una imagen corrupta.
    """
    import tempfile as tf

    with tf.TemporaryDirectory(prefix="eter_criba_") as tmp:
        carpeta = Path(tmp)
        indices: list[int] = []
        for i, v in enumerate(videos):
            f = carpeta / f"s{len(indices):03d}.png"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(v),
                 "-frames:v", "1", "-vf", "scale=480:270,setsar=1", str(f)],
                capture_output=True,
            )
            if f.exists():
                indices.append(i)
        if len(indices) < 2:
            return None
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-framerate", "1",
             "-i", str(carpeta / "s%03d.png"),
             "-vf", f"tile=4x{(len(indices) + 3) // 4}",
             "-frames:v", "1", str(dest)],
            capture_output=True,
        )
    if dest.exists() and dest.stat().st_size > 5_000:
        return dest, indices
    return None


def criba(videos: list[Path], tema: str, workdir: Path) -> set[int]:
    """Índices de los clips que NO pegan con el tema. Ante la duda, ninguno."""
    if not config.QUALITY_GATE or len(videos) < 2:
        return set()

    tira = workdir / "criba.png"
    hecho = _tira(videos, tira)
    if hecho is None:
        return set()
    _, indices = hecho

    datos = base64.standard_b64encode(tira.read_bytes()).decode()
    prompt = f"""Esta rejilla son {len(indices)} fotogramas, uno por clip, numerados de 0 a {len(indices) - 1} de izquierda a derecha y de arriba abajo. Son candidatos a entrar en un documental espacial sobre: «{tema}»

Di cuáles NO valen. No valen: escenas terrestres cotidianas, interiores, carteles o texto incrustado, archivo médico o de laboratorio, naturaleza de la Tierra, gráficos de tecnología o química, y fondos abstractos que no muestran ningún objeto concreto.

Sí valen: cualquier objeto astronómico real o recreado, naves, satélites, astronautas, y la Tierra vista desde el espacio.

Devuelve solo: {{"fuera": [números]}}"""

    from .script_gen import client, texto_de

    try:
        resp = client().messages.create(
            model=config.SCRIPT_MODEL,
            max_tokens=400,
            system=SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": datos}},
                {"type": "text", "text": prompt},
            ]}],
        )
        crudo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto_de(resp).strip())
        fuera = {n for n in json.loads(crudo).get("fuera", []) if isinstance(n, int)}
    except Exception as exc:  # noqa: BLE001
        log.debug("Criba no concluyente (%s); pasan todos", exc)
        return set()

    # Del número de casilla al número de clip.
    fuera = {indices[n] for n in fuera if 0 <= n < len(indices)}
    if fuera:
        log.info("Criba: %d de %d clips descartados por no pegar con el tema",
                 len(fuera), len(videos))
    return fuera
