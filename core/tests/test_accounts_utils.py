from django.test import RequestFactory, override_settings

from accounts.domain import utils


def test_normalize_email_strips_and_lowercases():
    assert utils.normalize_email("  Foo@Example.Com  ") == "foo@example.com"


@override_settings(FRONTEND_DOMAIN="frontend.local", DEV_FRONTEND_ORIGIN="")
def test_build_frontend_url_uses_frontend_domain():
    url = utils.build_frontend_url("activate", uidb64="uid123", token="tok456")
    assert url.startswith("http://frontend.local/pages/auth/activate.html?")
    assert "uid=uid123" in url
    assert url.endswith("token=tok456")


@override_settings(PUBLIC_FRONTEND_BASE="https://app.example.com")
def test_build_frontend_url_prefers_public_frontend_base():
    url = utils.build_frontend_url("reset", uidb64="uid", token="tok")
    assert url.startswith("https://app.example.com/pages/auth/confirm_password.html?")


def test_build_logo_url_prefers_request_absolute_uri():
    request = RequestFactory().get("/whatever")
    logo_url = utils.build_logo_url(request)
    assert logo_url.startswith("http://testserver")
    assert logo_url.endswith("/static/email/logo_icon.svg")


def test_build_logo_url_without_request_returns_relative_static_path():
    logo_url = utils.build_logo_url(None)
    assert logo_url.endswith("/static/email/logo_icon.svg")


@override_settings(PUBLIC_MEDIA_BASE="https://cdn.example.com")
def test_build_logo_url_uses_public_media_base_when_no_request():
    logo_url = utils.build_logo_url(None)
    assert logo_url.startswith("https://cdn.example.com")
    assert logo_url.endswith("/static/email/logo_icon.svg")


@override_settings(
    PUBLIC_MEDIA_BASE="", FRONTEND_BASE_URL="frontend.example.com", FRONTEND_DOMAIN=""
)
def test_build_logo_url_falls_back_to_frontend_base():
    logo_url = utils.build_logo_url(None)
    assert logo_url.endswith("/static/email/logo_icon.svg")


def test_ensure_scheme_defaults():
    assert utils._ensure_scheme("") == "http://localhost:3000"
    assert utils._ensure_scheme("example.com") == "http://example.com"
