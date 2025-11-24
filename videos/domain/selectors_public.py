from __future__ import annotations

from typing import Any
from collections.abc import Sequence

from django.db.models import Q, QuerySet
from django.http import Http404

from .models import Video
from .selectors import filter_queryset_ready


def _ordered_queryset(qs: QuerySet[Video]) -> QuerySet[Video]:
    return qs.order_by("-created_at", "id")


def get_user_video_queryset(user) -> QuerySet[Video]:
    """Return videos visible to the given user, ordered deterministically.

    Admins/staff see all videos. Regular users see their own uploads and any
    published videos.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return _ordered_queryset(Video.objects.none())

    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return _ordered_queryset(Video.objects.all())

    user_id = getattr(user, "id", None)
    if user_id is None:
        return _ordered_queryset(Video.objects.none())

    visible = Video.objects.filter(Q(is_published=True) | Q(owner_id=user_id))
    return _ordered_queryset(visible)


def resolve_public_id_to_real_id(user, public_id: int) -> int:
    """Resolve a 1-based public ordinal to the underlying database ID."""
    ids = list(get_user_video_queryset(user).values_list("id", flat=True))
    if public_id < 1:
        raise Http404("Video not found.")
    if public_id <= len(ids):
        return ids[public_id - 1]
    if public_id in ids:
        return public_id
    raise Http404("Video not found.")


def list_for_user_with_public_ids(
    user,
    *,
    ready_only: bool = True,
    res: str = "480p",
    ordering: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Serialize the video list for the user with ordinal IDs."""
    from videos.api.serializers import VideoSerializer

    videos = get_user_video_queryset(user)
    if ordering:
        videos = videos.order_by(*ordering)
    filtered = filter_queryset_ready(videos, res=res, ready_only=ready_only)
    if isinstance(filtered, QuerySet):
        filtered = list(filtered)
    serialized = VideoSerializer(filtered, many=True).data
    for index, item in enumerate(serialized, start=1):
        item["id"] = index
    return serialized
