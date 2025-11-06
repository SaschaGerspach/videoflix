from __future__ import annotations

import importlib
import io
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from videos.domain.choices import VideoCategory
from videos.domain.models import Video


pytestmark = pytest.mark.django_db


def _norm(s: str) -> str:
    import re

    return re.sub(r"\s+", " ", s or "").strip().lower()


def assert_in_any(needle_variants: list[str], hay: str) -> None:
    hay_norm = _norm(hay)
    for needle in needle_variants:
        if _norm(needle) in hay_norm:
            return
    raise AssertionError(f"None of {needle_variants!r} found in output:\n{hay}")


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    settings.MEDIA_ROOT = media_root
    return media_root


def write_source(media_root: Path, real_id: int, payload: bytes | None = None) -> Path:
    target = media_root / "sources" / f"{real_id}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload or b"source")
    return target


def write_manifest(base_dir: Path, *, segments: int = 2, stub: bool = False) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    manifest = base_dir / "index.m3u8"
    if stub:
        manifest.write_text("#EXTM3U\n", encoding="utf-8")
    else:
        lines = ["#EXTM3U"]
        for idx in range(segments):
            lines.append("#EXTINF:10,")
            name = f"{idx:03d}.ts"
            lines.append(name)
            (base_dir / name).write_bytes(f"segment-{idx}".encode("utf-8"))
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def create_video(pk: int, title: str = "Video") -> Video:
    return Video.objects.create(
        pk=pk,
        title=title,
        description="",
        thumbnail_url=f"http://example.com/{pk}.jpg",
        category=VideoCategory.DRAMA,
        is_published=True,
    )


def module(path: str):
    return importlib.import_module(path)


def test_enqueue_transcodes_happy_path_real_ids(media_root, monkeypatch):
    create_video(10, "Ten")
    create_video(9, "Nine")
    write_source(media_root, 9)
    write_source(media_root, 10)

    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    calls: list[tuple[int, list[str]]] = []

    def fake_enqueue(real_id: int, target_resolutions, *, force: bool = False):
        calls.append((real_id, list(target_resolutions)))

    monkeypatch.setattr(enqueue_mod.job_services, "enqueue_transcode", fake_enqueue)

    out, err = io.StringIO(), io.StringIO()
    call_command("enqueue_transcodes", "--real", "9", "10", "--res", "480p", stdout=out, stderr=err)

    assert calls == [(9, ["480p"]), (10, ["480p"])]
    assert_in_any(
        ["Queued 480p", "Queued 1 job(s) for 480p", "Queued 1 jobs for 480p"],
        out.getvalue(),
    )
    assert err.getvalue() == ""


def test_enqueue_transcodes_missing_real_ids_raises(media_root):
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("enqueue_transcodes", "--real", "7", "8", "--res", "720p", stdout=out, stderr=err)

    assert_in_any(
        [
            "Video(s) not found for real id(s): 7, 8",
            "Video(s) not found for real id(s): 7 8",
        ],
        str(excinfo.value),
    )


def test_enqueue_transcodes_force_purges(media_root, monkeypatch):
    video = create_video(42, "Force")
    target_dir = media_root / "hls" / str(video.id) / "480p"
    write_manifest(target_dir, segments=2)
    write_source(media_root, video.id)

    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    calls: list[tuple[int, list[str]]] = []

    def fake_enqueue(real_id: int, target_resolutions, *, force: bool = False):
        calls.append((real_id, list(target_resolutions)))

    monkeypatch.setattr(enqueue_mod.job_services, "enqueue_transcode", fake_enqueue)

    call_command("enqueue_transcodes", "--real", "42", "--res", "480p", "--force")

    assert calls == [(42, ["480p"])]
    assert not any(target_dir.glob("*.ts"))
    assert not (target_dir / "index.m3u8").exists()


def test_enqueue_transcodes_skips_existing(media_root, monkeypatch):
    video = create_video(55, "Skip")
    target_dir = media_root / "hls" / str(video.id) / "720p"
    write_manifest(target_dir, segments=1)
    write_source(media_root, video.id)

    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    monkeypatch.setattr(enqueue_mod.job_services, "enqueue_transcode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")))

    out = io.StringIO()
    call_command("enqueue_transcodes", "--real", "55", "--res", "720p", stdout=out)

    assert_in_any(
        ["Skipped existing renditions", "Skipped existing rendition"],
        out.getvalue(),
    )


def test_enqueue_transcodes_missing_source_reports_failure(media_root):
    video = create_video(77, "NoSource")
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("enqueue_transcodes", "--real", "77", "--res", "480p", stdout=out, stderr=err)

    combined = out.getvalue() + err.getvalue()
    assert_in_any(
        ["no source found", "no source located", "no source file found"],
        combined,
    )
    assert_in_any(
        [
            "Could not enqueue 1 job(s).",
            "Could not enqueue 1 jobs.",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_public_confirm(media_root, monkeypatch):
    video = create_video(5, "Missing")
    write_source(media_root, video.id)
    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        if public_id == 1:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    calls: list[tuple[int, list[str]]] = []

    def fake_enqueue(real_id: int, target_resolutions, *, force: bool = False):
        calls.append((real_id, list(target_resolutions)))

    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", fake_enqueue)

    out = io.StringIO()
    call_command("auto_enqueue_missing", "--public", "1", "--res", "480p", "--confirm", stdout=out)

    assert calls == [(5, ["480p"])]
    stdout = out.getvalue()
    assert_in_any(
        [
            "Missing public ids: 1 -> real 5",
            "Missing public id 1 -> real 5",
        ],
        stdout,
    )
    assert_in_any(
        ["Queued 1 job(s)", "Queued 1 jobs", "Queued 480p for real ids: 5"],
        stdout,
    )


def test_auto_enqueue_missing_dry_run(media_root, monkeypatch):
    video = create_video(6, "DryRun")
    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        if public_id == 2:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)
    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")))

    out = io.StringIO()
    call_command("auto_enqueue_missing", "--public", "2", "--res", "480p", "--dry-run", stdout=out)
    assert_in_any(
        [
            "Dry-run enabled; no jobs enqueued.",
            "Dry run enabled; no jobs enqueued.",
            "Dry-run enabled no jobs enqueued",
        ],
        out.getvalue(),
    )


def test_auto_enqueue_missing_invalid_identifiers(media_root, monkeypatch):
    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod, "_unique", lambda items: [])

    out, err = io.StringIO(), io.StringIO()
    command = auto_mod.Command()
    command.stdout = out
    command.stderr = err
    parser = command.create_parser("manage.py", "auto_enqueue_missing")
    options = parser.parse_args(["--public", "99", "--real", "1234", "--res", "480p"])
    cmd_opts = vars(options)
    args = cmd_opts.pop("args", ())

    with pytest.raises(CommandError) as excinfo:
        command.execute(*args, **cmd_opts)

    combined = out.getvalue() + err.getvalue()
    assert_in_any(
        [
            "Public id 99 does not map to a video.",
            "Public id 99 does not map to video.",
        ],
        combined,
    )
    assert_in_any(
        [
            "Video not found for real id 1234.",
            "Video not found for real id(s): 1234",
        ],
        combined,
    )
    assert_in_any(
        [
            "No valid videos to process.",
            "No valid videos to process!",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_already_present(media_root, monkeypatch):
    video = create_video(11, "Present")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        if public_id == 2:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")))

    out = io.StringIO()
    call_command("auto_enqueue_missing", "--public", "2", "--res", "480p", "--confirm", stdout=out)
    assert_in_any(
        [
            "already contain",
            "already contains",
        ],
        out.getvalue(),
    )


def test_auto_enqueue_missing_force_rebuild(media_root, monkeypatch):
    missing = create_video(60, "Needs")
    existing = create_video(61, "Existing")
    write_source(media_root, missing.id)
    write_source(media_root, existing.id)
    write_manifest(media_root / "hls" / str(existing.id) / "480p", segments=1)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    calls: list[tuple[int, list[str]]] = []

    def fake_enqueue(real_id: int, target_resolutions, *, force: bool = False):
        calls.append((real_id, list(target_resolutions)))

    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", fake_enqueue)

    out = io.StringIO()
    call_command(
        "auto_enqueue_missing",
        "--real",
        str(missing.id),
        str(existing.id),
        "--res",
        "480p",
        "--force",
        "--confirm",
        stdout=out,
    )

    assert calls == [(missing.id, ["480p"]), (existing.id, ["480p"])]
    assert_in_any(
        [
            "Force rebuild enabled",
            "Force rebuild enabled; adding existing renditions",
        ],
        out.getvalue(),
    )
    purge_dir = media_root / "hls" / str(existing.id) / "480p"
    assert not any(purge_dir.glob("*.ts"))
    assert not (purge_dir / "index.m3u8").exists()


def test_auto_enqueue_missing_enqueue_failure_reports(media_root, monkeypatch):
    video = create_video(63, "Fail")
    write_source(media_root, video.id)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)
    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("auto_enqueue_missing", "--real", str(video.id), "--res", "480p", "--confirm", stdout=out, stderr=err)

    combined = out.getvalue() + err.getvalue()
    assert_in_any(
        ["boom", "boom!"],
        combined,
    )
    assert_in_any(
        [
            "Failed to enqueue",
            "Failed to enqueue 1 video(s).",
        ],
        str(excinfo.value),
    )


def check_command_module():
    return module("videos.management.commands.check_renditions")


def test_check_renditions_classification(media_root, monkeypatch):
    v_ok = create_video(7, "OK")
    v_empty = create_video(8, "Empty")
    v_missing = create_video(9, "Missing")

    write_manifest(media_root / "hls" / str(v_ok.id) / "480p", segments=2)
    write_manifest(media_root / "hls" / str(v_empty.id) / "480p", stub=True)

    check_mod = check_command_module()

    def resolve_public(public_id: int):
        mapping = {1: v_ok.id, 2: v_empty.id, 3: v_missing.id}
        if public_id in mapping:
            return mapping[public_id]
        raise Video.DoesNotExist

    monkeypatch.setattr(check_mod, "resolve_public_id", resolve_public)

    out = io.StringIO()
    call_command("check_renditions", "--public", "1", "2", "3", "--res", "480p", stdout=out)
    stdout = out.getvalue()
    norm_out = _norm(stdout)
    assert "ok" in norm_out and "empty" in norm_out and "missing" in norm_out
    import re

    assert re.search(r"\bok\b\s+\d+\s*\|\s*empty\s+\d+\s*\|\s*missing\s+\d+", norm_out)


def test_check_renditions_real_ids_only(media_root):
    video = create_video(11, "OnlyReal")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)

    out = io.StringIO()
    call_command("check_renditions", "--real", str(video.id), "--res", "480p", stdout=out)
    stdout = out.getvalue()
    assert_in_any(
        [f"real: {video.id}", f"real {video.id}"],
        stdout,
    )
    assert "public" not in _norm(stdout)


def test_check_renditions_multiple_resolutions(media_root):
    video = create_video(60, "Multi")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)

    out = io.StringIO()
    call_command("check_renditions", "--real", str(video.id), "--res", "480p", "--res", "720p", stdout=out)
    stdout = out.getvalue()
    assert_in_any(
        ["480p: OK", "480p ok"],
        stdout,
    )
    assert_in_any(
        ["720p", "720 p"],
        stdout,
    )


def test_check_renditions_reports_1080p(media_root):
    video = create_video(61, "Has1080")
    write_manifest(media_root / "hls" / str(video.id) / "1080p", segments=1)

    out = io.StringIO()
    call_command("check_renditions", "--real", str(video.id), "--res", "1080p", stdout=out)
    stdout = out.getvalue()
    assert_in_any(["1080p: OK", "1080p ok"], stdout)


def test_check_renditions_multi_value_single_flag(media_root):
    video = create_video(61, "MultiFlag")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)
    write_manifest(media_root / "hls" / str(video.id) / "720p", segments=1)

    out = io.StringIO()
    call_command("check_renditions", "--real", str(video.id), "--res", "480p", "720p", stdout=out)
    stdout = out.getvalue()
    assert_in_any(["480p: OK", "480p ok"], stdout)
    assert_in_any(["720p: OK", "720p ok", "720p"], stdout)


def test_check_renditions_stub_counts_empty(media_root):
    video = create_video(70, "Stub")
    write_manifest(media_root / "hls" / str(video.id) / "480p", stub=True)

    out = io.StringIO()
    call_command("check_renditions", "--real", str(video.id), "--res", "480p", stdout=out)
    stdout = out.getvalue()
    assert_in_any(["EMPTY", "empty"], stdout)


def test_enqueue_transcodes_requires_identifier():
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("enqueue_transcodes", stdout=out, stderr=err)

    assert_in_any(
        [
            "Provide at least one --public or --real identifier.",
            "Provide at least one public or real identifier.",
        ],
        str(excinfo.value),
    )


def test_enqueue_transcodes_invalid_public_raises(monkeypatch):
    enqueue_mod = module("videos.management.commands.enqueue_transcodes")

    def fake_resolve(public_id: int):
        raise Video.DoesNotExist

    monkeypatch.setattr(enqueue_mod, "resolve_public_id", fake_resolve)

    with pytest.raises(CommandError) as excinfo:
        call_command("enqueue_transcodes", "--public", "1")

    assert_in_any(
        [
            "Public id 1 does not map to a video.",
            "Public id 1 does not map to video.",
        ],
        str(excinfo.value),
    )


def test_enqueue_transcodes_no_videos_after_unique(media_root, monkeypatch):
    video = create_video(101, "Dup")
    write_source(media_root, video.id)

    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    monkeypatch.setattr(enqueue_mod, "_unique", lambda items: [])

    out = io.StringIO()
    call_command("enqueue_transcodes", "--real", str(video.id), stdout=out)

    assert_in_any(
        ["No videos to process.", "No video to process."],
        out.getvalue(),
    )


def test_enqueue_transcodes_dry_run_reports_status(media_root, monkeypatch):
    enqueue_mod = module("videos.management.commands.enqueue_transcodes")

    video_stub = create_video(120, "Stub")
    video_present = create_video(121, "Present")
    video_missing = create_video(122, "Missing")

    write_manifest(media_root / "hls" / str(video_stub.id) / "480p", stub=True)
    write_manifest(media_root / "hls" / str(video_present.id) / "480p", segments=1)

    def resolve_public(public_id: int):
        if public_id == 1:
            return video_stub.id
        raise Video.DoesNotExist

    monkeypatch.setattr(enqueue_mod, "resolve_public_id", resolve_public)

    out = io.StringIO()
    call_command(
        "enqueue_transcodes",
        "--public",
        "1",
        "--real",
        str(video_stub.id),
        str(video_present.id),
        str(video_missing.id),
        "--dry-run",
        stdout=out,
    )

    stdout = out.getvalue()
    assert_in_any(
        ["DRY-RUN: would queue 480p for", "dry run would queue 480p for"],
        stdout,
    )
    assert_in_any(["(stub)", "stub manifest"], stdout)
    assert_in_any(["(existing)", "existing"], stdout)
    assert_in_any(["(missing)", "missing"], stdout)
    assert_in_any(
        ["Mapping: 1 (public) ->", "mapping 1 (public) ->"],
        stdout,
    )
    assert_in_any(
        ["Explicit real ids:", "Explicit real id(s):"],
        stdout,
    )


def test_enqueue_transcodes_stub_manifest_purged(media_root, monkeypatch):
    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    video = create_video(130, "StubPurge")
    target_dir = media_root / "hls" / str(video.id) / "480p"
    write_manifest(target_dir, stub=True)
    write_source(media_root, video.id)

    calls: list[int] = []

    def fake_enqueue(real_id: int, target_resolutions, *, force: bool = False):
        calls.append(real_id)

    def resolve_public(public_id: int):
        if public_id == 3:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(enqueue_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(enqueue_mod.job_services, "enqueue_transcode", fake_enqueue)

    out = io.StringIO()
    call_command(
        "enqueue_transcodes",
        "--public",
        "3",
        "--real",
        str(video.id),
        "--res",
        "480p",
        stdout=out,
    )

    assert calls == [video.id]
    assert not (target_dir / "index.m3u8").exists()
    assert_in_any(
        ["Queued 480p", "Queued 1 job(s) for 480p", "Queued 1 jobs for 480p"],
        out.getvalue(),
    )
    assert_in_any(
        ["public) ->", "(public) ->"],
        out.getvalue(),
    )


def test_enqueue_transcodes_enqueue_failure_collects_errors(media_root, monkeypatch):
    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    video = create_video(131, "Failure")
    write_source(media_root, video.id)

    monkeypatch.setattr(
        enqueue_mod.job_services,
        "enqueue_transcode",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "enqueue_transcodes",
            "--real",
            str(video.id),
            "--res",
            "480p",
            stdout=out,
            stderr=err,
        )

    assert_in_any(
        ["boom", "boom!"],
        err.getvalue(),
    )
    assert_in_any(
        [
            "Could not enqueue 1 job(s).",
            "Could not enqueue 1 jobs.",
        ],
        str(excinfo.value),
    )


def test_enqueue_transcodes_purge_handles_oserror(media_root, monkeypatch):
    enqueue_mod = module("videos.management.commands.enqueue_transcodes")
    command = enqueue_mod.Command()
    command.stdout = io.StringIO()
    command.stderr = io.StringIO()

    missing_dir = media_root / "hls" / "999" / "480p"
    command._purge_rendition_dir(missing_dir)

    target_dir = media_root / "hls" / "998" / "480p"
    write_manifest(target_dir, segments=1)

    manifest = target_dir / "index.m3u8"
    segment = next(target_dir.glob("*.ts"))
    real_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self in {manifest, segment}:
            raise OSError("cannot delete")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    command._purge_rendition_dir(target_dir)

    assert manifest.exists()
    assert segment.exists()


def test_auto_enqueue_missing_requires_identifier():
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("auto_enqueue_missing", stdout=out, stderr=err)

    assert_in_any(
        [
            "Provide at least one --public or --real identifier.",
            "Provide at least one public or real identifier.",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_missing_mapped_video_raises(monkeypatch):
    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        return 404

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)

    with pytest.raises(CommandError) as excinfo:
        call_command("auto_enqueue_missing", "--public", "1", "--res", "480p")

    assert_in_any(
        [
            "Video(s) not found for real id(s): 404",
            "Video(s) not found for real id(s): 404.",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_reports_invalid_and_missing_inputs(media_root, monkeypatch):
    valid = create_video(140, "Valid")
    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        if public_id == 7:
            return valid.id
        raise Video.DoesNotExist

    def filtered_unique(items):
        seen: set[int] = set()
        ordered: list[int] = []
        for item in items:
            if item == 999:
                continue
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod, "_unique", filtered_unique)
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "auto_enqueue_missing",
            "--public",
            "7",
            "8",
            "--real",
            "999",
            "--res",
            "480p",
            "--dry-run",
            stdout=out,
            stderr=err,
        )

    stderr = err.getvalue()
    assert_in_any(
        [
            "Ignored invalid public id(s): 8",
            "Ignored invalid public ids: 8",
        ],
        stderr,
    )
    assert_in_any(
        [
            "Ignored missing real id(s): 999",
            "Ignored missing real ids: 999",
        ],
        stderr,
    )
    assert_in_any(
        [
            "Completed dry-run with invalid identifiers.",
            "Completed dry run with invalid identifiers.",
        ],
        str(excinfo.value),
    )



def test_auto_enqueue_missing_dry_run_invalid_ids_raise(media_root, monkeypatch):
    valid = create_video(141, "DryInvalid")
    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "auto_enqueue_missing",
            "--public",
            "15",
            "--real",
            str(valid.id),
            "--res",
            "480p",
            "--dry-run",
            stdout=out,
            stderr=err,
        )

    assert_in_any(
        [
            "Completed dry-run with invalid identifiers.",
            "Completed dry run with invalid identifiers.",
        ],
        str(excinfo.value),
    )
    assert_in_any(
        [
            "Dry-run enabled; no jobs enqueued.",
            "Dry run enabled; no jobs enqueued.",
            "Dry-run enabled no jobs enqueued",
        ],
        out.getvalue(),
    )


def test_auto_enqueue_missing_all_present_with_invalid_ids_raise(media_root, monkeypatch):
    video = create_video(142, "PresentInvalid")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        if public_id == 9:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "auto_enqueue_missing",
            "--public",
            "9",
            "10",
            "--real",
            str(video.id),
        "--res",
        "480p",
        stdout=out,
        stderr=err,
    )

    assert_in_any(
        [
            "All requested videos already contain the requested rendition.",
            "All requested videos already contain requested rendition.",
        ],
        out.getvalue(),
    )
    assert_in_any(
        [
            "Completed with invalid identifiers.",
            "Completed with invalid identifier(s).",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_prompt_abort_without_invalids(media_root, monkeypatch):
    video = create_video(143, "PromptClean")
    write_source(media_root, video.id)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)
    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    out = io.StringIO()
    call_command(
        "auto_enqueue_missing",
        "--real",
        str(video.id),
        "--res",
        "480p",
        stdout=out,
    )

    assert_in_any(
        ["Aborted by user.", "Aborted by the user."],
        out.getvalue(),
    )


def test_auto_enqueue_missing_prompt_abort_with_invalids(media_root, monkeypatch):
    video = create_video(144, "PromptInvalid")
    write_source(media_root, video.id)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")

    def resolve_public(public_id: int):
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)
    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "auto_enqueue_missing",
            "--public",
            "11",
            "--real",
            str(video.id),
            "--res",
            "480p",
            stdout=out,
            stderr=err,
        )

    assert_in_any(
        ["Aborted by user.", "Aborted by the user."],
        out.getvalue(),
    )
    assert_in_any(
        [
            "Aborted with invalid identifiers.",
            "Aborted with invalid identifier(s).",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_missing_source_collects_failure(media_root, monkeypatch):
    video = create_video(145, "NoSourceAuto")

    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "auto_enqueue_missing",
            "--real",
            str(video.id),
            "--res",
            "480p",
            "--confirm",
            stdout=out,
            stderr=err,
        )

    assert_in_any(
        ["no source found", "no source located", "no source file found"],
        err.getvalue(),
    )
    assert_in_any(
        [
            "Failed to enqueue 1 video(s).",
            "Failed to enqueue one video.",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_invalid_ids_error_after_success(media_root, monkeypatch):
    video = create_video(146, "Mixed")
    write_source(media_root, video.id)

    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    monkeypatch.setattr(auto_mod.job_services, "is_transcode_locked", lambda _vid: False)

    calls: list[int] = []

    def fake_enqueue(real_id: int, target_resolutions, *, force: bool = False):
        calls.append(real_id)

    def resolve_public(public_id: int):
        if public_id == 12:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(auto_mod, "resolve_public_id", resolve_public)
    monkeypatch.setattr(auto_mod.job_services, "enqueue_transcode", fake_enqueue)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "auto_enqueue_missing",
            "--public",
            "12",
            "13",
            "--res",
            "480p",
            "--confirm",
            stdout=out,
            stderr=err,
        )

    assert calls == [video.id]
    assert_in_any(
        [
            "Done. Queued 1 job(s)",
            "Done queued 1 job(s)",
            "Done queued 1 jobs",
        ],
        out.getvalue(),
    )
    assert_in_any(
        [
            "Completed with invalid identifiers.",
            "Completed with invalid identifier(s).",
        ],
        str(excinfo.value),
    )


def test_auto_enqueue_missing_purge_handles_oserror(media_root, monkeypatch):
    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    command = auto_mod.Command()
    command.stdout = io.StringIO()
    command.stderr = io.StringIO()

    missing_dir = media_root / "hls" / "1000" / "480p"
    command._purge_rendition_dir(missing_dir)

    target_dir = media_root / "hls" / "1001" / "480p"
    write_manifest(target_dir, segments=1)

    manifest = target_dir / "index.m3u8"
    segment = next(target_dir.glob("*.ts"))
    real_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self in {manifest, segment}:
            raise OSError("cannot delete")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    command._purge_rendition_dir(target_dir)

    assert manifest.exists()
    assert segment.exists()


def test_auto_enqueue_missing_resolution_status_oserror(media_root, monkeypatch):
    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    command = auto_mod.Command()

    target_dir = media_root / "hls" / "1002" / "480p"
    target_dir.mkdir(parents=True)
    manifest = target_dir / "index.m3u8"
    manifest.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(auto_mod, "is_stub_manifest", lambda path: (_ for _ in ()).throw(OSError("stat")))

    status = command._resolution_status(1002, "480p")
    assert status == "missing"


def test_auto_enqueue_missing_resolution_status_empty(media_root):
    auto_mod = module("videos.management.commands.auto_enqueue_missing")
    command = auto_mod.Command()

    target_dir = media_root / "hls" / "1003" / "480p"
    write_manifest(target_dir, segments=1)
    for ts_file in target_dir.glob("*.ts"):
        ts_file.unlink()

    status = command._resolution_status(1003, "480p")
    assert status == "empty"



def test_check_renditions_defaults_all_resolutions(media_root):
    video = create_video(200, "Defaults")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)

    out = io.StringIO()
    call_command("check_renditions", "--real", str(video.id), stdout=out)

    stdout = out.getvalue()
    norm_out = _norm(stdout)
    assert all(token in norm_out for token in ["480p", "720p", "1080p"])


def test_check_renditions_requires_identifier():
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("check_renditions", stdout=out, stderr=err)

    assert_in_any(
        [
            "Provide at least one --public or --real identifier.",
            "Provide at least one public or real identifier.",
        ],
        str(excinfo.value),
    )


def test_check_renditions_invalid_public_and_no_valid(monkeypatch):
    check_mod = check_command_module()

    def resolve_public(public_id: int):
        raise Video.DoesNotExist

    monkeypatch.setattr(check_mod, "resolve_public_id", resolve_public)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("check_renditions", "--public", "5", stdout=out, stderr=err)

    assert_in_any(
        [
            "Public id 5 does not map to a video.",
            "Public id 5 does not map to video.",
        ],
        err.getvalue(),
    )
    assert_in_any(
        [
            "No valid videos to inspect.",
            "No valid videos to inspect!",
        ],
        str(excinfo.value),
    )


def test_check_renditions_missing_real_logged(media_root, monkeypatch):
    valid = create_video(201, "ValidReal")
    check_mod = check_command_module()

    def resolve_public(public_id: int):
        if public_id == 1:
            return valid.id
        raise Video.DoesNotExist

    monkeypatch.setattr(check_mod, "resolve_public_id", resolve_public)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "check_renditions",
            "--public",
            "1",
            "--real",
            str(valid.id),
            "999",
            "--res",
            "480p",
            stdout=out,
            stderr=err,
        )

    stderr = err.getvalue()
    assert_in_any(
        [
            "Ignored missing real id(s): 999",
            "Ignored missing real ids: 999",
        ],
        stderr,
    )
    assert_in_any(
        [
            "Completed with invalid identifiers.",
            "Completed with invalid identifier(s).",
        ],
        str(excinfo.value),
    )


def test_check_renditions_invalid_ids_raise_after_summary(media_root, monkeypatch):
    video = create_video(202, "MixedCheck")
    write_manifest(media_root / "hls" / str(video.id) / "480p", segments=1)

    check_mod = check_command_module()

    def resolve_public(public_id: int):
        if public_id == 2:
            return video.id
        raise Video.DoesNotExist

    monkeypatch.setattr(check_mod, "resolve_public_id", resolve_public)

    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command(
            "check_renditions",
            "--public",
            "2",
            "3",
            "--real",
            str(video.id),
            "888",
            "--res",
            "480p",
            stdout=out,
            stderr=err,
        )

    stderr = err.getvalue()
    assert_in_any(
        [
            "Ignored invalid public id(s): 3",
            "Ignored invalid public ids: 3",
        ],
        stderr,
    )
    assert_in_any(
        [
            "Ignored missing real id(s): 888",
            "Ignored missing real ids: 888",
        ],
        stderr,
    )
    assert_in_any(
        [
            "Completed with invalid identifiers.",
            "Completed with invalid identifier(s).",
        ],
        str(excinfo.value),
    )


def test_check_renditions_resolution_status_oserror(media_root, monkeypatch):
    check_mod = check_command_module()
    command = check_mod.Command()

    target_dir = media_root / "hls" / "300" / "480p"
    target_dir.mkdir(parents=True)
    manifest = target_dir / "index.m3u8"
    manifest.write_text("#EXTM3U\n", encoding="utf-8")

    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == manifest and not kwargs.get("follow_symlinks", False):
            raise OSError("stat")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    status, count = command._resolution_status(300, "480p")
    assert status == "MISSING"
    assert count == 0


