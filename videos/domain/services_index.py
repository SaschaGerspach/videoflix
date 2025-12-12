from __future__ import annotations

import logging
from pathlib import Path

from django.core.cache import cache
from django.db import IntegrityError, transaction

from .models import Video, VideoSegment, VideoStream
from .utils import find_manifest_path, is_stub_manifest

logger = logging.getLogger(__name__)

_CACHE_KEY_TEMPLATE = "videos:index-rendition:{real}:{res}"


def fs_rendition_exists(real_id: int, resolution: str) -> tuple[bool, Path, list[Path]]:
    """
    Return a tuple describing the manifest and segment files for a rendition.

    The boolean indicates whether the manifest exists on disk. The second entry is
    the canonical manifest path. The third contains a list of ``.ts`` files inside the
    rendition directory. Any file-system errors are swallowed and simply reported as
    a missing rendition.
    """
    try:
        manifest_path = find_manifest_path(real_id, resolution)
    except ValueError:
        # Unsafe resolution or traversal attempt; report as missing.
        return False, Path(), []

    try:
        if not manifest_path.exists():
            return False, manifest_path, []
    except OSError:
        return False, manifest_path, []

    try:
        base_dir = manifest_path.parent
        segments = [path for path in sorted(base_dir.glob("*.ts")) if path.is_file()]
    except OSError:
        segments = []

    return True, manifest_path, segments


def _should_run(real_id: int, resolution: str, timeout: int = 10) -> bool:
    key = _CACHE_KEY_TEMPLATE.format(real=real_id, res=resolution)
    try:
        return cache.add(key, "1", timeout=timeout)
    except Exception:  # pragma: no cover - cache backend misconfiguration
        return True


def index_existing_rendition(real_id: int, resolution: str) -> dict[str, object]:
    """
    Persist manifest text and segment binaries from the file system into the database.
    """
    outcome = _init_index_outcome()
    manifest_found, manifest_path, segment_paths = fs_rendition_exists(
        real_id, resolution
    )
    if not manifest_found:
        return outcome

    if not _should_run(real_id, resolution):
        outcome["segments"] = len(segment_paths)
        return outcome

    if not Video.objects.filter(pk=real_id).exists():
        logger.warning(
            "Skipping HLS index: video %s missing for %s", real_id, resolution
        )
        return outcome

    manifest_bytes = _read_manifest_bytes(manifest_path)
    if manifest_bytes is None:
        return outcome

    if is_stub_manifest(manifest_bytes):
        logger.debug(
            "Skipping HLS index for stub manifest video_id=%s resolution=%s",
            real_id,
            resolution,
        )
        return outcome

    manifest_text = manifest_bytes.decode("utf-8", "ignore")
    _log_missing_segments(segment_paths, real_id, resolution)

    payloads, total_bytes = _collect_segment_payloads(segment_paths)
    outcome["segments"] = len(payloads)
    outcome["bytes"] = total_bytes

    try:
        outcome = _persist_rendition_data(
            real_id, resolution, manifest_text, payloads, outcome
        )
    except IntegrityError:
        logger.warning(
            "Integrity error while indexing video_id=%s resolution=%s",
            real_id,
            resolution,
        )
        return outcome

    _log_index_result(real_id, resolution, outcome)
    return outcome


def _init_index_outcome() -> dict[str, object]:
    """Return the default outcome structure for indexing results."""
    return {"created": False, "updated": False, "segments": 0, "bytes": 0}


def _read_manifest_bytes(manifest_path: Path) -> bytes | None:
    """Read manifest bytes safely, returning None on failure."""
    try:
        return manifest_path.read_bytes()
    except OSError:
        return None


def _log_missing_segments(segment_paths: list[Path], real_id: int, resolution: str):
    """Log when a manifest exists but no segments were found."""
    if segment_paths:
        return
    logger.warning(
        "Manifest present but no segments for video_id=%s resolution=%s",
        real_id,
        resolution,
    )


def _collect_segment_payloads(
    segment_paths: list[Path],
) -> tuple[dict[str, bytes], int]:
    """Read segment files into payloads and return payload dict plus total bytes."""
    payloads: dict[str, bytes] = {}
    total_bytes = 0
    for path in segment_paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        payloads[path.name] = data
        total_bytes += len(data)
    return payloads, total_bytes


def _persist_rendition_data(
    real_id: int,
    resolution: str,
    manifest_text: str,
    payloads: dict[str, bytes],
    outcome: dict[str, object],
) -> dict[str, object]:
    """Persist manifest and segment payloads inside a transaction."""
    with transaction.atomic():
        stream, created = VideoStream.objects.get_or_create(
            video_id=real_id,
            resolution=resolution,
            defaults={"manifest": manifest_text},
        )
        outcome["created"] = created
        updated = False

        if not created and stream.manifest != manifest_text:
            stream.manifest = manifest_text
            stream.save(update_fields=["manifest"])
            updated = True

        if payloads:
            existing_qs = VideoSegment.objects.filter(
                stream=stream,
                name__in=list(payloads.keys()),
            )
            existing = {segment.name: segment for segment in existing_qs}

            for name, payload in payloads.items():
                segment = existing.get(name)
                if segment is None:
                    VideoSegment.objects.create(
                        stream=stream, name=name, content=payload
                    )
                    updated = True
                    continue
                current_bytes = bytes(segment.content or b"")
                if current_bytes != payload:
                    segment.content = payload
                    segment.save(update_fields=["content"])
                    updated = True

        outcome["updated"] = updated
    return outcome


def _log_index_result(
    real_id: int, resolution: str, outcome: dict[str, object]
) -> None:
    """Log indexing outcomes while preserving prior messages."""
    if outcome["created"] or outcome["updated"]:
        logger.info(
            "Indexed HLS rendition video_id=%s resolution=%s created=%s updated=%s segments=%s bytes=%s",
            real_id,
            resolution,
            outcome["created"],
            outcome["updated"],
            outcome["segments"],
            outcome["bytes"],
        )
    else:
        logger.debug(
            "HLS rendition already indexed video_id=%s resolution=%s",
            real_id,
            resolution,
        )
