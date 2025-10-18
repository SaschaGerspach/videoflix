import secrets
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.domain.services import (
    _USER_REFRESH_REVOKE_KEY,
    is_refresh_token_blacklisted,
    revoke_all_refresh_tokens_for_user,
)

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


def _make_refresh_token(
    user_id: int,
    *,
    username: str | None = None,
    jti: str | None = None,
    exp: datetime | None = None,
    iat: int | None = None,
) -> str:
    if iat is not None:
        issued_at_dt = datetime.fromtimestamp(iat, timezone.utc)
    else:
        issued_at_dt = datetime.now(timezone.utc)
    expires_at_dt = exp or (
        issued_at_dt + timedelta(seconds=settings.JWT_REFRESH_LIFETIME_SECONDS)
    )
    issued_epoch = int(issued_at_dt.timestamp())
    expires_epoch = int(expires_at_dt.timestamp())
    payload = {
        "user_id": user_id,
        "username": username or f"user-{user_id}",
        "type": "refresh",
        "jti": jti or secrets.token_urlsafe(12),
        "iat": issued_epoch,
        "exp": expires_epoch,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _cookie_has_flag(morsel, flag: str) -> bool:
    return flag in morsel.OutputString()


def test_token_refresh_success_sets_new_access_cookie(api_client: APIClient, create_user):
    user = create_user("user@example.com")
    _login(api_client, user.email, "securepassword123")

    response = api_client.post(reverse("token_refresh"), {}, format="json")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["detail"] == "Token refreshed"
    assert body["access"]
    assert response.cookies["access_token"].value == body["access"]
    access_cookie = response.cookies["access_token"]
    assert int(access_cookie["max-age"]
               ) == settings.JWT_ACCESS_LIFETIME_SECONDS
    assert access_cookie["path"] == EXPECTED_PATH
    assert (access_cookie["domain"] or None) == EXPECTED_DOMAIN
    assert access_cookie["samesite"] == EXPECTED_SAMESITE
    assert access_cookie["expires"]
    assert _cookie_has_flag(access_cookie, "HttpOnly")
    assert not _cookie_has_flag(access_cookie, "Secure")


def test_token_refresh_missing_cookie_returns_400(api_client: APIClient):
    response = api_client.post(reverse("token_refresh"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {"refresh_token": [
        "Refresh token cookie missing."]}}


def test_token_refresh_invalid_token_returns_401(api_client: APIClient):
    api_client.cookies["refresh_token"] = "not-a-token"

    response = api_client.post(reverse("token_refresh"), {}, format="json")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"errors": {
        "refresh_token": ["Invalid refresh token."]}}


def test_token_refresh_blacklisted_token_returns_401(api_client: APIClient, create_user):
    user = create_user("user@example.com")
    _login(api_client, user.email, "securepassword123")
    refresh_token_value = api_client.cookies["refresh_token"].value

    # Logout to blacklist the refresh token
    api_client.post(reverse("logout"), {}, format="json")
    assert is_refresh_token_blacklisted(refresh_token_value)

    api_client.cookies["refresh_token"] = refresh_token_value
    response = api_client.post(reverse("token_refresh"), {}, format="json")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"errors": {"refresh_token": [
        "Invalid or expired refresh token."]}}


def test_token_refresh_unknown_user_returns_401(api_client: APIClient):
    fake_token = _make_refresh_token(user_id=999_999)
    api_client.cookies["refresh_token"] = fake_token

    response = api_client.post(reverse("token_refresh"), {}, format="json")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"errors": {
        "refresh_token": ["Invalid refresh token."]}}


def test_token_refresh_invalid_json_returns_400(api_client: APIClient, create_user):
    user = create_user("user@example.com")
    _login(api_client, user.email, "securepassword123")

    response = api_client.post(
        reverse("token_refresh"),
        "not-json",
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "JSON parse error" in response.json(
    )["errors"]["non_field_errors"][0]


def test_token_refresh_allows_repeated_use_until_logout(api_client: APIClient, create_user):
    user = create_user("user@example.com")
    _login(api_client, user.email, "securepassword123")
    refresh_token_value = api_client.cookies["refresh_token"].value

    response_first = api_client.post(
        reverse("token_refresh"), {}, format="json")
    assert response_first.status_code == status.HTTP_200_OK

    api_client.cookies["refresh_token"] = refresh_token_value
    response_second = api_client.post(
        reverse("token_refresh"), {}, format="json")
    assert response_second.status_code == status.HTTP_200_OK

    assert response_first.cookies["access_token"].value != response_second.cookies["access_token"].value


@override_settings(SESSION_COOKIE_SECURE=True)
def test_token_refresh_sets_secure_flag_when_enabled(api_client: APIClient, create_user):
    user = create_user("secure@example.com")
    _login(api_client, user.email, "securepassword123")

    response = api_client.post(reverse("token_refresh"), {}, format="json")

    assert response.status_code == status.HTTP_200_OK
    access_cookie = response.cookies["access_token"]
    assert _cookie_has_flag(access_cookie, "Secure")
    assert access_cookie["samesite"] == EXPECTED_SAMESITE
    assert access_cookie["path"] == EXPECTED_PATH


def test_token_refresh_rejects_token_at_revocation_boundary(api_client: APIClient, create_user):
    user = create_user("boundary@example.com")
    revoke_all_refresh_tokens_for_user(user)
    revoke_before = cache.get(_USER_REFRESH_REVOKE_KEY.format(user_id=user.pk))
    assert revoke_before is not None

    refresh_token = _make_refresh_token(
        user.pk, username=user.username, iat=int(revoke_before)
    )
    api_client.cookies["refresh_token"] = refresh_token

    response = api_client.post(reverse("token_refresh"), {}, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["errors"] == {"refresh_token": ["Invalid or expired refresh token."]}


def test_token_refresh_expired_returns_401(api_client, create_user):
    user = create_user("expired@example.com")
    # abgelaufenes Token bauen
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    expired = _make_refresh_token(user.id, exp=past)
    api_client.cookies["refresh_token"] = expired
    r = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_token_refresh_wrong_signature_returns_401(api_client, create_user, settings):
    user = create_user("sig@example.com")
    payload = {"user_id": user.id, "type": "refresh", "jti": secrets.token_urlsafe(8),
               "iat": datetime.now(timezone.utc),
               "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.JWT_REFRESH_LIFETIME_SECONDS)}
    tampered = jwt.encode(payload, "WRONG_SECRET", algorithm="HS256")
    api_client.cookies["refresh_token"] = tampered
    r = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


def test_token_refresh_rejects_get_method_returns_405(api_client: APIClient):
    r = api_client.get(reverse("token_refresh"))
    assert r.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_token_refresh_does_not_set_refresh_cookie(api_client: APIClient, create_user):
    user = create_user("norefresh@example.com")
    _login(api_client, user.email, "securepassword123")

    r = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r.status_code == status.HTTP_200_OK

    # In dieser Response nur access_token setzen, kein refresh_token
    assert "access_token" in r.cookies
    assert "refresh_token" not in r.cookies

    # Optional: sicherstellen, dass NUR access_token gesetzt wurde
    # (falls du erwartest, dass sonst kein weiterer Cookie in dieser Response gesetzt wird)
    assert set(r.cookies.keys()) == {"access_token"}


@override_settings(SESSION_COOKIE_DOMAIN="dev.local")
def test_token_refresh_respects_domain_setting_and_logout_mirrors_delete(api_client: APIClient, create_user):
    user = create_user("domain@example.com")
    _login(api_client, user.email, "securepassword123")

    # Refresh setzt access_token mit Domain=dev.local
    r_refresh = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r_refresh.status_code == status.HTTP_200_OK
    access_cookie = r_refresh.cookies["access_token"]
    assert access_cookie["domain"] == "dev.local"

    # Logout löscht mit identischer Domain
    r_logout = api_client.post(reverse("logout"), {}, format="json")
    assert r_logout.status_code == status.HTTP_200_OK

    del_access = r_logout.cookies.get("access_token")
    del_refresh = r_logout.cookies.get("refresh_token")
    assert del_access is not None and del_refresh is not None
    assert del_access["domain"] == "dev.local"
    assert del_refresh["domain"] == "dev.local"

    # Max-Age kann als str oder int kommen – wir casten robust
    def _max_age(m):
        v = m.get("max-age")
        return int(v) if v is not None else None

    assert _max_age(del_access) == 0
    assert _max_age(del_refresh) == 0

    # Optional robustheitscheck: Expires vorhanden (Browser löscht so oder so)
    assert del_access.get("expires") is not None
    assert del_refresh.get("expires") is not None


# 4) SameSite aus Settings variieren (Strict) → Cookie übernimmt Wert
@override_settings(SESSION_COOKIE_SAMESITE="Strict")
def test_token_refresh_respects_samesite_setting_strict(api_client: APIClient, create_user):
    user = create_user("samesite@example.com")
    _login(api_client, user.email, "securepassword123")

    r = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r.status_code == status.HTTP_200_OK
    access_cookie = r.cookies["access_token"]
    assert access_cookie["samesite"] == "Strict"
    assert access_cookie["path"] == EXPECTED_PATH  # weiterhin aus Settings
    # Secure-Flag bleibt unverändert von Samesite-Einstellung (separat getestet)


def test_token_refresh_sets_single_set_cookie_for_access_token(api_client: APIClient, create_user):
    user = create_user("single@example.com")
    _login(api_client, user.email, "securepassword123")

    r = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r.status_code == status.HTTP_200_OK

    # Es wird genau EIN Cookie 'access_token' in DIESER Response gesetzt
    # (response.cookies enthält nur die Cookies, die diese Response setzt)
    assert set(r.cookies.keys()) == {"access_token"}


# 6) Claims-Konsistenz: user_id stimmt, exp ungefähr Settings-Lifetime
def test_token_refresh_emits_access_claims_consistent(api_client: APIClient, create_user, settings):
    user = create_user("claims@example.com")
    _login(api_client, user.email, "securepassword123")

    r = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r.status_code == status.HTTP_200_OK

    access_jwt = r.cookies["access_token"].value
    decoded = jwt.decode(access_jwt, settings.SECRET_KEY, algorithms=["HS256"])

    assert decoded.get("user_id") == user.id
    # exp - iat ≈ access lifetime (± ein bisschen Toleranz)
    exp = decoded.get("exp")
    iat = decoded.get("iat")
    assert isinstance(exp, int) and isinstance(iat, int)
    lifetime = exp - iat
    assert abs(lifetime - settings.JWT_ACCESS_LIFETIME_SECONDS) <= 5
