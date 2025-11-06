from __future__ import annotations

import builtins
import importlib
import sys
from types import ModuleType

import pytest
from django.core.management import CommandError, call_command


def _norm(value: str) -> str:
    import re

    return re.sub(r"\s+", " ", value or "").strip().lower()


def assert_in_any(needle_variants: list[str], haystack: str) -> None:
    norm_hay = _norm(haystack)
    for needle in needle_variants:
        if _norm(needle) in norm_hay:
            return
    raise AssertionError(f"None of {needle_variants!r} found in output:\n{haystack}")


@pytest.mark.django_db
def test_rqworker_transcode_runs_without_redis(settings, monkeypatch, capsys):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    work_calls: list[bool] = []
    get_calls: list[str] = []

    class DummyWorker:
        def work(self, *, burst: bool) -> None:
            work_calls.append(burst)

    def get_worker(queue_name: str) -> DummyWorker:
        get_calls.append(queue_name)
        return DummyWorker()

    fake_module = ModuleType("django_rq")
    fake_module.get_worker = get_worker  # type: ignore[attr-defined]

    monkeypatch.delitem(sys.modules, "django_rq", raising=False)
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name == "django_rq":
            return fake_module
        return original_import_module(name, package)  # pragma: no cover

    monkeypatch.setitem(sys.modules, "django_rq", fake_module)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    call_command("rqworker_transcode")
    captured = capsys.readouterr()

    assert_in_any(
        [
            "starting rq worker for queue 'transcode' (burst=false)",
            "starting rq worker for queue 'transcode' (burst=false).",
        ],
        captured.out,
    )
    assert captured.err == ""
    assert get_calls == ["transcode"]
    assert work_calls == [False]


@pytest.mark.django_db
def test_rqworker_transcode_burst_mode(settings, monkeypatch, capsys):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    work_calls: list[bool] = []
    get_calls: list[str] = []

    class DummyWorker:
        def work(self, *, burst: bool) -> None:
            work_calls.append(burst)

    def get_worker(queue_name: str) -> DummyWorker:
        get_calls.append(queue_name)
        return DummyWorker()

    fake_module = ModuleType("django_rq")
    fake_module.get_worker = get_worker  # type: ignore[attr-defined]

    monkeypatch.delitem(sys.modules, "django_rq", raising=False)
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name == "django_rq":
            return fake_module
        return original_import_module(name, package)  # pragma: no cover

    monkeypatch.setitem(sys.modules, "django_rq", fake_module)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    call_command("rqworker_transcode", "--burst")
    captured = capsys.readouterr()

    assert_in_any(
        [
            "starting rq worker for queue 'transcode' (burst=true)",
            "starting rq worker for queue 'transcode' (burst=true).",
        ],
        captured.out,
    )
    assert captured.err == ""
    assert get_calls == ["transcode"]
    assert work_calls == [True]


@pytest.mark.django_db
def test_rqworker_transcode_import_error(settings, monkeypatch):
    settings.RQ_QUEUE_TRANSCODE = "transcode"
    monkeypatch.delitem(sys.modules, "django_rq", raising=False)

    original_import_module = importlib.import_module

    def failing_import(name: str, package: str | None = None):
        if name == "django_rq":
            raise ImportError("django_rq not installed")
        return original_import_module(name, package)  # pragma: no cover

    original_import = builtins.__import__

    def failing___import__(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "django_rq":
            raise ImportError("django_rq not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(importlib, "import_module", failing_import)
    monkeypatch.setattr(builtins, "__import__", failing___import__)

    with pytest.raises(CommandError) as excinfo:
        call_command("rqworker_transcode")

    assert_in_any(
        [
            "django_rq is required to run this command.",
            "django_rq is required for this command.",
        ],
        str(excinfo.value),
    )
