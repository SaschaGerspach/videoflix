from __future__ import annotations

import json


from videos.domain import utils


class DummyCompletedProcess:
    def __init__(self, payload: dict):
        self.stdout = json.dumps(payload).encode("utf-8")
        self.stderr = b""


def test_probe_media_info_parses_ffprobe(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"data")

    payload = {
        "format": {"duration": "12.9"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "bit_rate": "8000000",
            },
            {"codec_type": "audio", "codec_name": "aac", "bit_rate": "192000"},
        ],
    }

    def fake_run(*args, **kwargs):
        return DummyCompletedProcess(payload)

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    info = utils.probe_media_info(source)

    assert info == {
        "width": 1920,
        "height": 1080,
        "duration_seconds": 12,
        "video_bitrate_kbps": 8000,
        "audio_bitrate_kbps": 192,
        "codec_name": "h264",
    }


def test_probe_media_info_handles_missing_ffprobe(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"data")

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffprobe missing")

    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    assert utils.probe_media_info(source) == {}
