"""Compatibility command delegating thumbnail rebuilds to media_maintenance."""

from __future__ import annotations

import io
import json

from django.core.management import call_command
from django.core.management.base import BaseCommand

from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id


class Command(BaseCommand):
    """Proxy command that forwards to media_maintenance --heal."""

    help = "Regenerate thumbnails for the supplied videos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--public",
            nargs="+",
            type=int,
            default=[],
            help="Public IDs to rebuild (maps to published ordering).",
        )
        parser.add_argument(
            "--real",
            nargs="+",
            type=int,
            default=[],
            help="Direct video primary keys to rebuild.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite thumbnails even if a file already exists.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON output (forwarded to media_maintenance).",
        )

    def handle(self, *args, **options):
        """Resolve requested video IDs and delegate healing to media_maintenance."""
        self.stderr.write(
            self.style.WARNING(
                "This command is deprecated; please use `media_maintenance --heal`."
            )
        )

        public_ids: list[int] = [int(value) for value in options.get("public") or []]
        explicit_reals = {int(value) for value in options.get("real") or []}
        wants_json: bool = bool(options.get("json"))
        force: bool = bool(options.get("force"))

        resolved_ids = set(explicit_reals)
        invalid_publics: list[int] = []

        for public_id in public_ids:
            try:
                resolved_ids.add(resolve_public_id(public_id))
            except Video.DoesNotExist:
                invalid_publics.append(public_id)

        for public_id in invalid_publics:
            self.stderr.write(f"Ignoring unknown public id: {public_id}")

        if force:
            self.stderr.write(
                self.style.WARNING(
                    "--force is ignored; media_maintenance overwrites thumbnails as needed."
                )
            )

        if not resolved_ids:
            self.stdout.write("No matching videos found; nothing to do.")
            return

        args: list[str] = ["--heal"]
        for real_id in sorted(resolved_ids):
            args.extend(["--real", str(real_id)])

        payload = self._run_media_maintenance(args)
        self._emit_payload(payload, wants_json)

    def _run_media_maintenance(self, base_args: list[str]) -> dict:
        """Run media_maintenance with the supplied arguments and parse its JSON output."""
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
        """Pretty-print the payload unless JSON passthrough is requested."""
        if not payload:
            if not wants_json:
                self.stdout.write("media_maintenance returned no data.")
            self.stdout.write(json.dumps(payload, indent=2))
            return

        if not wants_json:
            heal_data = payload.get("heal") or {}
            fixed = heal_data.get("fixed") or []
            thumbs = heal_data.get("thumbnails") or []

            if fixed:
                details = ", ".join(
                    f"{item['video_id']} ({', '.join(item['resolutions'])})"
                    for item in fixed
                )
                self.stdout.write(f"Healed manifests for: {details}")
            else:
                self.stdout.write("No stub manifests detected.")

            if thumbs:
                thumb_list = ", ".join(str(video_id) for video_id in thumbs)
                self.stdout.write(f"Generated thumbnails for: {thumb_list}")

        self.stdout.write(json.dumps(payload, indent=2))
