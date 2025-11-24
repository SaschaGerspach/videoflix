from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command

from videos.domain.models import Video, VideoStream

pytestmark = pytest.mark.django_db


def _write_rendition(
    media_root: Path, real_id: int, resolution: str, segments: list[str]
) -> None:
    target = media_root / "hls" / str(real_id) / resolution
    target.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"]
    for segment in segments:
        lines.append("#EXTINF:10,")
        lines.append(segment)
    manifest = "\n".join(lines) + "\n"
    (target / "index.m3u8").write_text(manifest, encoding="utf-8")
    for idx, segment in enumerate(segments):
        payload = f"segment-{idx}".encode()
        (target / segment).write_bytes(payload)


def test_index_renditions_command_with_real_ids(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    media_root = Path(settings.MEDIA_ROOT)

    video = Video.objects.create(
        id=321,
        title="CLI Real",
        description="",
        thumbnail_url="http://example.com/real.jpg",
        category="drama",
        is_published=True,
    )
    _write_rendition(media_root, video.pk, "720p", ["000.ts", "001.ts"])

    stdout = io.StringIO()
    call_command(
        "index_renditions", "--real", str(video.pk), "--res", "720p", stdout=stdout
    )

    output = stdout.getvalue()
    assert f"updated {video.pk}/720p" in output
    stream = VideoStream.objects.get(video=video, resolution="720p")
    assert stream.segments.count() == 2


def test_index_renditions_command_with_public_ids(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    media_root = Path(settings.MEDIA_ROOT)

    video = Video.objects.create(
        title="CLI Public",
        description="",
        thumbnail_url="http://example.com/public.jpg",
        category="drama",
        is_published=True,
    )
    _write_rendition(media_root, video.pk, "480p", ["000.ts", "001.ts"])

    stdout = io.StringIO()
    call_command("index_renditions", "--public", "1", "--res", "480p", stdout=stdout)

    output = stdout.getvalue()
    assert f"updated {video.pk}/480p" in output
    stream = VideoStream.objects.get(video=video, resolution="480p")
    assert stream.segments.count() == 2


def test_index_renditions_command_scan_all(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    media_root = Path(settings.MEDIA_ROOT)

    video_ok = Video.objects.create(
        title="CLI All OK",
        description="",
        thumbnail_url="http://example.com/allok.jpg",
        category="drama",
        is_published=True,
    )
    video_other = Video.objects.create(
        title="CLI All Other",
        description="",
        thumbnail_url="http://example.com/allother.jpg",
        category="drama",
        is_published=True,
    )

    _write_rendition(media_root, video_ok.pk, "720p", ["000.ts"])
    _write_rendition(media_root, video_other.pk, "480p", ["000.ts", "001.ts"])

    missing_dir = media_root / "hls" / "999" / "720p"
    missing_dir.mkdir(parents=True, exist_ok=True)

    stdout = io.StringIO()
    call_command("index_renditions", "--all", stdout=stdout)

    output = stdout.getvalue()
    assert f"updated {video_ok.pk}/720p" in output
    assert f"updated {video_other.pk}/480p" in output
    assert "missing 999/720p" in output
    assert "summary ok=" in output

    stream_ok = VideoStream.objects.get(video=video_ok, resolution="720p")
    stream_other = VideoStream.objects.get(video=video_other, resolution="480p")
    assert stream_ok.segments.count() == 1
    assert stream_other.segments.count() == 2
