from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.http import Http404

from videos.domain import selectors
from videos.domain import selectors_public
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoSegment, VideoStream


pytestmark = pytest.mark.django_db


def _create_video(owner, is_published=True, title="Video"):
    return Video.objects.create(
        owner=owner,
        title=title,
        description="",
        thumbnail_url="http://example.com/thumb.jpg",
        category=VideoCategory.DRAMA,
        is_published=is_published,
    )


def test_get_user_video_queryset_handles_roles():
    User = get_user_model()
    owner = User.objects.create_user("owner@example.com", "owner@example.com", "pass")
    admin = User.objects.create_user(
        "admin@example.com", "admin@example.com", "pass", is_staff=True
    )
    _create_video(owner, is_published=True, title="public")
    _create_video(owner, is_published=False, title="private")

    anon_qs = selectors_public.get_user_video_queryset(None)
    assert list(anon_qs) == []

    admin_qs = selectors_public.get_user_video_queryset(admin)
    assert admin_qs.count() == 2

    user_qs = selectors_public.get_user_video_queryset(owner)
    titles = {video.title for video in user_qs}
    assert titles == {"public", "private"}


def test_resolve_public_id_to_real_id_invalid_raises(owner_user):
    with pytest.raises(Http404):
        selectors_public.resolve_public_id_to_real_id(owner_user, 0)


@pytest.fixture
def owner_user():
    User = get_user_model()
    return User.objects.create_user("member@example.com", "member@example.com", "pass")


def test_list_for_user_with_public_ids(monkeypatch, owner_user):
    video = _create_video(owner_user, is_published=True)

    def fake_filter(qs, res, ready_only):
        return list(qs)

    monkeypatch.setattr(selectors_public, "filter_queryset_ready", fake_filter)
    serialized = selectors_public.list_for_user_with_public_ids(owner_user, ready_only=True)
    assert serialized[0]["id"] == 1
    assert serialized[0]["title"] == video.title


def test_get_video_stream_denies_invisible_user(tmp_path, monkeypatch, owner_user):
    video = _create_video(owner_user, is_published=False)
    video.streams.create(resolution="720p", manifest="#EXTM3U\n")

    def fake_dir(video_id, resolution):
        path = tmp_path / str(video_id) / resolution
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(selectors.transcode_services, "get_transcode_output_dir", fake_dir)

    outsider = get_user_model().objects.create_user(
        "viewer@example.com", "viewer@example.com", "pass"
    )
    with pytest.raises(PermissionError):
        selectors.get_video_stream(movie_id=video.id, resolution="720p", user=outsider)


def test_get_video_stream_returns_manifest_from_database(monkeypatch, owner_user, tmp_path):
    video = _create_video(owner_user, is_published=True)
    stream = VideoStream.objects.create(
        video=video,
        resolution="720p",
        manifest="#EXTM3U\n#EXTINF:10,\n000.ts\n",
    )

    def fake_dir(video_id, resolution):
        return tmp_path / "missing"

    monkeypatch.setattr(selectors.transcode_services, "get_transcode_output_dir", fake_dir)
    result = selectors.get_video_stream(movie_id=video.id, resolution="720p", user=owner_user)
    assert "#EXTM3U" in result.manifest


def test_get_video_segment_returns_database_content(monkeypatch, owner_user, tmp_path):
    video = _create_video(owner_user, is_published=True)
    stream = VideoStream.objects.create(
        video=video,
        resolution="720p",
        manifest="#EXTM3U\n",
    )
    segment = VideoSegment.objects.create(
        stream=stream,
        name="000.ts",
        content=b"bytes",
    )

    def fake_dir(video_id, resolution):
        return tmp_path / "missing"

    monkeypatch.setattr(selectors.transcode_services, "get_transcode_output_dir", fake_dir)
    result = selectors.get_video_segment(
        movie_id=video.id,
        resolution="720p",
        segment="000.ts",
        user=owner_user,
    )
    assert result.content == b"bytes"


def test_get_video_segment_reads_disk_when_available(monkeypatch, owner_user, tmp_path):
    video = _create_video(owner_user, is_published=True)
    stream = VideoStream.objects.create(
        video=video,
        resolution="720p",
        manifest="#EXTM3U\n",
    )
    VideoSegment.objects.create(stream=stream, name="000.ts", content=b"in-db")

    output_dir = tmp_path / str(video.id) / "720p"
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_path = output_dir / "000.ts"
    segment_path.write_bytes(b"on-disk")

    monkeypatch.setattr(
        selectors.transcode_services,
        "get_transcode_output_dir",
        lambda video_id, resolution: output_dir,
    )

    result = selectors.get_video_segment(
        movie_id=video.id,
        resolution="720p",
        segment="000.ts",
        user=owner_user,
    )
    assert result.content == b"on-disk"
