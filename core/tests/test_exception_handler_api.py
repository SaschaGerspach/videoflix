from __future__ import annotations

from rest_framework.exceptions import (
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

import core.api.exception_handler as exception_handler_module
from core.api.exception_handler import error_handler


def _make_request() -> Request:
    factory = APIRequestFactory()
    return Request(factory.get("/dummy"))


def test_validation_error_serialised_with_field_errors():
    request = _make_request()
    exc = ValidationError({"name": ["required"]})

    response = error_handler(exc, {"request": request})

    assert response.status_code == 400
    data = response.data
    assert "errors" in data
    assert data["errors"].get("name") == ["required"]


def test_not_authenticated_returns_401_with_non_field_errors():
    request = _make_request()
    exc = NotAuthenticated("credentials missing")

    response = error_handler(exc, {"request": request})

    assert response.status_code == 401
    data = response.data
    assert "errors" in data
    assert data["errors"].get("non_field_errors")


def test_permission_denied_returns_403_payload():
    request = _make_request()
    exc = PermissionDenied("nope")

    response = error_handler(exc, {"request": request})

    assert response.status_code == 403
    data = response.data
    assert "errors" in data
    assert data["errors"].get("non_field_errors")


def test_error_handler_handles_drf_none(monkeypatch):
    request = _make_request()
    monkeypatch.setattr(
        exception_handler_module, "drf_exception_handler", lambda exc, ctx: None
    )

    response = error_handler(ValueError("boom"), {"request": request})

    assert response.status_code == 500
    assert response.data == {"errors": {"non_field_errors": ["boom"]}}


def test_error_handler_preserves_errors_payload(monkeypatch):
    request = _make_request()
    original = Response({"errors": {"foo": ["bar"]}}, status=422)
    monkeypatch.setattr(
        exception_handler_module, "drf_exception_handler", lambda exc, ctx: original
    )

    response = error_handler(RuntimeError("ignored"), {"request": request})

    assert response is original


def test_error_handler_wraps_list_payload(monkeypatch):
    request = _make_request()
    original = Response(["boom"], status=400)
    monkeypatch.setattr(
        exception_handler_module, "drf_exception_handler", lambda exc, ctx: original
    )

    response = error_handler(RuntimeError("list"), {"request": request})

    assert response.status_code == 400
    assert response.data == {"errors": {"non_field_errors": ["boom"]}}
