from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from rest_framework.exceptions import NotAcceptable

from videos.api.views.media_base import MediaSegmentBaseView, _set_cache_headers, _user_can_access


pytest_plugins = ("videos.api.tests.test_hls_etag_cache",)
pytestmark = pytest.mark.django_db


class _StubView(MediaSegmentBaseView):
    media_renderer_class = None
    allowed_accept_types = ("application/vnd.apple.mpegurl",)


def test_accept_header_missing_allows_request():
    view = _StubView()
    request = SimpleNamespace(META={})
    assert view._accept_allows(request, expected_media_type="application/vnd.apple.mpegurl")


def test_accept_header_star_allows_any():
    view = _StubView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": "*/*"})
    view._ensure_accept_header(request, expected_media_type="application/json")


def test_accept_header_invalid_type_raises():
    view = _StubView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": "image/png"})
    with pytest.raises(NotAcceptable):
        view._ensure_accept_header(request, expected_media_type="application/vnd.apple.mpegurl")


def test_set_cache_headers_assigns_etag(tmp_path):
    candidate = tmp_path / "sample.bin"
    candidate.write_bytes(b"payload")
    response = HttpResponse(b"body", content_type="application/octet-stream")
    _set_cache_headers(response, candidate)
    assert response["Cache-Control"].startswith("public")
    assert response["ETag"].startswith('"')


def test_manifest_etag_allows_if_none_match_304(auth_client):
    initial = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert initial.status_code == 200
    etag = initial["ETag"]

    cached = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
        HTTP_IF_NONE_MATCH=etag,
    )
    assert cached.status_code in (200, 304)
    assert cached["ETag"] == etag


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("", True),
        ("*/*", True),
        ("application/vnd.apple.mpegurl", True),
        ("application/json", False),
    ],
)
def test_accept_header_matrix(header, expected):
    view = _StubView()
    request = SimpleNamespace(META={"HTTP_ACCEPT": header} if header else {})
    assert view._accept_allows(request, expected_media_type="application/vnd.apple.mpegurl") is expected


def test_manifest_request_sets_cache_headers_and_inline(auth_client):
    response = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    payload = b"".join(response.streaming_content)
    assert len(payload) > 0
    assert response["Cache-Control"].startswith("public")
    assert response["Content-Disposition"].startswith('inline; filename="index.m3u8"')
    etag = response["ETag"]

    cached = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
        HTTP_IF_NONE_MATCH=etag,
    )
    assert cached.status_code in (200, 304)
    assert cached["ETag"] == etag


def test_segment_request_enforces_cache_headers(auth_client):
    response = auth_client.get(
        "/api/video/1/720p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )
    body = b"".join(response.streaming_content)
    assert len(body) > 0
    assert response["Cache-Control"].startswith("public")
    etag = response["ETag"]

    cached = auth_client.get(
        "/api/video/1/720p/000.ts",
        HTTP_ACCEPT="video/MP2T",
        HTTP_IF_NONE_MATCH=etag,
    )
    assert cached.status_code in (200, 304)
    assert cached["ETag"] == etag


def test_local_bypass_allows_inline_delivery(settings):
    settings.DEBUG = True
    settings.DEV_HLS_AUTH_BYPASS = True
    request = SimpleNamespace(
        META={"REMOTE_ADDR": "127.0.0.1"},
        method="GET",
        user=SimpleNamespace(is_authenticated=False, is_staff=False, is_superuser=False, id=None),
    )
    video = SimpleNamespace(owner_id=None, is_published=True)

    assert _user_can_access(request, video) is True
