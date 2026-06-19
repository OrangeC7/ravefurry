"""Lightweight Redis-backed audit log for moderator and user actions."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from django.core.handlers.wsgi import WSGIRequest

from core import redis, user_manager

import pathlib
from django.conf import settings as conf

AUDIT_LOG_KEY = "audit-log:v1"
AUDIT_LOG_LIMIT = 250
AUDIT_LOG_TTL_SECONDS = 7 * 24 * 60 * 60
AUDIT_LOG_FILE = pathlib.Path(conf.BASE_DIR) / "config" / "moderator_audit_log.jsonl"
AUDIT_LOG_FILE_LIMIT = 500

def _entry_identity(entry: Dict[str, Any]) -> tuple:
    return (
        entry.get("ts"),
        entry.get("action"),
        entry.get("actor"),
        entry.get("target"),
        entry.get("songKey"),
    )


def _append_file(entry: Dict[str, Any]) -> None:
    AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    with AUDIT_LOG_FILE.open("a", encoding="utf-8") as outfile:
        outfile.write(json.dumps(entry, sort_keys=True))
        outfile.write("\n")

    try:
        lines = AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    if len(lines) > AUDIT_LOG_FILE_LIMIT:
        AUDIT_LOG_FILE.write_text(
            "\n".join(lines[-AUDIT_LOG_FILE_LIMIT:]) + "\n",
            encoding="utf-8",
        )


def _load_file_entries(limit: int) -> List[Dict[str, Any]]:
    try:
        lines = AUDIT_LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    entries: List[Dict[str, Any]] = []
    for line in reversed(lines[-max(0, limit):]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(value, dict):
            entries.append(value)

    return entries

def _actor_label(request: Optional[WSGIRequest]) -> str:
    if request is None:
        return "system"

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        username = getattr(user, "get_username", lambda: "")() or getattr(user, "username", "")
        if username:
            return username

    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None)
    if session_key:
        return f"session:{session_key[:8]}"

    return "anonymous"


def _actor_role(request: Optional[WSGIRequest]) -> str:
    if request is None:
        return "system"

    user = getattr(request, "user", None)
    if user_manager.is_admin(user):
        return "admin"
    if user_manager.is_moderator(user):
        return "moderator"
    return "user"


def append(
    action: str,
    *,
    request: Optional[WSGIRequest] = None,
    target: str = "",
    song_key: Optional[int] = None,
    song_title: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    entry = {
        "ts": time.time(),
        "action": action,
        "actor": _actor_label(request),
        "actorRole": _actor_role(request),
        "ip": user_manager.get_client_ip(request) if request is not None else "",
        "target": target,
        "songKey": song_key,
        "songTitle": song_title,
        "metadata": metadata or {},
    }

    pipe = redis.connection.pipeline()
    pipe.lpush(AUDIT_LOG_KEY, json.dumps(entry))
    pipe.ltrim(AUDIT_LOG_KEY, 0, AUDIT_LOG_LIMIT - 1)
    pipe.expire(AUDIT_LOG_KEY, AUDIT_LOG_TTL_SECONDS)
    pipe.execute()

    try:
        _append_file(entry)
    except OSError:
        pass


def get_recent(limit: int = 120) -> List[Dict[str, Any]]:
    raw_entries = redis.connection.lrange(AUDIT_LOG_KEY, 0, max(0, limit - 1))
    entries: List[Dict[str, Any]] = []

    for raw in raw_entries:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue

        if isinstance(value, dict):
            entries.append(value)

    seen = {_entry_identity(entry) for entry in entries}

    for entry in _load_file_entries(limit):
        identity = _entry_identity(entry)
        if identity in seen:
            continue
        seen.add(identity)
        entries.append(entry)

    entries.sort(key=lambda entry: float(entry.get("ts") or 0), reverse=True)
    return entries[:limit]
