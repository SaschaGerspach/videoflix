from __future__ import annotations

from typing import Any, Dict

from django.core.exceptions import ValidationError

from accounts.domain.validators import (validate_activation_params,
                                        validate_login_payload,
                                        validate_password_confirm_payload,
                                        validate_password_reset_payload,
                                        validate_registration_payload)


class RegistrationSerializer:
    """Serialize and validate incoming registration payloads."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, Any] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        try:
            self.validated_data = validate_registration_payload(self.initial_data)
            self._errors = {}
            return True
        except ValidationError as exc:
            self.validated_data = {}
            self._errors = format_validation_error(exc)
            if raise_exception:
                raise
            return False

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


class ActivationSerializer:
    """Serialize activation parameters passed via URL path."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, str] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        try:
            self.validated_data = validate_activation_params(self.initial_data)
            self._errors = {}
            return True
        except ValidationError as exc:
            self.validated_data = {}
            self._errors = format_validation_error(exc)
            if raise_exception:
                raise
            return False

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


class LoginSerializer:
    """Serialize and validate login payloads."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, str] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        try:
            self.validated_data = validate_login_payload(self.initial_data)
            self._errors = {}
            return True
        except ValidationError as exc:
            self.validated_data = {}
            self._errors = format_validation_error(exc)
            if raise_exception:
                raise
            return False

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


class LogoutSerializer:
    """Serialize logout payloads ensuring JSON parsing succeeds."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, Any] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        self.validated_data = {}
        self._errors = {}
        return True

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


class TokenRefreshSerializer:
    """Accept refresh requests without additional payload requirements."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, Any] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        self.validated_data = {}
        self._errors = {}
        return True

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


class PasswordResetSerializer:
    """Serialize and validate password reset requests."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, str] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        try:
            self.validated_data = validate_password_reset_payload(self.initial_data)
            self._errors = {}
            return True
        except ValidationError as exc:
            self.validated_data = {}
            self._errors = format_validation_error(exc)
            if raise_exception:
                raise
            return False

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


class PasswordConfirmSerializer:
    """Serialize and validate password confirmation payloads."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self.initial_data = data or {}
        self._errors: Dict[str, list[str]] = {}
        self.validated_data: Dict[str, str] = {}

    def is_valid(self, *, raise_exception: bool = False) -> bool:
        try:
            self.validated_data = validate_password_confirm_payload(self.initial_data)
            self._errors = {}
            return True
        except ValidationError as exc:
            self.validated_data = {}
            self._errors = format_validation_error(exc)
            if raise_exception:
                raise
            return False

    @property
    def errors(self) -> Dict[str, list[str]]:
        return self._errors


def format_validation_error(error: ValidationError) -> Dict[str, list[str]]:
    if hasattr(error, "message_dict"):
        return {key: [str(message) for message in messages] for key, messages in error.message_dict.items()}
    return {"non_field_errors": [str(message) for message in error.messages]}
