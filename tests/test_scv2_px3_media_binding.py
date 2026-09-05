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
