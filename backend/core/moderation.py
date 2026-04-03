"""Custom moderator dashboard and APIs for FURATIC."""
from __future__ import annotations

from typing import Any, Dict, List

from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import audit_log, base, ip_screening, models, site_mode, user_manager
from core.musiq import controller as musiq_controller, musiq, playback, song_utils
from core.settings import storage


VOTING_INTERACTIVITIES = {
    storage.Interactivity.upvotes_only,
    storage.Interactivity.full_voting,
}


def _queue_queryset():
    return musiq.ordered_queue_queryset()


def _serialize_song(song) -> Dict[str, Any]:
    queue_key = getattr(song, "queue_key", None) or getattr(song, "id")
    return {
        "queueKey": queue_key,
        "title": song.title,
        "artist": song.artist,
        "displayName": song.displayname(),
        "votes": getattr(song, "votes", 0),
        "duration": song.duration,
        "durationFormatted": song_utils.format_seconds(song.duration),
        "manuallyRequested": getattr(song, "manually_requested", False),
        "requesterIp": user_manager.get_song_requester_ip(queue_key),
    }


def _state_payload() -> Dict[str, Any]:
    try:
        current_song = models.CurrentSong.objects.get()
        current_payload = _serialize_song(current_song)
    except models.CurrentSong.DoesNotExist:
        current_payload = None

    queue_payload = [_serialize_song(song) for song in _queue_queryset()]
    return {
        "mode": site_mode.get_mode(),
        "currentSong": current_payload,
        "queue": queue_payload,
        "bannedIps": user_manager.get_banned_ips(),
        "auditLog": audit_log.get_recent(120),
        "blocklists": ip_screening.list_blocklists(),
        "ipIntel": ip_screening.get_runtime_state(),
    }


@user_manager.moderator_required
def dashboard(request: WSGIRequest) -> HttpResponse:
    """Render the moderator dashboard."""
    context = base.context(request)
    context.update(
        {
            "moderator_state_url": reverse("moderator-state"),
            "moderator_remove_song_url": reverse("moderator-remove-song"),
            "moderator_skip_current_url": reverse("moderator-skip-current"),
            "moderator_ban_ip_url": reverse("moderator-ban-ip"),
            "moderator_unban_ip_url": reverse("moderator-unban-ip"),
            "moderator_site_mode_url": reverse("moderator-site-mode"),
            "moderator_add_blocklist_url": reverse("moderator-add-blocklist"),
            "moderator_rename_blocklist_url": reverse("moderator-rename-blocklist"),
            "moderator_remove_blocklist_url": reverse("moderator-remove-blocklist"),
        }
    )
    return render(request, "moderator.html", context)


@require_GET
@user_manager.moderator_required
def state(_request: WSGIRequest) -> HttpResponse:
    """Return moderator state for polling / refreshes."""
    return JsonResponse(_state_payload())


@require_POST
@user_manager.moderator_required
def remove_song(request: WSGIRequest) -> HttpResponse:
    """Remove a song from the queue by queue key."""
    key = request.POST.get("key")
    if not key:
        return HttpResponseBadRequest("Missing queue key")
    try:
        removed = playback.queue.remove(int(key))
        audit_log.append(
            "moderator_remove_song",
            request=request,
            target="queue",
            song_key=int(key),
            song_title=removed.displayname(),
        )
        if not removed.manually_requested:
            playback.handle_autoplay(removed.external_url or removed.title)
        else:
            playback.handle_autoplay()
    except models.QueuedSong.DoesNotExist:
        return HttpResponseBadRequest("Song does not exist")
    musiq.update_state()
    return JsonResponse(_state_payload())

@require_POST
@user_manager.moderator_required
def skip_current_song(_request: WSGIRequest) -> HttpResponse:
    """Skip the currently playing song."""
    musiq_controller._skip()
    audit_log.append("moderator_skip_current", request=_request, target="current-song")
    musiq.update_state()
    return JsonResponse(_state_payload())

@require_POST
@user_manager.moderator_required
def ban_ip(request: WSGIRequest) -> HttpResponse:
    """Ban a requester IP directly or via a queue key."""
    ip = request.POST.get("ip", "")
    queue_key = request.POST.get("queue_key", "")

    if not ip and queue_key:
        try:
            ip = user_manager.get_song_requester_ip(int(queue_key))
        except ValueError:
            ip = ""

    if not ip:
        return HttpResponseBadRequest("No IP address available")

    try:
        normalized = user_manager.ban_ip(ip)
        audit_log.append(
            "moderator_ban_ip",
            request=request,
            target=normalized,
            metadata={"queueKey": queue_key or ""},
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return JsonResponse({"ip": normalized, "bannedIps": user_manager.get_banned_ips()})


@require_POST
@user_manager.moderator_required
def unban_ip(request: WSGIRequest) -> HttpResponse:
    """Unban a requester IP."""
    ip = request.POST.get("ip", "")
    if not ip:
        return HttpResponseBadRequest("Missing IP address")

    try:
        normalized = user_manager.unban_ip(ip)
        audit_log.append("moderator_unban_ip", request=request, target=normalized)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return JsonResponse({"ip": normalized, "bannedIps": user_manager.get_banned_ips()})


@require_POST
@user_manager.moderator_required
def add_blocklist(request: WSGIRequest) -> HttpResponse:
    """Upload or register a new IPv4 blocklist file."""
    try:
        blocklist = ip_screening.add_blocklist(
            name=request.POST.get("name", ""),
            separator=request.POST.get("separator", "auto"),
            entry_type=request.POST.get("entry_type", "auto"),
            uploaded_file=request.FILES.get("file"),
            source_url=request.POST.get("source_url", ""),
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    audit_log.append(
        "moderator_add_blocklist",
        request=request,
        target=blocklist["name"],
        metadata={
            "id": blocklist["id"],
            "entryCount": blocklist["entryCount"],
            "sourceKind": blocklist["sourceKind"],
        },
    )
    return JsonResponse(_state_payload())


@require_POST
@user_manager.moderator_required
def rename_blocklist(request: WSGIRequest) -> HttpResponse:
    """Rename a configured blocklist entry."""
    source_id = request.POST.get("id", "")
    new_name = request.POST.get("name", "")

    try:
        updated = ip_screening.rename_blocklist(source_id, new_name)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    audit_log.append(
        "moderator_rename_blocklist",
        request=request,
        target=updated["name"],
        metadata={"id": updated["id"]},
    )
    return JsonResponse(_state_payload())


@require_POST
@user_manager.moderator_required
def remove_blocklist(request: WSGIRequest) -> HttpResponse:
    """Remove a configured blocklist entry."""
    source_id = request.POST.get("id", "")
    if not source_id:
        return HttpResponseBadRequest("Missing blocklist id")

    try:
        removed_name = ip_screening.remove_blocklist(source_id)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    audit_log.append(
        "moderator_remove_blocklist",
        request=request,
        target=removed_name,
        metadata={"id": source_id},
    )
    return JsonResponse(_state_payload())

@require_POST
@user_manager.moderator_required
def set_site_mode(request: WSGIRequest) -> HttpResponse:
    """Switch between event mode and after-hours mode."""
    mode = request.POST.get("mode", "")
    if mode not in site_mode.VALID_MODES:
        return HttpResponseBadRequest("Invalid site mode")

    selected_mode = site_mode.set_mode(mode)
    audit_log.append("moderator_set_site_mode", request=request, target=selected_mode)
    if selected_mode == site_mode.AFTER_HOURS_MODE:
        playback.request_operator_command("pause_for_afterhours")
    else:
        playback.request_operator_command("resume_from_afterhours")

    return JsonResponse({"mode": selected_mode})
