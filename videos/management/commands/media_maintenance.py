"""Utility management command for scanning, healing, and pruning HLS assets."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from collections.abc import Iterable, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from jobs.domain import services as transcode_services
from videos.domain import services as video_services, thumbs as thumb_utils
from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id
from videos.domain.services_autotranscode import select_rungs_from_source
from videos.domain.utils import is_stub_manifest

DEFAULT_RESOLUTIONS = ("480p", "720p", "1080p")


class Command(BaseCommand):
    """Entry point for HLS maintenance tasks such as scanning, healing, and pruning."""

    help = "Run media maintenance tasks (scan, heal, enqueue missing renditions, prune orphan files)."

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
        parser.add_argument(
            "--scan",
            action="store_true",
            help="Display rendition status without modifying files.",
        )
        parser.add_argument(
            "--heal",
            action="store_true",
            help="Repair stub manifests (and regenerate thumbnails for affected videos).",
        )
        parser.add_argument(
            "--enqueue-missing",
            action="store_true",
            help="Enqueue missing renditions using select_rungs_from_source().",
        )
        parser.add_argument(
            "--prune-orphans",
            action="store_true",
            help="Delete MEDIA_ROOT/hls/* directories that no longer have a matching Video record.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required to perform destructive actions such as --prune-orphans.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON instead of human readable output.",
        )

    def handle(self, *args, **options):
        """Execute the requested maintenance actions and emit reports."""
        parsed = self._parse_options(options)
        result, messages = self._run_actions(
            actions=parsed["actions"],
            videos=parsed["videos"],
            resolutions=parsed["resolutions"],
            output_json=parsed["output_json"],
            confirm=parsed["confirm"],
        )
        self._print_report(result, messages, parsed["output_json"])

    def _parse_options(self, options) -> dict[str, object]:
        """Validate selected actions and prepare common parameters."""
        actions = self._parse_actions(options)
        resolutions = self._resolve_resolutions(options.get("resolutions"))
        videos = self._load_videos(options.get("public_ids"), options.get("real_ids"))
        return {
            "actions": actions,
            "resolutions": resolutions,
            "videos": videos,
            "output_json": bool(options.get("json")),
            "confirm": bool(options.get("confirm")),
        }

    def _parse_actions(self, options) -> dict[str, bool]:
        """Extract requested actions and ensure at least one is selected."""
        actions = {
            "scan": options.get("scan"),
            "heal": options.get("heal"),
            "enqueue": options.get("enqueue_missing"),
            "prune": options.get("prune_orphans"),
        }
        if not any(actions.values()):
            raise CommandError(
                "Select at least one action: --scan/--heal/--enqueue-missing/--prune-orphans."
            )
        return actions

    def _run_actions(
        self,
        *,
        actions: dict[str, bool],
        videos: dict[int, Video],
        resolutions: Sequence[str],
        output_json: bool,
        confirm: bool,
    ) -> tuple[dict[str, object], list[str]]:
        """Execute enabled actions and collect structured results and messages."""
        result: dict[str, object] = {}
        messages: list[str] = []

        if actions["scan"]:
            scan_data = self._action_scan(videos, resolutions)
            result["scan"] = scan_data
            if not output_json:
                messages.extend(self._format_scan_messages(scan_data))

        if actions["heal"]:
            heal_data = self._action_heal(videos, resolutions)
            result["heal"] = heal_data
            if not output_json:
                messages.extend(self._format_heal_messages(heal_data))

        if actions["enqueue"]:
            enqueue_data = self._action_enqueue(videos, resolutions)
            result["enqueue_missing"] = enqueue_data
            if not output_json:
                messages.extend(self._format_enqueue_messages(enqueue_data))

        if actions["prune"]:
            prune_data = self._action_prune_orphans(confirm)
            result["prune_orphans"] = prune_data
            if not output_json:
                messages.extend(self._format_prune_messages(prune_data, confirm))

        return result, messages

    def _print_report(
        self, result: dict[str, object], messages: list[str], output_json: bool
    ) -> None:
        """Emit either JSON output or the collected human-readable messages."""
        if output_json:
            self.stdout.write(json.dumps(result, indent=2))
            return
        if not messages:
            messages.append("media_maintenance completed; no actions required.")
        for line in messages:
            self.stdout.write(line)

    def _resolve_resolutions(self, raw) -> list[str]:
        """Flatten the --res arguments into a unique resolution list."""
        if not raw:
            return list(DEFAULT_RESOLUTIONS)
        flattened: list[str] = []
        for chunk in raw:
            flattened.extend(chunk)
        return list(dict.fromkeys(flattened)) or list(DEFAULT_RESOLUTIONS)

    def _load_videos(
        self,
        public_inputs: Sequence[Sequence[int]] | None,
        real_inputs: Sequence[Sequence[int]] | None,
    ) -> dict[int, Video]:
        """Resolve public + real IDs into a video map, raising when identifiers are invalid."""
        public_ids, real_ids = self._collect_ids(public_inputs, real_inputs)

        if not public_ids and not real_ids:
            return {video.pk: video for video in Video.objects.all()}

        resolved_reals, invalid_publics = self._resolve_public_ids(public_ids)
        self._raise_on_invalid_publics(invalid_publics)

        combined_ids = self._combined_ids(resolved_reals, real_ids)
        videos, missing = self._fetch_videos(combined_ids)
        self._raise_on_missing_reals(missing)
        return videos

    def _collect_ids(
        self,
        public_inputs: Sequence[Sequence[int]] | None,
        real_inputs: Sequence[Sequence[int]] | None,
    ) -> tuple[list[int], list[int]]:
        """Flatten nested CLI inputs for public and real IDs."""
        public_ids = _flatten(public_inputs)
        real_ids = _flatten(real_inputs)
        return public_ids, real_ids

    def _resolve_public_ids(
        self, public_ids: Sequence[int]
    ) -> tuple[list[int], list[int]]:
        """Resolve public IDs to real IDs, returning resolved list and invalid entries."""
        invalid_publics: list[int] = []
        resolved_reals: list[int] = []
        for public in public_ids:
            try:
                real_id = resolve_public_id(public)
            except Video.DoesNotExist:
                invalid_publics.append(public)
                continue
            resolved_reals.append(real_id)
        return resolved_reals, invalid_publics

    def _raise_on_invalid_publics(self, invalid_publics: list[int]) -> None:
        """Raise CommandError when invalid public IDs are detected."""
        if invalid_publics:
            raise CommandError(
                f"Invalid public id(s): {', '.join(str(pid) for pid in invalid_publics)}"
            )

    def _combined_ids(
        self, resolved_reals: list[int], real_ids: list[int]
    ) -> list[int]:
        """Merge and deduplicate resolved and explicit real IDs."""
        combined_ids = _unique(resolved_reals + real_ids)
        if not combined_ids:
            raise CommandError("No valid video identifiers provided.")
        return combined_ids

    def _fetch_videos(
        self, combined_ids: list[int]
    ) -> tuple[dict[int, Video], list[int]]:
        """Fetch videos for provided IDs and return missing IDs if any."""
        videos = {
            video.pk: video for video in Video.objects.filter(pk__in=combined_ids)
        }
        missing = [vid for vid in combined_ids if vid not in videos]
        return videos, missing

    def _raise_on_missing_reals(self, missing: list[int]) -> None:
        """Raise CommandError when real IDs cannot be found."""
        if missing:
            raise CommandError(
                f"Video(s) not found for real id(s): {', '.join(str(mid) for mid in missing)}"
            )

    def _action_scan(
        self, videos: dict[int, Video], resolutions: Sequence[str]
    ) -> dict:
        """Collect rendition health metadata for the requested videos."""
        summary: dict[str, int] = defaultdict(int)
        reports: list[dict] = []
        affected: set[int] = set()

        for video_id in sorted(videos.keys()):
            res_statuses: list[dict] = []
            for resolution in resolutions:
                status, ts_count = self._resolution_status(video_id, resolution)
                summary[status] += 1
                res_statuses.append(
                    {"resolution": resolution, "status": status, "segments": ts_count}
                )
                if status != "OK":
                    affected.add(video_id)
            reports.append({"video_id": video_id, "resolutions": res_statuses})

        return {
            "summary": dict(summary),
            "videos": reports,
            "affected_video_ids": sorted(affected),
        }

    def _action_heal(
        self, videos: dict[int, Video], resolutions: Sequence[str]
    ) -> dict:
        """Repair stub manifests and regenerate thumbnails when needed."""
        healed: list[dict] = []
        thumbnails: list[int] = []

        for video in videos.values():
            fixed_res: list[str] = []
            for resolution in resolutions:
                manifest_path = self._manifest_path(video.pk, resolution)
                if not manifest_path.exists():
                    continue
                try:
                    stub = is_stub_manifest(manifest_path)
                except Exception:
                    stub = False
                if not stub:
                    continue
                stream = video.streams.filter(resolution=resolution).first()
                if not stream or not stream.manifest:
                    continue
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(stream.manifest, encoding="utf-8")
                fixed_res.append(resolution)
            if fixed_res:
                healed.append({"video_id": video.pk, "resolutions": fixed_res})
                thumb_path = thumb_utils.ensure_thumbnail(video.pk)
                if thumb_path:
                    thumbnails.append(video.pk)

        return {"fixed": healed, "thumbnails": thumbnails}

    def _action_enqueue(
        self, videos: dict[int, Video], resolutions: Sequence[str]
    ) -> dict:
        """Schedule missing renditions for the supplied videos."""
        queued: list[dict] = []
        for video in videos.values():
            video_services.ensure_source_metadata(video)
            meta = video_services.extract_video_metadata(video)
            rung_list = select_rungs_from_source(meta)
            if resolutions:
                allowed = set(resolutions)
                rung_list = [res for res in rung_list if res in allowed]
            missing = self._missing_resolutions(video.pk, rung_list)
            if not missing:
                continue
            result = transcode_services.enqueue_transcode(
                video.pk, target_resolutions=missing
            )
            queued.append(
                {"video_id": video.pk, "resolutions": missing, "result": result}
            )
        return {"queued": queued}

    def _action_prune_orphans(self, confirm: bool) -> dict:
        """Remove HLS folders that no longer have matching database records."""
        base = Path(settings.MEDIA_ROOT).expanduser() / "hls"
        if not base.exists():
            return {"deleted": [], "pending": []}

        orphans: list[Path] = []
        for child in base.iterdir():
            if not child.is_dir():
                continue
            if not child.name.isdigit():
                continue
            video_id = int(child.name)
            if not Video.objects.filter(pk=video_id).exists():
                orphans.append(child)

        deleted_ids: list[int] = []
        pending_ids = [int(path.name) for path in orphans]

        if orphans and confirm:
            for folder in orphans:
                shutil.rmtree(folder, ignore_errors=True)
                deleted_ids.append(int(folder.name))
            pending_ids = []

        return {"deleted": deleted_ids, "pending": pending_ids, "confirm": confirm}

    def _resolution_status(self, video_id: int, resolution: str) -> tuple[str, int]:
        """Return the state label and segment count for a specific rendition."""
        manifest_path = self._manifest_path(video_id, resolution)
        if not manifest_path.exists():
            return "MISSING", 0
        try:
            if is_stub_manifest(manifest_path):
                return "STUB", 0
        except Exception:
            return "STUB", 0
        ts_count = sum(
            1 for item in manifest_path.parent.glob("*.ts") if item.is_file()
        )
        if ts_count <= 0:
            return "EMPTY", 0
        return "OK", ts_count

    def _manifest_path(self, video_id: int, resolution: str) -> Path:
        """Build the manifest path for a given video/resolution pair."""
        return (
            Path(settings.MEDIA_ROOT).expanduser()
            / "hls"
            / str(video_id)
            / resolution
            / "index.m3u8"
        )

    def _missing_resolutions(self, video_id: int, targets: Sequence[str]) -> list[str]:
        """List expected renditions that do not currently exist on disk."""
        missing: list[str] = []
        for resolution in targets:
            manifest_path = self._manifest_path(video_id, resolution)
            if not manifest_path.exists():
                missing.append(resolution)
                continue
            try:
                stub = is_stub_manifest(manifest_path)
            except Exception:
                stub = False
            if stub:
                missing.append(resolution)
        return missing

    def _format_scan_messages(self, scan_data: dict) -> list[str]:
        """Format human-readable output for scan results."""
        summary = scan_data.get("summary", {})
        lines = ["Scan summary:"]
        for status in ("OK", "EMPTY", "STUB", "MISSING"):
            lines.append(f"  {status}: {summary.get(status, 0)}")
        affected = scan_data.get("affected_video_ids") or []
        if affected:
            lines.append(f"Affected video IDs: {', '.join(str(v) for v in affected)}")
        else:
            lines.append("All renditions healthy.")
        return lines

    def _format_heal_messages(self, heal_data: dict) -> list[str]:
        """Format human-readable output for heal results."""
        lines = []
        fixed = heal_data.get("fixed") or []
        if fixed:
            lines.append("Healed manifests:")
            for item in fixed:
                lines.append(
                    f"  video {item['video_id']}: {', '.join(item['resolutions'])}"
                )
        else:
            lines.append("No stub manifests detected.")
        thumbs = heal_data.get("thumbnails") or []
        if thumbs:
            lines.append(
                f"Generated thumbnails for: {', '.join(str(vid) for vid in thumbs)}"
            )
        return lines

    def _format_enqueue_messages(self, enqueue_data: dict) -> list[str]:
        """Format human-readable output for enqueue results."""
        queued = enqueue_data.get("queued") or []
        if not queued:
            return ["No renditions enqueued; all targets already present."]
        lines = ["Enqueued renditions:"]
        for item in queued:
            lines.append(
                f"  video {item['video_id']}: {', '.join(item['resolutions'])}"
            )
        return lines

    def _format_prune_messages(self, prune_data: dict, confirm: bool) -> list[str]:
        """Format human-readable output for prune results."""
        deleted = prune_data.get("deleted") or []
        pending = prune_data.get("pending") or []
        lines: list[str] = []
        if deleted:
            lines.append(
                f"Deleted orphan HLS folders: {', '.join(str(vid) for vid in deleted)}"
            )
        elif pending:
            if confirm:
                lines.append("No orphan folders removed.")
            else:
                lines.append(
                    "Orphan folders detected (use --confirm to delete): "
                    + ", ".join(str(vid) for vid in pending)
                )
        else:
            lines.append("No orphan HLS folders detected.")
        return lines


def _flatten(values: Sequence[Sequence[int]] | None) -> list[int]:
    """Flatten nested CLI argument lists."""
    results: list[int] = []
    if not values:
        return results
    for group in values:
        results.extend(group)
    return results


def _unique(items: Iterable[int]) -> list[int]:
    """Return an ordered list with duplicates removed."""
    seen: set[int] = set()
    ordered: list[int] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
