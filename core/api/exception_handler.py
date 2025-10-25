from typing import Any, Dict

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def error_handler(exc, context) -> Response:
    resp = drf_exception_handler(exc, context)
    if resp is None:
        return Response({"errors": {"non_field_errors": [str(exc)]}}, status=500)

    data = resp.data
    if isinstance(data, dict) and "errors" in data:
        return resp
    if isinstance(data, dict) and "detail" in data:
        return Response({"errors": {"non_field_errors": [data["detail"]]}}, status=resp.status_code)
    if isinstance(data, dict):
        return Response({"errors": data}, status=resp.status_code)
    if isinstance(data, list):
        return Response({"errors": {"non_field_errors": data}}, status=resp.status_code)
    return Response({"errors": {"non_field_errors": [str(data)]}}, status=resp.status_code)
