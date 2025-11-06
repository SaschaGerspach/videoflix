from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.api.views import M3U8Renderer, TSRenderer
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream


@pytest.fixture(autouse=True)
def allow_test_hosts(settings):
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    return settings


def _make_access_token(user) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user.pk,
        "username": user.username,
        "type": "access",
        "jti": "hls-token",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@pytest.mark.django_db
def test_manifest_streams_entire_file(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="streamer",
        email="stream@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Test Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    stream = VideoStream.objects.create(video=video, resolution="480p", manifest="#EXTM3U\n")

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "480p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = hls_dir / "index.m3u8"
    manifest_content = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\n000.ts\n"
    manifest_path.write_text(manifest_content, encoding="utf-8")

    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"")
    segment_path = hls_dir / "000.ts"
    segment_bytes = b"\x00\x01test-bytes\x02"
    segment_path.write_bytes(segment_bytes)

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    assert response["Content-Type"] == M3U8Renderer.media_type
    manifest_size = manifest_path.stat().st_size
    assert int(response["Content-Length"]) == manifest_size
    body = b"".join(response.streaming_content)
    assert len(body) == manifest_size
    assert body == manifest_path.read_bytes()

    segment_response = client.get(
        "/api/video/1/480p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )
    assert segment_response.status_code == 200
    assert segment_response["Content-Type"] == TSRenderer.media_type
    segment_size = segment_path.stat().st_size
    assert int(segment_response["Content-Length"]) == segment_size
    segment_body = b"".join(segment_response.streaming_content)
    assert len(segment_body) == segment_size
    assert segment_body == segment_path.read_bytes()


@pytest.mark.django_db
def test_manifest_serves_without_db_and_self_heals_index(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="selfheal",
        email="selfheal@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        id=42,
        title="Self-heal Video",
        description="",
        thumbnail_url="http://example.com/selfheal.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "720p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = hls_dir / "index.m3u8"
    manifest_body = "#EXTM3U\n#EXTINF:10,\n000.ts\n#EXTINF:10,\n001.ts\n"
    manifest_path.write_text(manifest_body, encoding="utf-8")
    (hls_dir / "000.ts").write_bytes(b"segment-0")
    (hls_dir / "001.ts").write_bytes(b"segment-1")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/720p/index.m3u8",
        HTTP_ACCEPT=M3U8Renderer.media_type,
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content)
    assert body == manifest_path.read_bytes()

    stream = VideoStream.objects.get(video=video, resolution="720p")
    assert stream.manifest.replace("\r\n", "\n") == manifest_body
    assert stream.segments.count() == 2


@pytest.mark.django_db
def test_manifest_missing_returns_404_json(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="viewer",
        email="viewer@example.com",
        password="secret",
        is_active=True,
    )

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 404
    assert response.json() == {"errors": {"non_field_errors": ["Video manifest not found."]}}


@pytest.mark.django_db
def test_manifest_stub_file_returns_404(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="stub-user",
        email="stub@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Stub Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="480p", manifest="")

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "480p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    (hls_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 404
    assert response.json() == {"errors": {"non_field_errors": ["Video manifest not found."]}}


@pytest.mark.django_db
def test_manifest_stub_db_returns_404(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="stub-db-user",
        email="stubdb@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Stub DB Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="480p", manifest="#EXTM3U\n")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 404
    assert response.json() == {"errors": {"non_field_errors": ["Video manifest not found."]}}


@pytest.mark.django_db
def test_manifest_served_even_when_resolution_not_allowed(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ALLOWED_RENDITIONS = ("480p",)
    user = get_user_model().objects.create_user(
        username="fs-first",
        email="fs-first@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Filesystem Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="720p", manifest="#EXTM3U\n")

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "720p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = hls_dir / "index.m3u8"
    manifest_path.write_text("#EXTM3U\n#EXTINF:1,\n000.ts\n", encoding="utf-8")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/720p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content)
    assert body == manifest_path.read_bytes()


@pytest.mark.django_db
def test_manifest_disallowed_resolution_sets_debug_header(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.DEBUG = True
    settings.ALLOWED_RENDITIONS = ("480p",)
    user = get_user_model().objects.create_user(
        username="blocked",
        email="blocked@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Blocked Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="1080p", manifest="#EXTM3U\n")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/1080p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 404
    assert response["X-Debug-Why"] == "resolution-not-allowed"


@pytest.mark.django_db
def test_manifest_served_for_1080p_resolution(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="manifest-1080",
        email="manifest1080@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Manifest 1080p",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(video=video, resolution="1080p", manifest="#EXTM3U\n")

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "1080p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = hls_dir / "index.m3u8"
    manifest_path.write_text("#EXTM3U\n#EXTINF:2,\n000.ts\n", encoding="utf-8")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/1080p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == manifest_path.read_bytes()


@pytest.mark.django_db
def test_segment_served_for_1080p_resolution(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = get_user_model().objects.create_user(
        username="segment-1080",
        email="segment1080@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Segment 1080p",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    stream = VideoStream.objects.create(video=video, resolution="1080p", manifest="#EXTM3U\n")

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "1080p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    segment_path = hls_dir / "000.ts"
    segment_payload = b"segment-1080"
    segment_path.write_bytes(segment_payload)
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"db-payload")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/1080p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == segment_payload


@pytest.mark.django_db
def test_segment_served_even_when_resolution_not_allowed(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ALLOWED_RENDITIONS = ("480p",)
    user = get_user_model().objects.create_user(
        username="segment-fs",
        email="segment@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Segment Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    stream = VideoStream.objects.create(video=video, resolution="720p", manifest="#EXTM3U\n")

    hls_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "720p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    segment_path = hls_dir / "005.ts"
    payload = b"0123456789"
    segment_path.write_bytes(payload)
    VideoSegment.objects.create(stream=stream, name="005.ts", content=payload)

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/720p/5.ts",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content)
    assert body == payload


@pytest.mark.django_db
def test_segment_disallowed_resolution_sets_debug_header(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.DEBUG = True
    settings.ALLOWED_RENDITIONS = ("480p",)
    user = get_user_model().objects.create_user(
        username="segment-blocked",
        email="segment-blocked@example.com",
        password="secret",
        is_active=True,
    )

    video = Video.objects.create(
        title="Segment Blocked",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    stream = VideoStream.objects.create(video=video, resolution="1080p", manifest="#EXTM3U\n")
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"blocked")

    client = APIClient()
    client.cookies[settings.ACCESS_COOKIE_NAME] = _make_access_token(user)

    response = client.get(
        f"/api/video/{video.pk}/1080p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 404
    assert response["X-Debug-Why"] == "resolution-not-allowed"
