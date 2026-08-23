"""Lightweight Redis-backed audit log for moderator and user actions."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from django.core.handlers.wsgi import WSGIRequest

from core import redis, user_manager

import pathlib
from django.conf import settings as conf
from redis.exceptions import RedisError

AUDIT_LOG_KEY = "audit-log:v1"
AUDIT_LOG_LIMIT = 5000
AUDIT_LOG_TTL_SECONDS = 7 * 24 * 60 * 60
AUDIT_LOG_FILE = pathlib.Path(conf.BASE_DIR) / "config" / "moderator_audit_log.jsonl"
AUDIT_LOG_FILE_LIMIT = 5000

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

    # Avoid rereading thousands of JSON lines for every user action. The DB is
    # authoritative; this file is only a bounded recovery fallback.
    try:
        if AUDIT_LOG_FILE.stat().st_size < 10 * 1024 * 1024:
            return
    except OSError:
        return

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
    identity = user_manager.client_identity(request) if request is not None else None
    browser_token = getattr(request, "furatic_browser_token", "") if request is not None else ""
    entry = {
        "ts": time.time(),
        "action": action,
        "actor": _actor_label(request),
        "actorRole": _actor_role(request),
        "ip": user_manager.get_client_ip(request) if request is not None else "",
        "codename": identity.codename if identity else "",
        "browserToken": browser_token[:8] if browser_token else "",
        "target": target,
        "songKey": song_key,
        "songTitle": song_title,
        "metadata": metadata or {},
    }

    try:
        pipe = redis.connection.pipeline()
        pipe.lpush(AUDIT_LOG_KEY, json.dumps(entry))
        pipe.ltrim(AUDIT_LOG_KEY, 0, AUDIT_LOG_LIMIT - 1)
        pipe.expire(AUDIT_LOG_KEY, AUDIT_LOG_TTL_SECONDS)
        pipe.execute()
    except RedisError:
        # PostgreSQL and JSONL remain authoritative if Redis is restarting.
        pass

    try:
        _append_file(entry)
    except OSError:
        pass
    try:
        from core.models import AuditEntry
        AuditEntry.objects.create(
            action=action, actor=entry["actor"], actor_role=entry["actorRole"],
            ip=entry["ip"], codename=entry["codename"], browser_token=entry["browserToken"],
            target=target, song_key=song_key, song_title=song_title, metadata=metadata or {},
        )
        # An event-sized rolling history: enough for thousands of actions, not indefinite retention.
        stale_ids = AuditEntry.objects.order_by("-created").values_list("id", flat=True)[5000:]
        AuditEntry.objects.filter(id__in=list(stale_ids)).delete()
    except Exception:  # migrations/database may not be ready during startup
        pass


def get_recent(limit: int = 120) -> List[Dict[str, Any]]:
    try:
        from core.models import AuditEntry
        rows = AuditEntry.objects.all()[:limit]
        if rows:
            return [{"ts": row.created.timestamp(), "action": row.action, "actor": row.actor,
                     "actorRole": row.actor_role, "ip": row.ip, "codename": row.codename,
                     "browserToken": row.browser_token, "target": row.target,
                     "songKey": row.song_key, "songTitle": row.song_title,
                     "metadata": row.metadata} for row in rows]
    except Exception:
        pass
    try:
        raw_entries = redis.connection.lrange(AUDIT_LOG_KEY, 0, max(0, limit - 1))
    except RedisError:
        raw_entries = []
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


def update_song_title(song_key: int, song_title: str) -> None:
    """Replace placeholder/query text with resolved metadata in persistent audit rows."""
    try:
        from core.models import AuditEntry

        AuditEntry.objects.filter(song_key=song_key).update(song_title=song_title)
    except Exception:
        pass
