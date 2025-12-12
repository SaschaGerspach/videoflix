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
    allow_overwrite: bool = False,
) -> Path | None:
    """Use ffmpeg to materialise a thumbnail for the given video."""
    context = _resolve_thumbnail_context(
        video_id, timestamp=timestamp, width=width, height=height, size=size
    )
    if context is None:
        return None

    output_path = context["output_path"]
    if output_path.exists() and not allow_overwrite:
        logger.info(
            "Thumbnail skipped (exists): video_id=%s, path=%s", video_id, output_path
        )
        return output_path

    cmd = _build_thumbnail_command(context)
    if not _run_thumbnail_command(cmd, video_id):
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

    media_base = _public_media_base()
    if media_base:
        return f"{media_base}{normalized}"

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


def _public_media_base() -> str:
    raw = getattr(settings, "PUBLIC_MEDIA_BASE", "") or ""
    cleaned = _extract_base_value(raw)
    if not cleaned:
        return ""
    candidate = cleaned
    if candidate.startswith("//"):
        candidate = f"http:{candidate}"
    parts = urlsplit(str(candidate))
    if parts.scheme and parts.netloc:
        path = parts.path.rstrip("/")
        return f"{parts.scheme}://{parts.netloc}{path}"
    return candidate.rstrip("/")


def _frontend_origin() -> str:
    raw = _extract_base_value(getattr(settings, "FRONTEND_BASE_URL", "") or "")
    if not raw:
        return ""
    parts = urlsplit(str(raw))
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return raw.rstrip("/")


def _extract_base_value(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""

    for marker in ("http://", "https://", "//"):
        idx = raw.find(marker)
        if idx >= 0:
            return raw[idx:].strip()

    return raw


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


def _resolve_thumbnail_context(
    video_id: int,
    *,
    timestamp: str | None,
    width: int | None,
    height: int | None,
    size: str,
) -> dict[str, Any] | None:
    """Build the thumbnail generation context or return None when source is missing."""
    source_path = job_services.get_video_source_path(video_id)
    if not source_path.exists():
        logger.info("Thumbnail skipped (source missing): video_id=%s", video_id)
        return None

    resolved_timestamp = timestamp or getattr(settings, "THUMB_TIMESTAMP", "00:00:03")
    resolved_width = width or getattr(settings, "THUMB_WIDTH", 320)
    resolved_height = height or getattr(settings, "THUMB_HEIGHT", 180)
    output_path = get_thumbnail_path(video_id, size=size)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    return {
        "source_path": source_path,
        "timestamp": resolved_timestamp,
        "width": resolved_width,
        "height": resolved_height,
        "output_path": output_path,
        "size": size,
    }


def _build_thumbnail_command(context: dict[str, Any]) -> list[str]:
    """Construct the ffmpeg command for thumbnail generation."""
    filter_expr = (
        f"scale='if(gt(a,16/9),-2,{context['width']})':'if(gt(a,16/9),{context['height']},-2)',"
        f"crop={context['width']}:{context['height']}"
    )
    return [
        "ffmpeg",
        "-y",
        "-ss",
        str(context["timestamp"]),
        "-i",
        str(context["source_path"]),
        "-vframes",
        "1",
        "-vf",
        filter_expr,
        str(context["output_path"]),
    ]


def _run_thumbnail_command(cmd: list[str], video_id: int) -> bool:
    """Execute the ffmpeg command, logging failures and returning success flag."""
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        logger.warning("Thumbnail failed (ffmpeg missing): video_id=%s", video_id)
        return False
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Thumbnail failed (process error): video_id=%s, returncode=%s",
            video_id,
            getattr(exc, "returncode", "?"),
        )
        return False
    return True
