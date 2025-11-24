"""Management command that prints a JSON snapshot of selected runtime settings."""

from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Emit a JSON object with deployment-relevant settings."""

    help = "Prints a JSON line summarizing key runtime configuration."

    def handle(self, *args, **options):
        """Serialize selected settings to stdout as a single JSON object."""
        payload = {
            "env": getattr(settings, "ENV", None),
            "debug": bool(getattr(settings, "DEBUG", False)),
            "public_media_base": getattr(settings, "PUBLIC_MEDIA_BASE", None),
            "frontend_domain": getattr(settings, "FRONTEND_DOMAIN", None),
            "default_from_email": getattr(settings, "DEFAULT_FROM_EMAIL", None),
            "allowed_hosts": list(getattr(settings, "ALLOWED_HOSTS", []) or []),
            "cors_origins": self._as_list(
                getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []
            ),
            "allowed_renditions": list(
                getattr(
                    settings,
                    "ALLOWED_RENDITIONS",
                    getattr(settings, "VIDEO_ALLOWED_RENDITIONS", []),
                )
                or []
            ),
            "rq": {
                "queue_transcode": getattr(settings, "RQ_QUEUE_TRANSCODE", None),
                "redis_url": getattr(settings, "RQ_REDIS_URL", None),
            },
        }

        self.stdout.write(json.dumps(payload, ensure_ascii=False))

    def _as_list(self, value):
        """Normalize comma/space separated strings into lists."""
        if isinstance(value, str):
            return [item.strip() for item in value.split() if item.strip()]
        if isinstance(value, (list, tuple)):
            return list(value)
        return []
