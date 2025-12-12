from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest


def normalize_email(raw_email: Any) -> str:
    """Strip surrounding whitespace and lowercase the email."""
    return str(raw_email).strip().lower()


def build_frontend_url(
    action: Literal["activate", "reset"],
    *,
    uidb64: str,
    token: str,
) -> str:
    """Build absolute URLs for the activation/reset flows pointing at the frontend."""
    base = resolve_auth_frontend_base().rstrip("/")
    page_map = {
        "activate": "pages/auth/activate.html",
        "reset": "pages/auth/confirm_password.html",
    }
    target_page = page_map[action]
    query = urlencode({"uid": uidb64, "token": token})
    return f"{base}/{target_page}?{query}"


def build_logo_url(request: HttpRequest | None = None) -> str:
    """Create an absolute URL to the logo asset.

    Preference order:
    1. request.build_absolute_uri when a request is available
    2. PUBLIC_MEDIA_BASE fallback (which already points to an externally reachable host)
    3. Frontend domain as a final fallback
    """
    media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    relative_logo = f"{media_url.rstrip('/')}/logo/logo_icon.svg"

    if request is not None:
        return request.build_absolute_uri(relative_logo)

    public_media_base = getattr(settings, "PUBLIC_MEDIA_BASE", "")
    base = public_media_base or _resolve_frontend_base()
    base = _ensure_scheme(base).rstrip("/")
    return f"{base}{relative_logo}"


def _resolve_frontend_base() -> str:
    candidates = [
        getattr(settings, "PUBLIC_FRONTEND_BASE", None),
        getattr(settings, "FRONTEND_DOMAIN", None),
        getattr(settings, "FRONTEND_BASE_URL", None),
    ]
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if candidate:
            return _ensure_scheme(candidate)
    return "http://localhost:3000"


def resolve_auth_frontend_base() -> str:
    """Resolve the base URL used for auth-related email links (activation/reset)."""
    candidates = [
        getattr(settings, "PUBLIC_FRONTEND_BASE", None),
        getattr(settings, "DEV_FRONTEND_ORIGIN", None),
        getattr(settings, "FRONTEND_DOMAIN", None),
        getattr(settings, "FRONTEND_BASE_URL", None),
        getattr(settings, "PUBLIC_API_BASE", None),
    ]
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        base = _ensure_scheme(candidate).rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        return base
    return "https://videoflix.sascha-gerspach.de"


def _ensure_scheme(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "http://localhost:3000"
    if url.startswith(("http://", "https://")):
        return url
    return f"http://{url.lstrip('/')}"
