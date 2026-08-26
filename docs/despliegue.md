# Puesta en marcha en GitHub

Guía para pasar de producir en local a producir en la nube. Los números salen
de la primera producción real, «Qué Pasaría Si El Sol Se Apagara De Repente»:
20 minutos, 34 escenas, 272 planos.

---

## 1. Antes de nada: la cuenta de minutos

Es lo primero que hay que decidir porque condiciona todo lo demás.

Un vídeo de veinte minutos tardó **75 minutos** en producirse en un equipo de
16 núcleos. Los runners de GitHub tienen **4**. Las descargas irán más rápidas
allí que en una conexión doméstica, pero el renderizado de los planos y el
montaje son trabajo de CPU y no escalan solos.

Estimación realista: **2 horas y media a 3 horas por vídeo**. El límite por
trabajo son 6 horas, así que cabe, pero con menos margen del que parece.

Y de ahí sale la factura:

| Repositorio | Coste |
|---|---|
| **Público** | Gratis, minutos ilimitados. La cuota no le aplica |
| **Privado** | 2.000 min/mes en el plan Free. Un vídeo diario gasta ~5.400 |

Y un detalle que se pasa por alto: **esos 2.000 minutos son por CUENTA, no por
repositorio**. Se reparten entre todos tus repos privados, así que si ya tienes
otros con Actions, la bolsa es la misma y el margen es menor.

Con repositorio privado te pasarías del cupo hacia el día doce de cada mes. El
exceso se factura a unos 0,008 $/minuto en Linux, así que rondaría los **30 $ al
mes** solo de cómputo. Los multiplicadores de otros sistemas son brutales
—Windows cuenta doble y macOS diez veces— pero aquí solo se usa Linux.

El consumo real está en `github.com/settings/billing`.

Nada de lo que hay en el repositorio es secreto —las claves van en Secrets, no
en el código—, así que **público es la opción sensata** salvo que prefieras no
enseñar los guiones. Si eliges privado, cuenta con ese gasto.

Una tercera vía: un runner propio (`self-hosted`) en tu equipo o en un VPS.
Sale gratis en minutos y va más rápido, pero tienes que mantenerlo encendido.

---

## 2. Crear el repositorio

```bash
gh repo create eter --public --source=. --remote=origin --push
```

O a mano en github.com y luego:

```bash
git remote add origin https://github.com/TU_USUARIO/eter.git
git push -u origin main
```

Comprueba antes que no sube nada que no deba:

```bash
git status --short && git ls-files | grep -E "^\.env$|client_secret" || echo "limpio"
```

`.env`, `build*/` y `.cache/` están en `.gitignore`.

---

## 3. Los secretos

En **Settings → Secrets and variables → Actions → New repository secret**:

| Secreto | Para qué | Sin él |
|---|---|---|
| `ANTHROPIC_API_KEY` | Escribir el guion | **No arranca** |
| `AI33_API_KEY` | Voz, música y efectos | **No arranca** |
| `YT_CLIENT_ID` | Subida | No publica |
| `YT_CLIENT_SECRET` | Subida | No publica |
| `YT_REFRESH_TOKEN` | Subida | No publica |
| `PEXELS_API_KEY` | Clips | Se queda casi sin material |
| `PIXABAY_API_KEY` | Clips | Menos variedad |
| `OPENAI_API_KEY` | Imagen de la miniatura | Usa un fotograma del vídeo |

Las tres de YouTube salen de `python scripts/get_youtube_token.py`, que lleva
las instrucciones en su cabecera.

---

## 4. Antes de nada, la comprobación

```bash
python scripts/check.py
```

Treinta segundos, sin tocar la red ni gastar créditos. Verifica sintaxis,
importaciones, la cola de temas, los planes visuales, los filtros de material y
el propio workflow, y te dice qué secretos faltan. Todo lo que comprueba ha roto
una ejecución real en algún momento.

En GitHub se ejecuta sola en cada push (`.github/workflows/check.yml`), así que
si rompes algo te enteras en medio minuto y no tres horas después.

## 5. La primera ejecución, en seco

No dejes que el primer intento publique. En la pestaña **Actions →
Publicación diaria → Run workflow**, marca **«Renderizar sin publicar»**.

Eso produce el vídeo, la miniatura, los subtítulos y la descripción, y los deja
como artefacto durante catorce días sin tocar el canal. Descárgalo, míralo, y
solo entonces quita la marca.

Si prefieres una red de seguridad permanente, pon el secreto de entorno
`ETER_PRIVACY=private`: sube el vídeo cada día listo y en privado, y tú decides
cuándo hacerlo público.

---

## 6. Espacio en disco

Un runner tiene unos 14 GB libres y una producción de veinte minutos ocupa
**6,7 GB** si no se limpia nada:

| Carpeta | Tamaño |
|---|---|
| `raw` (material descargado) | 5,1 GB |
| `shots` (272 planos sueltos) | 570 MB |
| `scenes` | 515 MB |
| `audio` (WAV sin comprimir) | 454 MB |
| vídeo final | ~500 MB |

`ETER_PRUNE` borra cada etapa en cuanto deja de hacer falta y lo deja en torno
a **1 GB**. Se activa solo cuando detecta la variable `CI`, así que en la nube
ya está puesto y en local no, que ahí interesa poder relanzar sin volver a
descargar.

---

## 7. Qué esperar del primer vídeo automático

Lo que el pipeline hace bien, medido sobre la producción real:

- 272 planos, ninguno de más de 6 segundos
- 168 materiales distintos; el más repetido cubre 5 planos
- Cero imágenes fijas y cero planos procedurales: todo clips
- 85 productos de datos de la NASA descartados por llevar rótulos

Lo que conviene que revises a mano las primeras veces:

- **Que el guion suene a Éter.** Es lo único que no he podido verificar nunca,
  porque nunca ha habido clave de Anthropic. Usa `--script-only` antes de
  producir: escribe el guion y para, sin gastar en voz ni en render.
- **La relevancia de algún plano.** Los bancos de stock nunca devuelven cero:
  si no tienen lo que pides devuelven lo que más se parece por letras. Hay tres
  filtros para eso, pero no son infalibles.
- **La miniatura**, mientras no haya clave de OpenAI. La plantilla tipográfica
  reproduce la del canal al punto; la imagen de fondo, no.

---

## 8. Guiones escritos por ti

El cron usa la cola de `content/topics.yml`. Para un guion tuyo:

```bash
python scripts/from_script.py guion.md --scenes
```

Eso lista las escenas numeradas. Escribes su plan visual en
`content/plans/`, copiando el de `sol_se_apaga.yml`, y produces:

```bash
python scripts/from_script.py guion.md --plan content/plans/tu_plan.yml
```
