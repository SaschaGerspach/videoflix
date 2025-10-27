from __future__ import annotations

import hashlib
from rest_framework import status
from rest_framework.exceptions import NotAcceptable, NotAuthenticated, ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer, StaticHTMLRenderer
from rest_framework.response import Response
from rest_framework.views import APIView


class M3U8Renderer(StaticHTMLRenderer):
    media_type = "application/vnd.apple.mpegurl"
    format = "m3u8"


class TSRenderer(BaseRenderer):
    media_type = "video/MP2T"
    format = "ts"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class MediaSegmentBaseView(APIView):
    renderer_classes = [JSONRenderer]
    http_method_names = ["get", "head", "options"]
    media_renderer_class: type[BaseRenderer]
    allowed_accept_types: tuple[str, ...] = ("*/*",)
    not_acceptable_message = "Requested media type not acceptable."
    permission_classes = [IsAuthenticated]

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
                {"errors": {"non_field_errors": [self.not_acceptable_message]}},
                status.HTTP_406_NOT_ACCEPTABLE,
            )
        return super().handle_exception(exc)
