from __future__ import annotations


import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream


pytestmark = pytest.mark.django_db


@pytest.fixture
def media_root(tmp_path, settings):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(root)
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    return root


@pytest.fixture
def hls_environment(media_root):
    User = get_user_model()
    owner = User.objects.create_user("owner@example.com", "owner@example.com", "pass")
    viewer = User.objects.create_user(
        "viewer@example.com", "viewer@example.com", "pass"
    )
    video = Video.objects.create(
        owner=owner,
        title="HLS Video",
        description="",
        thumbnail_url="http://example.com/video.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n"
    )

    rendition_dir = media_root / "hls" / str(video.id) / "720p"
    rendition_dir.mkdir(parents=True, exist_ok=True)
    (rendition_dir / "index.m3u8").write_text(
        "#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8"
    )
    (rendition_dir / "000.ts").write_bytes(b"segment-bytes")

    return {"video": video, "viewer": viewer}


@pytest.fixture
def auth_client(hls_environment):
    client = APIClient()
    client.force_authenticate(user=hls_environment["viewer"])
    client.user = hls_environment["viewer"]
    return client


def test_manifest_etag_and_304(auth_client):
    first = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert first.status_code == 200
    etag = first.get("ETag")
    assert etag

    second = auth_client.get(
        "/api/video/1/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
        HTTP_IF_NONE_MATCH=etag,
    )
    assert second.status_code in (200, 304)


def test_segment_content_type_and_inline(auth_client):
    response = auth_client.get(
        "/api/video/1/720p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("inline;")
    assert "Content-Type" in response
