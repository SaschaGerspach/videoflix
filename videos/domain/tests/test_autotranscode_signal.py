from __future__ import annotations

import logging
from typing import Any

import pytest
from django.core.cache import cache

from jobs.domain import services as transcode_services
from videos.domain.models import Video
from videos.domain.services_autotranscode import schedule_default_transcodes


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_autotranscode_signal_enqueues_once_with_debounce(
    monkeypatch, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    settings.ENV = "prod"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {
        "transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}
    }

    video = Video.objects.create(
        title="Autotranscode Debounce",
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )

    source_path = transcode_services.get_video_source_path(video.id)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"stub")

    def fake_ensure(video_obj):
        Video.objects.filter(pk=video_obj.pk).update(
            height=900,
            video_bitrate_kbps=3000,
        )
        video_obj.refresh_from_db()
        video_obj._source_metadata_cache = {"height": 900, "video_bitrate_kbps": 3000}
        return video_obj

    monkeypatch.setattr(
        "videos.domain.services_autotranscode.ensure_source_metadata", fake_ensure
    )

    dummy_queue = type("DummyQueue", (), {"name": "transcode"})()
    calls: list[dict[str, Any]] = []

    def fake_enqueue(video_id: int, resolutions: list[str], *, queue=None, force=False):
        calls.append(
            {
                "video_id": video_id,
                "resolutions": list(resolutions),
                "queue": queue,
                "force": force,
            }
        )
        return {
            "accepted": True,
            "job_id": "job-1",
            "queue": getattr(queue, "name", None),
        }

    monkeypatch.setattr(
        transcode_services.transcode_queue, "get_transcode_queue", lambda: dummy_queue
    )
    monkeypatch.setattr(
        transcode_services.transcode_queue, "enqueue_transcode_job", fake_enqueue
    )

    video.title = "Autotranscode Debounce First"
    video.save()
    video.title = "Autotranscode Debounce Second"
    video.save()

    assert len(calls) == 1
    call = calls[0]
    assert call["video_id"] == video.id
    assert call["resolutions"] == ["720p", "480p"]
    assert call["queue"] is dummy_queue


@pytest.mark.django_db
def test_autotranscode_inline_fallback(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    settings.ENV = "dev"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {
        "transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}
    }

    video = Video.objects.create(
        title="Inline Fallback",
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )

    source_path = transcode_services.get_video_source_path(video.id)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"stub")

    def fake_ensure(video_obj):
        Video.objects.filter(pk=video_obj.pk).update(
            height=900,
            video_bitrate_kbps=3000,
        )
        video_obj.refresh_from_db()
        video_obj._source_metadata_cache = {"height": 900, "video_bitrate_kbps": 3000}
        return video_obj

    monkeypatch.setattr(
        "videos.domain.services_autotranscode.ensure_source_metadata", fake_ensure
    )

    monkeypatch.setattr(
        transcode_services.transcode_queue, "get_transcode_queue", lambda: None
    )

    recorded: dict[str, Any] = {}

    def fake_inline(video_id: int, resolutions):
        recorded["value"] = (video_id, tuple(resolutions))
        return {"ok": True, "message": "inline"}

    monkeypatch.setattr("jobs.domain.services.run_transcode_job", fake_inline)

    schedule_default_transcodes(video.id)

    assert recorded["value"] == (video.id, ("720p", "480p"))


@pytest.mark.django_db
def test_autotranscode_uses_selected_rungs(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    settings.ENV = "prod"

    video = Video.objects.create(
        title="Custom Selection",
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )

    source_path = transcode_services.get_video_source_path(video.id)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"stub")

    def fake_ensure(video_obj):
        video_obj._source_metadata_cache = {"height": 800, "video_bitrate_kbps": 3000}
        return video_obj

    monkeypatch.setattr(
        "videos.domain.services_autotranscode.ensure_source_metadata", fake_ensure
    )

    recorded_select: dict[str, Any] = {}

    def fake_select(meta):
        recorded_select["meta"] = meta
        return ["1080p", "720p", "480p"]

    monkeypatch.setattr(
        "videos.domain.services_autotranscode.select_rungs_from_source", fake_select
    )

    recorded_enqueue: dict[str, Any] = {}

    def fake_enqueue(video_id, *, target_resolutions, force=False):
        recorded_enqueue["args"] = (video_id, list(target_resolutions), force)
        return {"queued": True}

    monkeypatch.setattr(
        "videos.domain.services_autotranscode.transcode_services.enqueue_transcode",
        fake_enqueue,
    )

    schedule_default_transcodes(video.id, force=True)

    assert recorded_select["meta"]["height"] == 800
    assert recorded_enqueue["args"][0] == video.id
    assert recorded_enqueue["args"][1] == ["1080p", "720p", "480p"]


@pytest.mark.django_db
def test_autotranscode_skips_when_no_source(caplog, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    settings.ENV = "dev"

    video = Video.objects.create(
        title="No Source",
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )

    caplog.set_level(logging.INFO)
    schedule_default_transcodes(video.id)

    assert any(
        "autotranscode: skip (no source)" in message for message in caplog.messages
    )
