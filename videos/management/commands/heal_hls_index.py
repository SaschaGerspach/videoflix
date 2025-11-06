from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

from django.conf import settings
from django.core.management.base import BaseCommand

from videos.domain.services_ops import (
    format_heal_hls_index_text,
    run_heal_hls_index,
)


class Command(BaseCommand):
    help = "Index existing HLS renditions from the filesystem into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--public",
            nargs="*",
            type=int,
            dest="public_ids",
            help="Process public video identifiers.",
        )
        parser.add_argument(
            "--res",
            nargs="*",
            dest="resolutions",
            help="Limit processing to specific resolutions.",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            dest="write",
            default=False,
            help="Apply database/filesystem changes instead of dry-run.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json",
            default=False,
            help="Emit JSON report (exit 2 when errors encountered).",
        )
        parser.add_argument(
            "--rebuild-master",
            action="store_true",
            dest="rebuild_master",
            default=False,
            help="Rebuild master playlist for each processed video.",
        )

    def handle(self, *args, **options):
        public_ids: Sequence[int] | None = options.get("public_ids")
        resolutions: Sequence[str] | None = options.get("resolutions")
        write: bool = bool(options.get("write"))
        as_json: bool = bool(options.get("json"))
        rebuild_master: bool = bool(options.get("rebuild_master"))

        if not public_ids and not options.get("resolutions") and not rebuild_master and not write:
            # Allow dry-run with no args; only enforce when everything empty and no discovery possible.
            pass

        result = run_heal_hls_index(
            settings=settings,
            media_root=Path(settings.MEDIA_ROOT),
            publics=public_ids,
            resolutions=resolutions,
            write=write,
            rebuild_master=rebuild_master,
        )

        if as_json:
            self.stdout.write(json.dumps(result, indent=2))
        else:
            self.stdout.write(format_heal_hls_index_text(result))

        errors_present = any(item["errors"] for item in result.get("videos", []))
        if errors_present:
            sys.exit(2)  # pragma: no cover
