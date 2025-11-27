"""Health endpoints for checking whether HLS renditions exist on disk."""

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
    """Return rendition statistics for a public video without authentication."""

    permission_classes = [AllowAny]

    def get(self, request, public_id: int):
        real_id = self._resolve_real_id(public_id)
        if real_id is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        base_dir = self._resolve_base_dir(real_id)
        if base_dir is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        rendition_stats = self._collect_rendition_stats(base_dir)
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

    def _resolve_real_id(self, public_id: int) -> int | None:
        """Resolve a public video ID to its real database ID."""
        try:
            return resolve_public_id(public_id)
        except Video.DoesNotExist:
            return None

    def _resolve_base_dir(self, real_id: int) -> Path | None:
        """Return the base HLS directory for a video if it exists."""
        base_dir = Path(settings.MEDIA_ROOT) / "hls" / str(real_id)
        if not base_dir.exists():
            return None
        return base_dir

    def _collect_rendition_stats(self, base_dir: Path) -> dict[str, dict[str, int]]:
        """Collect segment counts and size stats for each available rendition."""
        rendition_stats: dict[str, dict[str, int]] = {}
        for resolution in TRANSCODE_PROFILE_CONFIG.keys():
            manifest_path = base_dir / resolution / "index.m3u8"
            if not manifest_path.exists():
                continue

            segment_files = [
                path for path in (manifest_path.parent).glob("*.ts") if path.is_file()
            ]
            sizes = [path.stat().st_size for path in segment_files]
            rendition_stats[resolution] = {
                "segments": len(segment_files),
                "bytes_min": min(sizes) if sizes else 0,
                "bytes_max": max(sizes) if sizes else 0,
            }
        return rendition_stats
