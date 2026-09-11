"""Fixed-scope production seams, with real persistence and transaction failure."""
import copy
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Media, SourceMetadataRecord, SourceConceptProductRun, SourceConceptProductMediaBinding
from app.services import pixiv_product_integration_service as product
from app.services.pixiv_product_binding_fixture import seed_media_binding_fixture
from app.services.pixiv_metadata_projection_service import build_canonical_pixiv_aggregates_from_session
from app.services.production_pixiv_service import (
    build_fixed_scope, production_consumer, build_production_clustering, replace_production_projection,
)


@pytest.fixture
def database(tmp_path):
    url=os.environ.get('VIOLET_A2_TEST_DATABASE_URL')
    schema=None
    if url:
        from sqlalchemy.engine import make_url
        assert make_url(url).database=='violet_pixiv_a2_test_20260911'
        schema='a2_test_'+uuid.uuid4().hex
        admin=create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        engine=create_engine(url,connect_args={'options':f'-c search_path={schema}'})
    else:
        engine = create_engine('sqlite:///' + str(tmp_path/'scope.sqlite'))
        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    from app.services.source_binding_revision import migrate_source_binding_revisions
    migrate_source_binding_revisions(engine)
    try:
        with Session(engine) as db:
            seed_media_binding_fixture(db)
            yield db
    finally:
        engine.dispose()
        if schema:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            admin.dispose()


def scope_for(db, *, exclude=()):
    rows = [dict(id=row.id,filename=row.filename,path=row.path) for row in db.query(Media) if row.id not in exclude]
    trusted = [dict(media_id=row.media_id,work_id=row.source_work_id,page_index=row.source_page_index) for row in db.query(SourceMetadataRecord)]
    return build_fixed_scope(rows,watermark='2026-09-11T00:00:00Z',trusted_bindings=trusted)


def build(db, works=None):
    aggregates=build_canonical_pixiv_aggregates_from_session(db,work_ids=works)
    return build_production_clustering(production_consumer(aggregates))


def apply(db, run, scope):
    plan=replace_production_projection(db,run,scope=scope)
    return replace_production_projection(db,run,scope=scope,apply=True,accepted_plan=plan)


def test_scope_uses_trusted_source_and_keeps_conflicts():
    rows=[{'id':1,'filename':'12345678_22222222_p1.png'},
          {'id':2,'filename':'12345678.png'}, {'id':3,'filename':'other.png'}]
    scope=build_fixed_scope(rows,watermark='T0',trusted_bindings=[{'media_id':1,'work_id':'22222222','page_index':1},
        {'media_id':2,'work_id':'12345678','page_index':0},{'media_id':2,'work_id':'22222222','page_index':0}])
    assert [row['disposition'] for row in scope['mappings']]==['trusted_source_mapping','conflicting_trusted_sources','not_applicable']
    assert scope['mappings'][0]['work_id']=='22222222'


def test_artist_parenthetical_context_is_adapted_without_dropping_raw_facts(database):
    source=database.query(SourceMetadataRecord).filter_by(id=101).one()
    source.artist_name='Aster (account note)'
    database.commit()
    aggregates=build_canonical_pixiv_aggregates_from_session(database)
    frozen=copy.deepcopy(aggregates)
    consumer=production_consumer(aggregates)
    assert aggregates==frozen
    artists=[s for s in consumer.signals if s.role_hint=='artist']
    assert all(s.work_context_key is None for s in artists)
    assert any(s.raw_value=='Aster (account note)' for s in artists)
    assert any(s.work_context_key for s in consumer.signals if s.role_hint!='artist')


def test_full_scope_replay_and_tail_exclusion(database):
    scope=scope_for(database,exclude=(2,))
    run=build(database)
    first=apply(database,run,scope)
    second=apply(database,run,scope)
    assert second['idempotent_replay'] is True
    assert {row.media_id for row in database.query(SourceConceptProductMediaBinding)}=={1,3,4}
    assert database.query(SourceConceptProductRun).filter_by(status='active').count()==1
    assert first['contract_id']=='production_pixiv_a2_v1'


def test_cumulative_atomic_replacement_keeps_audit(database,monkeypatch):
    scope=scope_for(database)
    partial=build(database,['910000001'])
    original=apply(database,partial,scope)
    full=build(database)
    plan=replace_production_projection(database,full,scope=scope)
    persist=product.persist_media_bindings
    def fail(*args,**kwargs):
        raise RuntimeError('injected failure after old withdrawal')
    monkeypatch.setattr(product,'persist_media_bindings',fail)
    with pytest.raises(RuntimeError,match='injected failure'):
        replace_production_projection(database,full,scope=scope,apply=True,accepted_plan=plan)
    assert database.query(SourceConceptProductRun).filter_by(run_key=original['run_key']).one().status=='active'
    assert {r.media_id for r in database.query(SourceConceptProductMediaBinding)}=={1,2}
    monkeypatch.setattr(product,'persist_media_bindings',persist)
    final=apply(database,full,scope)
    assert {r.media_id for r in database.query(SourceConceptProductMediaBinding)}=={1,2,3,4}
    assert database.query(SourceConceptProductRun).filter_by(run_key=original['run_key']).one().status=='rolled_back'
    product.rollback_pixiv_product_run(database,final['run_key'])
    assert database.query(SourceConceptProductMediaBinding).count()==0
    assert database.query(Media).count()==4
    assert database.query(SourceMetadataRecord).count()==4


def test_source_change_invalidates_prepared_replacement(database):
    scope=scope_for(database)
    run=build(database)
    plan=replace_production_projection(database,run,scope=scope)
    database.query(SourceMetadataRecord).filter_by(id=101).one().title='New title'
    database.commit()
    with pytest.raises(ValueError,match='accepted_replacement_changed'):
        replace_production_projection(database,build(database),scope=scope,apply=True,accepted_plan=plan)
    assert database.query(SourceConceptProductRun).count()==0


def test_frozen_mapping_excludes_new_other_work_on_same_media(database):
    scope=scope_for(database)
    source=database.query(SourceMetadataRecord).filter_by(id=103).one()
    values={col.name:getattr(source,col.name) for col in SourceMetadataRecord.__table__.columns
            if col.name not in ('id','provider_record_key','media_id','created_at','updated_at')}
    database.add(SourceMetadataRecord(id=999,provider_record_key='new-other-work-same-media',media_id=1,**values))
    database.commit()
    apply(database,build(database),scope)
    assert database.query(SourceConceptProductMediaBinding).filter_by(source_metadata_record_id=999).count()==0
    assert {r.media_id for r in database.query(SourceConceptProductMediaBinding)}=={1,2,3,4}


def test_production_snapshot_accounts_missing_page_without_poisoning_valid_page(database):
    from app.services.production_pixiv_service import build_production_inputs
    from app.services.pixiv_metadata_ingestion_service import queue_media_for_pixiv_metadata
    media=database.get(Media,2)
    media.filename='910000001_p99.jpg';media.path='media/910000001_p99.jpg'
    database.query(SourceMetadataRecord).filter_by(media_id=2).delete()
    queue_media_for_pixiv_metadata(database,media)
    database.commit()
    scope=scope_for(database)
    aggregates,coverage=build_production_inputs(database,scope)
    assert coverage['media_count']==4 and sum(coverage['counts'].values())==4
    assert coverage['counts']=={'metadata_complete':3,'metadata_pending':1}
    assert all(row['disposition']=='complete' for row in aggregates)
    assert {(row['work_id'],row['page_index']) for row in aggregates}=={('910000001',0),('910000002',0),('910000003',0)}
    apply(database,build_production_clustering(production_consumer(aggregates)),scope)
    assert {row.media_id for row in database.query(SourceConceptProductMediaBinding)}=={1,3,4}


def test_batch_order_and_resume_receipts_do_not_change_business_identity(database):
    aggregates=build_canonical_pixiv_aggregates_from_session(database)
    initial=build_production_clustering(production_consumer(aggregates))
    judgments=[{'left_signal_key':edge.left_signal_key,'right_signal_key':edge.right_signal_key,
        'judgment_id':f'accepted-{index}','cache_key':f'accepted-{index}',
        'decision':'cannot_link','confidence':0.9} for index,edge in enumerate(initial.resolution.edge_candidates[:2])]
    direct=build_production_clustering(production_consumer(aggregates),judgments=judgments)
    resumed=[{**row,'selected_pair_id':f'resume-{index}','cache_status':'hit','usage':{'historical':True}}
             for index,row in enumerate(reversed(judgments))]
    batches=[aggregates[::2],aggregates[1::2]]
    cumulative=[row for batch in reversed(batches) for row in batch]
    replay=build_production_clustering(production_consumer(cumulative),judgments=resumed)
    assert direct.resolution.run_id==replay.resolution.run_id
    assert direct.business_projection_fingerprint==replay.business_projection_fingerprint
    assert direct.resolution.aliases==replay.resolution.aliases
    scope=scope_for(database)
    first=apply(database,direct,scope)
    second=apply(database,replay,scope)
    assert second['idempotent_replay'] and first['product_result_fingerprint']==second['product_result_fingerprint']


def test_tied_positive_judgments_with_cannot_link_are_stable_across_resume_order(database):
    from itertools import permutations
    from app.models import SourceTagObservation
    from app.services.production_pixiv_semantics import build_semantic_vocabulary
    names=('AlphaVerse','BetaVerse','GammaVerse')
    for name in names:
        database.add(SourceTagObservation(source_metadata_record_id=101,provider='pixiv',
            observation_key='tied-'+name,raw_tag=name,normalized_tag=name.lower(),
            canonical_tag_key=name.lower(),source_category_raw='copyright',status='observed'))
    database.commit()
    aggregates=build_canonical_pixiv_aggregates_from_session(database)
    consumer=production_consumer(aggregates)
    vocabulary=build_semantic_vocabulary([{'canonical_name':name,'category':'copyright','source':'static'} for name in names])
    initial=build_production_clustering(consumer,vocabulary=vocabulary)
    keys={signal.raw_value:signal.signal_key for signal in initial.resolution.signals if signal.raw_value in names}
    judgments=[{'left_signal_key':keys[left],'right_signal_key':keys[right],
        'decision':decision,'confidence':0.9,'cache_key':left+right}
        for left,right,decision in [('AlphaVerse','BetaVerse','must_link'),
            ('BetaVerse','GammaVerse','must_link'),('AlphaVerse','GammaVerse','cannot_link')]]
    fingerprints=set();partitions=set()
    for order in permutations(judgments):
        run=build_production_clustering(consumer,vocabulary=vocabulary,judgments=order)
        groups=tuple(sorted(tuple(sorted(signal.raw_value for signal in concept.signals if signal.raw_value in names))
            for concept in run.resolution.concepts if any(signal.raw_value in names for signal in concept.signals)))
        assert len(groups)==2 and sorted(map(len,groups))==[1,2]
        assert not any({'AlphaVerse','GammaVerse'}<=set(group) for group in groups)
        fingerprints.add(run.business_projection_fingerprint);partitions.add(groups)
    assert len(partitions)==len(fingerprints)==1
