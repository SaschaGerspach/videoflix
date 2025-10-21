from django.db.models import QuerySet

from .models import Video, VideoSegment, VideoStream


def list_videos() -> QuerySet[Video]:
    """Return all videos ordered by creation time (newest first)."""
    return Video.objects.order_by("-created_at", "-id")


def get_video_stream(*, movie_id: int, resolution: str) -> VideoStream:
    """Return a single video stream manifest for the given video and resolution."""
    return VideoStream.objects.select_related("video").get(
        video_id=movie_id,
        resolution=resolution,
    )


def get_video_segment(*, movie_id: int, resolution: str, segment: str) -> VideoSegment:
    """Return video segment binary content for the given video stream."""
    return VideoSegment.objects.select_related("stream", "stream__video").get(
        stream__video_id=movie_id,
        stream__resolution=resolution,
        name=segment,
    )
