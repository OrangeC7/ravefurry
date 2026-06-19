"""File-backed persistence for trusted moderator runtime settings."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from django.conf import settings as conf

PERSISTED_SETTING_KEYS = {
    "banned_ips",
    "whitelisted_ips",
    "ip_blocklist_sources",
    "ip_blocklist_bootstrap_done",
    "request_cooldown_seconds",
    "max_song_duration_seconds",
    "site_mode",
    "maintenance_restart_song_interval",
    "songs_since_maintenance_restart",
}

STATE_FILE = pathlib.Path(conf.BASE_DIR) / "config" / "moderator_runtime_state.json"


def _read_state() -> dict[str, Any]:
    try:
        with STATE_FILE.open("r", encoding="utf-8") as infile:
            data = json.load(infile)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


def _write_state(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as outfile:
        json.dump(data, outfile, indent=2, sort_keys=True)
        outfile.write("\n")


def persist_setting(key: str, value: Any) -> None:
    if key not in PERSISTED_SETTING_KEYS:
        return

    data = _read_state()
    data[key] = value
    _write_state(data)


def restore_settings() -> None:
    from core.settings import storage  # pylint: disable=import-outside-toplevel

    data = _read_state()
    for key in PERSISTED_SETTING_KEYS:
        if key in data:
            try:
                storage.put(key, data[key])
            except Exception as error:  # pylint: disable=broad-except
                logger.warning("failed to restore persisted setting %s: %s", key, error)
