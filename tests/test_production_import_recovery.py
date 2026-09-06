"""Regression coverage for the real manual-sync selection/execution seams."""

import errno
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.test_s3a_m1_manual_sync_execute import (
    _enable_manual_execute, _patch_test_storage, _write_png,
    planner, execute_service, DynamicSourceItem, DynamicSyncRunItem, Media,
    create_manual_sync_execute_run, execute_manual_sync_run, calculate_file_hash,
)
from app.services.manual_sync_recovery import (
    fair_order, start_attempt, record_failure, disposition, recovery, set_recovery,
)
from app.utils.source_read_diagnostics import SourceReadReason
from app.utils.bounded_source_walk import source_files


@pytest.fixture()
def db():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    url = os.environ.get('VIOLET_IMPORT_RECOVERY_PG_URL')
    schema = None
    if url:
        from sqlalchemy.engine import make_url
        assert make_url(url).database == 'violet_a1_test_20260906'
        assert make_url(url).username == 'violet_a1_test_20260906'
        admin = create_engine(url)
        schema = 'import_recovery_' + uuid.uuid4().hex
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA {schema}'))
        engine = create_engine(url, connect_args={'options': f'-c search_path={schema}'})
    else:
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session
    engine.dispose()
    if schema:
        assert schema.startswith('import_recovery_') and len(schema) == 48
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def enqueue(db, root, cap):
    plan = planner.plan_manual_sync_dry_run(db, source_path=root.root_path,
        source_record_id=root.id, max_files=cap, stable_age_seconds=0, include_private_details=True)
    run = create_manual_sync_execute_run(db, root_id=root.id, max_files=cap,
        hydrated_only=True, stable_age_seconds=0,
        expected_plan_hash=plan['integrity']['plan_hash'],
        confirmation_phrase=plan['integrity']['confirmation_phrase'],
        plan_created_at=plan['job']['created_at'])
    return run, plan


@pytest.mark.parametrize('positions', [set(range(12)), set(range(3, 15)), set(range(6, 18))])
def test_independent_failures_never_truncate_healthy_candidates(db, tmp_path, monkeypatch, positions):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)
    monkeypatch.setenv('DYNAMIC_LIBRARY_MANUAL_SYNC_EXECUTE_MAX_FILES', '1000')
    monkeypatch.setenv('CONTENT_CLASSIFICATION_ENABLED', 'false')
    monkeypatch.setenv('AI_TAGGING_ENABLED', 'false')
    source = tmp_path/'source'
    for i in range(19):
        _write_png(source/f'{i:02}.png', (i, i+1, i+2))
    root = planner.register_source_root(db, path=source, label='failure-fixture')
    attempts = []

    def hash_file(path, timeout):
        index = int(path.stem)
        attempts.append(index)
        if index in positions:
            return None, SourceReadReason('read_timeout', dict(exception_type=None, errno=None,
                winerror=None, stage='source_hash', elapsed_seconds=timeout, timeout_seconds=timeout,
                exitcode=-15, worker_status='timeout'))
        return calculate_file_hash(path), None

    monkeypatch.setattr(execute_service, '_calculate_manual_plan_file_hash', hash_file)
    run, plan = enqueue(db, root, 1000)
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    assert sorted(attempts) == list(range(19))
    assert result['outcome_counts']['failed'] == 12
    assert result['outcome_counts']['imported'] == 7
    assert result['unprocessed_count'] == 0
    assert result['stopped_by'] is None
    assert db.query(Media).count() == 7
    failed = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.item_state == 'failed').all()
    assert len(failed) == 12
    for item in failed:
        detail = item.current_metadata_json['private_diagnostic']
        assert detail['source_item_id'] == item.source_item_id and detail['run_id'] == run.id
        assert detail['worker_status'] == 'timeout' and detail['winerror'] is None
    assert 'private_diagnostic' not in str(execute_service.serialize_manual_sync_execute_run(run))


def test_cap_one_mixed_and_pure_retry_queues_rotate():
    def row(kind, name):
        return dict(work_item_kind=kind, relative_path_hash=name, last_attempt_at='')
    rows = [row('IMPORT', f'n{i}') for i in range(30)] + [row('RETRY_SOURCE', f'r{i}') for i in range(3)]
    cursor = 0
    seen = []
    for i in range(18):
        ordered = fair_order(rows, cursor=cursor)
        first = ordered[0]
        cursor = first['scheduler_cursor_after']
        seen.append(first['relative_path_hash'])
        for candidate in rows:
            if candidate['relative_path_hash'] == first['relative_path_hash']:
                candidate['last_attempt_at'] = f'{i+1:08}'
    assert {'r0', 'r1', 'r2'}.issubset(seen)
    assert len({value for value in seen if value.startswith('n')}) >= 12
    only_retries = [row('RETRY_SOURCE', f'r{i}') for i in range(10)]
    assert [r['relative_path_hash'] for r in fair_order(only_retries)] == [f'r{i}' for i in range(10)]


def test_defer_uses_version_bound_real_runs_and_reentry():
    now = datetime.now(timezone.utc)
    item = SimpleNamespace(metadata_json={'manual_sync_retry': {'attempt_count': 900}})
    version = dict(file_size=20, mtime_ns=100)
    for run in range(1, 4):
        start_attempt(item, run_id=run, metadata=version, now=now)
        record_failure(item, run_id=run, reason='read_error', metadata=version, now=now)
        # The same run never counts twice.
        record_failure(item, run_id=run, reason='read_error', metadata=version, now=now)
    assert disposition(item, version) == 'deferred_diagnosis'
    assert recovery(item)['version_failure_run_ids'] == [1, 2, 3]
    assert disposition(item, dict(file_size=21, mtime_ns=101)) == 'retryable'
    set_recovery(item, dict(disposition='ignored'))
    assert disposition(item, dict(file_size=22, mtime_ns=102)) == 'ignored'
    set_recovery(item, dict(disposition='terminal', policy_version=0))
    assert disposition(item, version) == 'retryable'
    start_attempt(item, run_id=5, metadata=version, now=now)
    record_failure(item, run_id=5, reason='import_failed', now=now, metadata={**version,
        'private_diagnostic': {'stage':'copied_image_decode', 'copied_version_verified':True,
            'exception_type':'UnidentifiedImageError'}})
    assert disposition(item, version) == 'terminal'
    assert disposition(item, dict(file_size=21, mtime_ns=102)) == 'retryable'


def test_enqueue_then_crash_preserves_every_unattempted_identity(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)
    source = tmp_path/'source'
    for i in range(3):
        _write_png(source/f'{i}.png', (i, i+1, i+2))
    root = planner.register_source_root(db, path=source, label='crash-fixture')
    run, plan = enqueue(db, root, 3)
    assert db.query(DynamicSyncRunItem).filter_by(sync_run_id=run.id).count() == 3
    assert all(item.sync_state == 'deferred_unprocessed' for item in db.query(DynamicSourceItem))
    run.status = 'failed'
    db.commit()
    db.expire_all()
    fresh = planner.plan_manual_sync_dry_run(db, source_path=source, source_record_id=root.id,
        max_files=3, stable_age_seconds=0, include_private_details=True)
    assert {i['relative_path'] for i in fresh['private_details']['items']} == {f'{i}.png' for i in range(3)}


def test_initial_enumeration_is_bounded_and_directory_error_has_identity(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path/f'{i}.txt').write_text('fixture')
    errors = []
    assert len(list(source_files(tmp_path, errors=errors, max_entries=2))) == 2
    assert errors[0]['path'] == str(tmp_path) and errors[0]['coverage'] == 'unknown'
    def denied(path):
        raise PermissionError(errno.EACCES, 'test access denied', str(path))
    monkeypatch.setattr('app.utils.bounded_source_walk.os.scandir', denied)
    errors = []
    assert list(source_files(tmp_path, errors=errors)) == []
    assert errors[0]['exception_type'] == 'PermissionError'
    assert errors[0]['errno'] == errno.EACCES


def test_shared_storage_failure_requires_target_evidence(tmp_path, monkeypatch):
    _patch_test_storage(monkeypatch, tmp_path)
    assert execute_service._shared_storage_failure(OSError(errno.ENOSPC, 'full')) == 'systemic_storage_unavailable'
    assert execute_service._shared_storage_failure(PermissionError(errno.EACCES, 'denied', str(tmp_path/'source'))) is None


def test_private_recovery_endpoint_and_owner_reentry(db, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.admin import manual_sync_recovery as routes
    _patch_test_storage(monkeypatch, tmp_path)
    source = tmp_path/'source'
    _write_png(source/'retry.png')
    root = planner.register_source_root(db, path=source, label='private-fixture')
    item = DynamicSourceItem(source_root_id=root.id, relative_path='retry.png',
        relative_path_hash='fixture-hash', sync_state='failed', import_status='failed', failure_reason='read_timeout')
    db.add(item)
    db.commit()
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/admin')
    app.dependency_overrides[routes.get_db] = lambda: db
    client = TestClient(app)
    url='/api/admin/dynamic-library-sync/recovery-items'
    assert client.get(url, params={'root_id': root.id}).status_code in {401, 403}
    app.dependency_overrides[routes.require_admin_mode] = lambda: SimpleNamespace(id=1)
    response = client.get(url, params={'root_id': root.id})
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['items'][0]['diagnostic'] is None
    for action, expected in [('defer','deferred_diagnosis'), ('ignore','ignored'), ('resume','retryable')]:
        assert client.post(url+f'/{item.id}', json={'action':action}).json()['disposition'] == expected
    db.refresh(item)
    assert item.import_status == 'deferred'
    assert [x['action'] for x in recovery(item)['operator_events']] == ['defer','ignore','resume']
    assert (source/'retry.png').is_file()


def test_stat_failure_records_exact_listed_identity(db, tmp_path, monkeypatch):
    _patch_test_storage(monkeypatch, tmp_path)
    source = tmp_path/'source'
    _write_png(source/'known.png')
    root = planner.register_source_root(db, path=source, label='stat-fixture')
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, 'fixture only', str(source/'known.png'))
    monkeypatch.setattr(planner, '_metadata_for_path', denied)
    plan = planner.plan_manual_sync_dry_run(db, source_path=source, source_record_id=root.id,
        max_files=5, stable_age_seconds=0, include_private_details=True)
    detail = plan['private_details']['metadata_dispositions'][0]
    assert detail['relative_path'] == 'known.png'
    assert detail['metadata']['private_diagnostic']['errno'] == errno.EACCES
    assert 'known.png' not in str(planner._redact_private_sync_payload(plan))


def test_missing_unlinked_noop_is_observed_and_reenters_when_source_returns(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)
    monkeypatch.setenv('CONTENT_CLASSIFICATION_ENABLED', 'false')
    monkeypatch.setenv('AI_TAGGING_ENABLED', 'false')
    source = tmp_path/'source'
    source.mkdir()
    root = planner.register_source_root(db, path=source, label='missing-link-fixture')
    item = DynamicSourceItem(source_root_id=root.id, relative_path='missing.png',
        relative_path_hash=planner._hash_text('missing.png'), sync_state='skipped_existing_media',
        import_status='deferred', deferred_reason='existing_media_hash', file_size=10, mtime_ns=20)
    db.add(item)
    db.commit()
    plan = planner.plan_manual_sync_dry_run(db, source_path=source, source_record_id=root.id,
        max_files=10, stable_age_seconds=0, include_private_details=True)
    observed = next(row for row in plan['private_details']['metadata_dispositions'] if row.get('source_item_id') == item.id)
    assert observed['reason'] == 'stat_error'
    assert observed['metadata']['private_diagnostic']['exception_type'] == 'FileNotFoundError'
    assert item.media_id is None  # No invented link or successful content read.
    _write_png(source/'missing.png')
    run, _ = enqueue(db, root, 1)
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    db.refresh(item)
    assert result['outcome_counts']['imported'] == 1
    assert item.media_id is not None


def test_private_recovery_bounds_large_discovery_and_keeps_missing_link_identity(db, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.models import DynamicSyncRun
    from app.routes.admin import manual_sync_recovery as routes
    _patch_test_storage(monkeypatch, tmp_path)
    source = tmp_path/'source'
    source.mkdir()
    root = planner.register_source_root(db, path=source, label='large-discovery-fixture')
    gap = DynamicSourceItem(source_root_id=root.id, relative_path='missing.png', relative_path_hash='gap',
        sync_state='skipped_existing_media', import_status='deferred', deferred_reason='existing_media_hash')
    excluded = DynamicSourceItem(source_root_id=root.id, relative_path='old.heic', relative_path_hash='excluded',
        sync_state='deferred', import_status='deferred', deferred_reason='unsupported_extension')
    policy_rows = [DynamicSourceItem(source_root_id=root.id, relative_path=f'policy-{reason}.png',
        relative_path_hash=reason, sync_state='failed', import_status='deferred', failure_reason=reason)
        for reason in ('hidden', 'zero_byte', 'zero_byte_file')]
    db.add_all([gap, excluded, *policy_rows])
    db.flush()
    settled_reasons = ('unchanged', 'skipped_existing_media', 'skipped_duplicate')
    stable = [dict(relative_path=f'stable-{i}.png', reason=settled_reasons[i % 3]) for i in range(40000)]
    errors = [dict(relative_path=f'missing-{i}.png', source_item_id=gap.id if i == 0 else None,
        reason='stat_error', metadata={'private_diagnostic': {'exception_type': 'FileNotFoundError', 'errno': 2}})
        for i in range(205)]
    run = DynamicSyncRun(run_type='manual_sync_execute', status='completed',
        summary_json={'manual_sync_execute': {'request': {'root_id': root.id}, 'private_discovery': {
            'metadata_dispositions': stable + errors,
            'directory_errors': [dict(path=f'dir-{i}', reason='denied', coverage='unknown') for i in range(3)]}}})
    db.add(run)
    db.commit()
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/admin')
    app.dependency_overrides[routes.get_db] = lambda: db
    app.dependency_overrides[routes.require_admin_mode] = lambda: SimpleNamespace(id=1)
    client = TestClient(app)
    url = '/api/admin/dynamic-library-sync/recovery-items'
    response = client.get(url, params={'root_id': root.id, 'include_policy_excluded': False})
    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 1 and body['items'][0]['source_item_id'] == gap.id
    assert body['items'][0]['last_attempt_run_id'] is None
    assert body['items'][0]['disposition'] == 'waiting_source'
    assert body['items'][0]['reason'] == 'source_missing'
    assert body['items'][0]['last_metadata_run_id'] == run.id
    assert body['items'][0]['metadata_diagnostic']['errno'] == 2
    assert body['discovery_total'] == 208 and body['next_discovery_offset'] == 100
    assert len(response.content) < 100000 and 'stable-' not in response.text
    seen = list(body['metadata_dispositions'])
    for offset in (100, 200):
        page = client.get(url, params={'root_id': root.id, 'discovery_offset': offset}).json()
        assert len(page['metadata_dispositions']) + len(page['directory_errors']) <= 100
        seen += page['metadata_dispositions']
    assert len({row['relative_path'] for row in seen}) == 205
    assert len(page['directory_errors']) == 3 and page['next_discovery_offset'] is None
    assert next(item for item in page['items'] if item['source_item_id'] == excluded.id)['disposition'] == 'policy_excluded'
    assert all(next(item for item in page['items'] if item['source_item_id'] == row.id)['disposition'] == 'policy_excluded'
        for row in policy_rows)
    assert client.get(url, params={'root_id': root.id, 'limit': 201}).status_code == 422


def test_real_cap_one_runs_reach_old_retry_tail(db, tmp_path, monkeypatch):
    from app.models import DynamicSyncRun
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)
    source = tmp_path/'source'
    for i in range(24):
        _write_png(source/f'{i}.png', (i, i+1, i+2))
    root = planner.register_source_root(db, path=source, label='rotation-fixture')
    old_ids = set()
    for i in range(3):
        stat = (source/f'{i}.png').stat()
        item = DynamicSourceItem(source_root_id=root.id, relative_path=f'{i}.png',
            relative_path_hash=planner._hash_text(f'{i}.png'), sync_state='failed',
            import_status='failed', failure_reason='read_timeout', file_size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        db.add(item)
        db.flush()
        old_ids.add(item.id)
    db.commit()
    monkeypatch.setattr(execute_service, '_calculate_manual_plan_file_hash', lambda *a: (None, 'read_timeout'))
    attempted = set()
    cursors = []
    for _ in range(18):
        run, plan = enqueue(db, root, 1)
        result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
        rows = db.query(DynamicSyncRunItem).filter_by(sync_run_id=run.id, item_state='failed').all()
        assert len(rows) == 1
        attempted.add(rows[0].source_item_id)
        cursors.append(result['scheduler_cursor'])
        db.expire_all()  # A new plan/session must use persisted progress.
    assert old_ids <= attempted
    assert len(attempted) == 18
    assert cursors == sorted(set(cursors))
    assert db.query(DynamicSyncRun).count() == 18


def test_history_failure_count_is_reconstructed_from_versioned_outcomes(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)
    source = tmp_path/'source'
    _write_png(source/'one.png')
    root = planner.register_source_root(db, path=source, label='history-fixture')
    # Produce two actual failed runs; make each cooldown due for this fixture.
    monkeypatch.setattr(execute_service, '_calculate_manual_plan_file_hash', lambda *a: (None, 'read_timeout'))
    for _ in range(2):
        run, _ = enqueue(db, root, 1)
        execute_manual_sync_run(db, run_id=run.id)
        item = db.query(DynamicSourceItem).one()
        item.metadata_json = {'manual_sync_retry': {'attempt_count': 900}}
        db.commit()
    run, _ = enqueue(db, root, 1)
    execute_manual_sync_run(db, run_id=run.id)
    db.refresh(item)
    state = recovery(item)
    assert state['disposition'] == 'deferred_diagnosis'
    assert len(state['history_evidence']['matching_version_failure_run_ids']) == 2
    assert len(state['version_failure_run_ids']) == 3


def test_bounded_copy_decodes_compares_identity_and_preserves_existing(tmp_path):
    from app.utils.bounded_source_copy import copy_source, SourceCopyError
    source, target = tmp_path/'source.png', tmp_path/'target.png'
    _write_png(source)
    assert copy_source(source, target, timeout_seconds=10, expected_hash=calculate_file_hash(source)) > 0
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        copy_source(source, target, timeout_seconds=10)
    assert target.read_bytes() == before
    with pytest.raises(SourceCopyError) as mismatch:
        copy_source(source, tmp_path/'mismatch.png', timeout_seconds=10, expected_hash='0'*32)
    assert mismatch.value.diagnostic['reason'] == 'content_changed_after_plan'
    assert not (tmp_path/'mismatch.png').exists()
    invalid = tmp_path/'invalid.png'
    invalid.write_bytes(b'test invalid content')
    with pytest.raises(SourceCopyError) as decode:
        copy_source(invalid, tmp_path/'invalid-copy.png', timeout_seconds=10)
    assert decode.value.diagnostic['stage'] == 'copied_image_decode'
    assert decode.value.diagnostic['exception_type'] == 'UnidentifiedImageError'
    assert not (tmp_path/'invalid-copy.png').exists()
    with pytest.raises(SourceCopyError) as timeout:
        copy_source(source, tmp_path/'timeout.png', timeout_seconds=0.001)
    assert timeout.value.diagnostic['worker_status'] == 'timeout'
    assert not (tmp_path/'timeout.png').exists()


def test_production_missing_models_preserves_import_and_pending_downstream(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch, tmp_path)
    monkeypatch.setenv('VIOLET_ENV','production')
    monkeypatch.setenv('CONTENT_CLASSIFICATION_ENABLED','true')
    monkeypatch.setenv('CONTENT_CLASSIFICATION_METHOD','clip')
    monkeypatch.setenv('AI_TAGGING_ENABLED','true')
    monkeypatch.setattr(execute_service,'_ensure_clip_model_cache_only',lambda: (False,'classification_model_uncached'))
    monkeypatch.setattr(execute_service,'_ensure_wd_tagger_model_cache_only',lambda: (False,'manual_sync_ai_tagger_model_uncached'))
    source=tmp_path/'source'
    _write_png(source/'healthy.png')
    root=planner.register_source_root(db,path=source,label='unavailable-downstream')
    plan=planner.plan_manual_sync_dry_run(db,source_path=source,source_record_id=root.id,max_files=5,stable_age_seconds=0)
    run=create_manual_sync_execute_run(db,root_id=root.id,max_files=5,hydrated_only=True,stable_age_seconds=0,
        expected_plan_hash=plan['integrity']['plan_hash'],
        confirmation_phrase=plan['integrity']['production_confirmation_phrase'],
        plan_created_at=plan['job']['created_at'],production_acceptance_approved=True)
    result=execute_manual_sync_run(db,run_id=run.id)
    assert result['manual_sync_execute']['outcome_counts']['imported']==1
    assert db.query(Media).count()==1
    item=db.query(DynamicSourceItem).one()
    assert item.classification_status=='skipped_classification_model_uncached'
    assert item.ai_tagging_status!='ai_tagged'
    assert item.import_status=='imported'
    fresh=planner.plan_manual_sync_dry_run(db,source_path=source,source_record_id=root.id,max_files=5,stable_age_seconds=0)
    assert fresh['counts']['estimated_downstream_followup_count']==1


def test_repair_contract_rejects_completion_without_private_evidence():
    from scripts.phase_contracts import check_phase_contract
    result=check_phase_contract('production_import_recovery_v1',dict(contract_id='production_import_recovery_v1',
        target_met=True,candidate_head='0'*40,recovery={},validation={},safe_to_merge=False,route_approved=False))
    assert not result.passed


def test_proven_worker_start_failure_preserves_remaining_work(db, tmp_path, monkeypatch):
    _enable_manual_execute(monkeypatch)
    _patch_test_storage(monkeypatch,tmp_path)
    source=tmp_path/'source'
    for i in range(3):
        _write_png(source/f'{i}.png',(i,2,3))
    root=planner.register_source_root(db,path=source,label='worker-dependency')
    monkeypatch.setattr(execute_service,'_calculate_manual_plan_file_hash',lambda *args: (None,
        SourceReadReason('read_error',dict(shared_dependency='source_worker_start',stage='hash_worker_start',
            worker_status='start_failed',exception_type='OSError',errno=12,winerror=None))))
    run,_=enqueue(db,root,3)
    result=execute_manual_sync_run(db,run_id=run.id)['manual_sync_execute']
    assert result['stopped_by']=='execution_dependency_unavailable'
    assert result['unprocessed_count']==2
    assert db.query(DynamicSyncRunItem).filter_by(sync_run_id=run.id,item_state='deferred_unprocessed').count()==2
