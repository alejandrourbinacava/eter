# Éter — publicación diaria automatizada

Pipeline que escribe, narra, monta y publica un documental espacial al día en
[youtube.com/@Éter](https://www.youtube.com/channel/UCwj7Ry45KcedtaIcpSGeN-A),
manteniendo la voz, el ritmo y el aspecto de los vídeos ya publicados.

Una ejecución produce: guion de ~2.350 palabras, locución de 14 minutos,
montaje 1080p con material de archivo, miniatura con la plantilla del canal,
subtítulos en español, título, descripción y etiquetas — y lo sube.

```
tema  →  guion  →  locución  →  planos  →  montaje  →  miniatura  →  YouTube
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
| `YT_CLIENT_ID` / `YT_CLIENT_SECRET` / `YT_REFRESH_TOKEN` | Subida | Gratis |

Las dos primeras y las tres de YouTube son obligatorias. Pexels y Pixabay son
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

Cascada, de mejor a peor, en `pipeline/visuals.py`:

1. **`library/`** — tus propios clips. Prioridad absoluta. Ver
   [library/README.md](library/README.md).
2. **Pexels** y **Pixabay** — vídeo de stock, licencia libre comercial.
3. **Archivo fotográfico de la NASA** — Hubble, JWST, Cassini, JPL. Se convierte
   en plano con movimiento lento (cuatro trayectorias que se alternan).
4. **Vídeo de la NASA** — red de seguridad, con filtro estricto.
5. **Procedural** — campo de estrellas. El render nunca se cae por falta de
   material.

Dos filtros hacen casi todo el trabajo de calidad. Uno descarta por metadatos
las piezas de comunicación del archivo (ruedas de prensa, cabeceras, salas de
control). Otro, `_looks_like_space()`, mira el histograma y descarta láminas
científicas y esquemas: una foto astronómica real es casi toda negra, una figura
con rótulos tiene fondo claro. Sin ese segundo filtro se colaban láminas con
texto quemado, que es lo que delata al instante un montaje automático.

### Sobre generar clips con IA

`library/` existe precisamente para eso. Google Labs / Flow (Veo) no tiene API
pública: es interfaz web con sesión de Google iniciada, y automatizarla desde un
cron exigiría conducir un navegador con tus credenciales — frágil y contra sus
condiciones. Genera los clips a mano cuando te apetezca, déjalos en `library/`
con nombres descriptivos en inglés, y el pipeline los usará antes que cualquier
otra fuente.

La generación de imagen por API de ai33 está implementada (`ai33.generate_image`)
pero **desactivada**: a fecha de 24/08/2026 las tareas terminan con `status:
done`, cobran los créditos y devuelven `result_images` vacío en los tres modelos
probados. El endpoint de vídeo `/veo3/task/generate-video` existe en la web pero
responde 401 a las claves de API. Si ai33 lo arregla, se activa poniendo
`USE_AI_IMAGES = True` en `pipeline/visuals.py`.

## Estructura

```
pipeline/
  config.py      constantes y secretos
  topics.py      cola de temas, historial, reabastecimiento
  script_gen.py  guion y plan de producción
  voice.py       locución por escena, mezcla, subtítulos
  visuals.py     cascada de material y conversión a plano
  assemble.py    montaje con fundidos encadenados
  thumbnail.py   plantilla de miniatura del canal
  youtube.py     subida
  run.py         orquestador
brand/
  voice_guide.md      el contrato de voz
  reference_script.txt guion real de referencia
  music/              lechos musicales opcionales (los pones tú)
content/
  topics.yml     cola de temas
  published.json historial
library/         tus propios clips
```

### Sincronía entre voz e imagen

Cada plano se genera con longitud `duración_escena + pausa + fundido` y se
encadena con `xfade` en el desplazamiento acumulado exacto. El corte de plano
cae justo donde arranca la narración de su escena, y el fundido entra sobre la
pausa entre escenas, nunca sobre una palabra.

## El workflow

`.github/workflows/daily.yml` se dispara a las 15:00 UTC. Instala ffmpeg,
produce el vídeo, lo sube, y commitea `content/published.json` con lo publicado.
Guarda guion, miniatura, descripción y subtítulos como artefacto durante 14
días; el MP4 no, que ya está en YouTube.

También se puede lanzar a mano desde la pestaña Actions, eligiendo visibilidad y
duración. Para probar sin publicar, marca «Renderizar sin publicar».

Un render de 14 minutos tarda unos 25-40 minutos de runner. En repositorio
público los minutos son gratis; en privado consume del cupo mensual.

## Coste y cuotas

Por vídeo: ~0,40 $ de guion y unos 15.400 créditos de ai33. Con 1,9 millones de
créditos hay para unos 120 vídeos de locución. Pexels, Pixabay, NASA y la subida
a YouTube no cuestan nada.

La cuota diaria de la YouTube Data API es de 10.000 unidades. Una subida gasta
1.600 y la miniatura 50, así que un vídeo al día cabe con enorme margen.

## Sobre publicar a diario

Conviene decirlo claro: el canal venía de un vídeo semanal y pasar a uno diario
automatizado es exactamente el perfil que la política de contenido repetitivo de
YouTube (julio de 2025) vigila, con la monetización en juego. El pipeline
mitiga lo que puede — cada vídeo parte de una anomalía distinta, con estructura,
material y duración propios— pero el riesgo no es cero.

Si prefieres control humano sin perder la automatización, pon
`ETER_PRIVACY=private`: el vídeo se sube listo cada día y tú decides cuándo
hacerlo público.
