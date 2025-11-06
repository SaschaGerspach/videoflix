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
def test_autotranscode_signal_enqueues_once_with_debounce(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    settings.ENV = "prod"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}}

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
    monkeypatch.setattr(transcode_services, "probe_source_height", lambda *_: 900)

    dummy_queue = type("DummyQueue", (), {"name": "transcode"})()
    calls: list[dict[str, Any]] = []

    def fake_enqueue(video_id: int, resolutions: list[str], *, queue=None, force=False):
        calls.append({
            "video_id": video_id,
            "resolutions": list(resolutions),
            "queue": queue,
            "force": force,
        })
        return {"accepted": True, "job_id": "job-1", "queue": getattr(queue, "name", None)}

    monkeypatch.setattr(transcode_services.transcode_queue, "get_transcode_queue", lambda: dummy_queue)
    monkeypatch.setattr(transcode_services.transcode_queue, "enqueue_transcode_job", fake_enqueue)

    video.title = "Autotranscode Debounce First"
    video.save()
    video.title = "Autotranscode Debounce Second"
    video.save()

    assert len(calls) == 1
    call = calls[0]
    assert call["video_id"] == video.id
    assert call["resolutions"] == ["480p", "720p"]
    assert call["queue"] is dummy_queue


@pytest.mark.django_db
def test_autotranscode_inline_fallback(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    settings.ENV = "dev"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}}

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
    monkeypatch.setattr(transcode_services, "probe_source_height", lambda *_: 900)

    monkeypatch.setattr(transcode_services.transcode_queue, "get_transcode_queue", lambda: None)

    recorded: dict[str, Any] = {}

    def fake_inline(video_id: int, resolutions):
        recorded["value"] = (video_id, tuple(resolutions))
        return {"ok": True, "message": "inline"}

    monkeypatch.setattr("jobs.domain.services.run_transcode_job", fake_inline)

    schedule_default_transcodes(video.id)

    assert recorded["value"] == (video.id, ("480p", "720p"))


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

    assert any("autotranscode: skip (no source)" in message for message in caplog.messages)


def _create_video_with_source(tmp_path, title: str = "Auto Source") -> Video:
    video = Video.objects.create(
        title=title,
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )
    source_path = transcode_services.get_video_source_path(video.id)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"stub")
    return video


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("detected_height", "expected"),
    [
        (650, ["480p"]),
        (900, ["480p", "720p"]),
        (1300, ["480p", "720p", "1080p"]),
    ],
)
def test_autotranscode_selects_profiles_by_detected_height(
    monkeypatch, settings, tmp_path, detected_height, expected
):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    video = _create_video_with_source(tmp_path, title=f"Auto {detected_height}")

    calls: list[list[str]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None, force=False):
        calls.append(list(target_resolutions or []))
        return {"accepted": True}

    monkeypatch.setattr(transcode_services, "probe_source_height", lambda *_: detected_height)
    monkeypatch.setattr(transcode_services, "enqueue_transcode", fake_enqueue)

    schedule_default_transcodes(video.id, force=True)

    assert calls and calls[-1] == expected


@pytest.mark.django_db
def test_autotranscode_unknown_height_falls_back_to_defaults(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.IS_TEST_ENV = False
    video = _create_video_with_source(tmp_path, title="Auto Unknown Height")

    calls: list[list[str]] = []

    def fake_enqueue(video_id: int, *, target_resolutions=None, force=False):
        calls.append(list(target_resolutions or []))
        return {"accepted": True}

    monkeypatch.setattr(transcode_services, "probe_source_height", lambda *_: None)
    monkeypatch.setattr(transcode_services, "enqueue_transcode", fake_enqueue)

    schedule_default_transcodes(video.id, force=True)

    assert calls and calls[-1] == ["480p", "720p"]

