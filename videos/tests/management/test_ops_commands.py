from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream


def _prepare_video(
    settings,
    media_root: Path,
    resolutions: tuple[str, ...] = ("720p",),
    stream_resolutions: tuple[str, ...] = ("720p",),
) -> Video:
    settings.MEDIA_ROOT = str(media_root)
    settings.CANONICAL_RENDITIONS = resolutions

    video = Video.objects.create(
        title="Command Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    for res in resolutions:
        if res in stream_resolutions:
            VideoStream.objects.create(
                video=video,
                resolution=res,
                manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n",
            )
        rendition_dir = media_root / "hls" / str(video.pk) / res
        rendition_dir.mkdir(parents=True, exist_ok=True)
        (rendition_dir / "index.m3u8").write_text(
            "#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8"
        )
        (rendition_dir / "000.ts").write_bytes(b"segment-bytes")
    return video


@pytest.mark.django_db
def test_diagnose_backend_command_json(settings, tmp_path):
    media_root = tmp_path / "media"
    video = _prepare_video(settings, media_root)

    out = io.StringIO()
    call_command(
        "diagnose_backend",
        "--json",
        "--public",
        str(video.pk),
        "--res",
        "720p",
        stdout=out,
    )
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    idx = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
    payload = json.loads("\n".join(lines[idx:]))
    assert "scan" in payload
    assert payload["scan"]["videos"]


@pytest.mark.django_db
def test_heal_hls_index_command_dry_run(settings, tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    video = _prepare_video(
        settings,
        media_root,
        resolutions=("720p", "480p"),
        stream_resolutions=("720p",),
    )

    monkeypatch.setattr(
        "videos.domain.services_ops.ensure_thumbnail", lambda *args, **kwargs: None
    )

    out = io.StringIO()
    call_command(
        "heal_hls_index",
        "--json",
        "--public",
        str(video.pk),
        stdout=out,
    )
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    idx = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
    payload = json.loads("\n".join(lines[idx:]))
    assert "heal" in payload


@pytest.mark.django_db
def test_heal_hls_index_command_write_rebuild(settings, tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    video = _prepare_video(
        settings, media_root, ("720p",), stream_resolutions=("720p",)
    )

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

    out = io.StringIO()
    call_command(
        "heal_hls_index",
        "--json",
        "--public",
        str(video.pk),
        "--write",
        "--rebuild-master",
        stdout=out,
    )
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    idx = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
    payload = json.loads("\n".join(lines[idx:]))
    assert "heal" in payload


@pytest.mark.django_db
def test_heal_hls_index_command_supports_1080p(settings, tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    video = _prepare_video(
        settings,
        media_root,
        resolutions=("1080p",),
        stream_resolutions=(),
    )

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

    out = io.StringIO()
    call_command(
        "heal_hls_index",
        "--public",
        str(video.pk),
        "--res",
        "1080p",
        "--write",
        "--rebuild-master",
        stdout=out,
    )

    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    idx = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
    payload = json.loads("\n".join(lines[idx:]))
    assert "heal" in payload
