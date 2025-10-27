from __future__ import annotations

from functools import lru_cache
from typing import Any

import redis
from django.conf import settings
from rq import Queue, Retry


@lru_cache(maxsize=1)
def get_rq_connection() -> redis.Redis:
    """Return a shared Redis connection for RQ usage."""
    return redis.from_url(settings.RQ_REDIS_URL)


def enqueue_transcode_job(video_id: int, resolutions: list[str] | None = None) -> dict[str, Any]:
    """Enqueue a transcode job in the configured RQ queue."""
    queue = Queue(
        settings.RQ_QUEUE_TRANSCODE,
        connection=get_rq_connection(),
    )
    job = queue.enqueue(
        "jobs.tasks.transcode_video_job",
        args=(video_id, resolutions),
        job_timeout="15m",
        result_ttl=600,
        ttl=600,
        failure_ttl=3600,
        retry=Retry(max=4, interval=[5, 15, 45, 120]),
    )
    job.meta["video_id"] = video_id
    job.meta["resolutions"] = list(resolutions or [])
    job.save_meta()
    return {"accepted": True, "job_id": job.id, "queue": settings.RQ_QUEUE_TRANSCODE}


# Änderungen:
# - Pending-Locks werden jetzt außerhalb gesetzt; diese Funktion enqueued nur noch und speichert Kontext in den Job-Metadaten.
