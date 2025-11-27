from __future__ import annotations

import logging

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from jwt import InvalidTokenError
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class CookieJWTAuthentication(BaseAuthentication):
    """Authenticate requests using access tokens stored in HttpOnly cookies."""

    www_authenticate_realm = "api"
    logger = logging.getLogger(__name__)

    def authenticate(self, request) -> tuple[AbstractBaseUser, None] | None:
        """Authenticate via JWT access token from cookies, returning the user or None."""
        base_request = self._ensure_request_cookies(request)
        access_cookie_name = getattr(settings, "ACCESS_COOKIE_NAME", "access_token")
        raw_cookie_header = request.META.get("HTTP_COOKIE", "")
        self._log_debug_start(request, raw_cookie_header)

        token, token_source = self._extract_token(
            request, base_request, access_cookie_name, raw_cookie_header
        )
        if not token:
            self._log_missing_token(request, raw_cookie_header)
            return None

        payload = self._decode_token(token, token_source, request)
        self._validate_access_token(payload)
        user = self._load_user(payload)
        self._log_success(request, token_source, user)
        return user, None

    def authenticate_header(self, request) -> str:
        return f'Bearer realm="{self.www_authenticate_realm}"'

    def _ensure_request_cookies(self, request):
        """Ensure the request has a COOKIES attribute and return the base request."""
        if hasattr(request, "COOKIES"):
            return getattr(request, "_request", None)

        base_request = getattr(request, "_request", None)
        if base_request is not None and hasattr(base_request, "COOKIES"):
            request.COOKIES = base_request.COOKIES
        else:
            request.COOKIES = {}
        return base_request

    def _log_debug_start(self, request, raw_cookie_header: str) -> None:
        """Log the initial cookie state when debugging is enabled."""
        if not settings.DEBUG:
            return

        self.logger.debug(
            "CookieJWTAuthentication.start path=%s cookie_keys=%s has_raw_cookie=%s",
            getattr(request, "path", ""),
            sorted(list(request.COOKIES.keys())),
            bool(raw_cookie_header),
        )

    def _extract_token(
        self, request, base_request, access_cookie_name: str, raw_cookie_header: str
    ) -> tuple[str | None, str | None]:
        """Pull the access token from request cookies or raw headers."""
        token = request.COOKIES.get(access_cookie_name)
        token_source = "request.COOKIES" if token else None

        if not token and base_request is not None and hasattr(base_request, "COOKIES"):
            token = base_request.COOKIES.get(access_cookie_name)
            token_source = "request._request.COOKIES" if token else None

        if not token and raw_cookie_header:
            token, token_source = self._parse_raw_cookie_header(
                access_cookie_name, raw_cookie_header
            )

        return token, token_source

    def _parse_raw_cookie_header(
        self, access_cookie_name: str, raw_cookie_header: str
    ) -> tuple[str | None, str | None]:
        """Manually parse the raw cookie header to recover skipped cookies."""
        for part in raw_cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            value = value.strip()
            if name == access_cookie_name and value:
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                return value, "HTTP_COOKIE"
        return None, None

    def _log_missing_token(self, request, raw_cookie_header: str) -> None:
        """Log missing token details when in debug mode."""
        if not settings.DEBUG:
            return

        self.logger.debug(
            "CookieJWTAuthentication.missing_token path=%s raw_cookie_present=%s",
            getattr(request, "path", ""),
            bool(raw_cookie_header),
        )

    def _decode_token(self, token: str, token_source: str | None, request) -> dict:
        """Decode the JWT token and raise AuthenticationFailed on errors."""
        try:
            return jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=["HS256"],
                leeway=getattr(settings, "JWT_LEEWAY", 0),
            )
        except InvalidTokenError as exc:
            if settings.DEBUG:
                self.logger.debug(
                    "CookieJWTAuthentication.decode_failed path=%s source=%s error=%s",
                    getattr(request, "path", ""),
                    token_source,
                    str(exc),
                )
            raise AuthenticationFailed("Invalid token.") from exc

    def _validate_access_token(self, payload: dict) -> None:
        """Ensure the decoded token is an access token."""
        if payload.get("type") != "access":
            raise AuthenticationFailed("Invalid token.")

    def _load_user(self, payload: dict) -> AbstractBaseUser:
        """Load the user referenced in the token payload."""
        user_model = get_user_model()
        try:
            return user_model.objects.get(pk=payload.get("user_id"))
        except user_model.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid token.") from exc

    def _log_success(self, request, token_source: str | None, user) -> None:
        """Log successful authentication when debugging is enabled."""
        if not settings.DEBUG:
            return

        self.logger.debug(
            "CookieJWTAuthentication.success path=%s source=%s user_id=%s",
            getattr(request, "path", ""),
            token_source,
            getattr(user, "pk", None),
        )
