from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.http import Http404
from django.utils import timezone

from videos.domain import selectors, selectors_public
from videos.domain.choices import VideoCategory
from videos.domain.models import Video, VideoStream


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def staff_user():
    User = get_user_model()
    return User.objects.create_user(
        username="staff",
        email="staff@example.com",
        password="secret",
        is_staff=True,
    )


@pytest.fixture
def owner_user():
    User = get_user_model()
    return User.objects.create_user(
        username="owner",
        email="owner@example.com",
        password="secret",
    )


@pytest.fixture
def other_user():
    User = get_user_model()
    return User.objects.create_user(
        username="viewer",
        email="viewer@example.com",
        password="secret",
    )


def _write_manifest(media_root: Path, video_id: int, content: str, res: str = "480p") -> Path:
    manifest_path = media_root / "hls" / str(video_id) / res / "index.m3u8"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(content, encoding="utf-8")
    return manifest_path


def test_filter_queryset_ready_and_list_ready(media_root):
    ready = Video.objects.create(
        title="Ready",
        description="",
        thumbnail_url="http://example.com/r.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    stub = Video.objects.create(
        title="Stub",
        description="",
        thumbnail_url="http://example.com/s.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    missing = Video.objects.create(
        title="Missing",
        description="",
        thumbnail_url="http://example.com/m.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )

    _write_manifest(media_root, ready.id, """#EXTM3U
#EXTINF:10,
000.ts
""")
    _write_manifest(media_root, stub.id, """#EXTM3U
""")

    qs = Video.objects.filter(is_published=True)

    filtered = selectors.filter_queryset_ready(qs, res="480p", ready_only=True)
    assert [video.id for video in filtered] == [ready.id]

    passthrough = selectors.filter_queryset_ready(qs, res="480p", ready_only=False)
    assert passthrough.count() == 3

    published_ready = selectors.list_published_videos_ready(res="480p")
    assert [video.id for video in published_ready] == [ready.id]


def test_list_for_user_with_public_ids_ready_filter(media_root, owner_user, other_user):
    base = timezone.now()

    ready_pub = Video.objects.create(
        owner=owner_user,
        title="Ready Published",
        description="",
        thumbnail_url="http://example.com/rp.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    Video.objects.filter(pk=ready_pub.pk).update(created_at=base)

    stub_pub = Video.objects.create(
        owner=owner_user,
        title="Stub Published",
        description="",
        thumbnail_url="http://example.com/sp.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    Video.objects.filter(pk=stub_pub.pk).update(created_at=base - timedelta(seconds=10))

    draft_ready = Video.objects.create(
        owner=owner_user,
        title="Draft Ready",
        description="",
        thumbnail_url="http://example.com/dr.jpg",
        category=VideoCategory.DRAMA,
        is_published=False,
    )
    Video.objects.filter(pk=draft_ready.pk).update(created_at=base - timedelta(seconds=20))

    _write_manifest(media_root, ready_pub.id, """#EXTM3U
#EXTINF:10,
000.ts
""")
    _write_manifest(media_root, draft_ready.id, """#EXTM3U
#EXTINF:8,
001.ts
""")
    _write_manifest(media_root, stub_pub.id, """#EXTM3U
""")

    ready_only = selectors_public.list_for_user_with_public_ids(owner_user, ready_only=True)
    ready_titles = [item["title"] for item in ready_only]
    assert ready_titles == ["Ready Published", "Draft Ready"]
    assert [item["id"] for item in ready_only] == [1, 2]

    all_items = selectors_public.list_for_user_with_public_ids(owner_user, ready_only=False)
    assert [item["title"] for item in all_items] == ["Ready Published", "Stub Published", "Draft Ready"]
    assert [item["id"] for item in all_items] == [1, 2, 3]

    viewer_items = selectors_public.list_for_user_with_public_ids(other_user, ready_only=True)
    assert [item["title"] for item in viewer_items] == ["Ready Published"]


def test_resolve_public_id_variants(media_root):
    video = Video.objects.create(
        title="Resolver",
        description="",
        thumbnail_url="http://example.com/r.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    assert selectors.resolve_public_id(1) == video.id
    assert selectors.resolve_public_id(video.id) == video.id
    with pytest.raises(Video.DoesNotExist):
        selectors.resolve_public_id(0)
    with pytest.raises(Video.DoesNotExist):
        selectors.resolve_public_id(9999)


def test_resolve_public_id_to_real_id_handles_invalid(media_root, owner_user):
    video = Video.objects.create(
        owner=owner_user,
        title="Public Resolver",
        description="",
        thumbnail_url="http://example.com/p.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    assert selectors_public.resolve_public_id_to_real_id(owner_user, 1) == video.id
    with pytest.raises(Http404):
        selectors_public.resolve_public_id_to_real_id(owner_user, 5)


def test_get_user_video_queryset_for_staff(media_root, staff_user):
    Video.objects.create(
        title="Published",
        description="",
        thumbnail_url="http://example.com/p.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    Video.objects.create(
        title="Hidden",
        description="",
        thumbnail_url="http://example.com/h.jpg",
        category=VideoCategory.DRAMA,
        is_published=False,
    )
    qs = selectors_public.get_user_video_queryset(staff_user)
    assert qs.count() == 2


def test_get_video_stream_uses_database_manifest(media_root, owner_user):
    video = Video.objects.create(
        owner=owner_user,
        title="DB Manifest",
        description="",
        thumbnail_url="http://example.com/db.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    manifest_text = """#EXTM3U
#EXTINF:10,
segment.ts
"""
    VideoStream.objects.create(video=video, resolution="480p", manifest=manifest_text)
    result = selectors.get_video_stream(movie_id=video.id, resolution="480p", user=owner_user)
    assert "segment.ts" in result.manifest


def test_get_video_stream_missing_stream_raises(media_root, owner_user):
    video = Video.objects.create(
        owner=owner_user,
        title="No Stream",
        description="",
        thumbnail_url="http://example.com/ns.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )
    with pytest.raises(VideoStream.DoesNotExist):
        selectors.get_video_stream(movie_id=video.id, resolution="480p", user=owner_user)


def test_video_visible_to_user_checks_staff(media_root, owner_user, staff_user):
    video = Video.objects.create(
        owner=owner_user,
        title="Visibility",
        description="",
        thumbnail_url="http://example.com/v.jpg",
        category=VideoCategory.DRAMA,
        is_published=False,
    )
    assert selectors._video_visible_to_user(video, staff_user) is True
    assert selectors._video_visible_to_user(video, None) is False
