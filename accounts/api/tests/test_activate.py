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


def _encode_uid(user_id: int) -> str:
    return urlsafe_base64_encode(force_bytes(user_id))


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


def test_activate_fails_with_invalid_token(api_client: APIClient):
    user_model = get_user_model()
    user = user_model.objects.create_user(
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
    errors = response.json()["errors"]
    assert "non_field_errors" in errors

    user.refresh_from_db()
    assert user.is_active is False


def test_activate_fails_with_invalid_uid(api_client: APIClient):
    user_model = get_user_model()
    user = user_model.objects.create_user(
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
    errors = response.json()["errors"]
    assert "non_field_errors" in errors

    user.refresh_from_db()
    assert user.is_active is False


def test_activate_fails_when_user_already_active(api_client):
    user = get_user_model().objects.create_user(
        username="already@example.com", email="already@example.com",
        password="x", is_active=True
    )
    token = default_token_generator.make_token(user)
    uidb64 = _encode_uid(user.pk)

    r = api_client.post(
        reverse("activate"),
        {"uidb64": uidb64, "token": token},
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in r.json()["errors"]


def test_activate_token_cannot_be_reused(api_client):
    U = get_user_model()
    user = U.objects.create_user(
        username="once@example.com", email="once@example.com", password="x", is_active=False)
    token = default_token_generator.make_token(user)
    uidb64 = _encode_uid(user.pk)

    payload = {"uidb64": uidb64, "token": token}

    r1 = api_client.post(reverse("activate"), payload, format="json")
    assert r1.status_code == status.HTTP_200_OK

    r2 = api_client.post(reverse("activate"), payload, format="json")
    assert r2.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in r2.json()["errors"]


def test_activate_fails_when_user_not_found(api_client):
    # irgendeine hohe ID, die nicht existiert
    uidb64 = _encode_uid(999999)
    r = api_client.post(
        reverse("activate"),
        {"uidb64": uidb64, "token": "any"},
        format="json",
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert "non_field_errors" in r.json()["errors"]


@pytest.fixture
def make_user():
    def _make(**kw):
        defaults = dict(username="u@example.com",
                        email="u@example.com", password="x", is_active=False)
        defaults.update(kw)
        return get_user_model().objects.create_user(**defaults)
    return _make
