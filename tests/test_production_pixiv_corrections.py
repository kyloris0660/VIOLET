import json
from dataclasses import replace
import pytest
from app.services.production_pixiv_corrections import correction_units,signal_semantics
from app.services.production_pixiv_role_extraction import ROLE_SCHEMA,extract_production_roles,BudgetedExtractionProvider
from app.services.production_pixiv_semantics import adapt_production_semantics,build_semantic_vocabulary
from test_production_pixiv_role_coverage import context,Responses,candidate
from test_production_pixiv_role_extraction import task_budget


@pytest.mark.parametrize('field',['display_name','normalized_value','canonical_key'])
def test_correction_uses_the_same_multilingual_candidate_match_as_coverage(tmp_path,field):
    from app.services.production_pixiv_role_extraction import role_target_coverage
    consumer=context(['HeroName','AlternateHero']);vocabulary=build_semantic_vocabulary([])
    base=adapt_production_semantics(consumer,vocabulary,{'schema_version':ROLE_SCHEMA,'records':{}})
    target=next(s for s in base.signals if s.raw_value=='AlternateHero')
    request={'aggregate_fingerprint':'aggregate-12345678','raw_targets':['AlternateHero'],
        'supersedes':{'AlternateHero':signal_semantics(target)},
        'conflict_evidence':['same group explicit role contradiction'],'authorization':'bounded test correction'}
    units,_=correction_units(base,{},[request])
    provider=Responses(lambda group:([{**candidate('HeroName'),field:'AlternateHero'}],[]))
    budget=task_budget(tmp_path,provider)
    result=extract_production_roles(units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    record=result['records'][units[0].extraction_key]
    assert not role_target_coverage(units[0],record)['missing_raw_tags']
    facts={'schema_version':ROLE_SCHEMA,'records':{},'semantic_corrections':[request],'correction_records':result['records']}
    adapted=adapt_production_semantics(consumer,vocabulary,facts)
    assert next(s for s in adapted.signals if s.raw_value=='AlternateHero').role_hint=='character'
    # Matching another spelling in this group does not extend correction scope.
    assert next(s for s in adapted.signals if s.raw_value=='HeroName').role_hint==next(s for s in base.signals if s.raw_value=='HeroName').role_hint
    again=adapt_production_semantics(consumer,vocabulary,facts)
    assert again==adapted and len(provider.calls)==1
    assert json.loads(budget.path.read_text())['calls'][0]['status']=='success'


@pytest.mark.parametrize('candidate_role,expected_role',[('unknown_name_like','unknown'),('source_title','source_title')])
def test_corrected_uncertain_answer_survives_real_resolver_without_promotion(tmp_path,candidate_role,expected_role):
    from app.services.source_concept_resolver_service import resolve_source_concepts,LLMAdjudicationConfig
    value=context(['AmbiguousLiteral']);vocab=build_semantic_vocabulary([])
    base=adapt_production_semantics(value,vocab,{'schema_version':ROLE_SCHEMA,'records':{}})
    request={'aggregate_fingerprint':'aggregate-12345678','raw_targets':['AmbiguousLiteral'],
        'supersedes':{s.raw_value:signal_semantics(s) for s in base.signals},
        'conflict_evidence':['bounded source conflict'],'authorization':'test owner'}
    units,_=correction_units(base,{},[request])
    provider=Responses(lambda group:([{**candidate('AmbiguousLiteral',candidate_role),'status':'needs_review'}],[]))
    extracted=extract_production_roles(units,provider=provider,budget=task_budget(tmp_path,provider),cache_dir=tmp_path/'roles')
    facts={'schema_version':ROLE_SCHEMA,'records':{},'semantic_corrections':[request],'correction_records':extracted['records']}
    adapted=adapt_production_semantics(value,vocab,facts)
    signal=adapted.signals[0]
    assert (signal.role_hint,signal.trust_tier,signal.status)==(expected_role,'weak','needs_review')
    result=resolve_source_concepts(adapted.signals,run_id='correction-uncertainty',
        llm_config=LLMAdjudicationConfig(enabled=False,max_calls=0))
    assert result.links and all(c.status!='active' for c in result.concepts)
    assert len(provider.calls)==1


def test_identical_full_correction_question_reuses_original_answer_without_relabeling(tmp_path):
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    first=context(['Hero','Franchise']);second=context(['Hero','Franchise'],'22222222')
    value=replace(first,signals=(*first.signals,*second.signals))
    vocab=build_semantic_vocabulary([])
    base=adapt_production_semantics(value,vocab,{'schema_version':ROLE_SCHEMA,'records':{}})
    requests=[]
    for aggregate in ('aggregate-12345678','aggregate-22222222'):
        rows=[s for s in base.signals if s.evidence_payload['aggregate_fingerprint']==aggregate]
        requests.append({'aggregate_fingerprint':aggregate,'raw_targets':['Hero','Franchise'],
            'supersedes':{s.raw_value:signal_semantics(s) for s in rows},
            'conflict_evidence':['bounded fixture conflict'],'authorization':'test owner'})
    units,_=correction_units(base,{},requests)
    provider=Responses(lambda group:([candidate('Hero'),candidate('Franchise','work_title')],[]))
    budget=task_budget(tmp_path,provider)
    result=extract_production_roles(units[:1],provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    facts={'schema_version':ROLE_SCHEMA,'records':{},'semantic_corrections':requests,'correction_records':result['records'],
        'correction_equivalent_sources':{units[1].extraction_key:units[0].extraction_key}}
    adapted=adapt_production_semantics(value,vocab,facts)
    assert [s.role_hint for s in adapted.signals if s.raw_value=='Hero']==['character','character']
    assert len(facts['correction_records'])==len(provider.calls)==1
    assert verify_role_response_sources(value,vocab,facts,tmp_path/'roles',json.loads(budget.path.read_text()))['record_count']==1
    altered=replace(value,signals=(*value.signals,*context(['Other'],'22222222').signals))
    with pytest.raises(ValueError,match='reuse_question_changed|answer_missing|scope_or_evidence'):
        adapt_production_semantics(altered,vocab,facts)


def test_evidenced_correction_is_scoped_retains_old_answers_and_attempt_identity(tmp_path):
    value=context(['Hero','Franchise']);vocab=build_semantic_vocabulary([])
    baseline=adapt_production_semantics(value,vocab,{'schema_version':ROLE_SCHEMA,'records':{}})
    request={'aggregate_fingerprint':'aggregate-12345678','raw_targets':['Hero','Franchise'],
        'supersedes':{s.raw_value:signal_semantics(s) for s in baseline.signals},
        'conflict_evidence':['original metadata and explicit Lead conflict finding'], 'authorization':'lead bounded correction'}
    units,_=correction_units(baseline,{},[request])
    provider=Responses(lambda group:([{**candidate('Hero'),'work_context':'Franchise'},candidate('Franchise','work_title')],[]))
    budget=task_budget(tmp_path,provider)
    result=extract_production_roles(units,provider=provider,budget=budget,cache_dir=tmp_path/'roles')
    facts={'schema_version':ROLE_SCHEMA,'records':{},'semantic_corrections':[request],'correction_records':result['records']}
    adapted=adapt_production_semantics(value,vocab,facts)
    hero=next(s for s in adapted.signals if s.raw_value=='Hero')
    assert hero.role_hint=='character' and hero.work_context_key=='franchise'
    assert hero.evidence_payload['production_semantic_correction']['supersedes']['role_hint']=='unknown'
    # Changed group labels/prompts cannot reset the original logical target.
    old=replace(units[0].unit_group,group_key='old-question',data_origin='production_pixiv_residual_roles_v1')
    assert BudgetedExtractionProvider.logical_keys([old])==BudgetedExtractionProvider.logical_keys([units[0].unit_group])
    with pytest.raises(ValueError,match='scope_or_evidence'):
        correction_units(baseline,{},[{**request,'raw_targets':['Absent']}])
    with pytest.raises(ValueError,match='previous_fact_changed'):
        correction_units(baseline,{},[{**request,'supersedes':{}}])
    assert len(provider.calls)==1 and json.loads(budget.path.read_text())['calls'][0]['status']=='success'
