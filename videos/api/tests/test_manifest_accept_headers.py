from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from videos.domain.models import Video

pytestmark = pytest.mark.django_db


@override_settings()
def test_manifest_rejects_json_accept_header(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        email="owner@example.com",
        username="owner@example.com",
        password="pass123",
    )
    video = Video.objects.create(
        owner=owner,
        title="Sample",
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )

    manifest_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "480p"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "index.m3u8"
    manifest_path.write_text("#EXTM3U\n#EXTINF:1.0,\nsegment.ts\n", encoding="utf-8")
    (manifest_dir / "segment.ts").write_bytes(b"x")

    client = APIClient()
    client.force_authenticate(user=owner)
    url = reverse("video-segment", kwargs={"movie_id": 1, "resolution": "480p"})

    bad_resp = client.get(url, HTTP_ACCEPT="application/xml")
    assert bad_resp.status_code == 406

    good_resp = client.get(url, HTTP_ACCEPT="application/vnd.apple.mpegurl")
    assert good_resp.status_code == 200
    assert good_resp["Content-Type"].startswith("application/vnd.apple.mpegurl")
