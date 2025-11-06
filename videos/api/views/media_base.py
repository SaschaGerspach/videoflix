from __future__ import annotations

import hashlib
from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import NotAcceptable, NotAuthenticated
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


def _set_cache_headers(response, file_path):
    """
    Apply cache headers derived from file metadata without touching the body.
    """
    stat = file_path.stat()
    fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii", "ignore")
    response["Cache-Control"] = "public, max-age=0, no-cache"
    response["ETag"] = f'"{hashlib.md5(fingerprint).hexdigest()}"'
    return response


def _debug_not_found(response, reason: str):
    if getattr(settings, "DEBUG", False):
        response["X-Debug-Why"] = reason
    return response


def force_json_response(payload: dict, status_code: int = status.HTTP_200_OK) -> Response:
    renderer = JSONRenderer()
    response = Response(payload, status=status_code)
    response.accepted_renderer = renderer
    response.accepted_media_type = renderer.media_type
    response.renderer_context = {}
    response["Content-Type"] = renderer.media_type
    response.render()
    return response


def _is_local_request(request) -> bool:
    meta = getattr(request, "META", {}) or {}  # type: ignore[assignment]
    ip = meta.get("REMOTE_ADDR") or ""
    forwarded = meta.get("HTTP_X_FORWARDED_FOR") or ""
    first_forwarded = forwarded.split(",")[0].strip() if forwarded else ""
    candidate = first_forwarded or ip
    return candidate in {"127.0.0.1", "::1"}


def _user_can_access(request, video) -> bool:
    if (
        settings.DEBUG
        and getattr(settings, "DEV_HLS_AUTH_BYPASS", False)
        and getattr(request, "method", "GET") in {"GET", "HEAD"}
        and _is_local_request(request)
        and getattr(video, "is_published", False)
    ):
        return True

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    user_id = getattr(user, "id", None)
    if video.owner_id is not None and video.owner_id == user_id:
        return True
    if video.is_published:
        return True
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    return False


class MediaSegmentBaseView(APIView):
    renderer_classes = [JSONRenderer]
    http_method_names = ["get", "head", "options"]
    media_renderer_class: type[BaseRenderer]
    allowed_accept_types: tuple[str, ...] = ("*/*",)
    not_acceptable_message = "Requested media type not acceptable."
    permission_classes = [IsAuthenticated]

    def _ensure_accept_header(self, request, expected_media_type: str | None = None) -> None:
        if self._accept_allows(request, expected_media_type):
            return
        raise NotAcceptable(self.not_acceptable_message)

    def _accept_allows(self, request, expected_media_type: str | None = None) -> bool:
        accept_header = request.META.get("HTTP_ACCEPT", "")
        if not accept_header:
            return True

        expected = expected_media_type.lower() if expected_media_type else None
        for part in accept_header.split(","):
            media_type = part.strip().split(";")[0].lower()
            if not media_type:
                continue
            if media_type == "*/*":
                return True
            if expected:
                if expected == "application/json" and media_type == "application/json":
                    return True
                if self._media_type_matches(media_type, expected):
                    return True
                continue
            if self._media_type_in_allowed(media_type):
                return True
        return False

    def _media_type_matches(self, candidate: str, expected: str) -> bool:
        if candidate == expected:
            return True
        cand_main, _, cand_sub = candidate.partition("/")
        exp_main, _, exp_sub = expected.partition("/")
        if not cand_sub or not exp_sub:
            return False
        if exp_sub == "*" and cand_main == exp_main:
            return True
        if expected == TSRenderer.media_type.lower():
            return candidate == TSRenderer.media_type.lower()
        if expected == M3U8Renderer.media_type.lower():
            return candidate == M3U8Renderer.media_type.lower()
        return False

    def _media_type_in_allowed(self, candidate: str) -> bool:
        for allowed in self.allowed_accept_types:
            allowed = allowed.lower().strip()
            if allowed == "*/*":
                return True
            if candidate == allowed:
                return True
            allowed_main, _, allowed_sub = allowed.partition("/")
            cand_main, _, cand_sub = candidate.partition("/")
            if allowed_sub == "*" and cand_sub and allowed_main == cand_main:
                return True
        return False

    def _accepts_json_only(self, request) -> bool:
        accept_header = request.META.get("HTTP_ACCEPT", "")
        if not accept_header:
            return False
        media_types = []
        for part in accept_header.split(","):
            media_type = part.strip()
            if not media_type:
                continue
            media_type = media_type.split(";")[0].strip().lower()
            if not media_type:
                continue
            if media_type == "*/*":
                return False
            media_types.append(media_type)
        return bool(media_types) and all(mt == "application/json" for mt in media_types)

    def _json_response(self, payload, status_code: int) -> Response:
        renderer = JSONRenderer()
        response = Response(payload, status=status_code)
        response.accepted_renderer = renderer
        response.accepted_media_type = renderer.media_type
        response.renderer_context = {}
        response["Content-Type"] = renderer.media_type
        response.render()
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
