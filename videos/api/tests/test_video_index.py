import re
import pytest
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from urllib.parse import quote

from videos.api.serializers import VideoSegmentContentRequestSerializer
from videos.domain.models import Video

pytestmark = pytest.mark.django_db


def segment_url(movie_id: int, resolution: str, segment: str) -> str:
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


def assert_ts_success(response, expected_payload: bytes) -> None:
    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].lower().startswith("video/mp2t")
    assert response.content == expected_payload


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
def stream(video: Video):
    return video.streams.create(resolution="720p", manifest="#EXTM3U\n")


@pytest.fixture
def segment_factory(stream):
    def _factory(name: str, content: bytes):
        return stream.segments.create(name=name, content=content)

    return _factory


def test_video_segment_returns_binary_content(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x00\x01video-data"
    segment_factory("000.ts", payload)

    response = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "000.ts"))

    assert_ts_success(response, payload)


@pytest.mark.parametrize("accept_header", ["video/MP2T", "video/mp2t"])
def test_video_segment_accept_header_success(
    authenticated_client: APIClient,
    stream,
    segment_factory,
    accept_header: str,
) -> None:
    payload = b"\xaa\xbb"
    segment_factory("100.ts", payload)

    response = authenticated_client.get(
        segment_url(stream.video_id, stream.resolution, "100.ts"),
        HTTP_ACCEPT=accept_header,
    )

    assert_ts_success(response, payload)


def test_video_segment_requires_authentication(stream, segment_factory) -> None:
    segment_factory("000.ts", b"test")
    client = APIClient()

    response = client.get(segment_url(stream.video_id, stream.resolution, "000.ts"))

    payload = assert_json_error(response, status.HTTP_401_UNAUTHORIZED)
    assert payload["errors"]


def test_video_segment_returns_404_when_segment_missing(
    authenticated_client: APIClient, stream
) -> None:
    response = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "000.ts"))

    payload = assert_json_error(response, status.HTTP_404_NOT_FOUND)
    assert payload["errors"]["non_field_errors"][0].startswith("Video segment not found")


def test_video_segment_rejects_invalid_resolution_format(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    segment_factory("000.ts", b"x")

    response = authenticated_client.get(segment_url(stream.video_id, "invalid", "000.ts"))

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "resolution" in payload["errors"]
    assert payload["errors"]["resolution"][0].startswith("Invalid resolution format")


def test_video_segment_rejects_invalid_segment_name(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    segment_factory("000.ts", b"x")

    response = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "000"))

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "segment" in payload["errors"]
    assert payload["errors"]["segment"][0].startswith("Invalid segment name")


@pytest.mark.parametrize(
    "segment_value",
    ["..%2F000.ts", "000.ts%2F..", "%2e%2e%2f000.ts", "dir%2F000.ts"],
)
def test_video_segment_rejects_traversal_in_segment_name(
    authenticated_client: APIClient,
    stream,
    segment_factory,
    segment_value: str,
) -> None:
    segment_factory("000.ts", b"x")

    response = authenticated_client.get(
        segment_url(stream.video_id, stream.resolution, segment_value),
    )

    payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
    assert "segment" in payload["errors"]
    assert payload["errors"]["segment"][0].startswith("Invalid segment name")


def test_video_segment_is_idempotent(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x11\x22\x33"
    segment_factory("001.ts", payload)

    first = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "001.ts"))
    second = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "001.ts"))

    assert_ts_success(first, payload)
    assert_ts_success(second, payload)
    assert first.content == second.content


@pytest.mark.parametrize("accept_header", ["application/json", "text/plain"])
def test_video_segment_rejects_unacceptable_accept_header(
    authenticated_client: APIClient,
    stream,
    segment_factory,
    accept_header: str,
) -> None:
    segment_factory("200.ts", b"x")

    response = authenticated_client.get(
        segment_url(stream.video_id, stream.resolution, "200.ts"),
        HTTP_ACCEPT=accept_header,
    )

    payload = assert_json_error(response, status.HTTP_406_NOT_ACCEPTABLE)
    assert payload["errors"]["non_field_errors"][0].startswith("Requested media type not acceptable")


@pytest.mark.parametrize("method_name", ["post", "put", "patch", "delete"])
def test_video_segment_rejects_disallowed_methods(
    authenticated_client: APIClient,
    stream,
    segment_factory,
    method_name: str,
) -> None:
    segment_factory("300.ts", b"x")
    method = getattr(authenticated_client, method_name)

    response = method(segment_url(stream.video_id, stream.resolution, "300.ts"))

    payload = assert_json_error(response, status.HTTP_405_METHOD_NOT_ALLOWED)
    assert payload["errors"]["non_field_errors"][0].startswith("Method not allowed")


def test_video_segment_head_method_policy(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x99"
    segment_factory("head.ts", payload)

    response = authenticated_client.head(segment_url(stream.video_id, stream.resolution, "head.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response.content == b""
    assert response["Content-Type"].lower().startswith("video/mp2t")


def test_video_segment_options_method_policy(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    segment_factory("opt.ts", b"a")

    response = authenticated_client.options(segment_url(stream.video_id, stream.resolution, "opt.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].startswith("application/json")
    assert "Allow" in response


def test_video_segment_duplicate_name_raises_conflict(segment_factory) -> None:
    segment_factory("dup.ts", b"a")
    with pytest.raises(IntegrityError):
        segment_factory("dup.ts", b"b")


def test_video_segment_success_has_caching_headers(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x55"
    segment_factory("cache.ts", payload)

    response = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "cache.ts"))

    assert_ts_success(response, payload)
    cache_control = response.get("Cache-Control", "")
    assert cache_control
    assert "public" in cache_control.lower()
    assert re.search(r"\bmax-age=\d+\b", cache_control)
    assert "ETag" in response or "Last-Modified" in response


def test_video_segment_accept_mixed_prefers_ts(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x44"
    segment_factory("mixed.ts", payload)

    response = authenticated_client.get(
        segment_url(stream.video_id, stream.resolution, "mixed.ts"),
        HTTP_ACCEPT="application/json, video/mp2t;q=0.1",
    )

    assert_ts_success(response, payload)


@pytest.mark.parametrize(
    "segment_value",
    ["./000.ts", ".\\000.ts", "%2e/000.ts", "000.ts.."],
)
def test_video_segment_rejects_dotdot_variants_more(
    authenticated_client: APIClient,
    stream,
    segment_factory,
    segment_value: str,
) -> None:
    segment_factory("000.ts", b"x")

    encoded = quote(segment_value, safe="")
    path = f"/api/video/{stream.video_id}/{stream.resolution}/{encoded}/"

    response = authenticated_client.get(path)

    if response.status_code == status.HTTP_400_BAD_REQUEST:
        payload = assert_json_error(response, status.HTTP_400_BAD_REQUEST)
        assert "segment" in payload["errors"]
        assert payload["errors"]["segment"][0].startswith("Invalid segment name")
    else:
        assert response.status_code == status.HTTP_404_NOT_FOUND
        serializer = VideoSegmentContentRequestSerializer(
            data={
                "movie_id": stream.video_id,
                "resolution": stream.resolution,
                "segment": segment_value,
            }
        )
        assert not serializer.is_valid()
        assert serializer.errors["segment"][0].startswith("Invalid segment name")


def test_video_segment_options_includes_allow_header(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    segment_factory("allow.ts", b"x")

    response = authenticated_client.options(segment_url(stream.video_id, stream.resolution, "allow.ts"))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"].startswith("application/json")
    allow_header = response.get("Allow", "")
    assert allow_header
    for method in ("GET", "HEAD"):
        assert method in allow_header
    if "OPTIONS" not in allow_header:
        pytest.skip("OPTIONS not advertised; policy allows omission.")


def test_video_segment_success_cache_control_policy(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x66"
    segment_factory("policy.ts", payload)

    response = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "policy.ts"))

    assert_ts_success(response, payload)
    cache_control = response.get("Cache-Control", "")
    assert cache_control
    assert "public" in cache_control.lower()
    assert re.search(r"\bmax-age=\d+\b", cache_control)
    assert "ETag" in response or "Last-Modified" in response


def test_video_segment_success_etag_is_quoted(
    authenticated_client: APIClient, stream, segment_factory
) -> None:
    payload = b"\x77"
    segment_factory("etag.ts", payload)

    response = authenticated_client.get(segment_url(stream.video_id, stream.resolution, "etag.ts"))

    assert_ts_success(response, payload)
    if "ETag" not in response:
        pytest.skip("ETag header not present; using Last-Modified instead.")
    etag = response["ETag"]
    assert re.match(r'^".*"$', etag)
