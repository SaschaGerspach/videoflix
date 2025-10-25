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
        video = Video.objects.get(pk=movie_id)
    except Video.DoesNotExist as exc:  # pragma: no cover - defensive
        raise VideoStream.DoesNotExist from exc

    if not _video_visible_to_user(video, user):
        raise PermissionError(_FORBIDDEN_ERROR)

    output_dir = transcode_services.get_transcode_output_dir(movie_id, resolution)
    manifest_path = output_dir / "index.m3u8"
    if not manifest_path.exists():
        if settings.DEBUG:
            logger.debug(
                "Stream manifest missing: video_id=%s, resolution=%s, path=%s",
                movie_id,
                resolution,
                str(manifest_path.resolve()),
            )
        raise VideoStream.DoesNotExist

    manifest_bytes = manifest_path.read_bytes()
    manifest_content = manifest_bytes.decode("utf-8")
    return StreamResult(video=video, manifest=manifest_content)


def get_video_segment(*, movie_id: int, resolution: str, segment: str, user) -> SegmentResult:
    """Return video segment binary content for the given video stream."""
    try:
        video = Video.objects.get(pk=movie_id)
    except Video.DoesNotExist as exc:  # pragma: no cover - defensive
        raise VideoSegment.DoesNotExist from exc

    if not _video_visible_to_user(video, user):
        raise PermissionError(_FORBIDDEN_ERROR)

    output_dir = transcode_services.get_transcode_output_dir(movie_id, resolution)
    segment_filename = segment if segment.endswith(".ts") else f"{segment}.ts"
    segment_path = output_dir / segment_filename
    if not segment_path.exists():
        if settings.DEBUG:
            logger.debug(
                "Stream segment missing: video_id=%s, resolution=%s, segment=%s, path=%s",
                movie_id,
                resolution,
                segment_filename,
                str(segment_path.resolve()),
            )
        raise VideoSegment.DoesNotExist
    segment_content = segment_path.read_bytes()
    return SegmentResult(content=segment_content)
