from __future__ import annotations

import pytest

from videos.domain import services as video_services
from videos.domain.models import Video

pytestmark = pytest.mark.django_db


def create_video(**overrides) -> Video:
    defaults = {
        "title": "Meta",
        "description": "Data",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": "drama",
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(**defaults)


def test_ensure_source_metadata_persists_values(monkeypatch, tmp_path):
    video = create_video()

    source_path = tmp_path / "video.mp4"
    source_path.write_bytes(b"x")

    payload = {
        "width": 1920,
        "height": 1080,
        "duration_seconds": 120,
        "video_bitrate_kbps": 3500,
        "audio_bitrate_kbps": 192,
        "codec_name": "h264",
    }

    monkeypatch.setattr(
        "videos.domain.services.resolve_source_path",
        lambda *_: source_path,
    )
    monkeypatch.setattr(
        "videos.domain.services.probe_media_info",
        lambda *_: payload,
    )

    updated = video_services.ensure_source_metadata(video)
    updated.refresh_from_db()

    assert updated.height == 1080
    assert updated.video_bitrate_kbps == 3500
    assert updated._source_metadata_cache == payload


def test_extract_video_metadata_prefers_cached():
    video = create_video(height=480, width=640)
    cached = {"height": 720, "video_bitrate_kbps": 2500}
    video._source_metadata_cache = cached

    result = video_services.extract_video_metadata(video)
    assert result is cached


def test_extract_video_metadata_falls_back_to_model_fields():
    video = create_video(
        width=640,
        height=480,
        duration_seconds=42,
        video_bitrate_kbps=1500,
        audio_bitrate_kbps=128,
        codec_name="vp9",
        is_published=False,
    )

    result = video_services.extract_video_metadata(video)
    assert result["width"] == 640
    assert result["height"] == 480
    assert result["duration_seconds"] == 42
    assert result["video_bitrate_kbps"] == 1500
    assert result["audio_bitrate_kbps"] == 128
    assert result["codec_name"] == "vp9"


def test_ensure_source_metadata_handles_missing_path(monkeypatch):
    video = create_video()

    monkeypatch.setattr(
        "videos.domain.services.resolve_source_path",
        lambda *_: None,
    )

    result = video_services.ensure_source_metadata(video)
    assert result._source_metadata_cache == {}
