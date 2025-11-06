from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence, Union

from django.conf import settings


def is_stub_manifest(manifest: Union[Path, str, bytes]) -> bool:
    """
    Return True when the manifest exists but does not reference any segments.

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
    """
    Attempt to locate a playable source file for the given ``Video`` instance.

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

    uploads_candidate = Path(settings.MEDIA_ROOT) / "uploads" / "videos" / f"{video.pk}.mp4"
    found = record(uploads_candidate)
    if found:
        return found

    sources_candidate = Path(settings.MEDIA_ROOT) / "sources" / f"{video.pk}.mp4"
    found = record(sources_candidate)
    if found:
        return found

    return None


def ensure_hls_dir(real_id: int, resolution: str) -> Path:
    """
    Ensure the HLS target directory exists inside MEDIA_ROOT/hls/<real>/<resolution>.
    """
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
    """
    Return the expected manifest path inside MEDIA_ROOT/hls/<id>/<res>/index.m3u8.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_hls = (media_root / "hls").resolve()
    manifest = (base_hls / str(real_id) / res / "index.m3u8").resolve()
    try:
        manifest.relative_to(base_hls)
    except ValueError as exc:
        raise ValueError(f"Unsafe manifest path for video {real_id}: {manifest}") from exc
    return manifest


def has_hls_ready(real_id: int, res: str = "480p") -> bool:
    """
    Return True when the manifest exists and is not a stub manifest.
    """
    manifest = find_manifest_path(real_id, res)
    if not manifest.exists():
        return False
    return not is_stub_manifest(manifest)
