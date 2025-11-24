from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from jobs.domain import services as transcode_services
from videos.domain.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


@pytest.fixture
def user():
    user_model = get_user_model()
    return user_model.objects.create_user(
        username="uploader@example.com",
        email="uploader@example.com",
        password="securepass123",
    )


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    client.user = user
    return client


def upload_url(video_id: int) -> str:
    return reverse("video-upload", kwargs={"video_id": video_id})


def _create_video(owner) -> Video:
    return Video.objects.create(
        owner=owner,
        title="Sample",
        description="Sample description",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
    )


def _create_manifest(video_id: int, resolution: str) -> Path:
    manifest_path = (
        transcode_services.get_transcode_output_dir(video_id, resolution) / "index.m3u8"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n", encoding="utf-8")
    return manifest_path


def test_upload_owner_saves_file_and_queues_missing_profiles(
    auth_client, user, monkeypatch
):
    video = _create_video(user)
    _create_manifest(video.id, "360p")

    enqueue_mock = MagicMock()
    monkeypatch.setattr(transcode_services, "enqueue_transcode", enqueue_mock)

    upload_file = SimpleUploadedFile(
        "video.mp4", b"dummy-content", content_type="video/mp4"
    )

    response = auth_client.post(
        upload_url(video.id),
        data={"file": upload_file},
        format="multipart",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {"detail": "Upload ok", "video_id": video.id}

    source_path = transcode_services.get_video_source_path(video.id)
    assert source_path.exists()
    assert source_path.read_bytes() == b"dummy-content"

    enqueue_mock.assert_called_once_with(
        video.id, target_resolutions=["480p", "720p", "1080p"]
    )


def test_upload_with_existing_renditions_skips_transcode(
    auth_client, user, monkeypatch
):
    video = _create_video(user)
    for resolution in transcode_services.ALLOWED_TRANSCODE_PROFILES:
        _create_manifest(video.id, resolution)

    enqueue_mock = MagicMock()
    monkeypatch.setattr(transcode_services, "enqueue_transcode", enqueue_mock)

    upload_file = SimpleUploadedFile("video.mp4", b"content", content_type="video/mp4")

    response = auth_client.post(
        upload_url(video.id),
        data={"file": upload_file},
        format="multipart",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {"detail": "Upload ok", "video_id": video.id}
    enqueue_mock.assert_not_called()


def test_upload_requires_file(auth_client, user):
    video = _create_video(user)
    response = auth_client.post(
        upload_url(video.id),
        data={},
        format="multipart",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.json()
    assert "file" in payload.get("errors", {})


def test_upload_requires_authentication():
    client = APIClient()
    upload_file = SimpleUploadedFile("video.mp4", b"content", content_type="video/mp4")
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="owner@example.com",
        email="owner@example.com",
        password="secret123",
    )
    video = _create_video(owner)

    response = client.post(
        upload_url(video.id),
        data={"file": upload_file},
        format="multipart",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_upload_rejected_for_non_owner(auth_client, user):
    other_user = get_user_model().objects.create_user(
        username="other@example.com",
        email="other@example.com",
        password="pass1234",
    )
    video = _create_video(other_user)

    upload_file = SimpleUploadedFile("video.mp4", b"content", content_type="video/mp4")
    response = auth_client.post(
        upload_url(video.id),
        data={"file": upload_file},
        format="multipart",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "errors": {
            "non_field_errors": ["You do not have permission to modify this video."]
        }
    }


def test_upload_rejects_non_mp4(auth_client, user):
    video = _create_video(user)
    upload_file = SimpleUploadedFile(
        "video.avi", b"content", content_type="video/x-msvideo"
    )

    response = auth_client.post(
        upload_url(video.id),
        data={"file": upload_file},
        format="multipart",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.json()
    assert "file" in payload.get("errors", {})


def test_upload_rejects_large_file(auth_client, user, settings):
    settings.VIDEO_UPLOAD_MAX_BYTES = 10
    video = _create_video(user)
    upload_file = SimpleUploadedFile(
        "video.mp4", b"0123456789ABC", content_type="video/mp4"
    )

    response = auth_client.post(
        upload_url(video.id),
        data={"file": upload_file},
        format="multipart",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.json()
    assert "file" in payload.get("errors", {})
