from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_diagnose_backend_cli_json():
    output = StringIO()
    try:
        call_command("diagnose_backend", json=True, stdout=output)
    except SystemExit as exc:
        assert exc.code in (0, 2)
    payload = json.loads(output.getvalue() or "{}")
    assert "scan" in payload
    if "scan" in payload:
        assert "videos" in payload["scan"]


def test_heal_hls_index_cli_write_and_rebuild(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path.as_posix()
    output = StringIO()
    try:
        call_command(
            "heal_hls_index",
            json=True,
            write=True,
            rebuild_master=True,
            stdout=output,
        )
    except SystemExit as exc:
        assert exc.code in (0, 2)
    payload = json.loads(output.getvalue() or "{}")
    assert "heal" in payload
    if "heal" in payload:
        assert "fixed" in payload["heal"]
