from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _encode_uid(user_id: int) -> str:
    return urlsafe_base64_encode(force_bytes(user_id))


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@videoflix.local",
    FRONTEND_DOMAIN="https://frontend.videoflix.local",
)
def test_register_sends_activation_email_with_link(api_client: APIClient):
    payload = {
        "email": "new-activator@example.com",
        "password": "securepassword123",
        "confirmed_password": "securepassword123",
    }

    response = api_client.post(reverse("register"), payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    user_id = response.json()["user"]["id"]
    token = response.json()["token"]

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "Activate your Videoflix account"
    assert message.to == ["new-activator@example.com"]

    activation_link = next((part for part in message.body.split() if part.startswith("http")), "")
    assert activation_link, "Activation link missing in email body."

    frontend_domain = "https://frontend.videoflix.local".rstrip("/")
    assert activation_link.startswith(f"{frontend_domain}/activate/"), activation_link

    parsed = urlparse(activation_link)
    query = parse_qs(parsed.query)

    if query:
        uidb64 = query.get("uidb64", [None])[0]
        token_in_link = query.get("token", [None])[0]
    else:
        segments = [segment for segment in parsed.path.split("/") if segment]
        # Expect .../activate/<uidb64>/<token>
        uidb64 = segments[-2] if len(segments) >= 2 else None
        token_in_link = segments[-1] if segments else None

    assert uidb64, "uidb64 missing in activation link."
    assert token_in_link, "token missing in activation link."
    assert token_in_link == token
    assert urlsafe_base64_decode(uidb64).decode() == str(user_id)


def test_activate_success_marks_user_active(api_client: APIClient):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="activate.success@example.com",
        email="activate.success@example.com",
        password="securepassword123",
        is_active=False,
    )

    token = default_token_generator.make_token(user)
    uidb64 = _encode_uid(user.pk)

    response = api_client.post(
        reverse("activate"),
        {"uidb64": uidb64, "token": token},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Account activated."}

    user.refresh_from_db()
    assert user.is_active is True


def test_activate_invalid_token_400(api_client: APIClient):
    user = get_user_model().objects.create_user(
        username="activate.invalid.token@example.com",
        email="activate.invalid.token@example.com",
        password="securepassword123",
        is_active=False,
    )

    uidb64 = _encode_uid(user.pk)
    response = api_client.post(
        reverse("activate"),
        {"uidb64": uidb64, "token": "invalid-token"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in response.json()["errors"]


def test_activate_invalid_uid_400(api_client: APIClient):
    user = get_user_model().objects.create_user(
        username="activate.invalid.uid@example.com",
        email="activate.invalid.uid@example.com",
        password="securepassword123",
        is_active=False,
    )

    token = default_token_generator.make_token(user)
    response = api_client.post(
        reverse("activate"),
        {"uidb64": "invalid-uid", "token": token},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in response.json()["errors"]


def test_activate_token_cannot_be_reused(api_client: APIClient):
    user = get_user_model().objects.create_user(
        username="activate.once@example.com",
        email="activate.once@example.com",
        password="securepassword123",
        is_active=False,
    )

    token = default_token_generator.make_token(user)
    uidb64 = _encode_uid(user.pk)
    payload = {"uidb64": uidb64, "token": token}

    first = api_client.post(reverse("activate"), payload, format="json")
    assert first.status_code == status.HTTP_200_OK

    second = api_client.post(reverse("activate"), payload, format="json")
    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in second.json()["errors"]


def test_activate_fails_when_user_already_active(api_client: APIClient):
    user = get_user_model().objects.create_user(
        username="activate.already@example.com",
        email="activate.already@example.com",
        password="securepassword123",
        is_active=True,
    )

    token = default_token_generator.make_token(user)
    uidb64 = _encode_uid(user.pk)

    response = api_client.post(
        reverse("activate"),
        {"uidb64": uidb64, "token": token},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in response.json()["errors"]
