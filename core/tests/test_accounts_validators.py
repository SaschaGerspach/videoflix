import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.domain import validators


pytestmark = pytest.mark.django_db


def test_validate_registration_payload_normalizes_and_checks_uniqueness():
    result = validators.validate_registration_payload(
        {
            "email": " User@Example.com ",
            "password": "secret123",
            "confirmed_password": "secret123",
        }
    )

    assert result == {"email": "user@example.com", "password": "secret123"}


def test_validate_registration_payload_rejects_duplicate_email():
    user_model = get_user_model()
    user_model.objects.create_user(
        email="dup@example.com", username="dup@example.com", password="pass"
    )

    with pytest.raises(ValidationError) as exc:
        validators.validate_registration_payload(
            {"email": "dup@example.com", "password": "a", "confirmed_password": "a"}
        )

    assert "email" in exc.value.message_dict


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "", "password": "x", "confirmed_password": ""},
        {"email": "a@example.com", "password": "1", "confirmed_password": "2"},
    ],
)
def test_validate_registration_payload_reports_missing_or_mismatch(payload):
    with pytest.raises(ValidationError) as exc:
        validators.validate_registration_payload(payload)

    assert exc.value.message_dict


def test_validate_activation_params_requires_uid_and_token():
    with pytest.raises(ValidationError) as exc:
        validators.validate_activation_params({})

    assert "non_field_errors" in exc.value.message_dict


def test_validate_login_payload_normalizes_email():
    result = validators.validate_login_payload(
        {"email": " USER@ExAmple.Com ", "password": "pw"}
    )
    assert result == {"email": "user@example.com", "password": "pw"}


def test_validate_login_payload_invalid():
    with pytest.raises(ValidationError) as exc:
        validators.validate_login_payload({"email": "", "password": ""})

    assert "non_field_errors" in exc.value.message_dict


def test_validate_password_reset_payload_needs_existing_user():
    user_model = get_user_model()
    user_model.objects.create_user(
        email="exists@example.com", username="exists@example.com", password="x"
    )

    result = validators.validate_password_reset_payload({"email": "exists@example.com"})
    assert result == {"email": "exists@example.com"}

    with pytest.raises(ValidationError) as exc:
        validators.validate_password_reset_payload({"email": "missing@example.com"})

    assert "email" in exc.value.message_dict


def test_validate_password_reset_payload_rejects_blank():
    with pytest.raises(ValidationError) as exc:
        validators.validate_password_reset_payload({"email": "  "})

    assert "email" in exc.value.message_dict


def test_validate_password_confirm_payload_accepts_match_and_rejects_mismatch():
    ok = validators.validate_password_confirm_payload(
        {"new_password": "abc12345", "confirm_password": "abc12345"}
    )
    assert ok == {"new_password": "abc12345"}

    with pytest.raises(ValidationError) as exc:
        validators.validate_password_confirm_payload(
            {"new_password": "abc", "confirm_password": "def"}
        )

    assert "confirm_password" in exc.value.message_dict
