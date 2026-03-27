"""Runtime-only After Hours gate for public FURATIC pages."""
from django.http import HttpResponse
from django.shortcuts import redirect

from core import site_mode


_ALLOWED_PREFIXES = (
    "/afterhours/",
    "/admin/",
    "/api/site-mode/",
    "/static/",
    "/favicon.ico",
)


class AfterHoursModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if site_mode.is_afterhours() and not path.startswith(_ALLOWED_PREFIXES):
            if path.startswith("/ajax/") or path.startswith("/api/"):
                return HttpResponse(
                    "FURATIC is currently in After Hours mode.",
                    status=503,
                )
            return redirect("afterhours")

        return self.get_response(request)
