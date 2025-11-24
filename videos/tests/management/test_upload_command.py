from __future__ import annotations

import io
import json

import pytest
from django.core.management import call_command

from videos.domain.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


def test_upload_video_command_records_metadata(monkeypatch, media_root, tmp_path):
    source_file = tmp_path / "sample.mp4"
    source_file.write_bytes(b"video")

    def fake_ensure(video):
        Video.objects.filter(pk=video.pk).update(
            width=1920,
            height=1080,
            duration_seconds=123,
            video_bitrate_kbps=8000,
            audio_bitrate_kbps=192,
        )
        video.refresh_from_db()
        return video

    enqueued: list[list[str]] = []

    def fake_enqueue(video_id: int, *, force: bool = False):
        enqueued.append(["1080p", "720p", "480p"])
        return ["1080p", "720p", "480p"], {"queue": "transcode"}

    monkeypatch.setattr(
        "videos.management.commands.upload_video.video_services.ensure_source_metadata",
        fake_ensure,
    )
    monkeypatch.setattr(
        "videos.management.commands.upload_video.enqueue_dynamic_renditions",
        fake_enqueue,
    )

    out = io.StringIO()
    call_command(
        "upload_video",
        str(source_file),
        "--title",
        "Metadata Sample",
        "--publish",
        stdout=out,
    )

    video = Video.objects.get(title="Metadata Sample")
    assert video.width == 1920
    assert video.height == 1080
    assert video.duration_seconds == 123
    assert video.video_bitrate_kbps == 8000
    assert video.audio_bitrate_kbps == 192
    assert enqueued[-1] == ["1080p", "720p", "480p"]


def test_upload_video_command_json_output(monkeypatch, media_root, tmp_path):
    source_file = tmp_path / "sample-json.mp4"
    source_file.write_bytes(b"video-json")

    def fake_ensure(video_obj):
        Video.objects.filter(pk=video_obj.pk).update(
            height=720,
            video_bitrate_kbps=3000,
        )
        video_obj.refresh_from_db()
        video_obj._source_metadata_cache = {"height": 720, "video_bitrate_kbps": 3000}
        return video_obj

    def fake_enqueue(video_id: int, target_resolutions=None, force=False):
        return ["720p", "480p"], {"accepted": True}

    monkeypatch.setattr(
        "videos.management.commands.upload_video.video_services.ensure_source_metadata",
        fake_ensure,
    )
    monkeypatch.setattr(
        "videos.management.commands.upload_video.enqueue_dynamic_renditions",
        fake_enqueue,
    )

    out = io.StringIO()
    call_command(
        "upload_video",
        str(source_file),
        "--title",
        "JSON Sample",
        "--publish",
        "--json",
        stdout=out,
    )
    payload = json.loads(out.getvalue())
    assert payload["ok"] is True
    assert payload["video_id"]
    assert payload["rungs_enqueued"] == ["720p", "480p"]
    assert payload["published"] is True


def test_upload_video_command_json_error(tmp_path):
    fake_path = tmp_path / "missing.mp4"
    out = io.StringIO()
    with pytest.raises(SystemExit) as excinfo:
        call_command(
            "upload_video",
            str(fake_path),
            "--json",
            stdout=out,
        )
    assert excinfo.value.code == 1
    payload = json.loads(out.getvalue())
    assert payload["ok"] is False
    assert "Source file not found" in payload["error"]
