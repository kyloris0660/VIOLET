"""Focused tests for the Phase 4.4-P2R-F2 external gallery-dl adapter pilot."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.enums import ContentClassEnum, FileTypeEnum, RatingEnum  # noqa: E402
from app.models import Media  # noqa: E402
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as pilot  # noqa: E402


CONFIG_ENV_KEYS = [
    "VIOLET_ENV",
    "VIOLET_STORAGE_ROOT",
    "TEST_DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "VIOLET_GALLERY_DL_COMMAND",
]


def _clear_config_env(monkeypatch):
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _media(db, media_id: int, *, filename: str, content_class=ContentClassEnum.anime) -> Media:
    item = Media(
        id=media_id,
        filename=filename,
        path=f"media/original/{filename}",
        thumbnail_path=f"media/thumbnails/{filename}",
        hash=f"{media_id:064x}",
        file_type=FileTypeEnum.image,
        mime_type="image/jpeg",
        file_size=123,
        width=16,
        height=12,
        rating=RatingEnum.safe,
        source=None,
        content_class=content_class,
    )
    db.add(item)
    db.commit()
    return item


def _entrypoint(mode: str = "explicit_operator_command_mode") -> pilot.GalleryDlEntrypoint:
    return pilot.GalleryDlEntrypoint(
        mode=mode,
        command=("py", "-m", "gallery_dl"),
        version="1.32.1",
        available=True,
        reproducibility_status="conditional_explicit_operator_command",
    )


def _completed(args, returncode=0, stdout="1.32.1\n", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_sample_size_and_generated_record_gates_fail_closed():
    pilot.enforce_sample_size(10)
    with pytest.raises(pilot.SampleGateError, match="sample_size_exceeds_max_10"):
        pilot.enforce_sample_size(11)
    with pytest.raises(pilot.SampleGateError, match="generated_output_exceeds_max_records"):
        pilot.enforce_record_count(11, 10)
    with pytest.raises(pilot.SampleGateError, match="max_records_exceeds_max_10"):
        pilot.enforce_record_count(0, 11)


def test_command_construction_uses_argument_list_and_no_shell(tmp_path):
    sample = pilot.SelectedSample(
        work_id="100000001",
        page_indexes=(0,),
        content_classes=("anime",),
        local_media_ids_private=(1,),
        local_basenames_private=("100000001_p0.jpg",),
        has_p0_page=True,
        has_non_p0_page=False,
        duplicate_or_ambiguous=False,
    )
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["shell"] = kwargs.get("shell")
        return _completed(args, stdout=json.dumps([3, {"id": 100000001, "num": 0, "filename": "100000001_p0"}]))

    raw_dir = pilot.PHASE_OUTPUT_DIR / "raw-unit-test-command"
    shutil.rmtree(ROOT / raw_dir, ignore_errors=True)
    try:
        result = pilot.run_metadata_commands([sample], _entrypoint(), raw_dir, runner=fake_run)
        assert result[0].success is True
        assert isinstance(seen["args"], list)
        assert seen["shell"] is False
        assert seen["args"][:3] == ["py", "-m", "gallery_dl"]
        assert "--dump-json" in seen["args"]
        assert "--no-download" in seen["args"]
    finally:
        shutil.rmtree(ROOT / raw_dir, ignore_errors=True)


def test_project_python_module_mode_uses_sys_executable():
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return _completed(args, stdout="1.32.1\n")

    entrypoint = pilot.probe_gallery_dl_entrypoint(runner=fake_run, python_executable="C:/venv/python.exe")
    assert entrypoint.mode == "project_python_module_mode"
    assert entrypoint.command == ("C:/venv/python.exe", "-m", "gallery_dl")
    assert seen[0] == ["C:/venv/python.exe", "-m", "gallery_dl", "--version"]


def test_external_executable_mode_is_reported(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "C:/venv/python.exe":
            return _completed(args, returncode=1, stdout="", stderr="No module named gallery_dl")
        return _completed(args, stdout="1.32.1\n")

    monkeypatch.setattr(pilot.shutil, "which", lambda name: "C:/tools/gallery-dl.exe")
    entrypoint = pilot.probe_gallery_dl_entrypoint(runner=fake_run, python_executable="C:/venv/python.exe")
    assert entrypoint.mode == "external_executable_mode"
    assert entrypoint.public_dict()["uses_external_executable"] is True
    assert calls[-1] == ["C:/tools/gallery-dl.exe", "--version"]


def test_explicit_operator_command_mode_is_reported():
    def fake_run(args, **kwargs):
        return _completed(args, stdout="1.32.1\n")

    entrypoint = pilot.probe_gallery_dl_entrypoint("py -m gallery_dl", runner=fake_run)
    assert entrypoint.mode == "explicit_operator_command_mode"
    assert entrypoint.command == ("py", "-m", "gallery_dl")
    assert entrypoint.public_dict()["uses_explicit_operator_command"] is True


def test_no_silent_py_launcher_fallback(monkeypatch):
    def fake_run(args, **kwargs):
        assert args[:3] != ["py", "-m", "gallery_dl"]
        return _completed(args, returncode=1, stdout="", stderr="No module named gallery_dl")

    monkeypatch.setattr(pilot.shutil, "which", lambda name: None)
    with pytest.raises(pilot.GalleryDlUnavailable, match="no_silent_py_launcher_fallback"):
        pilot.probe_gallery_dl_entrypoint(runner=fake_run, python_executable="C:/venv/python.exe")


def test_missing_explicit_gallery_dl_gives_actionable_error():
    def fake_run(args, **kwargs):
        raise FileNotFoundError("missing")

    with pytest.raises(pilot.GalleryDlUnavailable, match="explicit_gallery_dl_command_unavailable"):
        pilot.probe_gallery_dl_entrypoint("missing-gallery-dl", runner=fake_run)


def test_db_default_host_matches_app_local_default(tmp_path, monkeypatch):
    _clear_config_env(monkeypatch)
    config = pilot.load_project_config(tmp_path)
    assert config.db_host == "localhost"
    assert config.db_name == "blombooru"


def test_settings_storage_root_and_test_database_url_precedence(tmp_path, monkeypatch):
    _clear_config_env(monkeypatch)
    storage = tmp_path / "storage"
    settings_file = storage / "data" / "settings.json"
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(
        json.dumps({"database": {"name": "from_settings", "host": "settings-host", "user": "settings-user"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("POSTGRES_DB", "from_env")
    config = pilot.load_project_config(tmp_path)
    assert config.db_name == "from_settings"
    assert config.db_host == "settings-host"
    assert config.storage_root_mode == "explicit_violet_storage_root"

    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://tester:secret@localhost:5432/custom_test_db")
    test_config = pilot.load_project_config(tmp_path)
    assert test_config.database_url_source == "test_database_url"
    assert test_config.db_name == "custom_test_db"


def test_directory_events_are_not_normalized_and_url_events_are_media(tmp_path):
    payload = "\n".join(
        [
            json.dumps([2, {"id": 100000002, "title": "directory"}]),
            json.dumps([3, "https://i.pximg.net/img-original/img/x/100000002_p0.jpg", {"id": 100000002, "num": 0}]),
        ]
    )
    input_file = tmp_path / "sample.jsonl"
    input_file.write_text(payload, encoding="utf-8")

    result = pilot.parse_gallery_dl_json_inputs(input_file)
    records = pilot.normalize_adapter_records(result, entrypoint=_entrypoint())

    assert result.directory_context_event_count == 1
    assert result.url_media_event_count == 1
    assert len(records) == 1
    assert records[0].source_adapter == "gallery_dl_external"


def test_public_report_redacts_exact_ids_paths_and_secret_payloads(tmp_path):
    record = pilot.PixivGalleryDlAdapterRecord(
        work_id="100000003",
        page_index=0,
        page_count=1,
        gallery_dl_filename="100000003_p0.jpg",
        metadata_richness="rich_structured_metadata",
    )
    parse_result = pilot.f1.ParseResult(records=[], files=[])
    summary = pilot.build_public_summary(
        generated_at="2026-06-01T00:00:00+00:00",
        pr_context={"pr88_state": "MERGED"},
        git_context={"branch": "test"},
        entrypoint=_entrypoint(),
        db_identity={"configured_db_host": "localhost", "db_sensitive_value_included": False},
        sample_public={"selected_count": 1, "exact_work_ids_public": False},
        parse_result=parse_result,
        records=[record],
        join_summary={"status_counts": {}, "page_index_status_counts": {}},
        command_public={"metadata_command_count": 1},
        download_public={"downloaded_file_count": 0, "downloaded_total_bytes": 0},
        containment={"output_path_violation": False},
    )
    report = pilot.build_markdown_report(summary, private_markers=["100000003", "100000003_p0.jpg", str(tmp_path)])
    assert "100000003" not in json.dumps(summary, ensure_ascii=False)
    assert "100000003" not in report
    assert str(tmp_path) not in report
    with pytest.raises(pilot.f1.PrivacyBlocked):
        pilot.f1.assert_no_secret_like_payload({"refresh_token": "secret"})


def test_all_download_artifacts_counted_and_cleanup_stays_inside():
    root = ROOT / pilot.PHASE_OUTPUT_DIR / "downloads-unit-test"
    outside = ROOT / pilot.PHASE_OUTPUT_DIR / "outside-unit-test.txt"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    outside.write_text("keep", encoding="utf-8")
    try:
        (root / "ref.jpg").write_bytes(b"123")
        (root / "metadata.json").write_text("{}", encoding="utf-8")
        (root / "bundle.zip").write_bytes(b"zip")
        (root / "nosuffix").write_bytes(b"x")
        public, private = pilot.summarize_download_artifacts(root, cleanup=False)
        assert public["downloaded_file_count"] == 4
        assert public["downloaded_total_bytes"] == 9
        assert public["downloaded_artifact_type_distribution"][".json"] == 1
        assert public["downloaded_artifact_type_distribution"][".zip"] == 1
        assert public["downloaded_artifact_type_distribution"]["<no_suffix>"] == 1
        assert private["download_files_private"]

        cleaned, _ = pilot.summarize_download_artifacts(root, cleanup=True)
        assert cleaned["cleanup_file_count"] == 4
        assert not any(path.is_file() for path in root.rglob("*"))
        assert outside.exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        outside.unlink(missing_ok=True)


def test_cleanup_rejects_paths_outside_phase_root(tmp_path):
    external = tmp_path / "downloads"
    external.mkdir()
    with pytest.raises(pilot.OutputPathError, match="gallery_dl_output_path_violation"):
        pilot.summarize_download_artifacts(external, cleanup=True)


def test_content_class_gate_blocks_unknown_and_non_anime(db):
    _media(db, 31, filename="100000031_p0.jpg", content_class=ContentClassEnum.anime)
    _media(db, 32, filename="100000032_p0.jpg", content_class=ContentClassEnum.unknown)
    _media(db, 33, filename="100000033_p0.jpg", content_class=ContentClassEnum.non_anime)
    prior_index = pilot.f1.build_local_prior_index(db)
    records = [
        pilot.PixivGalleryDlAdapterRecord(work_id="100000031", page_index=0, page_count=1, metadata_richness="rich_structured_metadata"),
        pilot.PixivGalleryDlAdapterRecord(work_id="100000032", page_index=0, page_count=1, metadata_richness="rich_structured_metadata"),
        pilot.PixivGalleryDlAdapterRecord(work_id="100000033", page_index=0, page_count=1, metadata_richness="rich_structured_metadata"),
    ]
    joined, summary = pilot.join_records_to_local_priors(records, prior_index)
    joined = pilot._finalize_joined_records(joined, reference_download_enabled=False, downloaded_file_count=0)

    assert summary["status_counts"]["metadata_matches_eligible_anime_local_prior"] == 1
    assert summary["status_counts"]["metadata_matches_ineligible_content_class"] == 2
    assert joined[0].eligible_for_future_local_source_hint is True
    assert joined[1].eligible_for_future_local_source_hint is False
    assert joined[2].eligible_for_future_local_source_hint is False


def test_local_prior_join_and_page_index_out_of_range(db):
    _media(db, 41, filename="100000041_p0.jpg")
    prior_index = pilot.f1.build_local_prior_index(db)
    records = [
        pilot.PixivGalleryDlAdapterRecord(work_id="100000041", page_index=0, page_count=1, metadata_richness="rich_structured_metadata"),
        pilot.PixivGalleryDlAdapterRecord(work_id="100000041", page_index=2, page_count=1, metadata_richness="rich_structured_metadata"),
    ]
    joined, summary = pilot.join_records_to_local_priors(records, prior_index)

    assert joined[0].local_match_status == "metadata_matches_eligible_anime_local_prior"
    assert joined[1].local_match_status == "page_index_out_of_range"
    assert summary["page_index_status_counts"]["page_index_out_of_range"] == 1


def test_private_artifact_paths_must_stay_under_local_manifests(tmp_path):
    containment = pilot.output_containment_summary(
        pilot.PHASE_OUTPUT_DIR,
        private_paths=[pilot.PRIVATE_DETAILS_JSON, pilot.PRIVATE_SHEET_CSV, pilot.PRIVATE_RAW_DIR],
    )
    assert containment["private_artifacts_under_phase_root"] is True
    with pytest.raises(pilot.OutputPathError, match="gallery_dl_output_path_violation"):
        pilot.output_containment_summary(pilot.PHASE_OUTPUT_DIR, private_paths=[tmp_path / "outside.json"])
