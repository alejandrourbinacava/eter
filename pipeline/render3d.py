"""Planos renderizados aquí mismo, sin banco de stock ni IA de pago.

Los bancos gratuitos no tienen astronomía: para «disco de acreción» devuelven
fondos abstractos, y para «polvo en el vacío», una aspiradora. Pero un agujero
negro con su disco no hace falta buscarlo, porque es física conocida y se
puede calcular.

Se traza el camino de la luz alrededor de una masa usando la ecuación de las
geodésicas de Schwarzschild en la forma que se integra bien:

    d²u/dφ² + u = (3/2) · Rs · u²        con u = 1/r

De ahí salen las tres cosas que hacen reconocible la imagen: la sombra central,
el anillo de Einstein donde la luz da la vuelta, y el disco que se ve por
encima Y por debajo a la vez porque la gravedad curva su parte trasera hacia
el observador.

El truco que lo hace viable en un runner: con la cámara quieta, el mapa de
deflexión es el MISMO en todos los fotogramas. Se calcula una vez —unos
segundos— y luego cada fotograma solo consulta texturas. Renderizar cinco
segundos cuesta menos que descargar un clip.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from . import config
from .util import log

# Radio de Schwarzschild en unidades de la escena. Todo lo demás va referido
# a él, así que cambiarlo solo cambia el zoom.
RS = 1.0

# La esfera de fotones está en 1,5·Rs y el radio de captura aparente, en
# 2,6·Rs: por dentro de ese parámetro de impacto la luz no vuelve. Es lo que
# dibuja el borde de la sombra.
B_CAPTURA = 2.6 * RS

# Disco de acreción, en radios de Schwarzschild. El borde interior en 3·Rs es
# la última órbita circular estable de un agujero negro sin rotación.
DISCO_INT = 3.0 * RS
DISCO_EXT = 12.0 * RS

PASOS = 220          # pasos de integración por rayo
CAMPO = 60.0         # grados de campo horizontal


def _mapa(ancho: int, alto: int, inclinacion: float):
    """Traza un rayo por píxel y devuelve dónde acaba cada uno.

    Devuelve (fondo_u, fondo_v, disco_r, disco_phi, dentro), todos del tamaño
    de la imagen: las coordenadas donde el rayo corta el cielo de fondo, dónde
    corta el disco si lo corta, y qué píxeles caen en la sombra.
    """
    import numpy as np

    # Dirección inicial de cada rayo, en el sistema de la cámara.
    escala = math.tan(math.radians(CAMPO) / 2)
    x = (np.arange(ancho) / (ancho - 1) * 2 - 1) * escala
    y = (np.arange(alto) / (alto - 1) * 2 - 1) * escala * alto / ancho
    px, py = np.meshgrid(x, y)
    pz = np.ones_like(px)

    norma = np.sqrt(px**2 + py**2 + pz**2)
    dx, dy, dz = px / norma, py / norma, pz / norma

    # La cámara mira al agujero desde una distancia, inclinada sobre el plano
    # del disco. Se rota el rayo, no la escena.
    inc = math.radians(inclinacion)
    ci, si = math.cos(inc), math.sin(inc)
    dy, dz = dy * ci - dz * si, dy * si + dz * ci

    distancia = 34.0 * RS
    ox = np.zeros_like(dx)
    oy = np.full_like(dy, distancia * si)
    oz = np.full_like(dz, -distancia * ci)

    # Cada rayo vive en su propio plano, el que contiene al origen y su
    # dirección. Se integra ahí en dos dimensiones y luego se vuelve al
    # espacio: es lo que permite hacerlo vectorizado sobre toda la imagen.
    rx, ry, rz = ox, oy, oz
    r0 = np.sqrt(rx**2 + ry**2 + rz**2)

    # Normal del plano de cada rayo.
    nx = ry * dz - rz * dy
    ny = rz * dx - rx * dz
    nz = rx * dy - ry * dx
    nn = np.sqrt(nx**2 + ny**2 + nz**2) + 1e-12
    nx, ny, nz = nx / nn, ny / nn, nz / nn

    # Parámetro de impacto: distancia del centro a la recta del rayo.
    b = nn

    # Ejes del plano: e1 hacia la posición inicial, e2 perpendicular.
    e1x, e1y, e1z = rx / r0, ry / r0, rz / r0
    e2x = ny * e1z - nz * e1y
    e2y = nz * e1x - nx * e1z
    e2z = nx * e1y - ny * e1x

    # Integración de u(φ) con Runge-Kutta 4. φ crece desde la cámara hacia
    # donde iba el rayo.
    u = 1.0 / r0
    # du/dφ inicial: proyección de la dirección sobre el radio.
    cos_a = (dx * e1x + dy * e1y + dz * e1z)
    du = -u * cos_a / np.sqrt(np.maximum(1 - cos_a**2, 1e-12))

    dphi = math.pi * 2.2 / PASOS
    dentro = np.zeros(u.shape, dtype=bool)
    corta = np.zeros(u.shape, dtype=bool)
    disco_r = np.zeros(u.shape)
    disco_phi = np.zeros(u.shape)
    phi = np.zeros(u.shape)

    def acel(uu):
        return -uu + 1.5 * RS * uu**2

    prev_y = None
    for _ in range(PASOS):
        # Posición actual en 3D, para saber si cruza el plano del disco.
        r = 1.0 / np.maximum(u, 1e-9)
        c, s = np.cos(phi), np.sin(phi)
        Px = r * (e1x * c + e2x * s)
        Py = r * (e1y * c + e2y * s)
        Pz = r * (e1z * c + e2z * s)

        if prev_y is not None:
            cruza = (prev_y * Py < 0) & (~dentro) & (~corta)
            rad = np.sqrt(Px**2 + Pz**2)
            valido = cruza & (rad > DISCO_INT) & (rad < DISCO_EXT)
            disco_r = np.where(valido, rad, disco_r)
            disco_phi = np.where(valido, np.arctan2(Pz, Px), disco_phi)
            corta |= valido
        prev_y = Py

        # RK4 sobre (u, du).
        k1u, k1d = du, acel(u)
        k2u, k2d = du + 0.5 * dphi * k1d, acel(u + 0.5 * dphi * k1u)
        k3u, k3d = du + 0.5 * dphi * k2d, acel(u + 0.5 * dphi * k2u)
        k4u, k4d = du + dphi * k3d, acel(u + dphi * k3u)
        u = u + dphi / 6 * (k1u + 2 * k2u + 2 * k3u + k4u)
        du = du + dphi / 6 * (k1d + 2 * k2d + 2 * k3d + k4d)
        phi = phi + dphi

        dentro |= (u > 1.0 / (1.05 * RS))
        u = np.where(dentro, 1.0 / (1.05 * RS), u)

    # A dónde apunta el rayo al escapar: su dirección final da el punto del
    # cielo de fondo.
    c, s = np.cos(phi), np.sin(phi)
    fx = e1x * c + e2x * s
    fy = e1y * c + e2y * s
    fz = e1z * c + e2z * s
    fondo_u = (np.arctan2(fz, fx) / (2 * math.pi) + 0.5) % 1.0
    fondo_v = np.clip(np.arccos(np.clip(fy, -1, 1)) / math.pi, 0, 1)

    dentro |= (b < B_CAPTURA) & (np.abs(phi) > math.pi)
    return fondo_u, fondo_v, disco_r, disco_phi, corta, dentro


def _cielo(ancho: int, alto: int, semilla: int):
    """Campo de estrellas equirectangular, con algo de polvo de fondo."""
    import numpy as np

    rng = np.random.default_rng(semilla)
    cielo = np.zeros((alto, ancho, 3), dtype=np.float32)

    # Nebulosa tenue: ruido suavizado en dos octavas, teñido de frío.
    from scipy.ndimage import gaussian_filter

    for escala, peso in ((60, 0.055), (18, 0.03)):
        n = gaussian_filter(rng.random((alto, ancho)).astype(np.float32), escala)
        n = (n - n.min()) / (n.max() - n.min() + 1e-9)
        cielo[..., 0] += n * peso * 0.5
        cielo[..., 1] += n * peso * 0.7
        cielo[..., 2] += n * peso

    # Estrellas: muchas débiles y unas pocas brillantes.
    cuantas = int(ancho * alto * 0.0032)
    ys = rng.integers(0, alto, cuantas)
    xs = rng.integers(0, ancho, cuantas)
    brillo = rng.power(0.35, cuantas).astype(np.float32)
    # Temperatura de color: de anaranjadas a azuladas.
    t = rng.random(cuantas).astype(np.float32)
    cielo[ys, xs, 0] += brillo * (0.75 + 0.35 * (1 - t))
    cielo[ys, xs, 1] += brillo * 0.85
    cielo[ys, xs, 2] += brillo * (0.75 + 0.35 * t)
    return np.clip(cielo, 0, 4)


def agujero_negro(dest: Path, seconds: float = 6.0, inclinacion: float = 12.0,
                  semilla: int = 0) -> Path | None:
    """Renderiza un agujero negro con disco de acreción y fondo estrellado."""
    import numpy as np

    W, H = config.WIDTH, config.HEIGHT
    fps = config.FPS
    n_frames = max(int(seconds * fps), 1)

    log.info("Renderizando agujero negro: %dx%d, %.1f s", W, H, seconds)
    # Se traza a media resolución y se amplía: el mapa es suave y no se nota,
    # y cuesta la cuarta parte.
    mw, mh = W // 2, H // 2
    fu, fv, dr, dphi, corta, dentro = _mapa(mw, mh, inclinacion)

    cielo = _cielo(2048, 1024, semilla)
    ch, cw = cielo.shape[:2]

    tmp = dest.parent / f".{dest.stem}_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    # Perfil radial del disco: brillante por dentro, se apaga hacia fuera.
    caida = np.clip((DISCO_EXT - dr) / (DISCO_EXT - DISCO_INT), 0, 1) ** 1.6
    caliente = np.clip((dr - DISCO_INT) / (DISCO_EXT - DISCO_INT), 0, 1)

    for i in range(n_frames):
        t = i / fps
        # El cielo gira despacio; el disco, mucho más rápido por dentro que
        # por fuera, que es como orbita de verdad.
        u_img = (fu + t * 0.004) % 1.0
        xi = np.clip((u_img * (cw - 1)).astype(np.int32), 0, cw - 1)
        yi = np.clip((fv * (ch - 1)).astype(np.int32), 0, ch - 1)
        img = cielo[yi, xi].copy()

        # Disco: bandas que giran con velocidad kepleriana.
        omega = 1.8 / np.maximum(dr, 1e-3) ** 1.5
        fase = dphi + t * omega * 6.0
        # Turbulencia en espiral, no anillos concéntricos: el término en
        # log(r) inclina las bandas y las arrastra, que es como se ve el gas
        # cayendo. Solo con seno del ángulo salían aros de vinilo.
        espiral = fase + 2.6 * np.log(np.maximum(dr, 1e-3))
        bandas = (0.58
                  + 0.26 * np.sin(espiral * 2.0)
                  + 0.14 * np.sin(espiral * 5.3 + 2.1)
                  + 0.08 * np.sin(espiral * 11.7 - 0.7))
        # Doppler: el lado que viene hacia la cámara brilla más.
        doppler = 1.0 + 0.85 * np.sin(dphi)
        brillo = caida * bandas * np.clip(doppler, 0.25, 2.2) * 2.1

        col = np.zeros_like(img)
        col[..., 0] = brillo * 1.00
        col[..., 1] = brillo * (0.55 + 0.30 * (1 - caliente))
        col[..., 2] = brillo * (0.22 + 0.45 * (1 - caliente))
        img = np.where(corta[..., None], img + col, img)

        img[dentro] = 0.0

        # Revelado: compresión suave y un punto de contraste.
        img = 1.0 - np.exp(-img * 1.9)
        img = np.clip(img, 0, 1) ** 0.92
        marco = Image.fromarray((img * 255).astype(np.uint8)).resize(
            (W, H), Image.LANCZOS)
        marco.save(tmp / f"f{i:04d}.png")

    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-framerate", str(fps),
         "-i", str(tmp / "f%04d.png"), "-c:v", "libx264", "-crf", "17",
         "-preset", "medium", "-pix_fmt", "yuv420p", str(dest)],
        capture_output=True,
    )
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return dest if dest.exists() else None


# --------------------------------------------------------------------------
# Otros sujetos que el archivo no cubre
# --------------------------------------------------------------------------


def _guardar(marcos, dest: Path, fps: int) -> Path | None:
    """Vuelca una secuencia de fotogramas a MP4 y limpia los PNG."""
    from PIL import Image

    tmp = dest.parent / f".{dest.stem}_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, arr in enumerate(marcos):
        Image.fromarray(arr).save(tmp / f"f{i:04d}.png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-framerate", str(fps),
         "-i", str(tmp / "f%04d.png"), "-c:v", "libx264", "-crf", "17",
         "-preset", "medium", "-pix_fmt", "yuv420p", str(dest)],
        capture_output=True,
    )
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    return dest if dest.exists() else None


def campo_estrellas(dest: Path, seconds: float = 6.0, semilla: int = 0,
                    velocidad: float = 0.55) -> Path | None:
    """Vuelo por un campo de estrellas, con paralaje real por profundidad.

    Cada estrella tiene su distancia y se proyecta en perspectiva, así que las
    cercanas barren la pantalla y las lejanas apenas se mueven. Es lo que
    distingue un vuelo espacial de un fondo con puntos moviéndose en bloque.
    """
    import numpy as np

    W, H = config.WIDTH, config.HEIGHT
    fps = config.FPS
    n = max(int(seconds * fps), 1)
    rng = np.random.default_rng(semilla)

    cuantas = 2600
    # Distribuidas en un tronco de pirámide delante de la cámara.
    z = rng.uniform(1.0, 60.0, cuantas)
    x = rng.uniform(-30, 30, cuantas)
    y = rng.uniform(-18, 18, cuantas)
    mag = rng.power(0.45, cuantas)
    temp = rng.random(cuantas)

    marcos = []
    for i in range(n):
        z_i = z - i * velocidad
        # La estrella que se pasa de largo reaparece al fondo.
        z_i = np.where(z_i < 0.6, z_i + 60.0, z_i)
        px = (x / z_i) * (W * 0.9) + W / 2
        py = (y / z_i) * (W * 0.9) + H / 2
        vis = (px >= 1) & (px < W - 1) & (py >= 1) & (py < H - 1)

        img = np.zeros((H, W, 3), dtype=np.float32)
        # Fondo: un tinte muy leve para que el negro no sea plano.
        img[..., 2] += 0.012
        img[..., 0] += 0.006

        xi = px[vis].astype(np.int32)
        yi = py[vis].astype(np.int32)
        # Cuanto más cerca, más brillante y más grande.
        brillo = (mag[vis] * (6.0 / z_i[vis])).astype(np.float32)
        t = temp[vis]
        np.add.at(img, (yi, xi, 0), brillo * (0.7 + 0.4 * (1 - t)))
        np.add.at(img, (yi, xi, 1), brillo * 0.8)
        np.add.at(img, (yi, xi, 2), brillo * (0.7 + 0.4 * t))
        # Las más cercanas dejan un punto de dos píxeles.
        cerca = brillo > 0.8
        if cerca.any():
            np.add.at(img, (yi[cerca], xi[cerca] + 1, slice(None)), 0.35)
            np.add.at(img, (yi[cerca] + 1, xi[cerca], slice(None)), 0.35)

        img = 1.0 - np.exp(-img * 2.2)
        marcos.append((np.clip(img, 0, 1) * 255).astype(np.uint8))

    log.info("Renderizando campo de estrellas: %d fotogramas", n)
    return _guardar(marcos, dest, fps)


def planeta(dest: Path, seconds: float = 6.0, semilla: int = 0,
            color=(0.35, 0.45, 0.62)) -> Path | None:
    """Un planeta girando, con terminador y borde de atmósfera.

    La superficie es ruido fractal proyectado sobre la esfera y desplazado en
    longitud, así que gira de verdad en vez de deslizarse una textura plana.
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    W, H = config.WIDTH, config.HEIGHT
    fps = config.FPS
    n = max(int(seconds * fps), 1)
    rng = np.random.default_rng(semilla)

    # Textura equirectangular del planeta.
    tw, th = 1024, 512
    superficie = np.zeros((th, tw), dtype=np.float32)
    for escala, peso in ((40, 1.0), (16, 0.5), (6, 0.25), (2, 0.12)):
        capa = gaussian_filter(rng.random((th, tw)).astype(np.float32), escala)
        capa = (capa - capa.min()) / (capa.max() - capa.min() + 1e-9)
        superficie += capa * peso
    superficie /= superficie.max()

    # Geometría: la esfera ocupa parte del encuadre, descentrada.
    radio = H * 0.42
    cx, cy = W * 0.38, H * 0.52
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = (xx - cx) / radio
    dy = (yy - cy) / radio
    d2 = dx**2 + dy**2
    dentro = d2 <= 1.0
    dz = np.sqrt(np.maximum(1.0 - d2, 0))

    # Latitud fija; la longitud avanza con el giro.
    lat = np.arcsin(np.clip(dy, -1, 1))
    lon0 = np.arctan2(dx, dz)

    # Iluminación desde un lado, con terminador suave.
    luz = np.clip(dx * 0.75 + dz * 0.66 - dy * 0.1, 0, 1) ** 0.8

    # Halo de atmósfera justo fuera del disco.
    borde = np.exp(-np.abs(np.sqrt(d2) - 1.0) * 26.0)

    marcos = []
    for i in range(n):
        lon = (lon0 + i * 0.0038) % (2 * math.pi)
        u = (lon / (2 * math.pi) * (tw - 1)).astype(np.int32)
        v = ((lat / math.pi + 0.5) * (th - 1)).astype(np.int32)
        alt = superficie[np.clip(v, 0, th - 1), np.clip(u, 0, tw - 1)]

        img = np.zeros((H, W, 3), dtype=np.float32)
        base = (0.45 + 0.55 * alt) * luz
        for c in range(3):
            img[..., c] = np.where(dentro, base * color[c] * 1.7, 0)
        # Atmósfera: azulada y solo del lado iluminado.
        atm = borde * np.clip(dx * 0.8 + 0.35, 0, 1)
        img[..., 0] += atm * 0.22
        img[..., 1] += atm * 0.42
        img[..., 2] += atm * 0.85

        img = 1.0 - np.exp(-img * 1.5)
        marcos.append((np.clip(img, 0, 1) * 255).astype(np.uint8))

    log.info("Renderizando planeta: %d fotogramas", n)
    return _guardar(marcos, dest, fps)


def lente_gravitacional(dest: Path, seconds: float = 8.0, semilla: int = 0) -> Path | None:
    """Una masa delante curva la luz de una galaxia que pasa por detrás.

    Es el mismo mapa de deflexión del agujero negro, pero en vez de mirar un
    disco de acreción se mira lo que hay al fondo. Cuando la fuente pasa cerca
    de la línea de visión, su imagen se estira en arcos y llega a cerrarse en
    un anillo de Einstein. No es un efecto dibujado: sale de que los rayos que
    rodean la masa por lados opuestos acaban en el mismo punto de la pantalla.
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    W, H = config.WIDTH, config.HEIGHT
    fps = config.FPS
    n = max(int(seconds * fps), 1)

    log.info("Renderizando lente gravitacional: %d fotogramas", n)
    mw, mh = W // 2, H // 2
    fu, fv, _, _, _, dentro = _mapa(mw, mh, 0.0)

    rng = np.random.default_rng(semilla)
    tw, th = 2048, 1024

    # Cielo de fondo con estrellas lejanas, muy tenue.
    fondo = np.zeros((th, tw, 3), dtype=np.float32)
    cuantas = int(tw * th * 0.0012)
    ys = rng.integers(0, th, cuantas)
    xs = rng.integers(0, tw, cuantas)
    br = rng.power(0.4, cuantas).astype(np.float32) * 0.55
    for c in range(3):
        fondo[ys, xs, c] += br

    # La fuente: una galaxia compacta y brillante, con núcleo y halo.
    gy, gx = np.mgrid[0:th, 0:tw].astype(np.float32)

    marcos = []
    for i in range(n):
        t = i / max(n - 1, 1)
        # La fuente cruza por detrás de la masa, de un lado al otro.
        cx = tw * (0.5 + 0.055 * math.cos(math.pi * t))
        cy = th * (0.5 - 0.035 + 0.07 * t)
        d = np.sqrt((gx - cx) ** 2 + ((gy - cy) * 1.35) ** 2)

        capa = fondo.copy()
        nucleo = np.exp(-(d / 7.0) ** 2)
        halo = np.exp(-(d / 26.0) ** 1.4)
        capa[..., 0] += nucleo * 2.6 + halo * 0.55
        capa[..., 1] += nucleo * 2.2 + halo * 0.42
        capa[..., 2] += nucleo * 1.6 + halo * 0.30

        xi = np.clip((fu * (tw - 1)).astype(np.int32), 0, tw - 1)
        yi = np.clip((fv * (th - 1)).astype(np.int32), 0, th - 1)
        img = capa[yi, xi]

        # La masa que hace de lente: una galaxia elíptica difusa en el centro.
        yy, xx = np.mgrid[0:mh, 0:mw].astype(np.float32)
        rr = np.sqrt(((xx - mw / 2) / (mw * 0.055)) ** 2
                     + ((yy - mh / 2) / (mh * 0.075)) ** 2)
        elip = np.exp(-rr ** 1.5) * 0.85
        img = img + np.stack([elip * 0.95, elip * 0.85, elip * 0.70], axis=-1)
        img[dentro] *= 0.15

        img = 1.0 - np.exp(-img * 1.7)
        from PIL import Image
        marco = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
        marcos.append(np.asarray(marco.resize((W, H), Image.LANCZOS)))

    return _guardar(marcos, dest, fps)


# --------------------------------------------------------------------------
# Diagramas explicativos
# --------------------------------------------------------------------------
# La otra familia de plano que el archivo no da: el esquema en perspectiva con
# los rayos de luz dibujados y los elementos rotulados. Es lo que usan los
# divulgadores para explicar un mecanismo, y lo que hace que un montaje
# parezca producido en vez de recopilado.
#
# Se dibuja con PIL sobre negro, con la tipografía y el halo blanco de la
# marca, y las líneas se van trazando: el recorrido aparece a la vez que la
# narración lo cuenta.


# Mucho más flojo que el de los rótulos: calibrado para texto reventaba el
# dibujo entero, porque una línea de 4 px con halo de 16 se convierte en una
# mancha. Aquí el halo solo tiene que separar el trazo del fondo.
def _halo_diagrama(capa, radios=((5, 0.09), (2, 0.11))):
    from PIL import Image, ImageFilter

    fuera = Image.new("RGBA", capa.size, (0, 0, 0, 0))
    for radio, fuerza in radios:
        difuso = capa.filter(ImageFilter.GaussianBlur(radio))
        alfa = difuso.split()[-1].point(lambda v: int(v * fuerza))
        blanco = Image.new("RGBA", capa.size, (255, 255, 255, 0))
        blanco.putalpha(alfa)
        fuera = Image.alpha_composite(fuera, blanco)
    return Image.alpha_composite(fuera, capa)


def diagrama_lente(dest: Path, seconds: float = 9.0, semilla: int = 3):
    """Esquema de lente gravitacional con acabado de motion graphics.

    Devuelve (ruta, eventos), donde eventos es una lista de (segundo, clase)
    para que sfx.py pueda sonorizar lo que se mueve: cuando arranca el haz,
    cuando el pulso llega al observador y cuando entra cada rotulo.

    Lo que lo separa de un dibujo de lineas:

    - Se traza al TRIPLE de resolucion y se reduce con Lanczos. PIL no suaviza
      bordes en lineas ni elipses y a tamano final salian dentados.
    - Deriva de camara: todo el conjunto se desplaza y escala muy despacio, y
      el fondo lo hace menos que el primer plano. Ese desajuste es lo que el
      ojo lee como profundidad; sin el, un esquema parece una lamina.
    - Pulsos de luz recorriendo los rayos, que es lo que convierte una linea
      quieta en energia viajando.
    - Bloom: se rescatan las zonas mas brillantes, se difuminan y se suman.
      Es lo que da el brillo de video producido en vez de dibujo plano.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageChops

    from .captions import _font

    W, H = config.WIDTH, config.HEIGHT
    fps = config.FPS
    n = max(int(seconds * fps), 1)
    # Doble y no triple. A 3x el diagrama tardaba 17 minutos por 7 segundos de
    # vídeo —nueve veces más píxeles, con bloom y deriva en cada fotograma— y
    # diez diagramas serían tres horas. A 1080p la diferencia entre 2x y 3x no
    # se aprecia: lo que subió la calidad fueron los pulsos, la cámara y el
    # bloom, y eso se mantiene igual.
    SS = 2
    w, h = W * SS, H * SS

    fuente = (w * 0.11, h * 0.46)
    lente = (w * 0.50, h * 0.46)
    ojo = (w * 0.89, h * 0.46)
    desvio = h * 0.17
    ancho_masa = w * 0.19

    def camino(signo, pasos=360):
        pts = []
        for k in range(pasos + 1):
            t = k / pasos
            x = fuente[0] + (ojo[0] - fuente[0]) * t
            d = math.exp(-(((x - lente[0]) / ancho_masa) ** 2))
            pts.append((x, fuente[1] + signo * desvio * d))
        return pts

    rayos = [camino(1.0), camino(-1.0)]

    # --- fondo: estrellas y malla del espacio-tiempo ----------------------
    rng = np.random.default_rng(semilla)
    fondo = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    df = ImageDraw.Draw(fondo)
    for _ in range(1100):
        sx, sy = int(rng.integers(0, w)), int(rng.integers(0, h))
        b = int(26 + 120 * rng.power(0.4))
        r = 1 if b < 100 else 2
        df.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(b, b, int(b * 1.06), 255))

    malla = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dm = ImageDraw.Draw(malla)
    base_y = h * 0.70
    for j in range(12):
        prof = j / 11
        yy = base_y + (h * 0.27) * prof ** 1.7
        alfa = int(52 * (1 - prof * 0.7))
        pts = []
        for k in range(161):
            xx = w * k / 160
            hund = (h * 0.09) * math.exp(
                -(((xx - lente[0]) / (ancho_masa * 1.15)) ** 2)) * (1 - prof * 0.45)
            pts.append((xx, yy + hund))
        dm.line(pts, fill=(150, 178, 225, alfa), width=2, joint="curve")
    for k in range(27):
        xx = w * k / 26
        pts = []
        for j in range(45):
            prof = j / 44
            yy = base_y + (h * 0.27) * prof ** 1.7
            hund = (h * 0.09) * math.exp(
                -(((xx - lente[0]) / (ancho_masa * 1.15)) ** 2)) * (1 - prof * 0.45)
            pts.append((xx, yy + hund))
        dm.line(pts, fill=(150, 178, 225, 36), width=2, joint="curve")
    fondo = Image.alpha_composite(fondo, malla)

    grande, chica = _font(34 * SS), _font(24 * SS)

    def galaxia_espiral(tam):
        """Galaxia con brazos, no una mancha: ruido en coordenadas polares."""
        from scipy.ndimage import gaussian_filter

        g = np.zeros((tam, tam), dtype=np.float32)
        yy, xx = np.mgrid[0:tam, 0:tam].astype(np.float32)
        cx = cy = tam / 2
        dx, dy = (xx - cx) / (tam / 2), (yy - cy) / (tam / 2)
        r = np.sqrt(dx ** 2 + dy ** 2) + 1e-6
        ang = np.arctan2(dy, dx)
        brazos = 0.5 + 0.5 * np.sin(2 * (ang + 2.6 * np.log(r + 0.05)))
        g = np.exp(-r * 2.6) * (0.35 + 0.65 * brazos)
        g += np.exp(-(r / 0.09) ** 2) * 1.5
        g = gaussian_filter(g, tam * 0.012)
        return np.clip(g / g.max(), 0, 1)

    tam_gal = int(210 * SS)
    gal = galaxia_espiral(tam_gal)
    gal_img = np.zeros((tam_gal, tam_gal, 4), dtype=np.uint8)
    gal_img[..., 0] = np.clip(gal * 255 * 1.00, 0, 255)
    gal_img[..., 1] = np.clip(gal * 255 * 0.93, 0, 255)
    gal_img[..., 2] = np.clip(gal * 255 * 0.80, 0, 255)
    gal_img[..., 3] = np.clip(gal * 255 * 1.15, 0, 255)
    galaxia = Image.fromarray(gal_img).resize(
        (tam_gal, int(tam_gal * 0.42)), Image.LANCZOS)

    eventos = [(0.35, "entra"), (0.9, "haz")]
    marcos = []

    for i in range(n):
        t = i / max(n - 1, 1)
        seg = i / fps
        avance = float(np.clip((seg - 0.9) / (seconds * 0.42), 0, 1))
        hasta = max(int(len(rayos[0]) * avance), 2)

        # Deriva de camara: el fondo se mueve menos que el primer plano.
        deriva = math.sin(t * math.pi * 0.7) * (w * 0.006)
        zoom = 1.0 + 0.022 * t

        capa = fondo.copy()

        haz = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dh = ImageDraw.Draw(haz)
        for rayo in rayos:
            dh.line(rayo[:hasta], fill=(115, 148, 255, 118),
                    width=14 * SS, joint="curve")
        haz = haz.filter(ImageFilter.GaussianBlur(10 * SS))
        capa = Image.alpha_composite(capa, haz)

        nucleo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dn = ImageDraw.Draw(nucleo)
        for rayo in rayos:
            dn.line(rayo[:hasta], fill=(255, 255, 255, 246),
                    width=2 * SS, joint="curve")

        # Pulsos de luz recorriendo el trazo ya dibujado.
        if avance > 0.02:
            for k in range(3):
                fase = (seg * 0.42 + k / 3.0) % 1.0
                if fase > avance:
                    continue
                idx = int(fase * (hasta - 1))
                for rayo in rayos:
                    px, py = rayo[idx]
                    rr = 7 * SS
                    dn.ellipse([px - rr, py - rr, px + rr, py + rr],
                               fill=(255, 255, 255, 235))
        nucleo_glow = nucleo.filter(ImageFilter.GaussianBlur(3 * SS))
        capa = Image.alpha_composite(capa, nucleo_glow)
        capa = Image.alpha_composite(capa, nucleo)

        cuerpos = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dc = ImageDraw.Draw(cuerpos)

        fx, fy = fuente
        latido = 1.0 + 0.06 * math.sin(seg * 3.1)
        for rr, a in ((30 * SS * latido, 55), (16 * SS, 125), (7 * SS, 255)):
            dc.ellipse([fx - rr, fy - rr, fx + rr, fy + rr],
                       fill=(255, 250, 235, a))
        for sgn in (-1, 1):
            dc.polygon([(fx, fy), (fx + sgn * 9 * SS, fy - 58 * SS),
                        (fx - sgn * 9 * SS, fy - 58 * SS)],
                       fill=(175, 200, 255, 66))
            dc.polygon([(fx, fy), (fx + sgn * 9 * SS, fy + 58 * SS),
                        (fx - sgn * 9 * SS, fy + 58 * SS)],
                       fill=(175, 200, 255, 66))

        ox, oy = ojo
        dc.polygon([(ox - 34 * SS, oy - 26 * SS), (ox + 8 * SS, oy - 13 * SS),
                    (ox + 8 * SS, oy + 13 * SS), (ox - 34 * SS, oy + 26 * SS)],
                   outline=(235, 240, 255, 240), width=3 * SS)
        dc.line([(ox + 8 * SS, oy), (ox + 30 * SS, oy)],
                fill=(235, 240, 255, 240), width=3 * SS)

        cuerpos_glow = cuerpos.filter(ImageFilter.GaussianBlur(6 * SS))
        capa = Image.alpha_composite(capa, cuerpos_glow)
        capa = Image.alpha_composite(capa, cuerpos)

        # La galaxia va encima, ya renderizada aparte.
        gx = int(lente[0] - galaxia.width / 2)
        gy = int(lente[1] - galaxia.height / 2)
        capa.alpha_composite(galaxia, (gx, gy))

        texto = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dt = ImageDraw.Draw(texto)

        def centrado(txt, cx, cy, f, alfa):
            caja = f.getbbox(txt)
            dt.text((cx - (caja[2] - caja[0]) / 2 - caja[0], cy), txt,
                    font=f, fill=(255, 255, 255, max(alfa, 0)))

        ent = int(255 * float(np.clip((seg - 0.35) / 0.5, 0, 1)))
        centrado("CUÁSAR", fx, fy + 50 * SS, grande, ent)
        centrado("GALAXIA", lente[0], lente[1] + 118 * SS, grande, ent)
        centrado("SU MASA CURVA EL ESPACIO", lente[0], lente[1] + 162 * SS,
                 chica, int(ent * 0.72))
        centrado("NOSOTROS", ox, oy + 50 * SS, grande, ent)
        capa = Image.alpha_composite(capa, texto)

        rgb = capa.convert("RGB")

        # Bloom: se aisla lo mas brillante, se difumina y se suma.
        arr = np.asarray(rgb).astype(np.float32)
        luz = np.clip((arr - 165) / 90.0, 0, 1)
        brillo = Image.fromarray((luz * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(11 * SS))
        rgb = ImageChops.add(rgb, brillo.point(lambda v: int(v * 0.42)))

        # Deriva y zoom, y recorte al encuadre final.
        nz = (int(w * zoom), int(h * zoom))
        rgb = rgb.resize(nz, Image.LANCZOS)
        cx = (nz[0] - w) / 2 + deriva
        cy = (nz[1] - h) / 2 - deriva * 0.35
        rgb = rgb.crop((int(cx), int(cy), int(cx) + w, int(cy) + h))

        marcos.append(np.asarray(rgb.resize((W, H), Image.LANCZOS)))

    eventos.append((0.9 + seconds * 0.42, "llega"))
    log.info("Renderizando diagrama de lente: %d fotogramas a %dx", n, SS)
    salida = _guardar(marcos, dest, fps)
    return (salida, eventos) if salida else None


# ---------------------------------------------------------------------------
# Saturno
# ---------------------------------------------------------------------------

# Radios del sistema de anillos, en radios ecuatoriales del planeta. Son los
# valores reales, y la División de Cassini entre 1,95 y 2,03 es lo que hace que
# el anillo se lea como Saturno y no como un disco cualquiera.
_ANILLOS = (
    # (interior, exterior, opacidad, brillo)
    (1.24, 1.53, 0.28, 0.55),   # anillo C, tenue
    (1.53, 1.95, 0.92, 1.00),   # anillo B, el denso y brillante
    (1.95, 2.03, 0.06, 0.40),   # División de Cassini: casi vacía
    (2.03, 2.27, 0.62, 0.86),   # anillo A
)

# Saturno está visiblemente achatado: 60.268 km de radio ecuatorial contra
# 54.364 en los polos. Dibujarlo redondo es el error que delata una ilustración.
_ACHATAMIENTO = 54364 / 60268


def saturno(dest: Path, seconds: float = 6.0, semilla: int = 0,
            inclinacion: float = 17.0, giro: float = 1.0) -> Path | None:
    """Saturno con sus anillos, girando, con las dos sombras que importan.

    La sombra del planeta sobre los anillos y la banda de sombra de los anillos
    sobre las nubes son la firma visual de Saturno: sin ellas el dibujo se lee
    como un icono, no como un mundo.

    `inclinacion` es la apertura del anillo vista desde la cámara, en grados.
    """
    import numpy as np
    from scipy.ndimage import gaussian_filter

    W, H = config.WIDTH, config.HEIGHT
    fps = config.FPS
    n = max(int(seconds * fps), 1)
    rng = np.random.default_rng(semilla)

    # --- textura de nubes: bandas en latitud, no ruido isótropo ------------
    tw, th = 1024, 512
    lat = np.linspace(-1.0, 1.0, th, dtype=np.float32)[:, None]
    # Cinturones y zonas alternos, más apretados hacia los polos.
    bandas = (0.5 + 0.5 * np.sin(lat * 11.0 + 0.6)) * 0.55 \
        + (0.5 + 0.5 * np.sin(lat * 26.0)) * 0.18
    turbulencia = np.zeros((th, tw), dtype=np.float32)
    for escala, peso in ((28, 1.0), (11, 0.45), (4, 0.2)):
        capa = gaussian_filter(rng.random((th, tw)).astype(np.float32), (escala * 0.35, escala))
        capa = (capa - capa.min()) / (capa.max() - capa.min() + 1e-9)
        turbulencia += capa * peso
    turbulencia /= turbulencia.max()
    # La turbulencia se estira en longitud: el viento zonal arrastra las nubes.
    nubes = np.clip(bandas + (turbulencia - 0.5) * 0.30, 0, 1).astype(np.float32)

    # Paleta de Saturno: crema, ocre y ámbar. Nada de gris.
    def tintar(v):
        return np.stack([
            0.52 + 0.46 * v,
            0.44 + 0.40 * v,
            0.30 + 0.28 * v,
        ], axis=-1).astype(np.float32)

    # --- geometría ---------------------------------------------------------
    incl = np.radians(inclinacion)
    sin_i, cos_i = np.sin(incl), np.cos(incl)
    Rp = H * 0.30                      # radio ecuatorial en píxeles
    cx, cy = W * 0.44, H * 0.54

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

    cielo = _cielo(W, H, semilla + 11)

    marcos = []
    for k in range(n):
        avance = k / max(n - 1, 1)
        img = cielo.copy()

        # La cámara se acerca un 14 % y deriva: es lo que convierte una
        # ilustración girando en un plano de documental. Sin esto el plano
        # medía 0,08 de movimiento percibido y el montador lo daba por muerto.
        acerca = 1.0 + 0.34 * avance
        Rp_k = Rp * acerca
        dx = xx - (cx + W * 0.07 * avance)
        dy = yy - (cy - H * 0.05 * avance)

        # Elipse del planeta (achatado).
        dentro_planeta = (dx / Rp_k) ** 2 + (dy / (Rp_k * _ACHATAMIENTO)) ** 2 <= 1.0

        # Punto del plano de los anillos que se proyecta en cada píxel.
        # sx = r·cosθ ; sy = r·sinθ·sin(i). Se invierte para sacar r y sinθ.
        seno_theta = np.where(np.abs(sin_i) > 1e-3, dy / (Rp_k * sin_i), 0.0)
        r_anillo = np.sqrt((dx / Rp_k) ** 2 + seno_theta ** 2)
        delante = dy > 0                   # mitad cercana del anillo

        # Máscara y color de los anillos, con un poco de grano radial.
        grano = gaussian_filter(rng.random((H, W)).astype(np.float32), 1.2)
        opacidad = np.zeros((H, W), dtype=np.float32)
        brillo_anillo = np.zeros((H, W), dtype=np.float32)
        for r0, r1, op, br in _ANILLOS:
            m = (r_anillo >= r0) & (r_anillo < r1)
            opacidad[m] = op * (0.82 + 0.36 * grano[m])
            brillo_anillo[m] = br
        # Los anillos se ven casi de canto cerca del borde: se afinan.
        opacidad *= 0.55 + 0.45 * np.abs(sin_i)

        # Sombra del planeta sobre los anillos. El Sol entra por arriba-izquierda.
        luz = np.array([-0.62, 0.50, 0.60], dtype=np.float32)
        luz /= np.linalg.norm(luz)
        # Punto 3D del anillo: (r·cosθ, 0, r·sinθ) con el eje Y como eje de giro.
        cos_theta = np.where(r_anillo > 1e-6, (dx / Rp_k) / np.maximum(r_anillo, 1e-6), 0.0)
        P = np.stack([r_anillo * cos_theta,
                      np.zeros_like(r_anillo),
                      r_anillo * seno_theta / np.maximum(r_anillo, 1e-6) * r_anillo], axis=-1)
        t = (P * luz).sum(axis=-1)
        perp = P - t[..., None] * luz
        dist = np.sqrt((perp ** 2).sum(axis=-1))
        sombra_anillo = (dist < 1.0) & (t < 0)
        opacidad = np.where(sombra_anillo, opacidad * 0.16, opacidad)


            # --- anillos de detrás ---------------------------------------------
        atras = (~delante) & (opacidad > 0) & (~dentro_planeta)
        col = brillo_anillo[..., None] * np.array([1.0, 0.93, 0.80], dtype=np.float32)
        a = (opacidad * atras)[..., None]
        img = img * (1 - a) + col * a

        # --- el planeta ------------------------------------------------------
        # Coordenadas esféricas de la cara visible.
        nx = dx / Rp
        ny = dy / (Rp * _ACHATAMIENTO)
        r2 = np.clip(1.0 - nx ** 2 - ny ** 2, 0, None)
        nz = np.sqrt(r2)
        lat_p = np.arcsin(np.clip(ny, -1, 1))
        lon_p = np.arctan2(nx, np.maximum(nz, 1e-6))
        # El giro desplaza la longitud: la textura rueda sobre la esfera.
        u = ((lon_p / (2 * np.pi) + avance * giro * 0.45) % 1.0) * (tw - 1)
        v = ((lat_p / np.pi + 0.5) * (th - 1))
        muestra = nubes[np.clip(v.astype(np.int32), 0, th - 1),
                        np.clip(u.astype(np.int32), 0, tw - 1)]
        superficie = tintar(muestra)

        # Iluminación difusa con el terminador donde toca.
        normal = np.stack([nx, ny, nz], axis=-1)
        difusa = np.clip((normal * luz).sum(axis=-1), 0, 1) ** 0.75
        superficie *= (0.10 + 0.95 * difusa)[..., None]

        # Banda de sombra de los anillos sobre las nubes: se busca dónde el rayo
        # de sol que llega a cada punto cruza el plano del anillo.
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.where(np.abs(luz[1]) > 1e-6, -ny / luz[1], 0.0)
        cxr = nx + s * luz[0]
        czr = nz + s * luz[2]
        r_cruce = np.sqrt(cxr ** 2 + czr ** 2)
        tapado = np.zeros_like(r_cruce, dtype=bool)
        for r0, r1, op, _br in _ANILLOS:
            if op < 0.2:
                continue
            tapado |= (r_cruce >= r0) & (r_cruce < r1) & (s > 0)
        superficie = np.where(tapado[..., None], superficie * 0.42, superficie)

        # Borde de atmósfera: un filo claro en el limbo.
        limbo = np.clip(1.0 - nz, 0, 1) ** 3
        superficie += (limbo * difusa)[..., None] * np.array([0.30, 0.26, 0.18], dtype=np.float32)

        img = np.where(dentro_planeta[..., None], superficie, img)

        # --- anillos de delante ---------------------------------------------
        a = (opacidad * delante * (opacidad > 0))[..., None]
        img = img * (1 - a) + col * a

        # Deriva de cámara muy lenta, para que el plano no esté clavado.
        marcos.append(np.clip(img * 255, 0, 255).astype(np.uint8))

    return _guardar(marcos, dest, fps)
