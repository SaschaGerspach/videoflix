"""Compatibility wrapper around media_maintenance --heal."""

from __future__ import annotations

import io
import json
from collections.abc import Sequence

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Command that forwards heal requests to media_maintenance."""

    help = "Repair HLS manifests by delegating to media_maintenance."

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
            "--json",
            action="store_true",
            dest="json",
            default=False,
            help="Emit JSON report (forwarded to media_maintenance).",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            dest="write",
            help="Deprecated; media_maintenance --heal always applies changes.",
        )
        parser.add_argument(
            "--rebuild-master",
            action="store_true",
            dest="rebuild_master",
            help="Deprecated; master playlists are handled via media_maintenance.",
        )

    def handle(self, *args, **options):
        """Translate CLI options into media_maintenance arguments."""
        self.stderr.write(
            self.style.WARNING(
                "This command is deprecated; please use `media_maintenance --heal`."
            )
        )

        public_ids: Sequence[int] | None = options.get("public_ids")
        resolutions: Sequence[str] | None = options.get("resolutions")
        wants_json: bool = bool(options.get("json"))

        if options.get("write"):
            self.stderr.write(
                self.style.WARNING(
                    "--write is ignored; healing already writes changes."
                )
            )
        if options.get("rebuild_master"):
            self.stderr.write(
                self.style.WARNING(
                    "--rebuild-master is ignored; master playlists are handled automatically."
                )
            )

        args: list[str] = ["--heal"]
        if public_ids:
            for pid in public_ids:
                args.extend(["--public", str(pid)])
        if resolutions:
            for res in resolutions:
                args.extend(["--res", res])

        payload = self._run_media_maintenance(args)
        self._emit_payload(payload, wants_json)

    def _run_media_maintenance(self, base_args: list[str]) -> dict:
        """Execute media_maintenance and parse the JSON response."""
        buffer = io.StringIO()
        call_command(
            "media_maintenance",
            *(base_args + ["--json"]),
            stdout=buffer,
            stderr=self.stderr,
        )
        raw = buffer.getvalue().strip()
        if not raw:
            return {}
        return json.loads(raw)

    def _emit_payload(self, payload: dict, wants_json: bool) -> None:
        """Print payload details unless JSON passthrough is requested."""
        if not payload:
            if not wants_json:
                self.stdout.write("media_maintenance returned no data.")
            self.stdout.write(json.dumps(payload, indent=2))
            return

        heal_data = payload.get("heal") or {}

        if not wants_json:
            fixed = heal_data.get("fixed") or []
            thumbnails = heal_data.get("thumbnails") or []

            if fixed:
                details = ", ".join(
                    f"{item['video_id']} ({', '.join(item['resolutions'])})"
                    for item in fixed
                )
                self.stdout.write(f"Healed manifests for: {details}")
            else:
                self.stdout.write("No stub manifests detected.")

            if thumbnails:
                thumb_list = ", ".join(str(video_id) for video_id in thumbnails)
                self.stdout.write(f"Generated thumbnails for: {thumb_list}")

        self.stdout.write(json.dumps(payload, indent=2))
