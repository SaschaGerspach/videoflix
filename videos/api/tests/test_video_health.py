from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from videos.domain.models import Video

pytestmark = pytest.mark.django_db


def _create_video(**overrides) -> Video:
    defaults = {
        "title": "Sample",
        "description": "Sample description",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": "drama",
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(**defaults)


def _public_id_for(video: Video) -> int:
    ordered_ids = list(
        Video.objects.filter(is_published=True)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)
    )
    return ordered_ids.index(video.id) + 1


def test_video_health_reports_segment_stats(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    client = APIClient()

    older = _create_video(title="Older")
    newer = _create_video(title="Newer")

    target_video = newer
    public_id = _public_id_for(target_video)

    base_dir = Path(settings.MEDIA_ROOT) / "hls" / str(target_video.id)
    rendition_specs = {
        "480p": [11, 17, 13],
        "720p": [21, 25],
    }
    for resolution, sizes in rendition_specs.items():
        rendition_dir = base_dir / resolution
        rendition_dir.mkdir(parents=True, exist_ok=True)
        manifest_text = (
            "#EXTM3U\n"
            + "\n".join(f"#EXTINF:10,\n{idx:03d}.ts" for idx in range(len(sizes)))
            + "\n"
        )
        (rendition_dir / "index.m3u8").write_text(manifest_text, encoding="utf-8")
        for idx, size in enumerate(sizes):
            (rendition_dir / f"{idx:03d}.ts").write_bytes(b"x" * size)

    response = client.get(reverse("video-health", kwargs={"public_id": public_id}))

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-cache"
    payload = response.json()
    assert payload["public"] == public_id
    assert payload["real"] == target_video.id
    assert set(payload["renditions"].keys()) == {"480p", "720p"}
    assert payload["renditions"]["480p"] == {
        "segments": 3,
        "bytes_min": 11,
        "bytes_max": 17,
    }
    assert payload["renditions"]["720p"] == {
        "segments": 2,
        "bytes_min": 21,
        "bytes_max": 25,
    }


def test_video_health_returns_404_when_missing(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    client = APIClient()

    video = _create_video(title="Lonely")
    public_id = _public_id_for(video)

    response = client.get(reverse("video-health", kwargs={"public_id": public_id}))

    assert response.status_code == 404
