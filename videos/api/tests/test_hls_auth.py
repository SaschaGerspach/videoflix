from __future__ import annotations

import datetime
from pathlib import Path

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.api.views import M3U8Renderer, TSRenderer
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream

pytestmark = pytest.mark.django_db


def _create_user(username="hls-user"):
    User = get_user_model()
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )


def _create_video(owner: Video | None = None) -> Video:
    kwargs = {"owner": owner} if owner else {}
    return Video.objects.create(
        title="Auth Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
        **kwargs,
    )


def _issue_access_cookie(user_id: int) -> str:
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "user_id": user_id,
        "type": "access",
        "iat": now,
        "exp": now + datetime.timedelta(minutes=5),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@pytest.fixture
def published_video(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    video = _create_video()
    resolution = "480p"
    manifest_body = "#EXTM3U\n#EXTINF:10,\n000.ts\n"
    stream = VideoStream.objects.create(
        video=video,
        resolution=resolution,
        manifest=manifest_body,
    )
    VideoSegment.objects.create(
        stream=stream,
        name="000.ts",
        content=b"",
    )

    base = Path(tmp_path) / "hls" / str(video.id) / resolution
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.m3u8").write_text(manifest_body, encoding="utf-8")
    (base / "000.ts").write_bytes(b"TS")

    return video, resolution


def _auth_client(user) -> APIClient:
    client = APIClient()
    token = _issue_access_cookie(user.id)
    client.cookies[settings.ACCESS_COOKIE_NAME] = token
    return client


def test_m3u8_requires_auth_cookie(published_video):
    video, resolution = published_video
    client = APIClient()

    response = client.get(
        f"/api/video/1/{resolution}/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 401


def test_m3u8_with_cookie_ok(published_video):
    video, resolution = published_video
    user = _create_user()
    client = _auth_client(user)

    response = client.get(
        f"/api/video/1/{resolution}/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert response.status_code == 200
    assert response["Content-Type"] == M3U8Renderer.media_type


def test_ts_requires_auth_cookie(published_video):
    video, resolution = published_video
    client = APIClient()

    response = client.get(
        f"/api/video/1/{resolution}/000.ts/",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 401


def test_ts_with_cookie_ok(published_video):
    video, resolution = published_video
    user = _create_user("segment-user")
    client = _auth_client(user)

    response = client.get(
        f"/api/video/1/{resolution}/000.ts/",
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == 200
    assert response["Content-Type"] == TSRenderer.media_type


def test_manifest_json_accept_returns_404_payload(published_video):
    video, resolution = published_video
    user = _create_user("json-accept")
    client = _auth_client(user)

    response = client.get(
        f"/api/video/1/{resolution}/index.m3u8",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 404
    assert response.json() == {
        "errors": {"non_field_errors": ["Video manifest not found."]}
    }
