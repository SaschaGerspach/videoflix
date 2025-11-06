from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings

from videos.domain import thumbs


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    settings.MEDIA_URL = "/media/"
    return tmp_path


def test_ensure_thumbnail_success(monkeypatch, tmp_path):
    video_id = 7
    source = tmp_path / "sources" / f"{video_id}.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00" * 10)

    def fake_source_path(_video_id: int):
        return source

    def fake_run(cmd, check, stdout, stderr):
        Path(cmd[-1]).write_bytes(b"thumb-bytes")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("videos.domain.thumbs.job_services.get_video_source_path", fake_source_path)
    monkeypatch.setattr("videos.domain.thumbs.subprocess.run", fake_run)

    output = thumbs.ensure_thumbnail(video_id)
    assert output is not None
    assert output.exists()
    assert output.read_bytes() == b"thumb-bytes"


def test_ensure_thumbnail_missing_source_returns_none(monkeypatch):
    video_id = 8

    def fake_source_path(_video_id: int):
        return Path(settings.MEDIA_ROOT) / "sources" / f"{_video_id}.mp4"

    monkeypatch.setattr("videos.domain.thumbs.job_services.get_video_source_path", fake_source_path)
    result = thumbs.ensure_thumbnail(video_id)
    assert result is None


def test_ensure_thumbnail_handles_process_error(monkeypatch, tmp_path):
    video_id = 9
    source = tmp_path / "sources" / f"{video_id}.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00")

    def fake_source_path(_video_id: int):
        return source

    def fake_run(*args, **kwargs):
        raise thumbs.subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"])

    monkeypatch.setattr("videos.domain.thumbs.job_services.get_video_source_path", fake_source_path)
    monkeypatch.setattr("videos.domain.thumbs.subprocess.run", fake_run)

    result = thumbs.ensure_thumbnail(video_id)
    assert result is None


def test_get_thumbnail_url_builds_absolute(monkeypatch, tmp_path):
    video_id = 11
    thumb_path = tmp_path / "thumbs" / str(video_id) / "default.jpg"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.write_bytes(b"img")

    request = SimpleNamespace(build_absolute_uri=lambda url: f"http://testserver{url}")
    url = thumbs.get_thumbnail_url(request, video_id)
    assert url == "http://testserver/media/thumbs/11/default.jpg"


def test_get_thumbnail_url_returns_empty_when_missing(tmp_path):
    video_id = 12
    url = thumbs.get_thumbnail_url(None, video_id)
    assert url == ""


def test_get_thumbnail_path_honors_size(settings, tmp_path):
    video_id = 13
    path = thumbs.get_thumbnail_path(video_id, size="micro")
    tail = path.parts[-3:]
    assert tail == ("thumbs", str(video_id), "micro.jpg")
    assert path.is_relative_to(Path(settings.MEDIA_ROOT))


def test_get_thumbnail_url_returns_absolute_without_request(settings, tmp_path):
    video_id = 14
    thumb_path = tmp_path / "thumbs" / str(video_id) / "default.jpg"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_path.write_bytes(b"thumb")
    settings.PUBLIC_MEDIA_BASE = "https://cdn.example.com"

    url = thumbs.get_thumbnail_url(None, video_id)
    assert url == "https://cdn.example.com/media/thumbs/14/default.jpg"


def test_ensure_thumbnail_handles_missing_ffmpeg_binary(monkeypatch, tmp_path):
    video_id = 15
    source = tmp_path / "sources" / f"{video_id}.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00" * 8)

    monkeypatch.setattr("videos.domain.thumbs.job_services.get_video_source_path", lambda *_: source)
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg missing")

    monkeypatch.setattr("videos.domain.thumbs.subprocess.run", fake_run)

    assert thumbs.ensure_thumbnail(video_id) is None
