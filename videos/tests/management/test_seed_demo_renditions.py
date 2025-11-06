from __future__ import annotations

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


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    root = tmp_path / "media"
    root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = root
    return root


@pytest.mark.django_db
def test_seed_demo_renditions_without_force(media_root, capsys):
    video_id = 10
    target_dir = media_root / "hls" / str(video_id) / "480p"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "random.txt").write_text("keep", encoding="utf-8")

    call_command("seed_demo_renditions", "--real", str(video_id), "--res", "480p")
    captured = capsys.readouterr()

    assert_in_any(
        [
            "seeded 10/480p (3 segments)",
            "seeded 10/480p (3 segment)",
        ],
        captured.out,
    )
    assert (target_dir / "random.txt").exists()
    assert (target_dir / "index.m3u8").exists()
    for segment in ["000.ts", "001.ts", "002.ts"]:
        assert (target_dir / segment).exists()


@pytest.mark.django_db
def test_seed_demo_renditions_with_force(media_root, capsys):
    video_id = 11
    target_dir = media_root / "hls" / str(video_id) / "480p"
    target_dir.mkdir(parents=True, exist_ok=True)
    leftover = target_dir / "leftover.ts"
    leftover.write_text("old", encoding="utf-8")

    call_command(
        "seed_demo_renditions",
        "--real",
        str(video_id),
        "--res",
        "480p",
        "--force",
    )
    captured = capsys.readouterr()

    assert_in_any(
        [
            "purged 11/480p wegen --force",
            "purged 11/480p wegen --force.",
        ],
        captured.out,
    )
    assert_in_any(
        [
            "seeded 11/480p (3 segments)",
            "seeded 11/480p (3 segment)",
        ],
        captured.out,
    )
    assert not leftover.exists()
    for name in ["index.m3u8", "000.ts", "001.ts", "002.ts"]:
        assert (target_dir / name).exists()


@pytest.mark.django_db
def test_seed_demo_renditions_require_source_missing(media_root, capsys):
    video_id = 12
    target_dir = media_root / "hls" / str(video_id) / "480p"
    target_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(CommandError) as excinfo:
        call_command(
            "seed_demo_renditions",
            "--real",
            str(video_id),
            "--res",
            "480p",
            "--require-source",
        )

    captured = capsys.readouterr()
    assert_in_any(
        [
            "no source found",
            "no source found at",
        ],
        captured.err,
    )
    assert_in_any(
        [
            "Failed to seed 1 video(s).",
            "Failed to seed one video.",
        ],
        str(excinfo.value),
    )
    for name in ["index.m3u8", "000.ts", "001.ts", "002.ts"]:
        assert not (target_dir / name).exists()
