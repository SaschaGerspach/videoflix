import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.api import views
from accounts.domain.services import AuthenticationError


pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def test_register_creates_user_and_returns_tokens(monkeypatch, client):
    monkeypatch.setattr(
        views,
        "send_activation_email",
        lambda user, fail_silently=True: ("tok123", True),
    )

    payload = {
        "email": "reg@example.com",
        "password": "pw12345",
        "confirmed_password": "pw12345",
    }
    response = client.post("/api/register/", payload, format="json")

    assert response.status_code == 201
    data = response.json()
    assert data["user"]["email"] == "reg@example.com"
    assert data["token"] == "tok123"


def test_register_returns_validation_errors(client):
    response = client.post(
        "/api/register/",
        {"email": "", "password": "", "confirmed_password": ""},
        format="json",
    )
    assert response.status_code == 400
    assert "errors" in response.json()


def test_login_success_sets_cookies(monkeypatch, client):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        email="login@example.com",
        username="login@example.com",
        password="pw",
        is_active=True,
    )

    tokens = {
        "access": "access-token",
        "refresh": "refresh-token",
        "access_max_age": 10,
        "refresh_max_age": 20,
    }

    monkeypatch.setattr(views, "login_user", lambda **kwargs: (user, tokens))

    response = client.post(
        "/api/login/", {"email": "login@example.com", "password": "pw"}, format="json"
    )

    assert response.status_code == 200
    assert response.cookies["access_token"].value == "access-token"
    assert response.cookies["refresh_token"].value == "refresh-token"


def test_login_handles_invalid_credentials(monkeypatch, client):
    error = AuthenticationError(
        {"non_field_errors": ["Invalid credentials."]}, reason="invalid_credentials"
    )
    monkeypatch.setattr(
        views, "login_user", lambda **kwargs: (_ for _ in ()).throw(error)
    )

    response = client.post(
        "/api/login/", {"email": "bad@example.com", "password": "pw"}, format="json"
    )

    assert response.status_code == 400
    assert "errors" in response.json()


def test_login_rejects_inactive_user(monkeypatch, client):
    error = AuthenticationError(
        {"non_field_errors": ["Account is inactive."]}, reason="inactive"
    )
    monkeypatch.setattr(
        views, "login_user", lambda **kwargs: (_ for _ in ()).throw(error)
    )

    response = client.post(
        "/api/login/", {"email": "bad@example.com", "password": "pw"}, format="json"
    )

    assert response.status_code == 403


def test_logout_clears_tokens(monkeypatch, client):
    called = {}
    monkeypatch.setattr(
        views, "logout_user", lambda token: called.setdefault("token", token)
    )

    client.cookies["refresh_token"] = "refresh123"
    response = client.post("/api/logout/", {}, format="json")

    assert response.status_code == 200
    assert called["token"] == "refresh123"
    assert response.cookies["access_token"]["max-age"] == 0
    assert response.cookies["refresh_token"]["max-age"] == 0


def test_logout_reports_validation_error(monkeypatch, client):
    monkeypatch.setattr(
        views,
        "logout_user",
        lambda token: (_ for _ in ()).throw(
            ValidationError({"refresh_token": ["bad"]})
        ),
    )
    client.cookies["refresh_token"] = "refresh123"

    response = client.post("/api/logout/", {}, format="json")
    assert response.status_code == 400
    assert "errors" in response.json()


def test_token_refresh_success(monkeypatch, client):
    monkeypatch.setattr(
        views,
        "refresh_access_token",
        lambda token: {"access": "new-access", "access_max_age": 5},
    )
    client.cookies["refresh_token"] = "refresh123"

    response = client.post("/api/token/refresh/", {}, format="json")

    assert response.status_code == 200
    assert response.json()["access"] == "new-access"
    assert response.cookies["access_token"].value == "new-access"


def test_token_refresh_missing_cookie(client):
    response = client.post("/api/token/refresh/", {}, format="json")
    assert response.status_code == 400
    assert "refresh_token" in response.json().get("errors", {})


def test_password_reset_triggers_email(monkeypatch, client):
    user_model = get_user_model()
    user_model.objects.create_user(
        email="reset@example.com",
        username="reset@example.com",
        password="pw",
        is_active=True,
    )
    monkeypatch.setattr(
        views, "send_password_reset_email", lambda **kwargs: ("tok", True)
    )
    response = client.post(
        "/api/password_reset/", {"email": "reset@example.com"}, format="json"
    )
    assert response.status_code == 200
    assert "detail" in response.json()


def test_password_reset_requires_email(client):
    response = client.post("/api/password_reset/", {}, format="json")
    assert response.status_code == 400
    assert "email" in response.json().get("errors", {})


def test_password_confirm_success(monkeypatch, client):
    called = {}

    def fake_confirm(uidb64, token, new_password):
        called["args"] = (uidb64, token, new_password)

    monkeypatch.setattr(views, "confirm_password_reset", fake_confirm)

    response = client.post(
        "/api/password_confirm/uid/tok/",
        {"new_password": "pw1", "confirm_password": "pw1"},
        format="json",
    )

    assert response.status_code == 200
    assert called["args"][0] == "uid"
    assert called["args"][1] == "tok"


def test_password_confirm_handles_error(monkeypatch, client):
    monkeypatch.setattr(
        views,
        "confirm_password_reset",
        lambda **kwargs: (_ for _ in ()).throw(ValidationError({"token": ["bad"]})),
    )

    response = client.post(
        "/api/password_confirm/uid/tok/",
        {"new_password": "pw1", "confirm_password": "pw1"},
        format="json",
    )

    assert response.status_code == 400
    assert "errors" in response.json()


def test_activate_view_get_html_success(monkeypatch, client):
    monkeypatch.setattr(
        views.ActivateAccountView, "_activate_user", lambda self, data: (True, None)
    )
    resp = client.get(
        "/api/activate/uid/token/", HTTP_ACCEPT="text/html,application/json"
    )
    assert resp.status_code == 200
    assert "<html" in resp.content.decode()


def test_activate_view_get_html_failure(monkeypatch, client):
    monkeypatch.setattr(
        views.ActivateAccountView,
        "_activate_user",
        lambda self, data: (False, {"non_field_errors": ["invalid"]}),
    )
    resp = client.get(
        "/api/activate/uid/token/", HTTP_ACCEPT="text/html,application/json"
    )
    assert resp.status_code == 400
    assert "<html" in resp.content.decode()


def test_logout_parse_error_returns_400(client):
    response = client.generic(
        "POST", "/api/logout/", data=b'{"bad":', content_type="application/json"
    )
    assert response.status_code == 400
    assert "errors" in response.json()
