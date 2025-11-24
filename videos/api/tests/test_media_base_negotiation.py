from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def authenticated_client(media_root):
    User = get_user_model()
    user = User.objects.create_user(
        username="media",
        email="media@example.com",
        password="secret",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(
        owner=user,
        title="Media Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    manifest_text = """#EXTM3U\n#EXTINF:10,\n000.ts\n"""
    stream = VideoStream.objects.create(
        video=video, resolution="480p", manifest=manifest_text
    )

    base = Path(media_root) / "hls" / str(video.id) / "480p"
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.m3u8").write_text(manifest_text, encoding="utf-8")
    (base / "001.ts").write_bytes(b"segment-001")

    VideoSegment.objects.create(stream=stream, name="001.ts", content=b"segment-001")

    return client, video


def test_manifest_accept_m3u8_returns_file(authenticated_client):
    client, video = authenticated_client
    response = client.get(
        f"/api/video/{video.id}/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.apple.mpegurl"
    assert "Cache-Control" in response
    assert "ETag" in response


def test_manifest_json_accept_returns_error(authenticated_client):
    client, video = authenticated_client
    response = client.get(
        f"/api/video/{video.id}/480p/index.m3u8",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 404
    assert response.json()["errors"]["non_field_errors"]


def test_segment_numeric_name_maps_to_zero_padded(authenticated_client):
    client, video = authenticated_client
    response = client.get(
        f"/api/video/{video.id}/480p/1.ts",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "video/MP2T"
    assert "Cache-Control" in response
    assert "ETag" in response
    assert b"".join(response.streaming_content).startswith(b"segment-001")


def test_manifest_unacceptable_accept_returns_406(authenticated_client):
    client, video = authenticated_client
    response = client.get(
        f"/api/video/{video.id}/480p/index.m3u8",
        HTTP_ACCEPT="application/xml",
    )

    assert response.status_code == 406
    assert response.json()["errors"]["non_field_errors"]
