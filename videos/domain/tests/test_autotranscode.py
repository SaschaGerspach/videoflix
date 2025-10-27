from __future__ import annotations

from unittest.mock import patch

import pytest

from jobs.domain import services as transcode_services
from videos.domain.choices import VideoCategory
from videos.domain.models import Video


@pytest.mark.django_db
def test_autotranscode_enqueues_only_missing_profiles(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.ENV = "dev"
    settings.IS_TEST_ENV = False

    with patch("videos.domain.signals.transcode_services.is_transcode_locked", return_value=False), patch(
        "jobs.queue.enqueue_transcode_job"
    ) as enqueue_mock:
        video = Video.objects.create(
            title="Autotranscode Test",
            description="dummy",
            thumbnail_url="http://example.com/thumb.jpg",
            category=VideoCategory.DRAMA,
            is_published=True,
        )

        source_path = transcode_services.get_video_source_path(video.id)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"source")

        manifest_path = transcode_services.get_transcode_output_dir(video.id, "360p") / "index.m3u8"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("#EXTM3U\n", encoding="utf-8")

        video.title = "Autotranscode Test Updated"
        video.save()

    enqueue_mock.assert_called_once_with(video.id, ["480p", "720p", "1080p"])
