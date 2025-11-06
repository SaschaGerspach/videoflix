from django.conf import settings
from django.urls import path, re_path

from videos.api.views import (
    AllowedRenditionsDebugView,
    DebugAuthView,
    HLSManifestDebugView,
    ThumbsDebugView,
    QueueHealthView,
    VideoListView,
    VideoHealthView,
    VideoManifestView,           # hinzugefügt
    VideoSegmentView,            # bleibt importiert, falls anderswo verwendet
    VideoSegmentContentView,
    VideoUploadView,
)

from . import views

urlpatterns = [
    path("video/", VideoListView.as_view(), name="video-list-alias"),
    path("", VideoListView.as_view(), name="video-list"),

    path(
        "video/<int:public_id>/health",
        VideoHealthView.as_view(),
        name="video-health",
    ),

    path(
        "video/<int:video_id>/upload/",
        VideoUploadView.as_view(),
        name="video-upload",
    ),

    # Manifest route must appear before the segment route so index.m3u8 is not captured there.
    re_path(
        r"^video/(?P<movie_id>\d+)/(?P<resolution>[^/]+)/index\.m3u8/?$",
        VideoManifestView.as_view(),
        name="video-segment",  # Legacy-Name beibehalten, falls reverse() verwendet wird
    ),

    # HLS segments (trailing slash optional) while explicitly excluding index.m3u8.
    re_path(
        r"^video/(?P<movie_id>\d+)/(?P<resolution>[^/]+)/(?P<segment>(?!index\.m3u8$)[^/]+)/?$",
        VideoSegmentContentView.as_view(),
        name="video-segment-content",
    ),

    path(
        "video/<int:video_id>/transcode/",
        views.VideoTranscodeView.as_view(),
        name="video-transcode",
    ),

    # Lokaler Auth-Debug-Endpunkt (verschwindet bei DEBUG=False)
    path("_debug/auth", DebugAuthView.as_view(), name="debug-auth"),

    # Queue-Gesundheitsprüfung
    path("_debug/queue", QueueHealthView.as_view(), name="debug-queue-health"),
]

# Zusätzliche Debug-Endpunkte (nur wenn DEBUG=True)
if settings.DEBUG:
    urlpatterns += [
        path(
            "_debug/hls/<int:pub>/<str:res>/manifest",
            HLSManifestDebugView.as_view(),
            name="debug-hls-manifest",
        ),
        path(
            "_debug/renditions",
            AllowedRenditionsDebugView.as_view(),
            name="debug-allowed-renditions",
        ),
        path(
            "_debug/thumbs/<int:public>",
            ThumbsDebugView.as_view(),
            name="debug-thumbs",
        ),
    ]
