from rest_framework import serializers

from videos.domain.models import Video


class VideoListRequestSerializer(serializers.Serializer):
    """Validator for the video list endpoint body (expects empty JSON object)."""


class VideoSegmentRequestSerializer(serializers.Serializer):
    movie_id = serializers.IntegerField(min_value=1)
    resolution = serializers.RegexField(
        regex=r"^\d{3,4}p$",
        max_length=16,
        error_messages={
            "invalid": "Invalid resolution format. Use e.g. 480p, 720p, 1080p."
        },
    )


class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = ["id", "created_at", "title", "description", "thumbnail_url", "category"]
        read_only_fields = fields


class VideoSegmentContentRequestSerializer(serializers.Serializer):
    movie_id = serializers.IntegerField(min_value=1)
    resolution = serializers.RegexField(
        regex=r"^\d{3,4}p$",
        max_length=16,
        error_messages={
            "invalid": "Invalid resolution format. Use e.g. 480p, 720p, 1080p."
        },
    )
    segment = serializers.RegexField(
        regex=r"^(?:\d{1,6}|[A-Za-z0-9_-]{1,64})\.ts$",
        max_length=255,
        error_messages={
            "invalid": "Invalid segment name. Use e.g. 000.ts."
        },
    )
