# Biblioteca de clips propios

Todo lo que dejes aquí tiene **prioridad sobre cualquier otra fuente**. Es el
sitio donde entran en el pipeline los clips que generes a mano en Google
Labs / Flow (Veo), Runway, Kling, Hailuo o donde sea, además de cualquier
metraje que compres o grabes.

## Por qué a mano y no por API

Google Labs / Flow no tiene API pública: es una interfaz web que exige una
sesión de Google iniciada. Un cron de GitHub Actions no puede usarla sin
automatizar un navegador con tus credenciales, que se rompe en cuanto cambian
la web y además va contra sus condiciones de uso. Esta carpeta es la manera
sólida de meter ese material en un pipeline automático: tú generas cuando te
apetece, el pipeline consume solo.

## Cómo nombrar los ficheros

El indexado sale del **nombre del fichero y de las carpetas que lo contienen**.
Todas las palabras cuentan como etiquetas, y se comparan con la `visual_query`
en inglés que el guionista asigna a cada escena. **Nombra en inglés.**

```
library/
  saturn-rings-closeup_01.mp4        → saturn, rings, closeup
  black-hole/accretion-disk_02.mp4   → black, hole, accretion, disk
  earth/aurora-from-orbit.mp4        → earth, aurora, orbit
```

Los sufijos numéricos (`_01`, `_02`) se ignoran, así que puedes tener varias
variantes del mismo concepto y el pipeline irá alternando: dentro de un mismo
vídeo nunca repite un clip.

## Requisitos técnicos

- Contenedor `.mp4`, `.mov`, `.webm` o `.m4v`.
- 1920×1080 o más. Se reencuadra a 16:9 automáticamente.
- Mínimo 3 segundos. Si es más corto que la escena, se repite en bucle con un
  empuje de zoom que disimula el salto.
- Sin audio, sin marca de agua, sin texto en pantalla.
- El audio del clip se descarta siempre: manda la locución.

## Rendimiento

Cuantos más clips buenos haya aquí, menos depende el canal de bancos de
terceros y más propio se ve. Con unos 150-200 clips bien etiquetados cubriendo
los objetos habituales (planetas, lunas, agujeros negros, nebulosas, sondas,
superficies, estrellas) prácticamente todos los vídeos se montan solo con
material tuyo.

Los ficheros de vídeo están en `.gitignore`. Si quieres que el runner de GitHub
los vea, tienes dos opciones: subirlos con `git lfs track "library/**/*.mp4"`,
o alojarlos en almacenamiento externo y sincronizarlos en el workflow antes del
render.

## De dónde sacar metraje bueno y legal

Por orden de calidad para este canal:

**Dominio público — uso comercial libre, sin atribución obligatoria**

- NASA SVS: `svs.gsfc.nasa.gov` — visualizaciones científicas puras, sin
  rótulos. Muchas duran entre 30 y 90 s, así que de un activo salen diez o
  quince planos. El pipeline ya las busca solo.
- Canales de YouTube de NASA, NASA Goddard y NASA JPL.
- `images.nasa.gov` — la biblioteca general, con vídeo.

**Creative Commons — comprueba la licencia de cada pieza**

- ESA / Hubble (`esahubble.org`), ESO (`eso.org`), Webb (`webbtelescope.org`).
- En YouTube: Buscar -> Filtros -> Licencia -> **Creative Commons**.

**Lo que NO vale**

Metraje con copyright de otros canales, por bueno que sea. Un canal que
monetiza con material ajeno se come strikes y desmonetización: es el riesgo
más caro del proyecto, y no lo compensa ningún plano.

## Requisitos técnicos

- MP4, MOV, WEBM o M4V; 1080p o más.
- Que **se mueva**: el filtro de movimiento percibido descarta lo que esté
  casi quieto, venga de donde venga.
- Sin rótulos, sin logotipos, sin presentador a cámara.
