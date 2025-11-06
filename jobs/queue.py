from __future__ import annotations

from __future__ import annotations

from typing import Any, Iterable, Sequence

from django.conf import settings
from rq import Retry


def get_transcode_queue():
    """
    Return the configured django-rq queue for transcodes or ``None`` when unavailable.
    """
    queue_name = (getattr(settings, "RQ_QUEUE_TRANSCODE", "") or "").strip()
    if not queue_name:
        return None

    queues = getattr(settings, "RQ_QUEUES", {}) or {}
    if queue_name not in queues:
        return None

    try:  # Import lazily so tests can run without django-rq installed
        import django_rq  # type: ignore
    except Exception:
        return None

    try:
        return django_rq.get_queue(queue_name)
    except Exception:
        return None


def enqueue_transcode_job(
    video_id: int,
    resolutions: Iterable[str] | None = None,
    *,
    queue=None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Enqueue the asynchronous transcode job on the configured queue.

    Raises ``RuntimeError`` when the queue is unavailable so callers can fall back.
    """
    queue_obj = queue or get_transcode_queue()
    if queue_obj is None:
        raise RuntimeError("Transcode queue is not available.")

    payload: Sequence[str] = list(resolutions or [])
    job = queue_obj.enqueue(
        "jobs.tasks.transcode_video_job",
        args=(video_id, list(payload) or None),
        kwargs={"force": force},
        job_timeout=60 * 20,
        result_ttl=86400,
        failure_ttl=3600,
        retry=Retry(max=4, interval=[5, 15, 45, 120]),
    )
    job.meta["video_id"] = video_id
    job.meta["resolutions"] = list(payload)
    job.save_meta()
    return {"accepted": True, "job_id": getattr(job, "id", None), "queue": queue_obj.name}
