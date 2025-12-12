"""Inspect on-disk HLS renditions for a set of videos."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from collections.abc import Iterable, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id

try:
    from videos.management.commands.enqueue_transcodes import _flatten, _unique  # type: ignore
except Exception:  # pragma: no cover - compatibility fallback

    def _flatten(values: Sequence[Sequence[int]] | None) -> list[int]:
        results: list[int] = []
        if not values:
            return results
        for group in values:
            results.extend(group)
        return results

    def _unique(items: Iterable[int]) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered


DEFAULT_RESOLUTIONS = ("480p", "720p", "1080p")


class Command(BaseCommand):
    """Report manifest health for selected videos without mutating state."""

    help = "Inspect existing HLS renditions for the provided videos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--public",
            action="append",
            nargs="+",
            type=int,
            dest="public_ids",
            help="Frontend public IDs (ordinal numbers).",
        )
        parser.add_argument(
            "--real",
            action="append",
            nargs="+",
            type=int,
            dest="real_ids",
            help="Real video primary keys.",
        )
        parser.add_argument(
            "--res",
            action="append",
            dest="resolutions",
            nargs="+",
            choices=DEFAULT_RESOLUTIONS,
            help="One or more resolutions to inspect (default: all).",
        )

    def handle(self, *args, **options):
        """Resolve requested videos and print rendition status summaries."""
        parsed = self._parse_options(options)
        self._run_checks(
            public_inputs=parsed["public_inputs"],
            real_inputs=parsed["real_inputs"],
            resolutions=parsed["resolutions"],
        )

    def _parse_options(self, options) -> dict[str, object]:
        """Validate options and normalise identifiers and resolutions."""
        public_inputs = _flatten(options.get("public_ids"))
        real_inputs = _flatten(options.get("real_ids"))
        raw_resolutions = options.get("resolutions") or []
        flattened_resolutions: list[str] = []
        for chunk in raw_resolutions:
            flattened_resolutions.extend(chunk)
        resolutions = list(dict.fromkeys(flattened_resolutions)) or list(
            DEFAULT_RESOLUTIONS
        )

        if not public_inputs and not real_inputs:
            raise CommandError("Provide at least one --public or --real identifier.")
        return {
            "public_inputs": public_inputs,
            "real_inputs": real_inputs,
            "resolutions": resolutions,
        }

    def _run_checks(
        self,
        *,
        public_inputs: list[int],
        real_inputs: list[int],
        resolutions: list[str],
    ) -> None:
        """Execute rendition checks and emit the same reporting output."""
        invalid_publics: list[int] = []
        public_mapping: dict[int, list[int]] = {}
        resolved_reals: list[int] = []

        for public_id in public_inputs:
            try:
                real_id = resolve_public_id(public_id)
            except Video.DoesNotExist:
                invalid_publics.append(public_id)
                continue
            public_mapping.setdefault(real_id, []).append(public_id)
            resolved_reals.append(real_id)

        missing_real_inputs: list[int] = []
        if real_inputs:
            existing = set(
                Video.objects.filter(pk__in=set(real_inputs)).values_list(
                    "pk", flat=True
                )
            )
            for real_id in real_inputs:
                if real_id not in existing:
                    missing_real_inputs.append(real_id)

        target_real_ids = _unique(resolved_reals + real_inputs)

        if not target_real_ids and (invalid_publics or missing_real_inputs):
            for pid in invalid_publics:
                self.stderr.write(f"Public id {pid} does not map to a video.")
            for rid in missing_real_inputs:
                self.stderr.write(f"Video not found for real id {rid}.")
            raise CommandError("No valid videos to inspect.")

        summary: dict[str, dict[str, int]] = {
            res: defaultdict(int) for res in resolutions  # type: ignore[arg-type]
        }

        for real_id in target_real_ids:
            header_parts = [f"real: {real_id}"]
            public_ids = public_mapping.get(real_id)
            if public_ids:
                header_parts.insert(
                    0, f"public: {', '.join(str(pid) for pid in public_ids)}"
                )
            self.stdout.write(" ".join(header_parts))

            for res in resolutions:
                status, ts_count = self._resolution_status(real_id, res)
                summary[res][status] += 1
                if status == "OK":
                    line = f"  {res:<6} OK ({ts_count} ts)"
                elif status == "EMPTY":
                    line = f"  {res:<6} EMPTY"
                else:
                    line = f"  {res:<6} MISSING"
                self.stdout.write(line)

        for res in resolutions:
            res_summary = summary[res]
            ok = res_summary.get("OK", 0)
            empty = res_summary.get("EMPTY", 0)
            missing = res_summary.get("MISSING", 0)
            self.stdout.write(f"{res}: OK {ok} | EMPTY {empty} | MISSING {missing}")

        if invalid_publics:
            self.stderr.write(
                f"Ignored invalid public id(s): {', '.join(str(pid) for pid in invalid_publics)}"
            )
        if missing_real_inputs:
            self.stderr.write(
                f"Ignored missing real id(s): {', '.join(str(rid) for rid in missing_real_inputs)}"
            )
        if invalid_publics or missing_real_inputs:
            raise CommandError("Completed with invalid identifiers.")

    def _resolution_status(self, real_id: int, resolution: str) -> tuple[str, int]:
        """Determine rendition status for the given video/resolution.

        Returns a tuple of (status, ts_count) where status is one of OK, EMPTY, or MISSING.
        """
        rendition_dir = Path(settings.MEDIA_ROOT) / "hls" / str(real_id) / resolution
        manifest_path = rendition_dir / "index.m3u8"
        if not manifest_path.exists():
            return "MISSING", 0
        try:
            if manifest_path.stat().st_size <= 8:
                return "MISSING", 0
        except OSError:
            return "MISSING", 0

        ts_count = sum(1 for item in rendition_dir.glob("*.ts") if item.is_file())
        if ts_count <= 0:
            return "EMPTY", ts_count
        return "OK", ts_count
