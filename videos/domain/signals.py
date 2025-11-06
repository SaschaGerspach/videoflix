from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from videos.domain.models import Video
from videos.domain.services_autotranscode import schedule_default_transcodes

logger = logging.getLogger("videoflix")


@receiver(post_save, sender=Video)
def schedule_default_renditions(
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
    try:
        schedule_default_transcodes(video_id)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "autotranscode: signal failed: video_id=%s, error=%s",
            video_id,
            exc,
        )
