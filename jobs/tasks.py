from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings

from jobs.domain import services
from jobs.domain.services import TranscodeError

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

    max_attempts = max(int(getattr(settings, "TRANSCODE_RETRY_MAX", 6)), 1)
    delays = list(getattr(settings, "TRANSCODE_RETRY_DELAYS", [1, 2, 4, 8, 16, 32]))
    if not delays:
        delays = [0]
    while len(delays) < max_attempts - 1:
        delays.append(delays[-1])

    is_test_env = getattr(settings, "ENV", "").lower() == "test" or getattr(
        settings, "USE_SQLITE_FOR_TESTS", False
    )

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                services.run_transcode_job(video_id, resolved_resolutions)
                break
            except TranscodeError as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code in {400, 403, 404, 409}:
                    raise
                if attempt >= max_attempts:
                    raise
                delay = 0 if is_test_env else delays[attempt - 1]
                logger.info(
                    "Transcode retry scheduled: video_id=%s, attempt=%s/%s, delay=%ss, error=%s",
                    video_id,
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                if delay > 0:
                    time.sleep(delay)
            except Exception as exc:  # pragma: no cover - defensive fallback
                if attempt >= max_attempts:
                    raise
                delay = 0 if is_test_env else delays[attempt - 1]
                logger.info(
                    "Transcode transient error, retry scheduled: video_id=%s, attempt=%s/%s, delay=%ss, error=%s",
                    video_id,
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                if delay > 0:
                    time.sleep(delay)
    finally:
        if env_overridden:
            setattr(settings, "ENV", original_env)

    return {
        "ok": True,
        "video_id": video_id,
        "resolutions": resolved_resolutions,
    }
