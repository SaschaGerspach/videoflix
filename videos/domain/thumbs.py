from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from jobs.domain import services as job_services

logger = logging.getLogger("videoflix")


def get_thumbnail_path(video_id: int, size: str = "default") -> Path:
    """Return the filesystem path for a generated thumbnail."""
    return Path(settings.MEDIA_ROOT) / "thumbs" / str(video_id) / f"{size}.jpg"


def ensure_thumbnail(
    video_id: int,
    *,
    timestamp: str | None = None,
    size: str = "default",
    width: int | None = None,
    height: int | None = None,
) -> Path | None:
    """Use ffmpeg to materialise a thumbnail for the given video."""
    source_path = job_services.get_video_source_path(video_id)
    if not source_path.exists():
        logger.info("Thumbnail skipped (source missing): video_id=%s", video_id)
        return None

    timestamp = timestamp or getattr(settings, "THUMB_TIMESTAMP", "00:00:03")
    width = width or getattr(settings, "THUMB_WIDTH", 320)
    height = height or getattr(settings, "THUMB_HEIGHT", 180)

    output_path = get_thumbnail_path(video_id, size=size)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    filter_expr = (
        f"scale='if(gt(a,16/9),-2,{width})':'if(gt(a,16/9),{height},-2)',"
        f"crop={width}:{height}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(source_path),
        "-vframes",
        "1",
        "-vf",
        filter_expr,
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        logger.warning("Thumbnail failed (ffmpeg missing): video_id=%s", video_id)
        return None
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Thumbnail failed (process error): video_id=%s, returncode=%s",
            video_id,
            getattr(exc, "returncode", "?"),
        )
        return None

    logger.info("Thumbnail generated: video_id=%s, path=%s", video_id, output_path)
    return output_path


def get_thumbnail_url(video, *, request=None, size: str = "default") -> str:
    """Return an absolute URL for the thumbnail when present, otherwise an empty string."""
    video_id = _resolve_video_id(video)
    if video_id is None:
        return ""

    thumb_path = get_thumbnail_path(video_id, size=size)
    if not thumb_path.exists():
        return ""

    relative_url = _thumbnail_relative_url(video_id, size=size)
    return build_media_url(relative_url, request=request)


def build_media_url(path: str | Path | None, *, request=None) -> str:
    """Build an absolute media URL depending on the active environment context."""
    if path is None:
        return ""

    relative = str(path).strip()
    if not relative:
        return ""

    if relative.startswith(("http://", "https://")):
        return relative

    normalized = _ensure_leading_slash(relative)

    if request is not None:
        try:
            return request.build_absolute_uri(normalized)
        except Exception:
            pass

    frontend_origin = _frontend_origin()
    if frontend_origin:
        return f"{frontend_origin}{normalized}"

    if getattr(settings, "DEBUG", False):
        return f"http://127.0.0.1:8000{normalized}"

    return normalized


def _thumbnail_relative_url(video_id: int, size: str = "default") -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/") or ""
    media_root = media_url.rstrip("/")
    if media_root.startswith("http://") or media_root.startswith("https://"):
        return f"{media_root}/thumbs/{video_id}/{size}.jpg"

    relative = f"{media_root}/thumbs/{video_id}/{size}.jpg"
    if not relative.startswith("/"):
        relative = "/" + relative.lstrip("/")
    return relative


def _ensure_leading_slash(value: str) -> str:
    if value.startswith("/"):
        return value
    return "/" + value.lstrip("/")


def _frontend_origin() -> str:
    raw = getattr(settings, "FRONTEND_BASE_URL", "") or ""
    if not raw:
        return ""
    parts = urlsplit(str(raw))
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return raw.rstrip("/")


def _resolve_video_id(video: Any) -> int | None:
    if video is None:
        return None
    if isinstance(video, int):
        return video
    if hasattr(video, "pk"):
        try:
            return int(video.pk)
        except (TypeError, ValueError):
            return None
    if hasattr(video, "id"):
        try:
            return int(video.id)
        except (TypeError, ValueError):
            return None
    try:
        return int(video)
    except (TypeError, ValueError):
        return None
