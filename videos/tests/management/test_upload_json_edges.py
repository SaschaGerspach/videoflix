from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command


pytestmark = pytest.mark.django_db


def test_upload_json_missing_source(tmp_path, settings):
    settings.MEDIA_ROOT = (tmp_path / "media").as_posix()
    output = StringIO()

    missing = tmp_path / "missing.mp4"
    with pytest.raises(SystemExit) as exc:
        call_command("upload_video", str(missing), "--json", stdout=output)

    assert exc.value.code == 1
    payload = json.loads(output.getvalue())
    assert payload["ok"] is False
    assert "Source file not found" in payload["error"]


def test_upload_json_owner_not_found(tmp_path, settings):
    media_root = tmp_path / "media"
    media_root.mkdir()
    settings.MEDIA_ROOT = media_root.as_posix()

    source_path = tmp_path / "sample.mp4"
    source_path.write_bytes(b"video-bytes")

    output = StringIO()
    with pytest.raises(SystemExit) as exc:
        call_command(
            "upload_video",
            str(source_path),
            "--owner",
            "missing@example.com",
            "--json",
            stdout=output,
        )

    assert exc.value.code == 1
    payload = json.loads(output.getvalue())
    assert payload["ok"] is False
    assert payload["error"] == "Owner not found"


def test_upload_json_happy_path(tmp_path, settings, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir()
    settings.MEDIA_ROOT = media_root.as_posix()

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video-bytes")

    user_model = get_user_model()
    owner = user_model.objects.create_user(
        email="owner@example.com",
        username="owner@example.com",
        password="pass",
    )

    def fake_ensure(video):
        video._source_metadata_cache = {"height": 1080, "video_bitrate_kbps": 8000}
        video.thumbnail_url = "http://example.com/thumb.jpg"
        return video

    queued_calls: list[tuple[int, tuple[str, ...], bool]] = []

    def fake_enqueue(video_id: int, *, force: bool = False):
        queued_calls.append((video_id, ("480p",), force))
        return ["480p"], {"queue": "transcode"}

    monkeypatch.setattr(
        "videos.management.commands.upload_video.video_services.ensure_source_metadata",
        fake_ensure,
    )
    monkeypatch.setattr(
        "videos.management.commands.upload_video.enqueue_dynamic_renditions",
        lambda video_id: fake_enqueue(video_id),
    )

    output = StringIO()
    call_command(
        "upload_video",
        str(source_path),
        "--owner",
        owner.email,
        "--title",
        "Sample Clip",
        "--json",
        stdout=output,
    )

    payload = json.loads(output.getvalue())
    assert payload["ok"] is True
    assert payload["video_id"] > 0
    assert payload["rungs_enqueued"] == ["480p"]
    assert payload["copied"] is True
    assert payload["moved"] is False
    assert payload["thumbnail_url"] == "http://example.com/thumb.jpg"

    assert queued_calls
    video_id = payload["video_id"]
    target_path = media_root / "sources" / f"{video_id}.mp4"
    assert target_path.exists()
    assert target_path.read_bytes() == source_path.read_bytes()
    assert source_path.exists()
