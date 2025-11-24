from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory

from videos.api.serializers import VideoSerializer
from videos.api.views.debug import HLSManifestDebugView, ThumbsDebugView
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_debug_view_not_available_when_debug_false(settings):
    settings.DEBUG = False
    request = APIRequestFactory().get("/api/_debug/hls/1/480p/manifest")
    response = HLSManifestDebugView.as_view()(request, pub=1, res="480p")
    assert response.status_code == 404


def test_debug_view_reports_manifest_details(settings, media_root, monkeypatch):
    settings.DEBUG = True
    User = get_user_model()
    owner = User.objects.create_user("dbg", "dbg@example.com", "secret")
    video = Video.objects.create(
        owner=owner,
        title="Debug Video",
        description="",
        thumbnail_url="http://example.com/dbg.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="480p", manifest="#EXTM3U")
    monkeypatch.setattr(
        "videos.api.views.debug.resolve_public_id", lambda pub: video.id
    )

    manifest_path = Path(media_root) / "hls" / str(video.id) / "480p" / "index.m3u8"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")

    request = APIRequestFactory().get(f"/api/_debug/hls/{video.id}/480p/manifest")
    response = HLSManifestDebugView.as_view()(request, pub=video.id, res="480p")
    payload = response.data
    assert response.status_code == 200
    assert payload["exists"] is True
    assert payload["size"] == manifest_path.stat().st_size
    assert payload["ctype"] == "application/vnd.apple.mpegurl"


def test_thumbs_debug_view_reports_absolute_url(settings, media_root):
    settings.DEBUG = True
    settings.PUBLIC_MEDIA_BASE = "https://thumbs.example.com"
    user_model = get_user_model()
    owner = user_model.objects.create_user("thumbs", "thumbs@example.com", "secret")
    video = Video.objects.create(
        owner=owner,
        title="Thumb Debug",
        description="",
        thumbnail_url="http://example.com/t.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    thumb_path = Path(media_root) / "thumbs" / str(video.id) / "default.jpg"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.write_bytes(b"binary-thumb")

    request = APIRequestFactory().get(f"/api/_debug/thumbs/{video.id}")
    response = ThumbsDebugView.as_view()(request, public=video.id)
    assert response.status_code == 200
    payload = response.data
    assert payload["exists"] is True
    assert payload["bytes"] == thumb_path.stat().st_size

    serializer = VideoSerializer(instance=video, context={"request": request})
    expected_url = serializer.data["thumbnail_url"]
    assert payload["url"] == expected_url
