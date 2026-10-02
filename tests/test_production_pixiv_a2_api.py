import pytest
from app.models import SourceConceptProductRun,SourceConceptProductMediaBinding,SourceMetadataRecord,Media
from app.services import pixiv_product_integration_service as product
from test_production_pixiv_a1 import real_api,ids
from test_production_pixiv_a2 import scope_for,build,apply,seed_historical_partial
from test_production_pixiv_a2 import _seed_untyped_alias_occurrence


@pytest.mark.parametrize('case',['constraint','typed_review','extra_member','unassigned_literal'])
def test_untyped_alias_real_api_keeps_component_boundary_and_set_algebra(real_api,case):
    from app.models import SourceConcept,SourceConceptSignal,SourceConceptSignalLink
    client,factory,_,_=real_api
    with factory() as db:
        _,signal,_=_seed_untyped_alias_occurrence(db,cannot_link=case=='constraint')
        if case in {'typed_review','extra_member'}:
            link=db.query(SourceConceptSignalLink).filter_by(signal_id=signal.id).one()
            concept=db.get(SourceConcept,link.concept_id)
            if case=='typed_review':concept.concept_type_hint='work'
            else:
                peer=db.query(SourceConceptSignal).filter(SourceConceptSignal.id!=signal.id).first()
                db.add(SourceConceptSignalLink(signal_id=peer.id,concept_id=concept.id,link_status='needs_review',run_id=link.run_id,
                    resolver_version='isolated-constraint-regression',resolution_reason_code='constraint_component_member'))
        db.commit()
    expected={1,2,3,4} if case=='unassigned_literal' else {1,2,3}
    assert ids(client,'MoonPetal')==expected
    literal_and_alias=ids(client,'SunPetal')
    assert literal_and_alias=={1,2,3,4}
    assert ids(client,'MoonPetal SunPetal')==expected&literal_and_alias
    assert ids(client,'SunPetal -MoonPetal')==literal_and_alias-expected


def test_completion_non_name_withdraws_source_search_support(real_api,tmp_path):
    from app.services.production_pixiv_service import production_consumer,build_production_clustering
    from app.services.pixiv_metadata_projection_service import build_canonical_pixiv_aggregates_from_session
    from app.services.production_pixiv_role_extraction import ROLE_SCHEMA,complete_contextual_production_roles
    from app.services.production_pixiv_semantics import build_semantic_vocabulary,adapt_production_semantics
    from test_production_pixiv_role_coverage import Responses,candidate
    from test_production_pixiv_role_extraction import task_budget
    client,factory,independent,_=real_api
    with factory() as db:
        # Explicitly isolate this fixture's production source path; no real
        # media, accepted ordinary tag or independent run is altered.
        product.rollback_pixiv_product_run(db,independent['run_key'])
        consumer=production_consumer(build_canonical_pixiv_aggregates_from_session(db))
        apply(db,build_production_clustering(consumer),scope_for(db))
    before=client.get('/api/search',params={'q':'SunPetal','limit':100}).json()
    assert {r['id'] for r in before['items']}=={3,4}
    assert before['source_concept_expansions']
    vocabulary=build_semantic_vocabulary([])
    provider=Responses(lambda group:([],[{'raw_value':tag['raw_tag'],'disposition':'non_name','reason_code':'fixture_description'}
        for tag in group['tags']]))
    facts=complete_contextual_production_roles(consumer,vocabulary,{'schema_version':ROLE_SCHEMA,'records':{}},
        provider=provider,budget=task_budget(tmp_path,provider),cache_dir=tmp_path/'roles')
    checked=adapt_production_semantics(consumer,vocabulary,facts)
    assert all(s.status=='rejected' for s in checked.signals if s.raw_value=='SunPetal'),[(s.raw_value,s.role_hint,s.status,s.evidence_payload) for s in checked.signals if s.raw_value=='SunPetal']
    with factory() as db:
        apply(db,build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts),scope_for(db))
    after=client.get('/api/search',params={'q':'SunPetal','limit':100}).json()
    assert after['source_concept_expansions']==[]
    # Literal source-tag retrieval is an independent valid support. A non-name
    # decision removes concept expansion, never the underlying provider tag.
    assert {r['id'] for r in after['items']}=={3,4}
    assert ids(client,'AsterHistorical')=={1,2,3}


@pytest.mark.parametrize('other_status',['active','needs_review'])
def test_alias_literal_recall_respects_another_typed_cannot_link_concept(real_api,other_status):
    from dataclasses import replace
    from app.services.production_pixiv_service import production_consumer,build_production_clustering
    from app.services.pixiv_metadata_projection_service import build_canonical_pixiv_aggregates_from_session
    client,factory,_,_=real_api
    with factory() as db:
        consumer=production_consumer(build_canonical_pixiv_aggregates_from_session(db))
        consumer=replace(consumer,signals=tuple(replace(s,role_hint='work',work_context_key=None,
            status=other_status if s.evidence_payload['work_id']=='910000003' else 'active',
            trust_tier='medium' if other_status=='needs_review' and s.evidence_payload['work_id']=='910000003' else 'strong')
            if s.origin_type=='pixiv_tag_observation' else s for s in consumer.signals))
        initial=build_production_clustering(consumer)
        by_work={s.evidence_payload['work_id']:s for s in initial.resolution.signals if s.origin_type=='pixiv_tag_observation'}
        a,b,c=[by_work[w] for w in ('910000001','910000002','910000003')]
        judgments=[{'left_signal_key':left.signal_key,'right_signal_key':right.signal_key,
            'decision':decision,'confidence':.99,'cache_key':'separated-alias-'+str(index)}
            for index,(left,right,decision) in enumerate(((a,b,'must_link'),(b,c,'cannot_link')))]
        run=build_production_clustering(consumer,judgments=judgments)
        links={r.signal_key:r.concept_key for r in run.resolution.links}
        assert links[a.signal_key]==links[b.signal_key] and links[c.signal_key]!=links[b.signal_key]
        apply(db,run,scope_for(db))
    # The literal SunPetal query may match both identities. MoonPetal may not
    # inherit the same-name occurrence that the accepted constraint separated.
    assert 4 in ids(client,'SunPetal')
    assert 4 not in ids(client,'MoonPetal')
    assert ids(client,'MoonPetal')=={1,2,3}
    assert 4 not in ids(client,'MoonPetal SunPetal')
    assert ids(client,'SunPetal -MoonPetal')=={4}


def test_cumulative_projection_search_and_owned_rollback_preserve_independent_consumer(real_api):
    client,factory,independent,engine=real_api
    baseline={query:ids(client,query) for query in ('AsterCurrent','AsterHistorical','AsterCurrent MoonGarden','AsterCurrent -MoonGarden')}
    with factory() as db:
        independent_row=db.query(SourceConceptProductRun).filter_by(run_key=independent['run_key']).one()
        independent_fingerprint=product._rollback_ownership_fingerprint(db,independent_row)
        scope=scope_for(db)
        first=seed_historical_partial(db,build(db,['910000001']),scope)
        full=apply(db,build(db),scope)
        assert product._rollback_ownership_fingerprint(db,independent_row)==independent_fingerprint
        owned=db.query(SourceConceptProductRun).filter_by(run_key=full['run_key']).one()
        assert {row.media_id for row in db.query(SourceConceptProductMediaBinding).filter_by(product_run_id=owned.id)}=={1,2,3,4}
    assert ids(client,'AsterHistorical')=={1,2,3}
    assert ids(client,'AsterCurrent MoonGarden')=={1,2}
    assert ids(client,'AsterCurrent -MoonGarden')=={3,4}
    assert client.get('/api/media/1').status_code==200
    with factory() as db:
        product.rollback_pixiv_product_run(db,full['run_key'])
        assert db.query(SourceConceptProductRun).filter_by(run_key=independent['run_key']).one().status=='active'
        assert db.query(SourceConceptProductRun).filter_by(run_key=first['run_key']).one().status=='rolled_back'
        assert product._rollback_ownership_fingerprint(db,independent_row)==independent_fingerprint
    assert {query:ids(client,query) for query in baseline}==baseline


@pytest.mark.parametrize('change',['update','partial_update','delete','transaction_rollback'])
def test_full_scope_source_change_withdrawal_and_formal_replacement(real_api,change):
    client,factory,independent,engine=real_api
    with factory() as db:
        scope=scope_for(db);first=apply(db,build(db),scope)
        independent_key=independent['run_key']
    assert 1 in ids(client,'MoonGarden')
    with factory() as db:
        selected=db.get(SourceMetadataRecord,101)
        if change=='delete':db.delete(selected)
        else:
            selected.title='ReplacementGarden'
            if change=='update':
                for record in db.query(SourceMetadataRecord).filter_by(source_work_id=selected.source_work_id):
                    record.title='ReplacementGarden'
        if change=='transaction_rollback':db.rollback()
        else:db.commit()
    assert (1 in ids(client,'MoonGarden')) is (change=='transaction_rollback')
    with factory() as db:
        if change in {'partial_update','delete'}:
            before=[(b.id,b.product_run_id,b.media_id) for b in db.query(SourceConceptProductMediaBinding).order_by(SourceConceptProductMediaBinding.id)]
            with pytest.raises(ValueError,match='fixed_media'):
                apply(db,build(db),scope)
            assert before==[(b.id,b.product_run_id,b.media_id) for b in db.query(SourceConceptProductMediaBinding).order_by(SourceConceptProductMediaBinding.id)]
            assert db.query(SourceConceptProductRun).filter_by(run_key=first['run_key']).one().status=='active'
            assert db.query(SourceConceptProductRun).filter_by(run_key=independent_key).one().status=='active'
            return
        withdrawn=product.rollback_pixiv_product_run(db,first['run_key'])
        assert withdrawn['rolled_back']
        assert product.rollback_pixiv_product_run(db,first['run_key'])['idempotent_replay']
        assert db.query(SourceConceptProductRun).filter_by(run_key=independent_key).one().status=='active'
        replacement=apply(db,build(db),scope)
        assert apply(db,build(db),scope)['idempotent_replay']
        assert db.query(Media).count()==4
        owned=db.query(SourceConceptProductRun).filter_by(run_key=replacement['run_key']).one()
        media_ids={b.media_id for b in db.query(SourceConceptProductMediaBinding).filter_by(product_run_id=owned.id)}
        expected={'delete':{2,3,4},'partial_update':{3,4}}.get(change,{1,2,3,4})
        assert media_ids==expected
    if change=='update':assert 1 in ids(client,'ReplacementGarden')
    assert 3 in ids(client,'AsterHistorical')


def test_real_api_recalls_current_literal_alias_without_overlay_or_identity_promotion(real_api,monkeypatch):
    from dataclasses import replace
    from app.models import SourceConceptSignal
    from app.services.production_pixiv_service import production_consumer,build_production_clustering
    from app.services.pixiv_metadata_projection_service import build_canonical_pixiv_aggregates_from_session
    from app.services import source_concept_search_service as source_search
    client,factory,_,_=real_api
    with factory() as db:
        consumer=production_consumer(build_canonical_pixiv_aggregates_from_session(db))
        consumer=replace(consumer,signals=tuple(replace(s,role_hint='work',trust_tier='strong',status='active',work_context_key=None)
            if s.origin_type=='pixiv_tag_observation' and s.evidence_payload['work_id']!='910000003' else s for s in consumer.signals))
        initial=build_production_clustering(consumer)
        typed=[s for s in initial.resolution.signals if s.origin_type=='pixiv_tag_observation' and s.role_hint=='work']
        run=build_production_clustering(consumer,judgments=[{'left_signal_key':typed[0].signal_key,
            'right_signal_key':typed[1].signal_key,'decision':'must_link','confidence':.99,'cache_key':'api-alias-recall-fixture'}])
        apply(db,run,scope_for(db))
        assert 4 not in source_search.source_layer_search_path_media_ids(db,'MoonPetal',include_needs_review=False)['identity']
    monkeypatch.setattr(source_search,'_overlay_fallback_media_ids',lambda *a:(_ for _ in ()).throw(AssertionError('unrelated overlay enabled')))
    assert 4 in ids(client,'MoonPetal')
    assert 4 in ids(client,'MoonPetal SunPetal')
    assert 4 not in ids(client,'MoonPetal -SunPetal')
    with factory() as db:
        signal=db.query(SourceConceptSignal).filter_by(created_by_run_id=run.resolution.run_id,
            canonical_key='sunpetal',role_hint='unknown').one()
        assert signal.role_hint=='unknown'
        signal.origin_type='pixiv_title_observation';db.commit()
    assert 4 not in ids(client,'MoonPetal')
    with factory() as db:
        signal=db.query(SourceConceptSignal).filter_by(created_by_run_id=run.resolution.run_id,
            canonical_key='sunpetal',role_hint='unknown').one()
        signal.origin_type='pixiv_tag_observation';db.get(SourceMetadataRecord,104).title='Changed revision';db.commit()
    assert 4 not in ids(client,'MoonPetal')
