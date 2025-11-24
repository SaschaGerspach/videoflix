from __future__ import annotations

from pathlib import Path

import pytest
from rest_framework.response import Response
from videos.domain import services_ops as ops
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream


pytestmark = pytest.mark.django_db


def _create_video(**overrides):
    payload = {
        "title": "Ops Video",
        "description": "",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": VideoCategory.DRAMA,
        "is_published": True,
    }
    payload.update(overrides)
    return Video.objects.create(**payload)


def _write_manifest(
    root: Path,
    video_id: int,
    resolution: str,
    segments: list[str],
    *,
    write_segments: bool = True,
) -> Path:
    manifest = root / "hls" / str(video_id) / resolution / "index.m3u8"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    body = ["#EXTM3U"]
    for name in segments:
        body.append("#EXTINF:10,")
        body.append(name)
        if write_segments:
            segment_path = manifest.parent / name
            segment_path.parent.mkdir(parents=True, exist_ok=True)
            segment_path.write_bytes(b"segment-bytes")
    manifest.write_text("\n".join(body) + "\n", encoding="utf-8")
    return manifest


def test_segment_name_candidates_handles_backslashes():
    candidates = ops._segment_name_candidates(r"hls\7\1\5.ts")
    assert "hls/7/1/005.ts" in candidates
    assert candidates[0].startswith("hls/")


def test_run_heal_hls_index_missing_vs_present_segments(
    tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video_missing = _create_video()
    video_ready = _create_video()

    # Ready video gets manifest and single segment
    _write_manifest(Path(settings.MEDIA_ROOT), video_ready.id, "720p", ["000.ts"])

    monkeypatch.setattr(ops, "ensure_thumbnail", lambda real_id: tmp_path / "thumb.jpg")

    result = ops.run_heal_hls_index(
        settings=settings,
        media_root=Path(settings.MEDIA_ROOT),
        publics=[video_missing.id, video_ready.id],
        resolutions=["720p"],
        write=False,
        rebuild_master=False,
    )

    videos = {entry["real"]: entry for entry in result["videos"]}
    assert videos[video_missing.id]["details"]["720p"]["exists"] is False
    assert videos[video_ready.id]["details"]["720p"]["exists"] is True
    assert videos[video_ready.id]["details"]["720p"]["ts_count"] == 1


def test_run_heal_hls_index_rebuild_master_creates_playlist(
    tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _create_video()
    VideoStream.objects.create(video=video, resolution="720p", manifest="#EXTM3U\n")
    manifest = _write_manifest(Path(settings.MEDIA_ROOT), video.id, "720p", ["000.ts"])
    assert manifest.exists()

    master_target = tmp_path / "hls" / str(video.id) / "index.m3u8"

    def fake_master(real_id: int):
        master_target.parent.mkdir(parents=True, exist_ok=True)
        master_target.write_text("#EXTM3U\n", encoding="utf-8")

    def fake_thumb(real_id: int):
        thumb_path = manifest.parent / "thumb.jpg"
        thumb_path.write_bytes(b"thumb")
        return thumb_path

    monkeypatch.setattr(ops, "write_master_playlist", fake_master)
    monkeypatch.setattr(ops, "ensure_thumbnail", fake_thumb)

    result = ops.run_heal_hls_index(
        settings=settings,
        media_root=Path(settings.MEDIA_ROOT),
        publics=[video.id],
        resolutions=["720p"],
        write=True,
        rebuild_master=True,
    )

    entry = result["videos"][0]
    assert "rebuild_master" in entry["actions"]
    assert "generate_thumb" in entry["actions"]
    assert master_target.exists()


def test_run_heal_hls_index_handles_empty_public_list(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    result = ops.run_heal_hls_index(
        settings=settings,
        media_root=Path(settings.MEDIA_ROOT),
        publics=[],
        resolutions=["720p"],
        write=False,
        rebuild_master=False,
    )
    assert result["videos"] == []
    assert any("No videos to process" in warning for warning in result["warnings"])


def test_inspect_filesystem_invalid_resolution_sets_error(
    tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _create_video()

    def fake_find(real_id, res):
        raise ValueError("unsafe")

    monkeypatch.setattr(ops, "find_manifest_path", fake_find)
    result = ops._inspect_filesystem(
        settings, Path(settings.MEDIA_ROOT), [(1, video.id, video)], ["../720p"]
    )
    entry = result["entries"][0]
    assert entry["failure"] is True
    assert "find_manifest_path failed" in entry["error"]


def test_collect_public_ids_deduplicates(monkeypatch, settings):
    monkeypatch.setattr(
        ops.selectors_public,
        "list_for_user_with_public_ids",
        lambda *args, **kwargs: [{"id": "1"}, {"id": "1"}, {"id": "2"}],
    )
    ids, warnings = ops._collect_public_ids(settings, None, ["720p"])
    assert ids == [1, 1, 2]
    assert warnings == []


def test_format_heal_hls_index_text_contains_actions():
    result = {
        "warnings": ["discovery empty"],
        "videos": [
            {
                "public": 1,
                "real": 1,
                "errors": [],
                "warnings": ["stub manifest"],
                "actions": ["create_stream 720p"],
            }
        ],
    }
    text = ops.format_heal_hls_index_text(result)
    assert "create_stream 720p" in text
    assert "stub manifest" in text


def test_format_diagnose_backend_text_verbose():
    report = {
        "summary": {"failures": 0, "warnings": 1},
        "settings": {
            "debug": True,
            "media_root": "/srv/media",
            "warnings": ["missing redis"],
            "allowed_renditions": ["720p"],
            "canonical_renditions": ["720p"],
            "static_url": "/static/",
            "redis_url": None,
            "rq": {
                "queue_default": "default",
                "queue_transcode": "transcode",
                "redis_url": None,
            },
        },
        "videos": [
            {
                "public": 1,
                "real": 3,
                "title": "Sample",
                "created_at": "2024-01-01T00:00:00",
            },
        ],
        "fs_checks": [
            {
                "public": 1,
                "resolution": "720p",
                "failure": False,
                "manifest": "/srv/index.m3u8",
            }
        ],
        "routing": {
            "paths": [{"path": "/api/video/1/720p/index.m3u8", "ok": True}],
            "failures": 0,
        },
        "views": {
            "failures": 0,
            "manifest": {"status": 200},
            "segment": {"status": 200},
        },
        "headers": {
            "manifest": {"ctype": "application/vnd.apple.mpegurl"},
            "segment": {"ctype": "video/vnd.dlna.mpeg-tts"},
        },
        "debug": {
            "failures": 0,
            "queue_health": {"importable": True},
            "debug_renditions": {},
        },
    }
    text = ops.format_diagnose_backend_text(report, verbose=True)
    assert "Settings" in text
    assert "Videos:" in text
    assert "Filesystem" in text


def test_check_routing_verifies_paths():
    video = _create_video()
    result = ops._check_routing([(1, video.id, video)], ["720p"])
    assert result["failures"] == 0
    assert any(item["ok"] for item in result["paths"])


def test_invoke_views_generates_header_report(monkeypatch):
    class DummyManifestView:
        @staticmethod
        def as_view():
            def view(request, movie_id, resolution):
                resp = Response(status=200)
                resp["Content-Type"] = "application/vnd.apple.mpegurl"
                resp["Content-Disposition"] = 'inline; filename="index.m3u8"'
                resp["Cache-Control"] = "public, max-age=0, no-cache"
                resp["ETag"] = '"manifest-etag"'
                return resp

            return view

    class DummySegmentView:
        @staticmethod
        def as_view():
            def view(request, movie_id, resolution, segment):
                resp = Response(status=200)
                resp["Content-Type"] = "video/vnd.dlna.mpeg-tts"
                resp["Content-Disposition"] = 'inline; filename="000.ts"'
                resp["Cache-Control"] = "public, max-age=0, no-cache"
                resp["ETag"] = '"segment-etag"'
                return resp

            return view

    monkeypatch.setattr(ops, "VideoManifestView", DummyManifestView)
    monkeypatch.setattr(ops, "VideoSegmentContentView", DummySegmentView)
    monkeypatch.setattr(ops.importlib.util, "find_spec", lambda name: None)

    fs_entry = {
        "public": 1,
        "real": 1,
        "resolution": "720p",
        "exists": True,
        "failure": False,
        "manifest": "/tmp/index.m3u8",
        "segment_on_disk": "000.ts",
        "segment_zero_on_disk": "000.ts",
    }

    view_info, header_report, header_warnings = ops._invoke_views([fs_entry])
    assert view_info["failures"] == 0
    assert header_report["manifest"]["ctype"].startswith(
        "application/vnd.apple.mpegurl"
    )
    assert header_report["segment"]["disposition_inline"] is True
    assert header_warnings == []


def test_scan_rendition_reports_missing_manifest(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    info = ops._scan_rendition(Path(settings.MEDIA_ROOT), 77, "480p")
    assert info.exists is False
    assert info.bytes is None
    assert info.ts_count == 0
    assert info.errors == []


def test_scan_rendition_detects_manifest_without_segments(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    root = Path(settings.MEDIA_ROOT)
    _write_manifest(root, 81, "720p", ["000.ts"], write_segments=False)

    info = ops._scan_rendition(root, 81, "720p")
    assert info.exists is True
    assert info.is_stub is False
    assert info.ts_count == 0
    assert info.min_bytes is None
    assert info.has_files is False


def test_scan_rendition_reports_segment_sizes(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    root = Path(settings.MEDIA_ROOT)
    _write_manifest(root, 82, "1080p", ["000.ts", "001.ts"])

    info = ops._scan_rendition(root, 82, "1080p")
    assert info.exists is True
    assert info.ts_count == 2
    assert info.min_bytes > 0
    assert info.max_bytes >= info.min_bytes
    assert info.has_files is True


def test_run_heal_hls_index_rebuilds_master_playlist_idempotent(
    tmp_path, settings, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _create_video(id=10)
    root = Path(settings.MEDIA_ROOT)
    _write_manifest(root, video.id, "480p", ["000.ts"])
    _write_manifest(root, video.id, "720p", ["000.ts"])
    master_path = root / "hls" / str(video.id) / "index.m3u8"

    writes: list[str] = []

    def fake_master(real_id: int):
        writes.append(f"rebuild-{real_id}")
        body = [
            "#EXTM3U",
            "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480",
            "480p/index.m3u8",
            "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720",
            "720p/index.m3u8",
        ]
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text("\n".join(body) + "\n", encoding="utf-8")

    def fake_thumbnail(real_id: int):
        thumb = root / "thumbs" / str(real_id) / "default.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"thumb")
        return thumb

    monkeypatch.setattr(ops, "write_master_playlist", fake_master)
    monkeypatch.setattr(ops, "ensure_thumbnail", fake_thumbnail)

    result = ops.run_heal_hls_index(
        settings=settings,
        media_root=root,
        publics=[video.id],
        resolutions=["480p", "720p"],
        write=True,
        rebuild_master=True,
    )
    entry = result["videos"][0]
    detail_480 = entry["details"]["480p"]
    assert detail_480["exists"] is True
    assert detail_480["bytes"] > 0
    assert detail_480["ts_count"] == 1
    assert detail_480["min_bytes"] > 0
    assert detail_480["max_bytes"] >= detail_480["min_bytes"]
    assert "rebuild_master" in entry["actions"]
    assert "generate_thumb" in entry["actions"]

    first_master = master_path.read_text(encoding="utf-8")
    ops.run_heal_hls_index(
        settings=settings,
        media_root=root,
        publics=[video.id],
        resolutions=["480p", "720p"],
        write=True,
        rebuild_master=True,
    )
    second_master = master_path.read_text(encoding="utf-8")
    assert second_master == first_master
    assert writes == ["rebuild-10", "rebuild-10"]
    assert VideoStream.objects.filter(video=video).count() == 2


def test_run_diagnose_backend_reports_rendition_metrics(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _create_video(id=11)
    root = Path(settings.MEDIA_ROOT)
    manifest = _write_manifest(root, video.id, "480p", ["000.ts"])
    (manifest.parent / "000.ts").write_bytes(b"x" * 4)

    report = ops.run_diagnose_backend(
        settings=settings,
        media_root=root,
        explicit_public=[video.id],
        requested_res=["480p"],
    )
    entry = report["fs_checks"][0]
    assert entry["exists"] is True
    assert entry["first_segment"] == "000.ts"
    assert entry["segment_on_disk"] == "000.ts"
    assert entry["min_ts_bytes"] > 0
    assert entry["max_ts_bytes"] >= entry["min_ts_bytes"]


def test_run_diagnose_backend_handles_unknown_resolution(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _create_video(id=12)
    root = Path(settings.MEDIA_ROOT)
    _write_manifest(root, video.id, "480p", ["000.ts"])

    report = ops.run_diagnose_backend(
        settings=settings,
        media_root=root,
        explicit_public=[video.id],
        requested_res=["unknown"],
    )
    entry = report["fs_checks"][0]
    assert entry["resolution"] == "unknown"
    assert entry["failure"] is True
    assert report["summary"]["failures"] >= 1


def test_run_diagnose_backend_handles_empty_inputs(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    report = ops.run_diagnose_backend(
        settings=settings,
        media_root=Path(settings.MEDIA_ROOT),
        explicit_public=[],
        requested_res=[],
    )
    assert report["videos"] == []
    assert report["summary"]["warnings"] >= 1


def test_inspect_filesystem_normalizes_mixed_paths(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    video = _create_video()
    root = Path(settings.MEDIA_ROOT)
    manifest = root / "hls" / str(video.id) / "720p" / "index.m3u8"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    nested = manifest.parent / "alt" / "000.ts"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b"segment-bytes")
    manifest.write_text("#EXTM3U\n#EXTINF:10,\nalt\\000.ts\n", encoding="utf-8")

    resolved = [(video.id, video.id, video)]
    result = ops._inspect_filesystem(settings, root, resolved, ["720p"])
    entry = result["entries"][0]
    assert entry["first_segment"] == "alt\\000.ts"
    assert entry["segment_on_disk"] == "alt/000.ts"
    assert entry["segment_zero_on_disk"] == "alt/000.ts"
    assert entry["failure"] is False
