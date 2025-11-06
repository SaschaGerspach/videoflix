from __future__ import annotations

from pathlib import Path

import pytest

from videos.domain import utils


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_is_stub_manifest_variants(media_root):
    stub_path = media_root / "stub.m3u8"
    stub_path.write_text("#EXTM3U\n", encoding="utf-8")
    real_path = media_root / "real.m3u8"
    real_path.write_text("#EXTM3U\n#EXTINF:5,\n000.ts\n", encoding="utf-8")

    assert utils.is_stub_manifest(stub_path) is True
    assert utils.is_stub_manifest(real_path) is False
    assert utils.is_stub_manifest(b"#EXTM3U\n") is True
    assert utils.is_stub_manifest("#EXTM3U\n#EXTINF:6,\n001.ts") is False


def test_find_manifest_path_and_ensure_dir(media_root):
    target_dir = utils.ensure_hls_dir(42, "720p")
    assert target_dir.exists()
    expected = media_root / "hls" / "42" / "720p"
    assert target_dir == expected

    manifest_path = utils.find_manifest_path(42, "720p")
    assert manifest_path == expected / "index.m3u8"

    manifest_path.write_text("#EXTM3U\n#EXTINF:4,\nseg.ts\n", encoding="utf-8")
    assert utils.has_hls_ready(42, "720p") is True

    empty_manifest = utils.find_manifest_path(43, "480p")
    empty_manifest.parent.mkdir(parents=True, exist_ok=True)
    empty_manifest.write_text("#EXTM3U\n", encoding="utf-8")
    assert utils.has_hls_ready(43, "480p") is False


def test_resolve_source_path_checks_fields(media_root):
    class Dummy:
        def __init__(self):
            self.pk = 77
            self.source_file = type('F', (), {'path': str(media_root / 'direct.mp4')})
            self.file = None

    dummy = Dummy()
    checked: list[Path] = []
    source_path = Path(dummy.source_file.path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b'source')

    result = utils.resolve_source_path(dummy, checked_paths=checked)
    assert result == source_path
    assert source_path in checked

    uploads_path = Path(media_root) / 'uploads' / 'videos' / '88.mp4'
    uploads_path.parent.mkdir(parents=True, exist_ok=True)
    uploads_path.write_bytes(b'alt')
    dummy2 = type('Obj', (), {'pk': 88})()
    fallback = utils.resolve_source_path(dummy2, checked_paths=checked)
    assert fallback == uploads_path
