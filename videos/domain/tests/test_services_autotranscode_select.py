from __future__ import annotations

import pytest

from videos.domain import services_autotranscode as autotranscode


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (
            {"height": 2300, "bitrate_total": 15_000_000},
            ["1080p", "720p", "480p"],
        ),
        (
            {"height": 1200, "video_bitrate_kbps": 5000, "audio_bitrate_kbps": 500},
            ["1080p", "720p", "480p"],
        ),
        ({"height": 800, "bitrate_total": 2_800_000}, ["720p", "480p"]),
        ({"height": 700, "bitrate_total": 2_400_000}, ["720p", "480p"]),
        ({}, ["480p"]),
    ),
)
def test_select_rungs_from_source(meta, expected):
    assert autotranscode.select_rungs_from_source(meta) == expected
