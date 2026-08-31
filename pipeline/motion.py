"""Motion graphics sobre el plano: datos y fichas de objeto.

Son la capa que separa un montaje de archivo de uno producido. El guion ya
marca la frase que remata cada escena y la locución devuelve los tiempos
palabra por palabra, así que el gráfico puede caer exactamente cuando se
pronuncia el dato, no «por ahí».

Dos piezas, las dos con el halo blanco de la marca:

DATO    Una cifra grande entre dos líneas horizontales, con su unidad debajo.
        Se dispara cuando la frase de remate contiene un número que merece
        verse: «4.300.000 masas solares», «200.000 millones de veces».

FICHA   Marco de esquinas con el nombre del objeto y una línea de contexto,
        abajo a la izquierda. Se dispara al nombrar un objeto propio.

Ambas entran desplazándose y con fundido, y salen igual. La animación se hace
con expresiones de ffmpeg sobre un PNG único: una secuencia de imágenes daría
un movimiento más rico, pero multiplica por veinte los ficheros intermedios de
un runner que ya va justo de disco.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config
from .util import log

# Un número por debajo de mil no impresiona a nadie y satura la pantalla.
MIN_VALOR = 1000

# Cuánto dura el gráfico en pantalla, y cuánto tarda en entrar y salir.
SECONDS = 3.4
SLIDE = 0.45

# Como mucho uno de cada tantas escenas: si aparece en todas deja de destacar
# y pasa a ser decorado.
CADA = 2

_UNIDADES = (
    "años luz", "años", "kilómetros", "km", "metros", "grados", "veces",
    "masas solares", "masas", "millones", "millones de años", "toneladas",
    "segundos", "minutos", "horas", "días", "siglos", "estrellas", "galaxias",
    "planetas", "kilómetros por segundo", "km/s", "kelvin", "atmósferas",
)

_NUM = re.compile(
    r"(\d[\d.,]*)\s*(mil millones|millones|mil|billones)?\s*(?:de\s+)?(" +
    "|".join(sorted(_UNIDADES, key=len, reverse=True)) + r")?",
    re.IGNORECASE,
)


def _limpio(texto: str) -> str:
    return " ".join(texto.split())


def dato_de(frase: str) -> tuple[str, str] | None:
    """(cifra, unidad) si la frase lleva un número que merece un gráfico."""
    for m in _NUM.finditer(frase):
        crudo, escala, unidad = m.group(1), m.group(2), m.group(3)
        digitos = crudo.replace(".", "").replace(",", ".")
        try:
            valor = float(digitos)
        except ValueError:
            continue
        if valor < MIN_VALOR and not escala:
            continue
        cifra = crudo.rstrip(".,")
        etiqueta = " de ".join(x for x in (escala, unidad) if x)
        if not etiqueta:
            continue
        return cifra, etiqueta.upper()
    return None


# --------------------------------------------------------------------------
# Dibujo
# --------------------------------------------------------------------------


def _halo(capa, radios=((24, 0.5), (7, 0.75))):
    from PIL import Image, ImageFilter

    fuera = Image.new("RGBA", capa.size, (0, 0, 0, 0))
    for radio, fuerza in radios:
        difuso = capa.filter(ImageFilter.GaussianBlur(radio))
        alfa = difuso.split()[-1].point(lambda v: int(v * fuerza))
        blanco = Image.new("RGBA", capa.size, (255, 255, 255, 0))
        blanco.putalpha(alfa)
        fuera = Image.alpha_composite(fuera, blanco)
    return Image.alpha_composite(fuera, capa)


def render_dato(cifra: str, unidad: str, dest: Path) -> Path | None:
    """Cifra grande centrada entre dos reglas, con la unidad debajo."""
    from PIL import Image, ImageDraw

    from .captions import _font

    W, H = config.WIDTH, config.HEIGHT
    lienzo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pincel = ImageDraw.Draw(lienzo)

    cuerpo = 150 if len(cifra) <= 9 else 110
    fuente = _font(cuerpo)
    while cuerpo > 48:
        fuente = _font(cuerpo)
        caja = fuente.getbbox(cifra)
        if (caja[2] - caja[0]) <= W * 0.62:
            break
        cuerpo -= 8

    menor = _font(max(int(cuerpo * 0.26), 22))
    caja = fuente.getbbox(cifra)
    ancho = caja[2] - caja[0]
    x = (W - ancho) // 2 - caja[0]
    y = int(H * 0.40)

    # Las reglas se dimensionan con el MÁS ancho de los dos textos. Midiéndolas
    # solo con la cifra, un número corto como «4,3» daba una regla más estrecha
    # que su propia unidad y la de abajo cortaba el texto por la mitad.
    caja2 = menor.getbbox(unidad)
    ancho_unidad = caja2[2] - caja2[0]
    regla = max(int(max(ancho, ancho_unidad) * 0.60) + 40, 240)

    pincel.rectangle([W // 2 - regla, y - 38, W // 2 + regla, y - 34],
                     fill=(255, 255, 255, 195))
    pincel.text((x, y), cifra, font=fuente, fill=(255, 255, 255, 255))

    baja = y + int(cuerpo * 1.26)
    x2 = (W - ancho_unidad) // 2 - caja2[0]
    pincel.text((x2, baja), unidad, font=menor, fill=(255, 255, 255, 220))
    pincel.rectangle([W // 2 - regla, baja + int(cuerpo * 0.44),
                      W // 2 + regla, baja + int(cuerpo * 0.44) + 4],
                     fill=(255, 255, 255, 195))

    # Velo oscuro detrás del bloque. Sin él, la cifra en blanco se pierde
    # cuando el plano de debajo es claro: en un vídeo real, «6.500 MILLONES DE
    # MASAS SOLARES» cayó sobre un disco de acreción brillante y no se leía.
    # Es una banda difuminada, no un rectángulo: un borde recto se ve.
    from PIL import ImageFilter

    velo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(velo).rectangle(
        [W // 2 - regla - 120, y - 130,
         W // 2 + regla + 120, baja + int(cuerpo * 0.44) + 90],
        fill=(0, 0, 0, 150),
    )
    velo = velo.filter(ImageFilter.GaussianBlur(60))

    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(velo, _halo(lienzo)).save(dest, "PNG")
    return dest


def render_ficha(titulo: str, linea: str, dest: Path) -> Path | None:
    """Marco de esquinas con nombre del objeto y una línea de contexto."""
    from PIL import Image, ImageDraw

    from .captions import _font

    W, H = config.WIDTH, config.HEIGHT
    lienzo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pincel = ImageDraw.Draw(lienzo)

    margen, brazo = 74, 92
    for px, py, dx, dy in ((margen, margen, 1, 1), (W - margen, margen, -1, 1),
                           (margen, H - margen, 1, -1), (W - margen, H - margen, -1, -1)):
        pincel.line([(px, py), (px + dx * brazo, py)], fill=(255, 255, 255, 205), width=3)
        pincel.line([(px, py), (px, py + dy * brazo)], fill=(255, 255, 255, 205), width=3)

    grande, chica = _font(54), _font(30)
    titulo = _limpio(titulo).upper()[:38]
    pincel.text((margen + 22, H - 196), titulo, font=grande, fill=(255, 255, 255, 255))
    if linea:
        pincel.text((margen + 24, H - 128), _limpio(linea).upper()[:64],
                    font=chica, fill=(255, 255, 255, 195))

    dest.parent.mkdir(parents=True, exist_ok=True)
    _halo(lienzo, ((20, 0.42), (6, 0.7))).save(dest, "PNG")
    return dest


# --------------------------------------------------------------------------
# Planificación
# --------------------------------------------------------------------------


_OBJETO = re.compile(
    r"\b(Sagitario A\*?|Sagittarius A\*?|Cygnus X-1|M87|TON 618|"
    r"Vía Láctea|Andrómeda|Betelgeuse|Proxima Centauri|Alfa Centauri|"
    r"Voyager \d|Hubble|James Webb|Encélado|Europa|Titán|Ío|Ganímedes|"
    r"RX J\d[\w.\-−]*|PSR [\w.+\-]+|SS 433|V404 Cygni|GW\d{6})\b")


def objeto_de(frase: str) -> tuple[str, str] | None:
    """(nombre, contexto) si la frase nombra un objeto astronómico propio."""
    m = _OBJETO.search(frase)
    if not m:
        return None
    # Solo el nombre. Se probó a sacar de la frase una línea de contexto y no
    # hay manera: un fragmento recortado siempre sale raro debajo del nombre
    # —«En un objeto tan grande como * las fuerz», «no es la naturaleza de la
    # trampa»—. El marco con el nombre a secas se lee bien siempre.
    return m.group(1), ""


def plan(scenes) -> list[tuple[float, float, str, tuple]]:
    """(inicio, fin, tipo, argumentos) de cada gráfico, en el reloj del vídeo.

    Dos tipos, y se alternan para que no canse siempre el mismo recurso:
    la cifra grande cuando la escena trae un número, y la ficha de objeto
    cuando nombra algo con nombre propio.

    Se busca en TODA la narración de la escena, no en su frase de énfasis: esa
    se elige por el golpe que da, no por llevar datos, y mirando solo ahí un
    guion con seis cifras aprovechables producía cero gráficos.

    Se sitúa contra las palabras de la transcripción y, si no se puede situar,
    no se pone: un gráfico desincronizado es peor que no tenerlo.
    """
    from .sfx import _locate
    from .util import sentences

    fuera: list[tuple[float, float, str, tuple]] = []
    reloj = 0.0
    ultima = -99
    ultimo_tipo = ""
    for scene in scenes:
        inicio_escena = reloj
        reloj += scene.duration + config.SCENE_GAP

        if scene.index - ultima < CADA:
            continue

        for frase in sentences(scene.narration or ""):
            # Se prueba primero el tipo que NO se usó la vez anterior.
            orden = [("ficha", objeto_de), ("dato", dato_de)]
            if ultimo_tipo == "ficha":
                orden.reverse()

            for tipo, extractor in orden:
                par = extractor(frase)
                if not par:
                    continue
                dentro = _locate(scene, frase)
                if dentro is None:
                    continue
                arranque = inicio_escena + dentro
                if arranque < 0.5:
                    continue
                fuera.append((arranque, arranque + SECONDS, tipo, par))
                ultima, ultimo_tipo = scene.index, tipo
                break
            if ultima == scene.index:
                break
    return fuera


def build(scenes, workdir: Path) -> list[tuple[float, float, Path]]:
    """Genera los PNG y devuelve (inicio, fin, ruta)."""
    if not config.MOTION_ENABLED:
        return []

    carpeta = workdir / "motion"
    salida: list[tuple[float, float, Path]] = []
    for i, (inicio, fin, tipo, args) in enumerate(plan(scenes)):
        png = carpeta / f"mg_{i:03d}.png"
        if not png.exists():
            hecho = render_dato(*args, png) if tipo == "dato" else render_ficha(*args, png)
            if hecho is None:
                continue
        if png.exists():
            salida.append((inicio, fin, png))

    if salida:
        log.info("Motion graphics: %d gráficos de dato", len(salida))
    return salida


def overlay_filters(graficos: list[tuple[float, float, Path]], entrada: str,
                    primer_indice: int) -> tuple[list[str], list[str], str]:
    """Superposición con entrada y salida desplazadas, para assemble."""
    entradas: list[str] = []
    pasos: list[str] = []
    actual = entrada

    for i, (inicio, fin, png) in enumerate(graficos):
        idx = primer_indice + i
        largo = fin - inicio
        entradas += ["-loop", "1", "-t", f"{largo + 0.2:.3f}", "-i", str(png)]
        pasos.append(
            f"[{idx}:v]format=rgba,"
            f"fade=t=in:st=0:d={SLIDE}:alpha=1,"
            f"fade=t=out:st={max(largo - SLIDE, 0.1):.3f}:d={SLIDE}:alpha=1,"
            f"setpts=PTS-STARTPTS+{inicio:.3f}/TB[mg{i}]"
        )
        etiqueta = f"mgo{i}"
        # Sube 26 px durante la entrada y se queda quieto: el desplazamiento
        # es lo que lo hace parecer animado y no un cartel pegado.
        desplazamiento = f"if(lt(t-{inicio:.3f},{SLIDE}),26*(1-(t-{inicio:.3f})/{SLIDE}),0)"
        pasos.append(
            f"[{actual}][mg{i}]overlay=0:'{desplazamiento}':"
            f"enable='between(t,{inicio:.3f},{fin:.3f})'[{etiqueta}]"
        )
        actual = etiqueta

    return entradas, pasos, actual
