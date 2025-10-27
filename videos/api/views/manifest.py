from __future__ import annotations

from rest_framework import status
from rest_framework.renderers import JSONRenderer

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from videos.api.serializers import VideoSegmentRequestSerializer
from videos.domain.models import VideoStream
from videos.domain.selectors import get_video_stream

from .common import ERROR_RESPONSE_REF
from .media_base import M3U8Renderer, MediaSegmentBaseView


class VideoSegmentView(MediaSegmentBaseView):
    """Return the HLS master playlist for the requested video stream."""

    renderer_classes = [JSONRenderer, M3U8Renderer]
    media_renderer_class = M3U8Renderer
    allowed_accept_types = ("*/*", "application/*", M3U8Renderer.media_type)

    @extend_schema(
        operation_id="video_manifest",
        request=None,
        tags=["Videos"],
        parameters=[],
        responses={
            (200, M3U8Renderer.media_type): OpenApiResponse(
                response=OpenApiTypes.BYTE,
                description="HLS-Masterplaylist im M3U8-Format",
            ),
            400: ERROR_RESPONSE_REF,
            403: ERROR_RESPONSE_REF,
            404: ERROR_RESPONSE_REF,
        },
        auth=[{"cookieJwtAuth": []}],
        examples=[
            OpenApiExample(
                "VideoManifestNotFound",
                value={"errors": {"non_field_errors": ["Video manifest not found."]}},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    def get(self, request, movie_id: int, resolution: str):
        serializer = VideoSegmentRequestSerializer(
            data={"movie_id": movie_id, "resolution": resolution}
        )
        if not serializer.is_valid():
            return self._json_response({"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST)

        self._ensure_accept_header(request)

        try:
            stream = get_video_stream(user=request.user, **serializer.validated_data)
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
        except VideoStream.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        return self._render_media_response(stream.manifest, status.HTTP_200_OK)
