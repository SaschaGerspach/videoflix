"""Autotranscode helpers for computing and scheduling renditions."""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Mapping, Sequence

from django.conf import settings
from django.core.cache import cache

from jobs.domain import services as transcode_services
from videos.domain.models import Video
from videos.domain.services import ensure_source_metadata, extract_video_metadata
from videos.domain.utils import ensure_hls_dir, is_stub_manifest

logger = logging.getLogger("videoflix")

AUTOTRANSCODE_CACHE_TTL_SECONDS = 10
_RELAXED_RUNG_ORDER = ("1080p", "720p", "480p")
_RELAXED_RUNG_SET = set(_RELAXED_RUNG_ORDER)


def _cache_key(video_id: int) -> str:
    return f"autotranscode:{video_id}"


def schedule_default_transcodes(video_id: int, *, force: bool = False) -> None:
    """Trigger the default renditions for a video while guarding against repeated
    scheduling within a short timeframe.
    """
    source_path = transcode_services.get_video_source_path(video_id)
    if not source_path.exists():
        logger.info("autotranscode: skip (no source): video_id=%s", video_id)
        return

    if not _acquire_debounce(video_id, force=force):
        logger.info("autotranscode: skip (debounced): video_id=%s", video_id)
        return

    try:
        queued, result = enqueue_dynamic_renditions(video_id, force=force)
    except Exception as exc:  # pragma: no cover - defensive safeguard
        cache.delete(_cache_key(video_id))
        logger.warning(
            "autotranscode: failed to schedule: video_id=%s, error=%s",
            video_id,
            exc,
        )
        return

    if not queued:
        cache.delete(_cache_key(video_id))
        logger.info("autotranscode: skip (no renditions needed): video_id=%s", video_id)
        return

    mode = "inline"
    if isinstance(result, dict) and result.get("queue"):
        mode = "rq"

    logger.info(
        "autotranscode: scheduled: video_id=%s, renditions=%s, mode=%s",
        video_id,
        tuple(queued),
        mode,
    )


def _acquire_debounce(video_id: int, *, force: bool) -> bool:
    """Use the configured Django cache to prevent repeated scheduling within a small window.
    Returns True when the caller may proceed.
    """
    cache_key = _cache_key(video_id)
    if force:
        cache.set(cache_key, True, timeout=AUTOTRANSCODE_CACHE_TTL_SECONDS)
        return True
    return cache.add(cache_key, True, timeout=AUTOTRANSCODE_CACHE_TTL_SECONDS)


def enqueue_dynamic_renditions(
    video_id: int, *, force: bool = False
) -> tuple[list[str], dict | None]:
    """Ensure metadata exists for the source and enqueue the necessary renditions.
    Returns the list of scheduled resolutions (in descending order) and the enqueue result.
    """
    try:
        video = Video.objects.get(pk=video_id)
    except Video.DoesNotExist:
        return [], None

    ensure_source_metadata(video)
    meta = extract_video_metadata(video)
    targets = select_rungs_from_source(meta)
    missing_targets = _filter_missing_renditions(video_id, targets)
    if not missing_targets:
        return [], None

    result = transcode_services.enqueue_transcode(
        video_id,
        target_resolutions=missing_targets,
        force=force,
    )
    return missing_targets, result


def select_rungs_from_source(meta: Mapping[str, int | None] | None) -> list[str]:
    """Select the optimal rendition list based on detected metadata."""
    policy = str(getattr(settings, "AUTOTRANSCODE_POLICY", "relaxed")).lower()
    if policy == "force_1080":
        return _force_1080_selection()
    if policy == "relaxed":
        return _relaxed_rung_selection()
    return _strict_rung_selection(meta)


def _relaxed_rung_selection() -> list[str]:
    allowed = getattr(settings, "ALLOWED_RENDITIONS", ())
    allowed_subset = {r for r in allowed if r in _RELAXED_RUNG_SET}
    return [r for r in _RELAXED_RUNG_ORDER if r in allowed_subset]


def _force_1080_selection() -> list[str]:
    """Relaxed baseline that always requests 1080p when allowed."""
    selections = _relaxed_rung_selection()
    allowed = getattr(settings, "ALLOWED_RENDITIONS", ())
    if "1080p" in allowed and "1080p" not in selections:
        selections.insert(0, "1080p")
    return selections


def _strict_rung_selection(meta: Mapping[str, int | None] | None) -> list[str]:
    if meta is None or not isinstance(meta, Mapping):
        meta = {}
    height = _safe_positive_int(meta.get("height"))
    bitrate_bps = _resolve_total_bitrate_bps(meta)

    thresholds = _current_thresholds()
    selections = ["480p"]

    has_720 = (
        height >= thresholds["min_720_height"]
        or bitrate_bps >= thresholds["min_720_bitrate"]
    )
    if has_720:
        selections.insert(0, "720p")

    has_1080 = (
        height >= thresholds["min_1080_height"]
        and bitrate_bps >= thresholds["min_1080_bitrate"]
    )
    if has_1080:
        selections.insert(0, "1080p")

    return selections


def _filter_missing_renditions(video_id: int, targets: Sequence[str]) -> list[str]:
    missing: list[str] = []
    for resolution in targets:
        manifest_path = transcode_services.manifest_path_for(video_id, resolution)
        if not manifest_path.exists():
            missing.append(resolution)
            continue
        try:
            stub = is_stub_manifest(manifest_path)
        except Exception:
            stub = False
        if stub:
            missing.append(resolution)
    return missing


def publish_and_enqueue(video: Video) -> list[str]:
    """Ensure metadata exists, prepare directories, and enqueue the required renditions."""
    ensure_source_metadata(video)
    meta = extract_video_metadata(video)
    targets = select_rungs_from_source(meta)
    for resolution in targets:
        ensure_hls_dir(video.pk, resolution)
    missing = _filter_missing_renditions(video.pk, targets)
    if not missing:
        return []
    transcode_services.enqueue_transcode(video.pk, target_resolutions=missing)
    return missing


def _current_thresholds() -> dict[str, int]:
    return {
        "min_720_height": getattr(settings, "TRANSCODE_ENABLE_720_MIN_SRC_HEIGHT", 700),
        "min_720_bitrate": getattr(
            settings, "TRANSCODE_ENABLE_720_MIN_SRC_BITRATE", 2_500_000
        ),
        "min_1080_height": getattr(
            settings, "TRANSCODE_ENABLE_1080_MIN_SRC_HEIGHT", 1000
        ),
        "min_1080_bitrate": getattr(
            settings, "TRANSCODE_ENABLE_1080_MIN_SRC_BITRATE", 4_500_000
        ),
    }


def _safe_positive_int(value) -> int:
    if value in (None, "", False):
        return 0
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    return number


def _resolve_total_bitrate_bps(meta: Mapping[str, Any]) -> int:
    direct_total = _safe_positive_int(meta.get("bitrate_total"))
    if direct_total:
        return direct_total

    total_bps = _safe_positive_int(meta.get("bitrate_total_bps"))
    if total_bps:
        return total_bps

    total_kbps = _safe_positive_int(meta.get("bitrate_total_kbps"))
    if total_kbps:
        return total_kbps * 1000

    video_bps = _safe_positive_int(meta.get("video_bitrate"))
    audio_bps = _safe_positive_int(meta.get("audio_bitrate"))
    if video_bps or audio_bps:
        return video_bps + audio_bps

    video_kbps = _safe_positive_int(meta.get("video_bitrate_kbps"))
    audio_kbps = _safe_positive_int(meta.get("audio_bitrate_kbps"))
    if video_kbps or audio_kbps:
        return (video_kbps + audio_kbps) * 1000

    return 0
