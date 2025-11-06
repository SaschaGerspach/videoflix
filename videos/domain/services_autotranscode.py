from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence

from django.core.cache import cache

from jobs.domain import services as transcode_services

logger = logging.getLogger("videoflix")

DEFAULT_RENDITIONS: Sequence[str] = ("480p", "720p")
AUTOTRANSCODE_CACHE_TTL_SECONDS = 10


def _cache_key(video_id: int) -> str:
    return f"autotranscode:{video_id}"


def schedule_default_transcodes(video_id: int, *, force: bool = False) -> None:
    """
    Trigger the default renditions for a video while guarding against repeated
    scheduling within a short timeframe.
    """
    source_path = transcode_services.get_video_source_path(video_id)
    if not source_path.exists():
        logger.info("autotranscode: skip (no source): video_id=%s", video_id)
        return

    if not _acquire_debounce(video_id, force=force):
        logger.info("autotranscode: skip (debounced): video_id=%s", video_id)
        return

    targets, detected_height = _target_profiles_for_source(source_path)
    if detected_height is not None:
        logger.info(
            "autotranscode: source height detected: video_id=%s, height=%s, targets=%s",
            video_id,
            detected_height,
            tuple(targets),
        )
    if not targets:
        cache.delete(_cache_key(video_id))
        logger.info(
            "autotranscode: skip (no allowed renditions): video_id=%s",
            video_id,
        )
        return

    try:
        result = transcode_services.enqueue_transcode(
            video_id,
            target_resolutions=targets,
            force=force,
        )
    except Exception as exc:  # pragma: no cover - defensive safeguard
        cache.delete(_cache_key(video_id))
        logger.warning(
            "autotranscode: failed to schedule: video_id=%s, error=%s",
            video_id,
            exc,
        )
        return

    mode = "inline"
    if isinstance(result, dict) and result.get("queue"):
        mode = "rq"

    logger.info(
        "autotranscode: scheduled: video_id=%s, renditions=%s, mode=%s",
        video_id,
        tuple(targets),
        mode,
    )


def _acquire_debounce(video_id: int, *, force: bool) -> bool:
    """
    Use the configured Django cache to prevent repeated scheduling within a small window.
    Returns True when the caller may proceed.
    """
    cache_key = _cache_key(video_id)
    if force:
        cache.set(cache_key, True, timeout=AUTOTRANSCODE_CACHE_TTL_SECONDS)
        return True
    return cache.add(cache_key, True, timeout=AUTOTRANSCODE_CACHE_TTL_SECONDS)


def _merge_default_profiles(resolutions: Sequence[str]) -> Iterable[str]:
    """
    Ensure we only request profiles that are registered as allowed.
    """
    allowed = transcode_services.ALLOWED_TRANSCODE_PROFILES
    return [res for res in resolutions if res in allowed]


def _target_profiles_for_source(source_path: Path) -> tuple[list[str], int | None]:
    """
    Dynamically determine the renditions that should be generated for the given source.
    """
    height = transcode_services.probe_source_height(source_path)
    if height is None:
        base_profiles = list(DEFAULT_RENDITIONS)
    else:
        base_profiles = ["480p"]
        if height >= 720:
            base_profiles.append("720p")
        if height >= 1080:
            base_profiles.append("1080p")
    merged = list(_merge_default_profiles(base_profiles))
    order = list(transcode_services.ALLOWED_TRANSCODE_PROFILES.keys())
    merged.sort(key=lambda res: order.index(res) if res in order else len(order))
    return merged, height
