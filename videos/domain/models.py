from django.db import models

from .choices import VideoCategory


class Video(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    thumbnail_url = models.URLField()
    category = models.CharField(max_length=32, choices=VideoCategory.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "pk")

    def __str__(self) -> str:
        return f"{self.title} ({self.get_category_display()})"


class VideoStream(models.Model):
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="streams")
    resolution = models.CharField(max_length=16, db_index=True)
    manifest = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        unique_together = ("video", "resolution")
        indexes = [
            models.Index(fields=["video", "resolution"]),
        ]

    def __str__(self) -> str:
        return f"{self.video_id} @ {self.resolution}"


class VideoSegment(models.Model):
    stream = models.ForeignKey(VideoStream, on_delete=models.CASCADE, related_name="segments")
    name = models.CharField(max_length=255)
    content = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        unique_together = ("stream", "name")
        indexes = [
            models.Index(fields=["stream", "name"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:
        return f"{self.stream_id}:{self.name}"
