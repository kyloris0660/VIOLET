"""A1 API result sets and transaction boundaries; optional isolated PostgreSQL."""

import copy
import os
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Media, SourceMetadataRecord, SourceConceptProductRun, Tag, blombooru_media_tags
from app.routes import search, media
from app.services import pixiv_product_integration_service as product
from app.services.pixiv_product_binding_fixture import seed_media_binding_fixture
from app.services.source_binding_revision import migrate_source_binding_revisions
from app.services.media_commit_boundary import MediaCommittedError


@pytest.fixture()
def real_api(monkeypatch, tmp_path):
    url = os.environ.get('VIOLET_A1_TEST_DATABASE_URL')
    schema = None
    if url:
        from sqlalchemy.engine import make_url
        assert make_url(url).database == 'violet_a1_test_20260906'
        schema = 'a1_test_' + uuid.uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        engine = create_engine(url, connect_args={'options': f'-c search_path={schema}'})
    else:
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        @event.listens_for(engine, 'connect')
        def enable_fk(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    migrate_source_binding_revisions(engine)
    migrate_source_binding_revisions(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        seed_media_binding_fixture(db)
        if engine.dialect.name == 'postgresql':
            for table in ('blombooru_media','blombooru_source_metadata_records'):
                db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}','id'), (SELECT max(id) FROM {table}))"))
            db.commit()
        run = product.build_clustering_from_source_metadata_session(db)
        plan = product.apply_pixiv_product_plan(db, run, scope_key='a1_regression', source_mode='repository_synthetic', apply=True)
    app = FastAPI()
    app.include_router(search.router)
    app.include_router(media.router)
    from app.routes.admin import pixiv_product_integration as admin_routes
    app.include_router(admin_routes.router, prefix='/api/admin')
    monkeypatch.setenv('SCV2_PX3_PRODUCT_INTEGRATION_ENABLED', '1')
    monkeypatch.setenv('SCV2_PX3_PRODUCT_APPLY_ENABLED', '1')
    app.dependency_overrides[admin_routes.require_admin_mode] = lambda: object()
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[search.get_db] = database
    app.dependency_overrides[media.get_db] = database
    app.dependency_overrides[admin_routes.get_db] = database
    with TestClient(app) as client:
        yield client, factory, plan, engine
    engine.dispose()
    if schema:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def ids(client, query):
    response = client.get('/api/search', params={'q': query, 'limit': 100})
    assert response.status_code == 200, response.text
    body = response.json()
    return {item['id'] for item in body['items']}


@pytest.mark.parametrize('change', ['update', 'delete', 'transaction_rollback', 'independent_consumer', 'unknown_drift'])
@pytest.mark.parametrize('direct_reference', [False, True])
def test_existing_source_api_refresh_formal_rollback_and_replacement(real_api, change, direct_reference, monkeypatch):
    client, factory, synthetic, engine = real_api
    from app.models import SourceConceptAlias, SourceConceptSignal, SourceConceptEvidence
    prefix = '/api/admin/pixiv-product-integration'
    def rollback(key):
        return client.post(prefix+'/runs/'+quote(key, safe='')+'/rollback',
                           json={'confirm': True, 'confirm_phrase': 'ROLLBACK_PIXIV_PRODUCT:'+key})
    assert rollback(synthetic['run_key']).status_code == 200
    if direct_reference:
        original_persist = product.persist_source_concept_resolution
        def persist_with_direct_reference(db, resolution, **kwargs):
            result = original_persist(db, resolution, **kwargs)
            # Supported direct-source FK shape, captured before initial apply's
            # ownership guard. No accepted fingerprint is modified by the test.
            source = db.query(SourceMetadataRecord).order_by(SourceMetadataRecord.source_work_id,SourceMetadataRecord.id).first()
            for model, owner in ((SourceConceptSignal,'created_by_run_id'),(SourceConceptEvidence,'run_id')):
                for child in db.query(model).filter(getattr(model,owner)==resolution.run_id):
                    child.source_metadata_record_id = source.id
            db.flush()
            return result
        monkeypatch.setattr(product,'persist_source_concept_resolution',persist_with_direct_reference)
    def plan_apply():
        response = client.post(prefix+'/source-metadata/run', json={'mode':'dry_run','canary_percent':5})
        assert response.status_code == 200, response.text
        plan = response.json()
        request = dict(mode='apply', canary_percent=5, confirm=True, confirm_phrase='APPLY_PIXIV_SOURCE_CONCEPTS',
                       accepted_selection_fingerprint=plan['selection_fingerprint'],
                       accepted_product_fingerprint=plan['product_result_fingerprint'],
                       accepted_binding_fingerprint=plan['media_binding']['local_binding_fingerprint'])
        result = client.post(prefix+'/source-metadata/run', json=request)
        assert result.status_code == 200, result.text
        return result.json(), request
    applied, request = plan_apply()
    with factory() as db:
        run = db.query(SourceConceptProductRun).filter_by(run_key=applied['run_key']).one()
        original_guard = run.rollback_guard_json['ownership_fingerprint']
        selected = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.source_work_id.in_(applied['input_selection']['selected_work_ids'])).order_by(SourceMetadataRecord.id).first()
        selected_id, selected_media, old_title = selected.id, selected.media_id, selected.title
        independent = db.query(SourceMetadataRecord).filter(SourceMetadataRecord.source_work_id.notin_(applied['input_selection']['selected_work_ids'])).first()
        independent_id, independent_revision = independent.id, independent.binding_revision
    before = ids(client, old_title)
    with engine.connect() as connection:
        transaction = connection.begin()
        if change == 'delete':
            connection.execute(SourceMetadataRecord.__table__.delete().where(SourceMetadataRecord.id==selected_id))
        else:
            connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id==selected_id).values(title='ReplacementGarden'))
        if change == 'transaction_rollback':
            transaction.rollback()
        else:
            transaction.commit()
    with factory() as db:
        run = db.query(SourceConceptProductRun).filter_by(run_key=applied['run_key']).one()
        assert run.rollback_guard_json['ownership_fingerprint'] == original_guard
        assert db.get(SourceMetadataRecord, independent_id).binding_revision == independent_revision
        if change == 'transaction_rollback':
            assert not run.rollback_guard_json.get('source_invalidations')
            assert ids(client, old_title) == before
            assert client.post(prefix+'/source-metadata/run', json=request).json()['idempotent_replay']
            return
        if direct_reference or change == 'delete':
            assert run.rollback_guard_json['source_invalidations']
        if change == 'independent_consumer':
            concept = db.query(SourceConceptAlias).filter_by(created_by_run_id=run.resolver_run_id).first().concept_id
            db.add(SourceConceptAlias(concept_id=concept, alias_key='independent', alias_value='Independent', display_name='Independent',
                       alias_role='artist', status='active', created_by_run_id='independent-consumer'))
            db.commit()
        elif change == 'unknown_drift':
            signal = db.query(SourceConceptSignal).filter_by(created_by_run_id=run.resolver_run_id).first()
            signal.raw_value = 'unexpected drift'
            db.commit()
    assert selected_media not in ids(client, old_title)
    result = rollback(applied['run_key'])
    if change in ('independent_consumer', 'unknown_drift'):
        assert result.status_code == 409
        with factory() as db:
            assert db.query(SourceConceptProductRun).filter_by(run_key=applied['run_key']).one().status == 'active'
            if change == 'independent_consumer':
                assert db.query(SourceConceptAlias).filter_by(created_by_run_id='independent-consumer').count() == 1
        return
    assert result.status_code == 200, result.text
    assert rollback(applied['run_key']).json()['idempotent_replay']
    replacement, request = plan_apply()
    assert replacement['run_key'] != applied['run_key']
    assert client.post(prefix+'/source-metadata/run', json=request).json()['idempotent_replay']


@pytest.mark.parametrize('method', ['orm', 'registry', 'sql_update', 'sql_delete', 'transaction_rollback'])
def test_registry_soft_alias_source_withdrawal(real_api, method):
    client, factory, _, engine = real_api
    from app.models import SourceNameAliasCandidate, SourceSearchableNameAssertion
    with factory() as db:
        record = db.get(SourceMetadataRecord, 101)
        db.add(SourceSearchableNameAssertion(provider='pixiv',source_metadata_record_id=104,
            assertion_key='independent-target',raw_input='OtherGarden',normalized_input='othergarden',
            canonical_name_key='othergarden',asserted_role='work_title',status='searchable_active',
            structured_output_schema_version='fixture',requires_review=False))
        db.add(SourceSearchableNameAssertion(provider='pixiv',source_metadata_record_id=103,
            assertion_key='independent-old-name',raw_input='WithdrawnSoft',normalized_input='withdrawnsoft',
            canonical_name_key='withdrawnsoft',asserted_role='work_title',status='searchable_active',
            structured_output_schema_version='fixture',requires_review=False))
        db.add_all([
            SourceNameAliasCandidate(source_name_key='withdrawnsoft', target_name_key='othergarden',
                source_display_name='WithdrawnSoft', target_display_name='OtherGarden', relation_type='provider_canonical',
                evidence_source='pixiv_provider_canonical', evidence_payload={'provider_record_key': record.provider_record_key}, status='active'),
            SourceNameAliasCandidate(source_name_key='independentsoft', target_name_key='othergarden',
                source_display_name='IndependentSoft', target_display_name='OtherGarden', relation_type='provider_canonical',
                evidence_source='pixiv_provider_canonical', evidence_payload={'provider_record_key': db.get(SourceMetadataRecord,104).provider_record_key}, status='active'),
        ])
        db.commit()
    # These aliases bridge to a different valid record, not the withdrawn one.
    before = ids(client, 'WithdrawnSoft')
    assert before == {3,4}
    if method == 'orm':
        with factory() as db:
            db.get(SourceMetadataRecord, 101).title = 'Changed'
            db.commit()
    elif method == 'registry':
        from app.services import source_metadata_registry_service as registry
        with factory() as db:
            record = db.get(SourceMetadataRecord,101)
            bundle=registry.build_source_registry_bundle([dict(provider='pixiv',provider_record_key=record.provider_record_key,
                media_id=1,source_work_id=record.source_work_id,source_page_index=0,title='Changed')])
            registry.persist_source_registry_bundle(db,bundle,apply=True)
    else:
        with engine.connect() as connection:
            transaction = connection.begin()
            statement = SourceMetadataRecord.__table__.delete() if method=='sql_delete' else update(SourceMetadataRecord).values(title='Changed')
            connection.execute(statement.where(SourceMetadataRecord.id==101))
            transaction.rollback() if method=='transaction_rollback' else transaction.commit()
    assert ids(client,'WithdrawnSoft') == (before if method=='transaction_rollback' else {3})
    assert ids(client,'IndependentSoft') == (before if method=='transaction_rollback' else {4})


def test_last_current_alias_disables_detail_search(real_api):
    _, factory, _, engine = real_api
    from app.models import SourceConcept, SourceConceptAlias
    from app.services.source_concept_search_service import _concept_summary
    with factory() as db:
        concept = db.query(SourceConcept).join(SourceConceptAlias).filter(SourceConceptAlias.display_name=='MoonGarden').first()
        assert concept is not None
        concept_id = concept.id
    with engine.begin() as connection:
        connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id.in_([101,102])).values(title='Changed'))
    with factory() as db:
        summary = _concept_summary(db, db.get(SourceConcept,concept_id))
        assert summary['aliases'] == []
        assert summary['display_name'] == '当前来源名称已撤回'
        assert summary['search_label'] is summary['search_value'] is summary['search_url'] is None


def test_suggestion_alias_negative_and_same_media_intersection(real_api):
    client, factory, _, _ = real_api
    from app.models import TagTranslation
    with factory() as db:
        tag = Tag(name='private_suggestion')
        accepted = Tag(name='accepted_anchor')
        db.add_all([tag, accepted])
        db.flush()
        db.execute(blombooru_media_tags.insert(), [
            dict(media_id=1, tag_id=tag.id, is_suggestion=True),
            dict(media_id=2, tag_id=accepted.id, is_suggestion=False),
        ])
        db.add_all([
            TagTranslation(canonical_name='private_suggestion',language='zh-CN',display_name='私有候选',source='manual',status='translated'),
            TagTranslation(canonical_name='AsterHistorical',language='zh-CN',display_name='星花旧名',source='manual',status='translated'),
        ])
        db.commit()
    assert ids(client, 'private_suggestion') == set()
    assert ids(client, '私有候选') == set()
    assert ids(client, '-私有候选') == {1,2,3,4}
    assert ids(client, '星花旧名 MoonGarden') == {1,2}
    assert ids(client, '-private_suggestion') == {1,2,3,4}
    assert ids(client, 'AsterCurrent') == {1,2,3,4}
    assert ids(client, 'AsterHistorical') == {1,2,3}
    assert ids(client, 'AsterCurrent MoonGarden') == {1,2}
    assert ids(client, 'AsterCurrent accepted_anchor') == {2}
    assert ids(client, 'AsterCurrent -MoonGarden') == {3,4}
    with factory() as db:
        tag = Tag(name='AsterHistorical')
        db.add(tag)
        db.flush()
        db.execute(blombooru_media_tags.insert(), dict(media_id=4,tag_id=tag.id,is_suggestion=True))
        db.commit()
    assert ids(client, 'AsterHistorical') == {1,2,3}


@pytest.mark.skipif(not os.environ.get('VIOLET_A1_TEST_DATABASE_URL'), reason='应用通配符使用 PostgreSQL 正则运算符')
def test_postgresql_suggestion_wildcard(real_api):
    client, factory, _, _ = real_api
    with factory() as db:
        tag = Tag(name='private_suggestion')
        db.add(tag)
        db.flush()
        db.execute(blombooru_media_tags.insert(), dict(media_id=1,tag_id=tag.id,is_suggestion=True))
        db.commit()
    assert ids(client,'private_sug*') == set()
    assert ids(client,'-private_sug*') == {1,2,3,4}


@pytest.mark.parametrize('field,value', [
    ('artist_id','9999999'), ('artist_name','RevisedCreator'),
    ('source_work_id','9999999'), ('source_page_index',8), ('media_id',4),
    ('title','RevisedTitle'), ('status','superseded'),
    ('metadata_kind','untrusted'), ('data_type_label','untrusted'),
    ('raw_metadata_json',{'id':910000001,'num':0,'changed':True}),
    ('provenance',{'source':'untrusted'}), ('provider','other'),
])
def test_source_update_retires_binding_and_old_child_search(real_api, field, value):
    client, factory, _, engine = real_api
    assert ids(client, 'AsterHistorical') == {1,2,3}
    assert 1 in ids(client, 'MoonPetal')
    # Bulk SQL deliberately bypasses all Python ingestion hooks.
    with engine.begin() as connection:
        connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id==101).values(**{field:value}))
    with factory() as db:
        assert db.get(SourceMetadataRecord,101).binding_revision == 1
    assert ids(client, 'AsterHistorical') == {2,3}
    assert 1 not in ids(client, 'MoonPetal')
    response = client.get('/api/media/1')
    assert response.status_code == 200, response.text
    from app.services.source_concept_search_service import list_media_source_concepts
    with factory() as db:
        assert list_media_source_concepts(db,1) == []
        assert list_media_source_concepts(db,2)


def test_deleted_source_has_no_old_binding(real_api):
    client, factory, _, _ = real_api
    with factory() as db:
        db.delete(db.get(SourceMetadataRecord,101))
        db.commit()
    assert ids(client, 'AsterHistorical') == {2,3}


def test_withdrawn_only_alias_cannot_propagate_to_other_media(real_api):
    client, factory, _, engine = real_api
    assert ids(client,'AsterHistorical') == {1,2,3}
    with engine.begin() as connection:
        connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id==103).values(artist_name='NewOnlyName'))
    assert ids(client,'AsterHistorical') == set()
    assert ids(client,'AsterCurrent') == {1,2,4}
    from app.services.source_concept_search_service import list_media_source_concepts
    with factory() as db:
        details = list_media_source_concepts(db,1)
        assert details
        assert all('AsterHistorical' not in str(detail) for detail in details)


def test_bulk_sql_source_change_cannot_reuse_old_cached_response(real_api, monkeypatch):
    client, _, _, engine = real_api
    from app.utils import cache
    class MemoryCache:
        _enabled=True
        client=None
        def __init__(self): self.values={}
        def get(self,key): return self.values.get(key)
        def set(self,key,value,expire): self.values[key]=value
    cached=MemoryCache()
    monkeypatch.setattr(cache,'redis_cache',cached)
    assert ids(client,'AsterHistorical') == {1,2,3}
    assert cached.values
    keys=set(cached.values)
    with engine.begin() as connection:
        connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id==103).values(artist_name='ChangedName'))
    assert ids(client,'AsterHistorical') == set()
    assert set(cached.values) != keys


def test_revision_noop_and_transaction_rollback(real_api):
    client, factory, _, engine = real_api
    with engine.begin() as connection:
        connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id==101).values(title='MoonGarden'))
    with factory() as db:
        assert db.get(SourceMetadataRecord,101).binding_revision == 0
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            connection.execute(update(SourceMetadataRecord).where(SourceMetadataRecord.id==101).values(title='transaction_failure'))
            raise RuntimeError('rollback')
    assert ids(client,'AsterHistorical') == {1,2,3}


@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
def test_reused_source_is_withdrawn_with_original(real_api, deleted, legacy):
    client, factory, _, engine = real_api
    with factory() as db:
        original = db.get(SourceMetadataRecord, 101)
        reused = db.get(SourceMetadataRecord, 102)
        reference = {'source_metadata_record_id':original.id} if legacy else {'source_provider_record_key':original.provider_record_key}
        reused.provenance = dict(reused.provenance, **reference)
        db.commit()
    with factory() as db:
        original = db.get(SourceMetadataRecord, 101)
        if deleted:
            db.delete(original)
        else:
            original.artist_id = 'changed_identity'
        db.commit()
    with factory() as db:
        assert db.get(SourceMetadataRecord, 102).status == 'superseded'
        assert db.get(SourceMetadataRecord, 104).status == 'observed'
    assert ids(client, 'AsterHistorical') == {3}


def test_multi_page_work_accounts_for_every_page_and_media(real_api):
    client, factory, plan, _ = real_api
    with factory() as db:
        product.rollback_pixiv_product_run(db, plan['run_key'])
        second = db.get(SourceMetadataRecord, 102)
        second.source_page_index = 1
        second.provider_record_key = 'pixiv:910000001:1:local:2'
        second.raw_metadata_json = dict(second.raw_metadata_json, num=1, page_count=2)
        second.provenance = dict(second.provenance, stable_identity_key={
            'provider':'pixiv', 'work_id':'910000001', 'page_index':1,
        })
        first = db.get(SourceMetadataRecord, 101)
        first.raw_metadata_json = dict(first.raw_metadata_json, page_count=2)
        db.commit()
    with factory() as db:
        run = product.build_clustering_from_source_metadata_session(db)
        assert {(a['work_id'], a['page_index']) for a in run.consumer.aggregates
                if a['work_id']=='910000001'} == {('910000001',0),('910000001',1)}
        result = product.apply_pixiv_product_plan(db, run, scope_key='a1_multipage',
            source_mode='repository_synthetic', apply=True)
        assert result['media_binding']['planned_media_binding_count'] == 4
    assert ids(client, 'AsterCurrent MoonGarden') == {1,2}


def test_policy_capture_rejects_unverifiable_history(real_api):
    _, factory, plan, _ = real_api
    with factory() as db:
        row = db.query(SourceConceptProductRun).filter_by(run_key=plan['run_key']).one()
        summary = dict(row.summary_json)
        summary.pop('policy_versions')
        row.summary_json = summary
        row.result_fingerprint = 'unverifiable'
        db.commit()
    with factory() as db:
        with pytest.raises(product.PixivProductIntegrationError, match='historical_policy_unknown'):
            product.get_pixiv_product_run(db, plan['run_key'])


def test_policy_versions_survive_current_constants_and_legacy_reconstruction(real_api, monkeypatch):
    client, factory, plan, _ = real_api
    with factory() as db:
        original = product.get_pixiv_product_run(db, plan['run_key'])
    monkeypatch.setattr(product,'PX2_CONTEXT_POLICY_VERSION','future_context')
    monkeypatch.setattr(product,'PX2_CANDIDATE_POLICY_VERSION','future_candidate')
    with factory() as db:
        assert product.get_pixiv_product_run(db,plan['run_key'])['policy_versions'] == original['policy_versions']
        row = db.query(SourceConceptProductRun).filter_by(run_key=plan['run_key']).one()
        summary = dict(row.summary_json)
        summary.pop('policy_versions')
        row.summary_json = summary
        db.commit()
    with factory() as db:
        detail = product.get_pixiv_product_run(db,plan['run_key'])
        assert detail['policy_versions'] == original['policy_versions']
        assert detail['policy_version_evidence'] == 'legacy_implementation_verified_by_result_fingerprint'
        assert product.rollback_pixiv_product_run(db,plan['run_key'])['rolled_back']
    with factory() as db:
        assert product.rollback_pixiv_product_run(db,plan['run_key'])['idempotent_replay']
        run = product.build_clustering_from_source_metadata_session(db)
        future = product.apply_pixiv_product_plan(db,run,scope_key='a1_regression',source_mode='repository_synthetic',apply=True)
        assert future['run_key'] != plan['run_key']
        assert product.get_pixiv_product_run(db,future['run_key'])['policy_versions']['context_policy_version'] == 'future_context'
        assert product.apply_pixiv_product_plan(db,run,scope_key='a1_regression',source_mode='repository_synthetic',apply=True)['idempotent_replay']


@pytest.mark.parametrize('caller', ['direct','manual_sync','upload','chunked'])
@pytest.mark.parametrize('fault', ['flush','refresh','cache','serialization'])
def test_import_commit_boundary_preserves_durable_files(real_api, monkeypatch, tmp_path, fault, caller):
    client, factory, _, _ = real_api
    settings = media.settings
    from app.enums import FileTypeEnum, RatingEnum
    original = tmp_path/'original'
    thumbs = tmp_path/'thumbnails'
    original.mkdir(); thumbs.mkdir()
    image = (original if caller=='direct' else tmp_path)/'sample.png'
    from PIL import Image
    Image.new('RGB', (10, 10), (20, 30, 40)).save(image)
    monkeypatch.setattr(settings,'ORIGINAL_DIR',original)
    monkeypatch.setattr(settings,'THUMBNAIL_DIR',thumbs)
    monkeypatch.setattr(settings,'storage_relative_path',lambda p: str(p.relative_to(tmp_path)))
    import hashlib
    expected_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(media,'calculate_file_hash',lambda p:expected_hash)
    monkeypatch.setattr(media,'process_media_file',lambda p:dict(file_type=FileTypeEnum.image,mime_type='image/png',file_size=18,width=10,height=10,duration=None))
    def thumbnail(source,target,kind):
        target.write_bytes(b'test-thumbnail')
        return True
    monkeypatch.setattr(media,'generate_thumbnail',thumbnail)
    def fail(*args,**kwargs):
        raise RuntimeError('injected_'+fault)
    with factory() as db:
        if fault in ('flush','refresh'):
            monkeypatch.setattr(db,fault,fail)
        if fault=='cache':
            monkeypatch.setattr(media,'invalidate_media_cache',fail)
        if fault=='serialization':
            monkeypatch.setattr(media.MediaResponse,'model_validate',fail)
        from fastapi import HTTPException, UploadFile
        error = HTTPException if fault=='flush' and caller in ('upload','chunked') else (RuntimeError if fault=='flush' else MediaCommittedError)
        from contextlib import nullcontext
        with (nullcontext() if caller in ('upload','chunked') and fault!='flush' else pytest.raises(error)):
            if caller=='direct':
                media.process_and_save_media(db,image,image.name,RatingEnum.safe,'',None,None,None)
            elif caller=='manual_sync':
                from app.services.manual_sync_execute_service import _copy_and_import_media
                _copy_and_import_media(db,image)
            elif caller=='chunked':
                import asyncio, json
                chunks = tmp_path/'chunks'
                upload_id = str(uuid.uuid4())
                directory = chunks/upload_id
                directory.mkdir(parents=True)
                (directory/'chunk_0').write_bytes(image.read_bytes())
                (directory/'meta.json').write_text(json.dumps(dict(filename='sample.png',total_chunks=1)))
                monkeypatch.setattr(media,'MEDIA_CHUNKS_DIR',chunks)
                response = asyncio.run(media.finalize_chunked_upload(upload_id=upload_id,rating=RatingEnum.safe,
                    tags='',album_ids=None,source=None,category_hints=None,current_user=None,db=db))
                assert response.status_code == 202
                assert json.loads(response.body)['requires_reupload'] is False
            else:
                import asyncio
                with image.open('rb') as handle:
                    response = asyncio.run(media.upload_media(file=UploadFile(handle,filename='sample.png'),
                        scanned_path=None,rating=RatingEnum.safe,tags='',album_ids=None,source=None,
                        category_hints=None,current_user=None,db=db))
                    assert response.status_code == 202
                    import json
                    assert json.loads(response.body)['requires_reupload'] is False
    with factory() as db:
        saved = db.query(Media).filter_by(hash=expected_hash).count()
    assert saved == (0 if fault=='flush' else 1)
    assert image.exists()
    if caller!='direct':
        assert (original/'sample.png').exists() == (fault!='flush')
    assert (thumbs/'sample.jpg').exists() == (fault!='flush')


@pytest.mark.parametrize('caller', ['manual_sync', 'scanner'])
@pytest.mark.parametrize('fault', ['flush', 'refresh', 'cache', 'serialization', 'identity'])
def test_committed_import_outer_registration_and_downstream(real_api, monkeypatch, tmp_path, caller, fault):
    _, factory, _, _ = real_api
    from test_s3a_m1_manual_sync_execute import _enable_manual_execute, _patch_test_storage, _write_png
    from app.services import manual_sync_execute_service as execute, dynamic_library_sync_service as planner
    from app.models import DynamicSourceItem, DynamicSyncRunItem
    from app.utils.local_library_scanner import scan_and_import
    _enable_manual_execute(monkeypatch)
    monkeypatch.setenv('CONTENT_CLASSIFICATION_ENABLED','false')
    monkeypatch.setenv('AI_TAGGING_ENABLED','false')
    storage = _patch_test_storage(monkeypatch,tmp_path)
    source = tmp_path/'source'
    _write_png(source/'new.png')
    source_bytes = (source/'new.png').read_bytes()
    original_process = media.process_and_save_media
    calls, classified = [], []
    def failing_process(**kwargs):
        calls.append(kwargs['file_path'])
        def fail(*args, **kw): raise RuntimeError('injected_'+fault)
        with monkeypatch.context() as scoped:
            if fault in ('flush','refresh'): scoped.setattr(kwargs['db'],fault,fail)
            if fault in ('cache','identity'): scoped.setattr(media,'invalidate_media_cache',fail)
            if fault=='serialization': scoped.setattr(media.MediaResponse,'model_validate',fail)
            try:
                return original_process(**kwargs)
            except MediaCommittedError as exc:
                if fault=='identity': exc.media_id = 1  # unrelated existing Media
                raise
    monkeypatch.setattr(execute,'process_and_save_media',failing_process)
    monkeypatch.setattr(media,'process_and_save_media',failing_process)
    monkeypatch.setattr(execute,'_classify_imported_media',lambda db,mid: classified.append(mid) or {'skipped':True,'reason':'classification_disabled'})
    with factory() as db:
        if caller == 'manual_sync':
            root = planner.register_source_root(db,path=source,label='bounded fixture')
            def run_once():
                plan = planner.plan_manual_sync_dry_run(db,source_path=source,source_record_id=root.id,max_files=5,stable_age_seconds=0,plan_mode='advanced_full_rescan')
                run = execute.create_manual_sync_execute_run(db,root_id=root.id,max_files=5,hydrated_only=True,stable_age_seconds=0,
                    plan_mode='advanced_full_rescan',expected_plan_hash=plan['integrity']['plan_hash'],
                    confirmation_phrase=plan['integrity']['confirmation_phrase'],plan_created_at=plan['job']['created_at'])
                return execute.execute_manual_sync_run(db,run_id=run.id)
            result = run_once()
        else:
            result = scan_and_import(db,[source])
        saved = db.query(Media).filter(Media.filename=='new.png').one_or_none()
        if fault=='flush':
            assert saved is None
            assert not calls[0].exists()
        elif fault=='identity':
            assert saved is not None and calls[0].exists()
            if caller=='manual_sync':
                item = db.query(DynamicSourceItem).one()
                assert item.import_status == 'failed' and item.media_id is None
                assert item.failure_reason == 'media_committed_identity_recovery_required'
            else:
                assert result['failed'] == 1 and not result['imported_media_ids']
        else:
            assert saved is not None and calls[0].exists()
            assert (storage/saved.thumbnail_path).exists()
            if caller=='manual_sync':
                item = db.query(DynamicSourceItem).one()
                assert item.media_id == saved.id and item.import_status == 'imported'
                assert db.query(DynamicSyncRunItem).one().media_id == saved.id
                assert classified == [saved.id]
                assert item.ai_tagging_status == 'skipped_ai_tagging_disabled'
                assert result['manual_sync_execute']['outcome_counts']['imported_recovery_pending'] == 1
                run_once()
            else:
                assert result['imported'] == 1 and result['failed'] == 0
                assert result['imported_media_ids'] == result['imported_recovery_pending_ids'] == [saved.id]
                replay = scan_and_import(db,[source])
                assert replay['imported'] == 0 and replay['skipped_duplicate'] == 1
            assert len(calls) == 1
            assert db.query(Media).filter(Media.filename=='new.png').count() == 1
        assert (source/'new.png').read_bytes() == source_bytes
