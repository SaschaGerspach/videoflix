from __future__ import annotations

from rest_framework import status
from rest_framework.renderers import JSONRenderer

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from videos.api.serializers import VideoSegmentContentRequestSerializer
from videos.domain.models import VideoSegment
from videos.domain.selectors import get_video_segment

from .common import ERROR_RESPONSE_REF
from .media_base import MediaSegmentBaseView, TSRenderer


class VideoSegmentContentView(MediaSegmentBaseView):
    """Return a binary HLS segment for the requested video stream."""

    renderer_classes = [JSONRenderer, TSRenderer]
    media_renderer_class = TSRenderer
    allowed_accept_types = ("*/*", "video/*", TSRenderer.media_type.lower(), TSRenderer.media_type)
    not_acceptable_message = "Requested media type not acceptable."

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
        serializer = VideoSegmentContentRequestSerializer(
            data={
                "movie_id": movie_id,
                "resolution": resolution,
                "segment": segment,
            }
        )
        if not serializer.is_valid():
            return self._json_response({"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST)

        self._ensure_accept_header(request)

        try:
            video_segment = get_video_segment(user=request.user, **serializer.validated_data)
        except PermissionError as exc:
            return self._json_response(
                {
                    "errors": {
                        "non_field_errors": [
                            str(exc) or "You do not have permission to access this video."
                        ]
                    }
                },
                status.HTTP_403_FORBIDDEN,
            )
        except VideoSegment.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        return self._render_media_response(video_segment.content, status.HTTP_200_OK)
