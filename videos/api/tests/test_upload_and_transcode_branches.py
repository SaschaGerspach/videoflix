from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from jobs.domain import services as job_services
from jobs.domain.services import TranscodeError
from videos.domain.choices import VideoCategory
from videos.domain.models import Video


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user_pair():
    User = get_user_model()
    owner = User.objects.create_user(
        username="owner", email="owner@example.com", password="secret"
    )
    viewer = User.objects.create_user(
        username="viewer", email="viewer@example.com", password="secret"
    )
    return owner, viewer


@pytest.fixture
def video_record(user_pair):
    owner, _ = user_pair
    return Video.objects.create(
        owner=owner,
        title="Branch Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )


def test_transcode_requires_authentication(video_record):
    client = APIClient()
    response = client.post(f"/api/video/{video_record.id}/transcode/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "non_field_errors" in response.json().get("errors", {})


def test_transcode_permission_denied(user_pair, video_record, monkeypatch):
    owner, viewer = user_pair
    client = APIClient()
    client.force_authenticate(user=viewer)
    monkeypatch.setattr("videos.domain.selectors.resolve_public_id", lambda _id: video_record.id)
    response = client.post(f"/api/video/{video_record.id}/transcode/")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["errors"]["non_field_errors"][0].startswith("You do not have permission")


def test_transcode_returns_error_when_enqueue_fails(user_pair, video_record, monkeypatch):
    owner, _ = user_pair
    client = APIClient()
    client.force_authenticate(user=owner)
    monkeypatch.setattr("videos.domain.selectors.resolve_public_id", lambda _id: video_record.id)
    monkeypatch.setattr("jobs.domain.services.is_transcode_locked", lambda _vid: False)

    def raise_error(*args, **kwargs):
        raise TranscodeError("Video source not found.", status_code=status.HTTP_404_NOT_FOUND)

    monkeypatch.setattr("jobs.domain.services.enqueue_transcode", raise_error)

    response = client.post(f"/api/video/{video_record.id}/transcode/")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["errors"]["non_field_errors"] == ["Video source not found."]


def test_upload_requires_authentication(video_record):
    client = APIClient()
    response = client.post(f"/api/video/{video_record.id}/upload/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_upload_permission_denied(user_pair, video_record):
    owner, viewer = user_pair
    client = APIClient()
    client.force_authenticate(user=viewer)
    file_obj = SimpleUploadedFile("sample.mp4", b"video-bytes", content_type="video/mp4")
    response = client.post(
        f"/api/video/{video_record.id}/upload/",
        data={"file": file_obj},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_upload_missing_file_returns_validation_error(user_pair, video_record):
    owner, _ = user_pair
    client = APIClient()
    client.force_authenticate(user=owner)
    response = client.post(f"/api/video/{video_record.id}/upload/")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["errors"]["file"] == ["No video file provided."]


def test_transcode_success_with_query_parameters(user_pair, video_record, monkeypatch):
    owner, _ = user_pair
    client = APIClient()
    client.force_authenticate(user=owner)
    monkeypatch.setattr("videos.domain.selectors.resolve_public_id", lambda _id: video_record.id)
    monkeypatch.setattr("jobs.domain.services.is_transcode_locked", lambda _vid: False)
    enqueue_calls = []

    def fake_enqueue(video_id, target_resolutions, *, force=False):
        enqueue_calls.append((video_id, list(target_resolutions)))
        return {"job_id": "job-1", "queue": "transcode"}

    monkeypatch.setattr("jobs.domain.services.enqueue_transcode", fake_enqueue)

    response = client.post(
        f"/api/video/{video_record.id}/transcode/",
        data={},
        HTTP_ACCEPT="application/json",
        QUERY_STRING="res=480p,720p",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert enqueue_calls == [(video_record.id, ["480p", "720p"])]


def test_upload_success_triggers_enqueue(user_pair, video_record, media_root, monkeypatch):
    owner, _ = user_pair
    client = APIClient()
    client.force_authenticate(user=owner)
    enqueue_calls = []

    def fake_manifest_exists(video_id, resolution):
        return False

    def fake_enqueue(video_id, target_resolutions, *, force=False):
        enqueue_calls.append((video_id, list(target_resolutions)))

    monkeypatch.setattr("jobs.domain.services.manifest_exists_for_resolution", fake_manifest_exists)
    monkeypatch.setattr("jobs.domain.services.is_transcode_locked", lambda _vid: False)
    monkeypatch.setattr("jobs.domain.services.enqueue_transcode", fake_enqueue)

    upload_file = SimpleUploadedFile("upload.mp4", b"dummy", content_type="video/mp4")
    response = client.post(
        f"/api/video/{video_record.id}/upload/",
        data={"file": upload_file},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert enqueue_calls == [(video_record.id, list(job_services.ALLOWED_TRANSCODE_PROFILES.keys()))]
    stored_path = job_services.get_video_source_path(video_record.id)
    assert stored_path.exists()
