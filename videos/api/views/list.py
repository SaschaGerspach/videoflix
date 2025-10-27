from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from videos.api.serializers import VideoListRequestSerializer, VideoSerializer
from videos.domain.selectors import list_published_videos

from .common import ERROR_RESPONSE_REF


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
