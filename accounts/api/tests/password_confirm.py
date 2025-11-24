import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def create_user():
    def _create_user(
        email: str,
        password: str = "oldpassword123",
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


def _build_confirm_url(user) -> tuple[str, str]:
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uidb64, token


def test_password_confirm_success_resets_password(api_client: APIClient, create_user):
    user = create_user("confirm@example.com")
    uidb64, token = _build_confirm_url(user)
    new_password = "newsecurepassword123"

    response = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        {"new_password": new_password, "confirm_password": new_password},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"detail": "Your Password has been successfully reset."}
    user.refresh_from_db()
    assert user.check_password(new_password)


def test_password_confirm_missing_fields_returns_400(
    api_client: APIClient, create_user
):
    user = create_user("missing@example.com")
    uidb64, token = _build_confirm_url(user)

    response = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    errors = response.json()["errors"]
    assert "new_password" in errors
    assert "confirm_password" in errors


def test_password_confirm_mismatched_passwords_returns_400(
    api_client: APIClient, create_user
):
    user = create_user("mismatch@example.com")
    uidb64, token = _build_confirm_url(user)

    response = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        {"new_password": "new123", "confirm_password": "different456"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    errors = response.json()["errors"]
    assert errors["confirm_password"] == ["Passwords do not match."]


def test_password_confirm_invalid_token_returns_400(api_client: APIClient, create_user):
    user = create_user("invalidtoken@example.com")
    uidb64, _ = _build_confirm_url(user)

    response = api_client.post(
        reverse(
            "password_confirm", kwargs={"uidb64": uidb64, "token": "invalid-token"}
        ),
        {"new_password": "newpass123", "confirm_password": "newpass123"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "token" in response.json()["errors"]


def test_password_confirm_unknown_user_returns_400(api_client: APIClient):
    uidb64 = urlsafe_base64_encode(force_bytes(999999))
    response = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": "any-token"}),
        {"new_password": "newpass123", "confirm_password": "newpass123"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "uidb64" in response.json()["errors"]


def test_password_confirm_token_cannot_be_reused(api_client: APIClient, create_user):
    user = create_user("reuse@example.com")
    uidb64, token = _build_confirm_url(user)
    payload = {"new_password": "freshpass123", "confirm_password": "freshpass123"}

    first = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        payload,
        format="json",
    )
    assert first.status_code == status.HTTP_200_OK

    second = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        payload,
        format="json",
    )
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert "token" in second.json()["errors"]


def test_password_confirm_invalid_json_returns_400(api_client: APIClient, create_user):
    user = create_user("parse@example.com")
    uidb64, token = _build_confirm_url(user)

    response = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        "not-json",
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "JSON parse error" in response.json()["errors"]["non_field_errors"][0]


def test_password_confirm_invalidates_old_password_and_allows_new_login(
    api_client, create_user
):
    user = create_user("flip@example.com", password="oldpw123")
    uidb64, token = (
        urlsafe_base64_encode(force_bytes(user.pk)),
        default_token_generator.make_token(user),
    )

    # confirm reset
    payload = {"new_password": "NEWpw!234", "confirm_password": "NEWpw!234"}
    r = api_client.post(
        reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
        payload,
        format="json",
    )
    assert r.status_code == status.HTTP_200_OK

    # login with the previous password must fail
    r_old = api_client.post(
        reverse("login"), {"email": user.email, "password": "oldpw123"}, format="json"
    )
    assert r_old.status_code == status.HTTP_400_BAD_REQUEST

    # login with the new password works
    r_new = api_client.post(
        reverse("login"), {"email": user.email, "password": "NEWpw!234"}, format="json"
    )
    assert r_new.status_code == status.HTTP_200_OK


def test_password_reset_rejects_get_method_returns_405(api_client):
    assert (
        api_client.get(reverse("password_reset")).status_code
        == status.HTTP_405_METHOD_NOT_ALLOWED
    )


def test_password_confirm_rejects_get_method_returns_405(api_client, create_user):
    user = create_user("getblock@example.com")
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    assert (
        api_client.get(
            reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token})
        ).status_code
        == status.HTTP_405_METHOD_NOT_ALLOWED
    )


def test_password_reset_accepts_case_insensitive_email(api_client, create_user):
    create_user("case@example.com")
    r = api_client.post(
        reverse("password_reset"), {"email": "Case@Example.com"}, format="json"
    )
    assert r.status_code == status.HTTP_200_OK


def test_password_confirm_revokes_existing_refresh_tokens(api_client, create_user):
    user = create_user("revoke@example.com", password="oldpw123")
    # Login, damit Refresh-Cookie existiert
    assert (
        api_client.post(
            reverse("login"),
            {"email": user.email, "password": "oldpw123"},
            format="json",
        ).status_code
        == status.HTTP_200_OK
    )

    # Password confirm
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    payload = {"new_password": "NEWpw!234", "confirm_password": "NEWpw!234"}
    assert (
        api_client.post(
            reverse("password_confirm", kwargs={"uidb64": uidb64, "token": token}),
            payload,
            format="json",
        ).status_code
        == status.HTTP_200_OK
    )

    # Refresh sollte nun scheitern (Blacklist-Policy)
    r_refresh = api_client.post(reverse("token_refresh"), {}, format="json")
    assert r_refresh.status_code == status.HTTP_401_UNAUTHORIZED
