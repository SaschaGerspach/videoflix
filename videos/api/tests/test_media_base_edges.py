from __future__ import annotations

from types import SimpleNamespace
import pytest
from django.http import HttpResponse
from rest_framework.exceptions import NotAcceptable

from videos.api.views.media_base import (
    M3U8Renderer,
    MediaSegmentBaseView,
    _debug_not_found,
    _set_cache_headers,
    _user_can_access,
    force_json_response,
)


class _DummyView(MediaSegmentBaseView):
    media_renderer_class = M3U8Renderer
    allowed_accept_types = ("application/vnd.apple.mpegurl",)


def test_set_cache_headers_sets_etag_and_cache_control(tmp_path):
    target = tmp_path / "manifest.m3u8"
    target.write_bytes(b"#EXTM3U")
    response = HttpResponse()
    _set_cache_headers(response, target)
    assert "Cache-Control" in response
    assert response["Cache-Control"].startswith("public")
    assert response["ETag"].startswith('"') and len(response["ETag"]) > 10


def test_debug_not_found_sets_header_when_debug(settings):
    settings.DEBUG = True
    response = HttpResponse(status=404)
    _debug_not_found(response, "missing")
    assert response["X-Debug-Why"] == "missing"


def test_ensure_accept_header_allows_wildcard():
    view = _DummyView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": "text/plain, */*"})
    view._ensure_accept_header(request, expected_media_type="application/json")


def test_ensure_accept_header_block_invalid():
    view = _DummyView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": "text/plain"})
    with pytest.raises(NotAcceptable):
        view._ensure_accept_header(request, expected_media_type="application/vnd.apple.mpegurl")


def test_accept_header_matches_allowed_type():
    view = _DummyView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": "application/vnd.apple.mpegurl"})
    assert view._accept_allows(request)


def test_accepts_json_only_detects_json_media_type():
    view = _DummyView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": "application/json"})
    assert view._accepts_json_only(request) is True
    request_any = SimpleNamespace(META={"HTTP_ACCEPT": "*/*"})
    assert view._accepts_json_only(request_any) is False


def test_force_json_response_sets_renderer():
    payload = {"detail": "ok"}
    response = force_json_response(payload, status_code=201)
    assert response.status_code == 201
    assert response["Content-Type"] == "application/json"
    assert response.data == payload


def test_user_can_access_local_bypass(settings):
    settings.DEBUG = True
    settings.DEV_HLS_AUTH_BYPASS = True
    request = SimpleNamespace(
        META={"REMOTE_ADDR": "127.0.0.1"},
        method="GET",
        user=None,
    )
    video = SimpleNamespace(is_published=True)
    assert _user_can_access(request, video) is True


def test_user_can_access_owner_authenticated():
    request = SimpleNamespace(
        META={},
        method="GET",
        user=SimpleNamespace(is_authenticated=True, id=1, is_staff=False, is_superuser=False),
    )
    video = SimpleNamespace(owner_id=1, is_published=False)
    assert _user_can_access(request, video) is True
