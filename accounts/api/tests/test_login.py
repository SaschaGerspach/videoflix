import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

EXPECTED_PATH = getattr(settings, "SESSION_COOKIE_PATH", "/")
EXPECTED_DOMAIN = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
EXPECTED_SAMESITE = getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax")


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def clear_throttle_cache():
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


def _login(api_client: APIClient, email: str, password: str):
    return api_client.post(
        reverse("login"),
        {"email": email, "password": password},
        format="json",
    )


def _cookie_domain(morsel) -> str | None:
    value = morsel["domain"]
    return value or None


def _cookie_has_flag(morsel, flag: str) -> bool:
    return flag in morsel.OutputString()


def test_login_sets_secure_cookies_with_flags(api_client: APIClient, create_user):
    create_user("user@example.com")

    response = _login(api_client, "user@example.com", "securepassword123")

    assert response.status_code == status.HTTP_200_OK

    access_cookie = response.cookies["access_token"]
    refresh_cookie = response.cookies["refresh_token"]

    assert access_cookie.value
    assert refresh_cookie.value

    assert _cookie_has_flag(access_cookie, "HttpOnly")
    assert _cookie_has_flag(refresh_cookie, "HttpOnly")

    assert access_cookie["samesite"] == EXPECTED_SAMESITE
    assert refresh_cookie["samesite"] == EXPECTED_SAMESITE

    assert access_cookie["path"] == EXPECTED_PATH
    assert refresh_cookie["path"] == EXPECTED_PATH

    assert _cookie_domain(access_cookie) == EXPECTED_DOMAIN
    assert _cookie_domain(refresh_cookie) == EXPECTED_DOMAIN

    assert not _cookie_has_flag(access_cookie, "Secure")
    assert not _cookie_has_flag(refresh_cookie, "Secure")

    assert int(access_cookie["max-age"]) == settings.JWT_ACCESS_LIFETIME_SECONDS
    assert int(refresh_cookie["max-age"]) == settings.JWT_REFRESH_LIFETIME_SECONDS
    assert access_cookie["expires"]
    assert refresh_cookie["expires"]


def test_login_normalizes_email(api_client: APIClient, create_user):
    create_user("user@example.com")

    response = _login(api_client, "User@Example.com", "securepassword123")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["user"]["username"] == "user@example.com"


@override_settings(SESSION_COOKIE_SECURE=True)
def test_login_sets_secure_flag_when_secure_setting_enabled(
    api_client: APIClient, create_user
):
    create_user("user@example.com")

    response = _login(api_client, "user@example.com", "securepassword123")

    assert response.status_code == status.HTTP_200_OK
    access_cookie = response.cookies["access_token"]
    refresh_cookie = response.cookies["refresh_token"]

    assert _cookie_has_flag(access_cookie, "Secure")
    assert _cookie_has_flag(refresh_cookie, "Secure")


def test_login_invalid_credentials_same_message(api_client: APIClient, create_user):
    create_user("user@example.com")

    wrong_password = _login(api_client, "user@example.com", "wrong-password")
    unknown_user = _login(api_client, "missing@example.com", "securepassword123")

    for resp in (wrong_password, unknown_user):
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.json()["errors"] == {"non_field_errors": ["Invalid credentials."]}


def test_login_fails_with_inactive_user(api_client: APIClient, create_user):
    create_user("inactive@example.com", is_active=False)

    response = _login(api_client, "inactive@example.com", "securepassword123")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["errors"] == {"non_field_errors": ["Account is inactive."]}


def test_login_repeated_requests_issue_new_tokens(api_client: APIClient, create_user):
    create_user("user@example.com")

    first = _login(api_client, "user@example.com", "securepassword123")
    second = _login(api_client, "user@example.com", "securepassword123")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.cookies["access_token"].value != second.cookies["access_token"].value
    assert first.cookies["refresh_token"].value != second.cookies["refresh_token"].value


def test_login_missing_fields_return_generic_error(api_client: APIClient):
    response = api_client.post(reverse("login"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["errors"] == {"non_field_errors": ["Invalid credentials."]}


def test_login_fails_with_invalid_json_payload(api_client: APIClient):
    response = api_client.post(
        reverse("login"),
        "not-json",
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_logout_clears_cookies(api_client: APIClient, create_user):
    create_user("user@example.com")
    _login(api_client, "user@example.com", "securepassword123")

    response = api_client.post(reverse("logout"))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "detail": "Logout successful! All tokens will be deleted. Refresh token is now invalid."
    }

    access_cookie = response.cookies["access_token"]
    refresh_cookie = response.cookies["refresh_token"]

    assert int(access_cookie["max-age"]) == 0
    assert int(refresh_cookie["max-age"]) == 0
    assert _cookie_domain(access_cookie) == EXPECTED_DOMAIN
    assert _cookie_domain(refresh_cookie) == EXPECTED_DOMAIN
    assert access_cookie["path"] == EXPECTED_PATH
    assert refresh_cookie["path"] == EXPECTED_PATH
    assert access_cookie["samesite"] == EXPECTED_SAMESITE
    assert refresh_cookie["samesite"] == EXPECTED_SAMESITE
    assert access_cookie["expires"]
    assert refresh_cookie["expires"]
    assert _cookie_has_flag(access_cookie, "HttpOnly")
    assert _cookie_has_flag(refresh_cookie, "HttpOnly")
    assert not _cookie_has_flag(access_cookie, "Secure")
    assert not _cookie_has_flag(refresh_cookie, "Secure")


def test_login_throttled_after_many_attempts(api_client: APIClient):
    responses = [
        _login(api_client, "missing@example.com", "wrong")
        for _ in range(6)
    ]

    assert responses[0].status_code == status.HTTP_400_BAD_REQUEST
    assert responses[-1].status_code == status.HTTP_429_TOO_MANY_REQUESTS
