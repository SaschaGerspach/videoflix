from __future__ import annotations

from pathlib import Path

import pytest

from videos.domain import services_ops as ops
from videos.domain.choices import VideoCategory
from videos.domain.models import Video


pytestmark = pytest.mark.django_db


def test_heal_handles_missing_manifest(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    Video.objects.create(
        title="Edge Video",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    result = ops.run_heal_hls_index(
        publics=[1],
        resolutions=["720p"],
        write=False,
        rebuild_master=False,
        settings=settings,
        media_root=Path(settings.MEDIA_ROOT),
    )
    assert "videos" in result
    assert result["videos"]
    entry = result["videos"][0]
    assert "details" in entry
    assert "720p" in entry["details"]


def test_diagnose_handles_empty_media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    report = ops.run_diagnose_backend(
        settings=settings,
        media_root=Path(settings.MEDIA_ROOT),
        explicit_public=[],
    )
    for key in ("settings", "videos", "fs_checks", "routing", "summary"):
        assert key in report


def test_normalise_resolutions_deduplicates_and_fallback(settings):
    settings.CANONICAL_RENDITIONS = ("720p", "1080p")
    result = ops._normalise_resolutions(settings, [" 720P ", "720p", "1080p", None])
    assert result == ["720p", "1080p"]
    result_empty = ops._normalise_resolutions(settings, [])
    assert result_empty == ["720p", "1080p"]


def test_collect_settings_summary_uses_allowed_when_no_canonical(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CANONICAL_RENDITIONS", tuple(), raising=False)
    summary = ops._collect_settings_summary(settings, tmp_path)
    assert summary["allowed_renditions"]
    assert summary["canonical_renditions"] == summary["allowed_renditions"]
    assert summary["warnings"] == []


def test_collect_videos_returns_warning_when_no_ids(monkeypatch, settings):
    def fake_selector(*args, **kwargs):
        return []

    monkeypatch.setattr(ops.selectors_public, "list_for_user_with_public_ids", fake_selector)
    result = ops._collect_videos(settings, explicit_public=None, resolution_hint="720p")
    assert result["warnings"]
    assert result["items"] == []


def test_segment_name_candidates_zero_padding():
    candidates = ops._segment_name_candidates("hls/7/1/5.ts")
    assert "hls/7/1/005.ts" in candidates


def test_inspect_filesystem_marks_missing_manifest(monkeypatch, tmp_path, settings):
    manifest_path = tmp_path / "missing.m3u8"

    monkeypatch.setattr(ops, "find_manifest_path", lambda real_id, res: manifest_path)
    monkeypatch.setattr(ops, "is_stub_manifest", lambda path: False)

    resolved = [(1, 10, object())]
    result = ops._inspect_filesystem(settings, tmp_path, resolved, ["720p"])
    entry = result["entries"][0]
    assert entry["failure"] is True
    assert result["failures"] == 1


def test_inspect_filesystem_reads_segments(monkeypatch, tmp_path, settings):
    manifest_path = tmp_path / "10" / "720p" / "index.m3u8"
    manifest_path.parent.mkdir(parents=True)
    (manifest_path.parent / "000.ts").write_bytes(b"abc")
    manifest_path.write_text("#EXTM3U\n#EXTINF:10,\n000.ts\n", encoding="utf-8")

    monkeypatch.setattr(ops, "find_manifest_path", lambda real_id, res: manifest_path)
    monkeypatch.setattr(ops, "is_stub_manifest", lambda path: False)

    resolved = [(1, 10, object())]
    result = ops._inspect_filesystem(settings, tmp_path, resolved, ["720p"])
    entry = result["entries"][0]
    assert entry["exists"] is True
    assert entry["segment_on_disk"]
    assert entry["failure"] is False


def test_evaluate_headers_notes_missing_headers(settings):
    class DummyResponse(dict):
        def __getitem__(self, key):
            return self.get(key)

    response = DummyResponse()
    record, warnings = ops._evaluate_headers(
        response,
        kind="manifest",
        expected_tokens=("mpegurl",),
        status_code=200,
    )
    assert record["ok"] is False
    assert warnings


def test_maybe_check_cors_options_skips_when_module_missing(monkeypatch):
    sample = ops._ViewSample(
        public_id=1,
        real_id=1,
        resolution="720p",
        manifest_path="",
        segment_name="000.ts",
    )

    monkeypatch.setattr(ops.importlib.util, "find_spec", lambda name: None)
    info, warnings = ops._maybe_check_cors_options(sample, ops.APIRequestFactory())
    assert info is None
    assert warnings == []
