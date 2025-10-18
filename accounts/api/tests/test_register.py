import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.core import mail
from django.test import override_settings
from django.utils.http import urlsafe_base64_decode
from urllib.parse import parse_qs, urlparse


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


pytestmark = pytest.mark.django_db


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@videoflix.local",
    FRONTEND_DOMAIN="http://localhost:3000",  # falls dein Service den Link baut
)
@pytest.mark.django_db
def test_register_success_creates_inactive_user(api_client: APIClient):
    payload = {
        "email": "NewUser@Example.com",
        "password": "securepassword123",
        "confirmed_password": "securepassword123",
    }

    response = api_client.post(reverse("register"), payload, format="json")

    assert response.status_code == 201
    data = response.json()
    assert data["user"]["id"] is not None
    assert data["user"]["email"] == "newuser@example.com"
    assert data["token"]

    user_model = get_user_model()
    user = user_model.objects.get(email="newuser@example.com")
    assert user.is_active is False


@pytest.mark.django_db
def test_register_fails_when_password_missing(api_client: APIClient):
    payload = {"email": "user@example.com",
               "confirmed_password": "securepassword123"}

    response = api_client.post(reverse("register"), payload, format="json")

    assert response.status_code == 400
    errors = response.json()["errors"]
    assert "password" in errors
    assert errors["password"] == ["Password is required."]


@pytest.mark.django_db
def test_register_fails_when_passwords_do_not_match(api_client: APIClient):
    payload = {
        "email": "user@example.com",
        "password": "securepassword123",
        "confirmed_password": "differentpassword456",
    }

    response = api_client.post(reverse("register"), payload, format="json")

    assert response.status_code == 400
    errors = response.json()["errors"]
    assert "confirmed_password" in errors
    assert errors["confirmed_password"] == ["Passwords do not match."]


@pytest.mark.django_db
def test_register_fails_when_email_missing(api_client: APIClient):
    payload = {"password": "securepassword123",
               "confirmed_password": "securepassword123"}

    response = api_client.post(reverse("register"), payload, format="json")

    assert response.status_code == 400
    errors = response.json()["errors"]
    assert "email" in errors
    assert errors["email"] == ["Email is required."]


@pytest.mark.django_db
def test_register_fails_when_email_already_exists(api_client: APIClient):
    user_model = get_user_model()
    user_model.objects.create_user(
        username="user@example.com",
        email="user@example.com",
        password="securepassword123",
    )

    payload = {
        "email": "user@example.com",
        "password": "securepassword123",
        "confirmed_password": "securepassword123",
    }

    response = api_client.post(reverse("register"), payload, format="json")

    assert response.status_code == 400
    errors = response.json()["errors"]
    assert "email" in errors
    assert errors["email"] == ["A user with this email already exists."]


@pytest.mark.django_db
def test_register_fails_when_email_exists_case_insensitive(api_client):
    get_user_model().objects.create_user(email="User@Example.com",
                                         username="User@Example.com", password="x")
    payload = {"email": "user@example.com",
               "password": "x1", "confirmed_password": "x1"}
    r = api_client.post(reverse("register"), payload, format="json")
    assert r.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_register_fails_on_invalid_json(api_client):
    r = api_client.post(reverse("register"), "not json",
                        content_type="application/json")
    assert r.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize("payload,field", [
    ({"confirmed_password": "a"}, "password"),
    ({"password": "a", "confirmed_password": "a"}, "email"),
])
@pytest.mark.django_db
def test_register_400_missing_fields(api_client, payload, field):
    r = api_client.post(reverse("register"), payload, format="json")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert field in r.json()["errors"]


def test_register_sends_activation_mail():
    client = APIClient()
    payload = {
        "email": "user@example.com",
        "password": "securepassword123",
        "confirmed_password": "securepassword123",
    }

    r = client.post(reverse("register"), payload, format="json")
    assert r.status_code == status.HTTP_201_CREATED

    # Mail-Assertions
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert "Activate your Videoflix account" in msg.subject
    assert "user@example.com" in msg.to
    activation_link = next(
        (part for part in msg.body.split() if part.startswith("http")), ""
    )
    parsed = urlparse(activation_link)
    query = parse_qs(parsed.query)
    assert "uidb64" in query
    assert "token" in query
    uidb64 = query["uidb64"][0]
    assert urlsafe_base64_decode(uidb64).decode() == str(r.json()["user"]["id"])
