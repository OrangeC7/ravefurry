"""This module contains all public api endpoints."""
import re

from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from django.conf import settings as conf
from django.db import transaction
from core import audit_log, site_mode, user_manager
from core import models
from core.musiq import musiq, playback
from core.musiq.music_provider import ProviderError
from core.settings import storage


@csrf_exempt
@user_manager.tracked
def post_song(request: WSGIRequest) -> HttpResponse:
    """This endpoint is part of the API and exempt from CSRF checks.
    Shareberry uses this endpoint."""
    if site_mode.is_afterhours():
        return HttpResponseBadRequest("FURATIC is currently in After Hours mode.")

    query = request.POST.get("query")
    if not query:
        return HttpResponseBadRequest("No query to share.")

    requester_ip = user_manager.get_client_ip(request)
    identity = user_manager.client_identity(request)
    requester_token = identity.token_hash if identity else user_manager.normalize_session_key(request.session.session_key or "")
    match = re.search(r"(?P<url>https?://[^\s]+)", query)
    if match:
        query = match.group("url")

    try:
        providers = musiq.get_providers(query)
    except ProviderError as error:
        return HttpResponseBadRequest(str(error))
    provider = musiq.try_providers(
        request.session.session_key,
        providers,
        defer_enqueue=True,
    )
    if provider.error:
        return HttpResponseBadRequest(provider.error)

    queued_song = getattr(provider, "queued_song", None)
    if queued_song is None:
        return HttpResponseBadRequest(
            "Playlist requests are disabled; add up to 10 individual songs instead."
        )
    # Background work is deliberately deferred until the ownership and
    # priority assignment commits.
    with transaction.atomic():
        models.ClientIdentity.objects.select_for_update().get(pk=identity.pk)
        active_count = (
            musiq.queue.filter(requester_token=requester_token).count()
            + models.CurrentSong.objects.filter(requester_token=requester_token).count()
        )
        if active_count >= 10:
            try:
                playback.queue.remove(queued_song.id)
            except models.QueuedSong.DoesNotExist:
                pass
            return HttpResponseBadRequest("You may have up to 10 active songs at once.")
        queue_key = queued_song.id
        has_primary = (
            musiq.queue.filter(
                requester_token=requester_token,
                priority_tier="normal",
            ).exclude(id=queue_key).exists()
            or models.CurrentSong.objects.filter(
                requester_token=requester_token,
            ).exists()
        )
        musiq.queue.filter(id=queue_key).update(
            requester_token=requester_token,
            priority_tier="extra" if has_primary else "normal",
        )
        musiq.queue.rebalance_priorities()
    queue_key = queued_song.id
    user_manager.remember_requester_ip(requester_ip, queue_key, request.session.session_key or "")
    if storage.get("ip_checking"):
        user_manager.try_vote(requester_ip, queue_key, 1, record_activity=False)

    if (
        storage.get("color_indication") != storage.Privileges.nobody
        and request.session.session_key
    ):
        user_manager.register_song(request, queue_key)
        user_manager.register_vote(request, queue_key, 1)

    audit_log.append(
        "user_add_song",
        request=request,
        target="queue",
        song_key=queue_key,
        song_title=queued_song.displayname(),
    )
    musiq.update_state()
    provider.start_deferred_enqueue()
    return HttpResponse(provider.ok_message)


def version(request: WSGIRequest) -> HttpResponse:
    """Return the version of the running instance."""

    return HttpResponse(f"Raveberry version {conf.VERSION}")
