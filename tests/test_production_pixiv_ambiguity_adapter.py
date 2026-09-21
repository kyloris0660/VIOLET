from dataclasses import replace
import sys
from pathlib import Path
import pytest
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'backend'),str(root/'tests')]
from test_phase45_scv2_r2_constraint_aware_graph_remediation import _signal
from app.services.source_concept_resolver_service import build_data_aware_ambiguity_profiles,resolve_source_concepts

def rows(contexts=('franchise',)*5,works=range(1,6),derived=False):
    result=[]
    for index,(context,work) in enumerate(zip(contexts,works)):
        evidence={'production_candidate_scope':f'pixiv:work:{work}','work_id':str(work),'page_index':index}
        if derived:evidence['production_adjudicated_work_context']={'key':'franchise','reason':'explicit_work_context','identity_equivalence_authorized_for_character':False}
        result.append(_signal(str(index),'mona',provider='pixiv',work_context=context,payload=evidence))
    return result

def test_stable_work_units_supply_existing_context_discount():
    p=build_data_aware_ambiguity_profiles(rows())['mona']
    assert p['score']==0.2729 and not p['ambiguous']

def test_pages_and_repeated_imports_do_not_inflate_frequency():
    values=rows(works=[1]*5)
    p=build_data_aware_ambiguity_profiles(values)['mona']
    assert p['signal_frequency']==1
    assert p==build_data_aware_ambiguity_profiles([*values,*values])['mona']


def test_portable_pixiv_input_before_production_scope_keeps_work_grain():
    values=[replace(s,source_record_id='page-fingerprint-'+str(i),
        evidence_payload={k:v for k,v in s.evidence_payload.items() if k!='production_candidate_scope'})
        for i,s in enumerate(rows(works=[1]*5))]
    profile=build_data_aware_ambiguity_profiles(values)['mona']
    assert profile['distinct_evidence_units']==profile['signal_frequency']==1

def test_only_accepted_contexts_are_unified():
    contexts=('franchise','作品','franchise','作品','franchise')
    assert build_data_aware_ambiguity_profiles(rows(contexts,derived=True))['mona']['distinct_work_contexts']==1
    assert build_data_aware_ambiguity_profiles(rows(contexts))['mona']['distinct_work_contexts']==2

def test_genuinely_different_contexts_remain_ambiguous():
    p=build_data_aware_ambiguity_profiles(rows(('one','two','one','two','one')))['mona']
    assert p['ambiguous'] and p['distinct_work_contexts']==2

def test_weak_context_reason_is_not_promoted():
    from app.services.source_concept_resolver_service import signal_context_key,_context_strength
    value=rows(derived=True)[0]
    value=replace(value,evidence_payload={**value.evidence_payload,'production_adjudicated_work_context':{'key':'franchise','reason':'unique_source_or_media_work_context'}})
    build_data_aware_ambiguity_profiles([value])
    assert _context_strength(value,signal_context_key(value,{})[1])=='weak'

def test_valid_cannot_link_still_splits_same_surface():
    values=rows()[:2]
    result=resolve_source_concepts(values,run_id='adapter-control',llm_judgments=[{'left_signal_key':'0','right_signal_key':'1','decision':'cannot_link','confidence':0.99}])
    assert len(result.concepts)==2


def test_historical_profile_replay_does_not_apply_new_discount():
    p=build_data_aware_ambiguity_profiles(rows(),_legacy_database_units=True)['mona']
    assert p['score']==0.5229 and p['ambiguous'] and 'distinct_evidence_units' not in p


def test_historical_policy_is_explicit_and_cannot_be_arbitrary():
    from app.services.production_pixiv_service import production_consumer
    with pytest.raises(ValueError,match='unsupported_historical'):
        production_consumer([],_historical_policy='invented')
