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


def assert_m3u8_success(response, expected_manifest: str) -> None:
    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].startswith("application/vnd.apple.mpegurl")
    assert response.content.decode("utf-8") == expected_manifest


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
    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n"
    stream_factory(video, "720p", manifest)

    response = authenticated_client.get(segment_url(video.id, "720p"))

    assert_m3u8_success(response, manifest)


def test_segment_playlist_forbids_unpublished_video_for_non_owner(
    authenticated_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream_factory(unpublished_video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(segment_url(unpublished_video.id, "720p"))

    payload = assert_json_error(response, status.HTTP_403_FORBIDDEN)
    assert payload["errors"]["non_field_errors"] == [
        "You do not have permission to access this video."
    ]


def test_segment_playlist_allows_owner_for_unpublished_video(
    owner_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n"
    stream_factory(unpublished_video, "720p", manifest)

    response = owner_client.get(segment_url(unpublished_video.id, "720p"))

    assert_m3u8_success(response, manifest)


def test_segment_playlist_allows_admin_for_unpublished_video(
    admin_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n"
    stream_factory(unpublished_video, "720p", manifest)

    response = admin_client.get(segment_url(unpublished_video.id, "720p"))

    assert_m3u8_success(response, manifest)


def test_segment_playlist_requires_authentication(video: Video, stream_factory) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")
    unauthenticated = APIClient()

    response = unauthenticated.get(segment_url(video.id, "720p"))

    payload = assert_json_error(response, status.HTTP_401_UNAUTHORIZED)
    assert payload["errors"]


def test_segment_playlist_returns_404_for_missing_video_or_resolution(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(segment_url(video.id, "1080p"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"errors": {"non_field_errors": [
        "Video manifest not found."]}}


def test_segment_playlist_rejects_invalid_resolution_format(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(segment_url(video.id, "invalid"))

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "resolution" in payload["errors"]
    assert payload["errors"]["resolution"][0].startswith(
        "Invalid resolution format")


def test_segment_playlist_idempotent_for_same_request(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=300000\n"
    stream_factory(video, "480p", manifest)

    first = authenticated_client.get(segment_url(video.id, "480p"))
    second = authenticated_client.get(segment_url(video.id, "480p"))

    assert_m3u8_success(first, manifest)
    assert_m3u8_success(second, manifest)
    assert first.content == second.content


def test_segment_playlist_invalid_resolution_content_type_is_json(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(segment_url(video.id, "invalid"))

    assert_json_error(response, status.HTTP_400_BAD_REQUEST)


def test_segment_playlist_missing_stream_content_type_is_json(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(segment_url(video.id, "1080p"))

    assert_json_error(response, status.HTTP_404_NOT_FOUND)


def test_segment_playlist_honours_m3u8_accept_header(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n"
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

    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n"
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

    segment_response = authenticated_client.get(
        segment_content_url(video.id, "480p", segment_name)
    )

    assert segment_response.status_code == status.HTTP_200_OK
    assert segment_response["Content-Type"].lower().startswith("video/mp2t")
    assert segment_response.content == segment_bytes


def test_segment_playlist_rejects_resolution_without_suffix(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(segment_url(video.id, "720"))

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "resolution" in payload["errors"]


def test_segment_playlist_returns_plain_text_without_pagination_wrapper(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n"
    stream_factory(video, "720p", manifest)

    response = authenticated_client.get(segment_url(video.id, "720p"))

    assert_m3u8_success(response, manifest)
    assert not response.content.decode("utf-8").startswith("{")


def test_segment_playlist_returns_json_for_errors_not_m3u8(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")

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
    stream_factory(video, "720p", "#EXTM3U\n")

    response = authenticated_client.get(
        segment_url(video.id, "720p"),
        HTTP_ACCEPT=accept_header,
    )

    payload = assert_json_error(response, status.HTTP_406_NOT_ACCEPTABLE)
    assert payload["errors"]["non_field_errors"][0].startswith(
        "Requested media type not acceptable")


def test_segment_content_forbids_unpublished_video_for_non_owner(
    authenticated_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream = stream_factory(unpublished_video, "720p", "#EXTM3U\n")
    stream.segments.create(name="part1.ts", content=b"segment-bytes")

    response = authenticated_client.get(segment_content_url(unpublished_video.id, "720p", "part1.ts"))

    payload = assert_json_error(response, status.HTTP_403_FORBIDDEN)
    assert payload["errors"]["non_field_errors"] == [
        "You do not have permission to access this video."
    ]


def test_segment_content_allows_owner_for_unpublished_video(
    owner_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream = stream_factory(unpublished_video, "720p", "#EXTM3U\n")
    segment = stream.segments.create(name="part1.ts", content=b"segment-bytes")

    response = owner_client.get(segment_content_url(unpublished_video.id, "720p", "part1.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].lower().startswith("video/mp2t")
    assert response.content == segment.content


def test_segment_content_allows_admin_for_unpublished_video(
    admin_client: APIClient, unpublished_video: Video, stream_factory
) -> None:
    stream = stream_factory(unpublished_video, "720p", "#EXTM3U\n")
    segment = stream.segments.create(name="part1.ts", content=b"segment-bytes")

    response = admin_client.get(segment_content_url(unpublished_video.id, "720p", "part1.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].lower().startswith("video/mp2t")
    assert response.content == segment.content


@pytest.mark.parametrize("method_name", ["post", "put"])
def test_segment_playlist_rejects_disallowed_methods(
    authenticated_client: APIClient,
    video: Video,
    stream_factory,
    method_name: str,
) -> None:
    stream_factory(video, "720p", "#EXTM3U\n")
    method = getattr(authenticated_client, method_name)

    response = method(segment_url(video.id, "720p"))

    payload = assert_json_error(response, status.HTTP_405_METHOD_NOT_ALLOWED)
    assert payload["errors"]["non_field_errors"][0].startswith(
        "Method not allowed")
