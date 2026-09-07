"""PR152 lead findings through physical I/O and ordinary plan/execute seams."""

import os
import time
from collections import Counter

import pytest
from fastapi import HTTPException

from tests.test_production_import_recovery import db, enqueue
from tests.test_s3a_m1_manual_sync_execute import (
    _enable_manual_execute, _patch_test_storage, _write_png,
    planner, execute_service, DynamicSourceItem, DynamicSyncRunItem, Media,
    create_manual_sync_execute_run, execute_manual_sync_run, calculate_file_hash,
)
from tests.source_io_worker_fixture import filesystem_worker
from app.utils.bounded_source_io import SourceIOWorker, SourceMetadataError
from app.utils.bounded_source_walk import source_files
from app.utils.bounded_source_copy import SourceCopyError


@pytest.mark.parametrize("operation", ["blocked-open", "blocked-next"])
def test_real_blocking_enumeration_is_terminated(tmp_path, operation):
    errors = []
    started = time.monotonic()
    assert list(source_files(tmp_path/operation, errors=errors, io_timeout=1,
        max_seconds=3, _worker_target=filesystem_worker)) == []
    assert time.monotonic() - started < 4
    assert errors[0]["worker_terminated"] is True
    assert errors[0]["coverage"] == "unknown"
    assert errors[0]["continuation"] == "new_plan_retry_unknown_subtree"


def test_real_blocking_metadata_is_terminated_and_next_request_works(tmp_path):
    worker = SourceIOWorker(worker_target=filesystem_worker)
    try:
        started = time.monotonic()
        with pytest.raises(SourceMetadataError) as error:
            worker.call("stat", str(tmp_path/"blocked-stat"), True, timeout=1)
        assert time.monotonic() - started < 4
        assert error.value.diagnostic["worker_terminated"] is True
        assert worker.process is None
        assert worker.call("stat", str(tmp_path), True)["is_dir"]
    finally:
        worker.close()


def setup_import(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    storage = _patch_test_storage(monkeypatch, tmp_path)
    monkeypatch.setenv("CONTENT_CLASSIFICATION_ENABLED", "false")
    monkeypatch.setenv("AI_TAGGING_ENABLED", "false")
    source = tmp_path/"source"
    source.mkdir()
    root = planner.register_source_root(db, path=source, label="bounded-fix-fixture")
    return source, root, storage


def test_bad_subdirectory_preserves_healthy_execution_and_unknown_continuation(db, tmp_path, monkeypatch):
    source, root, _storage = setup_import(db, tmp_path, monkeypatch)
    (source/"blocked-open").mkdir()
    _write_png(source/"healthy"/"one.png")
    def walk(path, *, walk_errors, dispositions=None):
        return source_files(path, errors=walk_errors, dispositions=dispositions,
            io_timeout=1, _worker_target=filesystem_worker)
    monkeypatch.setattr(planner, "_iter_source_files", walk)
    run, plan = enqueue(db, root, 5)
    assert plan["counts"]["partial_scan"]
    assert plan["limits"]["batch_executable"]
    assert not plan["limits"]["unsafe_partial_scan"]
    assert plan["limits"]["more_batches_remain"]
    assert plan["limits"]["walk_error_import_candidates_blocked"] == 0
    result = execute_manual_sync_run(db, run_id=run.id)["manual_sync_execute"]
    assert result["outcome_counts"]["imported"] == 1
    assert db.query(Media).count() == 1


def test_readdir_order_changes_between_public_private_and_execute(db, tmp_path, monkeypatch):
    source, root, _storage = setup_import(db, tmp_path, monkeypatch)
    for i in range(4):
        _write_png(source/f"{i}.png", (i, i+1, i+2))
    calls = []
    def walk(path, *, walk_errors, dispositions=None):
        rows = list(source_files(path, errors=walk_errors, dispositions=dispositions))
        calls.append(len(calls))
        return iter(rows if len(calls) % 2 else list(reversed(rows)))
    monkeypatch.setattr(planner, "_iter_source_files", walk)
    run, plan = enqueue(db, root, 5)
    result = execute_manual_sync_run(db, run_id=run.id)["manual_sync_execute"]
    assert len(calls) >= 3
    assert result["outcome_counts"]["imported"] == 4
    assert len({i["safe_label"] for i in plan["private_details"]["items"]}) == 4


@pytest.mark.parametrize("bound_evidence,replace_after_plan", [(True, True), (False, False), (True, False)])
def test_stored_hash_requires_current_bound_version(db, tmp_path, monkeypatch, bound_evidence, replace_after_plan):
    source, root, storage = setup_import(db, tmp_path, monkeypatch)
    target = source/"old.png"
    _write_png(target)
    _write_png(source/"healthy.png", (30, 40, 50))
    _write_png(storage/"media/original/old.png")
    metadata = planner._metadata_for_path(target)
    old_hash = calculate_file_hash(target)
    media = Media(filename="old.png", path="media/original/old.png", hash=old_hash, file_type="image")
    db.add(media)
    db.flush()
    item = DynamicSourceItem(source_root_id=root.id, relative_path=target.name,
        relative_path_hash=planner._hash_text(target.name), file_size=metadata["file_size"],
        mtime_ns=metadata["mtime_ns"], content_hash=old_hash, sync_state="new", import_status="pending")
    if bound_evidence:
        execute_service._remember_content_hash(item, old_hash, metadata)
    db.add(item)
    db.commit()
    run, _plan = enqueue(db, root, 5)
    if replace_after_plan:
        _write_png(target, (99, 20, 30))
        os.utime(target, ns=(metadata["mtime_ns"]+1000000000, metadata["mtime_ns"]+1000000000))
    hashed = []
    original_hash = execute_service._calculate_manual_plan_file_hash
    def hash_file(path, timeout):
        hashed.append(path.name)
        return original_hash(path, timeout)
    monkeypatch.setattr(execute_service, "_calculate_manual_plan_file_hash", hash_file)
    result = execute_manual_sync_run(db, run_id=run.id)["manual_sync_execute"]
    db.refresh(item)
    assert result["outcome_counts"]["imported"] == 1
    if replace_after_plan:
        assert item.media_id is None
        assert item.failure_reason == "content_changed_after_plan"
        assert result["outcome_counts"]["content_changed_after_plan"] == 1
    else:
        assert item.media_id == media.id
        assert ("old.png" in hashed) is (not bound_evidence)


@pytest.mark.parametrize("failure,reason", [("timeout", "read_timeout"), ("copy", "read_error"),
    ("decode", "import_failed"), ("process", "import_failed"), ("http", "import_failed")])
def test_import_exception_reason_once_and_healthy_continues(db, tmp_path, monkeypatch, failure, reason):
    source, root, _storage = setup_import(db, tmp_path, monkeypatch)
    _write_png(source/"bad.png")
    _write_png(source/"healthy.png", (30, 40, 50))
    original_copy = execute_service._copy_and_import_media
    original_process = execute_service.process_and_save_media
    calls = []
    def process(*args, **kwargs):
        calls.append("process")
        if len(calls) == 1 and failure in {"process", "http"}:
            if failure == "http":
                raise HTTPException(status_code=422, detail="fixture import rejection")
            raise RuntimeError("fixture processing failure")
        return original_process(*args, **kwargs)
    def copy(db, path, **kwargs):
        if path.name == "bad.png" and failure in {"timeout", "copy", "decode"}:
            raise SourceCopyError(dict(stage="copied_image_decode" if failure == "decode" else "source_copy",
                worker_status="timeout" if failure == "timeout" else "error",
                exception_type="UnidentifiedImageError" if failure == "decode" else "OSError", errno=22))
        return original_copy(db, path, **kwargs)
    monkeypatch.setattr(execute_service, "_copy_and_import_media", copy)
    monkeypatch.setattr(execute_service, "process_and_save_media", process)
    run, _plan = enqueue(db, root, 5)
    result = execute_manual_sync_run(db, run_id=run.id)["manual_sync_execute"]
    failed = db.query(DynamicSyncRunItem).filter_by(sync_run_id=run.id, item_state="failed").all()
    assert len(failed) == 1 and failed[0].reason == reason
    assert Counter(row.reason for row in failed)[reason] == result["outcome_counts"][reason] == 1
    assert result["outcome_counts"]["failed"] == run.failed_items == 1
    assert result["outcome_counts"]["imported"] == 1
    assert db.query(Media).count() == 1
    assert result["stopped_by"] is None
