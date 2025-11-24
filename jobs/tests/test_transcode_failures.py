from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings

from jobs.domain import services as job_services
from videos.domain.models import Video

pytestmark = pytest.mark.django_db


@override_settings()
def test_run_transcode_job_marks_failed_and_clears_lock(
    monkeypatch, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    video = Video.objects.create(
        title="Failure Case",
        description="Test",
        thumbnail_url="http://example.com/thumb.jpg",
        category="drama",
        is_published=True,
    )

    source_path = tmp_path / "sources" / f"{video.pk}.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"fake")

    monkeypatch.setattr(
        job_services, "resolve_source_path", lambda *_args, **_kwargs: source_path
    )

    def fail_run(*_args, **_kwargs):
        raise FileNotFoundError("ffmpeg missing")

    monkeypatch.setattr(job_services, "_run_ffmpeg_for_profile", fail_run)

    lock_key = job_services.transcode_lock_key(video.pk)
    cache.delete(lock_key)

    with pytest.raises(job_services.TranscodeError):
        job_services.run_transcode_job(video.pk, ["480p"], force=True)

    status = job_services.get_transcode_status(video.pk)
    assert status["state"] == "failed"
    assert cache.get(lock_key) is None
