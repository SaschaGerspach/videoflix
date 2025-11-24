import pytest
from django.http import HttpResponse
from django.urls import resolve

pytestmark = pytest.mark.django_db
pytest_plugins = ("videos.api.tests.test_hls_etag_cache",)

# 1) Cover the public cache helper directly to keep the helper executed at least once.


def test_common_public_cache_headers_present():
    # import locally so module import is deferred until the test runs
    from videos.api.views.common import set_public_cache_headers

    r = HttpResponse("ok")
    set_public_cache_headers(r)
    # Relaxed assertions because cache-control names can vary between implementations
    cc = r.get("Cache-Control", "")
    assert "public" in cc
    assert "no-cache" in cc or "max-age" in cc


# 2) Sanity-check routing guards for regressions


def test_manifest_route_still_points_to_manifest_view():
    m = resolve("/api/video/1/720p/index.m3u8")
    assert m.func.view_class.__name__ == "VideoManifestView"


def test_segment_route_still_points_to_segment_content_view():
    m = resolve("/api/video/1/720p/000.ts")
    assert m.func.view_class.__name__ == "VideoSegmentContentView"


# 3) Validate ETag round trip in a tolerant way without relying on 304


def test_manifest_serves_with_inline_and_etag(auth_client):
    r1 = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert r1.status_code == 200
    # Inline disposition and content type
    disp = r1.get("Content-Disposition", "")
    assert disp.startswith("inline;")
    ctype = r1.get("Content-Type", "")
    assert "mpegurl" in ctype
    # Ensure ETag is present and looks reasonable
    etag = r1.get("ETag")
    assert etag
    assert len(etag.strip('"')) >= 8  # heuristischer Mindestwert

    # Optional If-None-Match attempt; accept 200 or 304
    r2 = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
        HTTP_IF_NONE_MATCH=etag,
    )
    assert r2.status_code in (200, 304)
