import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import status
from rest_framework.test import APIClient
from urllib.parse import parse_qs, urlparse

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@videoflix.local",
    FRONTEND_DOMAIN="http://localhost:3000",
)
def test_password_reset_sends_email_for_existing_user(api_client: APIClient, create_user):
    user = create_user("reset@example.com")
    mail.outbox.clear()

    response = api_client.post(
        reverse("password_reset"),
        {"email": "reset@example.com"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "detail": "An email has been sent to reset your password."
    }
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert "Reset your Videoflix password" in message.subject
    assert user.email in message.to
    link = next((part for part in message.body.split()
                if part.startswith("http")), "")
    parsed = urlparse(link)
    params = parse_qs(parsed.query)
    uidb64 = params["uidb64"][0]
    token = params["token"][0]
    assert force_str(urlsafe_base64_decode(uidb64)) == str(user.pk)
    assert default_token_generator.check_token(user, token)


def test_password_reset_returns_400_for_unknown_email(api_client: APIClient):
    mail.outbox.clear()

    response = api_client.post(
        reverse("password_reset"),
        {"email": "missing@example.com"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {
        "email": ["User with this email does not exist."]}}
    assert mail.outbox == []


def test_password_reset_missing_email_returns_400(api_client: APIClient):
    response = api_client.post(reverse("password_reset"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {"email": ["Email is required."]}}


def test_password_reset_invalid_json_returns_400(api_client: APIClient, create_user):
    create_user("parse@example.com")

    response = api_client.post(
        reverse("password_reset"),
        "not-json",
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "JSON parse error" in response.json(
    )["errors"]["non_field_errors"][0]


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@videoflix.local",
)
def test_password_reset_multiple_requests_send_email_each_time(
    api_client: APIClient, create_user
):
    create_user("repeat@example.com")
    mail.outbox.clear()

    first = api_client.post(
        reverse("password_reset"),
        {"email": "repeat@example.com"},
        format="json",
    )
    second = api_client.post(
        reverse("password_reset"),
        {"email": "repeat@example.com"},
        format="json",
    )

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert len(mail.outbox) == 2


def test_password_reset_accepts_case_insensitive_email(api_client, create_user):
    create_user("case@example.com")
    r = api_client.post(reverse("password_reset"), {
                        "email": "Case@Example.com"}, format="json")
    assert r.status_code == status.HTTP_200_OK


def test_password_reset_allows_inactive_user(api_client, create_user):
    create_user("inactive@example.com", is_active=False)
    r = api_client.post(reverse("password_reset"), {
                        "email": "inactive@example.com"}, format="json")
    assert r.status_code == status.HTTP_200_OK
