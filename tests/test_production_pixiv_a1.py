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
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[search.get_db] = database
    app.dependency_overrides[media.get_db] = database
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


@pytest.mark.parametrize('caller', ['direct','manual_sync','upload'])
@pytest.mark.parametrize('fault', ['flush','refresh','cache','serialization'])
def test_import_commit_boundary_preserves_durable_files(real_api, monkeypatch, tmp_path, fault, caller):
    client, factory, _, _ = real_api
    settings = media.settings
    from app.enums import FileTypeEnum, RatingEnum
    original = tmp_path/'original'
    thumbs = tmp_path/'thumbnails'
    original.mkdir(); thumbs.mkdir()
    image = (original if caller=='direct' else tmp_path)/'sample.png'
    image.write_bytes(b'one-off-test-input')
    monkeypatch.setattr(settings,'ORIGINAL_DIR',original)
    monkeypatch.setattr(settings,'THUMBNAIL_DIR',thumbs)
    monkeypatch.setattr(settings,'storage_relative_path',lambda p: str(p.relative_to(tmp_path)))
    monkeypatch.setattr(media,'calculate_file_hash',lambda p:'test-import-boundary')
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
        error = HTTPException if fault=='flush' and caller=='upload' else (RuntimeError if fault=='flush' else MediaCommittedError)
        with pytest.raises(error):
            if caller=='direct':
                media.process_and_save_media(db,image,image.name,RatingEnum.safe,'',None,None,None)
            elif caller=='manual_sync':
                from app.services.manual_sync_execute_service import _copy_and_import_media
                _copy_and_import_media(db,image)
            else:
                import asyncio
                with image.open('rb') as handle:
                    asyncio.run(media.upload_media(file=UploadFile(handle,filename='sample.png'),
                        scanned_path=None,rating=RatingEnum.safe,tags='',album_ids=None,source=None,
                        category_hints=None,current_user=None,db=db))
    with factory() as db:
        saved = db.query(Media).filter_by(hash='test-import-boundary').count()
    assert saved == (0 if fault=='flush' else 1)
    assert image.exists()
    if caller!='direct':
        assert (original/'sample.png').exists() == (fault!='flush')
    assert (thumbs/'sample.jpg').exists() == (fault!='flush')
