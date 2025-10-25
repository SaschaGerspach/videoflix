import hashlib
import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.exceptions import (NotAcceptable, NotAuthenticated,
                                       ParseError)
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import (BaseRenderer, JSONRenderer,
                                      StaticHTMLRenderer)
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from jobs.domain.services import (ALLOWED_TRANSCODE_PROFILES, TranscodeError,
                                  is_transcode_locked)
from jobs.queue import enqueue_transcode_job
from videos.api.serializers import (VideoListRequestSerializer,
                                    VideoSegmentContentRequestSerializer,
                                    VideoSegmentRequestSerializer,
                                    VideoSerializer,
                                    VideoTranscodeRequestSerializer)
from videos.domain.models import Video, VideoSegment, VideoStream
from videos.domain.selectors import (get_video_segment, get_video_stream,
                                     list_published_videos)
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema


ERROR_RESPONSE_REF = {"$ref": "#/components/schemas/ErrorResponse"}
logger = logging.getLogger("videoflix")


def _format_validation_error(error: ValidationError) -> dict[str, list[str]]:
    if hasattr(error, "message_dict") and error.message_dict:
        return {
            key: [str(message) for message in messages]
            for key, messages in error.message_dict.items()
        }
    if hasattr(error, "messages") and error.messages:
        return {"non_field_errors": [str(message) for message in error.messages]}
    return {"non_field_errors": [str(error)]}


class M3U8Renderer(StaticHTMLRenderer):
    media_type = "application/vnd.apple.mpegurl"
    format = "m3u8"


class TSRenderer(BaseRenderer):
    media_type = "video/MP2T"
    format = "ts"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


@extend_schema(
    tags=["Videos"],
    responses={
        200: VideoSerializer(many=True),
        400: ERROR_RESPONSE_REF,
        401: ERROR_RESPONSE_REF,
    },
    auth=[{"cookieJwtAuth": []}],
)
class VideoListView(APIView):
    """Return all available videos for authenticated users."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            data = request.data
        except ParseError as exc:
            detail = getattr(exc, "detail", str(exc))
            message = f"Invalid JSON: {detail}"
            return Response(
                {"errors": {"non_field_errors": [str(message)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = VideoListRequestSerializer(data=data)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        videos = list_published_videos()
        payload = VideoSerializer(videos, many=True).data
        return Response(payload, status=status.HTTP_200_OK)


class MediaSegmentBaseView(APIView):
    renderer_classes = [JSONRenderer]
    http_method_names = ["get", "head", "options"]
    media_renderer_class: type[BaseRenderer]
    allowed_accept_types: tuple[str, ...] = ("*/*",)
    not_acceptable_message = "Requested media type not acceptable."

    def _ensure_accept_header(self, request) -> None:
        accept_header = request.META.get("HTTP_ACCEPT", "")
        if self._accept_allows(accept_header):
            return
        raise NotAcceptable(self.not_acceptable_message)

    def _accept_allows(self, accept_header: str) -> bool:
        if not accept_header:
            return True
        allowed = {item.lower() for item in self.allowed_accept_types}
        for part in accept_header.split(","):
            media_type = part.strip().split(";")[0].lower()
            if media_type in allowed:
                return True
        return False

    def _render_media_response(self, payload, status_code: int) -> Response:
        renderer = self.media_renderer_class()
        response = Response(payload, status=status_code)
        response.accepted_renderer = renderer
        response.accepted_media_type = renderer.media_type
        response.renderer_context = {}
        payload_bytes = payload if isinstance(
            payload, (bytes, bytearray)) else str(payload).encode("utf-8")
        response["Cache-Control"] = "public, max-age=60"
        response["ETag"] = f'"{hashlib.md5(payload_bytes).hexdigest()}"'
        response.render()
        return response

    def _json_response(self, payload, status_code: int) -> Response:
        renderer = JSONRenderer()
        response = Response(payload, status=status_code)
        response.accepted_renderer = renderer
        response.accepted_media_type = renderer.media_type
        response.renderer_context = {}
        response["Content-Type"] = renderer.media_type
        response.render()
        return response

    def head(self, request, *args, **kwargs):
        response = self.get(request, *args, **kwargs)
        response.content = b""
        response["Content-Length"] = "0"
        return response

    def http_method_not_allowed(self, request, *args, **kwargs):
        return self._json_response(
            {"errors": {"non_field_errors": ["Method not allowed."]}},
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def handle_exception(self, exc):
        if isinstance(exc, NotAuthenticated):
            return self._json_response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status.HTTP_401_UNAUTHORIZED,
            )
        if isinstance(exc, NotAcceptable):
            return self._json_response(
                {"errors": {"non_field_errors": [
                    self.not_acceptable_message]}},
                status.HTTP_406_NOT_ACCEPTABLE,
            )
        return super().handle_exception(exc)


class VideoSegmentView(MediaSegmentBaseView):
    """Return the HLS master playlist for the requested video stream."""

    renderer_classes = [JSONRenderer, M3U8Renderer]
    media_renderer_class = M3U8Renderer
    allowed_accept_types = ("*/*", "application/*", M3U8Renderer.media_type)
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Videos"],
        operation_id="video_manifest_retrieve",
        parameters=[],  # Path-Parameter werden automatisch erkannt
        request=None,
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
            stream = get_video_stream(
                user=request.user, **serializer.validated_data)
        except PermissionError as exc:
            return self._json_response(
                {"errors": {"non_field_errors": [
                    str(exc) or "You do not have permission to access this video."]}},
                status.HTTP_403_FORBIDDEN,
            )
        except VideoStream.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": [
                    "Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        return self._render_media_response(stream.manifest, status.HTTP_200_OK)


class VideoSegmentContentView(MediaSegmentBaseView):
    """Return a binary HLS segment for the requested video stream."""

    renderer_classes = [JSONRenderer, TSRenderer]
    media_renderer_class = TSRenderer
    allowed_accept_types = (
        "*/*", "video/*", TSRenderer.media_type.lower(), TSRenderer.media_type)
    not_acceptable_message = "Requested media type not acceptable."
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Videos"],
        operation_id="video_segment_content_retrieve",
        request=None,
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
            video_segment = get_video_segment(
                user=request.user, **serializer.validated_data)
        except PermissionError as exc:
            return self._json_response(
                {"errors": {"non_field_errors": [
                    str(exc) or "You do not have permission to access this video."]}},
                status.HTTP_403_FORBIDDEN,
            )
        except VideoSegment.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        return self._render_media_response(video_segment.content, status.HTTP_200_OK)


@extend_schema(
    tags=["Transcoding"],
    request=None,
    responses={
        202: {
            "type": "object",
            "properties": {
                "detail": {"type": "string"},
                "video_id": {"type": "integer"},
            },
            "required": ["detail", "video_id"],
        },
        400: ERROR_RESPONSE_REF,
        404: ERROR_RESPONSE_REF,
        409: ERROR_RESPONSE_REF,
    },
    auth=[{"cookieJwtAuth": []}],
    examples=[
        OpenApiExample(
            "TranscodeAccepted",
            value={"detail": "Transcode accepted", "video_id": 42},
            response_only=True,
            status_codes=["202"],
        ),
        OpenApiExample(
            "TranscodeError",
            value={"errors": {"non_field_errors": ["Unsupported resolution 'bogus'."]}},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
class VideoTranscodeView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "transcode"
    permission_classes = [IsAuthenticated]
    http_method_names = ["post", "options"]

    def post(self, request, video_id: int):
        try:
            payload = request.data or {}
        except ParseError as exc:
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        query_res = request.query_params.get("res")
        target_resolutions: list[str]

        if query_res is not None:
            requested = [item.strip()
                         for item in query_res.split(",") if item.strip()]
            if not requested:
                target_resolutions = list(ALLOWED_TRANSCODE_PROFILES.keys())
            else:
                invalid = next(
                    (item for item in requested if item not in ALLOWED_TRANSCODE_PROFILES), None)
                if invalid:
                    return Response(
                        {"errors": {
                            "res": [f"Unsupported resolution '{invalid}'"]}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                target_resolutions = requested
        else:
            serializer = VideoTranscodeRequestSerializer(data=payload)
            if not serializer.is_valid():
                return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
            target_resolutions = serializer.validated_data["resolutions"]

        try:
            video = Video.objects.get(pk=video_id)
        except Video.DoesNotExist:
            return Response(
                {"errors": {"non_field_errors": ["Video not found."]}},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = request.user
        is_owner = video.owner_id is not None and video.owner_id == getattr(
            user, "id", None)
        is_admin = getattr(user, "is_staff", False) or getattr(
            user, "is_superuser", False)
        if not (is_owner or is_admin):
            return Response(
                {"errors": {"non_field_errors": [
                    "You do not have permission to modify this video."]}},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_transcode_locked(video.id):
            return Response(
                {"errors": {"non_field_errors": [
                    "Transcode already in progress."]}},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            enqueue_result = enqueue_transcode_job(
                video.id,
                resolutions=target_resolutions,
            )
        except TranscodeError as exc:
            return Response(
                {"errors": _format_validation_error(exc)},
                status=getattr(exc, "status_code",
                               status.HTTP_400_BAD_REQUEST),
            )
        except ValidationError as exc:
            return Response(
                {"errors": _format_validation_error(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # pragma: no cover - defensive logging handled upstream
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if isinstance(enqueue_result, dict) and enqueue_result.get("job_id"):
            logger.info(
                "Transcode job accepted: video_id=%s, job_id=%s, queue=%s",
                video_id,
                enqueue_result.get("job_id"),
                enqueue_result.get("queue"),
            )

        return Response(
            {"detail": "Transcode accepted", "video_id": video_id},
            status=status.HTTP_202_ACCEPTED,
        )

    def handle_exception(self, exc):
        if isinstance(exc, NotAuthenticated):
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return super().handle_exception(exc)
