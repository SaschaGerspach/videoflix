"""Serve HLS segment content for authenticated clients."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from videos.api.serializers import VideoSegmentContentRequestSerializer
from videos.domain.models import Video, VideoSegment, VideoStream
from videos.domain.selectors import resolve_public_id
from videos.domain.services_index import index_existing_rendition

from .common import ERROR_RESPONSE_REF
from .media_base import (
    MediaSegmentBaseView,
    TSRenderer,
    _debug_not_found,
    _set_cache_headers,
    _user_can_access,
)

logger = logging.getLogger(__name__)


def _get_allowed_renditions() -> tuple[str, ...]:
    allowed = getattr(settings, "ALLOWED_RENDITIONS", None)
    if allowed is None:
        allowed = getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p"))
    if not allowed:
        return ()
    return tuple(allowed)


class VideoSegmentContentView(MediaSegmentBaseView):
    """Return a binary HLS segment for the requested video stream."""

    renderer_classes = [JSONRenderer, TSRenderer]
    media_renderer_class = TSRenderer
    allowed_accept_types = ("*/*", TSRenderer.media_type.lower(), TSRenderer.media_type)
    not_acceptable_message = "Requested media type not acceptable."
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="video_segment_content",
        request=None,
        tags=["Videos"],
        responses={
            (200, TSRenderer.media_type): OpenApiResponse(
                response=OpenApiTypes.BYTE,
                description="MPEG-TS Segment",
            ),
            400: ERROR_RESPONSE_REF,
            403: ERROR_RESPONSE_REF,
            404: ERROR_RESPONSE_REF,
        },
        auth=[{"cookieJwtAuth": []}],
        examples=[
            OpenApiExample(
                "VideoSegmentNotFound",
                value={"errors": {"non_field_errors": ["Video segment not found."]}},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    def get(self, request, movie_id: int, resolution: str, segment: str):
        """Return the segment file from disk or DB while preserving cache headers."""
        self._log_debug_request(request)
        real_id = self._resolve_real_id(movie_id)
        if real_id is None:
            return self._not_found_json()

        validation = self._validate_request(real_id, resolution, segment)
        if not isinstance(validation, dict):
            return validation

        resolution_value = validation["resolution"]
        requested_name = validation["segment"]

        video = self._get_video_or_404(real_id)
        if video is None:
            return self._not_found_json()

        if not _user_can_access(request, video):
            return self._not_found_json()

        base_dir = self._segment_base_dir(real_id, resolution_value)
        candidates, padded_name = self._segment_candidates(requested_name)
        segment_path, fs_hit = self._find_filesystem_segment(base_dir, candidates)

        if segment_path is None:
            allowed_renditions = _get_allowed_renditions()
            if allowed_renditions and resolution_value not in allowed_renditions:
                resp = self._not_found_json()
                return _debug_not_found(resp, "resolution-not-allowed")

            stream = self._get_stream(real_id, resolution_value)
            if stream is None:
                resp = self._not_found_json()
                return _debug_not_found(resp, "segment-missing-fs-and-db")

            segment_path = self._restore_segment_from_db(
                base_dir, stream, candidates, padded_name, requested_name
            )
            if segment_path is None:
                resp = self._not_found_json()
                return _debug_not_found(resp, "segment-missing-fs-and-db")

        self._ensure_accept_header(request, TSRenderer.media_type)
        response = FileResponse(segment_path.open("rb"))
        response["Content-Type"] = TSRenderer.media_type
        _set_cache_headers(response, segment_path)
        if fs_hit:
            try:
                index_existing_rendition(real_id, resolution_value)
            except Exception:  # pragma: no cover - defensive logging only
                logger.exception(
                    "Self-heal indexing failed for segment video_id=%s resolution=%s segment=%s",
                    real_id,
                    resolution_value,
                    requested_name,
                )
        return response

    def _log_debug_request(self, request) -> None:
        """Log request details when DEBUG is enabled for troubleshooting."""
        if not settings.DEBUG:
            return
        access_cookie_name = getattr(settings, "ACCESS_COOKIE_NAME", "access_token")
        cookies = getattr(request, "COOKIES", {}) or {}
        logger.debug(
            "VideoSegmentContentView.get path=%s trailing_slash=%s authenticated=%s cookie_present=%s raw_cookie=%s accept=%s",
            request.get_full_path(),
            request.path.endswith("/"),
            bool(getattr(request, "user", None) and request.user.is_authenticated),
            bool(cookies.get(access_cookie_name)),
            bool(request.META.get("HTTP_COOKIE")),
            request.META.get("HTTP_ACCEPT"),
        )

    def _resolve_real_id(self, movie_id: int | str) -> int | None:
        """Resolve a public movie id to the real database id."""
        try:
            movie_id = int(movie_id)
        except (TypeError, ValueError):
            return None
        try:
            return resolve_public_id(movie_id)
        except Video.DoesNotExist:
            return None

    def _validate_request(self, real_id: int, resolution: str, segment: str):
        """Validate incoming parameters; return validated data or an early response."""
        resolution_value = (resolution or "").strip().lower()
        segment_value = (segment or "").strip()
        serializer = VideoSegmentContentRequestSerializer(
            data={
                "movie_id": real_id,
                "resolution": resolution_value,
                "segment": segment_value,
            }
        )
        if not serializer.is_valid():
            return self._json_response(
                {"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST
            )
        return serializer.validated_data

    def _get_video_or_404(self, real_id: int) -> Video | None:
        """Fetch the video or return None to trigger a 404 response."""
        try:
            return Video.objects.get(pk=real_id)
        except Video.DoesNotExist:
            return None

    def _segment_base_dir(self, real_id: int, resolution_value: str) -> Path:
        """Return the base directory for the video segment files."""
        return Path(settings.MEDIA_ROOT) / "hls" / str(real_id) / resolution_value

    def _segment_candidates(self, requested_name: str) -> tuple[list[str], str | None]:
        """Return possible filenames for the requested segment (padded and original)."""
        candidates: list[str] = [requested_name]
        base, dot, ext = requested_name.rpartition(".")
        padded_name: str | None = None
        if dot and ext.lower() == "ts" and base.isdigit():
            width = max(len(base), 3)
            padded_name = f"{int(base):0{width}d}.ts"
            if padded_name not in candidates:
                candidates.append(padded_name)
        return candidates, padded_name

    def _find_filesystem_segment(
        self, base_dir: Path, candidates: list[str]
    ) -> tuple[Path | None, bool]:
        """Locate an existing segment on disk; return path and whether it was found on FS."""
        for candidate in candidates:
            candidate_path = base_dir / candidate
            if candidate_path.exists():
                return candidate_path, True
        return None, False

    def _get_stream(self, real_id: int, resolution_value: str) -> VideoStream | None:
        """Return the stream for the given video/resolution or None."""
        try:
            return VideoStream.objects.select_related("video").get(
                video_id=real_id,
                resolution=resolution_value,
            )
        except VideoStream.DoesNotExist:
            return None

    def _restore_segment_from_db(
        self,
        base_dir: Path,
        stream: VideoStream,
        candidates: list[str],
        padded_name: str | None,
        requested_name: str,
    ) -> Path | None:
        """Write the segment content from DB to disk if present, returning its path."""
        fallback_names = set(candidates)
        video_segment = (
            VideoSegment.objects.select_related("stream", "stream__video")
            .filter(stream=stream, name__in=fallback_names)
            .first()
        )
        if video_segment and video_segment.content is not None:
            target_name = video_segment.name or (padded_name or requested_name)
            segment_path = base_dir / target_name
            segment_path.parent.mkdir(parents=True, exist_ok=True)
            segment_path.write_bytes(bytes(video_segment.content))
            return segment_path
        return None

    def _not_found_json(self):
        """Return the standard segment not found JSON response."""
        return self._json_response(
            {"errors": {"non_field_errors": ["Video segment not found."]}},
            status.HTTP_404_NOT_FOUND,
        )
