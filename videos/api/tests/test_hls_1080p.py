from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.domain.models import Video, VideoSegment, VideoStream

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


def create_user(username="hls1080") -> object:
    User = get_user_model()
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )


def create_video_with_1080p() -> Video:
    video = Video.objects.create(
        title="Stream 1080p",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )
    stream = VideoStream.objects.create(
        video=video,
        resolution="1080p",
        manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n",
    )
    VideoSegment.objects.create(
        stream=stream,
        name="000.ts",
        content=b"segment-bytes",
    )

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "1080p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    (hls_dir / "index.m3u8").write_text(
        "#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8"
    )
    (hls_dir / "000.ts").write_bytes(b"segment-bytes")
    return video


def _auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_manifest_1080p_success(media_root):
    video = create_video_with_1080p()
    client = _auth_client(create_user())

    response = client.get(
        f"/api/video/{video.pk}/1080p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    assert (
        b"".join(response.streaming_content)
        == (
            Path(media_root) / "hls" / str(video.pk) / "1080p" / "index.m3u8"
        ).read_bytes()
    )


def test_segment_1080p_success(media_root):
    video = create_video_with_1080p()
    client = _auth_client(create_user("segment-1080"))

    response = client.get(
        f"/api/video/{video.pk}/1080p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 200
    assert (
        b"".join(response.streaming_content)
        == (Path(media_root) / "hls" / str(video.pk) / "1080p" / "000.ts").read_bytes()
    )
