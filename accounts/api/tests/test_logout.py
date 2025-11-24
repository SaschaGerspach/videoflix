import secrets
from datetime import datetime, timedelta, UTC

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.domain.services import is_refresh_token_blacklisted

pytestmark = pytest.mark.django_db

EXPECTED_PATH = getattr(settings, "SESSION_COOKIE_PATH", "/")
EXPECTED_DOMAIN = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
EXPECTED_SAMESITE = getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax")


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


@pytest.fixture
def create_user():
    def _create_user(
        email: str,
        password: str = "securepassword123",
        *,
        is_active: bool = True,
    ):
        user_model = get_user_model()
        return user_model.objects.create_user(
            username=email,
            email=email,
            password=password,
            is_active=is_active,
        )

    return _create_user


def _login(client: APIClient, email: str, password: str):
    return client.post(
        reverse("login"),
        {"email": email, "password": password},
        format="json",
    )


def _make_refresh_token(user_id: int, *, exp: datetime | None = None) -> str:
    issued_at = datetime.now(UTC)
    expires_at = exp or (
        issued_at + timedelta(seconds=settings.JWT_REFRESH_LIFETIME_SECONDS)
    )
    payload = {
        "user_id": user_id,
        "username": f"user-{user_id}",
        "type": "refresh",
        "jti": secrets.token_urlsafe(12),
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _cookie_has_flag(morsel, flag: str) -> bool:
    return flag in morsel.OutputString()


def test_logout_success_blacklists_refresh_token_and_clears_cookies(
    api_client: APIClient, create_user
):
    create_user("user@example.com")
    login_response = _login(api_client, "user@example.com", "securepassword123")
    assert login_response.status_code == status.HTTP_200_OK

    refresh_token_value = api_client.cookies["refresh_token"].value
    assert not is_refresh_token_blacklisted(refresh_token_value)

    response = api_client.post(reverse("logout"), {}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "detail": "Logout successful! All tokens will be deleted. Refresh token is now invalid."
    }
    assert int(response.cookies["access_token"]["max-age"]) == 0
    assert int(response.cookies["refresh_token"]["max-age"]) == 0
    assert (response.cookies["access_token"]["domain"] or None) == EXPECTED_DOMAIN
    assert (response.cookies["refresh_token"]["domain"] or None) == EXPECTED_DOMAIN
    assert response.cookies["access_token"]["path"] == EXPECTED_PATH
    assert response.cookies["refresh_token"]["path"] == EXPECTED_PATH
    assert response.cookies["access_token"]["samesite"] == EXPECTED_SAMESITE
    assert response.cookies["refresh_token"]["samesite"] == EXPECTED_SAMESITE
    assert response.cookies["access_token"]["expires"]
    assert response.cookies["refresh_token"]["expires"]
    assert _cookie_has_flag(response.cookies["access_token"], "HttpOnly")
    assert _cookie_has_flag(response.cookies["refresh_token"], "HttpOnly")
    assert not _cookie_has_flag(response.cookies["access_token"], "Secure")
    assert not _cookie_has_flag(response.cookies["refresh_token"], "Secure")
    assert is_refresh_token_blacklisted(refresh_token_value)


def test_logout_missing_refresh_cookie_returns_400(api_client: APIClient):
    response = api_client.post(reverse("logout"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "errors": {"refresh_token": ["Refresh token cookie missing."]}
    }


def test_logout_invalid_refresh_token_returns_400(api_client: APIClient):
    api_client.cookies["refresh_token"] = "invalid-token"

    response = api_client.post(reverse("logout"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {"refresh_token": ["Invalid refresh token."]}}


def test_logout_twice_returns_400_token_already_invalidated(
    api_client: APIClient, create_user
):
    create_user("user@example.com")
    login_response = _login(api_client, "user@example.com", "securepassword123")
    assert login_response.status_code == status.HTTP_200_OK

    refresh_token_value = api_client.cookies["refresh_token"].value

    first = api_client.post(reverse("logout"), {}, format="json")
    assert first.status_code == status.HTTP_200_OK

    api_client.cookies["refresh_token"] = refresh_token_value
    second = api_client.post(reverse("logout"), {}, format="json")

    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert second.json() == {
        "errors": {"refresh_token": ["Token already invalidated."]}
    }


@override_settings(SESSION_COOKIE_SECURE=True)
def test_logout_respects_secure_flag(api_client: APIClient, create_user):
    create_user("secure@example.com")
    _login(api_client, "secure@example.com", "securepassword123")

    response = api_client.post(reverse("logout"), {}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert _cookie_has_flag(response.cookies["access_token"], "Secure")
    assert _cookie_has_flag(response.cookies["refresh_token"], "Secure")
    assert response.cookies["access_token"]["samesite"] == EXPECTED_SAMESITE
    assert response.cookies["refresh_token"]["samesite"] == EXPECTED_SAMESITE


def test_logout_refresh_token_for_unknown_user_returns_400(api_client: APIClient):
    api_client.cookies["refresh_token"] = _make_refresh_token(user_id=999999)

    response = api_client.post(reverse("logout"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {"refresh_token": ["Invalid refresh token."]}}


def test_logout_invalid_json_payload_returns_400(api_client: APIClient, create_user):
    create_user("user@example.com")
    login_response = _login(api_client, "user@example.com", "securepassword123")
    assert login_response.status_code == status.HTTP_200_OK

    response = api_client.post(
        reverse("logout"),
        "not-json",
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    error_message = response.json()["errors"]["non_field_errors"][0]
    assert "JSON parse error" in error_message
