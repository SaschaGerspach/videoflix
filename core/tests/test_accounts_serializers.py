import pytest
from django.contrib.auth import get_user_model

from accounts.api import serializers as api_serializers


pytestmark = pytest.mark.django_db


def test_registration_serializer_validates_and_normalizes():
    serializer = api_serializers.RegistrationSerializer(
        {"email": "USER@example.com", "password": "pw", "confirmed_password": "pw"}
    )
    assert serializer.is_valid() is True
    assert serializer.validated_data["email"] == "user@example.com"
    assert serializer.errors == {}


def test_registration_serializer_collects_errors():
    serializer = api_serializers.RegistrationSerializer(
        {"email": "", "password": "a", "confirmed_password": "b"}
    )
    assert serializer.is_valid() is False
    assert "email" in serializer.errors or "confirmed_password" in serializer.errors


def test_activation_serializer_requires_fields():
    serializer = api_serializers.ActivationSerializer({"uidb64": "", "token": ""})
    assert serializer.is_valid() is False
    assert "non_field_errors" in serializer.errors


def test_login_serializer_rejects_missing_payload():
    serializer = api_serializers.LoginSerializer({"email": "", "password": ""})
    assert serializer.is_valid() is False
    assert "non_field_errors" in serializer.errors


def test_logout_and_refresh_serializers_always_pass():
    assert api_serializers.LogoutSerializer({}).is_valid() is True
    assert api_serializers.TokenRefreshSerializer({}).is_valid() is True


def test_password_reset_serializer_needs_existing_user():
    user_model = get_user_model()
    user_model.objects.create_user(
        email="exists@example.com", username="exists@example.com", password="x"
    )

    ok = api_serializers.PasswordResetSerializer({"email": "exists@example.com"})
    assert ok.is_valid() is True

    bad = api_serializers.PasswordResetSerializer({"email": ""})
    assert bad.is_valid() is False
    assert "email" in bad.errors


def test_format_validation_error_handles_message_dict():
    from django.core.exceptions import ValidationError

    err = ValidationError({"field": ["msg"]})
    formatted = api_serializers.format_validation_error(err)
    assert formatted == {"field": ["msg"]}

    err = ValidationError(["oops"])
    formatted = api_serializers.format_validation_error(err)
    assert formatted == {"non_field_errors": ["oops"]}


def test_password_confirm_serializer_detects_mismatch():
    serializer = api_serializers.PasswordConfirmSerializer(
        {"new_password": "abc", "confirm_password": "def"}
    )
    assert serializer.is_valid() is False
    assert "confirm_password" in serializer.errors
