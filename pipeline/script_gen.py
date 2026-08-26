"""Generación del guion en la voz de Éter.

Se hace en dos llamadas deliberadamente separadas:

  1. `write_narration`  — prosa corrida, sin estructura, sin JSON. Pedirle al
     modelo que rellene 25 objetos JSON degrada la escritura; escribiendo del
     tirón mantiene el ritmo y los encadenados largos que definen al canal.
  2. `plan_production`  — sobre esa prosa ya escrita, se trocea en escenas y se
     decide el material visual, el título, la miniatura y la descripción.

Un tercer paso local (`_split_into_scenes`) garantiza que ninguna escena se
corte a mitad de frase, pase lo que pase con el modelo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from anthropic import Anthropic

from . import config
from .util import assert_no_text_lost, log, sentences

_client: Anthropic | None = None


def texto_de(resp) -> str:
    """El texto de la respuesta, saltándose los bloques de razonamiento.

    `content[0]` no siempre es texto: los modelos que razonan devuelven primero
    uno o varios ThinkingBlock, y coger el primero a ciegas revienta con un
    AttributeError. Se concatenan todos los bloques que sí traen texto.
    """
    partes = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    if not partes:
        partes = [b.text for b in resp.content if hasattr(b, "text")]
    return "\n".join(partes).strip()


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=config.require("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY))
    return _client


@dataclass
class Scene:
    index: int
    narration: str
    # El sujeto concreto, en inglés, para los archivos científicos (NASA SVS,
    # imágenes de misión). Puede ser un nombre propio: "Enceladus geysers".
    visual_query: str = ""
    # Varias descripciones genéricas y filmables, en inglés, para los bancos de
    # stock. Son VARIAS porque una escena ocupa unos ocho planos: con una sola
    # búsqueda salen ocho planos casi idénticos y el corte no se percibe.
    # NUNCA un nombre propio: los bancos no devuelven cero, devuelven lo que se
    # parece por letras, y "great red spot" acaba siendo un pájaro carpintero.
    visual_generic: list = field(default_factory=list)
    # Prompt fotorrealista por si hay que generar el plano.
    visual_prompt: str = ""
    # Frases del bloque que merecen un golpe de sonido. Literales del texto.
    emphasis: list = field(default_factory=list)
    # Palabras con sus tiempos, que devuelve la locución. Es lo que permite
    # colocar ese golpe sobre la palabra exacta.
    words: list = field(default_factory=list)
    audio_path: str | None = None
    clip_path: str | None = None
    duration: float = 0.0


@dataclass
class VideoPlan:
    topic: dict
    title: str
    thumb_word: str
    # Descripción en inglés de la imagen de la miniatura, para generarla.
    thumb_prompt: str
    description: str
    tags: list[str]
    narration: str
    scenes: list[Scene] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.narration.split())

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "title": self.title,
            "thumb_word": self.thumb_word,
            "thumb_prompt": self.thumb_prompt,
            "description": self.description,
            "tags": self.tags,
            "narration": self.narration,
            "scenes": [s.__dict__ for s in self.scenes],
        }


# --------------------------------------------------------------------------
# Paso 1 — la prosa
# --------------------------------------------------------------------------

SYSTEM = """Eres el guionista de Éter, un canal de documentales espaciales en \
castellano. Escribes el texto que un narrador leerá en voz alta, nada más: sin \
encabezados, sin marcas de escena, sin acotaciones, sin viñetas, sin indicar \
tiempos. Solo la narración corrida, en párrafos.

Tu único criterio de calidad es que el resultado sea indistinguible de los \
guiones ya publicados del canal. Te doy la guía de voz y un guion real completo \
como referencia. Respétalos al detalle: la estructura en cinco tiempos, el \
ritmo de frase, el uso de cifras, las transiciones explícitas entre bloques y \
todas las prohibiciones.

Rigor factual innegociable: cada dato debe ser verificable y estar en el \
consenso científico actual. Si dudas de una cifra, cámbiala por una formulación \
cualitativa en vez de inventarla. La incertidumbre científica genuina es \
material narrativo de primera; la falsedad no."""


def write_narration(topic: dict, avoid: list[str]) -> str:
    guide = config.VOICE_GUIDE.read_text(encoding="utf-8")
    reference = config.REFERENCE_SCRIPT.read_text(encoding="utf-8")

    avoid_block = ""
    if avoid:
        avoid_block = (
            "\n\nEl canal ya ha publicado estos vídeos. No repitas su tesis ni "
            "reutilices sus aperturas:\n- " + "\n- ".join(avoid[-25:])
        )

    prompt = f"""<guia_de_voz>
{guide}
</guia_de_voz>

<guion_de_referencia>
{reference}
</guion_de_referencia>

<encargo>
Tema: {topic['title_hint']}
Ángulo — la anomalía que sostiene el vídeo: {topic['angle']}
Extensión objetivo: {config.TARGET_WORDS} palabras (margen de ±8 %).
</encargo>{avoid_block}

Escribe la narración completa. Empieza directamente por la primera frase del \
Tiempo 1 y termina en la última frase del Tiempo 5. No escribas nada más."""

    log.info("Escribiendo guion: %s", topic["title_hint"])
    resp = client().messages.create(
        model=config.SCRIPT_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = texto_de(resp)
    text = _strip_stage_directions(text)
    log.info("Guion escrito: %d palabras (~%.1f min)", len(text.split()),
             len(text.split()) / config.WORDS_PER_MINUTE)
    return text


def _strip_stage_directions(text: str) -> str:
    """Quita cualquier resto de marcado que el modelo se deje colar."""
    text = re.sub(r"^\s*(#+|\*\*)\s*(TIEMPO|Tiempo|ESCENA|Escena|Bloque)[^\n]*\n", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)
    text = re.sub(r"\[[^\]]{0,60}\]", "", text)       # [música], [pausa]
    text = re.sub(r"\((?:pausa|música|silencio)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Paso 2 — troceado en escenas (local, determinista)
# --------------------------------------------------------------------------

def _split_into_scenes(narration: str, words_per_scene: int) -> list[str]:
    """Agrupa frases completas en bloques de ~words_per_scene palabras.

    El corte va siempre en final de frase: un plano nunca cambia a mitad de una
    idea, y el TTS por escena no parte una entonación por la mitad.
    """
    frases = sentences(narration)
    assert_no_text_lost(narration, frases)
    blocks: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in frases:
        current.append(sentence)
        count += len(sentence.split())
        if count >= words_per_scene:
            blocks.append(" ".join(current))
            current, count = [], 0
    if current:
        # Una cola muy corta se pega al bloque anterior en vez de ser un plano.
        if blocks and count < words_per_scene * 0.4:
            blocks[-1] += " " + " ".join(current)
        else:
            blocks.append(" ".join(current))
    return blocks


# --------------------------------------------------------------------------
# Paso 3 — plan de producción
# --------------------------------------------------------------------------

PLAN_SYSTEM = """Eres el director de fotografía y el editor de metadatos de \
Éter. Recibes un guion ya escrito y devuelves exclusivamente un objeto JSON \
válido, sin texto alrededor y sin vallas de código."""


def plan_production(topic: dict, narration: str, blocks: list[str]) -> dict:
    guide = config.VOICE_GUIDE.read_text(encoding="utf-8")
    numbered = "\n\n".join(f"[{i}] {b}" for i, b in enumerate(blocks))

    prompt = f"""<guia_de_voz>
{guide}
</guia_de_voz>

El guion está dividido en {len(blocks)} bloques numerados. Cada bloque necesita \
un plano.

<bloques>
{numbered}
</bloques>

Devuelve este JSON exacto:

{{
  "title": "título de 45-65 caracteres en Title Case, según la sección 6",
  "thumb_word": "UNA SOLA PALABRA EN MAYÚSCULAS, 5-10 letras",
  "thumb_prompt": "EN INGLÉS, una frase describiendo la imagen de la miniatura: el objeto del vídeo en una composición dramática y fotorrealista sobre fondo negro. Sin texto ni rótulos, que la tipografía se añade aparte. Deja espacio oscuro a un lado para la palabra.",
  "description": "descripción de YouTube según la sección 7, con sus tres párrafos y emojis, sin los hashtags",
  "tags": ["25-30 etiquetas en español, de lo específico a lo genérico"],
  "scenes": [
    {{
      "index": 0,
      "visual_query": "2-4 palabras EN INGLÉS para buscar en archivos de vídeo de la NASA y bancos de imágenes. Concreto y filmable: 'saturn rings closeup', 'solar flare eruption'. Nunca abstracto: nada de 'human curiosity' ni 'the passage of time'.",
      "visual_prompt": "una frase EN INGLÉS describiendo un plano fotorrealista de documental para ese bloque, por si hay que generarlo",
      "emphasis": "LA frase del bloque que más golpea, copiada LITERAL Y EXACTA del texto, tal cual aparece. Cadena vacía si el bloque no tiene ninguna."
    }}
  ]
}}

Reglas de los planos:
- Un objeto por bloque, en orden, con "index" de 0 a {len(blocks) - 1}.
- El plano ilustra lo que se está diciendo en ese bloque concreto.
- Varía: no repitas la misma visual_query en bloques consecutivos.
- Material real de archivo espacial o render fotorrealista. Nunca diagramas, \
nunca texto en pantalla, nunca personas hablando a cámara."""

    log.info("Planificando %d planos y metadatos", len(blocks))
    resp = client().messages.create(
        model=config.SCRIPT_MODEL,
        max_tokens=16000,
        system=PLAN_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = texto_de(resp)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Rescate: quedarse con el primer objeto JSON bien formado del texto.
        start = raw.find("{")
        depth, end = 0, None
        for i, ch in enumerate(raw[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                end = i + 1
                break
        if end is None:
            raise
        return json.loads(raw[start:end])


# --------------------------------------------------------------------------
# API pública del módulo
# --------------------------------------------------------------------------


_FALLBACK_GENERIC = ["deep space stars", "cosmic nebula", "planet in space",
                     "galaxy in deep space"]


def _emphasis_list(value, block: str) -> list[str]:
    """Se queda solo con las frases que aparecen LITERALES en el bloque.

    El modelo tiende a reescribir levemente lo que cita. Una frase que no está
    en el texto no se puede localizar en la transcripción, así que se descarta
    en vez de colocar el golpe a ojo.
    """
    if isinstance(value, str):
        value = [value]
    out = []
    for item in value or []:
        phrase = str(item).strip().strip('"«»')
        if len(phrase) >= 4 and phrase in block:
            out.append(phrase)
    return out[:1]


def _generic_list(value) -> list[str]:
    """Normaliza `visual_generic` a una lista de búsquedas utilizables."""
    if isinstance(value, str):
        value = [value]
    items = [str(v).strip() for v in (value or []) if str(v).strip()]
    # Descartar frases que sean instrucciones y no búsquedas.
    items = [i for i in items if 2 <= len(i.split()) <= 6]
    return items or list(_FALLBACK_GENERIC)


def build_plan(topic: dict, avoid: list[str]) -> VideoPlan:
    narration = write_narration(topic, avoid)
    blocks = _split_into_scenes(narration, config.WORDS_PER_SCENE)
    plan = plan_production(topic, narration, blocks)

    by_index = {int(s.get("index", i)): s for i, s in enumerate(plan.get("scenes", []))}
    scenes = []
    for i, block in enumerate(blocks):
        meta = by_index.get(i, {})
        scenes.append(
            Scene(
                index=i,
                narration=block,
                visual_query=(meta.get("visual_query") or topic["keyword"]).strip(),
                visual_generic=_generic_list(meta.get("visual_generic")),
                visual_prompt=(meta.get("visual_prompt") or "").strip(),
                emphasis=_emphasis_list(meta.get("emphasis"), block),
            )
        )

    thumb_prompt = (plan.get("thumb_prompt") or "").strip()
    thumb_word = (plan.get("thumb_word") or topic.get("thumb_word", "ÉTER")).upper().strip()
    thumb_word = re.sub(r"[^A-ZÁÉÍÓÚÜÑ]", "", thumb_word)[:12] or "ÉTER"

    return VideoPlan(
        topic=topic,
        title=(plan.get("title") or topic["title_hint"]).strip()[:100],
        thumb_word=thumb_word,
        thumb_prompt=thumb_prompt,
        description=(plan.get("description") or "").strip(),
        tags=[t.strip() for t in plan.get("tags", []) if t.strip()][:35],
        narration=narration,
        scenes=scenes,
    )
