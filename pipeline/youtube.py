"""Subida a YouTube con la Data API v3.

Autenticación por refresh token de larga duración guardado en secretos, así que
el workflow no necesita ningún navegador. Genéralo una vez con
`python scripts/get_youtube_token.py`.

Cuota: una subida cuesta 1.600 unidades del presupuesto diario de 10.000, y
poner la miniatura otras 50. Un vídeo al día cabe de sobra.
"""

from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import config
from .util import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]


def _service():
    creds = Credentials(
        token=None,
        refresh_token=config.require("YT_REFRESH_TOKEN", config.YT_REFRESH_TOKEN),
        client_id=config.require("YT_CLIENT_ID", config.YT_CLIENT_ID),
        client_secret=config.require("YT_CLIENT_SECRET", config.YT_CLIENT_SECRET),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(
    video: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    thumbnail: Path | None = None,
    captions: Path | None = None,
    privacy: str | None = None,
) -> str:
    youtube = _service()
    privacy = privacy or config.PRIVACY

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": _fit_tags(tags),
            "categoryId": config.YT_CATEGORY_ID,
            "defaultLanguage": config.YT_LANGUAGE,
            "defaultAudioLanguage": config.YT_LANGUAGE,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "license": "youtube",
        },
    }

    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    log.info("Subiendo «%s» (%.0f MB, %s)", title, video.stat().st_size / 1e6, privacy)
    response = None
    last_pct = -10
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct - last_pct >= 10:
                log.info("  %d %%", pct)
                last_pct = pct

    video_id = response["id"]
    log.info("Publicado: https://youtu.be/%s", video_id)

    if thumbnail and thumbnail.exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id, media_body=MediaFileUpload(str(thumbnail))
            ).execute()
            log.info("Miniatura aplicada")
        except HttpError as exc:
            log.warning("No se pudo poner la miniatura: %s", exc)

    if captions and captions.exists():
        try:
            youtube.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": config.YT_LANGUAGE,
                                  "name": "Español", "isDraft": False}},
                media_body=MediaFileUpload(str(captions)),
            ).execute()
            log.info("Subtítulos aplicados")
        except HttpError as exc:
            log.warning("No se pudieron subir los subtítulos: %s", exc)

    return video_id


def _fit_tags(tags: list[str]) -> list[str]:
    """YouTube limita la suma de las etiquetas a 500 caracteres."""
    out, total = [], 0
    for tag in tags:
        tag = tag.strip()[:60]
        if not tag:
            continue
        cost = len(tag) + 1
        if total + cost > 480:
            break
        out.append(tag)
        total += cost
    return out
