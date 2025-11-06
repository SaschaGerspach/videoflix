from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from videos.domain.utils import ensure_hls_dir

MANIFEST_TEMPLATE = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:3
#EXT-X-PLAYLIST-TYPE:VOD
{segments}
#EXT-X-ENDLIST
"""


class Command(BaseCommand):
    help = "Seed demo HLS renditions for quick development previews."

    def add_arguments(self, parser):
        parser.add_argument(
            "--real",
            nargs="+",
            type=int,
            dest="real_ids",
            help="Real video primary keys to seed.",
        )
        parser.add_argument(
            "--res",
            default="480p",
            choices=["480p", "720p", "1080p"],
            help="Resolution to seed (default: 480p).",
        )
        parser.add_argument(
            "--allow-no-source",
            action="store_true",
            default=True,
            help="Allow seeding when no source file exists (default: allowed).",
        )
        parser.add_argument(
            "--require-source",
            action="store_false",
            dest="allow_no_source",
            help="Fail when the expected source file is missing.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Purge existing renditions before seeding.",
        )

    def handle(self, *args, **options):
        real_ids: list[int] | None = options.get("real_ids")
        resolution: str = options["res"]
        allow_no_source: bool = options["allow_no_source"]
        force: bool = options["force"]

        if not real_ids:
            raise CommandError("Provide at least one --real identifier.")

        failures: list[str] = []
        for real_id in real_ids:
            try:
                self._seed_rendition(real_id, resolution, allow_no_source, force)
            except Exception as exc:  # pragma: no cover - aggregated reporting
                failures.append(f"{real_id}: {exc}")

        if failures:
            for line in failures:
                self.stderr.write(line)
            raise CommandError(f"Failed to seed {len(failures)} video(s).")

    def _seed_rendition(
        self,
        real_id: int,
        resolution: str,
        allow_no_source: bool,
        force: bool,
    ) -> None:
        media_root = Path(settings.MEDIA_ROOT)
        source_path = media_root / "sources" / f"{real_id}.mp4"
        if not source_path.exists() and not allow_no_source:
            raise ValueError(f"no source found at {source_path}")

        if force:
            rendition_dir = self._safe_purge_hls_dir(real_id, resolution)
            self.stdout.write(f"Purged {real_id}/{resolution} wegen --force")
        else:
            rendition_dir = ensure_hls_dir(real_id, resolution)

        seeds = ["index.m3u8", "000.ts", "001.ts", "002.ts"]
        for seed in seeds:
            path = rendition_dir / seed
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

        segments = []
        for index in range(3):
            segment_name = f"{index:03d}.ts"
            segment_path = rendition_dir / segment_name
            payload = os.urandom(256)
            segment_path.write_bytes(payload)
            segments.append(f"#EXTINF:2.0,\n{segment_name}")

        manifest_content = MANIFEST_TEMPLATE.format(segments="\n".join(segments)).strip() + "\n"
        manifest_path = rendition_dir / "index.m3u8"
        manifest_path.write_text(manifest_content, encoding="utf-8")

        self.stdout.write(f"Seeded {real_id}/{resolution} (3 segments)")

    def _safe_purge_hls_dir(self, real_id: int, resolution: str) -> Path:
        base = Path(settings.MEDIA_ROOT).resolve()
        target = (base / "hls" / str(real_id) / resolution).resolve()
        base_hls = (base / "hls").resolve()

        try:
            target.relative_to(base_hls)
        except ValueError as exc:
            raise CommandError(f"Unsafe purge target: {target}") from exc

        if target.exists():
            for item in target.glob("*"):
                try:
                    if item.is_dir():
                        self._remove_tree(item)
                    else:
                        item.unlink()
                except OSError as exc:
                    raise CommandError(f"Failed to purge {item}: {exc}") from exc
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _remove_tree(self, path: Path) -> None:
        for child in path.glob("*"):
            if child.is_dir():
                self._remove_tree(child)
            else:
                child.unlink()
        path.rmdir()
