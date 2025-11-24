from __future__ import annotations

import pytest
from django.test import override_settings

from videos.domain.services_autotranscode import select_rungs_from_source


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"height": 1080, "bitrate_total": 5_000_000}, ["1080p", "720p", "480p"]),
        ({"height": 1080, "bitrate_total": 3_000_000}, ["720p", "480p"]),
        ({"height": 720, "bitrate_total": 3_000_000}, ["720p", "480p"]),
        ({"height": 680, "bitrate_total": 2_000_000}, ["480p"]),
        ({}, ["480p"]),
    ],
)
def test_select_rungs_matrix(meta, expected):
    assert select_rungs_from_source(meta) == expected


@override_settings(
    AUTOTRANSCODE_POLICY="relaxed",
    ALLOWED_RENDITIONS=("480p", "720p", "1080p"),
)
def test_relaxed_policy_returns_all_allowed():
    meta = {"height": 360, "bitrate_total": 500_000}
    assert select_rungs_from_source(meta) == ["1080p", "720p", "480p"]


def test_missing_metadata_defaults_to_baseline():
    assert select_rungs_from_source(None) == ["480p"]


def test_negative_metadata_treated_as_missing():
    meta = {"height": -720, "bitrate_total": -100}
    assert select_rungs_from_source(meta) == ["480p"]


def test_exact_threshold_enables_1080p():
    meta = {"height": 1000, "bitrate_total": 4_500_000}
    assert select_rungs_from_source(meta) == ["1080p", "720p", "480p"]


def test_derived_bitrate_from_audio_and_video_streams():
    meta = {
        "height": 1080,
        "video_bitrate_kbps": 4100,
        "audio_bitrate_kbps": 500,
    }
    assert select_rungs_from_source(meta) == ["1080p", "720p", "480p"]


def test_high_bitrate_allows_720_even_with_lower_height():
    meta = {"height": 640, "bitrate_total": 3_200_000}
    assert select_rungs_from_source(meta) == ["720p", "480p"]


@override_settings(
    TRANSCODE_ENABLE_720_MIN_SRC_HEIGHT=800,
    TRANSCODE_ENABLE_720_MIN_SRC_BITRATE=3_000_000,
    TRANSCODE_ENABLE_1080_MIN_SRC_HEIGHT=1200,
    TRANSCODE_ENABLE_1080_MIN_SRC_BITRATE=6_000_000,
)
def test_override_settings_respected():
    meta = {"height": 1100, "bitrate_total": 5_500_000}
    assert select_rungs_from_source(meta) == ["720p", "480p"]


def test_extremely_high_values_still_include_all():
    meta = {"height": 4000, "bitrate_total": 25_000_000}
    assert select_rungs_from_source(meta) == ["1080p", "720p", "480p"]
