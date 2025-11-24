import subprocess

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from jobs.domain import services as transcode_services
from videos.domain.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def create_user():
    def _create(
        email: str, password: str = "securepassword123", *, is_active: bool = True
    ):
        user_model = get_user_model()
        return user_model.objects.create_user(
            username=email,
            email=email,
            password=password,
            is_active=is_active,
        )

    return _create


@pytest.fixture
def authenticated_client(create_user):
    user = create_user("transcoder@example.com")
    client = APIClient()
    client.force_authenticate(user=user)
    client.user = user
    return client


@pytest.fixture
def owner_client(video):
    client = APIClient()
    client.force_authenticate(user=video.owner)
    client.user = video.owner
    return client


@pytest.fixture
def admin_client(create_user):
    admin = create_user("admin@example.com")
    admin.is_staff = True
    admin.is_superuser = True
    admin.save()
    client = APIClient()
    client.force_authenticate(user=admin)
    client.user = admin
    return client


@pytest.fixture
def video(create_user):
    owner = create_user("owner@example.com")
    return Video.objects.create(
        owner=owner,
        title="Sample Video",
        description="Sample Description",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )


@pytest.fixture
def video_source_file(video, media_root):
    source_path = transcode_services.get_video_source_path(video.id)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"dummy video content")
    return source_path


@pytest.fixture
def mock_ffmpeg(monkeypatch):
    def _fake_run(cmd, check, stdout=None, stderr=None):
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(transcode_services.subprocess, "run", _fake_run)
    return _fake_run


def transcode_url(video_id: int) -> str:
    return reverse("video-transcode", kwargs={"video_id": video_id})


def test_transcode_requires_auth(video):
    client = APIClient()
    response = client.post(transcode_url(video.id), {}, format="json")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    payload = response.json()
    assert "errors" in payload


def test_transcode_owner_can_transcode(
    owner_client, video, video_source_file, mock_ffmpeg
):
    response = owner_client.post(transcode_url(video.id), {}, format="json")

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == {"detail": "Transcode accepted", "video_id": video.id}


def test_transcode_forbidden_for_non_owner(authenticated_client, video):
    response = authenticated_client.post(transcode_url(video.id), {}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {
        "errors": {
            "non_field_errors": ["You do not have permission to modify this video."]
        }
    }


def test_transcode_allowed_for_admin(
    admin_client, video, video_source_file, mock_ffmpeg
):
    response = admin_client.post(transcode_url(video.id), {}, format="json")

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == {"detail": "Transcode accepted", "video_id": video.id}


def test_transcode_404_for_unknown_video(authenticated_client):
    response = authenticated_client.post(transcode_url(9999), {}, format="json")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"errors": {"non_field_errors": ["Video not found."]}}


def test_transcode_409_when_already_processing(
    owner_client, video, video_source_file, mock_ffmpeg
):
    cache.set(transcode_services.transcode_lock_key(video.id), True, timeout=900)

    response = owner_client.post(transcode_url(video.id), {}, format="json")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "errors": {"non_field_errors": ["Transcode already in progress."]}
    }


def test_transcode_validates_resolutions(owner_client, video, video_source_file):
    payload = {"resolutions": ["360p", "bogus"]}

    response = owner_client.post(transcode_url(video.id), payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {"resolutions": ["Invalid value 'bogus'."]}}


def test_transcode_gracefully_handles_missing_ffmpeg(
    owner_client, video, video_source_file, monkeypatch
):
    def _missing_binary(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(transcode_services.subprocess, "run", _missing_binary)

    response = owner_client.post(transcode_url(video.id), {}, format="json")

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json() == {"errors": {"non_field_errors": ["ffmpeg not found"]}}


def test_transcode_accepts_query_resolutions_ok(
    owner_client, video, video_source_file, mock_ffmpeg
):
    response = owner_client.post(
        f"{transcode_url(video.id)}?res=360p", {}, format="json"
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.json() == {"detail": "Transcode accepted", "video_id": video.id}


def test_transcode_rejects_unknown_resolution(owner_client, video):
    response = owner_client.post(
        f"{transcode_url(video.id)}?res=999p", {}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"errors": {"res": ["Unsupported resolution '999p'"]}}
