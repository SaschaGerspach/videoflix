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
