"""Task33 real API/update/plan/session and physical I/O consumer regressions."""

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_production_import_recovery import db, enqueue
from tests.test_pr152_bounded_fix import setup_import
from tests.test_s3a_m1_manual_sync_execute import (
    planner, execute_service, _write_png, DynamicSourceItem, DynamicSyncRunItem,
    Media, calculate_file_hash, execute_manual_sync_run,
)
from app.models import DynamicSyncRun
from app.services.manual_sync_recovery import recovery, record_failure, start_attempt, disposition
from app.services.manual_sync_lifecycle import source_item_downstream_complete
from app.routes.admin import manual_sync_recovery as routes


def client_for(session):
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/admin')
    app.dependency_overrides[routes.get_db] = lambda: session
    app.dependency_overrides[routes.require_admin_mode] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


def action(client, item_id, value):
    response = client.post(f'/api/admin/dynamic-library-sync/recovery-items/{item_id}', json={'action': value})
    assert response.status_code == 200, response.text
    return response.json()


def seed_failed(db, root, path, column_version):
    metadata = planner._metadata_for_path(path)
    item = DynamicSourceItem(source_root_id=root.id, relative_path=path.name,
        relative_path_hash=planner._hash_text(path.name), sync_state='failed', import_status='failed',
        failure_reason='read_timeout', file_size=column_version.get('file_size'), mtime_ns=column_version.get('mtime_ns'))
    db.add(item)
    db.flush()
    for _ in range(3):
        now = datetime.now(timezone.utc)
        run = DynamicSyncRun(run_type='manual_sync_execute', mode='execute', status='completed', dry_run=False)
        db.add(run)
        db.flush()
        start_attempt(item, run_id=run.id, metadata=metadata, now=now, db=db)
        record_failure(item, run_id=run.id, reason='read_timeout', metadata=metadata, now=now)
        record_failure(item, run_id=run.id, reason='read_timeout', metadata=metadata, now=now)
        db.add(DynamicSyncRunItem(sync_run_id=run.id, source_item_id=item.id,
            action='retry_source', item_state='failed', reason='read_timeout', current_metadata_json=metadata))
        db.commit()
    return item, metadata


@pytest.mark.parametrize('operator', ['defer', 'ignore', 'terminal'])
@pytest.mark.parametrize('columns', ['null', 'stale', 'current'])
def test_recovery_api_update_plan_new_session_and_proven_change(db, tmp_path, monkeypatch, operator, columns):
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    path = source/'failed.png'
    _write_png(path)
    current = planner._metadata_for_path(path)
    old = {} if columns == 'null' else dict(file_size=1, mtime_ns=1) if columns == 'stale' else current
    item, metadata = seed_failed(db, root, path, old)
    execute_service._remember_content_hash(item, 'old-bound-hash', metadata)
    db.commit()
    if operator == 'terminal':
        state = recovery(item)
        state['disposition'] = 'terminal'
        item.metadata_json = {**item.metadata_json, 'manual_sync_recovery': state}
        db.commit()
    else:
        with client_for(db) as client:
            action(client, item.id, operator)
        assert recovery(item)['version_evidence']['source'] == 'actual_attempt'
    expected = {'defer':'deferred_diagnosis', 'ignore':'ignored', 'terminal':'terminal'}[operator]
    before = deepcopy(item.metadata_json)
    item_id, root_id = item.id, root.id
    planner.run_update_check(db, root_ids=[root_id])
    db.expire_all()
    assert planner.get_pending_summary(db)['pending_import'] == 0
    latest_observation = db.query(DynamicSyncRunItem).filter_by(source_item_id=item_id, action='record_only').order_by(DynamicSyncRunItem.id.desc()).first()
    assert latest_observation.eligible_for_db_import is False
    assert recovery(db.get(DynamicSourceItem, item_id)) == before['manual_sync_recovery']
    assert db.get(DynamicSourceItem, item_id).metadata_json['content_hash_version'] == before['content_hash_version']
    with Session(db.get_bind()) as fresh:
        reloaded = fresh.get(DynamicSourceItem, item_id)
        assert len(recovery(reloaded)['version_failure_run_ids']) == 3
        assert disposition(reloaded, metadata) == expected
        with client_for(fresh) as client:
            response = client.get('/api/admin/dynamic-library-sync/recovery-items',
                params={'root_id':root_id, 'include_policy_excluded':False})
        row = next(row for row in response.json()['items'] if row['source_item_id'] == item_id)
        assert row['disposition'] == expected and row['reason'] == 'read_timeout'
        plan = planner.plan_manual_sync_dry_run(fresh, source_path=str(source), source_record_id=root_id,
            max_files=5, stable_age_seconds=0, include_private_details=True)
        assert not plan['private_details']['items']
        assert any(row.get('source_item_id') == item_id and row['reason'] == expected
            for row in plan['private_details']['metadata_dispositions'])
        # An observation with incomplete metadata cannot release any disposition.
        assert disposition(reloaded, {}) == expected
        os.utime(path, ns=(metadata['mtime_ns']+1000000000, metadata['mtime_ns']+1000000000))
        planner.run_update_check(fresh, root_ids=[root_id])
        changed = planner._metadata_for_path(path)
        assert disposition(reloaded, changed) == ('ignored' if operator == 'ignore' else 'retryable')
        with client_for(fresh) as client:
            response = client.get('/api/admin/dynamic-library-sync/recovery-items', params={'root_id':root_id})
        assert next(row for row in response.json()['items'] if row['source_item_id'] == item_id)['disposition'] == (
            'ignored' if operator == 'ignore' else 'retryable')
        assert execute_service._stored_hash_for_version(reloaded, changed) is None
        with client_for(fresh) as client:
            action(client, item_id, 'resume')
        run, new_plan = enqueue(fresh, fresh.get(type(root), root_id), 5)
        assert len(new_plan['private_details']['items']) == 1
        result = execute_manual_sync_run(fresh, run_id=run.id)['manual_sync_execute']
        assert result['outcome_counts']['imported'] == 1
        assert recovery(reloaded)['operator_events'][-1]['action'] == 'resume'


def test_unknown_version_defer_first_fill_then_real_change(db, tmp_path, monkeypatch):
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    path = source/'arrives-later.png'
    item = DynamicSourceItem(source_root_id=root.id, relative_path=path.name,
        relative_path_hash=planner._hash_text(path.name), file_size=12, mtime_ns=12,
        import_status='failed', sync_state='failed', failure_reason='read_error')
    db.add(item)
    db.commit()
    with client_for(db) as client:
        action(client, item.id, 'defer')
    assert recovery(item)['version_evidence']['source'] == 'unknown'
    assert recovery(item)['file_version'] == {'file_size':None, 'mtime_ns':None}
    _write_png(path)
    planner.run_update_check(db, root_ids=[root.id])
    version = planner._metadata_for_path(path)
    assert recovery(item)['version_evidence']['source'] == 'first_metadata_after_unknown_disposition'
    assert disposition(item, version) == 'deferred_diagnosis'
    assert planner.get_pending_summary(db)['pending_import'] == 0
    with Session(db.get_bind()) as fresh:
        current = fresh.get(DynamicSourceItem, item.id)
        plan = planner.plan_manual_sync_dry_run(fresh, source_path=str(source), source_record_id=root.id,
            max_files=5, stable_age_seconds=0, include_private_details=True)
        assert not plan['private_details']['items']
        os.utime(path, ns=(version['mtime_ns']+1000000000, version['mtime_ns']+1000000000))
        planner.run_update_check(fresh, root_ids=[root.id])
        assert disposition(current, planner._metadata_for_path(path)) == 'retryable'


def test_api_without_attempt_reads_current_metadata_not_stale_columns(db, tmp_path, monkeypatch):
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    path = source/'current.png'
    _write_png(path)
    item = DynamicSourceItem(source_root_id=root.id, relative_path=path.name,
        relative_path_hash=planner._hash_text(path.name), file_size=1, mtime_ns=1)
    db.add(item)
    db.commit()
    with client_for(db) as client:
        action(client, item.id, 'defer')
    assert recovery(item)['version_evidence']['source'] == 'bounded_current_metadata'
    assert disposition(item, planner._metadata_for_path(path)) == 'deferred_diagnosis'


def test_defer_after_newer_observation_does_not_bind_old_attempt(db, tmp_path, monkeypatch):
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    path = source/'changed.png'
    _write_png(path)
    item, previous = seed_failed(db, root, path, {})
    os.utime(path, ns=(previous['mtime_ns']+1000000000, previous['mtime_ns']+1000000000))
    planner.run_update_check(db, root_ids=[root.id])
    with client_for(db) as client:
        action(client, item.id, 'defer')
    assert recovery(item)['version_evidence']['source'] == 'newer_metadata_observation'
    assert disposition(item, planner._metadata_for_path(path)) == 'deferred_diagnosis'


def test_completed_app_copy_failure_retains_downstream_and_retry(db, tmp_path, monkeypatch):
    from app.utils.source_read_diagnostics import SourceReadReason
    source, root, storage = setup_import(db, tmp_path, monkeypatch)
    path = source/'pending-new-version.png'
    _write_png(path)
    run, _ = enqueue(db, root, 5)
    execute_manual_sync_run(db, run_id=run.id)
    item = db.query(DynamicSourceItem).one()
    item.classification_status, item.ai_tagging_status, item.localization_status = 'classified', 'tagged', 'localized'
    db.commit()
    old_media_id = item.media_id
    old_hash_version = deepcopy(item.metadata_json['content_hash_version'])
    _write_png(path, (90, 81, 12))
    planner.run_update_check(db, root_ids=[root.id])
    monkeypatch.setattr(execute_service, '_calculate_manual_plan_file_hash',
        lambda *args: (None, SourceReadReason('read_timeout', {'stage':'source_hash', 'worker_status':'timeout'})))
    run, plan = enqueue(db, root, 5)
    assert len(plan['private_details']['items']) == 1
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    assert result['outcome_counts']['failed'] == 1
    assert item.media_id == old_media_id and source_item_downstream_complete(item)
    assert item.metadata_json['content_hash_version'] == old_hash_version
    assert (storage/db.get(Media, old_media_id).path).is_file()
    assert planner.classify_source_item(item).work_item_kind.value == 'RETRY_SOURCE'


def test_app_media_followup_does_not_resolve_unneeded_source(db, tmp_path, monkeypatch):
    source, root, storage = setup_import(db, tmp_path, monkeypatch)
    _write_png(storage/'media/original/existing.png')
    media = Media(filename='existing.png', path='media/original/existing.png', hash='app-hash', file_type='image')
    db.add(media)
    db.flush()
    item = DynamicSourceItem(source_root_id=root.id, relative_path='offline.png',
        relative_path_hash=planner._hash_text('offline.png'), media_id=media.id,
        import_status='imported', sync_state='imported', classification_status='pending')
    db.add(item)
    db.commit()
    def forbidden(*args):
        raise AssertionError('app-copy followup must not parse its offline source')
    monkeypatch.setattr(planner, '_source_item_file_path', forbidden)
    plan = planner.plan_manual_sync_dry_run(db, source_path=str(source), source_record_id=root.id,
        max_files=5, stable_age_seconds=0, include_private_details=True)
    assert len(plan['private_details']['items']) == 1
    assert plan['private_details']['items'][0]['work_item_kind'] == 'FOLLOWUP'


def test_legacy_media_changed_version_reenters_without_update(db, tmp_path, monkeypatch):
    source, root, storage = setup_import(db, tmp_path, monkeypatch)
    path = source/'legacy.png'
    _write_png(path)
    original = planner._metadata_for_path(path)
    media = Media(filename='legacy.png', path='media/original/legacy.png', hash=calculate_file_hash(path), file_type='image')
    _write_png(storage/media.path)
    db.add(media)
    db.flush()
    item = DynamicSourceItem(source_root_id=root.id, relative_path=path.name,
        relative_path_hash=planner._hash_text(path.name), file_size=original['file_size'], mtime_ns=original['mtime_ns'],
        media_id=media.id, content_hash=media.hash, import_status='imported', sync_state='imported',
        classification_status='classified', ai_tagging_status='ai_tagged', localization_status='localized',
        metadata_json={'suffix':'.png', 'content_hash_computed':True})
    db.add(item)
    db.commit()
    os.utime(path, ns=(original['mtime_ns']+1000000000, original['mtime_ns']+1000000000))
    plan = planner.plan_manual_sync_dry_run(db, source_path=str(source), source_record_id=root.id,
        max_files=5, stable_age_seconds=0, include_private_details=True)
    assert plan['private_details']['items'][0]['source_item_id'] == item.id
    assert plan['private_details']['items'][0]['work_item_kind'] == 'RETRY_SOURCE'
    assert source_item_downstream_complete(item)
    assert 'content_hash_version' not in item.metadata_json


def test_completed_media_survives_missing_observation_and_changed_source_reenters(db, tmp_path, monkeypatch):
    source, root, storage = setup_import(db, tmp_path, monkeypatch)
    path = source/'old.png'
    _write_png(path)
    run, _ = enqueue(db, root, 5)
    execute_manual_sync_run(db, run_id=run.id)
    item = db.query(DynamicSourceItem).one()
    item.classification_status, item.ai_tagging_status, item.localization_status = 'classified', 'tagged', 'localized'
    db.commit()
    media_id = item.media_id
    old_binding = deepcopy(item.metadata_json['content_hash_version'])
    original_bytes = (storage/db.get(Media, media_id).path).read_bytes()
    # Move only this disposable fixture out of the enumerated source directory.
    displaced = tmp_path/'fixture-absent.png'
    path.rename(displaced)
    planner.run_update_check(db, root_ids=[root.id])
    assert item.source_status == 'missing' and item.media_id == media_id
    assert source_item_downstream_complete(item)
    displaced.rename(path)
    _write_png(path, (200, 80, 40))
    planner.run_update_check(db, root_ids=[root.id])
    assert source_item_downstream_complete(item)
    assert item.metadata_json['content_hash_version'] == old_binding
    assert item.metadata_json['current_source_version_pending'] is True
    run, plan = enqueue(db, root, 5)
    assert len(plan['private_details']['items']) == 1
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    assert result['outcome_counts']['imported'] == 1
    assert item.media_id != media_id
    assert (storage/db.get(Media, media_id).path).read_bytes() == original_bytes
    assert item.metadata_json['current_source_version_pending'] is False


@pytest.mark.parametrize('old_state', ['failed', 'skipped_existing_media', 'skipped_duplicate', 'unchanged'])
def test_priority_resolve_worker_reaped_identity_retained_and_healthy_executes(db, tmp_path, monkeypatch, old_state):
    from app.utils import bounded_source_io as io
    from tests.source_io_worker_fixture import filesystem_worker
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    _write_png(source/'blocked-resolve.png')
    _write_png(source/'healthy.png', (31, 60, 90))
    item = DynamicSourceItem(source_root_id=root.id, relative_path='blocked-resolve.png',
        relative_path_hash=planner._hash_text('blocked-resolve.png'), sync_state=old_state,
        import_status='failed' if old_state == 'failed' else 'pending', failure_reason='read_timeout' if old_state == 'failed' else None)
    db.add(item)
    db.commit()
    original_call = io.SourceIOWorker.call
    terminated = []
    def call(worker, op, *args, **kwargs):
        worker.worker_target = filesystem_worker
        try:
            return original_call(worker, op, *args, **{**kwargs, 'timeout':1 if op == 'resolve' else 10})
        except io.SourceMetadataError as exc:
            if exc.diagnostic.get('worker_terminated'):
                assert worker.process is None
                terminated.append(exc.diagnostic)
            raise
    monkeypatch.setattr(io.SourceIOWorker, 'call', call)
    started = time.monotonic()
    run, plan = enqueue(db, root, 5)
    assert time.monotonic()-started < 15
    errors = plan['private_details']['directory_errors']
    assert any(e['source_item_id'] == item.id and e['relative_path'] == item.relative_path
        and e['worker_terminated'] and e['continuation'].startswith('new_plan_retry_registered') for e in errors)
    assert plan['limits']['batch_executable']
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    assert result['outcome_counts']['imported'] == 1 and terminated
    assert db.get(DynamicSourceItem, item.id).media_id is None


def test_priority_containment_failure_keeps_registered_identity(tmp_path):
    item = SimpleNamespace(id=11, source_root_id=2, relative_path='../escape.png', relative_path_hash='gap',
        media_id=None, sync_state='skipped_duplicate', import_status='pending', failure_reason=None, deferred_reason=None)
    errors = []
    assert planner._manual_plan_priority_source_files(tmp_path, {'gap':item}, errors=errors) == []
    assert errors[0]['source_item_id'] == 11 and errors[0]['reason'] == 'source_path_escape'


def test_hash_real_consumers_persist_string_and_diagnostics_and_continue(db, tmp_path, monkeypatch):
    from app.utils import local_library_scanner as scanner
    from scripts import run_phase47_s2_baseline_full_import_ai_localization as legacy
    from app import database
    from tests.source_io_worker_fixture import hash_worker
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    paths = [source/name for name in ['timeout.png', 'error.png', 'healthy.png']]
    for path in paths:
        _write_png(path)
        db.add(DynamicSourceItem(source_root_id=root.id, relative_path=path.name,
            relative_path_hash=planner._hash_text(path.name), import_status='pending', sync_state='new'))
    existing = Media(filename='healthy.png', path='media/original/existing.png',
        hash=calculate_file_hash(paths[-1]), file_type='image')
    db.add(existing)
    db.commit()
    monkeypatch.setattr(scanner, '_hash_file_in_subprocess', hash_worker)
    monkeypatch.setattr(database, 'init_engine', lambda: None)
    monkeypatch.setattr(database, 'SessionLocal', lambda: Session(db.get_bind()))
    # Keep the real legacy query, loop, threshold rules and commit consumer.
    monkeypatch.setattr(legacy, 'prepare_private_output_dir', lambda args: tmp_path)
    args = SimpleNamespace(run_id='isolated-hash-consumer', import_batch_size=1, max_import_items=3,
        hydration_timeout_seconds=1, hydration_failure_max_items=10, hydration_failure_max_rate=10,
        import_failure_max_items=10, import_failure_max_rate=10)
    result = legacy.run_controlled_import(args, {'root_ids':[root.id]})
    db.expire_all()
    rows = db.query(DynamicSourceItem).order_by(DynamicSourceItem.id).all()
    assert [row.failure_reason for row in rows[:2]] == ['read_timeout', 'read_error']
    for row in rows[:2]:
        detail = row.metadata_json['phase47_s2_private_diagnostic']
        assert detail['stage'] == 'source_hash' and 'elapsed_seconds' in detail and 'timeout_seconds' in detail
        assert 'errno' in detail and 'winerror' in detail
    assert rows[-1].media_id == existing.id
    assert result['read_attempted'] == 3 and result['reused_existing'] == 1
    assert result['stopped_by_rule'] is None
    _, reason = planner._calculate_manual_plan_file_hash(paths[1], 1)
    assert reason == 'read_error' and reason.diagnostic['errno'] == 5
    stats = {'failed_files':[]}
    scanner._record_failure(stats, str(paths[1]), reason)
    assert stats['failed_files'][0]['reason'] == 'read_error'
    assert stats['failed_files'][0]['private_diagnostic']['errno'] == 5
