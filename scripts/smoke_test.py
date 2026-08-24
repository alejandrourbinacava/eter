"""Prueba de humo de toda la cadena de producción, sin tocar el generador de
guiones ni YouTube.

Usa un guion corto escrito a mano en la voz de Éter, así que solo necesita
AI33_API_KEY. Sirve para verificar que la locución, la búsqueda de material, el
montaje y la miniatura funcionan de punta a punta antes de gastar en un vídeo
completo.

    python scripts/smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import assemble, config, music, sfx, thumbnail, visuals, voice  # noqa: E402
from pipeline.script_gen import Scene, VideoPlan  # noqa: E402
from pipeline.util import log, require_binaries, setup_logging, write_json  # noqa: E402

NARRATION = """\
Bajo la superficie de Europa hay más agua líquida que en todos los océanos de \
la Tierra juntos. No es una estimación optimista ni una hipótesis de trabajo. \
Es la conclusión a la que llevan tres décadas de mediciones, y sigue siendo uno \
de los datos que peor encajan con la idea que tenemos del sistema solar. Europa \
es una luna de Júpiter más pequeña que la nuestra. Está a 628 millones de \
kilómetros del Sol, en un sitio donde la luz llega tan débil que la temperatura \
de la superficie no sube de los ciento sesenta grados bajo cero. Debería ser una \
bola de hielo muerta.

Pero hay algo que el hielo no puede esconder. Cuando la sonda Galileo pasó cerca \
en los años noventa, su magnetómetro detectó un campo magnético inducido. Para \
que eso ocurra hace falta una capa conductora bajo la corteza. Agua salada. \
Mucha. Los modelos actuales sitúan ese océano bajo entre quince y veinticinco \
kilómetros de hielo, con una profundidad de unos cien kilómetros. La Tierra, con \
toda su superficie cubierta de mar, tiene océanos de cuatro kilómetros de \
profundidad media. Europa tiene el doble de agua líquida que un planeta entero \
que se llama, precisamente, el planeta azul.

Y el agua sola no basta. Lo que convierte a Europa en el sitio más incómodo del \
sistema solar es de dónde sale el calor que la mantiene líquida. No viene del \
Sol. Viene de Júpiter. La órbita de Europa es ligeramente excéntrica, así que la \
gravedad del gigante la estira y la comprime en cada vuelta. Esa deformación \
constante genera fricción interna, y la fricción genera calor. Es el mismo \
mecanismo que hace de Ío el cuerpo más volcánico que conocemos. En Europa, ese \
calor no llega a fundir la superficie, pero sí es suficiente para sostener un \
océano durante miles de millones de años. Sin una estrella cerca. Sin luz.

Lo que no sabemos es lo que hay dentro. No hemos visto ese océano. No hemos \
medido su salinidad, ni su química, ni si el fondo tiene actividad hidrotermal \
como la que en la Tierra alimenta ecosistemas enteros sin necesidad de sol. \
Europa Clipper lleva desde 2024 camino de averiguarlo y no llegará hasta 2030. \
Cuarenta y nueve sobrevuelos para medir el grosor del hielo y la composición de \
lo que hay debajo. Ni siquiera va a aterrizar. Y aún así, cuando terminen esos \
sobrevuelos, seguiremos sin saber lo único que de verdad queremos saber. Porque \
un océano de cien kilómetros de profundidad, con energía, con sales y con miles \
de millones de años por delante, es exactamente la lista de ingredientes que en \
la Tierra bastó una vez. Una sola vez, que sepamos.\
"""

# (sujeto concreto para los archivos científicos, descripción genérica para los
# bancos de stock, prompt por si hubiera que generar el plano, y la frase de
# remate sobre la que caerá el golpe de sonido)
VISUALS = [
    ("Europa Jupiter moon",
     ["cracked ice texture", "frozen planet in space", "deep dark ocean water",
      "icy surface closeup"],
     "Photorealistic view of Europa's cracked icy surface",
     "Debería ser una bola de hielo muerta."),
    ("Galileo spacecraft Jupiter",
     ["spacecraft in deep space", "satellite orbiting planet", "gas giant clouds",
      "magnetic field aurora"],
     "A spacecraft passing above a fractured ice moon",
     "Agua salada."),
    ("Io volcanic moon",
     ["volcanic eruption lava", "gas giant in space", "molten rock glowing",
      "planet orbiting star"],
     "Tidal flexing of a moon lit by an enormous gas giant",
     "Sin una estrella cerca. Sin luz."),
    ("Europa Clipper mission",
     ["probe flying through space", "underwater hydrothermal vent", "ice cave blue",
      "distant sun in space"],
     "A probe approaching an ice-covered ocean world",
     "Una sola vez, que sepamos."),
]


def main() -> int:
    setup_logging()
    require_binaries()

    blocks = [b.strip().replace("\n", " ") for b in NARRATION.split("\n\n") if b.strip()]
    scenes = [
        Scene(index=i, narration=b, visual_query=VISUALS[i][0],
              visual_generic=VISUALS[i][1], visual_prompt=VISUALS[i][2],
              emphasis=[VISUALS[i][3]])
        for i, b in enumerate(blocks)
    ]

    plan = VideoPlan(
        topic={"title_hint": "smoke-test", "keyword": "europa jupiter moon"},
        title="El Océano De Europa Tiene Más Agua Que Toda La Tierra",
        thumb_word="OCÉANO",
        thumb_prompt="Europa's cracked icy surface with Jupiter behind it",
        description="Prueba de humo.",
        tags=["europa", "júpiter", "océano"],
        narration=" ".join(blocks),
        scenes=scenes,
    )

    workdir = config.BUILD_DIR / "smoke-test"
    workdir.mkdir(parents=True, exist_ok=True)
    log.info("Guion de prueba: %d palabras en %d escenas", plan.word_count, len(scenes))

    voice.narrate(plan.scenes, workdir)
    voice.write_srt(plan.scenes, workdir / "subtitulos.srt")
    total = (sum(s.duration for s in plan.scenes)
             + config.SCENE_GAP * (len(plan.scenes) - 1))
    bed = sfx.bed_for(plan.scenes, workdir, total)
    audio = voice.mix(plan.scenes, workdir, music.track(plan.title), bed)

    visuals.build_clips(plan.scenes, workdir)
    video = assemble.render(plan.scenes, audio, workdir / "video.mp4")

    hero = None
    raw = workdir / "raw"
    if raw.exists():
        images = sorted((p for p in raw.iterdir() if p.suffix.lower() in (".jpg", ".png")),
                        key=lambda p: p.stat().st_size, reverse=True)
        hero = images[0] if images else None
    if hero is None:
        hero = thumbnail.hero_frame(video, workdir / "hero.jpg", at=8.0)
    thumbnail.build(plan.thumb_word, hero, workdir / "miniatura.jpg")

    write_json(workdir / "plan.json", plan.to_dict())
    log.info("Prueba completa. Revisa %s", workdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
