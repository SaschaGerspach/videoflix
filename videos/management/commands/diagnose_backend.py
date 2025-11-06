from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

from django.conf import settings
from django.core.management.base import BaseCommand

from videos.domain.services_ops import (
    format_diagnose_backend_text,
    run_diagnose_backend,
)


class Command(BaseCommand):
    help = "Diagnose backend health (filesystem, routing, and view availability)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--public",
            nargs="*",
            type=int,
            default=None,
            help="Public video IDs to inspect. When omitted, discover ready videos.",
        )
        parser.add_argument(
            "--res",
            nargs="*",
            type=str,
            default=None,
            help="Set of renditions to inspect (default uses canonical/allowed settings).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=False,
            help="Emit JSON report to stdout.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Include per-check details in the output.",
        )

    def handle(self, *args, **options):
        explicit_public: Sequence[int] | None = options.get("public")
        requested_res: Sequence[str] | None = options.get("res")
        as_json: bool = bool(options.get("json"))
        verbose: bool = bool(options.get("verbose"))

        report = run_diagnose_backend(
            settings=settings,
            media_root=Path(settings.MEDIA_ROOT),
            explicit_public=explicit_public,
            requested_res=requested_res,
        )

        if as_json:
            indent = 2 if verbose else None
            self.stdout.write(json.dumps(report, indent=indent))
        else:
            self.stdout.write(format_diagnose_backend_text(report, verbose))

        failures = int(report.get("summary", {}).get("failures", 0))
        if failures:
            sys.exit(2)  # pragma: no cover
