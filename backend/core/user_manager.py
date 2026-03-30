"""This module manages and counts user accesses and handles permissions."""
import colorsys
import ipaddress
import random
import re
import secrets
import time
from ast import literal_eval
from functools import wraps
from typing import Callable, Iterable, Optional

from django.conf import settings as conf
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.contrib.sessions.models import Session
from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import timezone

from core import redis

# kick users after some time without any request
from core.lights import leds
from core.settings import storage
from core.settings.storage import Privileges
from core.util import extract_value

INACTIVITY_PERIOD = 600
MODERATOR_GROUP_NAME = "moderator"
QUEUE_SLOT_TTL_SECONDS = 7 * 24 * 60 * 60
REQUESTER_IP_TTL_SECONDS = 7 * 24 * 60 * 60
RECENT_VOTE_WINDOW_SECONDS = 10 * 60
RECENT_VOTE_TTL_SECONDS = RECENT_VOTE_WINDOW_SECONDS + 60
MAX_RECENT_DOWNVOTE_TRANSITIONS = 3
MIN_RECENT_UPVOTE_TRANSITIONS = 4


def _normalize_ip(value: str) -> str:
    """Normalize an IPv4/IPv6 address and drop any port / wrapper syntax."""
    if not value:
        return ""

    value = value.strip().strip('"').strip("'")
    if not value:
        return ""

    if value.lower().startswith("for="):
        value = value[4:]

    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]

    if "%" in value:
        value = value.split("%", 1)[0]

    if value.count(":") == 1 and "." in value:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            value = host

    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return ""


def _normalize_ip_collection(values: Iterable[str]) -> list[str]:
    normalized = {_normalize_ip(value) for value in values}
    normalized.discard("")
    return sorted(normalized)


def _parse_forwarded_header(value: str) -> str:
    if not value:
        return ""
    for candidate in value.split(","):
        normalized = _normalize_ip(candidate)
        if normalized:
            return normalized
    return ""


def _parse_rfc_forwarded_header(value: str) -> str:
    if not value:
        return ""
    matches = re.findall(
        r'for=(?:"?)(\[[^\]]+\]|[^;,"\s]+)',
        value,
        flags=re.IGNORECASE,
    )
    for candidate in matches:
        normalized = _normalize_ip(candidate)
        if normalized:
            return normalized
    return ""


def _extract_forwarded_ip(request: WSGIRequest) -> str:
    for header_name in conf.CLIENT_IP_HEADER_CANDIDATES:
        raw_value = request.META.get(header_name, "")
        if not raw_value:
            continue
        if header_name == "HTTP_FORWARDED":
            normalized = _parse_rfc_forwarded_header(raw_value)
        else:
            normalized = _parse_forwarded_header(raw_value)
        if normalized:
            return normalized
    return ""


def _trusted_proxy(remote_addr: str) -> bool:
    normalized = _normalize_ip(remote_addr)
    if not normalized:
        return False

    remote_ip = ipaddress.ip_address(normalized)

    for trusted in conf.TRUSTED_PROXY_IPS:
        trusted = str(trusted).strip()
        if not trusted:
            continue

        try:
            if "/" in trusted:
                if remote_ip in ipaddress.ip_network(trusted, strict=False):
                    return True
            else:
                if remote_ip == ipaddress.ip_address(trusted):
                    return True
        except ValueError:
            continue

    return False


def _resolve_client_ip(request: WSGIRequest) -> str:
    direct_ip = _normalize_ip(request.META.get("REMOTE_ADDR", ""))

    if direct_ip and _trusted_proxy(direct_ip):
        forwarded_ip = _extract_forwarded_ip(request)
        if forwarded_ip:
            return forwarded_ip

    return direct_ip


def has_controls(user) -> bool:
    """Determines whether the given user is allowed to control playback."""
    return is_admin(user)


def is_admin(user) -> bool:
    """Determines whether the given user is the admin."""
    return bool(getattr(user, "is_superuser", False))


def is_moderator(user) -> bool:
    """Determines whether the given user belongs to the moderator role."""
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.groups.filter(name=MODERATOR_GROUP_NAME).exists())


def can_moderate(user) -> bool:
    """Determines whether the given user can access moderator tools."""
    return is_admin(user) or is_moderator(user)


def moderator_required(
    func: Callable[[WSGIRequest], HttpResponse]
) -> Callable[[WSGIRequest], HttpResponse]:
    """Require an authenticated moderator or admin for the wrapped view."""

    def _decorator(request: WSGIRequest, *args, **kwargs) -> HttpResponse:
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_moderate(request.user):
            return HttpResponseForbidden("Moderator access required")
        return func(request, *args, **kwargs)

    return wraps(func)(_decorator)


def has_secret_controls(request: WSGIRequest) -> bool:
    """Determines whether this session was unlocked via the secret full-control route."""
    return bool(
        getattr(request, "session", None) and request.session.get("secret_controls")
    )


def has_privilege(user, privilege: Privileges):
    if privilege == Privileges.everybody:
        return True
    if privilege == Privileges.mod and can_moderate(user):
        return True
    if privilege == Privileges.admin and is_admin(user):
        return True
    return False


def ensure_builtin_moderator(
    password: Optional[str] = None, rotate_if_unset: bool = False
) -> tuple[str, str]:
    """Create/update the built-in moderator account.

    - If an explicit password is provided, use it and return it.
    - Otherwise, if FURATIC_MOD_PASSWORD is configured, use that.
    - Otherwise, rotate the password only when rotate_if_unset is True.
    """
    from django.contrib.auth.models import Group

    UserModel = get_user_model()
    username = conf.FURATIC_MOD_USERNAME
    configured_password = password
    if configured_password is None:
        configured_password = getattr(conf, "FURATIC_MOD_PASSWORD", "")
    configured_password = (configured_password or "").strip()

    returned_password = ""

    group, _ = Group.objects.get_or_create(name=MODERATOR_GROUP_NAME)
    user, _ = UserModel.objects.get_or_create(
        **{UserModel.USERNAME_FIELD: username}
    )

    user.is_active = True
    if hasattr(user, "is_staff"):
        user.is_staff = False
    if hasattr(user, "is_superuser"):
        user.is_superuser = False

    if configured_password:
        user.set_password(configured_password)
        returned_password = configured_password if password is not None else ""
    elif rotate_if_unset:
        returned_password = secrets.token_urlsafe(18)
        user.set_password(returned_password)

    user.save()
    user.groups.add(group)

    return username, returned_password


def _banned_ips_storage_key() -> str:
    return str(storage.get("banned_ips"))


def get_banned_ips() -> list[str]:
    """Return the persisted banned IP list."""
    raw_value = _banned_ips_storage_key()
    return _normalize_ip_collection(re.split(r"[\s,]+", raw_value))


def _store_banned_ips(ips: Iterable[str]) -> None:
    storage.put("banned_ips", "\n".join(_normalize_ip_collection(ips)))


def is_banned_ip(ip: str) -> bool:
    normalized = _normalize_ip(ip)
    return bool(normalized and normalized in set(get_banned_ips()))


def ban_ip(ip: str) -> str:
    """Persistently ban the given IP and return the normalized value."""
    normalized = _normalize_ip(ip)
    if not normalized:
        raise ValueError("Invalid IP address")
    banned_ips = set(get_banned_ips())
    banned_ips.add(normalized)
    _store_banned_ips(banned_ips)
    return normalized


def unban_ip(ip: str) -> str:
    """Remove the given IP from the persistent ban list."""
    normalized = _normalize_ip(ip)
    if not normalized:
        raise ValueError("Invalid IP address")
    banned_ips = set(get_banned_ips())
    banned_ips.discard(normalized)
    _store_banned_ips(banned_ips)
    return normalized


def _queue_slot_key(request_ip: str) -> str:
    return f"queue-slot:{request_ip}"


def _requester_ip_key(queue_key: int) -> str:
    return f"queue-requester:{queue_key}"


def remember_requester_ip(request_ip: str, queue_key: int) -> None:
    """Store requester IP metadata for moderator tooling and exports."""
    normalized = _normalize_ip(request_ip)
    if not normalized:
        return
    redis.connection.set(
        _requester_ip_key(queue_key),
        normalized,
        ex=REQUESTER_IP_TTL_SECONDS,
    )


def get_song_requester_ip(queue_key: int) -> str:
    """Return the requester IP stored for the given queue / current-song key."""
    return redis.connection.get(_requester_ip_key(queue_key)) or ""


def ip_has_active_queue_slot(request_ip: str) -> bool:
    """Return whether the given IP currently owns a queued song slot."""
    normalized = _normalize_ip(request_ip)
    if not normalized:
        return False
    return bool(redis.connection.get(_queue_slot_key(normalized)))


def claim_queue_slot(request_ip: str, queue_key: int) -> bool:
    """Claim the single queued-song slot for the given IP.

    Returns False if the IP already has another song in the queue.
    """
    normalized = _normalize_ip(request_ip)
    if not normalized:
        return True

    remember_requester_ip(normalized, queue_key)
    slot_key = _queue_slot_key(normalized)
    requester_key = _requester_ip_key(queue_key)
    allowed = True

    def check_entry(pipe) -> None:
        nonlocal allowed
        existing = pipe.get(slot_key)
        if existing not in (None, "", str(queue_key)):
            allowed = False
            return
        allowed = True
        pipe.multi()
        pipe.set(slot_key, queue_key, ex=QUEUE_SLOT_TTL_SECONDS)
        pipe.set(requester_key, normalized, ex=REQUESTER_IP_TTL_SECONDS)

    redis.connection.transaction(check_entry, slot_key)
    return allowed


def release_queue_slot_for_song(queue_key: int) -> None:
    """Release the active queue slot owned by the requester of the given song."""
    requester_ip = get_song_requester_ip(queue_key)
    if not requester_ip:
        return

    slot_key = _queue_slot_key(requester_ip)
    current_value = redis.connection.get(slot_key)
    pipe = redis.connection.pipeline()
    if current_value == str(queue_key):
        pipe.delete(slot_key)
    pipe.expire(_requester_ip_key(queue_key), REQUESTER_IP_TTL_SECONDS)
    pipe.execute()


def clear_queue_slots() -> None:
    """Clear all active single-song queue ownership locks."""
    keys = list(redis.connection.scan_iter(match="queue-slot:*") )
    if keys:
        redis.connection.delete(*keys)


def update_user_count() -> None:
    """Go through all recent requests and delete those that were too long ago."""
    now = time.time()
    last_requests = redis.get("last_requests")
    for key, value in list(last_requests.items()):
        if now - value >= INACTIVITY_PERIOD:
            del last_requests[key]
            redis.put("last_requests", last_requests)
    redis.put("last_user_count_update", now)


def get_count() -> int:
    """Returns the number of currently active users.
    Updates this number after an intervals since the last update."""
    if time.time() - redis.get("last_user_count_update") >= 60:
        update_user_count()
    return len(redis.get("last_requests"))


def partymode_enabled() -> bool:
    """Determines whether partymode is enabled,
    based on the number of currently active users."""
    return len(redis.get("last_requests")) >= storage.get("people_to_party")


import logging

logger = logging.getLogger(__name__)

from typing import Any, Callable, Iterable, Mapping, Optional


def _extract_forwarded_ip(meta: Mapping[str, str]) -> str:
    for header_name in conf.CLIENT_IP_HEADER_CANDIDATES:
        raw_value = meta.get(header_name, "")
        if not raw_value:
            continue
        if header_name == "HTTP_FORWARDED":
            normalized = _parse_rfc_forwarded_header(raw_value)
        else:
            normalized = _parse_forwarded_header(raw_value)
        if normalized:
            return normalized
    return ""


def _resolve_client_ip_from_meta(meta: Mapping[str, str], remote_addr: str) -> str:
    direct_ip = _normalize_ip(remote_addr)

    if direct_ip and _trusted_proxy(direct_ip):
        forwarded_ip = _extract_forwarded_ip(meta)
        if forwarded_ip:
            return forwarded_ip

    return direct_ip


def get_client_ip(request: WSGIRequest) -> str:
    return _resolve_client_ip_from_meta(
        request.META,
        request.META.get("REMOTE_ADDR", ""),
    ) or ""


def get_client_ip_from_scope(scope: Mapping[str, Any]) -> str:
    meta: dict[str, str] = {}

    for key, value in scope.get("headers", []):
        if isinstance(key, bytes):
            key = key.decode("latin1")
        if isinstance(value, bytes):
            value = value.decode("latin1")
        meta["HTTP_" + key.upper().replace("-", "_")] = value

    client = scope.get("client")
    remote_addr = ""
    if client and len(client) >= 1:
        remote_addr = str(client[0])

    return _resolve_client_ip_from_meta(meta, remote_addr) or ""


def _recent_downvote_key(request_ip: str) -> str:
    return f"recent-downvotes:{request_ip}"


def _recent_upvote_key(request_ip: str) -> str:
    return f"recent-upvotes:{request_ip}"


def _trim_recent_vote_activity(request_ip: str, now: float) -> tuple[str, str, float]:
    down_key = _recent_downvote_key(request_ip)
    up_key = _recent_upvote_key(request_ip)
    cutoff = now - RECENT_VOTE_WINDOW_SECONDS

    pipe = redis.connection.pipeline()
    pipe.zremrangebyscore(down_key, "-inf", cutoff)
    pipe.zremrangebyscore(up_key, "-inf", cutoff)
    pipe.expire(down_key, RECENT_VOTE_TTL_SECONDS)
    pipe.expire(up_key, RECENT_VOTE_TTL_SECONDS)
    pipe.execute()

    return down_key, up_key, cutoff


def _can_add_recent_downvote(
    request_ip: str, queue_key: int, previous_vote: int, now: float
) -> bool:
    down_key, up_key, cutoff = _trim_recent_vote_activity(request_ip, now)
    recent_downvotes = redis.connection.zcount(down_key, cutoff, "+inf")
    recent_upvotes = redis.connection.zcount(up_key, cutoff, "+inf")

    # If this vote flips a recent upvote on the same song into a downvote,
    # that song should no longer count as balancing upvote activity.
    if previous_vote > 0 and redis.connection.zscore(up_key, str(queue_key)) is not None:
        recent_upvotes -= 1

    return (
        recent_downvotes < MAX_RECENT_DOWNVOTE_TRANSITIONS
        or recent_upvotes >= MIN_RECENT_UPVOTE_TRANSITIONS
    )


def _sync_recent_vote_activity(
    request_ip: str, queue_key: int, new_vote: int, now: float, record_activity: bool
) -> None:
    if not record_activity:
        return

    down_key, up_key, cutoff = _trim_recent_vote_activity(request_ip, now)
    member = str(queue_key)
    pipe = redis.connection.pipeline()

    if new_vote < 0:
        pipe.zadd(down_key, {member: now})
        pipe.zrem(up_key, member)
    elif new_vote > 0:
        pipe.zadd(up_key, {member: now})
        pipe.zrem(down_key, member)
    else:
        pipe.zrem(down_key, member)
        pipe.zrem(up_key, member)

    pipe.zremrangebyscore(down_key, "-inf", cutoff)
    pipe.zremrangebyscore(up_key, "-inf", cutoff)
    pipe.expire(down_key, RECENT_VOTE_TTL_SECONDS)
    pipe.expire(up_key, RECENT_VOTE_TTL_SECONDS)
    pipe.execute()


def try_vote(
    request_ip: str, queue_key: int, amount: int, record_activity: bool = True
) -> bool:
    """If the user can not vote any more for the song into the given direction, return False.
    Otherwise, perform the vote and returns True."""
    normalized = _normalize_ip(request_ip)
    if not normalized:
        return True

    # Votes are stored as individual (who, what) tuples in redis.
    # A mapping who -> [what, ...] is not used,
    # because each modification would require deserialization and subsequent serialization.
    # Without such a mapping we cannot easily find all votes belonging to a session key,
    # which would be required to update the view of a user whose client-votes got desynced.
    # This should never happen during normal usage, so we optimize for our main use case:
    # looking up whether a single user voted for a single song, which is constant with tuples.
    # Since this feature indexes by the request IP and not the session_key,
    # it can not share its data structure with the votes for the color indicators.
    entry = str((normalized, queue_key))
    timestamp_key = f"vote-ts:{normalized}:{queue_key}"
    allowed = True
    cooldown_seconds = float(storage.get("vote_change_cooldown_seconds"))
    now = time.time()
    previous_vote = 0
    new_vote = 0

    # redis transaction: https://github.com/Redis/redis-py#pipelines
    def check_entry(pipe) -> None:
        nonlocal allowed, previous_vote, new_vote
        vote = pipe.get(entry)
        last_change = pipe.get(timestamp_key)

        if (
            cooldown_seconds > 0
            and last_change is not None
            and now - float(last_change) < cooldown_seconds
        ):
            allowed = False
            return

        previous_vote = 0 if vote is None else int(vote)
        new_vote = previous_vote + amount

        if new_vote < -1 or new_vote > 1:
            allowed = False
            return

        # Only limit transitions into a real downvote. Upvotes and clearing votes stay untouched.
        if (
            record_activity
            and previous_vote >= 0
            and new_vote < 0
            and not _can_add_recent_downvote(normalized, queue_key, previous_vote, now)
        ):
            allowed = False
            return

        allowed = True
        # expire these entries to avoid accumulation over long runtimes.
        pipe.multi()
        pipe.set(entry, new_vote, ex=24 * 60 * 60)
        pipe.set(timestamp_key, now, ex=24 * 60 * 60)

    redis.connection.transaction(check_entry, entry, timestamp_key)

    if allowed:
        _sync_recent_vote_activity(
            normalized, queue_key, new_vote, now, record_activity
        )

    return allowed


def _get_next_color():
    with transaction.atomic():
        next_index = storage.get("next_color_index")
        storage.put("next_color_index", next_index + 1)

    offset = storage.get("color_offset")

    hue = offset + next_index * (137.508 / 360)  # approximation for the golden angle
    color = colorsys.hsv_to_rgb(hue, 0.4, 1)

    return "#%02x%02x%02x" % tuple(round(v * 255) for v in color)


def color_of(session_key: str) -> Optional[str]:
    if not session_key or storage.get("color_indication") == storage.Privileges.nobody:
        return None
    # no transaction because this is called many times and at worst race conditions would result
    # in a different color being set
    color = redis.connection.get("color-" + session_key)
    if color is None:
        color = _get_next_color()
    # TODO: this is lost on server restart.
    # maybe store the color client side so it can be recovered?
    redis.connection.set("color-" + session_key, color, ex=24 * 60 * 60)
    return color


def register_song(request: WSGIRequest, queue_key: int) -> None:
    # For each song, identified by its queue_key, the following information is stored:
    # (<session_key>, {<session_key>: <vote>, …})
    # This requires a session_key, thus it can only be used in @tracked functions
    session_key = request.session.session_key

    key = f"engagement-{queue_key}"

    def update_entry(pipe) -> None:
        value = pipe.get(key)
        engagement = None if value is None else literal_eval(value)
        if engagement is None:
            engagement = (None, {})
        _, votes = engagement
        # expire these entries to avoid accumulation over long runtimes.
        pipe.multi()
        pipe.set(key, str((session_key, votes)), ex=24 * 60 * 60)

    redis.connection.transaction(update_entry, key)


def register_vote(request: WSGIRequest, queue_key: int, amount: int) -> None:
    session_key = request.session.session_key

    key = f"engagement-{queue_key}"

    def update_entry(pipe) -> None:
        value = pipe.get(key)
        engagement = None if value is None else literal_eval(value)
        if engagement is None:
            engagement = (None, {})
        requested_by, votes = engagement
        if session_key in votes:
            votes[session_key] += amount
        else:
            votes[session_key] = amount
        # clamp votes to [-1,1]. This helps recovering from desyncs after redis was cleared
        # but client votes are still locked in
        votes[session_key] = max(-1, min(1, votes[session_key]))
        if votes[session_key] == 0:
            del votes[session_key]
        # expire these entries to avoid accumulation over long runtimes.
        pipe.multi()
        pipe.set(key, str((requested_by, votes)), ex=24 * 60 * 60)

    redis.connection.transaction(update_entry, key)


def set_user_color(request: WSGIRequest) -> HttpResponse:
    """Updates the color assigned to the session of the request."""
    from core.musiq import musiq

    color, response = extract_value(request.POST)
    if not color or not re.match(r"^#[0-9a-f]{6}$", color):
        return HttpResponseBadRequest()
    session_key = request.session.session_key
    redis.connection.set("color-" + session_key, color, ex=24 * 60 * 60)
    musiq.update_state()
    return response


def tracked(
    func: Callable[[WSGIRequest], HttpResponse]
) -> Callable[[WSGIRequest], HttpResponse]:
    """A decorator that stores the last access for every connected ip
    so the number of active users can be determined."""

    def _decorator(request: WSGIRequest) -> HttpResponse:
        # create a sessions if none exists (necessary for anonymous users)
        if not request.session or not request.session.session_key:
            # if there are no active sessions (= this is the first one)
            # reset the color index and choose a new offset.
            active_sessions = Session.objects.filter(
                expire_date__gte=timezone.now()
            ).count()
            if active_sessions == 0:
                storage.put("color_offset", random.random())
                storage.put("next_color_index", 0)

            request.session.save()

        request_ip = get_client_ip(request)
        activity_key = request_ip or request.session.session_key or "anonymous"
        last_requests = redis.get("last_requests")
        last_requests[activity_key] = time.time()
        redis.put("last_requests", last_requests)

        def check():
            active = redis.get("active_requests")
            if active > 0:
                leds.enable_act_led()
            else:
                leds.disable_act_led()

        redis.connection.incr("active_requests")
        check()
        response = func(request)
        redis.connection.decr("active_requests")
        check()

        return response

    return wraps(func)(_decorator)
