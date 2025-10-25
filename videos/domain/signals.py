from __future__ import annotations

import logging
from typing import Iterable

from django.conf import settings

from django.db.models.signals import post_save
from django.dispatch import receiver

from jobs.domain import services as transcode_services
from videos.domain.models import Video

logger = logging.getLogger("videoflix")


def _missing_renditions(video_id: int) -> list[str]:
    available: Iterable[str] = transcode_services.ALLOWED_TRANSCODE_PROFILES.keys()
    return [
        resolution
        for resolution in available
        if not transcode_services.manifest_exists_for_resolution(video_id, resolution)
    ]


@receiver(post_save, sender=Video)
def enqueue_missing_transcodes(
    sender,
    instance: Video,
    created: bool,
    **kwargs,
) -> None:
    if kwargs.get("raw"):
        return
    if not instance.pk:
        return

    video_id = instance.pk
    env = str(getattr(settings, "ENV", "")).lower()
    if env == "test":
        logger.debug("Auto-transcode skipped (ENV=test): video_id=%s", video_id)
        return
    source_path = transcode_services.get_video_source_path(video_id)
    if not source_path.exists():
        logger.debug(
            "Auto-transcode skipped (source missing): video_id=%s, path=%s",
            video_id,
            source_path,
        )
        return

    missing_resolutions = _missing_renditions(video_id)
    if not missing_resolutions:
        logger.debug(
            "Auto-transcode skipped (no missing renditions): video_id=%s",
            video_id,
        )
        return
    if transcode_services.is_transcode_locked(video_id):
        logger.debug(
            "Auto-transcode skipped (lock active): video_id=%s, resolutions=%s",
            video_id,
            missing_resolutions,
        )
        return

    try:
        from jobs.queue import enqueue_transcode_job

        enqueue_transcode_job(video_id, missing_resolutions)
        logger.info(
            "Auto-transcode queued: video_id=%s, resolutions=%s",
            video_id,
            missing_resolutions,
        )
    except Exception as exc:
        logger.warning(
            "Auto-transcode skipped (error): video_id=%s, error=%s",
            video_id,
            exc,
        )
