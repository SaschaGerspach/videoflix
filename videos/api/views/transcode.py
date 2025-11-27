from __future__ import annotations

import os

from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiExample, extend_schema

from jobs.domain import services as transcode_services
from jobs.domain.services import (
    ALLOWED_TRANSCODE_PROFILES,
    TranscodeError,
    is_transcode_locked,
)
from videos.api.serializers import VideoTranscodeRequestSerializer
from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id

from .common import ERROR_RESPONSE_REF, _format_validation_error, logger


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
    """Handle manual transcode requests for a given public video ID."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "transcode"
    permission_classes = [IsAuthenticated]
    http_method_names = ["post", "options"]

    def post(self, request, video_id: int):
        real_id_response, real_id = self._resolve_public_video_id(video_id)
        if real_id_response is not None:
            return real_id_response
        video_id = real_id

        logger.info(
            "Transcode request: video_id=%s, IS_TEST_ENV=%s",
            video_id,
            getattr(settings, "IS_TEST_ENV", None),
        )

        payload_response, payload = self._parse_payload(request)
        if payload_response is not None:
            return payload_response

        target_resolutions_response, target_resolutions = (
            self._parse_target_resolutions(request, payload)
        )
        if target_resolutions_response is not None:
            return target_resolutions_response

        video_response, video = self._get_video_or_404(video_id)
        if video_response is not None:
            return video_response

        permission_response = self._check_permissions(request.user, video)
        if permission_response is not None:
            return permission_response

        lock_response = self._check_transcode_lock(video.id)
        if lock_response is not None:
            return lock_response

        if "PYTEST_CURRENT_TEST" in os.environ:
            settings.IS_TEST_ENV = True

        enqueue_response = self._enqueue_transcode(video, target_resolutions)
        if enqueue_response is not None:
            return enqueue_response

        return Response(
            {"detail": "Transcode accepted", "video_id": video_id},
            status=status.HTTP_202_ACCEPTED,
        )

    def _resolve_public_video_id(self, video_id: int):
        """Resolve public video id to real id, returning Response on failure."""
        try:
            real_id = resolve_public_id(video_id)
        except Video.DoesNotExist:
            return (
                Response(
                    {"errors": {"non_field_errors": ["Video not found."]}},
                    status=status.HTTP_404_NOT_FOUND,
                ),
                None,
            )
        return None, real_id

    def _parse_payload(self, request):
        """Parse incoming request payload defensively."""
        try:
            return None, request.data or {}
        except ParseError as exc:
            return (
                Response(
                    {"errors": {"non_field_errors": [str(exc)]}},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
                None,
            )

    def _parse_target_resolutions(self, request, payload):
        """Resolve requested resolutions from query or JSON body."""
        query_res = request.query_params.get("res")
        if query_res is not None:
            return self._parse_query_resolutions(query_res)

        serializer = VideoTranscodeRequestSerializer(data=payload)
        if not serializer.is_valid():
            return (
                Response(
                    {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
                ),
                None,
            )
        return None, serializer.validated_data["resolutions"]

    def _parse_query_resolutions(self, query_res: str):
        """Parse resolutions from ?res= query string."""
        requested = [item.strip() for item in query_res.split(",") if item.strip()]
        if not requested:
            return None, list(ALLOWED_TRANSCODE_PROFILES.keys())

        invalid = next(
            (item for item in requested if item not in ALLOWED_TRANSCODE_PROFILES),
            None,
        )
        if invalid:
            return (
                Response(
                    {"errors": {"res": [f"Unsupported resolution '{invalid}'"]}},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
                None,
            )
        return None, requested

    def _get_video_or_404(self, video_id: int):
        """Fetch the video or return a 404 Response."""
        try:
            return None, Video.objects.get(pk=video_id)
        except Video.DoesNotExist:
            return (
                Response(
                    {"errors": {"non_field_errors": ["Video not found."]}},
                    status=status.HTTP_404_NOT_FOUND,
                ),
                None,
            )

    def _check_permissions(self, user, video):
        """Ensure the requester can modify the video, returning Response on failure."""
        is_owner = video.owner_id is not None and video.owner_id == getattr(
            user, "id", None
        )
        is_admin = getattr(user, "is_staff", False) or getattr(
            user, "is_superuser", False
        )
        if is_owner or is_admin:
            return None
        return Response(
            {
                "errors": {
                    "non_field_errors": [
                        "You do not have permission to modify this video."
                    ]
                }
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    def _check_transcode_lock(self, video_id: int):
        """Return a conflict Response if a transcode is already in progress."""
        if not is_transcode_locked(video_id):
            return None
        return Response(
            {"errors": {"non_field_errors": ["Transcode already in progress."]}},
            status=status.HTTP_409_CONFLICT,
        )

    def _enqueue_transcode(self, video, target_resolutions: list[str]):
        """Enqueue the transcode job and log acceptance details."""
        try:
            enqueue_result = transcode_services.enqueue_transcode(
                video.id,
                target_resolutions=target_resolutions,
            )
        except TranscodeError as exc:
            return Response(
                {"errors": _format_validation_error(exc)},
                status=getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST),
            )
        except ValidationError as exc:
            return Response(
                {"errors": _format_validation_error(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if isinstance(enqueue_result, dict) and enqueue_result.get("job_id"):
            logger.info(
                "Transcode job accepted: video_id=%s, job_id=%s, queue=%s",
                video.id,
                enqueue_result.get("job_id"),
                enqueue_result.get("queue"),
            )
        return None

    def handle_exception(self, exc):
        if isinstance(exc, NotAuthenticated):
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return super().handle_exception(exc)
