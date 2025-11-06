from __future__ import annotations

from pathlib import Path
import shutil

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from videos.domain.models import Video, VideoCategory
from videos.domain.services_autotranscode import schedule_default_transcodes


class Command(BaseCommand):
    help = "Upload a local video and trigger background transcoding."

    def add_arguments(self, parser):
        parser.add_argument(
            "source_path",
            help="Path to the local video file to ingest.",
        )
        parser.add_argument(
            "--title",
            help="Optional video title. Defaults to the file name without extension.",
        )
        parser.add_argument(
            "--category",
            help="Optional category. Allowed values: "
            + ", ".join(value for value in VideoCategory.values if value),
        )
        parser.add_argument(
            "--owner",
            help="Optional owner email address. Must match an existing user.",
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Mark the created video as published.",
        )
        parser.add_argument(
            "--move",
            action="store_true",
            help="Move the source file instead of copying it.",
        )
        parser.add_argument(
            "--skip-transcode",
            action="store_true",
            dest="skip_transcode",
            help="Create the video and upload the source without scheduling transcodes.",
        )

    def handle(self, *args, **options):
        source_path = self._validate_source_path(options["source_path"])
        title = self._determine_title(options.get("title"), source_path)
        category_value = self._normalize_category(options.get("category"))
        owner = self._resolve_owner(options.get("owner"))
        publish = bool(options.get("publish"))
        move_file = bool(options.get("move"))
        skip_transcode = bool(options.get("skip_transcode"))

        video = None
        target_path: Path | None = None
        transfer_mode: str | None = None

        try:
            video = Video.objects.create(
                title=title,
                description="",
                thumbnail_url="",
                category=category_value,
                owner=owner,
                is_published=publish,
            )
            target_path = self._prepare_destination(video.pk)
            transfer_mode = self._transfer_file(source_path, target_path, move_file)
        except CommandError:
            self._cleanup_failed_upload(video, target_path)
            raise
        except Exception as exc:
            self._cleanup_failed_upload(video, target_path)
            raise CommandError(f"Upload failed: {exc}") from exc

        self.stdout.write(f'Video created: id={video.id}, title="{video.title}"')
        self.stdout.write(f"Source: {target_path} ({transfer_mode})")

        if skip_transcode:
            self.stdout.write("Transcode skipped (by flag).")
            return

        try:
            schedule_default_transcodes(video.id)
        except Exception as exc:
            raise CommandError(f"Failed to schedule transcodes: {exc}") from exc

        self.stdout.write("Transcode queued: 480p, 720p")

    def _validate_source_path(self, raw_path: str | None) -> Path:
        if not raw_path:
            raise CommandError("Source path is required.")
        candidate = Path(raw_path).expanduser()
        if not candidate.exists():
            raise CommandError(f"Source file not found: {candidate}")
        if not candidate.is_file():
            raise CommandError(f"Source path is not a file: {candidate}")
        return candidate

    def _determine_title(self, provided: str | None, source_path: Path) -> str:
        if provided:
            title = provided.strip()
            if title:
                return title
        stem = source_path.stem.strip()
        if stem:
            return stem
        fallback = source_path.name.strip()
        if fallback:
            return fallback
        raise CommandError("Unable to derive a title. Provide --title explicitly.")

    def _normalize_category(self, raw: str | None) -> str:
        if raw is None:
            return ""
        normalized = raw.strip().lower()
        if not normalized:
            return ""
        allowed = {value.lower(): value for value in VideoCategory.values if value}
        match = allowed.get(normalized)
        if match is None:
            choices = ", ".join(sorted(allowed.values()))
            raise CommandError(f"Invalid category '{raw}'. Choose one of: {choices}")
        return match

    def _resolve_owner(self, email: str | None):
        if not email:
            return None
        trimmed = email.strip()
        if not trimmed:
            raise CommandError("Owner email cannot be blank.")
        user_model = get_user_model()
        try:
            return user_model.objects.get(email=trimmed)
        except user_model.DoesNotExist as exc:
            raise CommandError("Owner not found") from exc
        except user_model.MultipleObjectsReturned as exc:
            raise CommandError("Multiple users found for the provided email.") from exc

    def _prepare_destination(self, video_id: int) -> Path:
        media_root = getattr(settings, "MEDIA_ROOT", None)
        if not media_root:
            raise CommandError("MEDIA_ROOT is not configured.")
        sources_dir = Path(media_root).expanduser() / "sources"
        try:
            sources_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandError(f"Could not create sources directory: {exc}") from exc
        target_path = sources_dir / f"{video_id}.mp4"
        if target_path.exists():
            raise CommandError(f"Target already exists: {target_path}")
        return target_path

    def _transfer_file(self, source_path: Path, target_path: Path, move_file: bool) -> str:
        try:
            source_real = source_path.resolve(strict=True)
        except FileNotFoundError:
            source_real = source_path
        target_real = target_path.resolve()
        if source_real == target_real:
            raise CommandError("Source and destination paths are identical.")

        if move_file:
            try:
                shutil.move(str(source_path), str(target_path))
            except (shutil.Error, OSError) as exc:
                raise CommandError(f"Could not move file: {exc}") from exc
            return "moved"

        try:
            shutil.copy2(str(source_path), str(target_path))
        except (shutil.Error, OSError) as exc:
            raise CommandError(f"Could not copy file: {exc}") from exc
        return "copied"

    def _cleanup_failed_upload(self, video: Video | None, target_path: Path | None) -> None:
        if target_path and target_path.exists():
            try:
                target_path.unlink()
            except OSError:
                pass
        if video and video.pk:
            video.delete()
