import shutil

from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.timezone import localtime
from django.db import transaction

from jobs.domain import services as job_services
from videos.domain import hls as hls_utils, thumbs as thumb_utils
from videos.domain import services as video_services, services_autotranscode
from videos.domain.models import Video
from videos.domain.services_autotranscode import publish_and_enqueue


class VideoAdminForm(forms.ModelForm):
    source_file = forms.FileField(required=False, help_text="Optional source upload.")
    thumbnail_image = forms.ImageField(
        required=False, help_text="Optional thumbnail upload."
    )

    class Meta:
        model = Video
        fields = "__all__"
        field_order = [
            "owner",
            "title",
            "description",
            "source_file",
            "thumbnail_image",
            "category",
            "is_published",
        ]


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    form = VideoAdminForm
    fieldsets = (
        (
            "Video",
            {
                "fields": (
                    "owner",
                    "title",
                    "description",
                    "source_file",
                    "thumbnail_image",
                    "category",
                    "is_published",
                )
            },
        ),
        (
            "Status & Renditions",
            {
                "fields": (
                    "status",
                    "transcode_state_readonly",
                    "available_resolutions_readonly",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "metadata_display",
                    "width",
                    "height",
                    "duration_seconds",
                    "video_bitrate_kbps",
                    "audio_bitrate_kbps",
                    "codec_name",
                )
            },
        ),
    )

    class AvailableRenditionsFilter(admin.SimpleListFilter):
        title = "Available renditions"
        parameter_name = "available_renditions"
        valid_values = ("480p", "720p", "1080p")

        def __init__(self, request, params, model, model_admin):
            super().__init__(request, params, model, model_admin)
            selected = request.GET.getlist(self.parameter_name)
            self.selected_values = [
                value for value in selected if value in self.valid_values
            ]

        def lookups(self, request, model_admin):
            return [(value, value) for value in self.valid_values]

        def value(self):
            return self.selected_values

        def queryset(self, request, queryset):
            if not self.selected_values:
                return queryset
            qs = queryset
            for resolution in self.selected_values:
                qs = qs.filter(streams__resolution=resolution)
            return qs.distinct()

        def choices(self, changelist):
            current = set(self.selected_values)
            yield {
                "selected": not current,
                "query_string": changelist.get_query_string(
                    new_params={}, remove=[self.parameter_name]
                ),
                "display": "All",
            }
            for value, title in self.lookup_choices:
                toggled = set(current)
                if value in toggled:
                    toggled.remove(value)
                else:
                    toggled.add(value)
                if toggled:
                    ordered = sorted(
                        toggled, key=lambda item: self.valid_values.index(item)
                    )
                    query_string = changelist.get_query_string(
                        new_params={self.parameter_name: ordered}
                    )
                else:
                    query_string = changelist.get_query_string(
                        new_params={}, remove=[self.parameter_name]
                    )
                yield {
                    "selected": value in current,
                    "query_string": query_string,
                    "display": title,
                }

    class HeightRangeFilter(admin.SimpleListFilter):
        title = "Height (px)"
        parameter_name = "height_range"

        def lookups(self, request, model_admin):
            return [
                ("lt_720", "< 720"),
                ("720_1079", "720-1079"),
                ("1080_2159", "1080-2159"),
                ("gte_2160", ">= 2160"),
            ]

        def queryset(self, request, queryset):
            value = self.value()
            if value == "lt_720":
                return queryset.filter(height__lt=720)
            if value == "720_1079":
                return queryset.filter(height__gte=720, height__lte=1079)
            if value == "1080_2159":
                return queryset.filter(height__gte=1080, height__lte=2159)
            if value == "gte_2160":
                return queryset.filter(height__gte=2160)
            return queryset

    list_display = (
        "id",
        "title",
        "owner",
        "is_published",
        "available_resolutions_display",
        "transcode_state_display",
        "metadata_display",
        "last_modified_display",
    )
    readonly_fields = (
        "transcode_state_readonly",
        "available_resolutions_readonly",
        "metadata_display",
        "status",
        "width",
        "height",
        "duration_seconds",
        "video_bitrate_kbps",
        "audio_bitrate_kbps",
        "codec_name",
    )
    list_filter = ("is_published", AvailableRenditionsFilter, HeightRangeFilter)
    search_fields = ("title", "owner__username", "id")
    actions = [
        "publish_and_render_action",
        "regenerate_thumbnail_action",
        "enqueue_480p",
        "enqueue_720p",
        "enqueue_1080p",
        "reencode_1080p",
        "reencode_720p",
        "reencode_480p",
        "purge_hls",
    ]

    def save_model(self, request, obj, form, change):
        source_file = form.cleaned_data.get("source_file")
        thumbnail_image = form.cleaned_data.get("thumbnail_image")

        super().save_model(request, obj, form, change)

        if not source_file:
            if thumbnail_image:
                self._save_thumbnail(obj, thumbnail_image)
            return

        target_path = job_services.get_video_source_path(obj.pk)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as destination:
            for chunk in source_file.chunks():
                destination.write(chunk)

        video_services.ensure_source_metadata(obj)

        transaction.on_commit(
            lambda: services_autotranscode.schedule_default_transcodes(obj.pk)
        )

        if thumbnail_image:
            self._save_thumbnail(obj, thumbnail_image)

    def _save_thumbnail(self, obj: Video, thumbnail_image):
        thumb_path = thumb_utils.get_thumbnail_path(obj.pk)
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with thumb_path.open("wb") as destination:
            for chunk in thumbnail_image.chunks():
                destination.write(chunk)

        obj.thumbnail_url = thumb_utils.get_thumbnail_url(obj)
        obj.save(update_fields=["thumbnail_url"])

    def get_ordering(self, request):
        base_ordering = ["-updated_at", "-height", "title"]
        model_fields = {field.name for field in self.model._meta.get_fields()}
        resolved = [item for item in base_ordering if item.lstrip("-") in model_fields]
        if not resolved:
            resolved = ["-pk"]
        return resolved

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

    def metadata_display(self, obj: Video) -> str:
        parts: list[str] = []
        if obj.width and obj.height:
            parts.append(f"{obj.width}x{obj.height}")
        if obj.video_bitrate_kbps or obj.audio_bitrate_kbps:
            total = (obj.video_bitrate_kbps or 0) + (obj.audio_bitrate_kbps or 0)
            if total:
                parts.append(f"{total} kbps")
        if obj.duration_seconds:
            minutes, seconds = divmod(obj.duration_seconds, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            else:
                parts.append(f"{minutes:02d}:{seconds:02d}")
        return " • ".join(parts) if parts else "-"

    metadata_display.short_description = "Metadata"

    def last_modified_display(self, obj: Video) -> str:
        timestamp = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
        if not timestamp:
            return "-"
        return localtime(timestamp).strftime("%Y-%m-%d %H:%M")

    last_modified_display.short_description = "Updated"

    def _queue_resolution(
        self,
        request,
        queryset,
        resolution: str,
        *,
        force: bool = False,
        allow_existing: bool = False,
    ) -> None:
        queued = failed = skipped_locked = skipped_existing = 0
        for video in queryset:
            available = set(hls_utils.get_available_resolutions(video.id))
            if resolution in available and not allow_existing:
                skipped_existing += 1
                continue
            if job_services.is_transcode_locked(video.id):
                skipped_locked += 1
                continue
            if allow_existing:
                target_dir = hls_utils.rendition_dir(video.id, resolution)
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
            try:
                job_services.enqueue_transcode(
                    video.id,
                    target_resolutions=[resolution],
                    force=force,
                )
            except Exception:
                failed += 1
            else:
                queued += 1
        parts = [f"Queued {resolution} for {queued} video(s)."]
        if skipped_locked:
            parts.append(f"Skipped (locked): {skipped_locked}")
        if skipped_existing and not allow_existing:
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

    @admin.action(description="Publish + Render")
    def publish_and_render_action(self, request, queryset):
        successes = failures = 0
        for video in queryset:
            try:
                if not video.is_published:
                    video.is_published = True
                    video.save(update_fields=["is_published"])
                rungs = publish_and_enqueue(video)
            except Exception as exc:
                failures += 1
                self.message_user(
                    request,
                    f"Video {video.id}: publish failed ({exc})",
                    level=messages.ERROR,
                )
            else:
                successes += 1
                summary = ", ".join(rungs) if rungs else "nothing to enqueue"
                self.message_user(
                    request,
                    f"Video {video.id}: publish ok (rungs: {summary})",
                    level=messages.SUCCESS,
                )
        self.message_user(
            request,
            f"Publish+Render complete: success={successes}, failures={failures}.",
        )

    @admin.action(description="Regenerate thumbnail")
    def regenerate_thumbnail_action(self, request, queryset):
        successes = failures = 0
        for video in queryset:
            try:
                result = thumb_utils.ensure_thumbnail(video.id)
            except Exception as exc:
                failures += 1
                self.message_user(
                    request,
                    f"Video {video.id}: thumbnail failed ({exc})",
                    level=messages.ERROR,
                )
            else:
                if result:
                    successes += 1
        self.message_user(
            request,
            f"Thumbnails regenerated: {successes} ok, {failures} failed.",
        )

    @admin.action(description="Re-encode 1080p")
    def reencode_1080p(self, request, queryset):
        self._queue_resolution(
            request, queryset, "1080p", force=True, allow_existing=True
        )

    @admin.action(description="Re-encode 720p")
    def reencode_720p(self, request, queryset):
        self._queue_resolution(
            request, queryset, "720p", force=True, allow_existing=True
        )

    @admin.action(description="Re-encode 480p")
    def reencode_480p(self, request, queryset):
        self._queue_resolution(
            request, queryset, "480p", force=True, allow_existing=True
        )

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
