from __future__ import annotations

from pathlib import Path

import pytest
from videos.domain import services_index as index
from videos.domain.models import Video, VideoSegment, VideoStream
from videos.domain.choices import VideoCategory
from videos.domain.utils import find_manifest_path


pytestmark = pytest.mark.django_db


def _make_video():
    return Video.objects.create(
        title="Indexed Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )


def _write_manifest(settings, video_id: int, resolution: str, segments: dict[str, bytes]) -> None:
    manifest_path = find_manifest_path(video_id, resolution)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"]
    for name in segments:
        lines.append("#EXTINF:10,")
        lines.append(name)
        (manifest_path.parent / name).write_bytes(segments[name])
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fs_rendition_exists_handles_missing_media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    exists, manifest, segments = index.fs_rendition_exists(1, "720p")
    assert exists is False
    assert manifest == find_manifest_path(1, "720p")
    assert segments == []


def test_index_existing_rendition_creates_stream_and_segments(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()
    _write_manifest(settings, video.pk, "720p", {"000.ts": b"a", "001.ts": b"b"})

    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: True)

    outcome = index.index_existing_rendition(video.pk, "720p")
    stream = VideoStream.objects.get(video=video, resolution="720p")
    assert outcome["created"] is True
    assert outcome["segments"] == 2
    assert outcome["bytes"] == 2
    assert VideoSegment.objects.filter(stream=stream).count() == 2


def test_index_existing_rendition_skips_stub(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()
    manifest_path = find_manifest_path(video.pk, "480p")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n", encoding="utf-8")
    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: True)

    outcome = index.index_existing_rendition(video.pk, "480p")
    assert outcome == {"created": False, "updated": False, "segments": 0, "bytes": 0}
    assert VideoStream.objects.filter(video=video, resolution="480p").exists() is False


def test_index_existing_rendition_deduplicates_existing_segments(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()
    _write_manifest(settings, video.pk, "1080p", {"000.ts": b"first"})

    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: True)
    index.index_existing_rendition(video.pk, "1080p")

    # Modify manifest + segment and re-run to trigger update in place.
    _write_manifest(settings, video.pk, "1080p", {"000.ts": b"second"})
    outcome = index.index_existing_rendition(video.pk, "1080p")

    assert outcome["created"] is False
    assert outcome["updated"] is True
    stream = VideoStream.objects.get(video=video, resolution="1080p")
    segment = VideoSegment.objects.get(stream=stream, name="000.ts")
    assert bytes(segment.content) == b"second"


def test_index_existing_rendition_skips_when_cache_blocks(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()
    _write_manifest(settings, video.pk, "360p", {"000.ts": b"x"})

    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: False)
    outcome = index.index_existing_rendition(video.pk, "360p")
    assert outcome["segments"] == 1
    assert outcome["created"] is False
    assert outcome["updated"] is False


def test_fs_rendition_exists_invalid_resolution(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    exists, manifest, segments = index.fs_rendition_exists(1, "../../720p")
    assert exists is False
    assert isinstance(manifest, Path)
    assert segments == []


def test_fs_rendition_exists_handles_oserror(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    manifest_path = find_manifest_path(2, "720p")

    original_exists = Path.exists

    def fake_exists(self):
        if self == manifest_path:
            raise OSError("boom")
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    exists, _, _ = index.fs_rendition_exists(2, "720p")
    assert exists is False


def test_index_existing_rendition_skips_missing_video(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    manifest_path = find_manifest_path(99, "720p")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")
    (manifest_path.parent / "000.ts").write_bytes(b"segment")

    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: True)
    outcome = index.index_existing_rendition(99, "720p")
    assert outcome == {"created": False, "updated": False, "segments": 0, "bytes": 0}


def test_index_existing_rendition_returns_default_when_media_empty(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()

    outcome = index.index_existing_rendition(video.pk, "720p")
    assert outcome == {"created": False, "updated": False, "segments": 0, "bytes": 0}
    assert VideoStream.objects.filter(video=video, resolution="720p").exists() is False


def test_index_existing_rendition_ignores_duplicate_directories(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()
    manifest_path = find_manifest_path(video.pk, "480p")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")
    (manifest_path.parent / "000.ts").write_bytes(b"a")

    duplicate = manifest_path.parent.parent / "480p-copy"
    duplicate.mkdir(parents=True, exist_ok=True)
    (duplicate / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: True)
    outcome = index.index_existing_rendition(video.pk, "480p")
    assert outcome["segments"] == 1
    assert VideoStream.objects.filter(video=video, resolution="480p").count() == 1


def test_index_existing_rendition_repeated_run_is_stable(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _make_video()
    _write_manifest(settings, video.pk, "360p", {"000.ts": b"stable"})

    monkeypatch.setattr(index, "_should_run", lambda *args, **kwargs: True)
    first = index.index_existing_rendition(video.pk, "360p")
    assert first["created"] is True

    second = index.index_existing_rendition(video.pk, "360p")
    assert second["updated"] is False
    stream = VideoStream.objects.get(video=video, resolution="360p")
    assert stream.manifest.count("#EXTINF") == 1
