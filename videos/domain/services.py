"""Video metadata helpers that persist probe results back to the model."""

from __future__ import annotations

from typing import Any

from videos.domain.models import Video
from videos.domain.utils import probe_media_info, resolve_source_path


def ensure_source_metadata(video: Video) -> Video:
    """Ensure basic media metadata is stored on the given Video instance."""
    source_path = resolve_source_path(video)
    if not source_path:
        video._source_metadata_cache = {}
        return video

    info = probe_media_info(source_path)
    video._source_metadata_cache = info
    if not info:
        return video

    field_mapping = {
        "width": "width",
        "height": "height",
        "duration_seconds": "duration_seconds",
        "video_bitrate_kbps": "video_bitrate_kbps",
        "audio_bitrate_kbps": "audio_bitrate_kbps",
        "codec_name": "codec_name",
    }
    update_fields: list[str] = []
    for field, key in field_mapping.items():
        value = info.get(key)
        if value is None:
            continue
        if getattr(video, field) != value:
            setattr(video, field, value)
            update_fields.append(field)

    if update_fields:
        video.save(update_fields=update_fields)
    return video


def extract_video_metadata(video: Video) -> dict[str, Any]:
    """Return the metadata dict for a video, preferring the cache populated by
    ensure_source_metadata() and falling back to stored DB fields.
    """
    cached = getattr(video, "_source_metadata_cache", None)
    if isinstance(cached, dict) and cached:
        return cached

    return {
        "width": video.width,
        "height": video.height,
        "duration_seconds": video.duration_seconds,
        "video_bitrate_kbps": video.video_bitrate_kbps,
        "audio_bitrate_kbps": video.audio_bitrate_kbps,
        "codec_name": video.codec_name,
    }
