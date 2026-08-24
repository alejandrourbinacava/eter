"""Locución: una pista por escena, la mezcla final y los subtítulos.

Se sintetiza escena a escena en vez de todo el guion de una vez por tres
razones: da la duración exacta de cada plano, permite reintentar solo el
fragmento que falle, y evita que un texto de 14.000 caracteres se pierda en una
sola llamada.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import ai33, config
from .util import ffmpeg, log, probe_duration


def narrate(scenes, workdir: Path) -> list:
    """Sintetiza cada escena y anota su duración."""
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    for scene in scenes:
        dest = audio_dir / f"scene_{scene.index:03d}.mp3"
        if not dest.exists():
            ai33.tts(_prepare_for_tts(scene.narration), dest)
        scene.audio_path = str(dest)
        scene.duration = probe_duration(dest)
        log.info(
            "  escena %02d: %5.1fs  %s…",
            scene.index, scene.duration, scene.narration[:52].replace("\n", " "),
        )

    total = sum(s.duration for s in scenes) + config.SCENE_GAP * (len(scenes) - 1)
    log.info("Locución completa: %.1f min en %d escenas", total / 60, len(scenes))
    return scenes


def _prepare_for_tts(text: str) -> str:
    """Retoques para que la voz lea los datos como los leería un narrador."""
    text = text.replace("…", "...")
    # 384.000 -> "384 mil" suena mal; ElevenLabs lee bien el punto de millar
    # en español, así que solo se normalizan las unidades abreviadas.
    text = re.sub(r"\bkm/s\b", "kilómetros por segundo", text)
    text = re.sub(r"\bkm/h\b", "kilómetros por hora", text)
    text = re.sub(r"\bkm\b", "kilómetros", text)
    text = re.sub(r"\b°C\b", "grados", text)
    text = re.sub(r"(\d)\s*%", r"\1 por ciento", text)
    return re.sub(r"\s+", " ", text).strip()


def mix(scenes, workdir: Path, music: Path | None = None) -> Path:
    """Concatena las escenas con silencios, añade música y normaliza."""
    audio_dir = workdir / "audio"
    silence = audio_dir / "gap.mp3"
    if not silence.exists():
        ffmpeg([
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(config.SCENE_GAP), "-c:a", "libmp3lame", "-b:a", "192k",
            str(silence),
        ])

    listing = audio_dir / "concat.txt"
    lines = []
    for i, scene in enumerate(scenes):
        if i:
            lines.append(f"file '{silence.as_posix()}'")
        lines.append(f"file '{Path(scene.audio_path).as_posix()}'")
    listing.write_text("\n".join(lines), encoding="utf-8")

    voice_track = audio_dir / "voice.wav"
    ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-ar", "44100", "-ac", "2", str(voice_track),
    ])

    final = audio_dir / "master.m4a"
    if music and music.exists():
        duration = probe_duration(voice_track)
        # Para que MUSIC_DB signifique algo, las dos pistas se llevan primero al
        # mismo punto de referencia (-16 LUFS) y solo entonces se baja la música.
        # Así -25 dB son veinticinco decibelios por debajo de la voz, y no un
        # número que depende de cómo viniera masterizada la pista.
        #
        # Encima va un compresor con cadena lateral: cuando entra la voz, la
        # música cede sola unos decibelios más y vuelve al soltar. Un nivel fijo
        # suena bien en los silencios y estorba durante la narración.
        ffmpeg([
            "-i", str(voice_track),
            "-stream_loop", "-1", "-i", str(music),
            "-filter_complex",
            f"[0:a]loudnorm=I=-16:TP=-2:LRA=11,asplit=2[voz][llave];"
            f"[1:a]loudnorm=I=-16:TP=-2:LRA=11,volume={config.MUSIC_DB}dB,"
            f"afade=t=in:st=0:d=3,afade=t=out:st={max(duration - 5, 0):.2f}:d=5[lecho];"
            f"[lecho][llave]sidechaincompress="
            f"threshold=0.03:ratio=4:attack=20:release=400:makeup=1[duck];"
            f"[voz][duck]amix=inputs=2:duration=first:normalize=0[mezcla];"
            f"[mezcla]loudnorm=I=-14:TP=-1.5:LRA=11[out]",
            "-map", "[out]", "-t", f"{duration:.3f}",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(final),
        ])
        log.info("Mezcla con música a %.0f dB bajo la voz", config.MUSIC_DB)
    else:
        ffmpeg([
            "-i", str(voice_track),
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(final),
        ])

    log.info("Audio maestro: %.1f min", probe_duration(final) / 60)
    return final


# --------------------------------------------------------------------------
# Subtítulos
# --------------------------------------------------------------------------


def write_srt(scenes, dest: Path, words_per_cue: int = 9) -> Path:
    """Reparte cada escena en rótulos proporcionales a su longitud.

    No es alineación forzada real, pero con locución sintética a ritmo
    constante el desfase se queda muy por debajo de lo perceptible.
    """
    cues: list[tuple[float, float, str]] = []
    clock = 0.0
    for scene in scenes:
        words = scene.narration.split()
        groups = [words[i:i + words_per_cue] for i in range(0, len(words), words_per_cue)]
        if not groups:
            continue
        total_chars = sum(len(" ".join(g)) for g in groups) or 1
        start = clock
        for group in groups:
            share = len(" ".join(group)) / total_chars
            span = scene.duration * share
            cues.append((start, start + span, " ".join(group)))
            start += span
        clock += scene.duration + config.SCENE_GAP

    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(text)
        lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")
    log.info("Subtítulos: %d rótulos", len(cues))
    return dest


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
