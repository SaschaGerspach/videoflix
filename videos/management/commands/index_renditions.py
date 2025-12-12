from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id
from videos.domain.services_index import fs_rendition_exists, index_existing_rendition


def _allowed_resolutions() -> tuple[str, ...]:
    allowed = getattr(
        settings,
        "ALLOWED_RENDITIONS",
        getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p")),
    )
    return tuple(allowed)


class Command(BaseCommand):
    """Index existing filesystem renditions into database VideoStream/VideoSegment."""

    help = "Index existing HLS renditions from the filesystem into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--real",
            nargs="+",
            type=int,
            dest="real_ids",
            help="Process specific video primary keys.",
        )
        parser.add_argument(
            "--public",
            nargs="+",
            type=int,
            dest="public_ids",
            help="Process public video identifiers.",
        )
        parser.add_argument(
            "--res",
            nargs="+",
            dest="resolutions",
            choices=_allowed_resolutions(),
            help="Limit processing to specific resolutions.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="scan_all",
            help="Scan MEDIA_ROOT/hls for available renditions.",
        )

    def handle(self, *args, **options):
        """Orchestrate option parsing, target discovery, indexing, and reporting."""
        parsed = self._parse_options(options)
        targets = self._resolve_targets(
            parsed["real_ids"],
            parsed["public_ids"],
            parsed["scan_all"],
            parsed["resolution_filter"],
        )
        if not targets:
            self._print_no_targets()
            return

        summary = self._run_indexing(targets)
        self._print_summary(**summary)

    def _parse_options(self, options) -> dict[str, object]:
        """Extract CLI options and derive the resolution filter."""
        real_ids = options.get("real_ids") or []
        public_ids = options.get("public_ids") or []
        scan_all = bool(options.get("scan_all"))
        if not (real_ids or public_ids or scan_all):
            raise CommandError("Provide --real, --public, or --all.")

        resolutions = options.get("resolutions")
        if not resolutions:
            resolutions = _allowed_resolutions()
        resolution_filter = set(resolutions)
        return {
            "real_ids": real_ids,
            "public_ids": public_ids,
            "scan_all": scan_all,
            "resolution_filter": resolution_filter,
        }

    def _resolve_targets(
        self,
        real_ids: Iterable[int],
        public_ids: Iterable[int],
        scan_all: bool,
        resolution_filter: set[str],
    ) -> set[tuple[int, str]]:
        """Determine which (video, resolution) pairs should be indexed."""
        targets: set[tuple[int, str]] = set()
        targets.update(self._expand_real_ids(real_ids, resolution_filter))
        targets.update(self._expand_public_ids(public_ids, resolution_filter))
        if scan_all:
            targets.update(self._discover_all(resolution_filter))
        return targets

    def _run_indexing(self, targets: set[tuple[int, str]]) -> dict[str, int]:
        """Index all target renditions and return counts for summary output."""
        ok = 0
        updated = 0
        missing = 0

        for real_id, resolution in sorted(targets):
            state = self._index_single_target(real_id, resolution)
            if state == "missing":
                missing += 1
            elif state == "updated":
                updated += 1
            else:
                ok += 1

        return {"ok": ok, "updated": updated, "missing": missing}

    def _index_single_target(self, real_id: int, resolution: str) -> str:
        """Index one rendition and return its resulting state label."""
        exists, manifest_path, segment_paths = fs_rendition_exists(real_id, resolution)
        if not exists:
            self.stdout.write(f"missing {real_id}/{resolution}")
            return "missing"

        result = index_existing_rendition(real_id, resolution)
        segments = result.get("segments") or len(segment_paths)
        total_bytes = result.get("bytes", 0)

        if result.get("created") or result.get("updated"):
            state = "updated"
        else:
            state = "ok"

        self.stdout.write(
            f"{state:>7} {real_id}/{resolution} segments={segments} bytes={total_bytes}"
        )
        return state

    def _print_no_targets(self) -> None:
        """Emit the no-targets messages when nothing matched the criteria."""
        self.stdout.write("No renditions matched the selected criteria.")
        self.stdout.write("summary ok=0 updated=0 missing=0")

    def _print_summary(self, ok: int, updated: int, missing: int) -> None:
        """Emit the final summary line."""
        self.stdout.write(f"summary ok={ok} updated={updated} missing={missing}")

    def _expand_real_ids(
        self,
        real_ids: Iterable[int],
        resolution_filter: set[str],
    ) -> set[tuple[int, str]]:
        targets: set[tuple[int, str]] = set()
        for real_id in real_ids:
            for resolution in resolution_filter:
                targets.add((int(real_id), resolution))
        return targets

    def _expand_public_ids(
        self,
        public_ids: Iterable[int],
        resolution_filter: set[str],
    ) -> set[tuple[int, str]]:
        targets: set[tuple[int, str]] = set()
        for public_id in public_ids:
            try:
                real_id = resolve_public_id(int(public_id))
            except Video.DoesNotExist:
                self.stderr.write(f"Skipping public id {public_id}: no matching video.")
                continue
            for resolution in resolution_filter:
                targets.add((real_id, resolution))
        return targets

    def _discover_all(self, resolution_filter: set[str]) -> set[tuple[int, str]]:
        base = Path(settings.MEDIA_ROOT) / "hls"
        if not base.exists():
            return set()

        targets: set[tuple[int, str]] = set()
        for real_dir in base.iterdir():
            if not real_dir.is_dir():
                continue
            try:
                real_id = int(real_dir.name)
            except ValueError:
                continue
            for rendition_dir in real_dir.iterdir():
                if not rendition_dir.is_dir():
                    continue
                resolution = rendition_dir.name
                if resolution_filter and resolution not in resolution_filter:
                    continue
                targets.add((real_id, resolution))
        return targets
