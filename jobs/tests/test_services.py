import pytest
from django.core.cache import cache

from jobs.domain import services
from videos.domain.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    settings.ENV = "dev"
    settings.RQ_QUEUE_TRANSCODE = ""
    settings.RQ_QUEUES = {}
    yield root


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _ensure_video_record(video_id: int) -> Video:
    video, _ = Video.objects.update_or_create(
        id=video_id,
        defaults={
            "title": f"Transcode {video_id}",
            "description": "Test video generated for transcode suite.",
            "thumbnail_url": "http://example.com/thumb.jpg",
            "category": "drama",
            "is_published": True,
        },
    )
    return video


def _create_source_file(video_id: int) -> None:
    _ensure_video_record(video_id)
    source_path = services.get_video_source_path(video_id)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"dummy video content")


def test_enqueue_transcode_returns_ok(settings):
    settings.ENV = "test"
    _ensure_video_record(42)
    res = services.enqueue_transcode(42, target_resolutions=["720p"])
    assert res["ok"] is True
    assert "Transcode triggered" in res["message"]
    assert services.get_transcode_status(42) == {"state": "ready", "message": None}


def test_status_processing_then_ready_on_success(monkeypatch):
    video_id = 7
    _create_source_file(video_id)
    seen_processing = {"value": False}

    assert services.get_transcode_status(video_id)["state"] == "unknown"

    def _fake_profile_run(video_id_arg, source, resolution):
        assert video_id_arg == video_id
        status = services.get_transcode_status(video_id_arg)
        if status["state"] == "processing":
            seen_processing["value"] = True
        output_dir = services.get_transcode_output_dir(video_id_arg, resolution)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.m3u8").write_text("#EXTM3U\n")

    monkeypatch.setattr(services, "_run_ffmpeg_for_profile", _fake_profile_run)

    services.enqueue_transcode(video_id, target_resolutions=["360p"])

    assert seen_processing["value"] is True
    assert services.get_transcode_status(video_id) == {"state": "ready", "message": None}


def test_status_failed_on_missing_source():
    video_id = 101
    _ensure_video_record(video_id)

    with pytest.raises(services.TranscodeError) as exc:
        services.enqueue_transcode(video_id, target_resolutions=["360p"])

    assert exc.value.status_code == 500
    assert services.get_transcode_status(video_id) == {
        "state": "failed",
        "message": "Video source not found.",
    }


def test_status_failed_on_missing_ffmpeg(monkeypatch):
    video_id = 202
    _create_source_file(video_id)

    def _missing_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg binary missing")

    monkeypatch.setattr(services.subprocess, "run", _missing_ffmpeg)

    with pytest.raises(services.TranscodeError) as exc:
        services.enqueue_transcode(video_id, target_resolutions=["360p"])

    assert exc.value.status_code == 500
    status = services.get_transcode_status(video_id)
    assert status["state"] == "failed"
    assert "ffmpeg" in (status["message"] or "")


def test_get_transcode_status_derives_ready_from_filesystem():
    video_id = 303
    output_dir = services.get_transcode_output_dir(video_id, "360p")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.m3u8").write_text("#EXTM3U\n")

    assert services.get_transcode_status(video_id) == {"state": "ready", "message": None}


def test_lock_idempotency():
    video_id = 404
    lock_key = services.transcode_lock_key(video_id)
    cache.set(lock_key, True, timeout=900)

    assert services.get_transcode_status(video_id)["state"] == "unknown"

    with pytest.raises(services.TranscodeError) as exc:
        services.enqueue_transcode(video_id, target_resolutions=["360p"])

    assert exc.value.status_code == 409
    assert services.is_transcode_locked(video_id) is True
    assert services.get_transcode_status(video_id)["state"] == "unknown"


def test_enqueue_transcode_clears_stale_pending(monkeypatch, settings):
    settings.IS_TEST_ENV = False
    settings.ENV = "dev"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {
        "transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}
    }
    video_id = 515
    _ensure_video_record(video_id)
    pending_key = services.transcode_pending_key(video_id)
    cache.set(pending_key, True, timeout=30)

    monkeypatch.setattr(services, "_has_active_transcode_job", lambda vid: False)

    captured = {}

    class DummyQueue:
        name = "transcode"

        def __init__(self):
            self.calls = []

        def enqueue(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            job = type("Job", (), {"id": "rq-job-1", "meta": {}, "save_meta": lambda self: None})()
            return job

    dummy_queue = DummyQueue()

    def fake_enqueue(video_id_arg, resolutions, *, queue=None, force=False):
        assert queue is dummy_queue
        captured["args"] = (video_id_arg, tuple(resolutions))
        return {"accepted": True, "job_id": "rq-job-1", "queue": dummy_queue.name}

    monkeypatch.setattr(services.transcode_queue, "get_transcode_queue", lambda: dummy_queue)
    monkeypatch.setattr(services.transcode_queue, "enqueue_transcode_job", fake_enqueue)

    result = services.enqueue_transcode(video_id, target_resolutions=["360p"])

    assert captured["args"][0] == video_id
    assert captured["args"][1] == ("360p",)
    assert result["job_id"] == "rq-job-1"
    assert cache.get(pending_key) is True


def test_enqueue_transcode_sets_pending_ttl(monkeypatch, settings):
    settings.IS_TEST_ENV = False
    settings.ENV = "dev"
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {
        "transcode": {"URL": "redis://127.0.0.1:6379/0", "DEFAULT_TIMEOUT": 60 * 20}
    }
    video_id = 616
    _ensure_video_record(video_id)
    pending_key = services.transcode_pending_key(video_id)

    orig_set = cache.set
    recorded_timeout = {}

    def fake_set(key, value, timeout=None, version=None, **kwargs):
        if key == pending_key:
            recorded_timeout["value"] = timeout
        return orig_set(key, value, timeout=timeout, version=version, **kwargs)

    monkeypatch.setattr(services.cache, "set", fake_set)

    class DummyQueue:
        name = "transcode"

        def enqueue(self, *args, **kwargs):
            return type("Job", (), {"id": "rq-job-2", "meta": {}, "save_meta": lambda self: None})()

    dummy_queue = DummyQueue()

    monkeypatch.setattr(services.transcode_queue, "get_transcode_queue", lambda: dummy_queue)
    monkeypatch.setattr(
        services.transcode_queue,
        "enqueue_transcode_job",
        lambda video_id_arg, resolutions, *, queue=None, force=False: {
            "accepted": True,
            "job_id": "rq-job-2",
            "queue": dummy_queue.name,
        },
    )

    services.enqueue_transcode(video_id, target_resolutions=["360p"])

    assert recorded_timeout.get("value") == services.PENDING_TTL_SECONDS




