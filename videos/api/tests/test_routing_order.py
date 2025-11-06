from django.urls import resolve

from videos.api.views import VideoManifestView, VideoSegmentContentView


def test_manifest_route_resolves_video_manifest_view():
    match = resolve("/api/video/1/720p/index.m3u8")
    assert getattr(match.func, "view_class", None) is VideoManifestView


def test_segment_route_resolves_video_segment_content_view():
    match = resolve("/api/video/1/720p/000.ts")
    assert getattr(match.func, "view_class", None) is VideoSegmentContentView
