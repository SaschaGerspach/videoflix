"""Public interface for ``videos.api.views``.

Keeps legacy import paths working after the views were split into modules.
"""

from __future__ import annotations

from .media_base import M3U8Renderer, MediaSegmentBaseView, TSRenderer
from .common import ERROR_RESPONSE_REF, _format_validation_error
from .list import VideoListView
from .manifest import VideoSegmentView
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
    "VideoSegmentContentView",
    "VideoTranscodeView",
]

try:  # Optional upload view
    from .upload import VideoUploadView  # noqa: F401
except Exception:  # pragma: no cover
    VideoUploadView = None  # type: ignore[assignment]
else:
    __all__.append("VideoUploadView")
