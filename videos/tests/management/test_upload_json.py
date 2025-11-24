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


def test_upload_video_json_success(monkeypatch, tmp_path):
    source_file = tmp_path / "demo.mp4"
    source_file.write_bytes(b"video")

    def fake_ensure(video_obj):
        Video.objects.filter(pk=video_obj.pk).update(
            height=1280,
            video_bitrate_kbps=7000,
        )
        video_obj.refresh_from_db()
        video_obj._source_metadata_cache = {"height": 1280, "video_bitrate_kbps": 7000}
        return video_obj

    def fake_enqueue(video_id: int, target_resolutions=None, force=False):
        return ["1080p", "720p", "480p"], {"accepted": True}

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
        "JSON Upload",
        "--publish",
        "--json",
        stdout=out,
    )
    payload = json.loads(out.getvalue())
    assert payload["ok"] is True
    assert payload["rungs_enqueued"] == ["1080p", "720p", "480p"]
    assert payload["published"] is True


def test_upload_video_json_error(tmp_path):
    source_file = tmp_path / "missing.mp4"
    out = io.StringIO()
    with pytest.raises(SystemExit) as excinfo:
        call_command(
            "upload_video",
            str(source_file),
            "--json",
            stdout=out,
        )
    assert excinfo.value.code == 1
    payload = json.loads(out.getvalue())
    assert payload["ok"] is False
    assert "Source file not found" in payload["error"]
