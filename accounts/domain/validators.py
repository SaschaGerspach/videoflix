from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q

from .utils import normalize_email


def validate_registration_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Validate registration payload values and return normalized data."""
    payload = payload or {}
    errors: dict[str, list[str]] = {}

    raw_email = payload.get("email")
    password = payload.get("password")
    confirmed_password = payload.get("confirmed_password")

    if not raw_email or not str(raw_email).strip():
        errors["email"] = ["Email is required."]
    if not password:
        errors["password"] = ["Password is required."]
    if confirmed_password is None:
        errors["confirmed_password"] = ["confirmed_password is required."]
    elif password != confirmed_password:
        errors["confirmed_password"] = ["Passwords do not match."]

    if errors:
        raise ValidationError(errors)

    email = normalize_email(str(raw_email))

    user_model = get_user_model()
    if user_model.objects.filter(
        Q(email__iexact=email) | Q(username__iexact=email)
    ).exists():
        raise ValidationError({"email": ["A user with this email already exists."]})

    return {"email": email, "password": password}


def validate_activation_params(payload: dict[str, Any]) -> dict[str, str]:
    """Validate activation payload consisting of uidb64 and token."""
    payload = payload or {}
    errors: list[str] = []

    raw_uidb64 = payload.get("uidb64")
    raw_token = payload.get("token")

    uidb64 = str(raw_uidb64).strip() if raw_uidb64 is not None else ""
    token = str(raw_token).strip() if raw_token is not None else ""

    if not uidb64:
        errors.append("uidb64 is required.")
    if not token:
        errors.append("token is required.")

    if errors:
        raise ValidationError({"non_field_errors": errors})

    return {"uidb64": uidb64, "token": token}


def validate_login_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Validate login payload values and return normalized data."""
    payload = payload or {}
    raw_email = payload.get("email")
    password = payload.get("password")

    if not raw_email or not str(raw_email).strip() or not password:
        raise ValidationError({"non_field_errors": ["Invalid credentials."]})

    email = normalize_email(str(raw_email))

    return {"email": email, "password": str(password)}


def validate_password_reset_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Validate password reset payload and ensure user exists."""
    payload = payload or {}
    raw_email = payload.get("email")

    if not raw_email or not str(raw_email).strip():
        raise ValidationError({"email": ["Email is required."]})

    email = normalize_email(str(raw_email))
    user_model = get_user_model()

    if not user_model.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": ["User with this email does not exist."]})

    return {"email": email}


def validate_password_confirm_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Validate password confirmation payload and return new password."""
    payload = payload or {}
    new_password = payload.get("new_password")
    confirm_password = payload.get("confirm_password")

    errors: dict[str, list[str]] = {}

    if not new_password:
        errors["new_password"] = ["new_password is required."]
    if not confirm_password:
        errors.setdefault("confirm_password", []).append(
            "confirm_password is required."
        )
    if new_password and confirm_password and new_password != confirm_password:
        errors["confirm_password"] = ["Passwords do not match."]

    if errors:
        raise ValidationError(errors)

    return {"new_password": str(new_password)}
