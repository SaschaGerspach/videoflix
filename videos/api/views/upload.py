from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated, ParseError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from jobs.domain import services as transcode_services
from jobs.domain.services import TranscodeError, is_transcode_locked
from videos.api.serializers import VideoUploadSerializer
from videos.domain.models import Video

from .common import ERROR_RESPONSE_REF, _format_validation_error, logger


class UploadFileSerializer(serializers.Serializer):
    file = serializers.FileField()


@extend_schema(
    request=UploadFileSerializer,
    responses={
        201: {
            "type": "object",
            "properties": {
                "detail": {"type": "string"},
                "video_id": {"type": "integer"},
            },
            "required": ["detail", "video_id"],
        },
        400: ERROR_RESPONSE_REF,
    },
    auth=[{"cookieJwtAuth": []}],
)
class VideoUploadView(APIView):
    """Accept multipart video uploads and trigger auto-transcode for missing profiles."""

    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "video_upload"
    http_method_names = ["post", "options"]

    def post(self, request, video_id: int):
        parse_response = self._ensure_parsed_request(request)
        if parse_response is not None:
            return parse_response

        upload_candidate, error_response = self._extract_upload_file(request)
        if error_response is not None:
            return error_response

        serializer = VideoUploadSerializer(data={"file": upload_candidate})
        if not serializer.is_valid():
            return Response(
                {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
            )

        video_response, video = self._get_video_or_404(video_id)
        if video_response is not None:
            return video_response

        permission_response = self._check_permissions(request.user, video)
        if permission_response is not None:
            return permission_response

        file_obj = serializer.validated_data["file"]
        size_response = self._validate_file_size(file_obj)
        if size_response is not None:
            return size_response

        target_path = transcode_services.get_video_source_path(video_id)
        self._store_upload(file_obj, target_path, video_id)

        missing_resolutions = self._collect_missing_resolutions(video_id)
        if not missing_resolutions:
            return Response(
                {"detail": "Upload ok", "video_id": video_id},
                status=status.HTTP_201_CREATED,
            )

        if is_transcode_locked(video_id):
            logger.info(
                "Upload processed, transcode currently locked: video_id=%s, pending_resolutions=%s",
                video_id,
                missing_resolutions,
            )
            return Response(
                {"detail": "Upload ok", "video_id": video_id},
                status=status.HTTP_201_CREATED,
            )

        enqueue_response = self._enqueue_transcode(video_id, missing_resolutions)
        if enqueue_response is not None:
            return enqueue_response

        logger.info(
            "Upload auto-transcode queued: video_id=%s, resolutions=%s",
            video_id,
            missing_resolutions,
        )
        return Response(
            {"detail": "Upload ok", "video_id": video_id},
            status=status.HTTP_201_CREATED,
        )

    def _ensure_parsed_request(self, request):
        """Force DRF to parse the request data, returning a Response on error."""
        try:
            _ = request.data
        except ParseError as exc:
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return None

    def _extract_upload_file(self, request):
        """Return uploaded file object or an error Response if missing."""
        upload_candidate = request.FILES.get("file")
        if upload_candidate is None:
            return (
                None,
                Response(
                    {"errors": {"file": ["No video file provided."]}},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            )
        return upload_candidate, None

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

    def _validate_file_size(self, file_obj):
        """Check file size against configured maximum, returning Response on failure."""
        max_bytes = getattr(settings, "VIDEO_UPLOAD_MAX_BYTES", 2 * 1024 * 1024 * 1024)
        file_size = getattr(file_obj, "size", None)
        if file_size is not None and file_size > max_bytes:
            return Response(
                {
                    "errors": {
                        "file": [f"File too large. Max size is {max_bytes} bytes."]
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    def _store_upload(self, file_obj, target_path, video_id: int) -> None:
        """Persist the uploaded file to the target path, logging the outcome."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as destination:
            for chunk in file_obj.chunks():
                destination.write(chunk)
        logger.info("Video source stored: video_id=%s, path=%s", video_id, target_path)

    def _collect_missing_resolutions(self, video_id: int) -> list[str]:
        """Return a list of missing renditions that require transcode."""
        return [
            resolution
            for resolution in transcode_services.ALLOWED_TRANSCODE_PROFILES
            if not transcode_services.manifest_exists_for_resolution(
                video_id, resolution
            )
        ]

    def _enqueue_transcode(
        self, video_id: int, missing_resolutions: list[str]
    ) -> Response | None:
        """Enqueue transcode jobs for missing resolutions, returning Response on error."""
        try:
            transcode_services.enqueue_transcode(
                video_id,
                target_resolutions=missing_resolutions,
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
        return None

    def handle_exception(self, exc):
        if isinstance(exc, NotAuthenticated):
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return super().handle_exception(exc)
