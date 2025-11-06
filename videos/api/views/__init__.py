"""Public interface for ``videos.api.views``.

Keeps legacy import paths working after the views were split into modules.
"""

from __future__ import annotations

from .media_base import M3U8Renderer, MediaSegmentBaseView, TSRenderer
from .common import ERROR_RESPONSE_REF, _format_validation_error
from .list import VideoListView
from .debug import AllowedRenditionsDebugView, HLSManifestDebugView, ThumbsDebugView
from .health import VideoHealthView
from .queue_health import QueueHealthView
from .manifest import DebugAuthView, VideoManifestView, VideoSegmentView
from .segment import VideoSegmentContentView
from .transcode import VideoTranscodeView

__all__ = [
    "M3U8Renderer",
    "MediaSegmentBaseView",
    "TSRenderer",
    "ERROR_RESPONSE_REF",
    "_format_validation_error",
    "VideoListView",
    "VideoSegmentView",
    "VideoManifestView",
    "VideoSegmentContentView",
    "VideoTranscodeView",
    "DebugAuthView",
    "HLSManifestDebugView",
    "ThumbsDebugView",
    "AllowedRenditionsDebugView",
    "VideoHealthView",
    "QueueHealthView",
]

try:  # Optional upload view
    from .upload import VideoUploadView  # noqa: F401
except Exception:  # pragma: no cover
    VideoUploadView = None  # type: ignore[assignment]
else:
    __all__.append("VideoUploadView")
