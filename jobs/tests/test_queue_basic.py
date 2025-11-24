from __future__ import annotations

import builtins
import sys
import types

import pytest

import jobs.queue as queue_module


@pytest.mark.django_db
def test_get_transcode_queue_returns_none_when_import_fails(monkeypatch, settings):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0"}}

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "django_rq":
            raise ImportError("forced missing module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert queue_module.get_transcode_queue() is None

    with pytest.raises(RuntimeError):
        queue_module.enqueue_transcode_job(123, resolutions=["480p"])


@pytest.mark.django_db
def test_enqueue_transcode_job_uses_queue(monkeypatch, settings):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0"}}

    class DummyJob:
        def __init__(self):
            self.id = "job-1"
            self.meta: dict[str, object] = {}

        def save_meta(self):
            return None

    class DummyQueue:
        name = "transcode"

        def __init__(self):
            self.calls: list[tuple[tuple, dict]] = []

        def enqueue(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return DummyJob()

    dummy_queue = DummyQueue()

    fake_module = types.SimpleNamespace(get_queue=lambda name: dummy_queue)
    monkeypatch.setitem(sys.modules, "django_rq", fake_module)

    queue_obj = queue_module.get_transcode_queue()
    assert queue_obj is dummy_queue

    result = queue_module.enqueue_transcode_job(
        456, resolutions=["480p"], queue=queue_obj
    )

    assert result == {"accepted": True, "job_id": "job-1", "queue": "transcode"}
    assert dummy_queue.calls, "expected enqueue to be invoked"
    args, kwargs = dummy_queue.calls[0]
    assert args[0] == "jobs.tasks.transcode_video_job"
    assert kwargs["args"] == (456, ["480p"])
    assert kwargs["kwargs"] == {"force": False}


def test_get_transcode_queue_without_config(settings):
    settings.RQ_QUEUE_TRANSCODE = ""
    settings.RQ_QUEUES = {}
    assert queue_module.get_transcode_queue() is None


def test_get_transcode_queue_missing_mapping(settings):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {}
    assert queue_module.get_transcode_queue() is None


@pytest.mark.django_db
def test_get_transcode_queue_handles_queue_failure(monkeypatch, settings):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    settings.RQ_QUEUES = {"transcode": {"URL": "redis://127.0.0.1:6379/0"}}

    def raise_error(name):
        raise RuntimeError("boom")

    fake_module = types.SimpleNamespace(get_queue=raise_error)
    monkeypatch.setitem(sys.modules, "django_rq", fake_module)

    assert queue_module.get_transcode_queue() is None
