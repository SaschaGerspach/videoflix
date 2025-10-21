from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from videos.domain.models import Video

pytestmark = pytest.mark.django_db


def _create_video(**overrides: Any) -> Video:
    defaults = {
        "title": "Sample Title",
        "description": "Sample Description",
        "thumbnail_url": "http://example.com/sample.jpg",
        "category": "drama",
    }
    defaults.update(overrides)
    return Video.objects.create(**defaults)


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
    )
    _create_video(
        title="Another Movie",
        description="Another Description",
        thumbnail_url="http://example.com/media/thumbnail/image2.jpg",
        category="romance",
    )

    client = _authenticated_client()
    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2
    first = payload[0]
    assert {"id", "created_at", "title", "description", "thumbnail_url", "category"} <= set(first.keys())
    titles = {item["title"] for item in payload}
    assert titles == {"Movie Title", "Another Movie"}


def test_video_list_handles_empty_collection() -> None:
    client = _authenticated_client()

    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_video_list_returns_deterministic_ordering() -> None:
    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    older = _create_video(title="Older", category="comedy")
    latest = _create_video(title="Latest", category="action")
    middle = _create_video(title="Middle", category="drama")

    Video.objects.filter(pk=older.pk).update(created_at=base_time - timedelta(days=1))
    Video.objects.filter(pk=middle.pk).update(created_at=base_time)
    Video.objects.filter(pk=latest.pk).update(created_at=base_time + timedelta(days=1))

    newest_duplicate = _create_video(title="Newest Duplicate", category="romance")
    Video.objects.filter(pk=newest_duplicate.pk).update(created_at=base_time + timedelta(days=1))

    client = _authenticated_client()

    response = client.get(reverse("video-list"))

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert [item["title"] for item in payload] == [
        "Newest Duplicate",
        "Latest",
        "Middle",
        "Older",
    ]
    assert payload[0]["created_at"] == payload[1]["created_at"]
    assert payload[0]["id"] > payload[1]["id"]


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
