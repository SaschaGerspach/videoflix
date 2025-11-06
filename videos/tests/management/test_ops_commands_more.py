from __future__ import annotations

import json
from io import StringIO

from django.core.management import call_command


def test_diagnose_backend_cli_json():
    output = StringIO()
    try:
        call_command("diagnose_backend", json=True, stdout=output)
    except SystemExit as exc:
        assert exc.code in (0, 2)
    data = json.loads(output.getvalue())
    assert "summary" in data


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
    data = json.loads(output.getvalue())
    assert "videos" in data
