import json
from dataclasses import replace
import pytest
from app.services.production_pixiv_corrections import correction_units,signal_semantics
from app.services.production_pixiv_role_extraction import ROLE_SCHEMA,extract_production_roles,BudgetedExtractionProvider
from app.services.production_pixiv_semantics import adapt_production_semantics,build_semantic_vocabulary
from test_production_pixiv_role_coverage import context,Responses,candidate
from test_production_pixiv_role_extraction import task_budget


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
