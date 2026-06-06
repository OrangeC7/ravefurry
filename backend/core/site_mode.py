from core import redis

EVENT_MODE = "event"
CLOSING_MODE = "closing"
AFTER_HOURS_MODE = "afterhours"

VALID_MODES = {EVENT_MODE, CLOSING_MODE, AFTER_HOURS_MODE}


def get_mode() -> str:
    mode = str(redis.get("site_mode"))
    return mode if mode in VALID_MODES else EVENT_MODE


def set_mode(mode: str) -> str:
    normalized = mode if mode in VALID_MODES else EVENT_MODE
    redis.put("site_mode", normalized)
    return normalized


def is_afterhours() -> bool:
    return get_mode() == AFTER_HOURS_MODE


def is_closing() -> bool:
    return get_mode() == CLOSING_MODE


def accepts_song_requests() -> bool:
    return get_mode() == EVENT_MODE
