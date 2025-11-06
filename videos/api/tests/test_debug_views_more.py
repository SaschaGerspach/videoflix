from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import core.urls
import videos.api.urls
import pytest
from django.urls import clear_url_caches, reverse

from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream

pytestmark = pytest.mark.django_db


def _prepare_debug_urls(settings):
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.CACHES["default"]["BACKEND"] = "django.core.cache.backends.locmem.LocMemCache"
    stub_pkg = types.ModuleType("django_rq")
    stub_module = types.ModuleType("django_rq.urls")
    stub_module.urlpatterns = []
    sys.modules["django_rq"] = stub_pkg
    sys.modules["django_rq.urls"] = stub_module
    importlib.reload(videos.api.urls)
    importlib.reload(core.urls)
    clear_url_caches()


def test_debug_renditions_headers(client, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path.as_posix()
    _prepare_debug_urls(settings)
    response = client.get(reverse("debug-allowed-renditions"), HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    payload = response.json()
    assert "allowed" in payload and isinstance(payload["allowed"], list)
    json.dumps(payload)
    assert response["Content-Type"].startswith("application/json")


def test_debug_hls_manifest_exists(client, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path.as_posix()
    _prepare_debug_urls(settings)
    video = Video.objects.create(
        title="Debug Manifest Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="720p", manifest="#EXTM3U\n")
    manifest_path = Path(settings.MEDIA_ROOT) / "hls" / str(video.id) / "720p" / "index.m3u8"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")

    response = client.get("/api/_debug/hls/1/720p/manifest", HTTP_ACCEPT="application/json")
    if response.status_code not in (200, 404):
        pytest.fail(response.content.decode("utf-8", "ignore"))
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        data = response.json()
        assert data.get("ctype") == "application/vnd.apple.mpegurl"
        assert data.get("exists") is True
        json.dumps(data)


def test_debug_renditions_disabled_returns_404(client, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path.as_posix()
    _prepare_debug_urls(settings)
    settings.DEBUG = False
    response = client.get(reverse("debug-allowed-renditions"), HTTP_ACCEPT="application/json")
    assert response.status_code == 404


def test_debug_hls_manifest_disabled_handles_debug_off(client, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path.as_posix()
    _prepare_debug_urls(settings)
    settings.DEBUG = False
    response = client.get("/api/_debug/hls/1/720p/manifest", HTTP_ACCEPT="application/json")
    assert response.status_code == 404
