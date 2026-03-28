"""This module contains all public api endpoints."""
import re

from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from django.conf import settings as conf
from core import site_mode, user_manager
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
    if storage.get("ip_checking") and user_manager.ip_has_active_queue_slot(requester_ip):
        return HttpResponseBadRequest("This IP address already has a song in the queue.")

    match = re.search(r"(?P<url>https?://[^\s]+)", query)
    if match:
        query = match.group("url")

    try:
        providers = musiq.get_providers(query)
    except ProviderError as error:
        return HttpResponseBadRequest(str(error))
    provider = musiq.try_providers(request.session.session_key, providers)
    if provider.error:
        return HttpResponseBadRequest(provider.error)

    queued_song = getattr(provider, "queued_song", None)
    if queued_song is not None:
        queue_key = queued_song.id
        user_manager.remember_requester_ip(requester_ip, queue_key)
        if storage.get("ip_checking"):
            if not user_manager.claim_queue_slot(requester_ip, queue_key):
                try:
                    playback.queue.remove(queue_key)
                except Exception:  # pylint: disable=broad-except
                    musiq.queue.filter(id=queue_key).delete()
                return HttpResponseBadRequest(
                    "This IP address already has a song in the queue."
                )
            user_manager.try_vote(requester_ip, queue_key, 1)

        if (
            storage.get("color_indication") != storage.Privileges.nobody
            and request.session.session_key
        ):
            user_manager.register_song(request, queue_key)
            user_manager.register_vote(request, queue_key, 1)

    return HttpResponse(provider.ok_message)


def version(request: WSGIRequest) -> HttpResponse:
    """Return the version of the running instance."""

    return HttpResponse(f"Raveberry version {conf.VERSION}")
