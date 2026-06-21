"""Detect and cache quiet audio tails for downloaded song files."""

from __future__ import annotations

import audioop
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from django.conf import settings as conf

from core.settings import storage

logger = logging.getLogger(__name__)

CACHE_FILE = Path(conf.BASE_DIR) / "config" / "audio_tail_cache.json"
CACHE_VERSION = 1
MAX_CACHE_ENTRIES = 512

SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2
CHUNK_SECONDS = 0.25
MINIMUM_ANALYSIS_DURATION_SECONDS = 20.0


@dataclass(frozen=True)
class TailAnalysis:
    effective_duration: float
    original_duration: float
    trimmed_seconds: float
    threshold_db: float


def _setting_float(key: str, fallback: float) -> float:
    try:
        return float(storage.get(key))
    except Exception:  # pylint: disable=broad-except
        return fallback


def enabled() -> bool:
    try:
        return bool(storage.get("silence_tail_skip_enabled"))
    except Exception:  # pylint: disable=broad-except
        return True


def transition_fade_seconds() -> float:
    return max(0.0, _setting_float("song_transition_fade_seconds", 2.0))


def _uri_to_path(uri: str) -> Optional[Path]:
    if not uri:
        return None

    uri = str(uri).strip()
    if not uri:
        return None

    if uri.startswith("file://"):
        parsed = urlparse(uri)

        if parsed.netloc and not parsed.path:
            raw_path = unquote(parsed.netloc)
        else:
            raw_path = unquote(parsed.path)
            if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
                raw_path = raw_path[1:]

        if os.name == "nt":
            raw_path = raw_path.replace("/", "\\")

        return Path(raw_path)

    path = Path(uri)
    if path.exists():
        return path

    return None


def _read_cache() -> dict[str, Any]:
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as infile:
            data = json.load(infile)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


def _write_cache(cache: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if len(cache) > MAX_CACHE_ENTRIES:
        sorted_items = sorted(
            cache.items(),
            key=lambda item: float(item[1].get("analyzed_at", 0.0))
            if isinstance(item[1], dict)
            else 0.0,
        )
        cache = dict(sorted_items[-MAX_CACHE_ENTRIES:])

    temp_file = CACHE_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as outfile:
        json.dump(cache, outfile, indent=2, sort_keys=True)
        outfile.write("\n")

    os.replace(temp_file, CACHE_FILE)


def _cache_key(
    path: Path,
    duration: float,
    threshold_db: float,
    min_tail_seconds: float,
    padding_seconds: float,
    start_grace_seconds: float,
) -> str:
    stat = path.stat()
    source = "|".join(
        [
            str(CACHE_VERSION),
            str(path.resolve()),
            str(stat.st_size),
            str(stat.st_mtime_ns),
            f"{float(duration):.3f}",
            f"{threshold_db:.2f}",
            f"{min_tail_seconds:.2f}",
            f"{padding_seconds:.2f}",
            f"{start_grace_seconds:.2f}",
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _analysis_from_cache_item(item: Any) -> Optional[TailAnalysis]:
    if not isinstance(item, dict):
        return None

    try:
        return TailAnalysis(
            effective_duration=float(item["effective_duration"]),
            original_duration=float(item["original_duration"]),
            trimmed_seconds=float(item["trimmed_seconds"]),
            threshold_db=float(item["threshold_db"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _dbfs_from_pcm(data: bytes) -> float:
    if not data:
        return -120.0

    if len(data) % SAMPLE_WIDTH:
        data = data[: -(len(data) % SAMPLE_WIDTH)]

    if not data:
        return -120.0

    rms = audioop.rms(data, SAMPLE_WIDTH)
    if rms <= 0:
        return -120.0

    return 20.0 * math.log10(rms / 32768.0)


def _analyze_path(
    path: Path,
    original_duration: float,
    threshold_db: float,
    min_tail_seconds: float,
    padding_seconds: float,
    start_grace_seconds: float,
) -> Optional[TailAnalysis]:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.info("ffmpeg not found; silence tail detection disabled for %s", path)
        return None

    if original_duration <= MINIMUM_ANALYSIS_DURATION_SECONDS:
        return TailAnalysis(
            effective_duration=original_duration,
            original_duration=original_duration,
            trimmed_seconds=0.0,
            threshold_db=threshold_db,
        )

    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "-",
    ]

    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    process = None
    elapsed = 0.0
    last_loud_end = 0.0
    chunk_size = int(SAMPLE_RATE * SAMPLE_WIDTH * CHUNK_SECONDS)

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        assert process.stdout is not None

        while True:
            data = process.stdout.read(chunk_size)
            if not data:
                break

            chunk_duration = len(data) / float(SAMPLE_RATE * SAMPLE_WIDTH)
            chunk_end = elapsed + chunk_duration

            if chunk_end >= start_grace_seconds:
                if _dbfs_from_pcm(data) >= threshold_db:
                    last_loud_end = chunk_end

            elapsed = chunk_end

        try:
            process.stdout.close()
        except OSError:
            pass

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    except Exception as error:  # pylint: disable=broad-except
        logger.warning("audio tail analysis failed for %s: %s", path, error)
        if process is not None and process.poll() is None:
            process.kill()
        return None

    if original_duration <= 0 and elapsed > 0:
        original_duration = elapsed

    if original_duration <= 0:
        return None

    if last_loud_end <= 0:
        return TailAnalysis(
            effective_duration=original_duration,
            original_duration=original_duration,
            trimmed_seconds=0.0,
            threshold_db=threshold_db,
        )

    effective_duration = min(
        original_duration,
        max(start_grace_seconds, last_loud_end + padding_seconds),
    )
    trimmed_seconds = max(0.0, original_duration - effective_duration)

    if trimmed_seconds < min_tail_seconds:
        effective_duration = original_duration
        trimmed_seconds = 0.0

    result = TailAnalysis(
        effective_duration=round(effective_duration, 3),
        original_duration=round(original_duration, 3),
        trimmed_seconds=round(trimmed_seconds, 3),
        threshold_db=threshold_db,
    )

    if result.trimmed_seconds > 0:
        logger.info(
            "audio tail detected for %s: original=%.2fs effective=%.2fs trimmed=%.2fs threshold=%.1fdB",
            path,
            result.original_duration,
            result.effective_duration,
            result.trimmed_seconds,
            result.threshold_db,
        )

    return result


def analyze_and_cache(uri: str, duration: Optional[float]) -> Optional[TailAnalysis]:
    if not enabled():
        return None

    try:
        original_duration = float(duration or 0.0)
    except (TypeError, ValueError):
        original_duration = 0.0

    if original_duration <= 0:
        return None

    path = _uri_to_path(uri)
    if path is None or not path.exists() or not path.is_file():
        return None

    threshold_db = _setting_float("silence_tail_threshold_db", -45.0)
    min_tail_seconds = max(0.0, _setting_float("silence_tail_min_seconds", 5.0))
    padding_seconds = max(0.0, _setting_float("silence_tail_padding_seconds", 2.0))
    start_grace_seconds = max(0.0, _setting_float("silence_tail_start_grace_seconds", 10.0))

    try:
        key = _cache_key(
            path,
            original_duration,
            threshold_db,
            min_tail_seconds,
            padding_seconds,
            start_grace_seconds,
        )
    except OSError as error:
        logger.warning("could not create audio tail cache key for %s: %s", path, error)
        return None

    cache = _read_cache()
    cached_result = _analysis_from_cache_item(cache.get(key))
    if cached_result is not None:
        return cached_result

    result = _analyze_path(
        path,
        original_duration,
        threshold_db,
        min_tail_seconds,
        padding_seconds,
        start_grace_seconds,
    )
    if result is None:
        return None

    cache[key] = {
        "effective_duration": result.effective_duration,
        "original_duration": result.original_duration,
        "trimmed_seconds": result.trimmed_seconds,
        "threshold_db": result.threshold_db,
        "source": str(path),
        "analyzed_at": __import__("time").time(),
    }
    _write_cache(cache)
    return result


def effective_duration_for_song(song) -> float:
    duration = float(getattr(song, "duration", 0.0) or 0.0)
    if duration <= 0:
        return duration

    uri = (
        getattr(song, "internal_url", "")
        or getattr(song, "stream_url", "")
        or getattr(song, "external_url", "")
    )

    result = analyze_and_cache(str(uri or ""), duration)
    if result is None:
        return duration

    return min(duration, max(0.0, result.effective_duration))
