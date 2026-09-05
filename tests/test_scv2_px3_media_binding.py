"""Actual gallery recall through PX2 aliases on new task-owned databases."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db, migrate_add_source_concept_product_media_bindings
from app.enums import FileTypeEnum
from app.models import (
    Media, SourceMetadataRecord, SourceTagObservation, SourceConceptProductMediaBinding,
    SourceConceptProductRun, SourceConceptResolutionRun, SourceConceptSignal,
    SourceConcept, SourceConceptProductCluster, SourceConceptCandidateDisposition,
    SourceConceptAmbiguityRecord,
)
from app.routes import search, source_concepts
from app.routes.admin import pixiv_product_integration as api
from app.services import pixiv_product_integration_service as service
from app.services.pixiv_metadata_ingestion_service import PIXIV_METADATA_NORMALIZER_VERSION
from app.services.source_concept_search_service import source_layer_search_path_media_ids
from app.utils.cache import invalidate_source_concept_search_cache


from app.services.pixiv_product_binding_fixture import seed_media_binding_fixture


@pytest.fixture
def bound_db(tmp_path):
    engine = create_engine(f'sqlite:///{(tmp_path / "binding.sqlite3").as_posix()}',
                           connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def fk(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        seed_media_binding_fixture(db)
        invalidate_source_concept_search_cache()
        yield db
    engine.dispose()


def client_for(db):
    app = FastAPI()
    app.include_router(search.router)
    app.include_router(source_concepts.router)
    app.include_router(api.router, prefix='/api/admin')
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[api.require_admin_mode] = lambda: object()
    return TestClient(app)


def accepted(plan):
    return dict(accepted_selection_fingerprint=plan['selection_fingerprint'],
                accepted_product_fingerprint=plan['product_result_fingerprint'],
                accepted_binding_fingerprint=plan['media_binding']['local_binding_fingerprint'])


def plan_apply(db, apply=False, plan=None):
    run = service.build_clustering_from_source_metadata_session(db)
    kwargs = dict(scope_key='pixiv:binding-fixture', source_mode='existing_source_metadata')
    if apply:
        plan = plan or service.apply_pixiv_product_plan(db, run, apply=False, **kwargs)
    return service.apply_pixiv_product_plan(db, run, apply=apply, **kwargs,
                                            **(accepted(plan) if apply else {}))


def ids(client, q):
    response = client.get('/api/search', params={'q': q})
    assert response.status_code == 200, response.text
    return {row['id'] for row in response.json()['items']}


def test_actual_gallery_alias_binding_replay_rollback_reapply(bound_db):
    db = bound_db
    client = client_for(db)
    plan = plan_apply(db)
    assert plan['media_binding']['planned_media_binding_count'] == 4
    assert plan['media_binding']['planned_source_record_binding_count'] == 4
    assert plan['media_binding']['binding_write_count'] == 0
    assert db.query(SourceConceptProductMediaBinding).count() == 0
    assert source_layer_search_path_media_ids(db, 'AsterHistorical')['identity'] == set()
    applied = plan_apply(db, True, plan)
    count = db.query(SourceConceptProductMediaBinding).count()
    assert count > 0
    assert ids(client, 'AsterHistorical') == {1, 2, 3}
    assert ids(client, 'AsterCurrent') == {1, 2, 3, 4}  # homonym surface, distinct concepts
    assert ids(client, 'AsterHistorical MoonGarden') == {1, 2}
    assert ids(client, 'AsterHistorical OtherGarden') == set()
    assert ids(client, 'MoonGarden') == {1, 2}
    assert ids(client, 'MoonPetal') == {1, 2}
    assert ids(client, 'AsterHistorical MoonPetal') == {1, 2}
    assert source_layer_search_path_media_ids(db, 'AsterHistorical')['identity'] == {1, 2, 3}
    detail = client.get('/api/source-concepts/media/1').json()['source_concepts']
    assert detail and any('AsterHistorical' in str(item['aliases']) for item in detail)
    assert any('pixiv' in item['providers'] for item in detail)
    assert all(item['is_entity_truth'] is False for item in detail)
    assert plan_apply(db, True)['idempotent_replay']
    assert db.query(SourceConceptProductMediaBinding).count() == count
    service.rollback_pixiv_product_run(db, applied['run_key'])
    assert ids(client, 'AsterHistorical MoonGarden') == set()
    assert client.get('/api/source-concepts/media/1').json()['source_concepts'] == []
    assert db.query(SourceConceptProductMediaBinding).count() == 0
    plan_apply(db, True)
    assert ids(client, 'AsterHistorical MoonGarden') == {1, 2}
    assert client.get('/api/source-concepts/media/1').json()['source_concepts']


@pytest.mark.parametrize('field', ['accepted_selection_fingerprint', 'accepted_product_fingerprint', 'accepted_binding_fingerprint'])
def test_accepted_plan_exact_match_before_any_write(bound_db, field):
    plan = plan_apply(bound_db)
    fields = accepted(plan)
    fields[field] = '0' * 64
    run = service.build_clustering_from_source_metadata_session(bound_db)
    with pytest.raises(service.PixivProductIntegrationError, match='accepted_plan_mismatch'):
        service.apply_pixiv_product_plan(bound_db, run, scope_key='pixiv:binding-fixture',
                                         source_mode='existing_source_metadata', apply=True, **fields)
    assert bound_db.query(SourceConceptProductRun).count() == 0
    assert bound_db.query(SourceConceptSignal).count() == 0


def test_fresh_session_binding_accounting_matches_rows_and_replay(bound_db):
    applied = plan_apply(bound_db, True)
    with sessionmaker(bind=bound_db.get_bind())() as fresh:
        row = fresh.query(SourceConceptProductRun).one()
        count = fresh.query(SourceConceptProductMediaBinding).count()
        assert count > 0
        assert row.summary_json['media_binding']['binding_write_count'] == count
        assert applied['media_binding']['binding_write_count'] == count
        replay = plan_apply(fresh, True)
        assert replay['idempotent_replay']
        assert replay['media_binding']['binding_write_count'] == 0
    with sessionmaker(bind=bound_db.get_bind())() as fresh:
        assert fresh.query(SourceConceptProductRun).one().summary_json['media_binding']['binding_write_count'] == count
        assert fresh.query(SourceConceptProductMediaBinding).count() == count


def test_actual_dry_run_plan_generator_and_different_selection_boundary(bound_db, monkeypatch):
    monkeypatch.setenv('SCV2_PX3_PRODUCT_INTEGRATION_ENABLED', '1')
    monkeypatch.setenv('SCV2_PX3_PRODUCT_APPLY_ENABLED', '1')
    from scripts.plan_scv2_px3_controlled_canary import accepted_apply_request, build_plan
    client = client_for(bound_db)
    endpoint = '/api/admin/pixiv-product-integration/source-metadata/run'
    first = client.post(endpoint, json={'mode': 'dry_run', 'canary_percent': 1}).json()
    generated = build_plan(gate='existing-db-canary', canary_percent=1, work_limit=1, dry_run=first)
    request = generated['apply_request']
    assert all(request[key] == value for key, value in accepted(first).items())
    applied = client.post(endpoint, json=request)
    assert applied.status_code == 200, applied.text
    count = bound_db.query(SourceConceptProductMediaBinding).count()
    other = client.post(endpoint, json={'mode': 'dry_run', 'canary_percent': 2}).json()
    assert other['selection_fingerprint'] != first['selection_fingerprint']
    rejected = client.post(endpoint, json=accepted_apply_request(other, canary_percent=2))
    assert rejected.status_code == 409
    assert rejected.json()['detail'] == 'px3_other_active_selection_requires_rollback'
    assert bound_db.query(SourceConceptProductRun).filter_by(status='active').count() == 1
    assert bound_db.query(SourceConceptProductMediaBinding).count() == count
    assert client.post(endpoint, json=request).json()['idempotent_replay']
    service.rollback_pixiv_product_run(bound_db, applied.json()['run_key'])
    assert client.post(endpoint, json=accepted_apply_request(other, canary_percent=2)).status_code == 200
    assert bound_db.query(SourceConceptProductRun).filter_by(status='active').count() == 1


def test_plan_generator_rejects_edited_dry_run(bound_db, monkeypatch):
    monkeypatch.setenv('SCV2_PX3_PRODUCT_INTEGRATION_ENABLED', '1')
    from scripts.plan_scv2_px3_controlled_canary import accepted_apply_request
    client = client_for(bound_db)
    plan = client.post('/api/admin/pixiv-product-integration/source-metadata/run',
                       json={'mode': 'dry_run', 'canary_percent': 1}).json()
    plan['media_binding']['local_binding_fingerprint'] = '0' * 64
    with pytest.raises(ValueError, match='actual_dry_run_required'):
        accepted_apply_request(plan, canary_percent=1)


def legacy_payload(record):
    return {'category': 'pixiv', 'id': int(record.source_work_id), 'num': record.source_page_index,
            'page_count': 1, 'title': record.title,
            'user': {'id': int(record.artist_id), 'name': record.artist_name}}


@pytest.mark.parametrize('mutation', ['valid', 'work', 'page', 'provider', 'creator', 'title', 'provenance', 'partial_stable', 'redacted', 'pending'])
def test_legacy_stored_provider_provenance_exact_local_binding(bound_db, mutation):
    from app.services.pixiv_product_media_binding import verified_local_binding_provenance
    record = bound_db.query(SourceMetadataRecord).first()
    record.raw_metadata_json = legacy_payload(record)
    record.provenance = {'adapter': 'gallery-dl', 'metadata_only': True, 'original_downloaded': False}
    if mutation == 'work': record.raw_metadata_json['id'] += 1
    if mutation == 'page': record.raw_metadata_json['num'] = 1
    if mutation == 'provider': record.raw_metadata_json['category'] = 'other'
    if mutation == 'creator': record.raw_metadata_json['user']['id'] += 1
    if mutation == 'title': record.raw_metadata_json['title'] = 'Changed'
    if mutation == 'provenance': record.provenance['adapter'] = 'unknown'
    if mutation == 'partial_stable': record.provenance['stable_identity_key'] = {'provider': 'pixiv'}
    if mutation == 'redacted': record.raw_metadata_json = {'private_stdout_artifact': 'never-read'}
    if mutation == 'pending': record.status = 'metadata_pending'
    before = copy.deepcopy((record.raw_metadata_json, record.provenance))
    assert verified_local_binding_provenance(record) is (mutation == 'valid')
    assert (record.raw_metadata_json, record.provenance) == before


def test_legacy_binding_and_rejected_popularity_tag_keep_px1_px2_identity(bound_db):
    for record in bound_db.query(SourceMetadataRecord):
        record.raw_metadata_json = legacy_payload(record)
        record.provenance = {'adapter': 'gallery-dl', 'metadata_only': True, 'original_downloaded': False}
    bound_db.add(SourceTagObservation(source_metadata_record_id=101, provider='pixiv',
        observation_key='popularity', raw_tag='10000users入り', normalized_tag='10000users入り',
        canonical_tag_key='10000users入り', status='observed'))
    bound_db.commit()
    run = service.build_clustering_from_source_metadata_session(bound_db)
    rejected = [s for s in run.consumer.signals if s.status == 'rejected']
    assert rejected and all(s.trust_tier == 'rejected' for s in rejected)
    plan = plan_apply(bound_db)
    applied = plan_apply(bound_db, True, plan)
    assert applied['media_binding']['planned_media_binding_count'] == 4
    assert applied['px1_input_fingerprint'] == run.consumer.input_fingerprint
    assert applied['px2_business_projection_fingerprint'] == run.business_projection_fingerprint
    assert ids(client_for(bound_db), 'AsterHistorical MoonGarden') == {1, 2}
    assert source_layer_search_path_media_ids(bound_db, '10000users入り')['identity'] == set()
    service.rollback_pixiv_product_run(bound_db, applied['run_key'])


def test_canary_eligibility_excludes_nonpixiv_queue_and_pending_metadata(bound_db):
    before = service.select_existing_pixiv_canary_work_ids(bound_db, percentage=1)
    for index, status in enumerate(('not_applicable_non_pixiv', 'metadata_pending', 'observed')):
        bound_db.add(SourceMetadataRecord(provider='pixiv', provider_record_key=f'ineligible:{index}',
            source_work_id='not-a-work' if index != 1 else '999999999', source_page_index=0,
            status=status, artist_name='AsterHistorical'))
    bound_db.commit()
    after = service.select_existing_pixiv_canary_work_ids(bound_db, percentage=1)
    assert after['selected_work_ids'] == before['selected_work_ids']
    assert after['eligible_work_count'] == before['eligible_work_count'] == 3
    assert after['excluded_source_record_count'] == 3


def test_persisted_projection_is_independent_of_database_collation(bound_db, monkeypatch):
    from sqlalchemy.orm import Query
    applied = plan_apply(bound_db, True)
    original = Query.order_by
    def reversed_collation(self, *clauses):
        return original(self, *(clause.desc() for clause in clauses))
    monkeypatch.setattr(Query, 'order_by', reversed_collation)
    detail = service.get_pixiv_product_run(bound_db, applied['run_key'])
    assert detail['result_fingerprint'] == applied['product_result_fingerprint']


def test_stale_plan_metadata_or_media_mapping_rejected(bound_db):
    plan = plan_apply(bound_db)
    record = bound_db.query(SourceMetadataRecord).first()
    record.media_id = 4
    bound_db.commit()
    with pytest.raises(service.PixivProductIntegrationError, match='accepted_plan_mismatch'):
        plan_apply(bound_db, True, plan)
    record.title = 'ChangedTitle'
    bound_db.commit()
    with pytest.raises(service.PixivProductIntegrationError, match='accepted_plan_mismatch'):
        plan_apply(bound_db, True, plan)
    assert bound_db.query(SourceConceptProductRun).count() == 0


def test_preexisting_empty_resolution_run_is_never_deleted(bound_db):
    run = service.build_clustering_from_source_metadata_session(bound_db)
    row = SourceConceptResolutionRun(run_id=run.resolution.run_id, resolver_version='preexisting')
    bound_db.add(row)
    bound_db.commit()
    applied = plan_apply(bound_db, True)
    with pytest.raises(service.PixivProductIntegrationError, match='rollback_guard_not_satisfied'):
        service.rollback_pixiv_product_run(bound_db, applied['run_key'])
    assert bound_db.query(SourceConceptResolutionRun).count() == 1


@pytest.mark.parametrize('status', ['superseded', 'planned', 'failed'])
def test_only_active_run_can_rollback(bound_db, status):
    applied = plan_apply(bound_db, True)
    row = bound_db.query(SourceConceptProductRun).one()
    row.status = status
    bound_db.commit()
    with pytest.raises(service.PixivProductIntegrationError, match='requires_active_run'):
        service.rollback_pixiv_product_run(bound_db, applied['run_key'])
    assert bound_db.query(SourceConceptSignal).count() > 0


def test_later_core_edit_prevents_rollback(bound_db):
    applied = plan_apply(bound_db, True)
    bound_db.query(SourceConcept).first().primary_display_name = 'LaterOwnerEdit'
    bound_db.commit()
    with pytest.raises(service.PixivProductIntegrationError, match='core_or_binding_drift'):
        service.rollback_pixiv_product_run(bound_db, applied['run_key'])


def test_binding_migration_additive_idempotent(bound_db):
    engine = bound_db.get_bind()
    SourceConceptProductMediaBinding.__table__.drop(engine)
    before = set(inspect(engine).get_table_names())
    migrate_add_source_concept_product_media_bindings(engine, inspect(engine))
    migrate_add_source_concept_product_media_bindings(engine, inspect(engine))
    assert set(inspect(engine).get_table_names()) - before == {SourceConceptProductMediaBinding.__tablename__}
    assert bound_db.query(Media).count() == 4


def test_database_row_ids_do_not_change_business_identity(bound_db):
    first = plan_apply(bound_db)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        seed_media_binding_fixture(db, offset=5000)
        other = plan_apply(db)
        for key in ['px1_input_fingerprint', 'px2_business_projection_fingerprint', 'product_result_fingerprint']:
            assert first[key] == other[key]
        assert first['media_binding']['local_binding_fingerprint'] != other['media_binding']['local_binding_fingerprint']
    engine.dispose()


def test_disabled_product_reads_do_not_load_runs(bound_db, monkeypatch):
    monkeypatch.setenv('SCV2_PX3_PRODUCT_INTEGRATION_ENABLED', '0')
    monkeypatch.setattr(api, 'list_pixiv_product_runs', lambda *_: pytest.fail('loaded runs while disabled'))
    client = client_for(bound_db)
    assert client.get('/api/admin/pixiv-product-integration/status').json() == {'enabled': False}
    assert client.get('/api/admin/pixiv-product-integration/runs').status_code == 403
    assert client.get('/api/admin/pixiv-product-integration/runs/any').status_code == 403


def test_persisted_child_and_projection_drift_are_conflicts(bound_db, monkeypatch):
    monkeypatch.setenv('SCV2_PX3_PRODUCT_INTEGRATION_ENABLED', '1')
    applied = plan_apply(bound_db, True)
    child = bound_db.query(SourceConceptProductCluster).first()
    child.canonical_fingerprint = '0' * 64
    bound_db.commit()
    response = client_for(bound_db).get('/api/admin/pixiv-product-integration/runs/' + applied['run_key'])
    assert response.status_code == 409
    assert 'child_fingerprint_drift' in response.text


def test_cached_gallery_response_is_invalidated_only_after_successful_rollback(bound_db, monkeypatch):
    from fnmatch import fnmatch
    from app.utils import cache
    class MemoryCache:
        _enabled = True
        def __init__(self):
            self.data = {}
            self.client = self
        def get(self, key):
            return self.data.get(key)
        def set(self, key, value, expire=None):
            self.data[key] = value
        def scan_iter(self, pattern, count=None):
            return [key for key in self.data if fnmatch(key, pattern)]
        def delete(self, *keys):
            for key in keys:
                self.data.pop(key, None)
    memory = MemoryCache()
    monkeypatch.setattr(cache, 'redis_cache', memory)
    applied = plan_apply(bound_db, True)
    client = client_for(bound_db)
    assert ids(client, 'AsterHistorical MoonGarden') == {1, 2}
    assert memory.data
    service.rollback_pixiv_product_run(bound_db, applied['run_key'])
    assert not memory.data
    assert ids(client, 'AsterHistorical MoonGarden') == set()


def test_api_requires_accepted_plan_and_recomputes_current_inputs(bound_db, monkeypatch):
    monkeypatch.setenv('SCV2_PX3_PRODUCT_INTEGRATION_ENABLED', '1')
    monkeypatch.setenv('SCV2_PX3_PRODUCT_APPLY_ENABLED', '1')
    client = client_for(bound_db)
    url = '/api/admin/pixiv-product-integration/source-metadata/run'
    request = dict(mode='apply', canary_percent=1, confirm=True, confirm_phrase='APPLY_PIXIV_SOURCE_CONCEPTS')
    assert client.post(url, json=request).status_code == 409
    plan = client.post(url, json=dict(mode='dry_run', canary_percent=1)).json()
    assert client.post(url, json={**request, **accepted(plan)}).status_code == 200
    record = bound_db.query(SourceMetadataRecord).filter_by(source_work_id=plan['input_selection']['selected_work_ids'][0]).first()
    record.title = 'OwnerHasNotSeenThis'
    bound_db.commit()
    assert client.post(url, json={**request, **accepted(plan)}).status_code == 409
def test_binding_proof_uses_mapped_metadata_after_database_module_reload(tmp_path, monkeypatch):
    import importlib
    from sqlalchemy.orm import declarative_base
    from app import database
    from app.services import pixiv_product_binding_proof

    monkeypatch.setattr(database, 'Base', declarative_base())
    proof_module = importlib.reload(pixiv_product_binding_proof)
    result = proof_module.prove_media_binding_search(tmp_path)
    assert result['passed']
