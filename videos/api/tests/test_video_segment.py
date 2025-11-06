import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from jobs.domain import services as transcode_services
from videos.domain.models import Video, VideoStream

pytestmark = pytest.mark.django_db


def segment_url(movie_id: int, resolution: str) -> str:
    return reverse("video-segment", kwargs={"movie_id": movie_id, "resolution": resolution})


def segment_content_url(movie_id: int, resolution: str, segment: str) -> str:
    return reverse(
        "video-segment-content",
        kwargs={"movie_id": movie_id, "resolution": resolution, "segment": segment},
    )


def assert_json_error(response, expected_status: int) -> dict:
    assert response.status_code == expected_status
    assert response["Content-Type"].startswith("application/json")
    payload = response.json()
    assert "errors" in payload
    return payload


def _collect_response_bytes(response) -> bytes:
    if hasattr(response, "_collected_stream"):
        return response._collected_stream
    if hasattr(response, "streaming_content"):
        data = b"".join(response.streaming_content)
        response._collected_stream = data
        response.streaming_content = iter((data,))
        return data
    return response.content


def assert_m3u8_success(response, expected_manifest: str) -> None:
    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].startswith("application/vnd.apple.mpegurl")
    body = _collect_response_bytes(response).decode("utf-8")
    assert body.replace("\r\n", "\n") == expected_manifest


def manifest_with_segments(*segments: str) -> str:
    if not segments:
        segments = ("000.ts",)
    lines = ["#EXTM3U"]
    for segment in segments:
        lines.append("#EXTINF:10,")
        lines.append(segment)
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(root)
    yield root


def _public_id_for(video: Video) -> int:
    ordered_ids = list(
        Video.objects.filter(is_published=True)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)
    )
    return ordered_ids.index(video.id) + 1


@pytest.fixture
def stream_owner():
    user_model = get_user_model()
    return user_model.objects.create_user(
        email="streamer@example.com",
        username="streamer@example.com",
        password="pass",
    )


@pytest.fixture
def authenticated_user():
    user_model = get_user_model()
    return user_model.objects.create_user(
        email="viewer@example.com",
        username="viewer@example.com",
        password="pass",
    )


@pytest.fixture
def authenticated_client(authenticated_user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=authenticated_user)
    client.user = authenticated_user
    return client


@pytest.fixture
def owner_client(stream_owner) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=stream_owner)
    client.user = stream_owner
    return client


@pytest.fixture
def admin_client():
    user_model = get_user_model()
    admin = user_model.objects.create_user(
        email="admin@example.com",
        username="admin@example.com",
        password="pass",
    )
    admin.is_staff = True
    admin.is_superuser = True
    admin.save()
    client = APIClient()
    client.force_authenticate(user=admin)
    client.user = admin
    return client


@pytest.fixture
def video(stream_owner) -> Video:
    return Video.objects.create(
        title="Sample Title",
        description="Sample Description",
        thumbnail_url="http://example.com/sample.jpg",
        category="drama",
        owner=stream_owner,
        is_published=True,
    )


@pytest.fixture
def unpublished_video(stream_owner) -> Video:
    return Video.objects.create(
        title="Draft Title",
        description="Draft Description",
        thumbnail_url="http://example.com/draft.jpg",
        category="drama",
        owner=stream_owner,
        is_published=False,
    )


@pytest.fixture
def stream_factory():
    def _factory(video: Video, resolution: str, manifest: str) -> VideoStream:
        return video.streams.create(resolution=resolution, manifest=manifest)

    return _factory


def test_segment_playlist_returns_manifest_for_authenticated_user(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\nplaylist.ts\n"
    stream_factory(video, "720p", manifest)

    response = authenticated_client.get(segment_url(video.id, "720p"))

    assert_m3u8_success(response, manifest)


def test_segment_playlist_forbids_unpublished_video_for_non_owner(
    authenticated_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream_factory(unpublished_video, "720p", manifest_with_segments("draft.ts"))

    response = authenticated_client.get(segment_url(unpublished_video.id, "720p"))

    payload = assert_json_error(response, status.HTTP_404_NOT_FOUND)
    assert payload["errors"]["non_field_errors"] == ["Video manifest not found."]


def test_segment_playlist_allows_owner_for_unpublished_video(
    owner_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    manifest = manifest_with_segments("owner.ts")
    stream_factory(unpublished_video, "720p", manifest)

    response = owner_client.get(segment_url(unpublished_video.id, "720p"))

    assert_m3u8_success(response, manifest)


def test_segment_playlist_allows_admin_for_unpublished_video(
    admin_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    manifest = manifest_with_segments("admin.ts")
    stream_factory(unpublished_video, "720p", manifest)

    response = admin_client.get(segment_url(unpublished_video.id, "720p"))

    assert_m3u8_success(response, manifest)


def test_segment_playlist_requires_authentication(video: Video, stream_factory) -> None:
    stream_factory(video, "720p", manifest_with_segments("auth.ts"))
    unauthenticated = APIClient()

    response = unauthenticated.get(segment_url(video.id, "720p"))

    payload = assert_json_error(response, status.HTTP_401_UNAUTHORIZED)
    assert payload["errors"]


def test_segment_playlist_returns_404_for_missing_video_or_resolution(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", manifest_with_segments("missing.ts"))

    response = authenticated_client.get(segment_url(video.id, "1080p"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"errors": {"non_field_errors": [
        "Video manifest not found."]}}


def test_segment_playlist_rejects_invalid_resolution_format(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", manifest_with_segments("invalid.ts"))

    response = authenticated_client.get(segment_url(video.id, "invalid"))

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "resolution" in payload["errors"]
    assert payload["errors"]["resolution"][0].startswith(
        "Invalid resolution format")


def test_segment_playlist_idempotent_for_same_request(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=300000\n#EXTINF:10,\n480p.ts\n"
    stream_factory(video, "480p", manifest)

    first = authenticated_client.get(segment_url(video.id, "480p"))
    second = authenticated_client.get(segment_url(video.id, "480p"))

    assert_m3u8_success(first, manifest)
    assert_m3u8_success(second, manifest)
    assert _collect_response_bytes(first) == _collect_response_bytes(second)


def test_segment_playlist_invalid_resolution_content_type_is_json(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", manifest_with_segments("invalid-json.ts"))

    response = authenticated_client.get(segment_url(video.id, "invalid"))

    assert_json_error(response, status.HTTP_400_BAD_REQUEST)


def test_segment_playlist_missing_stream_content_type_is_json(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", manifest_with_segments("missing-json.ts"))

    response = authenticated_client.get(segment_url(video.id, "1080p"))

    assert_json_error(response, status.HTTP_404_NOT_FOUND)


def test_segment_playlist_honours_m3u8_accept_header(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = manifest_with_segments("accept.ts")
    stream_factory(video, "480p", manifest)

    response = authenticated_client.get(
        segment_url(video.id, "480p"),
        HTTP_ACCEPT="application/vnd.apple.mpegurl",
    )

    assert_m3u8_success(response, manifest)


def test_segment_480p_filesystem_manifest_and_segment(
    authenticated_client: APIClient,
    video: Video,
    stream_factory,
    tmp_path,
    settings,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(media_root)

    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\n000.ts\n"
    stream = stream_factory(video, "480p", manifest)

    output_dir = transcode_services.get_transcode_output_dir(video.id, "480p")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.m3u8").write_text(manifest, encoding="utf-8")

    segment_name = "000.ts"
    segment_bytes = b"segment-bytes"
    (output_dir / segment_name).write_bytes(segment_bytes)
    stream.segments.create(name=segment_name, content=b"")

    manifest_response = authenticated_client.get(segment_url(video.id, "480p"))

    assert_m3u8_success(manifest_response, manifest)

    segment_response = authenticated_client.get(segment_content_url(video.id, "480p", segment_name))

    assert segment_response.status_code == status.HTTP_200_OK
    assert segment_response["Content-Type"].lower().startswith("video/mp2t")
    assert _collect_response_bytes(segment_response) == segment_bytes


def test_segment_playlist_rejects_resolution_without_suffix(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", manifest_with_segments("no-suffix.ts"))

    response = authenticated_client.get(segment_url(video.id, "720"))

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "resolution" in payload["errors"]


def test_segment_playlist_returns_plain_text_without_pagination_wrapper(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\nplain.ts\n"
    stream_factory(video, "720p", manifest)

    response = authenticated_client.get(segment_url(video.id, "720p"))

    assert_m3u8_success(response, manifest)
    assert not _collect_response_bytes(response).decode("utf-8").startswith("{")


def test_segment_playlist_returns_json_for_errors_not_m3u8(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", manifest_with_segments("json-error.ts"))

    response = authenticated_client.get(segment_url(video.id, "invalid"))

    assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert not response["Content-Type"].startswith(
        "application/vnd.apple.mpegurl")


@pytest.mark.parametrize("accept_header", ["application/json", "text/plain"])
def test_segment_playlist_rejects_unacceptable_accept_header(
    authenticated_client: APIClient,
    video: Video,
    stream_factory,
    accept_header: str,
) -> None:
    stream_factory(video, "720p", manifest_with_segments("not-acceptable.ts"))

    response = authenticated_client.get(
        segment_url(video.id, "720p"),
        HTTP_ACCEPT=accept_header,
    )

    expected_status = (
        status.HTTP_404_NOT_FOUND if accept_header == "application/json" else status.HTTP_406_NOT_ACCEPTABLE
    )
    payload = assert_json_error(response, expected_status)
    if expected_status == status.HTTP_406_NOT_ACCEPTABLE:
        assert payload["errors"]["non_field_errors"][0].startswith("Requested media type not acceptable")
    else:
        assert payload["errors"]["non_field_errors"] == ["Video manifest not found."]


def test_segment_content_forbids_unpublished_video_for_non_owner(
    authenticated_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream = stream_factory(unpublished_video, "720p", manifest_with_segments("segment.ts"))
    stream.segments.create(name="part1.ts", content=b"segment-bytes")

    response = authenticated_client.get(segment_content_url(unpublished_video.id, "720p", "part1.ts"))

    payload = assert_json_error(response, status.HTTP_404_NOT_FOUND)
    assert payload["errors"]["non_field_errors"] == ["Video segment not found."]


def test_segment_content_allows_owner_for_unpublished_video(
    owner_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream = stream_factory(unpublished_video, "720p", manifest_with_segments("owner-segment.ts"))
    segment = stream.segments.create(name="part1.ts", content=b"segment-bytes")

    response = owner_client.get(segment_content_url(unpublished_video.id, "720p", "part1.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].lower().startswith("video/mp2t")
    body = _collect_response_bytes(response)
    assert body == bytes(segment.content)


def test_segment_content_allows_admin_for_unpublished_video(
    admin_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream = stream_factory(unpublished_video, "720p", manifest_with_segments("admin-segment.ts"))
    segment = stream.segments.create(name="part1.ts", content=b"segment-bytes")

    response = admin_client.get(segment_content_url(unpublished_video.id, "720p", "part1.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].lower().startswith("video/mp2t")
    body = _collect_response_bytes(response)
    assert body == bytes(segment.content)


def test_segment_content_serves_filesystem_segments_for_public_ids(
    authenticated_client: APIClient,
    stream_owner,
    stream_factory,
    tmp_path,
    settings,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(media_root)

    manifest = manifest_with_segments("000.ts", "001.ts", "002.ts")

    older = Video.objects.create(
        title="Old Cut",
        description="Older description",
        thumbnail_url="http://example.com/older.jpg",
        category="drama",
        owner=stream_owner,
        is_published=True,
    )
    newer = Video.objects.create(
        title="New Cut",
        description="New description",
        thumbnail_url="http://example.com/new.jpg",
        category="drama",
        owner=stream_owner,
        is_published=True,
    )

    video_cases = (
        (newer, b"n"),
        (older, b"o"),
    )

    for video_obj, prefix in video_cases:
        stream_factory(video_obj, "480p", manifest)
        output_dir = transcode_services.get_transcode_output_dir(video_obj.id, "480p")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.m3u8").write_text(manifest, encoding="utf-8")
        for idx in range(3):
            payload = prefix + bytes([48 + idx])  # distinct payload per segment
            (output_dir / f"{idx:03d}.ts").write_bytes(payload)

    public_cases = (
        (1, newer, b"n"),
        (2, older, b"o"),
    )

    for public_id, video_obj, prefix in public_cases:
        for idx in range(3):
            segment_name = f"{idx:03d}.ts"
            response = authenticated_client.get(
                segment_content_url(public_id, "480p", segment_name)
            )
            assert response.status_code == status.HTTP_200_OK
            assert response["Content-Type"].lower().startswith("video/mp2t")
            assert _collect_response_bytes(response) == prefix + bytes([48 + idx])

        padded_response = authenticated_client.get(
            segment_content_url(public_id, "480p", "1.ts")
        )
        assert padded_response.status_code == status.HTTP_200_OK
        assert _collect_response_bytes(padded_response) == prefix + b"1"

        missing_response = authenticated_client.get(
            segment_content_url(public_id, "480p", "003.ts")
        )
        payload = assert_json_error(missing_response, status.HTTP_404_NOT_FOUND)
        assert payload["errors"]["non_field_errors"] == ["Video segment not found."]


@pytest.mark.parametrize("method_name", ["post", "put"])
def test_segment_playlist_rejects_disallowed_methods(
    authenticated_client: APIClient,
    video: Video,
    stream_factory,
    method_name: str,
) -> None:
    stream_factory(video, "720p", manifest_with_segments("disallowed.ts"))
    method = getattr(authenticated_client, method_name)

    response = method(segment_url(video.id, "720p"))

    payload = assert_json_error(response, status.HTTP_405_METHOD_NOT_ALLOWED)
    assert payload["errors"]["non_field_errors"][0].startswith(
        "Method not allowed")


def test_public_ids_map_to_real_videos_for_owner(owner_client: APIClient) -> None:
    owner = owner_client.user
    older = Video.objects.create(
        title="Older Cut",
        description="Older description",
        thumbnail_url="http://example.com/older.jpg",
        category="drama",
        owner=owner,
        is_published=True,
    )
    older_manifest = manifest_with_segments("older.ts")
    older.streams.create(resolution="480p", manifest=older_manifest)

    newer = Video.objects.create(
        title="Newer Cut",
        description="Newer description",
        thumbnail_url="http://example.com/newer.jpg",
        category="action",
        owner=owner,
        is_published=True,
    )
    newer_manifest = manifest_with_segments("newer.ts")
    newer.streams.create(resolution="480p", manifest=newer_manifest)

    first_response = owner_client.get(segment_url(1, "480p"))
    assert_m3u8_success(first_response, newer_manifest)

    second_response = owner_client.get(segment_url(2, "480p"))
    assert_m3u8_success(second_response, older_manifest)

    missing_response = owner_client.get(segment_url(3, "480p"))
    payload = assert_json_error(missing_response, status.HTTP_404_NOT_FOUND)
    assert payload["errors"]["non_field_errors"] == ["Video manifest not found."]


def test_manifest_served_from_filesystem_for_multiple_resolutions(
    authenticated_client: APIClient,
    stream_owner,
    stream_factory,
    tmp_path,
    settings,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = str(media_root)

    older = Video.objects.create(
        title="Older Cut",
        description="older",
        thumbnail_url="http://example.com/older.jpg",
        category="drama",
        owner=stream_owner,
        is_published=True,
    )
    newer = Video.objects.create(
        title="Newer Cut",
        description="newer",
        thumbnail_url="http://example.com/newer.jpg",
        category="drama",
        owner=stream_owner,
        is_published=True,
    )

    for video_obj, prefix in ((newer, b"n"), (older, b"o")):
        manifest = "#EXTM3U\n#EXTINF:10,\n000.ts\n#EXTINF:10,\n001.ts\n#EXTINF:10,\n002.ts\n"
        stream_factory(video_obj, "720p", manifest)
        output_dir = transcode_services.get_transcode_output_dir(video_obj.id, "720p")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "index.m3u8").write_text(manifest, encoding="utf-8")
        for idx in range(3):
            payload = prefix + bytes([48 + idx])
            (output_dir / f"{idx:03d}.ts").write_bytes(payload)

    public_cases = (
        (_public_id_for(newer), newer, b"n"),
        (_public_id_for(older), older, b"o"),
    )

    for public_id, video_obj, prefix in public_cases:
        manifest_response = authenticated_client.get(
            segment_url(public_id, "720p"),
            HTTP_ACCEPT="application/vnd.apple.mpegurl",
        )
        assert manifest_response.status_code == status.HTTP_200_OK
        assert manifest_response["Content-Type"].startswith("application/vnd.apple.mpegurl")
        assert _collect_response_bytes(manifest_response).startswith(b"#EXTM3U")

        seg_response = authenticated_client.get(
            segment_content_url(public_id, "720p", "001.ts"),
            HTTP_ACCEPT="video/MP2T",
        )
        assert seg_response.status_code == status.HTTP_200_OK
        assert _collect_response_bytes(seg_response) == prefix + b"1"

        missing_response = authenticated_client.get(
            segment_content_url(public_id, "720p", "003.ts"),
            HTTP_ACCEPT="video/MP2T",
        )
        payload = assert_json_error(missing_response, status.HTTP_404_NOT_FOUND)
        assert payload["errors"]["non_field_errors"] == ["Video segment not found."]


def test_segment_serves_without_db_and_self_heals_on_first_hit(
    authenticated_client: APIClient,
    media_root,
) -> None:
    video = Video.objects.create(
        id=99,
        title="FS First Segment",
        description="",
        thumbnail_url="http://example.com/segment.jpg",
        category="drama",
        is_published=True,
    )
    public_id = _public_id_for(video)

    rendition_dir = media_root / "hls" / str(video.pk) / "720p"
    rendition_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_with_segments("000.ts", "001.ts")
    (rendition_dir / "index.m3u8").write_text(manifest, encoding="utf-8")
    (rendition_dir / "000.ts").write_bytes(b"segment-zero")
    (rendition_dir / "001.ts").write_bytes(b"segment-one")

    response = authenticated_client.get(
        segment_content_url(public_id, "720p", "000.ts"),
        HTTP_ACCEPT="video/MP2T",
    )

    assert response.status_code == status.HTTP_200_OK
    assert _collect_response_bytes(response) == b"segment-zero"

    stream = VideoStream.objects.get(video=video, resolution="720p")
    assert stream.segments.count() == 2
