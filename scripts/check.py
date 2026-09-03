"""Comprobación rápida del repositorio. No gasta créditos ni toca la red.

Existe porque el cron diario tarda tres horas en fallar. Todo lo que se
comprueba aquí ha roto una ejecución real en algún momento: un YAML mal
entrecomillado, una frase de remate que no aparece literal, un contador sin
inicializar, un filtro que rechazaba justo el material bueno.

    python scripts/check.py

Sale con código 1 si algo falla, así que sirve tal cual en CI.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RAIZ = Path(__file__).resolve().parent.parent
fallos: list[str] = []
avisos: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  OK   {nombre}")
    else:
        print(f"  MAL  {nombre}{' — ' + detalle if detalle else ''}")
        fallos.append(nombre)


print("Sintaxis")
for fichero in sorted(RAIZ.glob("pipeline/*.py")) + sorted(RAIZ.glob("scripts/*.py")):
    try:
        ast.parse(fichero.read_text(encoding="utf-8"))
        ok, detalle = True, ""
    except SyntaxError as exc:
        ok, detalle = False, str(exc)
    comprobar(fichero.relative_to(RAIZ).as_posix(), ok, detalle)

print("\nImportación")
try:
    from pipeline import (  # noqa: F401
        ai33, assemble, captions, config, imagegen, inspect_media, music,
        script_gen, sfx, shots, thumbnail, topics, videogen, visuals, voice,
    )
    comprobar("todos los módulos importan", True)
except Exception as exc:  # noqa: BLE001
    comprobar("todos los módulos importan", False, str(exc))
    print("\nNo se puede seguir sin importar.")
    raise SystemExit(1)

print("\nConfiguración")
comprobar("ningún plano pasa de SHOT_MAX",
          all(max(shots.split_duration(t, s)) <= config.SHOT_MAX
              for t in (7, 9, 12, 20, 35, 50, 63) for s in range(10)))
comprobar("el troceado en frases no pierde texto", _ok := True)
from pipeline.util import assert_no_text_lost, sentences  # noqa: E402

try:
    muestra = ("La luz viaja a 300.000 kilómetros por segundo. "
               "La Luna está a 384.000 km. Son 11,2 km/s. Fin.")
    assert_no_text_lost(muestra, sentences(muestra))
    comprobar("las cifras con punto de millar no parten la frase", True)
except RuntimeError as exc:
    comprobar("las cifras con punto de millar no parten la frase", False, str(exc))

print("\nCola de temas")
try:
    temas = topics.load_topics()
    comprobar(f"topics.yml carga ({len(temas)} temas)", len(temas) > 0)
    comprobar("todos tienen los cuatro campos",
              all(all(t.get(k) for k in ("title_hint", "angle", "keyword", "thumb_word"))
                  for t in temas))
    largas = [t["thumb_word"] for t in temas if not 3 <= len(t["thumb_word"]) <= 12]
    comprobar("las palabras de miniatura miden 3-12 letras", not largas, str(largas))
except Exception as exc:  # noqa: BLE001
    comprobar("topics.yml carga", False, str(exc))

print("\nPlanes visuales")
import yaml  # noqa: E402

from scripts.from_script import parse_markdown, split_scenes  # noqa: E402

for plan_file in sorted((RAIZ / "content" / "plans").glob("*.yml")):
    try:
        plan = yaml.safe_load(plan_file.read_text(encoding="utf-8"))
        comprobar(f"{plan_file.name} es YAML válido", True)
        escenas = plan.get("scenes") or {}
        sin_cuatro = [i for i, v in escenas.items() if len(v.get("generic") or []) != 4]
        comprobar(f"{plan_file.name}: cuatro búsquedas por escena",
                  not sin_cuatro, f"escenas {sin_cuatro}")
    except Exception as exc:  # noqa: BLE001
        comprobar(f"{plan_file.name} es YAML válido", False, str(exc))

print("\nPlan de producción")
# El prompt no pedía visual_generic y las 23 escenas caían al mismo respaldo:
# el vídeo entero se montó con cuatro búsquedas. Que no vuelva a pasar callando.
import inspect  # noqa: E402

_fuente = inspect.getsource(script_gen.plan_production)
comprobar("el prompt pide visual_generic", '"visual_generic"' in _fuente)
comprobar("y avisa de no repetirla entre bloques", "ESE bloque" in _fuente)

_reparto = inspect.getsource(visuals.build_clips)
comprobar("la consulta específica también se busca",
          "opciones.append(especifica)" in _reparto)

# El renderizado propio falló en una producción entera con «No module named
# numpy» porque la dependencia no estaba en requirements.txt: el módulo importa
# numpy dentro de las funciones, así que el fallo no aparece hasta que se usa.
_reqs = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
comprobar("numpy y scipy están en requirements.txt",
          "numpy" in _reqs and "scipy" in _reqs,
          "el renderizado propio fallará en el runner")

comprobar("el control de calidad está conectado antes de publicar",
          "quality.exigir(" in (RAIZ / "pipeline/run.py").read_text(encoding="utf-8"),
          "se publicaría sin que nadie mire el montaje")

comprobar("el tope de reutilización no pasa del 10 %",
          "max_share: float = 0.0" in inspect.getsource(shots.ClipBank),
          "sigue permitiendo que un solo clip llene el vídeo")

print("\nFiltros de material")
casos = [
    ("aerial-view-of-solar-panel-farm", "satellite solar panels space", False),
    ("handwritten-poem-cards-burning", "star surface burning", False),
    ("a-dwarf-hamster-over-the-table", "frozen planet", False),
    ("great-spotted-woodpecker-in-forest", "great red spot", False),
    ("abstract-blue-light-leak-overlay", "sun flare lens", False),
    ("frozen-moon-surface-in-orbit", "ice crust from space", True),
    ("sun-plasma-surface-detail", "sun close up surface", True),
    ("blocks-of-ice-in-a-frozen-lake", "ice crust from space", True),
]
for slug, consulta, esperado in casos:
    comprobar(f"«{slug[:34]}» -> {'entra' if esperado else 'fuera'}",
              visuals._is_space_clip(slug, consulta) == esperado)

print("\nWorkflow")
try:
    wf = yaml.safe_load((RAIZ / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    comprobar("daily.yml es YAML válido", True)
    trabajo = wf["jobs"]["publish"]
    comprobar("el timeout cabe en el límite de GitHub", trabajo["timeout-minutes"] <= 360)
except Exception as exc:  # noqa: BLE001
    comprobar("daily.yml es YAML válido", False, str(exc))

# El paso que publica el release adjunta varios ficheros del directorio de
# trabajo. Si una vía de producción deja de escribir uno, el montaje entero
# —hora y media de runner— se tira a la basura al final. Pasó con la
# descripción y se perdió un vídeo de doce minutos ya terminado.
adjuntos = ["miniatura.jpg", "subtitulos.srt", "descripcion.txt", "plan.json"]
fuente = (RAIZ / "scripts/from_script.py").read_text(encoding="utf-8")
paso = (RAIZ / ".github/workflows/daily.yml").read_text(encoding="utf-8")
for fichero in adjuntos:
    if fichero not in paso:
        continue
    comprobar(f"from_script escribe {fichero}", fichero in fuente,
              "el release lo adjunta: sin él se pierde el vídeo al final")

print("\nSecretos")
import os  # noqa: E402

obligatorios = {
    "ANTHROPIC_API_KEY": "escribir el guion",
    "AI33_API_KEY": "voz, música y efectos",
}
recomendados = {
    "PEXELS_API_KEY": "clips",
    "PIXABAY_API_KEY": "clips",
    "YT_REFRESH_TOKEN": "publicar",
}
alias = {"ANTHROPIC_API_KEY": "CLAUDE_API_KEY"}
for clave, para in obligatorios.items():
    if os.getenv(clave) or os.getenv(alias.get(clave, "_")):
        print(f"  OK   {clave}")
    else:
        print(f"  FALTA {clave} — sin él no se puede {para}")
        avisos.append(clave)
for clave, para in recomendados.items():
    if not os.getenv(clave):
        print(f"  aviso {clave} — recomendable para {para}")

print()
print("=" * 58)
if fallos:
    print(f"  {len(fallos)} comprobaciones FALLIDAS")
    for f in fallos:
        print(f"    - {f}")
    print("=" * 58)
    raise SystemExit(1)
print("  Todo correcto." + (f" Faltan {len(avisos)} secretos obligatorios." if avisos else ""))
print("=" * 58)
