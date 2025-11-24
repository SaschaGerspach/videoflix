"""Signal handlers for publish events and automatic transcodes."""

from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from videos.domain.models import Video
from jobs.domain import services as transcode_services
from videos.domain.services_autotranscode import (
    publish_and_enqueue,
    schedule_default_transcodes,
)
from videos.domain.utils import has_hls_ready

logger = logging.getLogger("videoflix")


@receiver(pre_save, sender=Video)
def _remember_previous_publish(sender, instance: Video, **kwargs):
    """Store whether a video was previously published so post-save can detect transitions."""
    if instance._state.adding:
        instance._was_published = False
        return
    try:
        original = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        instance._was_published = False
    else:
        instance._was_published = original.is_published


@receiver(post_save, sender=Video)
def schedule_default_renditions(
    sender,
    instance: Video,
    created: bool,
    **kwargs,
) -> None:
    """Kick off default renditions or enqueue thumbnails when publication changes."""
    if kwargs.get("raw"):
        return
    if not instance.pk:
        return

    video_id = instance.pk
    publish_triggered = instance.is_published and (
        created or not getattr(instance, "_was_published", False)
    )
    if publish_triggered:
        try:
            scheduled = _handle_publish_flow(instance)
            logger.info(
                "publish: video_id=%s scheduled=%s",
                video_id,
                scheduled or "none",
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("publish: failed for video_id=%s error=%s", video_id, exc)
        return

    try:
        schedule_default_transcodes(video_id)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "autotranscode: signal failed: video_id=%s, error=%s",
            video_id,
            exc,
        )


def _handle_publish_flow(video: Video) -> list[str]:
    """Run publish-specific side effects (transcodes + readiness probes)."""
    source_path = transcode_services.get_video_source_path(video.pk)
    if not source_path.exists():
        logger.info("publish: skip (no source): video_id=%s", video.pk)
        return []
    scheduled = publish_and_enqueue(video)
    has_hls_ready(video.pk)
    return scheduled
