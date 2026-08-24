"""Utilidades compartidas: log, HTTP con reintentos, ffmpeg."""

from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

log = logging.getLogger("eter")


def setup_logging(verbose: bool = True) -> None:
    # La consola de Windows llega en cp1252 y revienta con cualquier acento.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("urllib3", "PIL", "PIL.TiffImagePlugin", "PIL.Image", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "eter-pipeline/1.0"


def http(
    method: str,
    url: str,
    *,
    tries: int = 5,
    timeout: int = 60,
    **kwargs,
) -> requests.Response:
    """Petición con reintentos, backoff exponencial y respeto a Retry-After."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            r = SESSION.request(method, url, timeout=timeout, **kwargs)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = float(r.headers.get("Retry-After", 2**attempt))
                log.warning("HTTP %s en %s, reintento en %.1fs", r.status_code, url[:80], wait)
                time.sleep(wait + random.uniform(0, 0.5))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            wait = 2**attempt + random.uniform(0, 0.5)
            log.warning("Fallo de red (%s), reintento en %.1fs", exc.__class__.__name__, wait)
            time.sleep(wait)
    raise RuntimeError(f"HTTP falló tras {tries} intentos: {url}") from last


def download(url: str, dest: Path, *, max_bytes: int | None = None) -> Path:
    """Descarga a fichero. Corta si supera max_bytes (los MP4 de la NASA
    pueden pesar 1,7 GB y no queremos eso en un runner)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with SESSION.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    log.debug("Descarga truncada a %.0f MB: %s", total / 1e6, url[:70])
                    break
    return dest


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------


def ffmpeg(args: list[str], *, quiet: bool = True) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg falló: %s", " ".join(cmd[:14]))
        log.error(proc.stderr[-2500:])
        raise RuntimeError("ffmpeg falló")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"No se pudo leer la duración de {path}: {out.stderr[-400:]}") from exc


def probe_streams(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"streams": []}


def require_binaries() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            raise RuntimeError(f"Falta {binary} en el PATH.")


# --------------------------------------------------------------------------
# Varios
# --------------------------------------------------------------------------


def slugify(text: str, maxlen: int = 60) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen] or "video"


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("%s ilegible, se usa el valor por defecto", path)
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
