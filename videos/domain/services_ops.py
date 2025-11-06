from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from django.urls import Resolver404, resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from videos.api.views.manifest import VideoManifestView
from videos.api.views.segment import VideoSegmentContentView
from videos.domain import selectors, selectors_public
from videos.domain.models import Video, VideoStream
from videos.domain.thumbs import ensure_thumbnail, get_thumbnail_path
from videos.domain.utils import find_manifest_path, is_stub_manifest

try:  # Optional helper for master playlist regeneration.
    from videos.domain.hls import write_master_playlist
except Exception:  # pragma: no cover - helper optional
    write_master_playlist = None  # type: ignore[assignment]


class _DiagnosticsUser:
    """Lightweight user object for selector discovery (staff-like access)."""

    id = 0
    is_staff = True
    is_superuser = True

    @property
    def is_authenticated(self) -> bool:  # pragma: no cover - trivial
        return True


@dataclass
class _ViewSample:
    public_id: int
    real_id: int
    resolution: str
    manifest_path: str
    segment_name: str


def run_diagnose_backend(
    *,
    settings,
    media_root: Path,
    explicit_public: Sequence[int] | None = None,
    requested_res: Sequence[str] | None = None,
) -> dict[str, Any]:
    resolutions = _normalise_resolutions(settings, requested_res)
    resolution_hint = resolutions[0] if resolutions else "480p"

    report: dict[str, Any] = {}
    total_failures = 0
    total_warnings = 0
    global_warnings: list[str] = []

    settings_summary = _collect_settings_summary(settings, media_root)
    total_warnings += len(settings_summary.get("warnings", []))
    report["settings"] = settings_summary

    videos_result = _collect_videos(settings, explicit_public, resolution_hint)
    report["videos"] = videos_result["items"]
    total_failures += videos_result["failures"]
    total_warnings += len(videos_result["warnings"])
    global_warnings.extend(videos_result["warnings"])
    resolved_pairs = videos_result["resolved"]

    fs_result = _inspect_filesystem(settings, media_root, resolved_pairs, resolutions)
    report["fs_checks"] = fs_result["entries"]
    total_failures += fs_result["failures"]
    total_warnings += len(fs_result["warnings"])
    global_warnings.extend(fs_result["warnings"])

    routing_result = _check_routing(resolved_pairs, resolutions)
    report["routing"] = routing_result
    total_failures += routing_result.get("failures", 0)

    view_result, header_report, header_warnings = _invoke_views(fs_result["entries"])
    report["views"] = view_result
    report["headers"] = header_report
    total_failures += view_result.get("failures", 0)
    view_warnings = view_result.get("warnings", [])
    total_warnings += len(view_warnings)
    global_warnings.extend(view_warnings)
    total_warnings += len(header_warnings)
    global_warnings.extend(header_warnings)

    debug_result = _check_debug_endpoints(settings)
    report["debug"] = debug_result
    total_failures += debug_result.get("failures", 0)

    summary = {
        "failures": total_failures,
        "warnings": total_warnings,
    }
    if global_warnings:
        summary["warning_messages"] = global_warnings

    report["summary"] = summary

    return report


def format_diagnose_backend_text(report: dict[str, Any], verbose: bool) -> str:
    lines: list[str] = []
    summary = report.get("summary", {})
    settings_summary = report.get("settings", {})
    icon = "✅"
    message = (
        f"{icon} Settings: debug={settings_summary.get('debug')} "
        f"media_root={settings_summary.get('media_root')}"
    )
    warnings = settings_summary.get("warnings", [])
    if warnings and not verbose:
        message += f" warnings={len(warnings)}"
    lines.append(message)
    if verbose:
        lines.append(f"   allowed_renditions={settings_summary.get('allowed_renditions')}")
        lines.append(f"   canonical_renditions={settings_summary.get('canonical_renditions')}")
        lines.append(f"   static_url={settings_summary.get('static_url')}")
        lines.append(f"   redis_url={settings_summary.get('redis_url')}")
        lines.append(f"   rq={settings_summary.get('rq')}")
        for warning in settings_summary.get("warnings", []):
            lines.append(f"   warning: {warning}")

    videos = report.get("videos", [])
    resolved_count = sum(1 for item in videos if "real" in item and not item.get("error"))
    videos_ok = resolved_count == len(videos)
    icon = "✅" if videos_ok else "❌"
    lines.append(f"{icon} Videos: {resolved_count}/{len(videos)} resolved")
    if verbose:
        for item in videos:
            if item.get("error"):
                lines.append(f"   public={item['public']} error={item['error']}")
            else:
                lines.append(
                    f"   public={item['public']} real={item['real']} title={item.get('title')} "
                    f"created={item.get('created_at')}"
                )

    fs_entries = report.get("fs_checks", [])
    fs_failures = sum(1 for entry in fs_entries if entry.get("failure"))
    icon = "✅" if fs_failures == 0 else "❌"
    lines.append(
        f"{icon} Filesystem: {len(fs_entries) - fs_failures}/{len(fs_entries)} manifests healthy"
    )
    if verbose:
        for entry in fs_entries:
            status = "ok"
            if entry.get("failure"):
                status = "failed"
            lines.append(
                f"   public={entry['public']} res={entry['resolution']} status={status} "
                f"manifest={entry.get('manifest')}"
            )

    routing = report.get("routing", {})
    paths = routing.get("paths", [])
    routing_failures = routing.get("failures", 0)
    icon = "✅" if routing_failures == 0 else "❌"
    lines.append(f"{icon} Routing: {len(paths) - routing_failures}/{len(paths)} paths ok")
    if verbose:
        for path_info in paths:
            if path_info.get("ok"):
                lines.append(f"   {path_info['path']} -> {path_info.get('matched')}")
            else:
                lines.append(
                    f"   {path_info['path']} ! {path_info.get('error', 'Unexpected resolver result')}"
                )

    views_info = report.get("views", {})
    view_failures = views_info.get("failures", 0)
    icon = "✅" if view_failures == 0 else "❌"
    lines.append(
        f"{icon} Views: manifest={views_info.get('manifest', {}).get('status')} "
        f"segment={views_info.get('segment', {}).get('status')}"
    )
    if verbose and views_info.get("sample"):
        lines.append(f"   sample={views_info['sample']}")
        for field in ("manifest", "segment"):
            details = views_info.get(field, {})
            lines.append(f"   {field}: {details}")

    headers_block = report.get("headers", {}) or {}
    manifest_header = headers_block.get("manifest") or {}
    segment_header = headers_block.get("segment") or {}
    header_entries = [
        entry for entry in (manifest_header, segment_header) if isinstance(entry, dict) and entry
    ]
    headers_ok = all(entry.get("ok", True) for entry in header_entries) if header_entries else True
    icon = "✅" if headers_ok else "⚠️"
    header_message = (
        f"{icon} Headers: manifest={manifest_header.get('ctype')} "
        f"segment={segment_header.get('ctype')}"
    )
    notes_count = sum(len(entry.get("notes", [])) for entry in header_entries)
    if notes_count and not verbose:
        header_message += f" notes={notes_count}"
    lines.append(header_message)
    if verbose:
        lines.append(f"   manifest_headers={manifest_header}")
        lines.append(f"   segment_headers={segment_header}")
        if headers_block.get("cors_options"):
            lines.append(f"   cors_options={headers_block['cors_options']}")

    debug_info = report.get("debug", {})
    debug_failures = debug_info.get("failures", 0)
    icon = "✅" if debug_failures == 0 else "❌"
    queue_status = debug_info.get("queue_health", {}).get("importable")
    lines.append(f"{icon} Debug endpoints: queue_health importable={queue_status}")
    if verbose:
        lines.append(f"   queue_health={debug_info.get('queue_health')}")
        lines.append(f"   debug_renditions={debug_info.get('debug_renditions')}")

    failure_count = summary.get("failures", 0)
    warning_count = summary.get("warnings", 0)
    lines.append(f"Summary: failures={failure_count} warnings={warning_count}")

    return "\n".join(lines)


def run_heal_hls_index(
    *,
    settings,
    media_root: Path,
    publics: Sequence[int] | None = None,
    resolutions: Sequence[str] | None = None,
    write: bool = False,
    rebuild_master: bool = False,
) -> dict[str, Any]:
    report: list[dict[str, Any]] = []

    public_ids, discovery_warnings = _collect_public_ids(settings, publics, resolutions)
    if not public_ids:
        return {
            "videos": [],
            "warnings": discovery_warnings + ["No videos to process (public IDs missing or discovery empty)."],
        }

    stream_fields = _stream_fields()

    seen_public: set[int] = set()
    ordered_public: list[int] = []
    for value in public_ids:
        if value in seen_public:
            continue
        seen_public.add(value)
        ordered_public.append(value)

    for public_id in ordered_public:
        entry = {
            "public": public_id,
            "real": None,
            "actions": [],
            "warnings": [],
            "errors": [],
        }
        report.append(entry)

        try:
            real_id = selectors.resolve_public_id(public_id)
        except Video.DoesNotExist:
            entry["warnings"].append("Could not resolve public ID to a video record.")
            continue
        entry["real"] = real_id

        video = Video.objects.filter(pk=real_id).first()
        if video is None:
            entry["warnings"].append("Video missing in database.")
            continue

        resolution_set = _normalise_resolutions(settings, resolutions)
        stream_cache = {
            stream.resolution: stream
            for stream in VideoStream.objects.filter(video_id=real_id, resolution__in=resolution_set)
        }

        ready_any = False

        for resolution in resolution_set:
            info = _scan_rendition(media_root, real_id, resolution)
            entry.setdefault("details", {})[resolution] = {
                "manifest": str(info.manifest_path) if info.manifest_path else None,
                "exists": info.exists,
                "bytes": info.bytes,
                "is_stub": info.is_stub,
                "ts_count": info.ts_count,
                "min_bytes": info.min_bytes,
                "max_bytes": info.max_bytes,
                "errors": info.errors,
            }

            if info.errors:
                entry["errors"].extend(info.errors)

            stream = stream_cache.get(resolution)

            if info.has_files:
                ready_any = True

            manifest_text: str | None = None
            if info.exists and info.manifest_path is not None:
                manifest_text, manifest_error = _read_manifest_text(info)
                if manifest_error:
                    entry["errors"].append(manifest_error)

            if stream is None:
                if info.has_files:
                    entry["actions"].append(f"create_stream {resolution}")
                    if write:
                        error = _create_stream(real_id, resolution, manifest_text, info, stream_fields)
                        if error:
                            entry["errors"].append(error)
                continue

            if not info.exists:
                entry["warnings"].append(f"stale stream {resolution}: manifest missing.")
                continue
            if info.is_stub:
                entry["warnings"].append(f"stale stream {resolution}: manifest stub.")
                continue
            if info.ts_count == 0:
                entry["warnings"].append(f"stale stream {resolution}: no segments on disk.")
                continue

            update_needed = False
            update_fields: list[str] = []

            if "manifest" in stream_fields and manifest_text is not None:
                if getattr(stream, "manifest", "") != manifest_text:
                    update_needed = True
                    update_fields.append("manifest")

            if "segments" in stream_fields:
                current_segments = getattr(stream, "segments", None)
                if current_segments not in (info.ts_count, None):
                    update_needed = True
                    update_fields.append("segments")

            if update_needed:
                entry["actions"].append(f"update_stream {resolution}")
                if write and update_fields:
                    try:
                        if "manifest" in update_fields and manifest_text is not None:
                            stream.manifest = manifest_text
                        if "segments" in update_fields:
                            setattr(stream, "segments", info.ts_count)
                        stream.save(update_fields=update_fields)
                    except Exception as exc:  # pragma: no cover - defensive guard
                        entry["errors"].append(f"{resolution}: stream update failed ({exc})")

        if rebuild_master and ready_any:
            if write:
                if write_master_playlist:
                    try:
                        write_master_playlist(real_id)
                        entry["actions"].append("rebuild_master")
                    except Exception as exc:  # pragma: no cover - defensive
                        msg = f"Master rebuild failed: {exc}"
                        entry["warnings"].append(msg)
                else:
                    entry["warnings"].append("Master rebuild skipped: helper unavailable.")
            else:
                entry["actions"].append("rebuild_master")
                entry["warnings"].append("Master rebuild skipped (dry-run).")

        thumb_path = get_thumbnail_path(real_id)
        if not thumb_path.exists() and ready_any:
            if write:
                try:
                    generated = ensure_thumbnail(real_id)
                except Exception as exc:  # pragma: no cover - defensive
                    entry["warnings"].append(f"Thumbnail generation error: {exc}")
                else:
                    if generated is None:
                        entry["warnings"].append("Thumbnail generation did not produce a file.")
                    else:
                        entry["actions"].append("generate_thumb")
            else:
                entry["actions"].append("generate_thumb")
                entry["warnings"].append("Thumbnail generation skipped (dry-run).")

    return {"videos": report, "warnings": discovery_warnings}


def format_heal_hls_index_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for note in result.get("warnings", []):
        lines.append(f"⚠️ discovery: {note}")

    for item in result.get("videos", []):
        icon = "✅"
        if item["errors"]:
            icon = "❌"
        elif item["warnings"]:
            icon = "⚠️"
        actions = ", ".join(item["actions"]) if item["actions"] else "no-op"
        lines.append(f"{icon} video public={item['public']} real={item.get('real')} actions={actions}")
        for warn in item["warnings"]:
            lines.append(f"   warning: {warn}")
        for err in item["errors"]:
            lines.append(f"   error: {err}")
    return "\n".join(lines)


def _normalise_resolutions(settings, requested: Sequence[str] | None) -> list[str]:
    candidates: Iterable[str] | None = requested
    canonical = getattr(settings, "CANONICAL_RENDITIONS", None)
    if not candidates:
        candidates = canonical or getattr(
            settings,
            "ALLOWED_RENDITIONS",
            getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p")),
        )
    values: list[str] = []
    seen: set[str] = set()
    for item in candidates or []:
        text = (str(item) if item is not None else "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    if not values:
        values = ["480p", "720p"]
    return values


def _collect_settings_summary(settings, media_root: Path) -> dict[str, Any]:
    allowed = tuple(
        getattr(
            settings,
            "ALLOWED_RENDITIONS",
            getattr(settings, "VIDEO_ALLOWED_RENDITIONS", ("480p", "720p")),
        )
    )
    canonical = tuple(getattr(settings, "CANONICAL_RENDITIONS", ())) or allowed

    summary = {
        "debug": bool(getattr(settings, "DEBUG", False)),
        "media_root": str(media_root.resolve()),
        "static_url": getattr(settings, "STATIC_URL", None),
        "allowed_renditions": list(allowed),
        "canonical_renditions": list(canonical),
        "redis_url": getattr(settings, "REDIS_URL", None),
        "rq": {
            "queue_default": getattr(settings, "RQ_QUEUE_DEFAULT", None),
            "queue_transcode": getattr(settings, "RQ_QUEUE_TRANSCODE", None),
            "redis_url": getattr(settings, "RQ_REDIS_URL", None),
        },
        "warnings": [],
    }
    if not canonical:
        summary["warnings"].append("No canonical renditions configured; using fallback list.")
    return summary


def _collect_videos(settings, explicit_public: Sequence[int] | None, resolution_hint: str) -> dict[str, Any]:
    failures = 0
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    resolved: list[tuple[int, int, Video]] = []

    public_ids: list[int] = []
    discovered = False
    discovery_error: str | None = None

    if explicit_public is None:
        try:
            discovered_entries = selectors_public.list_for_user_with_public_ids(
                _DiagnosticsUser(),
                ready_only=True,
                res=resolution_hint,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            discovery_error = f"Selector discovery failed: {exc}"
        else:
            for entry in discovered_entries:
                try:
                    public_ids.append(int(entry.get("id")))
                except (TypeError, ValueError):
                    continue
            discovered = True
    else:
        public_ids.extend(int(value) for value in explicit_public)

    if discovery_error:
        failures += 1
        warnings.append(discovery_error)

    if not public_ids:
        warnings.append("No public video IDs supplied or discovered.")
        return {
            "items": items,
            "failures": failures,
            "warnings": warnings,
            "resolved": resolved,
        }

    unique_public_ids: list[int] = []
    seen_public: set[int] = set()
    for value in public_ids:
        if value in seen_public:
            continue
        seen_public.add(value)
        unique_public_ids.append(value)

    real_ids: list[int] = []
    for public_id in unique_public_ids:
        entry: dict[str, Any] = {"public": public_id}
        try:
            real_id = selectors.resolve_public_id(public_id)
        except Video.DoesNotExist:
            entry["error"] = "Could not resolve public ID to a video."
            failures += 1
            items.append(entry)
            continue
        real_ids.append(real_id)
        entry["real"] = real_id
        items.append(entry)

    if not real_ids:
        return {
            "items": items,
            "failures": failures,
            "warnings": warnings,
            "resolved": resolved,
        }

    videos = Video.objects.in_bulk(real_ids)
    for entry in items:
        real_id = entry.get("real")
        if not real_id:
            continue
        video = videos.get(real_id)
        if video is None:
            entry["error"] = "Video record missing in database."
            failures += 1
            continue
        entry["title"] = getattr(video, "title", None)
        created_at = getattr(video, "created_at", None)
        entry["created_at"] = created_at.isoformat() if created_at else None
        resolved.append((entry["public"], real_id, video))

    if discovered and not resolved:
        warnings.append(
            f"Ready-only selector returned {len(public_ids)} videos but none resolved for resolution '{resolution_hint}'."
        )

    return {
        "items": items,
        "failures": failures,
        "warnings": warnings,
        "resolved": resolved,
    }


def _segment_name_candidates(name: str) -> list[str]:
    cleaned = (name or "").strip().replace("\\", "/")
    if not cleaned:
        return []
    candidates = [cleaned]
    parts = cleaned.rsplit("/", 1)
    prefix = ""
    leaf = cleaned
    if len(parts) == 2:
        prefix = parts[0] + "/"
        leaf = parts[1]
    base, dot, ext = leaf.rpartition(".")
    if dot and ext.lower() == "ts" and base.isdigit():
        width = max(len(base), 3)
        padded_leaf = f"{int(base):0{width}d}.ts"
        padded_name = f"{prefix}{padded_leaf}"
        if padded_name not in candidates:
            candidates.append(padded_name)
    return candidates


def _inspect_filesystem(settings, media_root: Path, resolved, resolutions: Sequence[str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    failures = 0
    warnings: list[str] = []

    for public_id, real_id, _video in resolved:
        for resolution in resolutions or ["480p"]:
            entry = {
                "public": public_id,
                "real": real_id,
                "resolution": resolution,
                "manifest": None,
                "exists": False,
                "size": None,
                "is_stub": None,
                "ts_count": 0,
                "min_ts_bytes": None,
                "max_ts_bytes": None,
                "first_segment": None,
                "segment_on_disk": None,
                "segment_zero_on_disk": None,
                "failure": False,
            }
            entries.append(entry)
            try:
                manifest_path = find_manifest_path(real_id, resolution)
            except Exception as exc:
                entry["error"] = f"find_manifest_path failed: {exc}"
                entry["failure"] = True
                failures += 1
                continue

            entry["manifest"] = str(manifest_path)

            if not manifest_path.exists():
                entry["failure"] = True
                failures += 1
                continue

            entry["exists"] = True

            try:
                entry["size"] = manifest_path.stat().st_size
            except OSError as exc:
                entry["error"] = f"Manifest stat failed: {exc}"

            try:
                stub_flag = is_stub_manifest(manifest_path)
            except Exception as exc:  # pragma: no cover - defensive guard
                stub_flag = True
                entry["error"] = f"Stub detection failed: {exc}"
            entry["is_stub"] = bool(stub_flag)

            try:
                manifest_text = manifest_path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                entry.setdefault("errors", []).append(f"Manifest read failed: {exc}")
                manifest_text = ""

            segments = []
            if manifest_text:
                for line in manifest_text.splitlines():
                    token = line.strip()
                    if not token or token.startswith("#"):
                        continue
                    if token.lower().endswith(".ts"):
                        segments.append(token)
                if segments:
                    entry["first_segment"] = segments[0]

            entry["ts_count"] = len(segments)

            available_sizes: list[int] = []
            zero_segment_name: str | None = None
            for name in segments:
                candidates = _segment_name_candidates(name)
                found_name: str | None = None
                for candidate in candidates:
                    candidate_path = Path(manifest_path.parent, candidate).resolve()
                    if candidate_path.exists():
                        found_name = candidate
                        try:
                            size = candidate_path.stat().st_size
                        except OSError:
                            size = None
                        if size is not None:
                            available_sizes.append(size)
                        break
                if found_name:
                    if entry["segment_on_disk"] is None:
                        entry["segment_on_disk"] = found_name
                    leaf_name = found_name.rsplit("/", 1)[-1]
                    if leaf_name in ("000.ts", "0000.ts") and zero_segment_name is None:
                        zero_segment_name = found_name
            entry["segment_zero_on_disk"] = zero_segment_name

            if available_sizes:
                entry["min_ts_bytes"] = min(available_sizes)
                entry["max_ts_bytes"] = max(available_sizes)

            entry["failure"] = entry["failure"] or entry["ts_count"] == 0 or bool(entry["is_stub"])
            if entry["failure"]:
                failures += 1

    return {
        "entries": entries,
        "failures": failures,
        "warnings": warnings,
    }


def _check_routing(resolved, resolutions: Sequence[str]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    failures = 0

    if not resolved:
        return {"paths": results, "failures": failures}

    public_ids = [pair[0] for pair in resolved]
    unique_public = list(dict.fromkeys(public_ids))
    targets = resolutions or ["480p"]

    for public_id in unique_public:
        for resolution in targets:
            manifest_path = f"/api/video/{public_id}/{resolution}/index.m3u8"
            segment_path = f"/api/video/{public_id}/{resolution}/000.ts"
            results.append(_resolve_path(manifest_path, VideoManifestView))
            results.append(_resolve_path(segment_path, VideoSegmentContentView))

    failures = sum(1 for item in results if not item["ok"])

    return {"paths": results, "failures": failures}


def _resolve_path(path: str, expected_view) -> dict[str, Any]:
    result = {"path": path, "expected": expected_view.__name__, "ok": False}
    try:
        match = resolve(path)
    except Resolver404 as exc:
        result["error"] = f"Resolver404: {exc}"
        return result
    except Exception as exc:  # pragma: no cover - defensive guard
        result["error"] = f"Resolve failed: {exc}"
        return result

    view_class = getattr(match.func, "view_class", None)
    result["matched"] = getattr(view_class, "__name__", repr(match.func))
    result["ok"] = view_class is expected_view
    if not result["ok"]:
        result["error"] = (
            f"Unexpected view class: expected {expected_view.__name__}, got {result['matched']}"
        )
    return result


def _invoke_views(fs_entries: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    if not fs_entries:
        view_info = {
            "failures": 0,
            "warnings": ["View checks skipped: no filesystem entries available."],
        }
        return view_info, {}, []

    sample: _ViewSample | None = None
    for entry in fs_entries:
        if not entry.get("exists"):
            continue
        if entry.get("failure"):
            continue
        segment_name = entry.get("segment_zero_on_disk") or entry.get("segment_on_disk")
        if not segment_name:
            continue
        sample = _ViewSample(
            public_id=entry["public"],
            real_id=entry["real"],
            resolution=entry["resolution"],
            manifest_path=entry.get("manifest") or "",
            segment_name=segment_name,
        )
        break

    if sample is None:
        view_info = {
            "failures": 0,
            "warnings": ["View checks skipped: no ready manifest with segments on disk."],
        }
        return view_info, {}, []

    failures = 0
    warnings: list[str] = []
    header_warnings: list[str] = []
    header_report: dict[str, Any] = {}
    results: dict[str, Any] = {
        "sample": {
            "public": sample.public_id,
            "resolution": sample.resolution,
            "segment": sample.segment_name,
        }
    }

    factory = APIRequestFactory()
    auth_user = _DiagnosticsUser()

    manifest_request = factory.get(f"/api/video/{sample.public_id}/{sample.resolution}/index.m3u8")
    force_authenticate(manifest_request, user=auth_user)
    manifest_response = None
    manifest_status: int | None = None
    manifest_error: Exception | None = None
    try:
        manifest_response = VideoManifestView.as_view()(
            manifest_request,
            movie_id=sample.public_id,
            resolution=sample.resolution,
        )
        manifest_status = getattr(manifest_response, "status_code", None)
    except Exception as exc:  # pragma: no cover - defensive guard
        manifest_error = exc

    if manifest_error is not None:
        results["manifest"] = {"ok": False, "error": f"View raised exception: {manifest_error}"}
        failures += 1
        record = _baseline_header_record()
        record["ok"] = False
        note = f"Manifest view raised exception: {manifest_error}"
        record["notes"].append(note)
        header_report["manifest"] = record
        header_warnings.append(note)
    else:
        manifest_ok = manifest_status == 200
        if not manifest_ok:
            failures += 1
            warnings.append(f"Manifest view returned status {manifest_status}.")
        results["manifest"] = {"ok": manifest_ok, "status": manifest_status}
        if manifest_response is not None:
            record, record_warnings = _evaluate_headers(
                manifest_response,
                kind="manifest",
                expected_tokens=("mpegurl",),
                status_code=manifest_status,
            )
            header_report["manifest"] = record
            header_warnings.extend(record_warnings)

    if manifest_response is not None and hasattr(manifest_response, "close"):
        try:
            manifest_response.close()
        except Exception:
            pass

    segment_request = factory.get(
        f"/api/video/{sample.public_id}/{sample.resolution}/{sample.segment_name}"
    )
    force_authenticate(segment_request, user=auth_user)
    segment_response = None
    segment_status: int | None = None
    segment_error: Exception | None = None
    try:
        segment_response = VideoSegmentContentView.as_view()(
            segment_request,
            movie_id=sample.public_id,
            resolution=sample.resolution,
            segment=sample.segment_name,
        )
        segment_status = getattr(segment_response, "status_code", None)
    except Exception as exc:  # pragma: no cover - defensive guard
        segment_error = exc

    if segment_error is not None:
        results["segment"] = {"ok": False, "error": f"View raised exception: {segment_error}"}
        failures += 1
        record = _baseline_header_record()
        record["ok"] = False
        note = f"Segment view raised exception: {segment_error}"
        record["notes"].append(note)
        header_report["segment"] = record
        header_warnings.append(note)
    else:
        segment_ok = segment_status == 200
        if not segment_ok:
            failures += 1
            warnings.append(f"Segment view returned status {segment_status}.")
        results["segment"] = {"ok": segment_ok, "status": segment_status}
        if segment_response is not None:
            record, record_warnings = _evaluate_headers(
                segment_response,
                kind="segment",
                expected_tokens=("video/vnd.dlna.mpeg-tts", "mpeg-tts"),
                status_code=segment_status,
            )
            header_report["segment"] = record
            header_warnings.extend(record_warnings)

    if segment_response is not None and hasattr(segment_response, "close"):
        try:
            segment_response.close()
        except Exception:
            pass

    cors_info, cors_warnings = _maybe_check_cors_options(sample, factory)
    if cors_info:
        header_report["cors_options"] = cors_info
    header_warnings.extend(cors_warnings)

    results["failures"] = failures
    if warnings:
        results["warnings"] = warnings
    return results, header_report, header_warnings


def _baseline_header_record() -> dict[str, Any]:
    return {
        "ok": True,
        "ctype": None,
        "cache_control": None,
        "content_disposition": None,
        "disposition_inline": False,
        "notes": [],
    }


def _response_header(response, name: str) -> Any:
    if response is None:
        return None
    if hasattr(response, "headers"):
        try:
            return response.headers.get(name)
        except Exception:
            pass
    try:
        return response[name]
    except Exception:
        return None


def _evaluate_headers(
    response,
    *,
    kind: str,
    expected_tokens: Sequence[str],
    status_code: int | None,
) -> tuple[dict[str, Any], list[str]]:
    record = _baseline_header_record()
    warnings: list[str] = []
    label = kind.capitalize()

    def add_note(message: str, mark_not_ok: bool = True) -> None:
        if mark_not_ok:
            record["ok"] = False
        record["notes"].append(message)
        warnings.append(message)

    if response is None or status_code != 200:
        add_note(f"{label}: Header check skipped due to status {status_code}.")
        return record, warnings

    ctype = _response_header(response, "Content-Type")
    record["ctype"] = ctype
    if not ctype:
        add_note(f"{label}: Content-Type header missing.")
    else:
        lower_ctype = ctype.lower()
        if not any(token in lower_ctype for token in expected_tokens):
            expected = ", ".join(expected_tokens)
            add_note(f"{label}: Content-Type '{ctype}' lacks expected token(s): {expected}.")

    disposition = _response_header(response, "Content-Disposition")
    record["content_disposition"] = disposition
    disposition_ok = bool(disposition and "inline;" in disposition.lower())
    record["disposition_inline"] = disposition_ok
    if not disposition:
        add_note(f"{label}: Content-Disposition header missing.")
    elif not disposition_ok:
        add_note(f"{label}: Content-Disposition '{disposition}' missing 'inline;'.")

    cache_control = _response_header(response, "Cache-Control")
    record["cache_control"] = cache_control
    if not cache_control:
        add_note(f"{label}: Cache-Control header missing.")
    else:
        from django.conf import settings as global_settings

        if not getattr(global_settings, "DEBUG", False):
            lower_cache = cache_control.lower()
            if not any(flag in lower_cache for flag in ("public", "no-cache")):
                add_note(
                    f"{label}: Cache-Control '{cache_control}' missing recommended directives (public or no-cache).",
                    mark_not_ok=False,
                )

    return record, warnings


def _maybe_check_cors_options(
    sample: _ViewSample,
    factory: APIRequestFactory,
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    info: dict[str, Any] | None = None

    try:
        cors_spec = importlib.util.find_spec("corsheaders")
    except Exception:  # pragma: no cover - defensive guard
        cors_spec = None
    if cors_spec is None:
        return info, warnings

    from django.conf import settings as global_settings

    allow_all = bool(getattr(global_settings, "CORS_ALLOW_ALL_ORIGINS", False))
    allowed_origins = getattr(global_settings, "CORS_ALLOWED_ORIGINS", [])
    if not allow_all and not allowed_origins:
        return info, warnings

    origin = "https://example.com"
    if allowed_origins:
        try:
            origin = next(iter(allowed_origins)) or origin
        except Exception:
            origin = "https://example.com"

    request = factory.options(
        f"/api/video/{sample.public_id}/{sample.resolution}/index.m3u8",
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
    )

    info = {"origin": origin}
    try:
        response = VideoManifestView.as_view()(
            request,
            movie_id=sample.public_id,
            resolution=sample.resolution,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        info["error"] = f"OPTIONS request failed: {exc}"
        warnings.append(f"CORS OPTIONS request raised exception: {exc}")
        return info, warnings

    status_code = getattr(response, "status_code", None)
    info["status"] = status_code
    header_names = [
        "Access-Control-Allow-Origin",
        "Access-Control-Allow-Methods",
        "Access-Control-Allow-Headers",
    ]
    header_values: dict[str, Any] = {}
    missing: list[str] = []
    for name in header_names:
        value = _response_header(response, name)
        header_values[name] = value
        if not value:
            missing.append(name)
    info["headers"] = header_values
    if missing:
        note = f"Missing: {', '.join(missing)}"
        info["notes"] = [note]
        warnings.append(f"CORS headers missing for manifest OPTIONS: {', '.join(missing)}.")
    if hasattr(response, "close"):
        try:
            response.close()
        except Exception:
            pass

    return info, warnings


def _collect_public_ids(settings, requested_public, resolutions) -> tuple[list[int], list[str]]:
    warnings: list[str] = []
    public_ids: list[int] = []

    if requested_public:
        for value in requested_public:
            try:
                public_ids.append(int(value))
            except (TypeError, ValueError):
                warnings.append(f"Invalid public ID '{value}' ignored.")
        return public_ids, warnings

    try:
        entries = selectors_public.list_for_user_with_public_ids(
            _DiagnosticsUser(),
            ready_only=True,
            res=_normalise_resolutions(settings, resolutions)[0],
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        warnings.append(f"Public selector discovery failed: {exc}")
        return [], warnings

    for entry in entries:
        try:
            public_ids.append(int(entry.get("id")))
        except (TypeError, ValueError):
            continue
    return public_ids, warnings


@dataclass
class _RenditionInfo:
    resolution: str
    manifest_path: Path | None
    exists: bool
    bytes: int | None
    is_stub: bool | None
    ts_count: int
    min_bytes: int | None
    max_bytes: int | None
    errors: list[str]

    @property
    def has_files(self) -> bool:
        return bool(self.exists and self.is_stub is False and self.ts_count > 0)


def _scan_rendition(media_root: Path, real_id: int, resolution: str) -> _RenditionInfo:
    errors: list[str] = []
    manifest_path: Path | None = None
    exists = False
    file_bytes: int | None = None
    is_stub: bool | None = None
    ts_count = 0
    min_bytes: int | None = None
    max_bytes: int | None = None

    try:
        manifest_path = find_manifest_path(real_id, resolution)
    except Exception as exc:
        errors.append(f"{resolution}: manifest path resolution failed ({exc})")
        return _RenditionInfo(
            resolution,
            None,
            False,
            None,
            None,
            0,
            None,
            None,
            errors,
        )

    try:
        exists = manifest_path.exists()
    except OSError as exc:
        errors.append(f"{resolution}: manifest existence check failed ({exc})")
        exists = False

    if exists:
        try:
            file_bytes = manifest_path.stat().st_size
        except OSError as exc:
            errors.append(f"{resolution}: manifest stat failed ({exc})")
            file_bytes = None
        try:
            is_stub = is_stub_manifest(manifest_path)
        except Exception as exc:  # pragma: no cover - defensive guard
            errors.append(f"{resolution}: stub detection failed ({exc})")
            is_stub = None

        try:
            raw_paths = sorted(manifest_path.parent.glob("*.ts"))
        except OSError as exc:
            errors.append(f"{resolution}: segment listing failed ({exc})")
            raw_paths = []

        segment_paths: list[Path] = []
        for path in raw_paths:
            try:
                if path.is_file():
                    segment_paths.append(path)
            except OSError:
                continue

        sizes: list[int] = []
        for path in segment_paths:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            sizes.append(size)

        ts_count = len(segment_paths)
        if sizes:
            min_bytes = min(sizes)
            max_bytes = max(sizes)

    return _RenditionInfo(
        resolution,
        manifest_path,
        exists,
        file_bytes,
        is_stub,
        ts_count,
        min_bytes,
        max_bytes,
        errors,
    )


def _stream_fields() -> set[str]:
    return {
        field.name
        for field in VideoStream._meta.get_fields()
        if getattr(field, "concrete", False) and not getattr(field, "many_to_many", False)
    }


def _create_stream(
    real_id: int,
    resolution: str,
    manifest_text: str | None,
    info: _RenditionInfo,
    stream_fields: set[str],
) -> str | None:
    if manifest_text is None:
        return f"{resolution}: manifest content unavailable for stream creation."

    payload: dict[str, Any] = {}
    if "manifest" in stream_fields:
        payload["manifest"] = manifest_text
    if "segments" in stream_fields:
        payload["segments"] = info.ts_count

    try:
        VideoStream.objects.create(
            video_id=real_id,
            resolution=resolution,
            **payload,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        return f"{resolution}: create stream failed ({exc})"
    return None


def _read_manifest_text(info: _RenditionInfo) -> tuple[str | None, str | None]:
    if not info.manifest_path:
        return None, f"{info.resolution}: manifest path unavailable."
    try:
        return info.manifest_path.read_text(encoding="utf-8", errors="ignore"), None
    except OSError as exc:
        return None, f"{info.resolution}: manifest read failed ({exc})"


def _check_debug_endpoints(settings) -> dict[str, Any]:
    failures = 0
    result: dict[str, Any] = {}

    queue_info: dict[str, Any] = {"importable": False}
    try:
        module = importlib.import_module("videos.api.views.queue_health")
    except Exception as exc:  # pragma: no cover - defensive guard
        queue_info["error"] = f"Import failed: {exc}"
        failures += 1
    else:
        queue_info["importable"] = hasattr(module, "QueueHealthView")
        if not queue_info["importable"]:
            queue_info["error"] = "QueueHealthView not found."
            failures += 1
    result["queue_health"] = queue_info

    debug_info: dict[str, Any] = {"importable": None}
    if getattr(settings, "DEBUG", False):
        try:
            debug_module = importlib.import_module("videos.api.views.debug")
        except Exception as exc:  # pragma: no cover - defensive guard
            debug_info["importable"] = False
            debug_info["error"] = f"Import failed: {exc}"
            failures += 1
        else:
            has_view = hasattr(debug_module, "AllowedRenditionsDebugView")
            debug_info["importable"] = has_view
            if not has_view:
                debug_info["error"] = "AllowedRenditionsDebugView not found."
                failures += 1
    else:
        debug_info["skipped"] = "DEBUG disabled; renditions debug view not required."
    result["debug_renditions"] = debug_info

    result["failures"] = failures
    return result
