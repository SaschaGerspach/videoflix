from __future__ import annotations

import io
import json
from collections.abc import Sequence

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Diagnose backend health by delegating to media_maintenance --scan."""

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

    def handle(self, *args, **options):
        """Orchestrate option parsing, delegated scan, and result output."""
        parsed = self._parse_options(options)
        args = self._build_media_maintenance_args(parsed["public"], parsed["res"])
        payload = self._run_media_maintenance(args)
        self._print_result(payload, parsed["json"])

    def _parse_options(self, options) -> dict[str, object]:
        """Parse CLI options and warn about deprecation."""
        self.stderr.write(
            self.style.WARNING(
                "Dieses Kommando ist veraltet, bitte `media_maintenance --scan` verwenden."
            )
        )
        return {
            "public": options.get("public"),
            "res": options.get("res"),
            "json": bool(options.get("json")),
        }

    def _build_media_maintenance_args(
        self, explicit_public: Sequence[int] | None, requested_res: Sequence[str] | None
    ) -> list[str]:
        """Construct the media_maintenance argument list based on CLI inputs."""
        args: list[str] = ["--scan"]
        if explicit_public:
            for pid in explicit_public:
                args.extend(["--public", str(pid)])
        if requested_res:
            for res in requested_res:
                args.extend(["--res", res])
        return args

    def _run_media_maintenance(self, base_args: list[str]) -> dict:
        """Call media_maintenance with JSON output and parse the response."""
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

    def _print_result(self, payload: dict, wants_json: bool) -> None:
        """Print scan results unless JSON passthrough is requested."""
        if not payload:
            if not wants_json:
                self.stdout.write("media_maintenance returned no data.")
            self.stdout.write(json.dumps(payload, indent=2))
            return

        scan_data = payload.get("scan") or {}
        reports = scan_data.get("videos") or []
        affected = scan_data.get("affected_video_ids") or []

        if not wants_json:
            if not reports:
                self.stdout.write("No videos scanned; nothing to report.")
            else:
                count = len(reports)
                self.stdout.write(f"Scanned {count} video(s).")
                if affected:
                    affected_list = ", ".join(str(video_id) for video_id in affected)
                    self.stdout.write(f"Affected videos: {affected_list}")
                else:
                    self.stdout.write("All renditions OK.")

        self.stdout.write(json.dumps(payload, indent=2))
