import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.domain import services


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def active_user():
    user_model = get_user_model()
    return user_model.objects.create_user(
        email="active@example.com",
        username="active@example.com",
        password="secret123",
        is_active=True,
    )


@pytest.fixture
def inactive_user():
    user_model = get_user_model()
    return user_model.objects.create_user(
        email="inactive@example.com",
        username="inactive@example.com",
        password="secret123",
        is_active=False,
    )


def test_create_inactive_user_sets_flags():
    user = services.create_inactive_user(email="new@example.com", password="pw")
    assert user.is_active is False
    assert user.check_password("pw")


@override_settings(PUBLIC_API_BASE="http://api.test")
def test_send_activation_email_success(monkeypatch, inactive_user):
    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(services, "_send_multipart_email", fake_send)
    token, delivered = services.send_activation_email(inactive_user, fail_silently=True)
    assert delivered is True
    assert captured["recipient"] == "inactive@example.com"
    assert token in captured["context"]["action_url"]
    assert "activate" in captured["context"]["action_url"]


def test_send_activation_email_handles_failure(monkeypatch, inactive_user):
    def boom(**kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(services, "_send_multipart_email", boom)
    token, delivered = services.send_activation_email(inactive_user, fail_silently=True)
    assert token
    assert delivered is False

    with pytest.raises(RuntimeError):
        services.send_activation_email(inactive_user, fail_silently=False)


@override_settings(DEV_FRONTEND_ORIGIN="http://frontend.test")
def test_send_password_reset_email_success(monkeypatch, active_user):
    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(services, "_send_multipart_email", fake_send)
    token, delivered = services.send_password_reset_email(
        active_user.email, fail_silently=True
    )

    assert delivered is True
    assert "confirm_password.html" in captured["context"]["action_url"]
    assert token in captured["context"]["action_url"]


def test_send_password_reset_email_failure(monkeypatch, active_user):
    def boom(**kwargs):
        raise RuntimeError("email fail")

    monkeypatch.setattr(services, "_send_multipart_email", boom)
    token, delivered = services.send_password_reset_email(
        active_user.email, fail_silently=True
    )
    assert token
    assert delivered is False

    with pytest.raises(RuntimeError):
        services.send_password_reset_email(active_user.email, fail_silently=False)


def test_confirm_password_reset_updates_password(active_user):
    token = default_token_generator.make_token(active_user)
    uidb64 = urlsafe_base64_encode(force_bytes(active_user.pk))

    services.confirm_password_reset(
        uidb64=uidb64, token=token, new_password="new-secret"
    )
    active_user.refresh_from_db()
    assert active_user.check_password("new-secret")


def test_confirm_password_reset_rejects_bad_token(active_user):
    uidb64 = urlsafe_base64_encode(force_bytes(active_user.pk))
    with pytest.raises(ValidationError) as exc:
        services.confirm_password_reset(
            uidb64=uidb64, token="invalid", new_password="pw"
        )

    assert "token" in exc.value.message_dict


def test_login_user_success(active_user):
    user, tokens = services.login_user(email=active_user.email, password="secret123")
    assert user.pk == active_user.pk
    assert {"access", "refresh", "access_max_age", "refresh_max_age"} <= set(
        tokens.keys()
    )


def test_login_user_rejects_inactive(inactive_user):
    with pytest.raises(services.AuthenticationError) as exc:
        services.login_user(email=inactive_user.email, password="secret123")

    assert exc.value.reason == "inactive"


def test_login_user_rejects_bad_credentials(active_user):
    with pytest.raises(services.AuthenticationError) as exc:
        services.login_user(email=active_user.email, password="wrong")

    assert exc.value.reason == "invalid_credentials"


def test_refresh_access_token_success(active_user):
    _, tokens = services.login_user(email=active_user.email, password="secret123")
    refreshed = services.refresh_access_token(tokens["refresh"])
    assert refreshed["access"]
    assert refreshed["access_max_age"] == services.ACCESS_LIFETIME_SECONDS


def test_refresh_access_token_blacklisted_after_logout(active_user):
    _, tokens = services.login_user(email=active_user.email, password="secret123")
    services.logout_user(tokens["refresh"])
    with pytest.raises(ValidationError):
        services.refresh_access_token(tokens["refresh"])


def test_refresh_access_token_rejects_access_token(active_user):
    access_token, _ = services._generate_token(
        active_user, services.ACCESS_TOKEN_LIFETIME, "access"
    )
    with pytest.raises(ValidationError):
        services.refresh_access_token(access_token)


def test_logout_user_sets_blacklist_and_rejects_missing(active_user):
    _, tokens = services.login_user(email=active_user.email, password="secret123")
    services.logout_user(tokens["refresh"])
    assert services.is_refresh_token_blacklisted(tokens["refresh"]) is True

    with pytest.raises(ValidationError):
        services.logout_user(None)


def test_activate_user_happy_path(inactive_user):
    token = default_token_generator.make_token(inactive_user)
    uidb64 = urlsafe_base64_encode(force_bytes(inactive_user.pk))

    services.activate_user(uidb64=uidb64, token=token)
    inactive_user.refresh_from_db()
    assert inactive_user.is_active is True


def test_activate_user_rejects_invalid_token(inactive_user):
    uidb64 = urlsafe_base64_encode(force_bytes(inactive_user.pk))
    with pytest.raises(ValidationError):
        services.activate_user(uidb64=uidb64, token="bad-token")


def test_confirm_password_reset_rejects_bad_uid():
    # Encoded numeric UID that does not exist should raise ValidationError
    missing_uid = urlsafe_base64_encode(force_bytes("9999"))
    with pytest.raises(ValidationError):
        services.confirm_password_reset(
            uidb64=missing_uid, token="bad", new_password="pw"
        )


def test_render_email_bodies_fallback_includes_action_url():
    context = {"action_url": "http://example.com/reset"}
    text, html = services._render_email_bodies("email/password_reset_email", context)
    assert "reset" in html
    assert "reset" in text  # txt missing falls back to strip_tags + action link


def test_send_multipart_email_uses_send(monkeypatch):
    sent = {}

    class DummyEmail:
        def __init__(self, subject, body, from_email, to, **kwargs):
            sent["kwargs"] = {
                "subject": subject,
                "body": body,
                "from_email": from_email,
                "to": to,
                **kwargs,
            }

        def attach_alternative(self, *_args, **_kwargs):
            sent["attached"] = True

        def send(self):
            sent["sent"] = True

    monkeypatch.setattr(services, "EmailMultiAlternatives", DummyEmail)
    services._send_multipart_email(
        subject="Subj",
        template_base="email/activation_email",
        context={"action_url": "http://example.com"},
        recipient="user@example.com",
    )
    assert sent["sent"] is True
    assert sent["attached"] is True
