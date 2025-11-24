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
        # Force DRF to always call this method, regardless of Accept header
        if not hasattr(request, "COOKIES"):
            base_request = getattr(request, "_request", None)
            if base_request is not None and hasattr(base_request, "COOKIES"):
                request.COOKIES = base_request.COOKIES
            else:
                request.COOKIES = {}
        else:
            base_request = getattr(request, "_request", None)

        access_cookie_name = getattr(settings, "ACCESS_COOKIE_NAME", "access_token")
        raw_cookie_header = request.META.get("HTTP_COOKIE", "")
        if settings.DEBUG:
            self.logger.debug(
                "CookieJWTAuthentication.start path=%s cookie_keys=%s has_raw_cookie=%s",
                getattr(request, "path", ""),
                sorted(list(request.COOKIES.keys())),
                bool(raw_cookie_header),
            )

        token = request.COOKIES.get(access_cookie_name)
        token_source = "request.COOKIES" if token else None
        # Compatibility: some content types bypass DRF's standard parsers
        if not token and base_request is not None and hasattr(base_request, "COOKIES"):
            token = base_request.COOKIES.get(access_cookie_name)
            token_source = "request._request.COOKIES" if token else None
        if not token:
            if raw_cookie_header:
                # Manual cookie parsing to cover edge cases where Django skipped cookie parsing
                # (e.g. streaming responses bypassing Django's request wrapper).
                for part in raw_cookie_header.split(";"):
                    name, _, value = part.strip().partition("=")
                    value = value.strip()
                    if name == access_cookie_name and value:
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        token = value
                        token_source = "HTTP_COOKIE"
                        break
        if not token:
            if settings.DEBUG:
                self.logger.debug(
                    "CookieJWTAuthentication.missing_token path=%s raw_cookie_present=%s",
                    getattr(request, "path", ""),
                    bool(raw_cookie_header),
                )
            return None

        try:
            payload = jwt.decode(
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

        if payload.get("type") != "access":
            raise AuthenticationFailed("Invalid token.")

        user_model = get_user_model()
        try:
            user = user_model.objects.get(pk=payload.get("user_id"))
        except user_model.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid token.") from exc

        if settings.DEBUG:
            self.logger.debug(
                "CookieJWTAuthentication.success path=%s source=%s user_id=%s",
                getattr(request, "path", ""),
                token_source,
                getattr(user, "pk", None),
            )

        return user, None

    def authenticate_header(self, request) -> str:
        return f'Bearer realm="{self.www_authenticate_realm}"'
