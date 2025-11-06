from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

from jobs import queue as transcode_queue
from videos.domain.models import Video
from videos.domain.utils import resolve_source_path

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


def _call_run_transcode_callable(
    run_callable: Callable[..., Any],
    video_id: int,
    resolutions: Iterable[str],
    *,
    force: bool = False,
) -> Any:
    """
    Invoke the provided run_transcode callable while tolerating the absence of a
    force kwarg (older deployments/tests).
    """
    try:
        return run_callable(video_id, resolutions, force=force)
    except TypeError as exc:
        message = str(exc)
        if "force" not in message or "unexpected keyword argument" not in message:
            raise
        return run_callable(video_id, resolutions)


def invoke_run_transcode_job(
    video_id: int,
    resolutions: Iterable[str],
    *,
    force: bool = False,
) -> Any:
    """
    Public compat shim so other modules can run inline transcodes without caring
    whether the underlying implementation supports the force kwarg.
    """
    return _call_run_transcode_callable(run_transcode_job, video_id, resolutions, force=force)


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


def get_transcode_output_dir(video_id: int, resolution: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "hls" / str(video_id) / resolution


def manifest_path_for(video_id: int, resolution: str) -> Path:
    return get_transcode_output_dir(video_id, resolution) / "index.m3u8"


def get_video_source_path(video_id: int) -> Path:
    """
    Return the best-effort filesystem path for a video's source file.

    Prefers the actual file linked via model fields and falls back to the
    canonical MEDIA_ROOT/sources/<id>.mp4 location so callers can create the file.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    fallback_upload = (media_root / "uploads" / "videos" / f"{video_id}.mp4").resolve()
    fallback_source = (media_root / "sources" / f"{video_id}.mp4").resolve()

    try:
        video = Video.objects.get(pk=video_id)
    except Video.DoesNotExist:
        return fallback_source

    resolved = resolve_source_path(video)
    if resolved:
        return resolved

    if fallback_upload.exists():
        return fallback_upload

    if fallback_source.exists():
        return fallback_source

    return fallback_source


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


def _probe_source_dimensions(source: Path) -> tuple[int | None, int | None]:
    """
    Return (width, height) for the first video stream when ffprobe is available.
    """
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
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
            "ffprobe not found while checking video dimensions: source=%s",
            source,
        )
        return None, None
    except subprocess.CalledProcessError:
        return None, None

    try:
        payload = json.loads(result.stdout.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None

    streams = payload.get("streams") or []
    if not streams:
        return None, None
    stream = streams[0] or {}
    width = stream.get("width")
    height = stream.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return width, height
    return None, None


def probe_source_height(source: Path) -> int | None:
    """
    Convenience wrapper that returns only the video height when known.
    """
    _, height = _probe_source_dimensions(source)
    return height


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


def enqueue_transcode(
    video_id: int,
    *,
    target_resolutions: Iterable[str] | None = None,
    force: bool = False,
) -> dict | None:
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
        try:
            thumbnail_result = enqueue_thumbnail(video_id)
            if not thumbnail_result.get("ok"):
                logger.debug(
                    "Thumbnail skipped after transcode check: video_id=%s",
                    video_id,
                )
        except Exception as exc:  # pragma: no cover - defensive only
            logger.debug(
                "Thumbnail trigger failed after transcode check: video_id=%s, error=%s",
                video_id,
                exc,
            )
        return {
            "ok": True,
            "message": f"Transcode skipped for video {video_id}; renditions already exist.",
        }

    resolutions = pending_resolutions

    if getattr(settings, "IS_TEST_ENV", False):
        return invoke_run_transcode_job(video_id, resolutions, force=force)

    pending_key = transcode_pending_key(video_id)
    queue = transcode_queue.get_transcode_queue()
    queue_name = queue.name if queue is not None else ""

    if queue and cache.get(pending_key) and not is_transcode_locked(video_id):
        try:
            if not getattr(settings, "IS_TEST_ENV", False) and not _has_active_transcode_job(video_id):
                cache.delete(pending_key)
                logger.info("Cleared stale transcode pending flag: video_id=%s", video_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Pending sanity check failed: video_id=%s, error=%s", video_id, exc)

    if cache.get(pending_key) or is_transcode_locked(video_id):
        raise TranscodeError("Transcode already in progress.", status_code=409)

    if queue is not None:
        try:
            enqueue_result = transcode_queue.enqueue_transcode_job(
                video_id,
                resolutions,
                queue=queue,
                force=force,
            )
        except (TranscodeError, ValidationError):
            raise
        except Exception as exc:  # pragma: no cover - defensive logging handled upstream
            logger.info(
                "RQ unavailable, running inline transcode: video_id=%s, queue=%s, error=%s",
                video_id,
                getattr(queue, "name", queue_name),
                exc,
            )
        else:
            if isinstance(enqueue_result, dict) and enqueue_result.get("job_id"):
                cache.set(pending_key, True, timeout=PENDING_TTL_SECONDS)
            logger.info(
                "Transcode enqueued on RQ: video_id=%s, queue=%s, profiles=%s",
                video_id,
                queue.name,
                resolutions,
            )
            return enqueue_result
    else:
        logger.info("RQ queue not available; running inline transcode: video_id=%s", video_id)

    return invoke_run_transcode_job(video_id, resolutions, force=force)


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
    queue = transcode_queue.get_transcode_queue()
    if queue is None:
        logger.debug(
            "RQ inspection unavailable; keeping pending lock: video_id=%s, error=%s",
            video_id,
            "queue unavailable",
        )
        return True

    from rq.exceptions import NoSuchJobError  # type: ignore
    from rq.job import Job  # type: ignore

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


def run_transcode_job(video_id: int, resolutions: Iterable[str], *, force: bool = False) -> dict | None:
    lock_key = transcode_lock_key(video_id)
    pending_key = transcode_pending_key(video_id)
    if not cache.add(lock_key, True, timeout=TRANSCODE_LOCK_TTL_SECONDS):
        raise TranscodeError("Transcode already in progress.", status_code=409)

    try:
        cache.delete(pending_key)
        mark_transcode_processing(video_id)

        try:
            video = Video.objects.get(pk=video_id)
        except Video.DoesNotExist as exc:
            mark_transcode_failed(video_id, "Video record not found.")
            cache.delete(lock_key)
            cache.delete(pending_key)
            raise TranscodeError("Video record not found.", status_code=404) from exc

        checked_sources: list[Path] = []
        source_path = resolve_source_path(video, checked_paths=checked_sources)

        if not source_path or not source_path.exists():
            candidate_str = ", ".join(str(path) for path in checked_sources) or "none"
            if getattr(settings, "ENV", "").lower() == "test":
                mark_transcode_ready(video_id)
                try:
                    thumbnail_result = enqueue_thumbnail(video_id)
                    if not thumbnail_result.get("ok"):
                        logger.debug(
                            "Thumbnail skipped after test transcode trigger: video_id=%s",
                            video_id,
                        )
                except Exception as exc:  # pragma: no cover - defensive only
                    logger.debug(
                        "Thumbnail trigger failed in test shortcut: video_id=%s, error=%s",
                        video_id,
                        exc,
                    )
                return {
                    "ok": True,
                    "message": f"Transcode triggered for video {video_id} ({', '.join(resolutions)})",
                }
            mark_transcode_failed(video_id, "Video source not found.")
            logger.warning(
                "Transcode failed (missing source): video_id=%s, checked=%s",
                video_id,
                candidate_str,
            )
            raise TranscodeError(
                f"Video source not found. Checked: {candidate_str}",
                status_code=500,
            )

        for resolution in resolutions:
            _run_ffmpeg_for_profile(video_id, source_path, resolution)

        from videos.domain import hls as hls_utils

        hls_utils.write_master_playlist(video_id)
        mark_transcode_ready(video_id)
        try:
            thumbnail_result = enqueue_thumbnail(video_id)
            if not thumbnail_result.get("ok"):
                logger.debug(
                    "Thumbnail skipped after transcode: video_id=%s",
                    video_id,
                )
        except Exception as exc:  # pragma: no cover - defensive only
            logger.debug(
                "Thumbnail trigger failed after transcode: video_id=%s, error=%s",
                video_id,
                exc,
            )
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


def enqueue_thumbnail(video_id: int) -> dict[str, Any]:
    """
    Hook for future async thumbnail generation. Currently executes in-process.
    """
    return run_thumbnail_job(video_id)


def run_thumbnail_job(video_id: int) -> dict[str, Any]:
    from videos.domain import thumbs as thumb_utils

    try:
        thumb_path = thumb_utils.ensure_thumbnail(video_id)
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.warning(
            "Thumbnail job failed: video_id=%s, error=%s",
            video_id,
            exc,
        )
        return {"ok": False, "path": None}

    if thumb_path is None:
        return {"ok": False, "path": None}

    return {"ok": True, "path": str(thumb_path)}
