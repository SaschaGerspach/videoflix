from rest_framework import serializers

from jobs.domain.services import ALLOWED_TRANSCODE_PROFILES
from videos.domain import thumbs as thumb_utils
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
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = ["id", "created_at", "title", "description", "thumbnail_url", "category"]
        read_only_fields = fields

    def get_thumbnail_url(self, obj: Video) -> str:
        request = self.context.get("request")
        return thumb_utils.get_thumbnail_url(request, obj.id) or ""


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


class VideoTranscodeRequestSerializer(serializers.Serializer):
    resolutions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    _ALLOWED = tuple(ALLOWED_TRANSCODE_PROFILES.keys())

    def validate_resolutions(self, value):
        invalid = [item for item in value if item not in self._ALLOWED]
        if invalid:
            raise serializers.ValidationError(f"Invalid value '{invalid[0]}'.")
        return value

    def validate(self, attrs):
        resolutions = attrs.get("resolutions") or list(self._ALLOWED)
        attrs["resolutions"] = resolutions
        return attrs


class VideoUploadSerializer(serializers.Serializer):
    file = serializers.FileField(write_only=True)

    def validate_file(self, file):
        content_type = getattr(file, "content_type", "")
        name = getattr(file, "name", "")
        if content_type and content_type.lower() not in {"video/mp4", "application/octet-stream"}:
            raise serializers.ValidationError("Only MP4 uploads are supported.")
        if not name.lower().endswith(".mp4"):
            raise serializers.ValidationError("Filename must end with .mp4.")
        return file
