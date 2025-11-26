import pytest
from django.test import override_settings

from accounts.domain.utils import build_frontend_url


@pytest.mark.parametrize(
    ("frontend_domain", "action", "expected_prefix"),
    [
        (
            "frontend.local",
            "activate",
            "http://frontend.local/pages/auth/activate.html?",
        ),
        (
            "https://app.videoflix.local",
            "reset",
            "https://app.videoflix.local/pages/auth/confirm_password.html?",
        ),
    ],
)
def test_build_frontend_url_handles_schema(frontend_domain, action, expected_prefix):
    with override_settings(FRONTEND_DOMAIN=frontend_domain):
        url = build_frontend_url(action, uidb64="uid123", token="token456")

    assert url.startswith(expected_prefix)
    assert "uid=uid123" in url
    assert url.endswith("token=token456")
