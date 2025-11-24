from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from videos.domain.models import Video, VideoStream


pytestmark = pytest.mark.django_db


def test_media_maintenance_full_flow(tmp_path, settings, monkeypatch):
    media_root = tmp_path / "media"
    settings.MEDIA_ROOT = media_root.as_posix()

    video = Video.objects.create(
        title="Maintenance Video",
        description="",
        thumbnail_url="",
        category="drama",
        is_published=True,
    )

    VideoStream.objects.create(
        video=video,
        resolution="720p",
        manifest="#EXTM3U\n#EXTINF:10,\nsegment.ts\n",
    )

    stub_manifest = media_root / "hls" / str(video.pk) / "720p" / "index.m3u8"
    stub_manifest.parent.mkdir(parents=True, exist_ok=True)
    stub_manifest.write_text("#EXTM3U\n", encoding="utf-8")

    metadata = {"height": 1080, "video_bitrate_kbps": 7500}

    def fake_ensure(video_obj):
        video_obj._source_metadata_cache = metadata
        return video_obj

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.video_services.ensure_source_metadata",
        fake_ensure,
    )
    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.video_services.extract_video_metadata",
        lambda video_obj: metadata,
    )

    enqueue_calls: list[tuple[int, tuple[str, ...], bool]] = []

    def fake_enqueue(video_id: int, *, target_resolutions, force: bool = False):
        enqueue_calls.append((video_id, tuple(target_resolutions), force))
        return {"queue": "transcode"}

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.transcode_services.enqueue_transcode",
        fake_enqueue,
    )

    thumb_calls: list[int] = []

    def fake_thumb(video_id: int):
        thumb_path = media_root / "thumbs" / str(video_id) / "default.jpg"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(b"thumb")
        thumb_calls.append(video_id)
        return thumb_path

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.thumb_utils.ensure_thumbnail",
        fake_thumb,
    )

    output = StringIO()
    call_command(
        "media_maintenance",
        "--scan",
        "--heal",
        "--enqueue-missing",
        "--json",
        "--real",
        str(video.pk),
        stdout=output,
    )

    payload = json.loads(output.getvalue())

    assert payload["scan"]["summary"]["STUB"] >= 1
    assert payload["heal"]["fixed"][0]["video_id"] == video.pk
    assert "720p" in payload["heal"]["fixed"][0]["resolutions"]
    assert video.pk in payload["heal"]["thumbnails"]
    queued = payload["enqueue_missing"]["queued"][0]
    assert queued["video_id"] == video.pk
    assert set(queued["resolutions"]) == {"1080p", "480p"}

    assert enqueue_calls == [(video.pk, ("1080p", "480p"), False)]
    assert thumb_calls == [video.pk]

    healed_content = stub_manifest.read_text(encoding="utf-8")
    assert "segment.ts" in healed_content
