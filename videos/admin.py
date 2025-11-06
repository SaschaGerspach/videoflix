import shutil

from django.contrib import admin
from django.utils.html import format_html
from django.utils.timezone import localtime

from jobs.domain import services as job_services
from videos.domain import hls as hls_utils
from videos.domain.models import Video


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "owner",
        "is_published",
        "available_resolutions_display",
        "transcode_state_display",
        "last_modified_display",
    )
    readonly_fields = ("transcode_state_readonly", "available_resolutions_readonly")
    list_filter = ("is_published",)
    search_fields = ("title", "owner__username", "id")
    actions = ["enqueue_480p", "enqueue_720p", "enqueue_1080p", "purge_hls"]
    ordering = ("-id",)

    @staticmethod
    def _resolution_sort_key(resolution: str) -> int:
        try:
            return int(resolution.rstrip("p"))
        except ValueError:
            return 0

    def available_resolutions_display(self, obj: Video) -> str:
        resolutions = hls_utils.get_available_resolutions(obj.id)
        if not resolutions:
            return "-"
        ordered = sorted(resolutions, key=self._resolution_sort_key)
        return ", ".join(ordered)

    available_resolutions_display.short_description = "Renditions"

    def available_resolutions_readonly(self, obj: Video) -> str:
        return self.available_resolutions_display(obj)

    available_resolutions_readonly.short_description = "Renditions"

    def transcode_state_display(self, obj: Video) -> str:
        try:
            status = job_services.get_transcode_status(obj.id)
        except Exception:
            status = {"state": "unknown", "message": None}

        state = status.get("state", "unknown")
        message = status.get("message")
        icon_map = {
            "ready": "[ready]",
            "processing": "[processing]",
            "failed": "[failed]",
        }
        icon = icon_map.get(state, "[state]")
        label = message or state
        return format_html("{} {}", icon, label)

    transcode_state_display.short_description = "Transcode"

    def transcode_state_readonly(self, obj: Video) -> str:
        return self.transcode_state_display(obj)

    transcode_state_readonly.short_description = "Transcode"

    def last_modified_display(self, obj: Video) -> str:
        timestamp = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
        if not timestamp:
            return "-"
        return localtime(timestamp).strftime("%Y-%m-%d %H:%M")

    last_modified_display.short_description = "Updated"

    def _queue_resolution(self, request, queryset, resolution: str) -> None:
        queued = failed = skipped_locked = skipped_existing = 0
        for video in queryset:
            available = set(hls_utils.get_available_resolutions(video.id))
            if resolution in available:
                skipped_existing += 1
                continue
            if job_services.is_transcode_locked(video.id):
                skipped_locked += 1
                continue
            try:
                job_services.enqueue_transcode(video.id, target_resolutions=[resolution])
            except Exception:
                failed += 1
            else:
                queued += 1
        parts = [f"Queued {resolution} for {queued} video(s)."]
        if skipped_locked:
            parts.append(f"Skipped (locked): {skipped_locked}")
        if skipped_existing:
            parts.append(f"Skipped (exists): {skipped_existing}")
        if failed:
            parts.append(f"Failures: {failed}")
        self.message_user(request, " ".join(parts))

    @admin.action(description="Enqueue 480p transcode")
    def enqueue_480p(self, request, queryset):
        self._queue_resolution(request, queryset, "480p")

    @admin.action(description="Enqueue 720p transcode")
    def enqueue_720p(self, request, queryset):
        self._queue_resolution(request, queryset, "720p")

    @admin.action(description="Enqueue 1080p transcode")
    def enqueue_1080p(self, request, queryset):
        self._queue_resolution(request, queryset, "1080p")

    @admin.action(description="Purge HLS renditions")
    def purge_hls(self, request, queryset):
        removed = failed = 0
        for video in queryset:
            target_dir = hls_utils.hls_dir(video.id)
            if not target_dir.exists():
                continue
            try:
                shutil.rmtree(target_dir)
            except OSError:
                failed += 1
            else:
                removed += 1
        self.message_user(
            request,
            f"Purged {removed} folder(s). Failures: {failed}.",
        )
