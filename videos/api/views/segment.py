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

_ALLOWED_RENDITIONS = tuple(
    getattr(
        settings,
        "ALLOWED_RENDITIONS",
        getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p")),
    )
)


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
        if settings.DEBUG:
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
        # Normalise public id before auth/selector logic: if it is not an int we bail out early.
        try:
            movie_id = int(movie_id)
        except (TypeError, ValueError):
            return self._json_response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status.HTTP_404_NOT_FOUND,
            )
        try:
            real_id = resolve_public_id(movie_id)
        except Video.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        resolution = (resolution or "").strip().lower()
        segment = (segment or "").strip()

        serializer = VideoSegmentContentRequestSerializer(
            data={
                "movie_id": real_id,
                "resolution": resolution,
                "segment": segment,
            }
        )
        if not serializer.is_valid():
            return self._json_response({"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST)

        try:
            video = Video.objects.get(pk=real_id)
        except Video.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        if not _user_can_access(request, video):
            return self._json_response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        resolution_value = serializer.validated_data["resolution"]
        requested_name = serializer.validated_data["segment"]
        base_dir = Path(settings.MEDIA_ROOT) / "hls" / str(real_id) / resolution_value

        candidates: list[str] = [requested_name]
        base, dot, ext = requested_name.rpartition(".")
        padded_name: str | None = None
        if dot and ext.lower() == "ts" and base.isdigit():
            width = max(len(base), 3)
            padded_name = f"{int(base):0{width}d}.ts"
            if padded_name not in candidates:
                candidates.append(padded_name)

        segment_path = None
        fs_hit = False
        for candidate in candidates:
            candidate_path = base_dir / candidate
            if candidate_path.exists():
                segment_path = candidate_path
                fs_hit = True
                break

        if segment_path is None:
            if _ALLOWED_RENDITIONS and resolution_value not in _ALLOWED_RENDITIONS:
                resp = self._json_response(
                    {"errors": {"non_field_errors": ["Video segment not found."]}},
                    status.HTTP_404_NOT_FOUND,
                )
                return _debug_not_found(resp, "resolution-not-allowed")

            fallback_names = set(candidates)
            try:
                stream = VideoStream.objects.select_related("video").get(
                    video_id=real_id,
                    resolution=resolution_value,
                )
            except VideoStream.DoesNotExist:
                resp = self._json_response(
                    {"errors": {"non_field_errors": ["Video segment not found."]}},
                    status.HTTP_404_NOT_FOUND,
                )
                return _debug_not_found(resp, "segment-missing-fs-and-db")

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
            else:
                resp = self._json_response(
                    {"errors": {"non_field_errors": ["Video segment not found."]}},
                    status.HTTP_404_NOT_FOUND,
                )
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
