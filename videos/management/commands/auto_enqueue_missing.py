"""CLI helper that finds missing renditions and enqueues transcodes."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from jobs.domain import services as job_services
from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id
from videos.domain.utils import is_stub_manifest, resolve_source_path

try:
    from videos.management.commands.enqueue_transcodes import _flatten, _unique  # type: ignore
except Exception:  # pragma: no cover - fallback if helpers change

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
    """Management command that inspects videos and enqueues needed renditions."""

    help = "Check for missing HLS renditions and enqueue transcodes for the gaps."

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
            default="480p",
            choices=["480p", "720p", "1080p"],
            help="Resolution to verify/enqueue (default: 480p).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only display what would be enqueued.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Skip interactive confirmation and enqueue immediately.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rebuild renditions even when an existing manifest is present.",
        )

    def handle(self, *args, **options):
        """Process CLI arguments, display current state, and enqueue missing renditions."""
        parsed = self._parse_options(options)
        self._perform_enqueue(**parsed)

    def _parse_options(self, options) -> dict[str, object]:
        """Validate CLI options and normalise identifier lists."""
        public_inputs = _flatten(options.get("public_ids"))
        real_inputs = _flatten(options.get("real_ids"))
        resolution: str = options["res"]
        dry_run: bool = options["dry_run"]
        auto_confirm: bool = options["confirm"]
        force: bool = options["force"]

        if not public_inputs and not real_inputs:
            raise CommandError("Provide at least one --public or --real identifier.")
        return {
            "public_inputs": public_inputs,
            "real_inputs": real_inputs,
            "resolution": resolution,
            "dry_run": dry_run,
            "auto_confirm": auto_confirm,
            "force": force,
        }

    def _perform_enqueue(
        self,
        *,
        public_inputs: list[int],
        real_inputs: list[int],
        resolution: str,
        dry_run: bool,
        auto_confirm: bool,
        force: bool,
    ) -> None:
        """Execute the missing-rendition discovery and enqueue flow."""
        targets = self._resolve_targets(public_inputs, real_inputs)

        self.stdout.write(f"Checking renditions ({resolution})")
        status_map, missing_real_ids, present_real_ids = self._classify_renditions(
            targets["target_real_ids"], resolution
        )

        self._report_missing_and_present(
            missing_real_ids,
            present_real_ids,
            status_map,
            targets["public_mapping"],
            targets["invalid_publics"],
            targets["missing_real_inputs"],
        )

        selected_targets = self._build_targets(
            missing_real_ids, present_real_ids, force
        )
        if self._handle_dry_run(
            dry_run,
            selected_targets,
            targets["invalid_publics"],
            targets["missing_real_inputs"],
        ):
            return

        if not selected_targets:
            self.stdout.write(
                "All requested videos already contain the requested rendition."
            )
            if targets["invalid_publics"] or targets["missing_real_inputs"]:
                raise CommandError("Completed with invalid identifiers.")
            return

        if not auto_confirm:
            confirmed = self._confirm_or_abort(
                selected_targets,
                resolution,
                targets["invalid_publics"],
                targets["missing_real_inputs"],
            )
            if not confirmed:
                return

        enqueued, failures = self._enqueue_targets(
            selected_targets,
            resolution,
            targets["videos"],
            status_map,
            force,
        )
        self._finalise_enqueue(enqueued, failures, resolution, targets)

    def _resolve_targets(
        self, public_inputs: list[int], real_inputs: list[int]
    ) -> dict[str, object]:
        """Resolve public/real identifiers into concrete video targets."""
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
            for invalid in invalid_publics:
                self.stderr.write(f"Public id {invalid} does not map to a video.")
            for missing in missing_real_inputs:
                self.stderr.write(f"Video not found for real id {missing}.")
            raise CommandError("No valid videos to process.")

        videos = {
            video.pk: video for video in Video.objects.filter(pk__in=target_real_ids)
        }
        missing_videos = [vid for vid in target_real_ids if vid not in videos]
        if missing_videos:
            raise CommandError(
                f"Video(s) not found for real id(s): {', '.join(str(v) for v in missing_videos)}"
            )

        return {
            "invalid_publics": invalid_publics,
            "public_mapping": public_mapping,
            "missing_real_inputs": missing_real_inputs,
            "target_real_ids": target_real_ids,
            "videos": videos,
        }

    def _classify_renditions(
        self, target_real_ids: list[int], resolution: str
    ) -> tuple[dict[int, str], list[int], list[int]]:
        """Determine which renditions are missing or present for each target."""
        missing_real_ids: list[int] = []
        present_real_ids: list[int] = []
        status_map: dict[int, str] = {}

        for real_id in target_real_ids:
            status = self._resolution_status(real_id, resolution)
            status_map[real_id] = status
            if status in {"missing", "empty"}:
                missing_real_ids.append(real_id)
            else:
                present_real_ids.append(real_id)

        return status_map, missing_real_ids, present_real_ids

    def _report_missing_and_present(
        self,
        missing_real_ids: list[int],
        present_real_ids: list[int],
        status_map: dict[int, str],
        public_mapping: dict[int, list[int]],
        invalid_publics: list[int],
        missing_real_inputs: list[int],
    ) -> None:
        """Emit the same missing/present diagnostics as before."""
        if invalid_publics:
            self.stderr.write(
                f"Ignored invalid public id(s): {', '.join(str(pid) for pid in invalid_publics)}"
            )
        if missing_real_inputs:
            self.stderr.write(
                f"Ignored missing real id(s): {', '.join(str(rid) for rid in missing_real_inputs)}"
            )

        if missing_real_ids:
            parts: list[str] = []
            for rid in missing_real_ids:
                status = status_map[rid]
                label = f"{rid} ({status.upper()})" if status == "empty" else str(rid)
                parts.append(label)
            self.stdout.write(f"Missing: {', '.join(parts)}")
            missing_publics: list[int] = []
            for rid in missing_real_ids:
                missing_publics.extend(public_mapping.get(rid, []))
            if missing_publics:
                self.stdout.write(
                    "Missing public ids: "
                    + ", ".join(str(pid) for pid in missing_publics)
                    + " -> real "
                    + ", ".join(str(rid) for rid in missing_real_ids)
                )
        else:
            self.stdout.write("Missing: -")

        if present_real_ids:
            present_line = ", ".join(str(rid) for rid in present_real_ids)
            self.stdout.write(f"Already present: {present_line}")

    def _build_targets(
        self, missing_real_ids: list[int], present_real_ids: list[int], force: bool
    ) -> list[int]:
        """Combine targets based on missing/present sets and force flag."""
        targets = list(missing_real_ids)
        if force and present_real_ids:
            self.stdout.write(
                f"Force rebuild enabled; adding existing renditions: {', '.join(str(rid) for rid in present_real_ids)}"
            )
            targets = _unique(targets + present_real_ids)
        return targets

    def _handle_dry_run(
        self,
        dry_run: bool,
        targets: list[int],
        invalid_publics: list[int],
        missing_real_inputs: list[int],
    ) -> bool:
        """Return True if dry-run short-circuits execution, preserving errors."""
        if not dry_run:
            return False
        self.stdout.write("Dry-run enabled; no jobs enqueued.")
        if invalid_publics or missing_real_inputs:
            raise CommandError("Completed dry-run with invalid identifiers.")
        return True

    def _confirm_or_abort(
        self,
        targets: list[int],
        resolution: str,
        invalid_publics: list[int],
        missing_real_inputs: list[int],
    ) -> bool:
        """Prompt for confirmation unless auto-confirm was provided."""
        prompt = (
            f"Proceed with enqueue for {', '.join(str(rid) for rid in targets)} "
            f"({resolution})? [y/N] "
        )
        answer = input(prompt)
        if answer.lower() != "y":
            self.stdout.write("Aborted by user.")
            if invalid_publics or missing_real_inputs:
                raise CommandError("Aborted with invalid identifiers.")
            return False
        return True

    def _enqueue_targets(
        self,
        targets: list[int],
        resolution: str,
        videos: dict[int, Video],
        status_map: dict[int, str],
        force: bool,
    ) -> tuple[int, list[str]]:
        """Enqueue transcodes for the selected targets and return counts/errors."""
        self.stdout.write(f"Queuing transcodes for {len(targets)} video(s)...")

        enqueued = 0
        failures: list[str] = []

        for real_id in targets:
            video = videos[real_id]
            checked_paths: list[Path] = []
            source_path = resolve_source_path(video, checked_paths=checked_paths)
            if not source_path:
                checked_text = ", ".join(str(path) for path in checked_paths) or "none"
                failures.append(
                    f"Video {real_id}: no source found. Checked: {checked_text}"
                )
                continue

            rendition_dir = self._rendition_dir(real_id, resolution)
            if force or status_map.get(real_id) in {"missing", "empty"}:
                self._purge_rendition_dir(rendition_dir)

            try:
                job_services.enqueue_transcode(real_id, target_resolutions=[resolution])
            except Exception as exc:
                failures.append(f"Video {real_id}: {exc}")
            else:
                enqueued += 1

        return enqueued, failures

    def _finalise_enqueue(
        self,
        enqueued: int,
        failures: list[str],
        resolution: str,
        targets: dict[str, object],
    ) -> None:
        """Report enqueue outcomes and raise on failures or invalid identifiers."""
        if failures:
            for message in failures:
                self.stderr.write(message)
            raise CommandError(f"Failed to enqueue {len(failures)} video(s).")

        self.stdout.write(f"Done. Queued {enqueued} job(s) for {resolution}.")
        if targets["invalid_publics"] or targets["missing_real_inputs"]:
            raise CommandError("Completed with invalid identifiers.")

    def _rendition_dir(self, real_id: int, resolution: str) -> Path:
        return Path(settings.MEDIA_ROOT) / "hls" / str(real_id) / resolution

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

    def _resolution_status(self, real_id: int, resolution: str) -> str:
        """Return 'missing', 'empty', or 'ok' for the requested rendition."""
        rendition_dir = self._rendition_dir(real_id, resolution)
        manifest_path = rendition_dir / "index.m3u8"
        if not manifest_path.exists():
            return "missing"
        try:
            if is_stub_manifest(manifest_path):
                return "missing"
        except OSError:
            return "missing"
        ts_count = sum(1 for item in rendition_dir.glob("*.ts") if item.is_file())
        if ts_count <= 0:
            return "empty"
        return "ok"
