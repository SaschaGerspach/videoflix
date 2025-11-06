from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.queue import get_transcode_queue


class QueueHealthView(APIView):
    """Debug helper that reports the status of the transcode queue."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        if not settings.DEBUG:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        queue_name = (getattr(settings, "RQ_QUEUE_TRANSCODE", "") or "").strip()
        payload: dict[str, Any] = {
            "queue": queue_name or None,
            "connected": False,
            "count": None,
        }

        detail: str | None = None

        if not queue_name:
            detail = "RQ_QUEUE_TRANSCODE is not configured."
        else:
            try:
                queue = get_transcode_queue()
            except Exception as exc:  # pragma: no cover - defensive guard
                detail = f"Queue resolution failed: {exc}"
            else:
                if queue is None:
                    detail = "Queue unavailable."
                else:
                    payload["queue"] = getattr(queue, "name", queue_name)
                    try:
                        count_attr = getattr(queue, "count", None)
                        if callable(count_attr):
                            count_value = count_attr()
                        else:
                            count_value = count_attr
                        payload["count"] = int(count_value) if count_value is not None else 0
                        payload["connected"] = True
                    except Exception as exc:  # pragma: no cover - best effort logging
                        detail = f"Queue inspection failed: {exc}"

        if detail:
            payload["detail"] = detail

        response = Response(payload)
        response["Cache-Control"] = "no-cache"
        return response
