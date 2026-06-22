"""Next-up queue locking.

The song locked as "next up" is stored in Redis so it survives the planned web
child recycle but does not require a database migration.
"""

from __future__ import annotations

from typing import Iterable, Optional

from redis.exceptions import RedisError

from core import redis

NEXT_UP_LOCK_KEY = "furatic-next-up-locked-queue-key"


def get_locked_queue_key() -> Optional[int]:
    """Return the currently locked next-up queue key, if any."""
    try:
        raw_value = redis.connection.get(NEXT_UP_LOCK_KEY)
    except RedisError:
        return None

    if raw_value in (None, ""):
        return None

    try:
        locked_key = int(raw_value)
    except (TypeError, ValueError):
        clear_locked_queue_key()
        return None

    return locked_key if locked_key > 0 else None


def set_locked_queue_key(queue_key: int) -> Optional[int]:
    """Lock the given queue key as the next-up song."""
    try:
        queue_key = int(queue_key)
    except (TypeError, ValueError):
        clear_locked_queue_key()
        return None

    if queue_key <= 0:
        clear_locked_queue_key()
        return None

    try:
        redis.connection.set(NEXT_UP_LOCK_KEY, str(queue_key))
    except RedisError:
        return None

    return queue_key


def clear_locked_queue_key() -> None:
    """Clear the next-up lock."""
    try:
        redis.connection.delete(NEXT_UP_LOCK_KEY)
    except RedisError:
        return


def resolve_locked_queue_key(
    ordered_candidate_ids: Iterable[int],
    *,
    allow_new_lock: bool,
) -> Optional[int]:
    """Return the valid locked key or create one from the first candidate.

    Existing locks are honored even when allow_new_lock is False. This matters
    during the small transition window after the current song is deleted and
    before the locked queue song becomes CurrentSong.
    """
    candidate_ids = [int(candidate_id) for candidate_id in ordered_candidate_ids]
    candidate_set = set(candidate_ids)

    locked_key = get_locked_queue_key()
    if locked_key is not None:
        if locked_key in candidate_set:
            return locked_key

        clear_locked_queue_key()
        locked_key = None

    if not allow_new_lock or not candidate_ids:
        return None

    return set_locked_queue_key(candidate_ids[0])


def is_locked_queue_key(queue_key: int) -> bool:
    """Return whether the given queue key is currently locked as next up."""
    locked_key = get_locked_queue_key()
    try:
        return locked_key is not None and int(queue_key) == locked_key
    except (TypeError, ValueError):
        return False


def clear_if_locked(queue_key: int) -> None:
    """Clear the next-up lock if it points at the given queue key."""
    if is_locked_queue_key(queue_key):
        clear_locked_queue_key()
