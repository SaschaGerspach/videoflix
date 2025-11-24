from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.exceptions import ParseError
from rest_framework.test import APIRequestFactory, force_authenticate

from videos.api.views.upload import VideoUploadView
from videos.domain.choices import VideoCategory
from videos.domain.models import Video


pytestmark = pytest.mark.django_db


@pytest.fixture
def upload_env(monkeypatch, tmp_path, settings):
    from jobs.domain import services as transcode_services

    settings.MEDIA_ROOT = tmp_path.as_posix()
    monkeypatch.setattr(VideoUploadView, "throttle_classes", [], raising=False)
    storage_dir = tmp_path / "sources"
    storage_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        transcode_services,
        "ALLOWED_TRANSCODE_PROFILES",
        {"720p": {}, "1080p": {}},
        raising=False,
    )
    monkeypatch.setattr(
        transcode_services,
        "get_video_source_path",
        lambda video_id: storage_dir / f"{video_id}.mp4",
    )
    manifest_state: dict[tuple[int, str], bool] = {}

    def manifest_exists(video_id: int, resolution: str) -> bool:
        return manifest_state.get((video_id, resolution), False)

    monkeypatch.setattr(
        transcode_services,
        "manifest_exists_for_resolution",
        manifest_exists,
    )
    calls: dict[str, list[tuple[int, tuple[str, ...]]]] = {"enqueue": []}

    def enqueue(video_id: int, target_resolutions: list[str]):
        calls["enqueue"].append((video_id, tuple(target_resolutions)))
        return {"job_id": "job-1"}

    monkeypatch.setattr(transcode_services, "enqueue_transcode", enqueue)

    lock_state = {"locked": False}
    monkeypatch.setattr(
        "videos.api.views.upload.is_transcode_locked",
        lambda video_id: lock_state["locked"],
    )

    return SimpleNamespace(
        manifest_state=manifest_state,
        calls=calls,
        lock_state=lock_state,
        storage_dir=storage_dir,
    )


def _create_user(email="u@example.com", **extra):
    User = get_user_model()
    return User.objects.create_user(email, email, "pass", **extra)


def _create_video(owner, title="Video"):
    return Video.objects.create(
        owner=owner,
        title=title,
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )


def test_upload_parse_error_returns_400():
    user = _create_user()
    view = VideoUploadView()

    class BrokenRequest:
        def __init__(self, user):
            self.user = user
            self.FILES = {}

        @property
        def data(self):
            raise ParseError("broken payload")

    response = view.post(BrokenRequest(user), video_id=1)
    assert response.status_code == 400
    assert "broken payload" in response.data["errors"]["non_field_errors"][0]


def test_upload_missing_file_returns_400(upload_env):
    factory = APIRequestFactory()
    user = _create_user()
    request = factory.post("/api/video/1/upload/", {}, format="multipart")
    force_authenticate(request, user=user)
    response = VideoUploadView.as_view()(request, video_id=1)
    assert response.status_code == 400
    assert "No video file provided" in response.data["errors"]["file"][0]


def test_upload_invalid_extension_rejected(upload_env):
    factory = APIRequestFactory()
    user = _create_user()
    file = SimpleUploadedFile("clip.txt", b"invalid", content_type="video/mp4")
    request = factory.post("/api/video/1/upload/", {"file": file}, format="multipart")
    force_authenticate(request, user=user)
    response = VideoUploadView.as_view()(request, video_id=1)
    assert response.status_code == 400
    assert "Filename must end with .mp4." in response.data["errors"]["file"][0]


def test_upload_video_not_found_returns_404(upload_env):
    factory = APIRequestFactory()
    owner = _create_user()
    file = SimpleUploadedFile("clip.mp4", b"data", content_type="video/mp4")
    request = factory.post("/api/video/99/upload/", {"file": file}, format="multipart")
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=99)
    assert response.status_code == 404


def test_upload_permission_denied(upload_env):
    factory = APIRequestFactory()
    owner = _create_user("owner@example.com")
    outsider = _create_user("outsider@example.com")
    video = _create_video(owner)
    file = SimpleUploadedFile("clip.mp4", b"data", content_type="video/mp4")
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=outsider)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 403


def test_upload_file_too_large(upload_env, settings):
    factory = APIRequestFactory()
    owner = _create_user("owner2@example.com")
    video = _create_video(owner)
    settings.VIDEO_UPLOAD_MAX_BYTES = 1
    file = SimpleUploadedFile(
        "clip.mp4", b"more than one byte", content_type="video/mp4"
    )
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 400
    assert "File too large" in response.data["errors"]["file"][0]


def test_upload_skips_enqueue_when_complete(upload_env):
    factory = APIRequestFactory()
    owner = _create_user("owner3@example.com")
    video = _create_video(owner)
    upload_env.manifest_state[(video.id, "720p")] = True
    upload_env.manifest_state[(video.id, "1080p")] = True

    file = SimpleUploadedFile("clip.mp4", b"content", content_type="video/mp4")
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 201
    assert upload_env.calls["enqueue"] == []


def test_upload_respects_transcode_lock(upload_env):
    factory = APIRequestFactory()
    owner = _create_user("owner4@example.com")
    video = _create_video(owner)
    upload_env.lock_state["locked"] = True

    file = SimpleUploadedFile("clip.mp4", b"content", content_type="video/mp4")
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 201
    assert response.data["video_id"] == video.id


def test_upload_handles_transcode_error(upload_env, monkeypatch):
    from jobs.domain import services as transcode_services

    factory = APIRequestFactory()
    owner = _create_user("owner5@example.com")
    video = _create_video(owner)

    class Boom(transcode_services.TranscodeError):
        status_code = 422

        def __init__(self):
            super().__init__("bad profile", status_code=422)

    def fail_enqueue(video_id, target_resolutions):
        raise Boom()

    monkeypatch.setattr(transcode_services, "enqueue_transcode", fail_enqueue)

    file = SimpleUploadedFile("clip.mp4", b"content", content_type="video/mp4")
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 422
    assert "bad profile" in response.data["errors"]["non_field_errors"][0]


def test_upload_handles_validation_error(upload_env, monkeypatch):
    from jobs.domain import services as transcode_services
    from django.core.exceptions import ValidationError

    factory = APIRequestFactory()
    owner = _create_user("owner6@example.com")
    video = _create_video(owner)

    def fail_enqueue(video_id, target_resolutions):
        raise ValidationError({"file": ["invalid data"]})

    monkeypatch.setattr(transcode_services, "enqueue_transcode", fail_enqueue)

    file = SimpleUploadedFile("clip.mp4", b"content", content_type="video/mp4")
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 400
    assert "invalid data" in response.data["errors"]["file"][0]


def test_upload_successful_enqueue(upload_env):
    factory = APIRequestFactory()
    owner = _create_user("owner7@example.com")
    video = _create_video(owner)

    file = SimpleUploadedFile("clip.mp4", b"content", content_type="video/mp4")
    request = factory.post(
        f"/api/video/{video.id}/upload/", {"file": file}, format="multipart"
    )
    force_authenticate(request, user=owner)
    response = VideoUploadView.as_view()(request, video_id=video.id)
    assert response.status_code == 201
    assert upload_env.calls["enqueue"]
