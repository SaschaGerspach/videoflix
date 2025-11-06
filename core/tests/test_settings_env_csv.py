import importlib
import os

from core import settings as core_settings


def test_env_csv_returns_empty_list_when_unset(monkeypatch):
    monkeypatch.delenv("TEST_EMPTY_CSV", raising=False)
    assert core_settings.env_csv("TEST_EMPTY_CSV") == []


def test_env_csv_parses_comma_separated_values(monkeypatch):
    monkeypatch.setenv("TEST_CSV_VALUES", "http://a, http://b ")
    assert core_settings.env_csv("TEST_CSV_VALUES") == ["http://a", "http://b"]


def test_settings_import_handles_none_like_values(monkeypatch):
    original_module = core_settings
    previous_allowed = list(original_module.ALLOWED_HOSTS)
    previous_csrf = list(original_module.CSRF_TRUSTED_ORIGINS)
    previous_cors = list(original_module.CORS_ALLOWED_ORIGINS)

    class FakeEnviron(dict):
        def get(self, key, default=None):
            if key == "CSRF_TRUSTED_ORIGINS":
                return None
            return super().get(key, default)

    fake_env = FakeEnviron(os.environ.copy())
    fake_env["ALLOWED_HOSTS"] = ""
    fake_env["CORS_ALLOWED_ORIGINS"] = ""

    monkeypatch.setattr(os, "environ", fake_env)

    reloaded = importlib.reload(original_module)

    assert reloaded.CSRF_TRUSTED_ORIGINS == []
    assert reloaded.ALLOWED_HOSTS == []
    assert reloaded.CORS_ALLOWED_ORIGINS == []

    monkeypatch.undo()
    restored = importlib.reload(original_module)
    restored.ALLOWED_HOSTS = previous_allowed
    restored.CSRF_TRUSTED_ORIGINS = previous_csrf
    restored.CORS_ALLOWED_ORIGINS = previous_cors
