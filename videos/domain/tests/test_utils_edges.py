from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from videos.domain import utils


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    return tmp_path


def test_is_stub_manifest_with_bytes_and_path(tmp_path):
    path = tmp_path / "stub.m3u8"
    path.write_text("#EXTM3U\n", encoding="utf-8")
    assert utils.is_stub_manifest(path) is True
    assert utils.is_stub_manifest(b"#EXTM3U\n") is True

    full = tmp_path / "full.m3u8"
    full.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")
    assert utils.is_stub_manifest(full) is False


def test_is_stub_manifest_handles_missing_file(tmp_path):
    missing = tmp_path / "missing.m3u8"
    assert utils.is_stub_manifest(missing) is False


def test_resolve_source_path_collects_candidates(tmp_path):
    uploads = Path(utils.settings.MEDIA_ROOT) / "uploads" / "videos"
    uploads.mkdir(parents=True, exist_ok=True)
    source_path = uploads / "5.mp4"
    source_path.write_bytes(b"video")

    checked: list[Path] = []
    video = SimpleNamespace(pk=5, source_file=None, file=None, video_file=None, video=None, source=None, path=None)

    resolved = utils.resolve_source_path(video, checked_paths=checked)
    assert resolved == source_path
    assert source_path in checked


def test_ensure_hls_dir_rejects_escape():
    with pytest.raises(ValueError):
        utils.ensure_hls_dir(1, "../../../720p")


def test_find_manifest_path_rejects_escape():
    with pytest.raises(ValueError):
        utils.find_manifest_path(1, "../../../720p")


def test_has_hls_ready_with_real_manifest():
    manifest = Path(utils.settings.MEDIA_ROOT) / "hls" / "3" / "720p" / "index.m3u8"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")
    assert utils.has_hls_ready(3, "720p") is True


def test_has_hls_ready_false_for_stub():
    manifest = Path(utils.settings.MEDIA_ROOT) / "hls" / "4" / "720p" / "index.m3u8"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("#EXTM3U\n", encoding="utf-8")
    assert utils.has_hls_ready(4, "720p") is False


def test_resolve_source_path_tracks_relative_candidates(tmp_path):
    video = SimpleNamespace(
        pk=7,
        source="./uploads/video7.mp4",
        source_file=None,
        file=None,
        video_file=None,
        video=None,
        path=None,
    )
    checked: list[Path] = []
    resolved = utils.resolve_source_path(video, checked_paths=checked)
    assert resolved is None
    assert checked
    assert checked[0].name == "video7.mp4"


def test_ensure_hls_dir_creates_directories(tmp_path, settings):
    target = utils.ensure_hls_dir(8, "920p")
    assert target.is_dir()
    assert target.parts[-3:] == ("hls", "8", "920p")


def test_is_stub_manifest_handles_inline_text():
    manifest_text = "#EXTM3U\n#EXTINF:10,\n000.ts\n"
    assert utils.is_stub_manifest(manifest_text) is False
