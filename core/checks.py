from django.core.cache import caches
from django.core.checks import Warning, register


@register()
def redis_cache_reachable_check(app_configs, **kwargs):  # pragma: no cover - exercised via Django system checks
    try:
        default_cache = caches["default"]
    except Exception as exc:
        return [
            Warning(
                "Redis cache unreachable; falling back may degrade performance",
                id="videoflix.E001",
                hint=str(exc),
            )
        ]

    test_key = "__videoflix_redis_check__"
    try:
        default_cache.set(test_key, 1, 5)
        if default_cache.get(test_key) != 1:
            raise RuntimeError("Cache read-after-write failed")
    except Exception as exc:
        return [
            Warning(
                "Redis cache unreachable; falling back may degrade performance",
                id="videoflix.E001",
                hint=str(exc),
            )
        ]
    finally:
        try:
            default_cache.delete(test_key)
        except Exception:
            pass

    return []


@register()
def hls_routing_order_check(app_configs, **kwargs):  # pragma: no cover - executed via system checks
    warnings = []

    try:
        from django.urls import Resolver404, resolve
        from videos.api.views import VideoManifestView, VideoSegmentContentView
    except Exception as exc:
        return [
            Warning(
                "HLS routing check skipped; views unavailable.",
                id="videoflix.W009",
                hint=str(exc),
            )
        ]

    manifest_message = "index.m3u8 is not served by VideoManifestView. Check URL order."
    segment_message = "*.ts is not served by VideoSegmentContentView. Check URL patterns."

    try:
        manifest_match = resolve("/api/video/1/720p/index.m3u8")
    except Resolver404:
        warnings.append(Warning(manifest_message, id="videoflix.W010"))
    except Exception as exc:
        warnings.append(Warning(manifest_message, id="videoflix.W010", hint=str(exc)))
    else:
        if getattr(manifest_match.func, "view_class", None) is not VideoManifestView:
            warnings.append(Warning(manifest_message, id="videoflix.W010"))

    try:
        segment_match = resolve("/api/video/1/720p/000.ts")
    except Resolver404:
        warnings.append(Warning(segment_message, id="videoflix.W011"))
    except Exception as exc:
        warnings.append(Warning(segment_message, id="videoflix.W011", hint=str(exc)))
    else:
        if getattr(segment_match.func, "view_class", None) is not VideoSegmentContentView:
            warnings.append(Warning(segment_message, id="videoflix.W011"))

    return warnings
