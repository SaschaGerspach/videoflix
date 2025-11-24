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
    outcome = {"created": False, "updated": False, "segments": 0, "bytes": 0}

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

    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError:
        return outcome

    if is_stub_manifest(manifest_bytes):
        logger.debug(
            "Skipping HLS index for stub manifest video_id=%s resolution=%s",
            real_id,
            resolution,
        )
        return outcome

    manifest_text = manifest_bytes.decode("utf-8", "ignore")

    if not segment_paths:
        logger.warning(
            "Manifest present but no segments for video_id=%s resolution=%s",
            real_id,
            resolution,
        )

    payloads: dict[str, bytes] = {}
    total_bytes = 0
    for path in segment_paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        payloads[path.name] = data
        total_bytes += len(data)

    outcome["segments"] = len(payloads)
    outcome["bytes"] = total_bytes

    try:
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
    except IntegrityError:
        logger.warning(
            "Integrity error while indexing video_id=%s resolution=%s",
            real_id,
            resolution,
        )
        return outcome

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

    return outcome
