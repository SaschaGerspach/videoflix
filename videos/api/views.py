import hashlib

from rest_framework import status
from rest_framework.exceptions import NotAcceptable, NotAuthenticated, ParseError
from rest_framework.renderers import BaseRenderer, JSONRenderer, StaticHTMLRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from videos.api.serializers import (
    VideoListRequestSerializer,
    VideoSegmentContentRequestSerializer,
    VideoSegmentRequestSerializer,
    VideoSerializer,
)
from videos.domain.models import VideoSegment, VideoStream
from videos.domain.selectors import get_video_segment, get_video_stream, list_videos


class M3U8Renderer(StaticHTMLRenderer):
    media_type = "application/vnd.apple.mpegurl"
    format = "m3u8"


class TSRenderer(BaseRenderer):
    media_type = "video/MP2T"
    format = "ts"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class VideoListView(APIView):
    """Return all available videos for authenticated users."""

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

        videos = list_videos()
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
        payload_bytes = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode("utf-8")
        response["Cache-Control"] = "public, max-age=60"
        response["ETag"] = f'"{hashlib.md5(payload_bytes).hexdigest()}"'
        response.render()
        return response

    def head(self, request, *args, **kwargs):
        response = self.get(request, *args, **kwargs)
        response.content = b""
        response["Content-Length"] = "0"
        return response

    def http_method_not_allowed(self, request, *args, **kwargs):
        return Response(
            {"errors": {"non_field_errors": ["Method not allowed."]}},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def handle_exception(self, exc):
        if isinstance(exc, NotAuthenticated):
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if isinstance(exc, NotAcceptable):
            return Response(
                {"errors": {"non_field_errors": [self.not_acceptable_message]}},
                status=status.HTTP_406_NOT_ACCEPTABLE,
            )
        return super().handle_exception(exc)


class VideoSegmentView(MediaSegmentBaseView):
    """Return the HLS master playlist for the requested video stream."""

    renderer_classes = [JSONRenderer, M3U8Renderer]
    media_renderer_class = M3U8Renderer
    allowed_accept_types = ("*/*", "application/*", M3U8Renderer.media_type)

    def get(self, request, movie_id: int, resolution: str):
        serializer = VideoSegmentRequestSerializer(
            data={"movie_id": movie_id, "resolution": resolution}
        )
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        self._ensure_accept_header(request)

        try:
            stream = get_video_stream(**serializer.validated_data)
        except VideoStream.DoesNotExist:
            return Response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status=status.HTTP_404_NOT_FOUND,
            )

        return self._render_media_response(stream.manifest, status.HTTP_200_OK)


class VideoSegmentContentView(MediaSegmentBaseView):
    """Return a binary HLS segment for the requested video stream."""

    renderer_classes = [JSONRenderer, TSRenderer]
    media_renderer_class = TSRenderer
    allowed_accept_types = ("*/*", "video/*", TSRenderer.media_type.lower(), TSRenderer.media_type)
    not_acceptable_message = "Requested media type not acceptable."

    def get(self, request, movie_id: int, resolution: str, segment: str):
        serializer = VideoSegmentContentRequestSerializer(
            data={
                "movie_id": movie_id,
                "resolution": resolution,
                "segment": segment,
            }
        )
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        self._ensure_accept_header(request)

        try:
            video_segment = get_video_segment(**serializer.validated_data)
        except VideoSegment.DoesNotExist:
            return Response(
                {"errors": {"non_field_errors": ["Video segment not found."]}},
                status=status.HTTP_404_NOT_FOUND,
            )

        return self._render_media_response(video_segment.content, status.HTTP_200_OK)
