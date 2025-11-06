from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


@pytest.fixture
def allow_test_hosts(settings):
    settings.ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
    settings.DEBUG = True
    return settings


def _make_access_token(user) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user.pk,
        "username": user.username,
        "type": "access",
        "jti": "test-token",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@pytest.mark.django_db
def test_cookie_auth_non_json_accept_headers(allow_test_hosts):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="cookieuser",
        email="cookie@example.com",
        password="pass1234",
        is_active=True,
    )

    client = APIClient()
    client.cookies["access_token"] = _make_access_token(user)

    response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert response.status_code in {200, 404}

    response = client.get(
        "/api/video/1/480p/segment.ts/",
        HTTP_ACCEPT="video/MP2T",
        follow=True,
    )
    assert response.status_code in {200, 404}


@pytest.mark.django_db
def test_login_sets_cookie_that_allows_hls_requests(allow_test_hosts, settings, client, tmp_path):
    user_model = get_user_model()
    password = "pass1234"
    user = user_model.objects.create_user(
        username="login-cookie-user",
        email="login-cookie@example.com",
        password=password,
        is_active=True,
    )

    from videos.domain.models import Video, VideoSegment, VideoStream
    from videos.domain.choices import VideoCategory

    settings.MEDIA_ROOT = tmp_path
    video = Video.objects.create(
        title="Stream Test",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    manifest_body = "#EXTM3U\n#EXTINF:10,\n000.ts\n"
    stream = VideoStream.objects.create(video=video, resolution="480p", manifest=manifest_body)
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"segment-bytes")

    login_response = client.post(
        "/api/login/",
        {"email": user.email, "password": password},
        HTTP_ACCEPT="application/json",
    )
    assert login_response.status_code == 200
    assert "access_token" in client.cookies

    manifest_response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert manifest_response.status_code == 200
    wildcard_manifest = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="*/*",
    )
    assert wildcard_manifest.status_code == 200

    manifest_with_slash = client.get(
        "/api/video/1/480p/index.m3u8/",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert manifest_with_slash.status_code == 200

    segment_with_slash = client.get(
        "/api/video/1/480p/000.ts/",
        HTTP_ACCEPT="video/MP2T",
    )
    assert segment_with_slash.status_code == 200

    segment_without_slash = client.get(
        "/api/video/1/480p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )
    assert segment_without_slash.status_code == 200
    wildcard_segment = client.get(
        "/api/video/1/480p/000.ts",
        HTTP_ACCEPT="*/*",
    )
    assert wildcard_segment.status_code == 200


class _MockRequest:
    def __init__(self, raw_cookie_header: str):
        self.COOKIES = {}
        self.META = {"HTTP_COOKIE": raw_cookie_header}
        self._request = type(
            "UnderlyingRequest",
            (),
            {"COOKIES": {}, "META": {"HTTP_COOKIE": raw_cookie_header}},
        )()


@pytest.mark.django_db
def test_cookie_authentication_reads_raw_cookie_header(allow_test_hosts):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="raw-header",
        email="raw@example.com",
        password="secret123",
    )
    token = _make_access_token(user)
    raw_cookie = f"refresh_token=dummy; access_token={token}; other=1"

    request = _MockRequest(raw_cookie)

    from accounts.domain.authentication import CookieJWTAuthentication

    authenticator = CookieJWTAuthentication()
    authenticated_user, _ = authenticator.authenticate(request)

    assert authenticated_user == user


@pytest.mark.django_db
def test_hls_endpoints_require_cookie(allow_test_hosts, settings, tmp_path):
    from videos.domain.models import Video, VideoSegment, VideoStream
    from videos.domain.choices import VideoCategory

    settings.MEDIA_ROOT = tmp_path
    video = Video.objects.create(
        title="No Cookie",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    stream = VideoStream.objects.create(video=video, resolution="480p", manifest="#EXTM3U\n")
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"segment-bytes")

    client = APIClient()

    manifest_response = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )
    assert manifest_response.status_code == 401
    wildcard_manifest = client.get(
        "/api/video/1/480p/index.m3u8",
        HTTP_ACCEPT="*/*",
    )
    assert wildcard_manifest.status_code == 401

    segment_with_slash = client.get(
        "/api/video/1/480p/000.ts/",
        HTTP_ACCEPT="video/MP2T",
    )
    assert segment_with_slash.status_code == 401

    segment_without_slash = client.get(
        "/api/video/1/480p/000.ts",
        HTTP_ACCEPT="video/MP2T",
    )
    assert segment_without_slash.status_code == 401


@pytest.mark.django_db
def test_debug_auth_returns_json_regardless_of_accept(allow_test_hosts, client):
    response = client.get(
        "/api/_debug/auth",
        HTTP_ACCEPT="text/plain",
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    payload = response.json()
    assert {"seen_access_cookie", "user_authenticated", "user_id"}.issubset(payload.keys())
