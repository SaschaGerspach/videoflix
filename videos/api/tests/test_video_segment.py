import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from videos.domain.models import Video

pytestmark = pytest.mark.django_db


def segment_url(movie_id: int, resolution: str) -> str:
    return reverse("video-segment", kwargs={"movie_id": movie_id, "resolution": resolution})


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
def authenticated_client() -> APIClient:
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = user_model.objects.create_user(
        email="viewer@example.com",
        username="viewer@example.com",
        password="pass",
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def video() -> Video:
    return Video.objects.create(
        title="Sample Title",
        description="Sample Description",
        thumbnail_url="http://example.com/sample.jpg",
        category="drama",
    )


@pytest.fixture
def stream_factory():
    def _factory(video: Video, resolution: str, manifest: str) -> None:
        video.streams.create(resolution=resolution, manifest=manifest)

    return _factory


def test_segment_playlist_returns_manifest_for_authenticated_user(
    authenticated_client: APIClient, video: Video, stream_factory
) -> None:
    manifest = "#EXTM3U\n#EXT-X-VERSION:3\n"
    stream_factory(video, "720p", manifest)

    response = authenticated_client.get(segment_url(video.id, "720p"))

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
