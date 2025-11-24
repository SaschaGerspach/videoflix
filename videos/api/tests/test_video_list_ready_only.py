from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from videos.domain.models import Video
from videos.domain.utils import find_manifest_path

pytestmark = pytest.mark.django_db


def create_user(prefix: str = "user"):
    user_model = get_user_model()
    unique = uuid4()
    return user_model.objects.create_user(
        email=f"{prefix}-{unique}@example.com",
        username=f"{prefix}-{unique}@example.com",
        password="pass",
    )


def create_video(owner, **overrides) -> Video:
    defaults = {
        "title": "Sample Title",
        "description": "Sample Description",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": "drama",
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(owner=owner, **defaults)


def write_manifest(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def manifest_ready() -> str:
    return "#EXTM3U\n#EXTINF:10,\nsegment.ts\n"


def manifest_stub() -> str:
    return "#EXTM3U\n"


def authenticated_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    client.user = user
    return client


def video_list_url() -> str:
    return reverse("video-list")


def test_video_list_ready_only_filters_stub_and_missing(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path
    viewer = create_user("viewer")

    video_ready_a = create_video(viewer, title="Ready Video A")
    video_ready_b = create_video(viewer, title="Ready Video B")
    video_stub = create_video(viewer, title="Stub Video")
    video_missing = create_video(viewer, title="Missing Manifest")

    for video_ready in (video_ready_a, video_ready_b):
        ready_manifest_path = find_manifest_path(video_ready.id)
        write_manifest(ready_manifest_path, manifest_ready())
        (ready_manifest_path.parent / "segment.ts").write_bytes(b"x")

    stub_manifest_path = find_manifest_path(video_stub.id)
    write_manifest(stub_manifest_path, manifest_stub())

    client = authenticated_client(viewer)

    response_default = client.get(video_list_url())
    assert response_default.status_code == 200
    payload_default = response_default.json()
    titles_default = [item["title"] for item in payload_default]
    assert titles_default == ["Ready Video B", "Ready Video A"]
    first_entry = payload_default[0]
    assert {
        "id",
        "created_at",
        "title",
        "description",
        "thumbnail_url",
        "category",
    } <= set(first_entry.keys())

    response_ready = client.get(video_list_url(), {"ready_only": "1"})
    assert response_ready.status_code == 200
    titles_ready = [item["title"] for item in response_ready.json()]
    assert titles_ready == ["Ready Video B", "Ready Video A"]

    response_all = client.get(video_list_url(), {"ready_only": "0"})
    assert response_all.status_code == 200
    titles_all = [item["title"] for item in response_all.json()]
    assert set(titles_all) == {
        "Ready Video A",
        "Ready Video B",
        "Stub Video",
        "Missing Manifest",
    }


def test_video_list_supports_ordering_param(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path
    viewer = create_user("ordering-viewer")

    alpha = create_video(viewer, title="Alpha", height=1080, is_published=True)
    beta = create_video(viewer, title="Beta", height=720, is_published=True)
    gamma = create_video(viewer, title="Gamma", height=480, is_published=True)

    now = timezone.now()
    Video.objects.filter(pk=alpha.pk).update(created_at=now - timedelta(minutes=3))
    Video.objects.filter(pk=beta.pk).update(created_at=now - timedelta(minutes=1))
    Video.objects.filter(pk=gamma.pk).update(created_at=now - timedelta(minutes=2))

    client = authenticated_client(viewer)

    response_title = client.get(video_list_url(), {"ready_only": "0", "order": "title"})
    assert response_title.status_code == 200
    assert response_title.json()[0]["title"] == "Alpha"

    response_height = client.get(
        video_list_url(), {"ready_only": "0", "order": "height"}
    )
    assert response_height.status_code == 200
    assert response_height.json()[0]["title"] == "Gamma"

    response_updated_desc = client.get(
        video_list_url(),
        {"ready_only": "0", "order": "-updated_at"},
    )
    assert response_updated_desc.status_code == 200
    assert response_updated_desc.json()[0]["title"] == "Beta"
