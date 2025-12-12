from __future__ import annotations

import logging
import time
from typing import Any
from collections.abc import Callable, Iterable

from django.conf import settings

from jobs.domain import services
from jobs.domain.services import TranscodeError

logger = logging.getLogger("videoflix")


def _safe_run_transcode(
    runner: Callable[..., Any],
    video_id: int,
    resolutions: Iterable[str],
    *,
    force: bool,
) -> Any:
    """
    Execute the provided run_transcode callable while tolerating deployments/tests
    that do not accept a ``force`` keyword argument yet.
    """
    try:
        return runner(video_id, resolutions, force=force)
    except TypeError as exc:
        message = str(exc)
        if "force" not in message or "unexpected keyword argument" not in message:
            raise
        return runner(video_id, resolutions)


def transcode_video_job(
    video_id: int,
    resolutions: Iterable[str] | None = None,
    *,
    force: bool = False,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute the actual transcode using the existing domain service.

    Returns a small status payload so worker logs can capture context.
    """
    resolutions_result = _prepare_resolutions_safe(resolutions, video_id)
    if resolutions_result.get("error"):
        return resolutions_result

    resolved_resolutions = resolutions_result["resolutions"]
    env_state = _override_env_for_worker()
    retry_plan = _build_retry_plan()

    try:
        _run_transcode_with_retries(
            video_id,
            resolved_resolutions,
            force=bool(force),
            max_attempts=retry_plan["max_attempts"],
            delays=retry_plan["delays"],
            is_test_env=retry_plan["is_test_env"],
        )
    finally:
        _restore_env(env_state)

    return {
        "ok": True,
        "video_id": video_id,
        "resolutions": resolved_resolutions,
    }


def _prepare_resolutions_safe(
    resolutions: Iterable[str] | None, video_id: int
) -> dict[str, Any]:
    """Validate or normalise resolutions, returning error payload on failure."""
    try:
        resolved_resolutions = services._prepare_resolutions(resolutions)
    except TranscodeError as exc:  # pragma: no cover - defensive, validated upstream
        return {
            "ok": False,
            "video_id": video_id,
            "error": str(exc),
            "status_code": getattr(exc, "status_code", None),
            "resolutions": [],
        }
    return {"ok": True, "resolutions": resolved_resolutions}


def _override_env_for_worker() -> dict[str, Any]:
    """Override ENV for worker contexts when running in dev/prod."""
    original_env = getattr(settings, "ENV", "")
    env_lower = str(original_env).lower()
    env_overridden = False
    if env_lower in {"dev", "prod"}:
        settings.ENV = "worker"
        env_overridden = True
    return {"original": original_env, "overridden": env_overridden}


def _restore_env(env_state: dict[str, Any]) -> None:
    """Restore ENV if it was overridden for worker execution."""
    if env_state.get("overridden"):
        settings.ENV = env_state.get("original", "")


def _build_retry_plan() -> dict[str, Any]:
    """Compute retry attempts, delays, and test-env flag."""
    max_attempts = max(int(getattr(settings, "TRANSCODE_RETRY_MAX", 6)), 1)
    delays = list(getattr(settings, "TRANSCODE_RETRY_DELAYS", [1, 2, 4, 8, 16, 32]))
    if not delays:
        delays = [0]
    while len(delays) < max_attempts - 1:
        delays.append(delays[-1])

    is_test_env = getattr(settings, "ENV", "").lower() == "test" or getattr(
        settings, "USE_SQLITE_FOR_TESTS", False
    )
    return {"max_attempts": max_attempts, "delays": delays, "is_test_env": is_test_env}


def _run_transcode_with_retries(
    video_id: int,
    resolutions: Iterable[str],
    *,
    force: bool,
    max_attempts: int,
    delays: list[int],
    is_test_env: bool,
) -> None:
    """Attempt the transcode with retry semantics preserved from the original implementation."""
    for attempt in range(1, max_attempts + 1):
        try:
            _invoke_transcode(video_id, resolutions, force=force)
            break
        except TranscodeError as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in {400, 403, 404, 409}:
                raise
            if attempt >= max_attempts:
                raise
            _sleep_and_log_retry(
                video_id, attempt, max_attempts, delays, is_test_env, exc
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            if attempt >= max_attempts:
                raise
            _sleep_and_log_retry(
                video_id, attempt, max_attempts, delays, is_test_env, exc
            )


def _invoke_transcode(
    video_id: int, resolutions: Iterable[str], *, force: bool
) -> None:
    """Call run_transcode_job or enqueue_transcode preserving compat behaviour."""
    run_callable = getattr(services, "run_transcode_job", None)
    if run_callable:
        compat_runner = getattr(services, "invoke_run_transcode_job", None)
        if callable(compat_runner):
            compat_runner(video_id, resolutions, force=bool(force))
        else:
            _safe_run_transcode(
                run_callable,
                video_id,
                resolutions,
                force=bool(force),
            )
    else:  # Backward-compat fallback without force support.
        services.enqueue_transcode(video_id, target_resolutions=resolutions)


def _sleep_and_log_retry(
    video_id: int,
    attempt: int,
    max_attempts: int,
    delays: list[int],
    is_test_env: bool,
    exc: Exception,
) -> None:
    """Log retry information and sleep when required."""
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


def run_thumbnail_job_task(video_id: int) -> dict[str, Any]:
    """
    Thin wrapper so thumbnail generation can be queued later on.
    """
    return services.run_thumbnail_job(video_id)
