from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from videos.domain import selectors
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream


pytestmark = pytest.mark.django_db


def _make_video(owner=None, **attrs):
    defaults = {
        "title": "Selector Video",
        "description": "",
        "thumbnail_url": "http://example.com/thumb.jpg",
        "category": VideoCategory.DRAMA,
        "is_published": True,
    }
    defaults.update(attrs)
    return Video.objects.create(owner=owner, **defaults)


def test_resolve_public_id_invalid_raises():
    with pytest.raises(Video.DoesNotExist):
        selectors.resolve_public_id(0)


def test_filter_queryset_ready_respects_has_hls_ready(settings, monkeypatch):
    owner = get_user_model().objects.create_user(
        "owner@example.com", "owner@example.com", "pass"
    )
    _make_video(owner=owner)

    monkeypatch.setattr(
        "videos.domain.selectors.has_hls_ready", lambda *args, **kwargs: False
    )
    qs = selectors.list_published_videos()
    filtered = selectors.filter_queryset_ready(qs, res="720p", ready_only=True)
    assert filtered == []


def test_get_video_stream_unknown_resolution(settings, tmp_path):
    owner = get_user_model().objects.create_user(
        "streamer@example.com", "streamer@example.com", "pass"
    )
    video = _make_video(owner=owner)
    with pytest.raises(VideoStream.DoesNotExist):
        selectors.get_video_stream(movie_id=video.id, resolution="720p", user=owner)


def test_get_video_stream_returns_manifest_when_disk_missing(monkeypatch, tmp_path):
    owner = get_user_model().objects.create_user(
        "diskless@example.com", "diskless@example.com", "pass"
    )
    video = _make_video(owner=owner)
    stream = VideoStream.objects.create(
        video=video,
        resolution="720p",
        manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n",
    )

    def fake_dir(*args, **kwargs):
        return tmp_path / "missing"

    monkeypatch.setattr(
        selectors.transcode_services, "get_transcode_output_dir", fake_dir
    )
    result = selectors.get_video_stream(
        movie_id=video.id, resolution="720p", user=owner
    )
    assert "#EXTM3U" in result.manifest


def test_get_video_segment_missing_segment_raises(monkeypatch, tmp_path):
    owner = get_user_model().objects.create_user(
        "segments@example.com", "segments@example.com", "pass"
    )
    video = _make_video(owner=owner)
    stream = VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n"
    )

    def fake_dir(*args, **kwargs):
        return tmp_path / "missing"

    monkeypatch.setattr(
        selectors.transcode_services, "get_transcode_output_dir", fake_dir
    )
    with pytest.raises(VideoSegment.DoesNotExist):
        selectors.get_video_segment(
            movie_id=video.id, resolution="720p", segment="000.ts", user=owner
        )


def test_resolve_public_id_negative_value_raises():
    with pytest.raises(Video.DoesNotExist):
        selectors.resolve_public_id(-5)


def test_get_video_stream_blank_resolution_rejected(tmp_path):
    owner = get_user_model().objects.create_user(
        "blank@example.com", "blank@example.com", "pass"
    )
    video = _make_video(owner=owner)
    VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n"
    )
    with pytest.raises(VideoStream.DoesNotExist):
        selectors.get_video_stream(movie_id=video.id, resolution="", user=owner)


def test_get_video_segment_blank_name_rejected(tmp_path):
    owner = get_user_model().objects.create_user(
        "segmentblank@example.com", "segmentblank@example.com", "pass"
    )
    video = _make_video(owner=owner)
    stream = VideoStream.objects.create(
        video=video, resolution="720p", manifest="#EXTM3U\n"
    )
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"bytes")
    with pytest.raises(VideoSegment.DoesNotExist):
        selectors.get_video_segment(
            movie_id=video.id, resolution="720p", segment="", user=owner
        )


def test_filter_queryset_ready_handles_blank_resolution(monkeypatch):
    monkeypatch.setattr(
        "videos.domain.selectors.has_hls_ready", lambda *args, **kwargs: True
    )
    video = _make_video()
    qs = selectors.list_published_videos()
    ready = selectors.filter_queryset_ready(qs, res="", ready_only=True)
    assert video in ready
