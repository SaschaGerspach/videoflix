from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


def create_video(**overrides):
    defaults = {
        "title": "Video",
        "description": "",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": VideoCategory.DRAMA,
        "is_published": True,
    }
    defaults.update(overrides)
    return Video.objects.create(**defaults)


def write_manifest(video_id: int, resolution: str, content: str):
    manifest_path = (
        Path(settings.MEDIA_ROOT) / "hls" / str(video_id) / resolution / "index.m3u8"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(content, encoding="utf-8")
    return manifest_path


class TestMediaMaintenanceCommand:
    def test_scan_json_reports_status(self):
        video = create_video(title="Scan Video")
        ok_path = write_manifest(video.pk, "480p", "#EXTM3U\n#EXTINF:10,\n000.ts\n")
        (ok_path.parent / "000.ts").write_bytes(b"segment")
        write_manifest(video.pk, "720p", "#EXTM3U\n")

        out = io.StringIO()
        call_command(
            "media_maintenance",
            "--real",
            str(video.pk),
            "--scan",
            "--json",
            stdout=out,
        )
        payload = json.loads(out.getvalue())
        assert payload["scan"]["summary"]["OK"] >= 1
        assert payload["scan"]["summary"]["STUB"] >= 1

    def test_heal_rewrites_stub_manifests_and_thumbs(self, monkeypatch):
        video = create_video(title="Heal Video")
        stream = VideoStream.objects.create(
            video=video,
            resolution="480p",
            manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n",
        )
        write_manifest(video.pk, "480p", "#EXTM3U\n")
        source_path = Path(settings.MEDIA_ROOT) / "sources" / f"{video.pk}.mp4"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"source")

        def fake_thumbnail(video_id: int):
            thumb_path = (
                Path(settings.MEDIA_ROOT) / "thumbs" / str(video_id) / "default.jpg"
            )
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            thumb_path.write_bytes(b"thumb")
            return thumb_path

        monkeypatch.setattr(
            "videos.management.commands.media_maintenance.thumb_utils.ensure_thumbnail",
            fake_thumbnail,
        )

        call_command(
            "media_maintenance",
            "--real",
            str(video.pk),
            "--res",
            "480p",
            "--heal",
            "--json",
        )

        manifest_path = (
            Path(settings.MEDIA_ROOT) / "hls" / str(video.pk) / "480p" / "index.m3u8"
        )
        assert manifest_path.read_text(encoding="utf-8") == stream.manifest
        thumb_path = (
            Path(settings.MEDIA_ROOT) / "thumbs" / str(video.pk) / "default.jpg"
        )
        assert thumb_path.exists()

    def test_enqueue_missing_uses_dynamic_rungs(self, monkeypatch):
        video = create_video(title="Enqueue Video")
        source_path = Path(settings.MEDIA_ROOT) / "sources" / f"{video.pk}.mp4"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"source")

        def fake_ensure(video_obj):
            Video.objects.filter(pk=video_obj.pk).update(
                height=1300,
                video_bitrate_kbps=9000,
            )
            video_obj.refresh_from_db()
            video_obj._source_metadata_cache = {
                "height": 1300,
                "video_bitrate_kbps": 9000,
            }
            return video_obj

        enqueued: list[list[str]] = []

        def fake_enqueue(video_id: int, target_resolutions=None, force=False):
            enqueued.append(list(target_resolutions or []))
            return {"accepted": True}

        monkeypatch.setattr(
            "videos.domain.services.ensure_source_metadata", fake_ensure
        )
        monkeypatch.setattr("jobs.domain.services.enqueue_transcode", fake_enqueue)

        call_command(
            "media_maintenance",
            "--real",
            str(video.pk),
            "--enqueue-missing",
        )

        assert enqueued[-1] == ["1080p", "720p", "480p"]

    def test_prune_orphans_requires_confirm(self):
        base = Path(settings.MEDIA_ROOT) / "hls"
        orphan_dir = base / "999"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

        out = io.StringIO()
        call_command("media_maintenance", "--prune-orphans", "--json", stdout=out)
        assert orphan_dir.exists()

        call_command("media_maintenance", "--prune-orphans", "--confirm")
        assert not orphan_dir.exists()
