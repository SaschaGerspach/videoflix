# jobs/domain/services.py (falls bereits vorhanden – sonst später!)
def enqueue_transcode(video_id: int, *, target_resolutions: list[str]) -> None:
    """
    Queue a transcode job for a video. No-op in dev; later wire Celery/Redis.
    """
    pass
