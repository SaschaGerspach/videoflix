from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Ensure project-level checks and extensions register at the correct time."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:  # pragma: no cover - import side effects only
        # Import modules that register system checks and OpenAPI extensions.
        from . import checks  # noqa: F401
        from .api import spectacular_ext  # noqa: F401
