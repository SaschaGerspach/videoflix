from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from collections.abc import Iterable, Sequence

from django.conf import settings

logger = logging.getLogger("videoflix")


def is_stub_manifest(manifest: Path | str | bytes) -> bool:
    """Return True when the manifest exists but does not reference any segments.

    The check is intentionally lightweight: files shorter than or equal to eight bytes
    (just ``#EXTM3U``) or lacking any ``#EXTINF`` directive are treated as stubs.
    """
    text: str | None = None
    path_obj: Path | None = None

    if isinstance(manifest, Path):
        path_obj = manifest
    elif isinstance(manifest, bytes):
        text = manifest.decode("utf-8", "ignore")
    elif isinstance(manifest, str):
        candidate_path = Path(manifest)
        if candidate_path.exists():
            path_obj = candidate_path
        else:
            text = manifest

    if path_obj is not None:
        if not path_obj.exists():
            return False
        try:
            if path_obj.stat().st_size <= 8:
                return True
            with path_obj.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if "#extinf" in line.lower():
                        return False
        except OSError:
            return True
        return True

    if text is None:
        return True

    if len(text) <= 8:
        return True
    for line in text.splitlines():
        if "#extinf" in line.lower():
            return False
    return True


def resolve_source_path(
    video,
    checked_paths: list[Path] | None = None,
    extra_field_names: Sequence[str] | None = None,
) -> Path | None:
    """Attempt to locate a playable source file for the given ``Video`` instance.

    The function inspects common file field names and a set of fallback locations.
    The optional ``checked_paths`` list collects every candidate that was evaluated.
    """
    field_names: Iterable[str] = extra_field_names or (
        "source_file",
        "file",
        "video_file",
        "video",
        "source",
        "path",
    )

    def record(candidate: Path | None) -> Path | None:
        if candidate is None:
            return None
        candidate = candidate.expanduser()
        if checked_paths is not None:
            checked_paths.append(candidate)
        if candidate.exists():
            return candidate
        return None

    for field_name in field_names:
        attr = getattr(video, field_name, None)
        if not attr:
            continue
        candidate: Path | None = None
        if hasattr(attr, "path"):
            try:
                candidate = Path(attr.path)
            except (TypeError, ValueError):
                candidate = None
        elif isinstance(attr, (str, Path)):
            candidate = Path(attr)
        found = record(candidate)
        if found:
            return found

    uploads_candidate = (
        Path(settings.MEDIA_ROOT) / "uploads" / "videos" / f"{video.pk}.mp4"
    )
    found = record(uploads_candidate)
    if found:
        return found

    sources_candidate = Path(settings.MEDIA_ROOT) / "sources" / f"{video.pk}.mp4"
    found = record(sources_candidate)
    if found:
        return found

    return None


def ensure_hls_dir(real_id: int, resolution: str) -> Path:
    """Ensure the HLS target directory exists inside MEDIA_ROOT/hls/<real>/<resolution>."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_hls = (media_root / "hls").resolve()
    target = (base_hls / str(real_id) / resolution).resolve()
    try:
        target.relative_to(base_hls)
    except ValueError as exc:
        raise ValueError(f"Unsafe HLS path for video {real_id}: {target}") from exc

    target.mkdir(parents=True, exist_ok=True)
    return target


def find_manifest_path(real_id: int, res: str = "480p") -> Path:
    """Return the expected manifest path inside MEDIA_ROOT/hls/<id>/<res>/index.m3u8."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_hls = (media_root / "hls").resolve()
    manifest = (base_hls / str(real_id) / res / "index.m3u8").resolve()
    try:
        manifest.relative_to(base_hls)
    except ValueError as exc:
        raise ValueError(
            f"Unsafe manifest path for video {real_id}: {manifest}"
        ) from exc
    return manifest


def has_hls_ready(real_id: int, res: str = "480p") -> bool:
    """Return True when the manifest exists and is not a stub manifest."""
    manifest = find_manifest_path(real_id, res)
    if not manifest.exists():
        return False
    return not is_stub_manifest(manifest)


def probe_media_info(path: Path) -> dict[str, int | str]:
    """Use ffprobe to collect basic metadata about a media file.
    Returns an empty dict when ffprobe is unavailable or probing fails.
    """
    if not path or not Path(path).exists():
        return {}

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("ffprobe not found while probing media info: path=%s", path)
        return {}
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "ffprobe failed while probing media info: path=%s code=%s",
            path,
            getattr(exc, "returncode", "?"),
        )
        return {}

    try:
        payload = json.loads(result.stdout.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}

    format_meta = payload.get("format") or {}
    streams = payload.get("streams") or []

    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), {}
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), {}
    )

    width = video_stream.get("width")
    height = video_stream.get("height")
    codec_name = video_stream.get("codec_name")
    video_bitrate = _normalize_bitrate(video_stream.get("bit_rate"))
    audio_bitrate = _normalize_bitrate(audio_stream.get("bit_rate"))

    duration_seconds: int | None = None
    duration_value = format_meta.get("duration")
    if duration_value is not None:
        try:
            duration_seconds = int(float(duration_value))
        except (TypeError, ValueError):
            duration_seconds = None

    return {
        "width": width,
        "height": height,
        "duration_seconds": duration_seconds,
        "video_bitrate_kbps": video_bitrate,
        "audio_bitrate_kbps": audio_bitrate,
        "codec_name": codec_name,
    }


def _normalize_bitrate(raw_value) -> int | None:
    if raw_value in (None, "", 0):
        return None
    try:
        bits_per_second = int(raw_value)
    except (TypeError, ValueError):
        return None
    if bits_per_second <= 0:
        return None
    return max(1, bits_per_second // 1000)
