"""Command to enqueue explicit transcode jobs for selected videos."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from jobs.domain import services as job_services
from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id
from videos.domain.utils import is_stub_manifest, resolve_source_path


def _flatten(values: Sequence[Sequence[int]] | None) -> list[int]:
    result: list[int] = []
    if not values:
        return result
    for group in values:
        result.extend(group)
    return result


def _unique(items: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


class Command(BaseCommand):
    """Management command that schedules renditions for a curated set of IDs."""

    help = "Enqueue HLS transcode jobs for the specified videos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--public",
            action="append",
            nargs="+",
            type=int,
            dest="public_ids",
            help="Frontend public IDs (ordinal numbers) to resolve and enqueue.",
        )
        parser.add_argument(
            "--real",
            action="append",
            nargs="+",
            type=int,
            dest="real_ids",
            help="Real video primary keys to enqueue directly.",
        )
        parser.add_argument(
            "--res",
            default="480p",
            choices=["480p", "720p", "1080p"],
            help="Target resolution to enqueue (default: 480p).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Display the actions without enqueuing any jobs.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rebuild renditions even when an existing manifest is present.",
        )

    def handle(self, *args, **options):
        """Inspect requested identifiers and enqueue/skip per CLI options."""
        parsed = self._parse_options(options)
        self._perform_enqueue(**parsed)

    def _parse_options(self, options) -> dict[str, object]:
        """Validate CLI options and normalise identifier lists."""
        public_inputs = _flatten(options.get("public_ids"))
        real_inputs = _flatten(options.get("real_ids"))
        resolution: str = options["res"]
        dry_run: bool = options["dry_run"]
        force: bool = options["force"]

        if not public_inputs and not real_inputs:
            raise CommandError("Provide at least one --public or --real identifier.")
        return {
            "public_inputs": public_inputs,
            "real_inputs": real_inputs,
            "resolution": resolution,
            "dry_run": dry_run,
            "force": force,
        }

    def _perform_enqueue(
        self,
        *,
        public_inputs: list[int],
        real_inputs: list[int],
        resolution: str,
        dry_run: bool,
        force: bool,
    ) -> None:
        """Execute the enqueue logic using the parsed options."""
        targets = self._resolve_targets(public_inputs, real_inputs)
        action_prefix = "DRY-RUN: would queue" if dry_run else "Queued"

        if dry_run:
            self._report_dry_run(
                targets["target_real_ids"],
                resolution,
                action_prefix,
                targets["public_mappings"],
                real_inputs,
            )
            return

        enqueued, skipped, failures = self._enqueue_real_ids(
            targets["target_real_ids"],
            resolution,
            targets["videos"],
            force,
        )
        self._print_summary(
            enqueued,
            skipped,
            failures,
            resolution,
            action_prefix,
            targets["public_mappings"],
            real_inputs,
        )

    def _resolve_targets(
        self, public_inputs: list[int], real_inputs: list[int]
    ) -> dict[str, object]:
        """Resolve public/real identifiers into concrete targets and videos."""
        public_mappings: list[tuple[int, int]] = []
        resolved_real_ids: list[int] = []

        for public_id in public_inputs:
            try:
                real_id = resolve_public_id(public_id)
            except Video.DoesNotExist as exc:
                raise CommandError(
                    f"Public id {public_id} does not map to a video."
                ) from exc
            public_mappings.append((public_id, real_id))
            resolved_real_ids.append(real_id)

        if real_inputs:
            existing_ids = set(
                Video.objects.filter(pk__in=set(real_inputs)).values_list(
                    "pk", flat=True
                )
            )
            missing = [
                real_id for real_id in real_inputs if real_id not in existing_ids
            ]
            if missing:
                missing_str = ", ".join(str(mid) for mid in missing)
                raise CommandError(f"Video(s) not found for real id(s): {missing_str}")

        target_real_ids = _unique(resolved_real_ids + real_inputs)

        if not target_real_ids:
            self.stdout.write("No videos to process.")
            return {
                "target_real_ids": [],
                "videos": {},
                "public_mappings": public_mappings,
            }

        videos = {
            video.pk: video for video in Video.objects.filter(pk__in=target_real_ids)
        }
        missing_videos = [vid for vid in target_real_ids if vid not in videos]
        if missing_videos:
            raise CommandError(
                f"Video(s) not found for real id(s): {', '.join(str(v) for v in missing_videos)}"
            )

        return {
            "target_real_ids": target_real_ids,
            "videos": videos,
            "public_mappings": public_mappings,
        }

    def _report_dry_run(
        self,
        target_real_ids: list[int],
        resolution: str,
        action_prefix: str,
        public_mappings: list[tuple[int, int]],
        real_inputs: list[int],
    ) -> None:
        """Print dry-run diagnostics for the selected targets."""
        for real_id in target_real_ids:
            manifest_path = self._manifest_path(real_id, resolution)
            if manifest_path.exists():
                status = "stub" if is_stub_manifest(manifest_path) else "existing"
            else:
                status = "missing"
            self.stdout.write(f"{action_prefix} {resolution} for {real_id} ({status})")
        if public_mappings:
            public_part = ", ".join(str(pub) for pub, _ in public_mappings)
            real_part = ", ".join(str(real) for _, real in public_mappings)
            self.stdout.write(f"Mapping: {public_part} (public) -> {real_part} (real)")
        if real_inputs:
            explicit = ", ".join(str(rid) for rid in _unique(real_inputs))
            self.stdout.write(f"Explicit real ids: {explicit}")

    def _enqueue_real_ids(
        self,
        target_real_ids: list[int],
        resolution: str,
        videos: dict[int, Video],
        force: bool,
    ) -> tuple[list[int], list[int], list[str]]:
        """Enqueue transcodes for concrete real IDs and return results."""
        enqueued: list[int] = []
        skipped: list[int] = []
        failures: list[str] = []

        for real_id in target_real_ids:
            manifest_path = self._manifest_path(real_id, resolution)
            rendition_dir = manifest_path.parent
            manifest_exists = manifest_path.exists()
            stub_manifest = manifest_exists and is_stub_manifest(manifest_path)

            if manifest_exists and not stub_manifest and not force:
                skipped.append(real_id)
                continue

            video = videos[real_id]
            checked_paths: list[Path] = []
            source_path = resolve_source_path(video, checked_paths=checked_paths)
            if not source_path:
                checked_text = ", ".join(str(path) for path in checked_paths) or "none"
                failures.append(
                    f"Video {real_id}: no source found. Checked: {checked_text}"
                )
                continue

            if force and manifest_exists and not stub_manifest or stub_manifest:
                self._purge_rendition_dir(rendition_dir)

            try:
                job_services.enqueue_transcode(real_id, target_resolutions=[resolution])
            except Exception as exc:
                failures.append(f"Video {real_id}: {exc}")
            else:
                enqueued.append(real_id)

        return enqueued, skipped, failures

    def _print_summary(
        self,
        enqueued: list[int],
        skipped: list[int],
        failures: list[str],
        resolution: str,
        action_prefix: str,
        public_mappings: list[tuple[int, int]],
        real_inputs: list[int],
    ) -> None:
        """Emit final summary lines and raise on failures."""
        if failures:
            for message in failures:
                self.stderr.write(message)
            raise CommandError(f"Could not enqueue {len(failures)} job(s).")

        if enqueued:
            self.stdout.write(
                f"{action_prefix} {resolution} for real ids: {', '.join(str(rid) for rid in enqueued)}"
            )
        if skipped:
            self.stdout.write(
                f"Skipped existing renditions: {', '.join(str(rid) for rid in skipped)}"
            )
        if public_mappings:
            public_part = ", ".join(str(pub) for pub, _ in public_mappings)
            real_part = ", ".join(str(real) for _, real in public_mappings)
            self.stdout.write(
                f"{action_prefix} {resolution} for: {public_part} (public) -> {real_part} (real)"
            )
        if real_inputs:
            explicit = ", ".join(str(rid) for rid in _unique(real_inputs))
            self.stdout.write(
                f"{action_prefix} {resolution} for explicit real ids: {explicit}"
            )

    def _manifest_path(self, real_id: int, resolution: str) -> Path:
        return (
            Path(settings.MEDIA_ROOT) / "hls" / str(real_id) / resolution / "index.m3u8"
        )

    def _purge_rendition_dir(self, rendition_dir: Path) -> None:
        if not rendition_dir.exists():
            return
        manifest_path = rendition_dir / "index.m3u8"
        try:
            if manifest_path.exists():
                manifest_path.unlink()
        except OSError:
            pass
        for segment in rendition_dir.glob("*.ts"):
            try:
                segment.unlink()
            except OSError:
                continue
