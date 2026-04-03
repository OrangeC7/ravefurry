"""Runtime-only FURATIC middleware for bans, IP screening, and After Hours gating."""
from django.http import HttpResponse
from django.shortcuts import redirect
import logging

from core import audit_log, ip_screening, site_mode, user_manager

logger = logging.getLogger(__name__)

_BAN_ALWAYS_ALLOWED_PREFIXES = (
    "/static/",
    "/favicon.ico",
)

_MODERATOR_RECOVERY_PREFIXES = (
    "/admin/",
    "/moderator/",
    "/api/moderator/",
    "/accounts/",
    "/login/",
    "/logout/",
    "/logged-in/",
)

_ALLOWED_PREFIXES = (
    "/afterhours/",
    "/admin/",
    "/moderator/",
    "/api/moderator/",
    "/api/site-mode/",
    "/accounts/",
    "/login/",
    "/logout/",
    "/logged-in/",
    "/static/",
    "/favicon.ico",
)


def _can_bypass_ban(path, request) -> bool:
    if path.startswith(_BAN_ALWAYS_ALLOWED_PREFIXES):
        return True
    if path.startswith(_MODERATOR_RECOVERY_PREFIXES) and user_manager.can_moderate(
        getattr(request, "user", None)
    ):
        return True
    return False


def _ban_response(reason: str = "") -> HttpResponse:
    if reason in {"api", "blocklist"}:
        message = (
            'Connections from VPNs, proxies, relays, or datacenter IPs are not allowed here. '
            'Please disconnect from your VPN or proxy and try again. '
            'If you believe this is in error, please contact us on Discord: '
            '<a href="https://discord.gg/Sr4pAFa8E5" target="_blank" rel="noopener noreferrer">Join our Discord</a>'
        )
    else:
        message = (
            'This IP address is banned. If you believe this is in error, please contact us on Discord: '
            '<a href="https://discord.gg/Sr4pAFa8E5" target="_blank" rel="noopener noreferrer">Join our Discord</a>'
        )

    return HttpResponse(message, status=403)


class ClientIpBanMiddleware:
    """Resolve the real client IP, log requests, and block banned traffic."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.client_ip = user_manager.get_client_ip(request)
        path = request.path
        screening = None

        if request.client_ip and user_manager.is_banned_ip(request.client_ip):
            screening = {
                "blocked": True,
                "reason": "manual-ban",
                "cached": True,
                "newlyBlocked": False,
            }
        elif request.client_ip and user_manager.is_whitelisted_ip(request.client_ip):
            screening = {
                "blocked": False,
                "reason": "whitelist",
                "cached": True,
                "newlyBlocked": False,
            }
        elif request.client_ip:
            screening = ip_screening.evaluate_ip(
                request.client_ip,
                allow_api=not site_mode.is_afterhours(),
            )
            if screening.get("blocked") and screening.get("newlyBlocked"):
                audit_log.append(
                    "ip_screen_block",
                    request=request,
                    target=request.client_ip,
                    metadata={
                        "reason": screening.get("reason", ""),
                        "source": screening.get("source", {}),
                        "api": screening.get("api", {}),
                    },
                )

        if screening and screening.get("blocked"):
            if _can_bypass_ban(path, request):
                response = self.get_response(request)
            else:
                response = _ban_response(screening.get("reason", ""))
        else:
            response = self.get_response(request)

        logger.info(
            "HTTP %s %s %s [%s]",
            request.method,
            request.get_full_path(),
            response.status_code,
            request.client_ip or request.META.get("REMOTE_ADDR", ""),
        )
        return response


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
