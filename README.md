# Éter — publicación diaria automatizada

Pipeline que escribe, narra, monta y publica un documental espacial al día en
[youtube.com/@Éter](https://www.youtube.com/channel/UCwj7Ry45KcedtaIcpSGeN-A),
manteniendo la voz, el ritmo y el aspecto de los vídeos ya publicados.

Una ejecución produce: guion de ~2.350 palabras, locución de 14 minutos,
montaje 1080p con material de archivo, miniatura con la plantilla del canal,
subtítulos en español, título, descripción y etiquetas, y lo deja todo
listo para descargar.

```
tema  →  guion  →  locución  →  planos  →  montaje  →  miniatura  →  artefacto
```

---

## Arranque rápido

```bash
pip install -r requirements.txt
cp .env.example .env        # y rellena las claves
python scripts/smoke_test.py
```

`smoke_test.py` produce un vídeo de dos minutos con un guion ya escrito. Solo
necesita `AI33_API_KEY` y sirve para comprobar que la cadena entera funciona
antes de gastar en un vídeo completo. El resultado queda en `build/smoke-test/`.

Cuando eso funcione:

```bash
python -m pipeline.run --minutes 4 --dry-run    # vídeo corto, sin publicar
python -m pipeline.run                          # el ciclo completo
```

## Claves

| Variable | Para qué | Coste |
|---|---|---|
| `ANTHROPIC_API_KEY` | Escribir el guion | ~0,40 $/vídeo |
| `AI33_API_KEY` | Voz de Javier vía ai33.pro | ~15.400 créditos/vídeo |
| `PEXELS_API_KEY` | Clips de stock | Gratis |
| `PIXABAY_API_KEY` | Clips de stock | Gratis |
| `YT_CLIENT_ID` / `YT_CLIENT_SECRET` / `YT_REFRESH_TOKEN` | Subida, **opcional** | Gratis |

Las dos primeras son obligatorias. Las tres de YouTube solo hacen falta si
quieres que suba solo; por defecto el cron no sube y el vídeo se descarga del
artefacto. Pexels y Pixabay son
opcionales pero **muy** recomendables: sin ellas el pipeline se queda sin
material en movimiento y tira solo del archivo fotográfico de la NASA. Se sacan
en dos minutos en [pexels.com/api](https://www.pexels.com/api/) y
[pixabay.com/api/docs](https://pixabay.com/api/docs/).

Para las de YouTube: `python scripts/get_youtube_token.py client_secret.json`
lleva las instrucciones completas en su cabecera.

En GitHub, los mismos nombres van en **Settings → Secrets and variables →
Actions**.

## Cómo se mantiene la calidad

El canal tiene una voz muy definida y el riesgo real de automatizar es diluirla.
Tres mecanismos la sostienen:

**`brand/voice_guide.md`** es el contrato. Estructura en cinco tiempos, reglas
de frase, prohibiciones explícitas, plantilla de miniatura y de descripción.
Está destilado del guion real de un vídeo publicado y se inyecta entero en cada
generación. Si quieres cambiar el tono del canal, se cambia ahí y en ningún
otro sitio.

**`brand/reference_script.txt`** es la transcripción completa de *Por Qué Es Tan
Difícil Viajar A Los Planetas Del Sistema Solar*, 2.485 palabras. Va como
ejemplo canónico en el prompt: el modelo tiene delante el objetivo, no una
descripción del objetivo.

**Dos llamadas separadas.** El guion se escribe primero como prosa corrida, sin
JSON ni estructura. Solo después, en una segunda llamada, se trocea en escenas y
se deciden los planos y los metadatos. Pedir las dos cosas a la vez degrada
notablemente la escritura.

`content/published.json` guarda todo lo emitido y se le pasa al modelo como
lista de lo que no debe repetir. Ese fichero lo commitea el propio workflow, así
que el canal tiene memoria aunque cada ejecución arranque en un runner limpio.

## De dónde sale la imagen

La base son **clips de vídeo**, nunca imágenes fijas, y ningún plano pasa de
seis segundos. Un vídeo de veinte minutos son 272 planos. Cada uno lleva dos
búsquedas y se enruta según cuál aplique:

**Específico** — un objeto con nombre propio: Encélado, la Europa Clipper.
Va a los archivos científicos, los únicos que tienen la cosa concreta.

**Genérico** — lo que se ve, sin nombrarlo: «cracked ice texture», «accretion
disk». Va a los bancos de stock.

Nunca al revés. Mandar un nombre propio a un banco es lo peor posible porque no
devuelve cero: devuelve lo que más se le parece por letras. Medido con las
claves reales, «great red spot» devuelve un pájaro carpintero y «kinman dwarf»
un hámster.

La cascada, de mejor a peor:

1. **`library/`** — tus propios clips. Prioridad absoluta. Ver
   [library/README.md](library/README.md).
2. **Pexels** y **Pixabay** — el grueso del material. Unos 190 clips por vídeo.
3. **NASA SVS**, solo lo etiquetado `Animation`. Fuente de precisión, no de
   volumen.
4. **Vídeo general de la NASA** — red de seguridad.
5. **Procedural** — campo de estrellas. El render nunca se cae.

Con `CLIPS_ONLY` activado (por defecto) el archivo fotográfico no entra: una
imagen fija con movimiento de cámara se distingue de un clip en cuanto se ve al
lado de uno.

### Los filtros, y por qué son los que son

Cada uno existe porque algo se coló en una producción real.

**`_is_space_clip`** cruza la descripción del clip contra tres listas: espacio,
texturas terrestres que sirven de análogo, y exclusiones. Las exclusiones se
levantan para lo que pide la propia búsqueda: un bosque no pinta nada en un
documental espacial, pero si el guion habla del fin de la fotosíntesis y pides
«forest canopy», el bosque es justo lo que hace falta.

**`_svs` restringido a `Animation`.** De 227 resultados del SVS en siete
búsquedas reales, solo 7 son de ese tipo, y son los únicos limpios. Lo
etiquetado `Visualization` es mayoritariamente producto de datos —«Greenland
Ice Mass Loss 2002-2025», «Map of the Eclipse»— con leyenda y fecha quemadas
por definición.

**`inspect_media`** muestrea un fotograma cada tres segundos y marca los tramos
utilizables, porque una misma pieza puede tener cuarenta segundos de imagen
buena y quince de gráficas. Ojo con su límite, que está medido: **no detecta
rótulos pequeños y grises**. Puntúa cero en ellos incluso analizando a 640×360,
mientras que fotogramas limpios puntúan hasta 10. Para eso sirve el filtro por
título del SVS, no este.

**Tope de reutilización.** Ninguna fuente puede cubrir más del 22 % de los
planos. Sin él, en cuanto se agotan las fuentes de una búsqueda el primer clip
disponible llena medio vídeo.

### Sobre generar clips con IA

`library/` existe precisamente para eso. Google Labs / Flow (Veo) no tiene API
pública: es interfaz web con sesión de Google iniciada, y automatizarla desde un
cron exigiría conducir un navegador con tus credenciales — frágil y contra sus
condiciones. Genera los clips a mano cuando te apetezca, déjalos en `library/`
con nombres descriptivos en inglés, y el pipeline los usará antes que cualquier
otra fuente.

La generación por API de ai33 está implementada y **desactivada**: sus tareas de
imagen terminan con `status: done`, cobran los créditos y devuelven
`result_images` vacío; comprobado con tres modelos y de nuevo dos horas después.
El endpoint de vídeo responde 401 a las claves de API. Para la miniatura hay un
adaptador de OpenAI en `pipeline/imagegen.py`, sin verificar por no tener clave.

## Estructura

```
pipeline/
  config.py        constantes y secretos
  topics.py        cola de temas, historial, reabastecimiento
  script_gen.py    guion y plan de producción
  voice.py         locución por escena, mezcla, subtítulos
  visuals.py       búsqueda y descarga del material
  inspect_media.py control de calidad: qué tramos de un clip valen
  shots.py         troceado en planos de 3-6 s y ritmo de montaje
  sfx.py           efectos de transición y dónde caen
  music.py         lecho musical
  assemble.py      montaje con fundidos encadenados
  thumbnail.py     plantilla de miniatura del canal
  imagegen.py      imagen de miniatura por IA (sin verificar)
  youtube.py       subida
  run.py           orquestador
scripts/
  from_script.py   producir desde un guion tuyo en Markdown
  smoke_test.py    prueba de humo de 2 min, solo necesita AI33_API_KEY
  get_youtube_token.py
brand/
  voice_guide.md       el contrato de voz
  reference_script.txt guion real de referencia
  music/               lechos musicales opcionales
content/
  topics.yml     cola de 30 temas
  plans/         planes visuales de guiones escritos a mano
  published.json historial
library/         tus propios clips
```

### Sincronía entre voz e imagen

Cada plano se genera con longitud `duración_escena + pausa + fundido` y se
encadena con `xfade` en el desplazamiento acumulado exacto. El corte de plano
cae justo donde arranca la narración de su escena, y el fundido entra sobre la
pausa entre escenas, nunca sobre una palabra.

## Puesta en marcha en GitHub

Está en [docs/despliegue.md](docs/despliegue.md): la cuenta de minutos, los
secretos, la primera ejecución en seco y el espacio en disco. Léelo antes de
activar el cron — la decisión de repositorio público o privado cambia la
factura de unos 30 $ al mes a cero.

## El workflow

`.github/workflows/daily.yml` arranca a las 6:00 de Madrid. Instala ffmpeg,
produce el vídeo, marca el tema como hecho y commitea `content/published.json`.
Guarda el MP4, la miniatura, la descripción, los subtítulos y el guion como
artefacto durante 30 días. **No sube a YouTube**: el vídeo se descarga y se
sube a mano. Para que suba solo, añade los secretos `YT_*` y lanza a mano con
la casilla «Subir a YouTube».

Tres detalles del cron que costaron dos días de silencio:

- **Nunca en la hora en punto.** Es cuando GitHub tiene más cola y lo primero
  que descarta. Medido aquí: un cron a las 15:00 se ejecutó a las 17:04.
- **UTC sin horario de verano.** Madrid es UTC+1 en invierno y UTC+2 en verano,
  así que se disparan las dos horas posibles y un guardián corta la que sobra.
- **El guardián acepta una ventana, no una hora exacta.** Comparar la hora justa
  hacía que cualquier disparo retrasado se tirase a la basura. Lo que evita el
  vídeo duplicado es el historial, no el reloj.

Medido sobre las producciones reales en la nube: **95 minutos por vídeo**, así
que arrancando a las 6 está listo sobre las 7:35. En repositorio público los
minutos son gratis; en privado un vídeo diario se pasa del cupo hacia el día
doce y cuesta unos 30 $ al mes.

## Coste y cuotas

Por vídeo: ~0,40 $ de guion y unos 15.400 créditos de ai33. Con 1,9 millones de
créditos hay para unos 120 vídeos de locución. Pexels, Pixabay, NASA y los
minutos de Actions en repositorio público no cuestan nada.

Si activas la subida automática: la cuota diaria de la YouTube Data API es de
10.000 unidades, una subida gasta 1.600 y la miniatura 50, así que un vídeo al
día cabe con enorme margen.

## Sobre publicar a diario

Conviene decirlo claro: el canal venía de un vídeo semanal y pasar a uno diario
automatizado es exactamente el perfil que la política de contenido repetitivo de
YouTube (julio de 2025) vigila, con la monetización en juego. El pipeline
mitiga lo que puede — cada vídeo parte de una anomalía distinta, con estructura,
material y duración propios— pero el riesgo no es cero.

Por eso el ajuste por defecto es el más conservador: **el cron no publica**.
Produce el vídeo y lo deja en el artefacto, y la decisión de subirlo es tuya
cada mañana. Si prefieres que suba solo pero sin exponerlo, añade los secretos
`YT_*` y deja `ETER_PRIVACY=private`: aparece cada día en el canal en privado y
tú eliges cuándo hacerlo público.
