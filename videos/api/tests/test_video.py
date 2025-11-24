from datetime import datetime, timedelta, UTC
from typing import Any
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from videos.api.serializers import VideoSerializer
from videos.domain.models import Video
from videos.domain.utils import find_manifest_path

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


def _ensure_hls_ready(video_id: int, res: str = "480p") -> None:
    manifest_path = find_manifest_path(video_id, res)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n#EXTINF:10,\nsegment.ts\n", encoding="utf-8")
    (manifest_path.parent / "segment.ts").write_bytes(b"x")


def _create_video(**overrides: Any) -> Video:
    hls_ready = overrides.pop("hls_ready", True)
    base_defaults = {
        "title": "Sample Title",
        "description": "Sample Description",
        "thumbnail_url": "http://example.com/sample.jpg",
        "category": "drama",
    }
    defaults = dict(base_defaults)
    user_model = get_user_model()
    if "owner" not in overrides:
        unique_id = uuid4()
        defaults["owner"] = user_model.objects.create_user(
            email=f"owner-{unique_id}@example.com",
            username=f"owner-{unique_id}@example.com",
            password="pass",
        )
    if "is_published" not in overrides:
        defaults["is_published"] = True
    defaults.update(overrides)
    video = Video.objects.create(**defaults)
    if hls_ready and video.is_published:
        _ensure_hls_ready(video.pk)
    return video


def _authenticated_client(user=None) -> APIClient:
    client = APIClient()
    user_model = get_user_model()
    if user is None:
        user = user_model.objects.create_user(
            email="viewer@example.com", username="viewer@example.com", password="pass"
        )
    client.force_authenticate(user=user)
    return client


def test_video_list_returns_videos_for_authenticated_user() -> None:
    _create_video(
        title="Movie Title",
        description="Movie Description",
        thumbnail_url="http://example.com/media/thumbnail/image.jpg",
        category="drama",
        is_published=True,
    )
    _create_video(
        title="Another Movie",
        description="Another Description",
        thumbnail_url="http://example.com/media/thumbnail/image2.jpg",
        category="romance",
        is_published=True,
    )
    _create_video(
        title="Hidden Draft",
        description="Should stay hidden",
        thumbnail_url="http://example.com/media/thumbnail/image3.jpg",
        category="comedy",
        is_published=False,
    )

    client = _authenticated_client()
    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2
    first = payload[0]
    assert {
        "id",
        "created_at",
        "title",
        "description",
        "thumbnail_url",
        "category",
    } <= set(first.keys())
    titles = {item["title"] for item in payload}
    assert titles == {"Movie Title", "Another Movie"}


def test_video_list_handles_empty_collection() -> None:
    client = _authenticated_client()

    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_video_list_returns_deterministic_ordering() -> None:
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    older = _create_video(title="Older", category="comedy")
    latest = _create_video(title="Latest", category="action")
    middle = _create_video(title="Middle", category="drama")

    Video.objects.filter(pk=older.pk).update(created_at=base_time - timedelta(days=1))
    Video.objects.filter(pk=middle.pk).update(created_at=base_time)
    Video.objects.filter(pk=latest.pk).update(created_at=base_time + timedelta(days=1))

    newest_duplicate = _create_video(title="Newest Duplicate", category="romance")
    Video.objects.filter(pk=newest_duplicate.pk).update(
        created_at=base_time + timedelta(days=1)
    )

    client = _authenticated_client()

    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert [item["title"] for item in payload] == [
        "Latest",
        "Newest Duplicate",
        "Middle",
        "Older",
    ]
    assert payload[0]["created_at"] == payload[1]["created_at"]
    assert [item["id"] for item in payload] == [1, 2, 3, 4]


def test_video_list_requires_authentication() -> None:
    _create_video()
    client = APIClient()

    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_video_list_rejects_invalid_json_body() -> None:
    client = _authenticated_client()
    raw_body = b'{"invalid":'  # malformed JSON

    response = client.generic(
        "GET",
        reverse("video-list"),
        data=raw_body,
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    errors = response.json().get("errors", {})
    assert "non_field_errors" in errors
    assert errors["non_field_errors"][0].startswith("Invalid JSON")


def test_video_list_is_idempotent() -> None:
    _create_video(title="Idempotent Movie")
    client = _authenticated_client()

    first_response = client.get(reverse("video-list"))
    second_response = client.get(reverse("video-list"))

    assert first_response.status_code == status.HTTP_200_OK
    assert second_response.status_code == status.HTTP_200_OK
    assert first_response.json() == second_response.json()


def test_video_serializer_uses_frontend_origin(settings, media_root):
    settings.FRONTEND_BASE_URL = "https://static.example.com/app"
    settings.DEBUG = False
    video = _create_video()
    thumb_path = media_root / "thumbs" / str(video.id) / "default.jpg"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.write_bytes(b"img")

    serializer = VideoSerializer(instance=video, context={})
    payload = serializer.data
    url = payload["thumbnail_url"]
    assert url.startswith("https://static.example.com")
    assert url.endswith(f"/media/thumbs/{video.id}/default.jpg")


def test_video_serializer_returns_empty_string_for_missing_thumbnail(media_root):
    video = _create_video()
    serializer = VideoSerializer(instance=video, context={})
    assert serializer.data["thumbnail_url"] == ""
