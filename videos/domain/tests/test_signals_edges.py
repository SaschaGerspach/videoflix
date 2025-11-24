from __future__ import annotations


from videos.domain.models import Video
from videos.domain.signals import schedule_default_renditions


def test_schedule_default_renditions_skips_raw(monkeypatch):
    called = []
    monkeypatch.setattr(
        "videos.domain.signals.schedule_default_transcodes",
        lambda *args, **kwargs: called.append(True),
    )

    video = Video(pk=1)
    schedule_default_renditions(Video, video, created=True, raw=True)
    assert called == []


def test_schedule_default_renditions_skips_without_pk(monkeypatch):
    called = []
    monkeypatch.setattr(
        "videos.domain.signals.schedule_default_transcodes",
        lambda *args, **kwargs: called.append(True),
    )

    video = Video()
    schedule_default_renditions(Video, video, created=True)
    assert called == []


def test_schedule_default_renditions_triggers(monkeypatch):
    called = []
    monkeypatch.setattr(
        "videos.domain.signals.schedule_default_transcodes",
        lambda video_id: called.append(video_id),
    )

    video = Video(pk=5)
    schedule_default_renditions(Video, video, created=True)
    assert called == [5]


def test_schedule_default_renditions_logs_when_service_errors(monkeypatch, caplog):
    def boom(video_id):
        raise RuntimeError("ffmpeg down")

    monkeypatch.setattr("videos.domain.signals.schedule_default_transcodes", boom)
    video = Video(pk=6)

    with caplog.at_level("WARNING", logger="videoflix"):
        schedule_default_renditions(Video, video, created=True)
    assert "signal failed" in caplog.text
