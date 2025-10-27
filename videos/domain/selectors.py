import logging
from dataclasses import dataclass

from django.conf import settings
from django.db.models import QuerySet

from jobs.domain import services as transcode_services

from .models import Video, VideoSegment, VideoStream

_FORBIDDEN_ERROR = "You do not have permission to access this video."
logger = logging.getLogger("videoflix")


@dataclass(frozen=True)
class StreamResult:
    video: Video
    manifest: str


@dataclass(frozen=True)
class SegmentResult:
    content: bytes


def list_published_videos() -> QuerySet[Video]:
    """Return published videos ordered by creation time (newest first)."""
    return Video.objects.filter(is_published=True).order_by("-created_at", "-id")


def _video_visible_to_user(video: Video, user) -> bool:
    if user is None:
        return False

    user_id = getattr(user, "id", None)
    if video.owner_id is not None and video.owner_id == user_id:
        return True
    if video.is_published:
        return True
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    return False


def get_video_stream(*, movie_id: int, resolution: str, user) -> StreamResult:
    """Return a single video stream manifest for the given video and resolution."""
    try:
        stream = VideoStream.objects.select_related("video").get(
            video_id=movie_id,
            resolution=resolution,
        )
    except VideoStream.DoesNotExist:
        raise

    video = stream.video
    if not _video_visible_to_user(video, user):
        raise PermissionError(_FORBIDDEN_ERROR)

    output_dir = transcode_services.get_transcode_output_dir(movie_id, resolution)
    manifest_path = output_dir / "index.m3u8"
    if manifest_path.exists():
        manifest_content = manifest_path.read_text(encoding="utf-8")
        return StreamResult(video=video, manifest=manifest_content)

    if stream.manifest:
        logger.debug(
            "Stream manifest served from database: video_id=%s, resolution=%s",
            movie_id,
            resolution,
        )
        return StreamResult(video=video, manifest=stream.manifest)

    if settings.DEBUG:
        logger.debug(
            "Stream manifest missing: video_id=%s, resolution=%s, path=%s",
            movie_id,
            resolution,
            str(manifest_path),
        )
    raise VideoStream.DoesNotExist


def get_video_segment(*, movie_id: int, resolution: str, segment: str, user) -> SegmentResult:
    """Return video segment binary content for the given video stream."""
    try:
        video_segment = VideoSegment.objects.select_related("stream", "stream__video").get(
            stream__video_id=movie_id,
            stream__resolution=resolution,
            name=segment,
        )
    except VideoSegment.DoesNotExist:
        raise

    video = video_segment.stream.video
    if not _video_visible_to_user(video, user):
        raise PermissionError(_FORBIDDEN_ERROR)

    output_dir = transcode_services.get_transcode_output_dir(movie_id, resolution)
    segment_filename = segment if segment.endswith(".ts") else f"{segment}.ts"
    segment_path = output_dir / segment_filename
    if segment_path.exists():
        segment_content = segment_path.read_bytes()
        return SegmentResult(content=segment_content)

    if video_segment.content:
        logger.debug(
            "Stream segment served from database: video_id=%s, resolution=%s, segment=%s",
            movie_id,
            resolution,
            segment_filename,
        )
        return SegmentResult(content=bytes(video_segment.content))

    if settings.DEBUG:
        logger.debug(
            "Stream segment missing: video_id=%s, resolution=%s, segment=%s, path=%s",
            movie_id,
            resolution,
            segment_filename,
            str(segment_path),
        )
    raise VideoSegment.DoesNotExist
