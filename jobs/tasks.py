from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from jobs.domain import services
from jobs.domain.services import TranscodeError
from rq import get_current_job

logger = logging.getLogger("videoflix")


def transcode_video_job(video_id: int, resolutions: list[str] | None = None) -> dict[str, Any]:
    """
    Execute the actual transcode using the existing domain service.

    Returns a small status payload so worker logs can capture context.
    """
    try:
        resolved_resolutions = services._prepare_resolutions(resolutions)
    except TranscodeError as exc:  # pragma: no cover - defensive, should be validated upstream
        return {
            "ok": False,
            "video_id": video_id,
            "error": str(exc),
            "status_code": getattr(exc, "status_code", None),
        }

    original_env = getattr(settings, "ENV", "")
    env_lower = str(original_env).lower()
    env_overridden = False

    if env_lower in {"dev", "prod"}:
        setattr(settings, "ENV", "worker")
        env_overridden = True

    try:
        services.enqueue_transcode(
            video_id,
            target_resolutions=resolved_resolutions,
        )
    except TranscodeError as exc:
        job = None
        try:
            job = get_current_job()
        except Exception:  # pragma: no cover - defensive
            job = None

        retries_left = getattr(job, "retries_left", 0) if job else 0
        if retries_left:
            logger.info(
                "Transcode job failed, retry scheduled: video_id=%s, retries_left=%s, error=%s",
                video_id,
                retries_left,
                exc,
            )
        else:
            logger.warning(
                "Transcode job fehlgeschlagen, keine weiteren Versuche: video_id=%s, error=%s",
                video_id,
                exc,
            )
        raise
    finally:
        if env_overridden:
            setattr(settings, "ENV", original_env)

    return {
        "ok": True,
        "video_id": video_id,
        "resolutions": resolved_resolutions,
    }
