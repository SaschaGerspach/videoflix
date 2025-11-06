from django.urls import resolve


def test_manifest_route_resolves_to_manifest_view():
    match = resolve("/api/video/1/720p/index.m3u8")
    assert match.func.view_class.__name__ == "VideoManifestView"


def test_segment_route_resolves_to_segment_content_view():
    match = resolve("/api/video/1/720p/000.ts")
    assert match.func.view_class.__name__ == "VideoSegmentContentView"
