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

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from jobs.domain import services as transcode_services
from jobs.domain.services import ALLOWED_TRANSCODE_PROFILES, TranscodeError, is_transcode_locked
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "transcode"
    permission_classes = [IsAuthenticated]
    http_method_names = ["post", "options"]

    def post(self, request, video_id: int):
        try:
            real_id = resolve_public_id(video_id)
        except Video.DoesNotExist:
            return Response(
                {"errors": {"non_field_errors": ["Video not found."]}},
                status=status.HTTP_404_NOT_FOUND,
            )
        video_id = real_id

        logger.info(
            "Transcode request: video_id=%s, IS_TEST_ENV=%s",
            video_id,
            getattr(settings, "IS_TEST_ENV", None),
        )

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
            requested = [item.strip() for item in query_res.split(",") if item.strip()]
            if not requested:
                target_resolutions = list(ALLOWED_TRANSCODE_PROFILES.keys())
            else:
                invalid = next(
                    (item for item in requested if item not in ALLOWED_TRANSCODE_PROFILES),
                    None,
                )
                if invalid:
                    return Response(
                        {"errors": {"res": [f"Unsupported resolution '{invalid}'"]}},
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
        is_owner = video.owner_id is not None and video.owner_id == getattr(user, "id", None)
        is_admin = getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
        if not (is_owner or is_admin):
            return Response(
                {"errors": {"non_field_errors": ["You do not have permission to modify this video."]}},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_transcode_locked(video.id):
            return Response(
                {"errors": {"non_field_errors": ["Transcode already in progress."]}},
                status=status.HTTP_409_CONFLICT,
            )

        if "PYTEST_CURRENT_TEST" in os.environ:
            setattr(settings, "IS_TEST_ENV", True)

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
