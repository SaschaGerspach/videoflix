from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from videos.domain.models import Video, VideoStream

pytestmark = pytest.mark.django_db


def create_video(**overrides) -> Video:
    defaults = {
        "title": "Thumb Demo",
        "description": "Test",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": "drama",
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(**defaults)


def test_rebuild_thumbs_invalid_real_id_raises_command_error():
    with pytest.raises(CommandError, match="Video\\(s\\) not found"):
        call_command("rebuild_thumbs", "--real", "99999")


@override_settings()
def test_rebuild_thumbs_heals_stub_and_reports_details(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    video = create_video()
    VideoStream.objects.create(
        video=video,
        resolution="480p",
        manifest="#EXTM3U\n#EXTINF:1.0,\nsegment.ts\n",
    )

    manifest_dir = Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "480p"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "index.m3u8"
    manifest_path.write_text("#EXTM3U\n", encoding="utf-8")

    def fake_thumb(video_id: int):
        thumb = Path(settings.MEDIA_ROOT) / "thumbs" / str(video_id) / "default.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"x")
        return thumb

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.thumb_utils.ensure_thumbnail",
        fake_thumb,
    )

    stdout = io.StringIO()
    call_command("rebuild_thumbs", "--real", str(video.pk), stdout=stdout)

    output = stdout.getvalue()
    assert "Healed manifests" in output or "Generated thumbnails" in output
    assert "#EXTINF" in manifest_path.read_text(encoding="utf-8")
