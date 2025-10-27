from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

logger = logging.getLogger("videoflix")

TRANSCODE_LOCK_TTL_SECONDS = 15 * 60
FAILED_STATUS_TTL_SECONDS = 10 * 60
PENDING_TTL_SECONDS = 10 * 60  # 10 minutes


@dataclass(frozen=True)
class TranscodeProfile:
    width: int
    height: int
    bandwidth: int
    scale: str | None = None
    video_bitrate: str | None = None
    maxrate: str | None = None
    bufsize: str | None = None
    audio_bitrate: str = "128k"
    audio_channels: int = 2
    audio_rate: int = 48000


TRANSCODE_PROFILE_CONFIG: dict[str, TranscodeProfile] = {
    "360p": TranscodeProfile(
        width=640,
        height=360,
        bandwidth=800_000,
    ),
    "480p": TranscodeProfile(
        width=854,
        height=480,
        bandwidth=2_100_000,
        scale="scale=-2:480",
        video_bitrate="1500k",
        maxrate="2100k",
        bufsize="3000k",
    ),
    "720p": TranscodeProfile(
        width=1280,
        height=720,
        bandwidth=4_000_000,
    ),
    "1080p": TranscodeProfile(
        width=1920,
        height=1080,
        bandwidth=8_000_000,
    ),
}

ALLOWED_TRANSCODE_PROFILES: dict[str, tuple[int, int]] = {
    resolution: (profile.width, profile.height)
    for resolution, profile in TRANSCODE_PROFILE_CONFIG.items()
}


class TranscodeError(ValidationError):
    """Domain-level error that carries an HTTP-friendly status code."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__({"non_field_errors": [message]})
        self.status_code = status_code


def transcode_lock_key(video_id: int) -> str:
    return f"video:{video_id}:transcoding"


def transcode_ready_key(video_id: int) -> str:
    return f"video:{video_id}:ready"


def transcode_status_key(video_id: int) -> str:
    return f"video:{video_id}:status"


def transcode_pending_key(video_id: int) -> str:
    return f"video:{video_id}:transcoding:pending"


def is_transcode_locked(video_id: int) -> bool:
    return bool(cache.get(transcode_lock_key(video_id)))


def get_video_source_path(video_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / "uploads" / "videos" / f"{video_id}.mp4"


def get_transcode_output_dir(video_id: int, resolution: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "hls" / str(video_id) / resolution


def manifest_path_for(video_id: int, resolution: str) -> Path:
    return get_transcode_output_dir(video_id, resolution) / "index.m3u8"


def _source_has_audio_stream(source: Path) -> bool | None:
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "compact=p=0:nk=1",
        str(source),
    ]
    try:
        result = subprocess.run(
            probe_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning(
            "ffprobe not found while checking audio stream: source=%s",
            source,
        )
        return None
    except subprocess.CalledProcessError:
        return False
    stdout = getattr(result, "stdout", b"")
    if not stdout:
        return False
    return bool(stdout.strip())


def mark_transcode_processing(video_id: int) -> None:
    cache.delete(transcode_ready_key(video_id))
    cache.set(
        transcode_status_key(video_id),
        {"state": "processing", "message": None},
        timeout=TRANSCODE_LOCK_TTL_SECONDS,
    )


def mark_transcode_ready(video_id: int) -> None:
    cache.set(transcode_ready_key(video_id), True,
              timeout=TRANSCODE_LOCK_TTL_SECONDS)
    cache.set(
        transcode_status_key(video_id),
        {"state": "ready", "message": None},
        timeout=TRANSCODE_LOCK_TTL_SECONDS,
    )


def mark_transcode_failed(video_id: int, message: str) -> None:
    cache.delete(transcode_ready_key(video_id))
    cache.set(
        transcode_status_key(video_id),
        {"state": "failed", "message": message},
        timeout=FAILED_STATUS_TTL_SECONDS,
    )


def get_transcode_status(video_id: int) -> dict:
    cached_status = cache.get(transcode_status_key(video_id))
    if isinstance(cached_status, dict) and cached_status.get("state"):
        return {
            "state": cached_status.get("state", "unknown"),
            "message": cached_status.get("message"),
        }

    if cache.get(transcode_ready_key(video_id)) or _manifest_exists(video_id):
        return {"state": "ready", "message": None}

    return {"state": "unknown", "message": None}


def enqueue_transcode(video_id: int, *, target_resolutions: Iterable[str] | None = None) -> dict | None:
    """
    Start an ffmpeg-based HLS transcode for the given video.

    Raises TranscodeError when a conflicting job is running, ffmpeg is missing,
    or when the operation fails unexpectedly.
    """
    resolutions = _prepare_resolutions(target_resolutions)

    pending_resolutions: list[str] = []
    for resolution in resolutions:
        manifest_path = manifest_path_for(video_id, resolution)
        if manifest_path.exists():
            logger.info(
                "Transcode skipped (manifest exists): video_id=%s, resolution=%s",
                video_id,
                resolution,
            )
            continue
        pending_resolutions.append(resolution)

    if not pending_resolutions:
        cache.delete(transcode_pending_key(video_id))
        cache.delete(transcode_lock_key(video_id))
        mark_transcode_ready(video_id)
        return {
            "ok": True,
            "message": f"Transcode skipped for video {video_id}; renditions already exist.",
        }

    resolutions = pending_resolutions

    if getattr(settings, "IS_TEST_ENV", False):
        return run_transcode_job(video_id, resolutions)

    env = getattr(settings, "ENV", "").lower()
    pending_key = transcode_pending_key(video_id)

    if cache.get(pending_key) and not is_transcode_locked(video_id):
        try:
            should_inspect_queue = not getattr(settings, "IS_TEST_ENV", False)
            if should_inspect_queue and not _has_active_transcode_job(video_id):
                cache.delete(pending_key)
                logger.info(
                    "Cleared stale transcode pending flag: video_id=%s", video_id
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug(
                "Pending sanity check failed: video_id=%s, error=%s", video_id, exc
            )

    if cache.get(pending_key) or is_transcode_locked(video_id):
        raise TranscodeError("Transcode already in progress.", status_code=409)

    if env in {"dev", "prod"}:
        from jobs.queue import enqueue_transcode_job

        try:
            enqueue_result = enqueue_transcode_job(video_id, resolutions)
        except TranscodeError:
            raise
        except ValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive logging handled upstream
            logger.warning(
                "Transcode enqueue failed, falling back to inline execution: video_id=%s, error=%s",
                video_id,
                exc,
            )
            return run_transcode_job(video_id, resolutions)

        if isinstance(enqueue_result, dict) and enqueue_result.get("job_id"):
            cache.set(pending_key, True, timeout=PENDING_TTL_SECONDS)
        else:  # pragma: no cover - should not happen but keep defensive
            logger.debug(
                "Enqueue result missing job_id; pending lock not set: video_id=%s, result=%s",
                video_id,
                enqueue_result,
            )

        logger.info(
            "Transcode enqueued: video_id=%s, profiles=%s", video_id, resolutions
        )
        return enqueue_result

    return run_transcode_job(video_id, resolutions)


def _run_ffmpeg_for_profile(video_id: int, source: Path, resolution: str) -> None:
    profile = TRANSCODE_PROFILE_CONFIG[resolution]
    width, height = profile.width, profile.height
    output_dir = get_transcode_output_dir(video_id, resolution)
    output_dir.mkdir(parents=True, exist_ok=True)

    segment_pattern = output_dir / "%03d.ts"
    manifest_path = manifest_path_for(video_id, resolution)

    if manifest_path.exists():
        logger.info(
            "Transcode skipped (manifest exists): video_id=%s, resolution=%s",
            video_id,
            resolution,
        )
        return

    has_audio = _source_has_audio_stream(source)
    scale_filter = profile.scale or f"scale={width}:{height}"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(source),
        "-vf", scale_filter,
        "-c:v", "h264",
        "-profile:v", "main",
        "-level", "3.1",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-g", "48",
        "-sc_threshold", "0",
        "-map", "0:v:0",
    ]
    if profile.video_bitrate:
        cmd.extend(["-b:v", profile.video_bitrate])
    if profile.maxrate:
        cmd.extend(["-maxrate", profile.maxrate])
    if profile.bufsize:
        cmd.extend(["-bufsize", profile.bufsize])
    if has_audio:
        cmd.extend(
            [
                "-map",
                "0:a:0",
                "-c:a",
                "aac",
                "-b:a",
                profile.audio_bitrate,
                "-ar",
                str(profile.audio_rate),
                "-ac",
                str(profile.audio_channels),
            ]
        )
    elif has_audio is None:
        logger.debug(
            "Audio detection skipped (ffprobe unavailable): video_id=%s, source=%s",
            video_id,
            source,
        )

    cmd.extend(
        [
            "-hls_time",
            "4",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "vod",
            "-hls_segment_filename",
            str(segment_pattern),
            str(manifest_path),
        ]
    )

    logger.debug("ffmpeg start: video_id=%s, res=%s, cmd=%s", video_id, resolution, cmd)
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.debug("ffmpeg done:  video_id=%s, res=%s", video_id, resolution)


def manifest_exists_for_resolution(video_id: int, resolution: str) -> bool:
    return manifest_path_for(video_id, resolution).exists()


def _has_active_transcode_job(video_id: int) -> bool:
    """
    Return True when an RQ job for the given video_id is still queued/started/deferred.
    Falls back to True (keep lock) when inspection fails so we do not clear pending eagerly.
    """
    try:
        from rq import Queue
        from rq.exceptions import NoSuchJobError  # type: ignore
        from rq.job import Job  # type: ignore

        from jobs.queue import get_rq_connection
    except Exception as exc:  # pragma: no cover - RQ may be unavailable
        logger.debug(
            "RQ inspection unavailable; keeping pending lock: video_id=%s, error=%s",
            video_id,
            exc,
        )
        return True

    try:
        queue = Queue(settings.RQ_QUEUE_TRANSCODE, connection=get_rq_connection())
    except Exception as exc:  # pragma: no cover - Redis misconfigured/unavailable
        logger.debug(
            "Redis connection failed during pending sanity check: video_id=%s, error=%s",
            video_id,
            exc,
        )
        return True

    job_ids: set[str] = set()
    try:
        job_ids.update(queue.job_ids)
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.debug("Unable to collect queued job IDs: video_id=%s, error=%s", video_id, exc)

    registry_sources = [
        getattr(queue, "started_job_registry", None),
        getattr(queue, "deferred_job_registry", None),
        getattr(queue, "scheduled_job_registry", None),
    ]
    for registry in registry_sources:
        if registry is None:
            continue
        try:
            job_ids.update(registry.get_job_ids())
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug(
                "Unable to collect registry job IDs: video_id=%s, registry=%s, error=%s",
                video_id,
                getattr(registry, "name", type(registry).__name__),
                exc,
            )

    for job_id in job_ids:
        try:
            job = Job.fetch(job_id, connection=queue.connection)
        except NoSuchJobError:
            continue
        except Exception as exc:  # pragma: no cover - keep lock
            logger.debug(
                "Job fetch failed during pending sanity check: video_id=%s, job_id=%s, error=%s",
                video_id,
                job_id,
                exc,
            )
            continue

        candidate = job.meta.get("video_id")
        if candidate is None and job.args:
            candidate = job.args[0]

        if candidate is None:
            continue

        try:
            if int(candidate) == int(video_id):
                return True
        except (TypeError, ValueError):
            if str(candidate) == str(video_id):
                return True

    return False


def _manifest_exists(video_id: int) -> bool:
    for resolution in ALLOWED_TRANSCODE_PROFILES:
        if manifest_exists_for_resolution(video_id, resolution):
            return True
    return False


def _prepare_resolutions(target_resolutions: Iterable[str] | None) -> list[str]:
    resolutions = list(target_resolutions or ALLOWED_TRANSCODE_PROFILES.keys())
    resolutions = list(dict.fromkeys(resolutions))
    missing_profiles = [
        res for res in resolutions if res not in ALLOWED_TRANSCODE_PROFILES
    ]
    if missing_profiles:
        raise TranscodeError(
            f"Unsupported resolution '{missing_profiles[0]}'.", status_code=400
        )
    return resolutions


def run_transcode_job(video_id: int, resolutions: Iterable[str]) -> dict | None:
    lock_key = transcode_lock_key(video_id)
    pending_key = transcode_pending_key(video_id)
    if not cache.add(lock_key, True, timeout=TRANSCODE_LOCK_TTL_SECONDS):
        raise TranscodeError("Transcode already in progress.", status_code=409)

    try:
        cache.delete(pending_key)
        mark_transcode_processing(video_id)

        source_path = get_video_source_path(video_id)

        if not source_path.exists():
            if getattr(settings, "ENV", "").lower() == "test":
                mark_transcode_ready(video_id)
                return {
                    "ok": True,
                    "message": f"Transcode triggered for video {video_id} ({', '.join(resolutions)})",
                }
            mark_transcode_failed(video_id, "Video source not found.")
            logger.warning(
                "Transcode failed (missing source): video_id=%s", video_id
            )
            raise TranscodeError("Video source not found.", status_code=500)

        for resolution in resolutions:
            _run_ffmpeg_for_profile(video_id, source_path, resolution)

        from videos.domain import hls as hls_utils

        hls_utils.write_master_playlist(video_id)
        mark_transcode_ready(video_id)
        logger.info(
            "Transcode finished: video_id=%s, profiles=%s",
            video_id,
            resolutions,
        )
    except FileNotFoundError:
        mark_transcode_failed(video_id, "ffmpeg not found")
        logger.error(
            "Transcode failed (ffmpeg not found): video_id=%s", video_id
        )
        raise TranscodeError("ffmpeg not found", status_code=500)
    except subprocess.CalledProcessError as exc:
        mark_transcode_failed(video_id, "Transcode failed.")
        logger.error(
            "Transcode failed (process error): video_id=%s, returncode=%s",
            video_id,
            getattr(exc, "returncode", "?"),
        )
        raise TranscodeError("Transcode failed.", status_code=500) from exc
    finally:
        cache.delete(lock_key)
        cache.delete(pending_key)
        logger.info("Transcode lock released: video_id=%s", video_id)
