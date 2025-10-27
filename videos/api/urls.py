from django.urls import path

from videos.api.views import (VideoListView, VideoSegmentContentView,
                              VideoSegmentView, VideoUploadView)

from . import views

urlpatterns = [
    path("video/", VideoListView.as_view(), name="video-list-alias"),
    path("", VideoListView.as_view(), name="video-list"),
    path("videos/<int:video_id>/upload/",
         VideoUploadView.as_view(), name="video-upload"),
    path(
        "<int:movie_id>/<str:resolution>/index.m3u8",
        VideoSegmentView.as_view(),
        name="video-segment",
    ),
    path(
        "<int:movie_id>/<str:resolution>/<path:segment>/",
        VideoSegmentContentView.as_view(),
        name="video-segment-content",
    ),
    path("videos/<int:video_id>/transcode/",
         views.VideoTranscodeView.as_view(), name="video-transcode"),
]
