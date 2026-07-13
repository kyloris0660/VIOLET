from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models import ScanJob
from app.routes.admin import media as admin_media
from app.utils.local_library_scanner import run_scan_job


def _scan_result(closure: dict, *, imported_ids=None) -> dict:
    return {
        "total_seen": 1, "processed": 1, "imported": len(imported_ids or []),
        "skipped_duplicate": 0, "skipped_unsupported": 0, "skipped_limit": 0,
        "skipped_cloud_placeholder": 0, "skipped_zero_byte": 0, "skipped_timeout": 0,
        "skipped_unreadable": 0, "skipped_hidden": 0, "skipped_too_large": 0,
        "failed": 0, "limit_reached": False, "failed_files": [],
        "imported_media_ids": imported_ids or [], "pixiv_metadata_closure": closure,
    }


def _job() -> ScanJob:
    return ScanJob(id=77, status="pending", paths_json='["C:\\\\local-test"]', dry_run=False, hydrated_only=True)


def test_completed_scan_with_pending_metadata_uses_terminal_scan_status() -> None:
    job = _job()
    db = MagicMock()
    db.query.return_value.get.return_value = job
    closure = {"closed": False, "open_candidate_count": 1, "pixiv_candidate_count": 1}
    with patch("app.database.SessionLocal", return_value=db), patch(
        "app.utils.local_library_scanner.scan_and_import", return_value=_scan_result(closure, imported_ids=[9])
    ):
        run_scan_job(job.id)
    assert job.status == "completed"
    assert job.finished_at is not None
    assert "metadata acquisition remains pending" in job.error_message


def test_pending_metadata_does_not_strand_enabled_after_scan_automation() -> None:
    job = _job()
    db = MagicMock()
    db.query.return_value.get.return_value = job
    closure = {"closed": False, "open_candidate_count": 1, "pixiv_candidate_count": 1}
    with patch("app.database.SessionLocal", return_value=db), patch(
        "app.utils.local_library_scanner.scan_and_import",
        return_value=_scan_result(closure, imported_ids=[9]),
    ), patch(
        "app.services.ai_tagging_job_service.create_auto_tag_job_after_scan"
    ) as auto_tag, patch(
        "app.services.classification_job_service.create_auto_classification_job_after_scan"
    ) as auto_classify:
        run_scan_job(job.id)

    auto_tag.assert_called_once_with(job.id, [9])
    auto_classify.assert_called_once_with(job.id, [9])


def test_completed_scan_without_pixiv_candidates_is_completed_cleanly() -> None:
    job = _job()
    db = MagicMock()
    db.query.return_value.get.return_value = job
    closure = {"closed": True, "open_candidate_count": 0, "pixiv_candidate_count": 0}
    with patch("app.database.SessionLocal", return_value=db), patch(
        "app.utils.local_library_scanner.scan_and_import", return_value=_scan_result(closure)
    ):
        run_scan_job(job.id)
    assert job.status == "completed"
    assert job.finished_at is not None
    assert not job.error_message


def test_failed_scan_remains_failed() -> None:
    job = _job()
    db = MagicMock()
    db.query.return_value.get.return_value = job
    with patch("app.database.SessionLocal", return_value=db), patch(
        "app.utils.local_library_scanner.scan_and_import", side_effect=RuntimeError("scan failed")
    ):
        run_scan_job(job.id)
    assert job.status == "failed"
    assert job.finished_at is not None


def test_scan_api_separates_execution_and_metadata_status(monkeypatch) -> None:
    job = _job()
    job.status = "completed"
    job.finished_at = job.created_at
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [(9,)]
    monkeypatch.setattr(
        admin_media, "summarize_batch_closure",
        lambda _db, _ids: {
            "pixiv_candidate_count": 1, "closed": False, "open_candidate_count": 1,
            "lifecycle_counts": {"pending": 1},
        },
    )
    payload = admin_media._serialize_job(job, db)
    assert payload["status"] == "completed"
    assert payload["source_metadata_status"] == "pending"
    assert payload["source_metadata_open_count"] == 1
    assert payload["source_metadata_blocked"] is True


def test_legacy_job_without_pixiv_candidates_is_not_falsely_blocked(monkeypatch) -> None:
    job = _job()
    job.status = "completed"
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [(9,)]
    monkeypatch.setattr(
        admin_media,
        "summarize_batch_closure",
        lambda _db, _ids: {
            "pixiv_candidate_count": 0,
            "closed": False,
            "open_candidate_count": 0,
            "lifecycle_counts": {},
        },
    )

    payload = admin_media._serialize_job(job, db)

    assert payload["source_metadata_status"] == "not_applicable"
    assert payload["source_metadata_blocked"] is False


def test_frontend_poller_terminates_completed_scan_and_shows_metadata_warning() -> None:
    script = (admin_media.Path(__file__).resolve().parents[1] / "frontend/static/js/admin.js").read_text(encoding="utf-8")
    assert "['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status)" in script
    assert "job.source_metadata_blocked" in script
    assert "Pixiv metadata ${job.source_metadata_status}" in script
