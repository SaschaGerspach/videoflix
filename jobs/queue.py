from __future__ import annotations

from functools import lru_cache
from typing import Any

import redis
from django.conf import settings
from django.core.cache import cache
from rq import Queue, Retry

from jobs.domain import services
from jobs.domain.services import TranscodeError


@lru_cache(maxsize=1)
def get_rq_connection() -> redis.Redis:
    """Return a shared Redis connection for RQ usage."""
    return redis.from_url(settings.RQ_REDIS_URL)


def enqueue_transcode_job(video_id: int, resolutions: list[str] | None = None) -> dict[str, Any]:
    """Enqueue a transcode job or run synchronously in test environments."""
    if getattr(settings, "IS_TEST_ENV", False):
        return services.enqueue_transcode(
            video_id,
            target_resolutions=resolutions,
        )

    pending_key = services.transcode_pending_key(video_id)
    if cache.get(pending_key) or services.is_transcode_locked(video_id):
        raise TranscodeError("Transcode already in progress.", status_code=409)
    if not cache.add(pending_key, True, timeout=services.TRANSCODE_LOCK_TTL_SECONDS):
        raise TranscodeError("Transcode already in progress.", status_code=409)

    queue = Queue(
        settings.RQ_QUEUE_TRANSCODE,
        connection=get_rq_connection(),
    )
    try:
        job = queue.enqueue(
            "jobs.tasks.transcode_video_job",
            args=(video_id, resolutions),
            job_timeout="15m",
            result_ttl=600,
            ttl=600,
            failure_ttl=3600,
            retry=Retry(max=4, interval=[5, 15, 45, 120]),
        )
    except Exception:
        cache.delete(pending_key)
        raise
    return {"accepted": True, "job_id": job.id, "queue": settings.RQ_QUEUE_TRANSCODE}


# Änderungen:
# - Transcoding-Jobs erhalten jetzt eine RQ-Retry-Konfiguration (4 Versuche, 5/15/45/120 s Backoff).
# - Pending-Locks werden weiterhin sauber entfernt, auch wenn Enqueue scheitert.
