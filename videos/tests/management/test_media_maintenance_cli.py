from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from videos.domain.models import Video, VideoStream

pytestmark = pytest.mark.django_db


def create_video(**overrides) -> Video:
    defaults = {
        "title": "Media",
        "description": "Maintenance",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": "drama",
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(**defaults)


def write_manifest(
    base: Path, video_id: int, resolution: str, content: str, segments: int = 0
) -> Path:
    path = base / "hls" / str(video_id) / resolution / "index.m3u8"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    for idx in range(segments):
        (path.parent / f"segment-{idx}.ts").write_bytes(b"x")
    return path


def call_media_command(*args: str) -> dict:
    buffer = io.StringIO()
    call_command("media_maintenance", *args, stdout=buffer)
    raw = buffer.getvalue().strip()
    return json.loads(raw) if raw else {}


def test_media_maintenance_requires_action():
    with pytest.raises(CommandError):
        call_command("media_maintenance")


def test_media_maintenance_scan_json_reports_statuses(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    video = create_video()

    write_manifest(
        tmp_path, video.id, "480p", "#EXTM3U\n#EXTINF:5,\nsegment.ts\n", segments=1
    )
    write_manifest(tmp_path, video.id, "720p", "#EXTM3U\n")

    payload = call_media_command("--scan", "--json", "--real", str(video.pk))
    scan = payload["scan"]

    assert scan["summary"]["OK"] == 1
    assert scan["summary"]["STUB"] == 1
    record = scan["videos"][0]
    statuses = {entry["resolution"]: entry["status"] for entry in record["resolutions"]}
    assert statuses["480p"] == "OK"
    assert statuses["720p"] == "STUB"
    assert statuses["1080p"] == "MISSING"


def test_media_maintenance_enqueue_missing_json(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    video = create_video()

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.video_services.ensure_source_metadata",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.video_services.extract_video_metadata",
        lambda *_: {"height": 1080},
    )
    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.select_rungs_from_source",
        lambda *_: ["480p", "720p"],
    )

    queued_calls: list[tuple[int, list[str]]] = []

    def fake_enqueue(video_id: int, *, target_resolutions, force=False):
        queued_calls.append((video_id, list(target_resolutions)))
        return {"queued": True}

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.transcode_services.enqueue_transcode",
        fake_enqueue,
    )

    payload = call_media_command("--enqueue-missing", "--json", "--real", str(video.pk))
    queued = payload["enqueue_missing"]["queued"]
    assert queued[0]["video_id"] == video.pk
    assert queued[0]["resolutions"] == ["480p", "720p"]
    assert queued_calls == [(video.pk, ["480p", "720p"])]


def test_media_maintenance_prune_orphans_flow(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    video = create_video()

    existing_dir = tmp_path / "hls" / str(video.pk)
    existing_dir.mkdir(parents=True, exist_ok=True)

    orphan_dir = tmp_path / "hls" / "9999"
    orphan_dir.mkdir(parents=True, exist_ok=True)

    dry_run = call_media_command("--prune-orphans", "--json")
    assert dry_run["prune_orphans"]["pending"] == [9999]
    assert orphan_dir.exists()

    confirmed = call_media_command("--prune-orphans", "--confirm", "--json")
    assert confirmed["prune_orphans"]["deleted"] == [9999]
    assert not orphan_dir.exists()
    assert existing_dir.exists()


def test_media_maintenance_heal_json(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    video = create_video()

    VideoStream.objects.create(
        video=video,
        resolution="480p",
        manifest="#EXTM3U\n#EXTINF:5,\nsegment.ts\n",
    )

    manifest = write_manifest(tmp_path, video.pk, "480p", "#EXTM3U\n")

    recorded_thumbs: list[int] = []

    def fake_thumb(video_id: int):
        recorded_thumbs.append(video_id)
        thumb_path = tmp_path / "thumbs" / str(video_id) / "default.jpg"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(b"x")
        return thumb_path

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.thumb_utils.ensure_thumbnail",
        fake_thumb,
    )

    payload = call_media_command("--heal", "--json", "--real", str(video.pk))
    heal = payload["heal"]
    assert heal["fixed"][0]["video_id"] == video.pk
    assert heal["fixed"][0]["resolutions"] == ["480p"]
    assert heal["thumbnails"] == [video.pk]
    assert recorded_thumbs == [video.pk]
    assert "#EXTINF" in manifest.read_text()


def test_media_maintenance_scan_handles_stub_exceptions(
    monkeypatch, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    video = create_video()
    manifest = write_manifest(tmp_path, video.pk, "480p", "#EXTM3U\n")

    def raise_for_stub(path: Path):
        if path == manifest:
            raise ValueError("boom")
        return False

    monkeypatch.setattr(
        "videos.management.commands.media_maintenance.is_stub_manifest",
        raise_for_stub,
    )

    payload = call_media_command("--scan", "--json", "--real", str(video.pk))
    status = payload["scan"]["videos"][0]["resolutions"][0]["status"]
    assert status == "STUB"
