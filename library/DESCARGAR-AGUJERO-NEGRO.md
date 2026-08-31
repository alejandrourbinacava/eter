# Material para «La Salida Que NO Existe Dentro De Un Agujero Negro»

Trece clips, todos **Creative Commons** de instituciones científicas: ESO,
HubbleWebbESA, LIGO Caltech/MIT. Resolución y año verificados uno a uno con
`yt-dlp`, no copiados de la ficha de YouTube.

Descarga cada uno y guárdalo en `library/` con **el nombre exacto** de la
última columna, respetando las subcarpetas. El montaje los busca por esas
palabras y los usa **antes que cualquier banco de stock**.

## 4K

| Vídeo | Fuente | Año | Dur. | Guardar como |
|---|---|---|---|---|
| [Stars orbiting the black hole at the heart of the Milky Way](https://youtu.be/TF8THY5spmo) | ESO | 2018 | 0:20 | `black-hole/stars-orbiting-galactic-center.mp4` |
| [Strong Gravitational lensing](https://youtu.be/Rsx0AGQhQvs) | HubbleWebbESA | 2017 | 0:21 | `lensing/strong-gravitational-lensing-render.mp4` |
| [Gravitational lensing of distant quasar](https://youtu.be/tUOJ0mfHGWw) | HubbleWebbESA | 2019 | 0:21 | `lensing/quasar-gravitational-lensing.mp4` |
| [Neutron star merger ending with kilonova](https://youtu.be/bBCArmUPgCw) | HubbleWebbESA | 2017 | 0:49 | `stellar-collapse/neutron-star-merger-kilonova.mp4` |
| [Zoom of the Milky Way's central region](https://youtu.be/6uVNBGgHApU) | ESO | 2019 | 0:51 | `milky-way/zoom-galactic-center-region.mp4` |
| [Pan video: M88](https://youtu.be/H3KNj8GYeVk) | HubbleWebbESA | 2026 | 0:30 | `galaxy/spiral-galaxy-m88-pan.mp4` |
| [Webb + Hubble Ultra Deep Field](https://youtu.be/Bf6A-FNW2xY) | HubbleWebbESA | 2025 | 0:30 | `deep-field/hubble-ultra-deep-field-webb.mp4` |

## 1080p

| Vídeo | Fuente | Año | Dur. | Guardar como |
|---|---|---|---|---|
| [Simulation of a Supermassive Black Hole](https://youtu.be/3NeIVjfuKQY) | ESO | 2019 | 0:54 | `black-hole/supermassive-black-hole-simulation.mp4` |
| [Material orbiting close to a black hole](https://youtu.be/Zmdcew3g9ME) | ESO | 2018 | 0:16 | `black-hole/material-orbiting-accretion-disk.mp4` |
| [Warped Space and Time Around Colliding Black Holes](https://youtu.be/1agm33iEAuo) | LIGO Caltech/MIT | 2016 | 1:14 | `spacetime/warped-spacetime-colliding.mp4` |
| [Two Black Holes Merge into One](https://youtu.be/I_88S8DWbcU) | LIGO Caltech/MIT | 2016 | 0:35 | `black-hole/two-black-holes-merging.mp4` |
| [Gravitational Lensing: White Dwarf](https://youtu.be/wy_ITGoD1vY) | HubbleWebbESA | 2023 | 0:16 | `lensing/white-dwarf-lensing-event.mp4` |
| [Event Horizon Telescope infrastructure](https://youtu.be/ljUixb41cvo) | ESO | 2019 | 1:18 | `event-horizon/event-horizon-telescope.mp4` |

**Descartado:** *A close look at the spiral galaxy NGC 1637* (ESO, 2013) —
solo 720p.

## Cómo descargarlos

Con `yt-dlp`, que ya está instalado en tu equipo, pidiendo la mejor calidad:

    yt-dlp -f "bv*[height<=2160]+ba/b" -o "library/black-hole/stars-orbiting-galactic-center.%(ext)s" https://youtu.be/TF8THY5spmo

O con cualquier descargador web pegando el enlace, y renombrando después.

## Dónde encontrar más

En YouTube: **Filtros → Licencia → Creative Commons**, y quédate con estos
canales, que son los que dan CGI limpio sin rótulos ni presentador:

`European Southern Observatory (ESO)` · `HubbleWebbESA` ·
`LIGO Lab Caltech : MIT` · `NASA Goddard` · `NASA JPL`

Y sin pasar por YouTube, en descarga directa y a menudo en 4K:
`svs.gsfc.nasa.gov` · `eso.org/public/videos` · `esahubble.org/videos`
