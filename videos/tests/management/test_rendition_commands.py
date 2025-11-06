from __future__ import annotations

import io
from pathlib import Path

import pytest
import django
from django.apps import apps
from django.core.management import call_command, get_commands
from django.test import override_settings

if not apps.ready:
    django.setup()
assert "enqueue_transcodes" in get_commands()
assert "auto_enqueue_missing" in get_commands()
assert "check_renditions" in get_commands()

from videos.domain.choices import VideoCategory
from videos.domain.models import Video


def write_manifest(media_root: Path, real_id: int, resolution: str, lines: list[str]) -> Path:
    rendition_dir = media_root / "hls" / str(real_id) / resolution
    rendition_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = rendition_dir / "index.m3u8"
    content = "\n".join(lines)
    if lines:
        content += "\n"
    manifest_path.write_text(content, encoding="utf-8")
    return manifest_path


def write_segment(media_root: Path, real_id: int, resolution: str, name: str = "000.ts", payload: bytes | None = None) -> Path:
    rendition_dir = media_root / "hls" / str(real_id) / resolution
    rendition_dir.mkdir(parents=True, exist_ok=True)
    segment_path = rendition_dir / name
    segment_path.write_bytes(payload or b"segment-bytes")
    return segment_path


def write_source_file(media_root: Path, real_id: int, payload: bytes | None = None) -> Path:
    sources_dir = media_root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    source_path = sources_dir / f"{real_id}.mp4"
    source_path.write_bytes(payload or b"source-bytes")
    return source_path


@pytest.mark.django_db
def test_check_renditions_reports_ok_and_missing(tmp_path, settings):
    media_root = tmp_path / "media"
    with override_settings(MEDIA_ROOT=str(media_root)):
        ok_video = Video.objects.create(
            title="OK video",
            description="",
            thumbnail_url="http://example.com/ok.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )
        missing_video = Video.objects.create(
            title="Missing video",
            description="",
            thumbnail_url="http://example.com/missing.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )
        write_manifest(media_root, ok_video.id, "480p", ["#EXTM3U", "#EXTINF:10,", "000.ts"])
        write_segment(media_root, ok_video.id, "480p")
        write_manifest(media_root, missing_video.id, "480p", [])

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "check_renditions",
            "--real",
            str(ok_video.id),
            str(missing_video.id),
            "--res",
            "480p",
            stdout=stdout,
            stderr=stderr,
        )

        output = stdout.getvalue()
        assert "OK" in output and str(ok_video.id) in output
        assert "MISSING" in output and str(missing_video.id) in output
        assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_check_renditions_reports_empty_without_segments(tmp_path, settings):
    media_root = tmp_path / "media"
    with override_settings(MEDIA_ROOT=str(media_root)):
        video = Video.objects.create(
            title="Empty segments video",
            description="",
            thumbnail_url="http://example.com/empty-segments.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )
        write_manifest(media_root, video.id, "480p", ["#EXTM3U", "#EXTINF:10,", "000.ts"])

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "check_renditions",
            "--real",
            str(video.id),
            "--res",
            "480p",
            stdout=stdout,
            stderr=stderr,
        )

        output = stdout.getvalue()
        assert "EMPTY" in output
        assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_enqueue_transcodes_invokes_enqueue(tmp_path, monkeypatch):
    calls: list[tuple[int, list[str] | None]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None):
        calls.append((video_id, target_resolutions))

    monkeypatch.setattr(
        "videos.management.commands.enqueue_transcodes.job_services.enqueue_transcode",
        fake_enqueue,
    )

    media_root = tmp_path / "media"
    with override_settings(MEDIA_ROOT=str(media_root)):
        Video.objects.create(
            pk=7,
            title="v7",
            description="",
            thumbnail_url="http://example.com/v7.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )
        Video.objects.create(
            pk=8,
            title="v8",
            description="",
            thumbnail_url="http://example.com/v8.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )
        write_source_file(media_root, 7)
        write_source_file(media_root, 8)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "enqueue_transcodes",
            "--real",
            "7",
            "8",
            "--res",
            "720p",
            stdout=stdout,
            stderr=stderr,
        )

    assert calls == [(7, ["720p"]), (8, ["720p"])]
    assert "Queued" in stdout.getvalue()
    assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_auto_enqueue_missing_dry_run_and_confirm(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    ok_video = Video.objects.create(
        title="OK video auto",
        description="",
        thumbnail_url="http://example.com/ok-auto.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    missing_video = Video.objects.create(
        title="Missing auto",
        description="",
        thumbnail_url="http://example.com/mis-auto.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    calls: list[tuple[int, list[str] | None]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None):
        calls.append((video_id, target_resolutions))

    monkeypatch.setattr(
        "videos.management.commands.auto_enqueue_missing.job_services.enqueue_transcode",
        fake_enqueue,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        write_manifest(media_root, ok_video.id, "1080p", ["#EXTM3U", "#EXTINF:5,", "000.ts"])
        write_segment(media_root, ok_video.id, "1080p")
        write_source_file(media_root, missing_video.id)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "auto_enqueue_missing",
            "--real",
            str(ok_video.id),
            str(missing_video.id),
            "--res",
            "1080p",
            "--dry-run",
            stdout=stdout,
            stderr=stderr,
        )
        assert calls == []
        dry_output = stdout.getvalue()
        assert "Missing" in dry_output and str(missing_video.id) in dry_output
        assert "Dry-run" in dry_output

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "auto_enqueue_missing",
            "--real",
            str(ok_video.id),
            str(missing_video.id),
            "--res",
            "1080p",
            "--confirm",
            stdout=stdout,
            stderr=stderr,
        )
        assert calls == [(missing_video.id, ["1080p"])]
        assert "Queued" in stdout.getvalue()
        assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_auto_enqueue_missing_with_public_mapping(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    missing_real_id = 42
    public_id = 5
    Video.objects.create(
        pk=missing_real_id,
        title="Public mapped video",
        description="",
        thumbnail_url="http://example.com/public-mapped.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    calls: list[tuple[int, list[str] | None]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None):
        calls.append((video_id, target_resolutions))

    monkeypatch.setattr(
        "videos.management.commands.auto_enqueue_missing.job_services.enqueue_transcode",
        fake_enqueue,
    )
    monkeypatch.setattr(
        "videos.management.commands.auto_enqueue_missing.resolve_public_id",
        lambda pid: missing_real_id if pid == public_id else pid,
        raising=True,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        write_source_file(media_root, missing_real_id)
        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "auto_enqueue_missing",
            "--public",
            str(public_id),
            "--res",
            "480p",
            "--confirm",
            stdout=stdout,
            stderr=stderr,
        )

        assert calls == [(missing_real_id, ["480p"])]
        output = stdout.getvalue()
        assert str(public_id) in output
        assert str(missing_real_id) in output
        assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_check_renditions_treats_empty_manifest_as_missing(tmp_path, settings):
    media_root = tmp_path / "media"
    with override_settings(MEDIA_ROOT=str(media_root)):
        video = Video.objects.create(
            title="Empty manifest video",
            description="",
            thumbnail_url="http://example.com/empty.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )
        write_manifest(media_root, video.id, "480p", ["#EXTM3U"])

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "check_renditions",
            "--real",
            str(video.id),
            "--res",
            "480p",
            stdout=stdout,
            stderr=stderr,
        )

        output = stdout.getvalue()
        assert "MISSING" in output and str(video.id) in output
        assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_auto_enqueue_missing_treats_stub_manifest_as_missing(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    video = Video.objects.create(
        title="Stub manifest video",
        description="",
        thumbnail_url="http://example.com/stub.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    calls: list[tuple[int, list[str] | None]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None):
        calls.append((video_id, target_resolutions))

    monkeypatch.setattr(
        "videos.management.commands.auto_enqueue_missing.job_services.enqueue_transcode",
        fake_enqueue,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        write_manifest(media_root, video.id, "480p", ["#EXTM3U"])
        write_source_file(media_root, video.id)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "auto_enqueue_missing",
            "--real",
            str(video.id),
            "--res",
            "480p",
            "--confirm",
            stdout=stdout,
            stderr=stderr,
        )

    assert calls == [(video.id, ["480p"])]
    assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_enqueue_transcodes_force_rebuilds(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    video = Video.objects.create(
        pk=55,
        title="Force rebuild video",
        description="",
        thumbnail_url="http://example.com/force.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    calls: list[tuple[int, list[str] | None]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None):
        calls.append((video_id, target_resolutions))

    monkeypatch.setattr(
        "videos.management.commands.enqueue_transcodes.job_services.enqueue_transcode",
        fake_enqueue,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        manifest_path = write_manifest(
            media_root,
            video.id,
            "480p",
            ["#EXTM3U", "#EXTINF:9,", "000.ts"],
        )
        write_segment(media_root, video.id, "480p")
        write_source_file(media_root, video.id)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "enqueue_transcodes",
            "--real",
            str(video.id),
            "--res",
            "480p",
            "--force",
            stdout=stdout,
            stderr=stderr,
        )

        assert not manifest_path.exists()

    assert calls == [(video.id, ["480p"])]
    assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_auto_enqueue_missing_uses_sources_directory(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    video = Video.objects.create(
        title="Sources dir video",
        description="",
        thumbnail_url="http://example.com/src.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    calls: list[tuple[int, list[str] | None]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None):
        calls.append((video_id, target_resolutions))

    monkeypatch.setattr(
        "videos.management.commands.auto_enqueue_missing.job_services.enqueue_transcode",
        fake_enqueue,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        write_source_file(media_root, video.id)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "auto_enqueue_missing",
            "--real",
            str(video.id),
            "--res",
            "720p",
            "--confirm",
            stdout=stdout,
            stderr=stderr,
        )

    assert calls == [(video.id, ["720p"])]
    assert stderr.getvalue() == ""


@pytest.mark.django_db
def test_seed_demo_renditions_force_rebuild(tmp_path):
    media_root = tmp_path / "media"
    video = Video.objects.create(
        title="Force seed video",
        description="",
        thumbnail_url="http://example.com/seed-force.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        target_dir = media_root / "hls" / str(video.id) / "480p"
        target_dir.mkdir(parents=True, exist_ok=True)
        old_manifest = target_dir / "index.m3u8"
        old_manifest.write_text("#EXTM3U\n", encoding="utf-8")
        extra_file = target_dir / "alt.bin"
        extra_file.write_bytes(b"legacy")

        write_source_file(media_root, video.id)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "seed_demo_renditions",
            "--real",
            str(video.id),
            "--res",
            "480p",
            stdout=stdout,
            stderr=stderr,
        )

        assert extra_file.exists()
        manifest_text = (target_dir / "index.m3u8").read_text(encoding="utf-8")
        assert manifest_text.count("#EXTINF") == 3

        stdout_force = io.StringIO()
        stderr_force = io.StringIO()
        call_command(
            "seed_demo_renditions",
            "--real",
            str(video.id),
            "--res",
            "480p",
            "--force",
            stdout=stdout_force,
            stderr=stderr_force,
        )

        assert "Purged" in stdout_force.getvalue()
        entries = sorted(p.name for p in target_dir.iterdir())
        assert entries == ["000.ts", "001.ts", "002.ts", "index.m3u8"]
        assert stderr_force.getvalue() == ""


@pytest.mark.django_db
def test_seed_demo_renditions_creates_assets(tmp_path):
    media_root = tmp_path / "media"
    video = Video.objects.create(
        title="Seed demo video",
        description="",
        thumbnail_url="http://example.com/seed.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    with override_settings(MEDIA_ROOT=str(media_root)):
        write_source_file(media_root, video.id)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            "seed_demo_renditions",
            "--real",
            str(video.id),
            "--res",
            "480p",
            stdout=stdout,
            stderr=stderr,
        )

        rendition_dir = media_root / "hls" / str(video.id) / "480p"
        manifest_path = rendition_dir / "index.m3u8"
        assert manifest_path.exists()
        text = manifest_path.read_text(encoding="utf-8")
        assert text.count("#EXTINF") == 3
        segments = list(rendition_dir.glob("*.ts"))
        assert len(segments) == 3
        assert "Seeded" in stdout.getvalue()
        assert stderr.getvalue() == ""
