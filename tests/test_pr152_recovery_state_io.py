"""Task33/36 real recovery, Media lifecycle and physical I/O regressions."""

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


def _task36_models(monkeypatch):
    """Deterministic model adapters; real executor, media import and localization."""
    from app.models import Tag, blombooru_media_tags
    monkeypatch.setenv('CONTENT_CLASSIFICATION_ENABLED', 'true')
    monkeypatch.setenv('CONTENT_CLASSIFICATION_METHOD', 'clip')
    monkeypatch.setenv('AI_TAGGING_ENABLED', 'true')
    monkeypatch.setattr(execute_service, '_ensure_clip_model_cache_only', lambda: (True, None))
    calls = []
    def classify(session, media_id):
        session.get(Media, media_id).content_class = 'anime'
        calls.append(('classification', media_id))
        return {'media_id':media_id, 'content_class':'anime', 'method':'clip'}
    def tag(session, media_id):
        row = session.query(Tag).filter_by(name='1girl').first()
        if row is None:
            row = Tag(name='1girl', category='general')
            session.add(row)
            session.flush()
        session.execute(blombooru_media_tags.insert().values(media_id=media_id, tag_id=row.id,
            source='ai_wd', confidence=.95, is_locked=False, is_suggestion=False))
        calls.append(('wd', media_id))
        return {'media_id':media_id, 'tags_added':1, 'suggestions_added':0}
    monkeypatch.setattr(execute_service, '_classify_imported_media', classify)
    monkeypatch.setattr(execute_service, '_ai_tag_imported_media', tag)
    return calls


def _task36_existing(db, tmp_path, monkeypatch, *, legacy=False):
    from app.models import Tag, TagTranslation, blombooru_media_tags
    source, root, storage = setup_import(db, tmp_path, monkeypatch)
    path = source/'changing.png'
    _write_png(path, (10, 20, 30))
    _write_png(storage/'media/original/old.png', (10, 20, 30))
    media = Media(filename='old.png', path='media/original/old.png', hash=calculate_file_hash(path),
        file_size=path.stat().st_size, file_type='image', content_class='anime')
    tag = Tag(name='task36_preserved', category='general')
    db.add_all([media, tag])
    db.flush()
    db.execute(blombooru_media_tags.insert().values(media_id=media.id, tag_id=tag.id,
        source='ai_wd', confidence=.96, is_locked=True, is_suggestion=False))
    db.add(TagTranslation(tag_id=tag.id, canonical_name=tag.name, language='zh-CN', display_name='preserved', source='manual'))
    items = []
    for name in ['changing.png', 'independent.png']:
        _write_png(source/name, (10, 20, 30))
        observed = planner._metadata_for_path(source/name)
        item = DynamicSourceItem(source_root_id=root.id, relative_path=name,
            relative_path_hash=planner._hash_text(name), file_size=observed['file_size'], mtime_ns=observed['mtime_ns'],
            media_id=media.id, content_hash=media.hash, import_status='imported', sync_state='imported',
            classification_status='classified', ai_tagging_status='ai_tagged', localization_status='localized')
        db.add(item)
        if not legacy:
            execute_service._remember_content_hash(item, media.hash, observed)
        items.append(item)
    db.commit()
    return source, root, storage, items[0], items[1], media, _task36_models(monkeypatch)


def _task36_media_facts(session, media_id, storage):
    from app.models import TagTranslation, blombooru_media_tags
    media = session.get(Media, media_id)
    relations = session.execute(blombooru_media_tags.select().where(blombooru_media_tags.c.media_id==media_id)).all()
    return (media.hash, media.path, media.content_class, (storage/media.path).read_bytes(),
        [tuple(row) for row in relations],
        [(row.canonical_name, row.display_name, row.source) for row in session.query(TagTranslation).order_by(TagTranslation.id)])


@pytest.mark.parametrize('discovery', ['update', 'direct_legacy'])
@pytest.mark.parametrize('failure', ['copy', 'decode', 'http'])
def test_existing_media_downstream_survives_post_hash_failure(db, tmp_path, monkeypatch, discovery, failure):
    from fastapi import HTTPException
    from app.utils import bounded_source_copy
    source, root, storage, item, donor, old, calls = _task36_existing(db, tmp_path, monkeypatch, legacy=discovery=='direct_legacy')
    source_id, old_id, donor_id = item.id, old.id, donor.id
    facts = _task36_media_facts(db, old_id, storage)
    path = source/'changing.png'
    if failure=='decode':
        path.write_bytes(b'not a decodable image for task36')
    else:
        _write_png(path, (90, 80, 70))
    _write_png(source/'healthy.png', (50, 60, 70))
    if discovery=='update':
        planner.run_update_check(db, root_ids=[root.id])
    real_copy = bounded_source_copy.copy_source
    real_import = execute_service.process_and_save_media
    def copy(source_file, destination, **kwargs):
        if source_file.name=='changing.png' and failure=='copy':
            raise bounded_source_copy.SourceCopyError({'stage':'source_copy', 'reason':'read_error', 'errno':5})
        return real_copy(source_file, destination, **kwargs)
    def save(**kwargs):
        if kwargs['unique_filename'].startswith('changing') and failure=='http':
            raise HTTPException(status_code=422, detail='task36 nonduplicate import error')
        return real_import(**kwargs)
    monkeypatch.setattr(bounded_source_copy, 'copy_source', copy)
    monkeypatch.setattr(execute_service, 'process_and_save_media', save)
    run, plan = enqueue(db, root, 5)
    assert next(row for row in plan['private_details']['items'] if row.get('source_item_id')==source_id)['work_item_kind']=='RETRY_SOURCE'
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    assert result['outcome_counts']['failed']==1 and result['outcome_counts']['imported']==1
    assert result['stopped_by'] is None
    with Session(db.get_bind()) as fresh:
        current = fresh.get(DynamicSourceItem, source_id)
        assert current.media_id==old_id and source_item_downstream_complete(current)
        assert current.import_status=='failed' and current.failure_reason
        assert current.metadata_json['current_source_version_pending'] is True
        assert current.metadata_json['content_hash_version']['content_hash']==calculate_file_hash(path)
        assert current.content_hash!=old.hash
        assert recovery(current)['version_failure_run_ids']==[run.id]
        failed = fresh.query(DynamicSyncRunItem).filter_by(sync_run_id=run.id, source_item_id=source_id).one()
        assert failed.item_state=='failed' and failed.reason==current.failure_reason
        assert failed.current_metadata_json['private_diagnostic']['stage'] in {'source_copy','copied_image_decode','import'}
        assert _task36_media_facts(fresh, old_id, storage)==facts
        assert source_item_downstream_complete(fresh.get(DynamicSourceItem, donor_id))
        healthy = fresh.query(DynamicSourceItem).filter_by(relative_path='healthy.png').one()
        assert source_item_downstream_complete(healthy)
        assert calls==[('classification', healthy.media_id), ('wd', healthy.media_id)]
        assert planner.classify_source_item(current).work_item_kind.value=='RETRY_SOURCE'
        with client_for(fresh) as client:
            action(client, source_id, 'defer')
        planner.run_update_check(fresh, root_ids=[root.id])
        assert source_item_downstream_complete(current) and disposition(current, planner._metadata_for_path(path))=='deferred_diagnosis'


@pytest.mark.parametrize('discovery', ['update', 'direct_legacy'])
@pytest.mark.parametrize('mode', ['new', 'existing_complete', 'existing_gap', 'existing_localization_gap',
    'http409_complete', 'http409_gap', 'http409_localization_gap', 'post_commit'])
def test_existing_media_switch_or_deduplicate_completes_target(db, tmp_path, monkeypatch, discovery, mode):
    from fastapi import HTTPException
    from app.models import Tag, blombooru_media_tags
    from app.services.media_commit_boundary import MediaCommittedError
    source, root, storage, item, donor, old, calls = _task36_existing(db, tmp_path, monkeypatch, legacy=discovery=='direct_legacy')
    source_id, old_id, donor_id = item.id, old.id, donor.id
    facts = _task36_media_facts(db, old_id, storage)
    _write_png(source/'changing.png', (90, 80, 70))
    target_ids = []
    def create_target():
        destination = storage/'media/original/existing-target.png'
        destination.write_bytes((source/'changing.png').read_bytes())
        target = Media(filename=destination.name, path='media/original/existing-target.png',
            file_size=destination.stat().st_size, hash=calculate_file_hash(destination), file_type='image', content_class='anime')
        db.add(target)
        db.flush()
        target_ids.append(target.id)
        # An independent source proves the target's classification is already done.
        target_source = DynamicSourceItem(source_root_id=root.id, relative_path='target-support.png',
            relative_path_hash=planner._hash_text('target-support.png'), media_id=target.id,
            content_hash=target.hash, sync_state='imported', import_status='imported',
            classification_status='classified', ai_tagging_status='ai_tagged' if mode.endswith(('complete','localization_gap')) else 'pending',
            localization_status='localized' if mode.endswith('complete') else 'waiting_ai_tags')
        db.add(target_source)
        if mode.endswith(('complete','localization_gap')):
            tag=db.query(Tag).filter_by(name='task36_preserved').one()
            db.execute(blombooru_media_tags.insert().values(media_id=target.id, tag_id=tag.id,
                source='ai_wd', confidence=.96, is_locked=True, is_suggestion=False))
        db.commit()
    if discovery=='update':planner.run_update_check(db, root_ids=[root.id])
    run, _ = enqueue(db, root, 5)
    # Populate after planning to isolate this source's binding decision from
    # the independent target source's own future FOLLOWUP schedule.
    if mode.startswith('existing_'):create_target()
    real_import = execute_service.process_and_save_media
    def save(**kwargs):
        if mode.startswith('http409_'):
            create_target()
            raise HTTPException(status_code=409, detail='concurrent matching Media')
        result=real_import(**kwargs)
        if mode=='post_commit':
            raise MediaCommittedError(result.id, file_hash=result.hash, path=result.path)
        return result
    monkeypatch.setattr(execute_service, 'process_and_save_media', save)
    result = execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    with Session(db.get_bind()) as fresh:
        current=fresh.get(DynamicSourceItem, source_id)
        assert current.media_id!=old_id and source_item_downstream_complete(current)
        assert current.import_status=='imported' and current.failure_reason is None
        assert current.metadata_json['current_source_version_pending'] is False
        assert current.content_hash==fresh.get(Media, current.media_id).hash==calculate_file_hash(source/'changing.png')
        assert (storage/fresh.get(Media, current.media_id).path).is_file()
        assert recovery(current)['disposition']=='complete'
        assert _task36_media_facts(fresh, old_id, storage)==facts
        assert source_item_downstream_complete(fresh.get(DynamicSourceItem, donor_id))
        if mode in {'new', 'post_commit'}:
            assert calls==[('classification',current.media_id), ('wd',current.media_id)]
            assert result['outcome_counts']['imported']==1
        else:
            assert current.media_id==target_ids[0]
            assert calls==([] if mode.endswith(('complete','localization_gap')) else [('wd',current.media_id)])
            assert result['outcome_counts']['skipped_existing_media']==1
        assert result['outcome_counts'].get('failed',0)==0
        assert result['localization']['llm_called'] is False


@pytest.mark.parametrize('failure', ['copy', 'http'])
def test_first_import_failure_has_no_completed_media(db, tmp_path, monkeypatch, failure):
    from fastapi import HTTPException
    from app.utils import bounded_source_copy
    source, root, _ = setup_import(db, tmp_path, monkeypatch)
    _write_png(source/'new.png')
    if failure=='copy':
        def fail(*args, **kwargs):raise OSError('task36 first copy error')
        monkeypatch.setattr(bounded_source_copy, 'copy_source', fail)
    else:
        def fail(**kwargs):raise HTTPException(status_code=422, detail='task36 first HTTP error')
        monkeypatch.setattr(execute_service, 'process_and_save_media', fail)
    run, _ = enqueue(db, root, 5)
    result=execute_manual_sync_run(db, run_id=run.id)['manual_sync_execute']
    with Session(db.get_bind()) as fresh:
        item=fresh.query(DynamicSourceItem).one()
        assert item.media_id is None and item.import_status=='failed'
        assert not source_item_downstream_complete(item)
        assert item.classification_status==item.ai_tagging_status=='deferred'
        assert item.localization_status=='blocked_import_failed'
        assert result['outcome_counts']['failed']==1


def test_existing_followup_without_source_hash_executes_only_missing_stage(db, tmp_path, monkeypatch):
    source, root, storage, item, donor, old, calls = _task36_existing(db, tmp_path, monkeypatch)
    item.content_hash=None
    item.localization_status='waiting_localization'
    db.commit()
    # Source validation must be irrelevant to the already available app copy.
    def forbidden(*args, **kwargs):raise AssertionError('FOLLOWUP source hash access')
    monkeypatch.setattr(execute_service, '_calculate_manual_plan_file_hash', forbidden)
    run, plan=enqueue(db, root, 5)
    assert plan['private_details']['items'][0]['work_item_kind']=='FOLLOWUP'
    execute_manual_sync_run(db, run_id=run.id)
    with Session(db.get_bind()) as fresh:
        assert source_item_downstream_complete(fresh.get(DynamicSourceItem,item.id))
        assert fresh.get(DynamicSourceItem,item.id).media_id==old.id
    assert calls==[]


@pytest.mark.parametrize('discovery', ['update', 'direct_legacy'])
def test_existing_media_copy_precommit_interrupt_recovers_in_new_session(db, tmp_path, monkeypatch, discovery):
    from datetime import timedelta
    from app.utils import bounded_source_copy
    source, root, storage, item, donor, old, calls = _task36_existing(db, tmp_path, monkeypatch, legacy=discovery=='direct_legacy')
    source_id, old_id = item.id, old.id
    facts = _task36_media_facts(db, old_id, storage)
    _write_png(source/'changing.png', (90, 80, 70))
    if discovery=='update':planner.run_update_check(db, root_ids=[root.id])
    class CopyInterrupted(BaseException):pass
    def interrupt(*args, **kwargs):raise CopyInterrupted()
    with monkeypatch.context() as patch:
        patch.setattr(bounded_source_copy, 'copy_source', interrupt)
        run, _ = enqueue(db, root, 5)
        run_id = run.id
        with pytest.raises(CopyInterrupted):execute_manual_sync_run(db, run_id=run_id)
    db.rollback()
    with Session(db.get_bind()) as fresh:
        current = fresh.get(DynamicSourceItem, source_id)
        assert current.media_id==old_id and source_item_downstream_complete(current)
        assert current.import_status=='import_in_progress'
        assert current.metadata_json['current_source_version_pending'] is True
        assert recovery(current)['disposition']!='complete'
        assert _task36_media_facts(fresh, old_id, storage)==facts
        stale = fresh.get(DynamicSyncRun, run_id)
        stale.started_at=datetime.now(timezone.utc)-timedelta(hours=1)
        fresh.commit()
        retry, plan = enqueue(fresh, fresh.get(type(root), root.id), 5)
        assert fresh.get(DynamicSyncRun, run_id).status=='failed'
        assert next(row for row in plan['private_details']['items'] if row['source_item_id']==source_id)['work_item_kind']=='RETRY_SOURCE'
        result=execute_manual_sync_run(fresh, run_id=retry.id)['manual_sync_execute']
        fresh.expire_all()
        assert result['outcome_counts']['imported']==1
        assert current.media_id!=old_id and source_item_downstream_complete(current)
        assert current.metadata_json['current_source_version_pending'] is False
        assert calls==[('classification', current.media_id), ('wd', current.media_id)]
        assert result['localization']['llm_called'] is False
        assert _task36_media_facts(fresh, old_id, storage)==facts


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
