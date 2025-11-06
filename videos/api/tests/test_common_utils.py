from __future__ import annotations

from django.core.exceptions import ValidationError

from videos.api.views.common import _format_validation_error


def test_format_validation_error_with_message_dict():
    error = ValidationError({"name": ["required"]})

    result = _format_validation_error(error)

    assert result == {"name": ["required"]}


def test_format_validation_error_with_messages_list():
    error = ValidationError(["bad request"])

    result = _format_validation_error(error)

    assert result == {"non_field_errors": ["bad request"]}


def test_format_validation_error_default_branch():
    error = ValidationError("broken")

    result = _format_validation_error(error)

    assert result == {"non_field_errors": ["broken"]}
