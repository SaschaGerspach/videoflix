"""API views related to listing videos for authenticated users."""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.exceptions import FieldDoesNotExist

from drf_spectacular.utils import extend_schema

from videos.api.serializers import VideoListRequestSerializer, VideoSerializer
from videos.domain import selectors_public
from videos.domain.models import Video

from .common import ERROR_RESPONSE_REF

try:
    Video._meta.get_field("updated_at")
    UPDATED_ORDER_FIELD = "updated_at"
except FieldDoesNotExist:
    UPDATED_ORDER_FIELD = "created_at"


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
    """Return all available videos for authenticated users (HLS-ready by default)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        parse_response, data = self._parse_request_data(request)
        if parse_response is not None:
            return parse_response

        serializer = VideoListRequestSerializer(data=data)
        if not serializer.is_valid():
            return Response(
                {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
            )

        ready_only = self._resolve_ready_only(request.query_params.get("ready_only"))
        order_param = request.query_params.get("order")
        ordering = self._resolve_ordering(order_param)

        payload = selectors_public.list_for_user_with_public_ids(
            request.user,
            ready_only=ready_only,
            ordering=ordering,
        )
        return Response(payload, status=status.HTTP_200_OK)

    def _parse_request_data(self, request):
        """Extract request data while mirroring existing ParseError handling."""
        try:
            return None, request.data
        except ParseError as exc:
            detail = getattr(exc, "detail", str(exc))
            message = f"Invalid JSON: {detail}"
            return (
                Response(
                    {"errors": {"non_field_errors": [str(message)]}},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
                None,
            )

    @staticmethod
    def _resolve_ready_only(raw: str | None) -> bool:
        """Resolve ready_only flag from query params (defaults to True)."""
        if raw is None:
            return True
        return raw not in {"0", "false", "False"}

    @staticmethod
    def _resolve_ordering(raw: str | None) -> list[str] | None:
        if not raw:
            return None
        candidate = raw.strip()
        if not candidate:
            return None

        descending = candidate.startswith("-")
        key = candidate.lstrip("-")

        field_map = {
            "title": "title",
            "height": "height",
            "updated_at": UPDATED_ORDER_FIELD,
        }

        target = field_map.get(key)
        if not target:
            return None

        prefix = "-" if descending else ""
        primary = f"{prefix}{target}"
        fallback = "-pk" if descending else "pk"
        return [primary, fallback]
