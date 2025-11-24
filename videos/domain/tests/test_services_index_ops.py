from __future__ import annotations

import pytest

from videos.domain.models import Video, VideoStream
from videos.domain.services_ops import run_diagnose_backend, run_heal_hls_index
from videos.domain.choices import VideoCategory


@pytest.mark.django_db
def test_run_diagnose_backend_structure(settings, tmp_path):
    media_root = tmp_path / "media"
    settings.MEDIA_ROOT = str(media_root)
    settings.CANONICAL_RENDITIONS = ("720p",)

    video = Video.objects.create(
        title="Diag Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n"
    )

    hls_dir = media_root / "hls" / str(video.pk) / "720p"
    hls_dir.mkdir(parents=True, exist_ok=True)
    (hls_dir / "index.m3u8").write_text(
        "#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8"
    )
    (hls_dir / "000.ts").write_bytes(b"segment-bytes")

    report = run_diagnose_backend(
        settings=settings,
        media_root=media_root,
        explicit_public=[video.pk],
        requested_res=["720p"],
    )

    for key in {
        "settings",
        "videos",
        "fs_checks",
        "routing",
        "views",
        "headers",
        "debug",
        "summary",
    }:
        assert key in report
    assert report["videos"]
    assert "failures" in report["summary"]


@pytest.mark.django_db
def test_run_heal_hls_index_dry_run(settings, tmp_path):
    media_root = tmp_path / "media"
    settings.MEDIA_ROOT = str(media_root)
    settings.CANONICAL_RENDITIONS = ("720p", "480p")

    video = Video.objects.create(
        title="Heal Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n"
    )

    for res in ("720p", "480p"):
        rendition_dir = media_root / "hls" / str(video.pk) / res
        rendition_dir.mkdir(parents=True, exist_ok=True)
        (rendition_dir / "index.m3u8").write_text(
            "#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8"
        )
        (rendition_dir / "000.ts").write_bytes(b"segment-bytes")

    result = run_heal_hls_index(
        settings=settings,
        media_root=media_root,
        publics=[1],
        resolutions=["720p", "480p"],
        write=False,
        rebuild_master=False,
    )

    assert "videos" in result
    assert result["videos"]
    actions = result["videos"][0]["actions"]
    assert any(
        "create_stream" in action or "update_stream" in action for action in actions
    )


@pytest.mark.django_db
def test_run_heal_hls_index_write_rebuild(settings, tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    settings.MEDIA_ROOT = str(media_root)
    settings.CANONICAL_RENDITIONS = ("720p",)

    video = Video.objects.create(
        title="Heal Write Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n"
    )

    rendition_dir = media_root / "hls" / str(video.pk) / "720p"
    rendition_dir.mkdir(parents=True, exist_ok=True)
    (rendition_dir / "index.m3u8").write_text(
        "#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8"
    )
    (rendition_dir / "000.ts").write_bytes(b"segment-bytes")

    def fake_thumbnail(video_id: int, *args, **kwargs):
        thumb = media_root / "thumbs" / str(video_id) / "default.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"thumb")
        return thumb

    def fake_master(video_id: int):
        base = media_root / "hls" / str(video_id)
        base.mkdir(parents=True, exist_ok=True)
        (base / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr("videos.domain.services_ops.ensure_thumbnail", fake_thumbnail)
    monkeypatch.setattr("videos.domain.services_ops.write_master_playlist", fake_master)

    result = run_heal_hls_index(
        settings=settings,
        media_root=media_root,
        publics=[1],
        resolutions=["720p"],
        write=True,
        rebuild_master=True,
    )

    master_path = media_root / "hls" / str(video.pk) / "index.m3u8"
    assert master_path.exists()
    actions = result["videos"][0]["actions"]
    assert any("rebuild_master" in action for action in actions)
