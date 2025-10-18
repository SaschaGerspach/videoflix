from __future__ import annotations

from typing import Optional, Tuple

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

    def authenticate(self, request) -> Optional[Tuple[AbstractBaseUser, None]]:
        token = request.COOKIES.get("access_token")
        if not token:
            return None

        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=["HS256"],
            )
        except InvalidTokenError as exc:
            raise AuthenticationFailed("Invalid token.") from exc

        if payload.get("type") != "access":
            raise AuthenticationFailed("Invalid token.")

        user_model = get_user_model()
        try:
            user = user_model.objects.get(pk=payload.get("user_id"))
        except user_model.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid token.") from exc

        return user, None

    def authenticate_header(self, request) -> str:
        return f'Bearer realm="{self.www_authenticate_realm}"'
