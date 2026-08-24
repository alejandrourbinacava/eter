"""Cola de temas: elegir el siguiente, registrar lo publicado y reponer.

El historial (`content/published.json`) lo commitea el propio workflow, así que
el canal recuerda de qué ha hablado aunque cada ejecución arranque en un runner
limpio. Es lo que impide que a la vigésima publicación se repita el tema.
"""

from __future__ import annotations

import datetime as dt
import json
import re

import yaml

from . import config
from .util import log, read_json, write_json

MIN_BACKLOG = 6
REPLENISH_COUNT = 12


def load_topics() -> list[dict]:
    if not config.TOPICS_FILE.exists():
        return []
    data = yaml.safe_load(config.TOPICS_FILE.read_text(encoding="utf-8")) or {}
    return data.get("topics", [])


def load_history() -> list[dict]:
    return read_json(config.PUBLISHED_FILE, [])


def next_topic() -> dict:
    topics = load_topics()
    used = {_key(entry.get("title_hint", "")) for entry in load_history()}
    for topic in topics:
        if _key(topic["title_hint"]) not in used:
            return topic
    raise RuntimeError(
        "No quedan temas sin publicar. Añade más a content/topics.yml o deja que "
        "el reabastecimiento automático los genere."
    )


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def record(topic: dict, plan, video_id: str | None) -> None:
    history = load_history()
    history.append({
        "date": dt.date.today().isoformat(),
        "title_hint": topic["title_hint"],
        "title": plan.title,
        "thumb_word": plan.thumb_word,
        "words": plan.word_count,
        "scenes": len(plan.scenes),
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}" if video_id else None,
    })
    write_json(config.PUBLISHED_FILE, history)
    log.info("Historial actualizado: %d vídeos", len(history))


def remaining() -> int:
    used = {_key(e.get("title_hint", "")) for e in load_history()}
    return sum(1 for t in load_topics() if _key(t["title_hint"]) not in used)


# --------------------------------------------------------------------------
# Reabastecimiento
# --------------------------------------------------------------------------

REPLENISH_SYSTEM = """Eres el editor de contenidos de Éter, un canal de \
documentales espaciales en castellano. Propones temas y devuelves \
exclusivamente un array JSON, sin texto alrededor y sin vallas de código."""


def replenish(count: int = REPLENISH_COUNT) -> int:
    """Pide temas nuevos y los añade al final de topics.yml."""
    from .script_gen import client

    existing = [t["title_hint"] for t in load_topics()]
    published = [e.get("title", "") for e in load_history()]

    prompt = f"""El canal ya tiene cubiertos o en cola estos temas:

{chr(10).join('- ' + t for t in existing + published)}

Propón {count} temas NUEVOS que no solapen con ninguno de los anteriores.

Cada tema tiene que sostenerse sobre una anomalía real y verificable: algo que \
la ciencia ha medido y todavía no explica del todo, o un dato que contradice la \
intuición del espectador. Nada de pseudociencia y nada de temas tan trillados \
que no aporten un ángulo propio.

Devuelve un array JSON de objetos con exactamente estas claves:

[
  {{
    "title_hint": "título tentativo en Title Case, 45-65 caracteres",
    "angle": "una frase en español con la anomalía concreta y su dato",
    "keyword": "2-3 palabras EN INGLÉS para buscar material de archivo",
    "thumb_word": "UNA PALABRA EN MAYÚSCULAS, 5-10 letras"
  }}
]"""

    log.info("Reponiendo la cola de temas (%d nuevos)", count)
    resp = client().messages.create(
        model=config.SCRIPT_MODEL,
        max_tokens=4000,
        system=REPLENISH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.content[0].text.strip())
    try:
        new_topics = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("El reabastecimiento devolvió algo que no es JSON; se omite")
        return 0

    seen = {_key(t) for t in existing}
    fresh = [
        t for t in new_topics
        if isinstance(t, dict) and t.get("title_hint") and _key(t["title_hint"]) not in seen
    ]
    if not fresh:
        return 0

    block = "\n" + "\n\n".join(
        "  - title_hint: {title_hint}\n"
        "    angle: {angle}\n"
        "    keyword: {keyword}\n"
        "    thumb_word: {thumb_word}".format(
            title_hint=_yaml_scalar(t["title_hint"]),
            angle=_yaml_scalar(t.get("angle", "")),
            keyword=_yaml_scalar(t.get("keyword", "deep space")),
            thumb_word=_yaml_scalar(t.get("thumb_word", "ÉTER")),
        )
        for t in fresh
    ) + "\n"

    with open(config.TOPICS_FILE, "a", encoding="utf-8") as fh:
        fh.write(block)

    # Releer para confirmar que el YAML sigue siendo válido.
    yaml.safe_load(config.TOPICS_FILE.read_text(encoding="utf-8"))
    log.info("Añadidos %d temas a la cola", len(fresh))
    return len(fresh)


def _yaml_scalar(value: str) -> str:
    """Entrecomilla si el valor puede romper el YAML de bloque."""
    value = str(value).replace("\n", " ").strip()
    if value.startswith(("'", '"', "&", "*", "!", "%", "@", "`", "-", "?")) or ": " in value:
        return json.dumps(value, ensure_ascii=False)
    return value
