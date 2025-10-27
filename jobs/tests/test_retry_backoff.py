from __future__ import annotations

import pytest

from jobs.domain import services
from jobs.domain.services import TranscodeError
from jobs.tasks import transcode_video_job


@pytest.mark.django_db
def test_transcode_retries_transient(monkeypatch, settings):
    settings.ENV = "test"
    settings.TRANSCODE_RETRY_MAX = 4
    settings.TRANSCODE_RETRY_DELAYS = [1, 2, 3]

    attempts = {"count": 0}

    def fake_run(video_id, resolutions):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TranscodeError("temporary", status_code=500)

    monkeypatch.setattr(services, "run_transcode_job", fake_run)

    def fail_sleep(seconds):  # pragma: no cover - should not be called in tests
        raise AssertionError("sleep should not be called in test env")

    monkeypatch.setattr("jobs.tasks.time.sleep", fail_sleep)

    result = transcode_video_job(7, ["360p"])

    assert attempts["count"] == 3
    assert result == {"ok": True, "video_id": 7, "resolutions": ["360p"]}


@pytest.mark.django_db
def test_transcode_permanent_error_no_retry(monkeypatch, settings):
    settings.ENV = "test"
    settings.TRANSCODE_RETRY_MAX = 4

    def fake_run(video_id, resolutions):
        raise TranscodeError("missing", status_code=404)

    monkeypatch.setattr(services, "run_transcode_job", fake_run)

    def fail_sleep(seconds):  # pragma: no cover - should not be called
        raise AssertionError("sleep should not be called for permanent errors")

    monkeypatch.setattr("jobs.tasks.time.sleep", fail_sleep)

    with pytest.raises(TranscodeError) as excinfo:
        transcode_video_job(3, ["360p"])

    assert getattr(excinfo.value, "status_code", None) == 404
