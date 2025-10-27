"""Utilities to build optional HLS master playlists."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from jobs.domain.services import ALLOWED_TRANSCODE_PROFILES, TRANSCODE_PROFILE_CONFIG

logger = logging.getLogger("videoflix")

HLS_BASE = Path(getattr(settings, "MEDIA_ROOT")) / "hls"


def hls_dir(video_id: int) -> Path:
    return HLS_BASE / str(video_id)


def rendition_dir(video_id: int, resolution: str) -> Path:
    return hls_dir(video_id) / resolution


def resolution_to_dims(resolution: str) -> str:
    dims = ALLOWED_TRANSCODE_PROFILES.get(resolution)
    if not dims:
        return "0x0"
    width, height = dims
    return f"{width}x{height}"


def write_master_playlist(video_id: int) -> None:
    """Generate master playlist referencing existing renditions.

    If no rendition manifests exist, no file is written."""

    base = hls_dir(video_id)
    if not base.exists():
        return

    candidates = sorted(
        (
            (resolution, profile.bandwidth)
            for resolution, profile in TRANSCODE_PROFILE_CONFIG.items()
            if profile.bandwidth
        ),
        key=lambda item: item[1],
        reverse=True,
    )

    entries: list[tuple[str, int, str]] = []
    for resolution, bandwidth in candidates:
        manifest_path = rendition_dir(video_id, resolution) / "index.m3u8"
        if manifest_path.exists():
            entries.append((resolution, bandwidth, f"{resolution}/index.m3u8"))

    if not entries:
        return

    lines = ["#EXTM3U"]
    for resolution, bandwidth, uri in entries:
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution_to_dims(resolution)}"
        )
        lines.append(uri)

    master_path = base / "index.m3u8"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    master_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Master playlist written: video_id=%s, path=%s", video_id, master_path)
