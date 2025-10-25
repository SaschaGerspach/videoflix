from django.contrib import admin
from django.contrib import messages

from jobs.domain import services as job_services
from videos.domain.models import Video


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    actions = ["transcode_missing_profiles"]
    list_display = ("id", "title", "owner", "is_published", "transcode_status")
    list_filter = ("is_published", "owner")
    search_fields = ("title", "owner__username")
    ordering = ("-id",)
    readonly_fields = ("transcode_status",)

    def transcode_status(self, obj):
        status = job_services.get_transcode_status(obj.id)
        state = status.get("state", "unknown")
        message = status.get("message")
        return state if not message else f"{state}: {message}"

    transcode_status.short_description = "Transcode Status"

    def transcode_missing_profiles(self, request, queryset):
        for video in queryset:
            video_id = video.id
            if job_services.is_transcode_locked(video_id):
                self.message_user(
                    request,
                    f"Video {video_id}: Transcode skipped because a job is already running.",
                    level=messages.WARNING,
                )
                continue

            missing = [
                resolution
                for resolution in job_services.ALLOWED_TRANSCODE_PROFILES
                if not (job_services.get_transcode_output_dir(video_id, resolution) / "index.m3u8").exists()
            ]

            if not missing:
                self.message_user(
                    request,
                    f"Video {video_id}: All profiles already exist.",
                    level=messages.INFO,
                )
                continue

            try:
                job_services.enqueue_transcode(video_id, target_resolutions=missing)
            except Exception as exc:
                self.message_user(
                    request,
                    f"Video {video_id}: Failed to enqueue transcode – {exc}",
                    level=messages.ERROR,
                )
                continue

            self.message_user(
                request,
                f"Video {video_id}: Enqueued transcode for {', '.join(missing)}.",
                level=messages.SUCCESS,
            )

    transcode_missing_profiles.short_description = "Transcode missing profiles"
