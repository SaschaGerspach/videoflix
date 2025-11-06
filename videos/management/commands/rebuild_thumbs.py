from __future__ import annotations

from django.core.management.base import BaseCommand

from videos.domain.models import Video
from videos.domain.selectors import resolve_public_id
from videos.domain import thumbs as thumb_utils


class Command(BaseCommand):
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

    def handle(self, *args, **options):
        public_ids: list[int] = options.get("public") or []
        real_ids: set[int] = set(options.get("real") or [])
        force: bool = bool(options.get("force"))

        for public_id in public_ids:
            try:
                real_ids.add(resolve_public_id(public_id))
            except Video.DoesNotExist:
                self.stderr.write(f"[skip] public id {public_id}: no matching video")

        if not real_ids:
            real_ids = set(Video.objects.values_list("id", flat=True))
            if not real_ids:
                self.stdout.write("No videos available for thumbnail rebuild.")
                return

        for real_id in sorted(real_ids):
            thumb_path = thumb_utils.get_thumbnail_path(real_id)
            if thumb_path.exists() and not force:
                self.stdout.write(f"[skip] video {real_id}: thumbnail already present")
                continue

            result = thumb_utils.ensure_thumbnail(real_id)
            if result is None:
                self.stdout.write(f"[warn] video {real_id}: thumbnail not generated")
                continue

            size = result.stat().st_size if result.exists() else 0
            self.stdout.write(f"[ok] video {real_id}: wrote {size} bytes -> {result}")
