"""Serve HLS manifests and segments based on public video IDs."""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import FileResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.views import APIView

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from videos.api.serializers import VideoSegmentRequestSerializer
from videos.domain.models import Video, VideoStream
from videos.domain.selectors import resolve_public_id
from videos.domain.services_index import fs_rendition_exists, index_existing_rendition
from videos.domain.utils import find_manifest_path, is_stub_manifest

from .common import ERROR_RESPONSE_REF
from .media_base import (
    M3U8Renderer,
    MediaSegmentBaseView,
    _debug_not_found,
    _set_cache_headers,
    _user_can_access,
    force_json_response,
)

logger = logging.getLogger(__name__)


def _get_allowed_renditions() -> tuple[str, ...]:
    allowed = getattr(settings, "ALLOWED_RENDITIONS", None)
    if allowed is None:
        allowed = getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p"))
    if not allowed:
        return ()
    return tuple(allowed)


class _JSONOnlyNegotiation(BaseContentNegotiation):
    def select_renderer(self, request, renderers, format_suffix=None):
        renderer = renderers[0]
        return renderer, renderer.media_type

    def select_parser(self, request, parsers):
        return parsers[0]


class VideoSegmentView(MediaSegmentBaseView):
    """Return the HLS master playlist for the requested video stream."""

    renderer_classes = [JSONRenderer, M3U8Renderer]
    media_renderer_class = M3U8Renderer
    allowed_accept_types = ("*/*", M3U8Renderer.media_type)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="video_manifest",
        request=None,
        tags=["Videos"],
        parameters=[],
        responses={
            (200, M3U8Renderer.media_type): OpenApiResponse(
                response=OpenApiTypes.BYTE,
                description="HLS master playlist in M3U8 format",
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
        if settings.DEBUG:
            access_cookie_name = getattr(settings, "ACCESS_COOKIE_NAME", "access_token")
            cookies = getattr(request, "COOKIES", {}) or {}
            logger.debug(
                "VideoSegmentView.get path=%s trailing_slash=%s authenticated=%s cookie_present=%s raw_cookie=%s accept=%s",
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
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )
        try:
            real_id = resolve_public_id(movie_id)
        except Video.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        resolution = (resolution or "").strip().lower()

        serializer = VideoSegmentRequestSerializer(
            data={"movie_id": real_id, "resolution": resolution}
        )
        if not serializer.is_valid():
            return self._json_response(
                {"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST
            )

        try:
            video = Video.objects.get(pk=real_id)
        except Video.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        if not _user_can_access(request, video):
            return self._json_response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        resolution_value = serializer.validated_data["resolution"]
        allowed_renditions = _get_allowed_renditions()
        fs_exists, fs_manifest_path, _ = fs_rendition_exists(real_id, resolution_value)
        manifest_path = (
            fs_manifest_path
            if fs_manifest_path.parts
            else find_manifest_path(real_id, resolution_value)
        )

        if fs_exists:
            try:
                manifest_bytes = manifest_path.read_bytes()
            except OSError:
                manifest_bytes = None
            else:
                if is_stub_manifest(manifest_bytes):
                    resp = self._json_response(
                        {"errors": {"non_field_errors": ["Video manifest not found."]}},
                        status.HTTP_404_NOT_FOUND,
                    )
                    return _debug_not_found(resp, "manifest-stub")
                if self._accepts_json_only(request):
                    resp = self._json_response(
                        {"errors": {"non_field_errors": ["Video manifest not found."]}},
                        status.HTTP_404_NOT_FOUND,
                    )
                    return _debug_not_found(resp, "json-only-not-allowed")
                self._ensure_accept_header(request, M3U8Renderer.media_type)
                response = FileResponse(manifest_path.open("rb"))
                response["Content-Type"] = M3U8Renderer.media_type
                response["Content-Disposition"] = 'inline; filename="index.m3u8"'
                _set_cache_headers(response, manifest_path)
                try:
                    index_existing_rendition(real_id, resolution_value)
                except Exception:  # pragma: no cover - defensive logging only
                    logger.exception(
                        "Self-heal indexing failed for manifest video_id=%s resolution=%s",
                        real_id,
                        resolution_value,
                    )
                return response

        if allowed_renditions and resolution_value not in allowed_renditions:
            resp = self._json_response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )
            return _debug_not_found(resp, "resolution-not-allowed")

        try:
            stream = VideoStream.objects.select_related("video").get(
                video_id=real_id,
                resolution=resolution_value,
            )
        except VideoStream.DoesNotExist:
            return self._json_response(
                {"errors": {"non_field_errors": ["Video manifest not found."]}},
                status.HTTP_404_NOT_FOUND,
            )

        db_manifest = stream.manifest or ""
        if db_manifest and not is_stub_manifest(db_manifest):
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(db_manifest, encoding="utf-8")
            if self._accepts_json_only(request):
                resp = self._json_response(
                    {"errors": {"non_field_errors": ["Video manifest not found."]}},
                    status.HTTP_404_NOT_FOUND,
                )
                return _debug_not_found(resp, "json-only-not-allowed")
            self._ensure_accept_header(request, M3U8Renderer.media_type)
            response = FileResponse(manifest_path.open("rb"))
            response["Content-Type"] = M3U8Renderer.media_type
            response["Content-Disposition"] = 'inline; filename="index.m3u8"'
            _set_cache_headers(response, manifest_path)
            return response

        resp = self._json_response(
            {"errors": {"non_field_errors": ["Video manifest not found."]}},
            status.HTTP_404_NOT_FOUND,
        )
        return _debug_not_found(resp, "no-manifest-file-and-db-empty")


class VideoManifestView(VideoSegmentView):
    """Backward-compatible alias for manifest endpoint."""


class DebugAuthView(APIView):
    """Local-only helper that dumps cookie and auth status for troubleshooting."""

    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    content_negotiation_class = _JSONOnlyNegotiation

    def get(self, request):
        if not settings.DEBUG:
            return force_json_response({}, status.HTTP_404_NOT_FOUND)

        access_cookie_name = getattr(settings, "ACCESS_COOKIE_NAME", "access_token")
        cookies = dict(getattr(request, "COOKIES", {}) or {})
        raw_cookie = request.META.get("HTTP_COOKIE")
        seen_access_cookie = access_cookie_name in cookies or (
            raw_cookie is not None and f"{access_cookie_name}=" in raw_cookie
        )
        user_obj = getattr(request, "user", None)
        authenticated = bool(user_obj and user_obj.is_authenticated)
        user_id = getattr(user_obj, "id", None) if authenticated else None

        truncated_cookie = raw_cookie[:256] if raw_cookie else None

        return force_json_response(
            {
                "cookies": cookies,
                "raw_cookie": truncated_cookie,
                "seen_access_cookie": bool(seen_access_cookie),
                "user_authenticated": authenticated,
                "user_id": user_id,
            }
        )
