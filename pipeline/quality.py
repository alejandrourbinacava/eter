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
# Con 24 muestras, 20 % son cinco. Un montaje sano baja del 8 %.
TOPE = 20.0

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
