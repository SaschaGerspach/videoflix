from __future__ import annotations


from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from videos.domain import thumbs as thumb_utils
from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id


class HLSManifestDebugView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pub: int, res: str):
        if not settings.DEBUG:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            real_id = resolve_public_id(pub)
        except Video.DoesNotExist:
            real_id = None

        path = None
        exists = False
        size = 0
        if real_id is not None:
            path = (
                Path(settings.MEDIA_ROOT)
                / "hls"
                / str(real_id)
                / res
                / "index.m3u8"
            )
            exists = path.exists()
            if exists:
                size = len(path.read_bytes())

        return Response(
            {
                "exists": exists,
                "size": size,
                "ctype": "application/vnd.apple.mpegurl",
            }
        )


class AllowedRenditionsDebugView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        if not settings.DEBUG:
            return Response(status=status.HTTP_404_NOT_FOUND)

        allowed = tuple(
            getattr(
                settings,
                "ALLOWED_RENDITIONS",
                getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p")),
            )
        )
        return Response({"allowed": list(allowed)})


class ThumbsDebugView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public: int):
        if not settings.DEBUG:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            real_id = resolve_public_id(public)
        except Video.DoesNotExist:
            real_id = None

        thumb_path: Path | None = None
        exists = False
        size: int | None = None
        url = ""

        if real_id is not None:
            thumb_path = thumb_utils.get_thumbnail_path(real_id)
            exists = thumb_path.exists()
            size = thumb_path.stat().st_size if exists else None
            url = thumb_utils.get_thumbnail_url(request, real_id) or ""

        return Response(
            {
                "public": public,
                "real": real_id,
                "path": str(thumb_path) if thumb_path is not None else None,
                "exists": exists,
                "bytes": size,
                "url": url,
            }
        )
