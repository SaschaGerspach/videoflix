from __future__ import annotations

import io
import json

from django.core.management import call_command
from django.test import override_settings


def _run_print_config() -> dict:
    buffer = io.StringIO()
    call_command("print_config", stdout=buffer)
    raw = buffer.getvalue().strip()
    assert raw, "print_config did not emit any output"
    return json.loads(raw)


def test_print_config_outputs_expected_fields():
    payload = _run_print_config()

    assert "default_from_email" in payload
    assert "frontend_domain" in payload
    assert isinstance(payload.get("rq"), dict)
    assert {"queue_transcode", "redis_url"} <= set(payload["rq"].keys())


@override_settings(
    DEFAULT_FROM_EMAIL="alerts@example.com",
    FRONTEND_DOMAIN="https://app.example.com",
    PUBLIC_MEDIA_BASE="https://cdn.example.com",
)
def test_print_config_reflects_runtime_overrides():
    payload = _run_print_config()

    assert payload["default_from_email"] == "alerts@example.com"
    assert payload["frontend_domain"] == "https://app.example.com"
    assert payload["public_media_base"] == "https://cdn.example.com"
