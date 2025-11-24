from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from videos.domain.models import Video


pytestmark = pytest.mark.django_db


def _create_user(prefix: str) -> object:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"{prefix}@example.com",
        username=f"{prefix}@example.com",
        password="pass",
    )


def _create_video(owner, **overrides) -> Video:
    defaults = {
        "title": "Video",
        "description": "",
        "thumbnail_url": "",
        "category": "drama",
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(owner=owner, **defaults)


def _auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    client.user = user
    return client


def test_video_list_orders_by_height_desc(monkeypatch, tmp_path, settings):
    settings.MEDIA_ROOT = (tmp_path / "media").as_posix()
    viewer = _create_user("viewer")
    other = _create_user("other")

    mine = _create_video(viewer, title="My Draft", is_published=False, height=1080)
    published = _create_video(
        other, title="Public Entry", is_published=True, height=720
    )
    _create_video(other, title="Hidden Draft", is_published=False, height=960)

    client = _auth_client(viewer)
    url = reverse("video-list")

    response = client.get(url, {"ready_only": "0", "order": "-height"})
    assert response.status_code == 200

    titles = [item["title"] for item in response.json()]
    assert titles[0] == mine.title
    assert "Hidden Draft" not in titles


def test_video_list_orders_by_title(monkeypatch, tmp_path, settings):
    settings.MEDIA_ROOT = (tmp_path / "media").as_posix()
    viewer = _create_user("viewer-title")

    _create_video(viewer, title="Zeta Clip")
    _create_video(viewer, title="Alpha Clip")
    _create_video(viewer, title="Beta Clip")

    client = _auth_client(viewer)
    url = reverse("video-list")

    response = client.get(url, {"ready_only": "0", "order": "title"})
    assert response.status_code == 200

    titles = [item["title"] for item in response.json()]
    assert titles[0] == "Alpha Clip"
