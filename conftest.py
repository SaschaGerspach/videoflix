"""Pytest configuration helpers for global options."""

import pytest


def pytest_addoption(parser):
    """Provide a no-op flag so CI can pass --no-success-flakes without extra plugins."""
    parser.addoption(
        "--no-success-flakes",
        action="store_true",
        default=False,
        help="Compatibility shim; does not change test behavior.",
    )


@pytest.fixture(autouse=True)
def _default_strict_autotranscode_policy(settings):
    """Keep legacy strict behavior as the default for tests unless explicitly overridden."""
    if getattr(settings, "AUTOTRANSCODE_POLICY", "relaxed") != "strict":
        settings.AUTOTRANSCODE_POLICY = "strict"
