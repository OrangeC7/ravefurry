"""DB-independent backup of live playback state.

This mirrors CurrentSong and QueuedSong to a small JSON file so a local
PostgreSQL restart/recreate does not wipe the active event queue.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Any, Dict, Optional

from django.conf import settings as conf
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core import models, redis
from core.settings import storage

logger = logging.getLogger(__name__)

STATE_FILE = pathlib.Path(conf.BASE_DIR) / "config" / "playback_state_backup.json"

QUEUE_FIELDS = (
    "index",
    "manually_requested",
    "votes",
    "internal_url",
    "external_url",
    "stream_url",
    "artist",
    "title",
    "duration",
    "requester_ip",
    "requester_session_key",
)

CURRENT_FIELDS = (
    "queue_key",
    "manually_requested",
    "votes",
    "internal_url",
    "external_url",
    "stream_url",
    "artist",
    "title",
    "duration",
    "requester_ip",
    "requester_session_key",
)


def _datetime_to_string(value) -> str:
    if value is None:
        return timezone.now().isoformat()
    return value.isoformat()


def _string_to_datetime(value: Any):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _song_payload(song, fields) -> Dict[str, Any]:
    payload = {}
    for field in fields:
        payload[field] = getattr(song, field)
    return payload


def _current_payload(song: models.CurrentSong) -> Dict[str, Any]:
    payload = _song_payload(song, CURRENT_FIELDS)
    payload["created"] = _datetime_to_string(song.created)
    payload["last_paused"] = _datetime_to_string(song.last_paused)
    return payload


def _atomic_write(payload: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = STATE_FILE.with_suffix(".tmp")

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())

    os.replace(temporary_file, STATE_FILE)


def snapshot() -> None:
    """Write a small, atomic mirror of the current playback state."""

    try:
        current_song: Optional[models.CurrentSong] = models.CurrentSong.objects.first()
        queued_songs = list(models.QueuedSong.objects.order_by("index", "id"))

        payload = {
            "version": 1,
            "saved_at": timezone.now().isoformat(),
            "paused": bool(redis.get("paused")),
            "current": _current_payload(current_song) if current_song else None,
            "queue": [_song_payload(song, QUEUE_FIELDS) for song in queued_songs],
        }

        _atomic_write(payload)
    except Exception as error:  # pylint: disable=broad-except
        logger.warning("failed to snapshot playback state: %s", error)


def snapshot_on_commit() -> None:
    """Snapshot after the active DB transaction commits."""

    try:
        transaction.on_commit(snapshot)
    except Exception:  # pylint: disable=broad-except
        snapshot()


def _load_payload() -> Optional[Dict[str, Any]]:
    if not STATE_FILE.exists():
        return None

    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, dict):
            return None
        if int(payload.get("version", 0)) != 1:
            return None

        return payload
    except Exception as error:  # pylint: disable=broad-except
        logger.warning("failed to read playback state backup: %s", error)
        return None


def _create_queued_song(item: Dict[str, Any]) -> None:
    models.QueuedSong.objects.create(
        index=int(item.get("index") or 1),
        manually_requested=bool(item.get("manually_requested")),
        votes=int(item.get("votes") or 0),
        internal_url=item.get("internal_url"),
        external_url=str(item.get("external_url") or ""),
        stream_url=item.get("stream_url"),
        artist=str(item.get("artist") or ""),
        title=str(item.get("title") or ""),
        duration=float(item.get("duration") or -1),
        requester_ip=str(item.get("requester_ip") or ""),
        requester_session_key=str(item.get("requester_session_key") or ""),
    )


def _create_current_song(item: Dict[str, Any]) -> None:
    current_song = models.CurrentSong.objects.create(
        queue_key=int(item.get("queue_key") or -1),
        manually_requested=bool(item.get("manually_requested")),
        votes=int(item.get("votes") or 0),
        internal_url=str(item.get("internal_url") or ""),
        external_url=str(item.get("external_url") or ""),
        stream_url=item.get("stream_url"),
        artist=str(item.get("artist") or ""),
        title=str(item.get("title") or ""),
        duration=float(item.get("duration") or -1),
        requester_ip=str(item.get("requester_ip") or ""),
        requester_session_key=str(item.get("requester_session_key") or ""),
    )

    # CurrentSong uses auto_now_add, so update timestamps after creation.
    models.CurrentSong.objects.filter(pk=current_song.pk).update(
        created=_string_to_datetime(item.get("created")),
        last_paused=_string_to_datetime(item.get("last_paused")),
    )


def restore_if_database_empty() -> bool:
    """Restore playback state only when DB playback tables are empty."""

    payload = _load_payload()
    if not payload:
        return False

    try:
        if models.CurrentSong.objects.exists() or models.QueuedSong.objects.exists():
            return False

        queue_items = payload.get("queue") or []
        current_item = payload.get("current")

        if not queue_items and not current_item:
            return False

        with transaction.atomic():
            for item in sorted(
                queue_items,
                key=lambda queue_item: int(queue_item.get("index") or 0),
            ):
                _create_queued_song(item)

            if current_item:
                _create_current_song(current_item)

        if "paused" in payload:
            storage.put("paused", bool(payload.get("paused")))

        logger.info(
            "restored playback state from backup: current=%s queued=%s",
            bool(current_item),
            len(queue_items),
        )
        return True
    except Exception as error:  # pylint: disable=broad-except
        logger.warning("failed to restore playback state backup: %s", error)
        return False
