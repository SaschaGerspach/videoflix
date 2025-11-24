from __future__ import annotations


import pytest
from django.contrib.auth import get_user_model

from jobs.domain import services as transcode_services
from videos.domain import selectors
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def owner_user():
    User = get_user_model()
    return User.objects.create_user(
        username="owner", email="owner@example.com", password="secret"
    )


@pytest.fixture
def published_video(owner_user):
    return Video.objects.create(
        owner=owner_user,
        title="Published",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )


@pytest.mark.django_db
def test_filter_queryset_ready_respects_helper(monkeypatch, published_video):
    other = Video.objects.create(
        title="Other",
        description="",
        thumbnail_url="http://example.com/other.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    qs = Video.objects.filter(pk__in=[published_video.pk, other.pk])
    monkeypatch.setattr(
        selectors, "has_hls_ready", lambda video_id, res: video_id == published_video.id
    )

    filtered = selectors.filter_queryset_ready(qs, res="480p", ready_only=True)

    assert [video.id for video in filtered] == [published_video.id]
    assert selectors.filter_queryset_ready(qs, ready_only=False).count() == 2


@pytest.mark.django_db
def test_get_video_stream_prefers_filesystem(media_root, owner_user, settings):
    video = Video.objects.create(
        owner=owner_user,
        title="FS Stream",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=False,
    )
    stream = VideoStream.objects.create(
        video=video, resolution="480p", manifest="#EXTM3U\n"
    )
    manifest_dir = transcode_services.get_transcode_output_dir(video.id, "480p")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "index.m3u8").write_text("#EXTM3U\n#EXTINF:10,\n", encoding="utf-8")

    result = selectors.get_video_stream(
        movie_id=video.id, resolution="480p", user=owner_user
    )

    assert "#EXTINF" in result.manifest
    assert result.video == stream.video


@pytest.mark.django_db
def test_get_video_stream_requires_permission(media_root, owner_user):
    video = Video.objects.create(
        owner=owner_user,
        title="Private",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=False,
    )
    VideoStream.objects.create(video=video, resolution="480p", manifest="#EXTM3U\n")

    other_user = get_user_model().objects.create_user(
        username="other", email="other@example.com", password="secret"
    )

    with pytest.raises(PermissionError):
        selectors.get_video_stream(
            movie_id=video.id, resolution="480p", user=other_user
        )


@pytest.mark.django_db
def test_get_video_segment_falls_back_to_database(media_root, owner_user):
    video = Video.objects.create(
        owner=owner_user,
        title="Segment",
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=False,
    )
    stream = VideoStream.objects.create(
        video=video, resolution="480p", manifest="#EXTM3U\n"
    )
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"payload")

    result = selectors.get_video_segment(
        movie_id=video.id,
        resolution="480p",
        segment="000.ts",
        user=owner_user,
    )

    assert result.content == b"payload"


@pytest.mark.django_db
def test_resolve_public_id_handles_existing_pk(published_video):
    assert selectors.resolve_public_id(1) == published_video.id
    assert selectors.resolve_public_id(published_video.id) == published_video.id
    with pytest.raises(Video.DoesNotExist):
        selectors.resolve_public_id(9999)
