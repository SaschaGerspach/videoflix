from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_user():
    User = get_user_model()
    return User.objects.create_user(
        username="hls-user",
        email="hls-user@example.com",
        password="pass",
    )


@pytest.fixture
def api_client(auth_user):
    client = APIClient()
    client.force_authenticate(user=auth_user)
    return client


def _create_video():
    video = Video.objects.create(
        title="Test Video",
        description="Desc",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    return video


def _collect_response_bytes(response) -> bytes:
    if hasattr(response, "_collected_stream"):
        return response._collected_stream
    if hasattr(response, "streaming_content"):
        data = b"".join(response.streaming_content)
        response._collected_stream = data
        response.streaming_content = iter((data,))
        return data
    return response.content


def test_manifest_resolves_public_id_serves_file(
    settings, tmp_path, api_client, auth_user, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    resolution = "480p"
    video = _create_video()
    real_id = video.id
    VideoStream.objects.create(
        video=video,
        resolution=resolution,
        manifest="#EXTM3U\n#EXTINF:10,\nsegment.ts\n",
    )

    hls_path = Path(tmp_path) / "hls" / str(real_id) / resolution
    hls_path.mkdir(parents=True)
    (hls_path / "index.m3u8").write_text("#EXTM3U\n#EXTINF:10,\nsegment.ts\n", encoding="utf-8")
    (hls_path / "segment.ts").write_bytes(b"TS")

    resolver = lambda public_id: real_id  # noqa: E731
    monkeypatch.setattr("videos.api.views.manifest.resolve_public_id", resolver)

    response = api_client.get(
        f"/api/video/123/{resolution}/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.apple.mpegurl")
    assert _collect_response_bytes(response).startswith(b"#EXTM3U")


def test_segment_resolves_public_id_serves_file(
    settings, tmp_path, api_client, auth_user, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    resolution = "480p"
    video = _create_video()
    real_id = video.id
    stream = VideoStream.objects.create(
        video=video,
        resolution=resolution,
        manifest="#EXTM3U\n",
    )
    VideoSegment.objects.create(
        stream=stream,
        name="000.ts",
        content=b"DB",
    )

    hls_path = Path(tmp_path) / "hls" / str(real_id) / resolution
    hls_path.mkdir(parents=True)
    (hls_path / "000.ts").write_bytes(b"TS")

    resolver = lambda public_id: real_id  # noqa: E731
    monkeypatch.setattr("videos.api.views.segment.resolve_public_id", resolver)

    response = api_client.get(
        f"/api/video/456/{resolution}/000.ts/",
        HTTP_ACCEPT="video/mp2t",
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "video/MP2T"
    assert _collect_response_bytes(response) == b"TS"


def test_manifest_missing_file_returns_404_after_mapping(
    settings, tmp_path, api_client, auth_user, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    resolution = "480p"
    video = _create_video()
    real_id = video.id
    VideoStream.objects.create(
        video=video,
        resolution=resolution,
        manifest="",
    )

    resolver = lambda public_id: real_id  # noqa: E731
    monkeypatch.setattr("videos.api.views.manifest.resolve_public_id", resolver)

    response = api_client.get(
        f"/api/video/789/{resolution}/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 404
    assert response.json() == {
        "errors": {"non_field_errors": ["Video manifest not found."]},
    }


def test_manifest_requires_authentication(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    resolution = "480p"
    video = _create_video()
    real_id = video.id
    VideoStream.objects.create(
        video=video,
        resolution=resolution,
        manifest="#EXTM3U\n",
    )

    hls_path = Path(tmp_path) / "hls" / str(real_id) / resolution
    hls_path.mkdir(parents=True)
    (hls_path / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    resolver = lambda public_id: real_id  # noqa: E731
    monkeypatch.setattr("videos.api.views.manifest.resolve_public_id", resolver)

    client = APIClient()

    response = client.get(
        f"/api/video/999/{resolution}/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 401
    assert response.json() == {
        "errors": {
            "non_field_errors": ["Authentication credentials were not provided."]
        }
    }


def test_segment_requires_authentication(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    resolution = "480p"
    video = _create_video()
    real_id = video.id
    stream = VideoStream.objects.create(
        video=video,
        resolution=resolution,
        manifest="#EXTM3U\n",
    )
    VideoSegment.objects.create(
        stream=stream,
        name="000.ts",
        content=b"DB",
    )

    hls_path = Path(tmp_path) / "hls" / str(real_id) / resolution
    hls_path.mkdir(parents=True)
    (hls_path / "000.ts").write_bytes(b"TS")

    resolver = lambda public_id: real_id  # noqa: E731
    monkeypatch.setattr("videos.api.views.segment.resolve_public_id", resolver)

    client = APIClient()

    response = client.get(
        f"/api/video/1000/{resolution}/000.ts/",
        HTTP_ACCEPT="video/mp2t",
    )

    assert response.status_code == 401
    assert response.json() == {
        "errors": {
            "non_field_errors": ["Authentication credentials were not provided."]
        }
    }
