from __future__ import annotations

from django.core.exceptions import ValidationError

from videos.api.views.common import _format_validation_error, set_public_cache_headers


def test_set_public_cache_headers_with_max_age():
    from django.http import HttpResponse

    response = HttpResponse()
    set_public_cache_headers(response, max_age=60)
    assert "Cache-Control" in response
    value = response["Cache-Control"]
    assert "max-age=60" in value
    assert "no-cache" not in value


def test_format_validation_error_uses_message_dict():
    error = ValidationError({"file": ["bad"]})
    payload = _format_validation_error(error)
    assert payload["file"] == ["bad"]
