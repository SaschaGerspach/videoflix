from __future__ import annotations

from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.domain.services import TRANSCODE_PROFILE_CONFIG
from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id


class VideoHealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public_id: int):
        try:
            real_id = resolve_public_id(public_id)
        except Video.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        base_dir = Path(settings.MEDIA_ROOT) / "hls" / str(real_id)
        if not base_dir.exists():
            return Response(status=status.HTTP_404_NOT_FOUND)

        rendition_stats: dict[str, dict[str, int]] = {}
        for resolution in TRANSCODE_PROFILE_CONFIG.keys():
            manifest_path = base_dir / resolution / "index.m3u8"
            if not manifest_path.exists():
                continue

            segment_files = [
                path
                for path in (manifest_path.parent).glob("*.ts")
                if path.is_file()
            ]
            sizes = [path.stat().st_size for path in segment_files]
            rendition_stats[resolution] = {
                "segments": len(segment_files),
                "bytes_min": min(sizes) if sizes else 0,
                "bytes_max": max(sizes) if sizes else 0,
            }

        if not rendition_stats:
            return Response(status=status.HTTP_404_NOT_FOUND)

        payload = {
            "public": int(public_id),
            "real": real_id,
            "renditions": rendition_stats,
        }
        response = Response(payload, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-cache"
        return response
