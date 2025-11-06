from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from jobs.domain import services
from jobs.domain.services import transcode_pending_key
from videos.domain import hls as hls_utils


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_enqueue_transcode_uses_rq_queue(monkeypatch, settings, tmp_path):
    settings.IS_TEST_ENV = False
    settings.ENV = "prod"
    settings.MEDIA_ROOT = tmp_path
    settings.RQ_URL = "redis://127.0.0.1:6379/0"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {
        "transcode": {"URL": settings.RQ_URL, "DEFAULT_TIMEOUT": 60 * 20},
    }

    monkeypatch.setattr(services, "_prepare_resolutions", lambda _: ["480p"])

    def fail_run(*args, **kwargs):
        raise AssertionError("run_transcode_job should not run when enqueued.")

    monkeypatch.setattr(services, "run_transcode_job", fail_run)

    class DummyQueue:
        name = "transcode"

    captured: dict[str, Any] = {}

    def fake_enqueue(video_id_arg, resolutions, *, queue=None, force=False):
        captured["video_id"] = video_id_arg
        captured["resolutions"] = list(resolutions)
        captured["queue"] = queue
        return {"accepted": True, "job_id": "job-1", "queue": queue.name if queue else None}

    dummy_queue = DummyQueue()
    monkeypatch.setattr(services.transcode_queue, "enqueue_transcode_job", fake_enqueue)
    monkeypatch.setattr(services.transcode_queue, "get_transcode_queue", lambda: dummy_queue)

    result = services.enqueue_transcode(101, target_resolutions=["480p"])

    assert result == {"accepted": True, "job_id": "job-1", "queue": "transcode"}
    assert captured["queue"] is dummy_queue
    assert captured["resolutions"] == ["480p"]
    assert cache.get(transcode_pending_key(101)) is True


@pytest.mark.django_db
def test_enqueue_transcode_falls_back_when_queue_fails(monkeypatch, settings, tmp_path):
    settings.IS_TEST_ENV = False
    settings.ENV = "prod"
    settings.MEDIA_ROOT = tmp_path
    settings.RQ_URL = "redis://127.0.0.1:6379/0"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {
        "transcode": {"URL": settings.RQ_URL, "DEFAULT_TIMEOUT": 60 * 20},
    }

    monkeypatch.setattr(services, "_prepare_resolutions", lambda _: ["480p"])

    def fake_run(video_id: int, resolutions):
        return {"ok": True, "video_id": video_id, "resolutions": resolutions}

    run_calls: dict[str, tuple[int, list[str]]] = {}

    def run_and_record(video_id: int, resolutions):
        run_calls["value"] = (video_id, resolutions)
        return fake_run(video_id, resolutions)

    monkeypatch.setattr(services, "run_transcode_job", run_and_record)
    monkeypatch.setattr(services.transcode_queue, "get_transcode_queue", lambda: None)

    result = services.enqueue_transcode(202, target_resolutions=["480p"])

    assert result == {"ok": True, "video_id": 202, "resolutions": ["480p"]}
    assert run_calls["value"] == (202, ["480p"])
    assert cache.get(transcode_pending_key(202)) is None


def test_write_master_playlist_includes_all_profiles(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    import videos.domain.hls as hls_module
    importlib.reload(hls_module)

    video_id = 303
    base_dir = tmp_path / "hls" / str(video_id)

    for resolution in ("480p", "720p"):
        rendition_dir = base_dir / resolution
        rendition_dir.mkdir(parents=True, exist_ok=True)
        (rendition_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    hls_module.write_master_playlist(video_id)

    master_path = base_dir / "index.m3u8"
    assert master_path.exists()
    master_content = master_path.read_text(encoding="utf-8")
    assert master_content.count("#EXT-X-STREAM-INF") >= 2
    assert "480p/index.m3u8" in master_content
    assert "720p/index.m3u8" in master_content



@pytest.mark.django_db
def test_queue_health_reports_available_queue(monkeypatch, settings):
    settings.DEBUG = True
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}}

    class DummyQueue:
        name = "transcode"
        count = 4

    monkeypatch.setattr("videos.api.views.queue_health.get_transcode_queue", lambda: DummyQueue())
    client = APIClient()
    response = client.get("/api/_debug/queue")

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-cache"
    assert response.json() == {"queue": "transcode", "connected": True, "count": 4}


@pytest.mark.django_db
def test_queue_health_handles_unavailable_queue(monkeypatch, settings):
    settings.DEBUG = True
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {}

    monkeypatch.setattr("videos.api.views.queue_health.get_transcode_queue", lambda: None)
    client = APIClient()
    response = client.get("/api/_debug/queue")

    payload = response.json()
    assert response.status_code == 200
    assert payload["queue"] == "transcode"
    assert payload["connected"] is False
    assert payload["count"] is None
    assert "detail" in payload
    assert response["Cache-Control"] == "no-cache"




@pytest.mark.django_db
def test_queue_health_returns_404_when_debug_false(settings):
    settings.DEBUG = False
    client = APIClient()

    response = client.get("/api/_debug/queue")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}


@pytest.mark.django_db
def test_queue_health_handles_callable_count(monkeypatch, settings):
    settings.DEBUG = True
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}}

    class DummyQueue:
        name = "transcode"

        def count(self):
            return 7

    monkeypatch.setattr("videos.api.views.queue_health.get_transcode_queue", lambda: DummyQueue())
    client = APIClient()
    response = client.get("/api/_debug/queue")

    assert response.status_code == 200
    assert response.json() == {"queue": "transcode", "connected": True, "count": 7}
