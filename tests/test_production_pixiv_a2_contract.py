import pytest

from scripts.check_production_pixiv_a2 import check_public_result
from scripts.phase_contracts.contract_checks import check_phase_contract


def summary():
    return {'contract_id':'production_pixiv_a2_v1','candidate_head':'a'*40,'target_met':True,
        'safe_to_merge':False,'route_approved':False,'project_lead_acceptance':'pending',
        'coverage':{'media_count':10,'counts':{'metadata_complete':8,'terminal_remote_unavailable':2}},
        'production':{'active_runs':1,'bound_media':8,'duplicate_support_count':0},
        'budget':{'cap_usd':10,'charged_or_reserved_usd':0.4},
        'quality':{'case_count':40,'failed_cases':0},'workload':{'query_count':240,'failed_queries':0},
        'browser':{'originals_loaded':3,'thumbnails_loaded':3},
        'launcher':{'new_process':True,'apply_enabled':False},'validation':{},'recovery':{}}


def test_public_projection_cannot_substitute_for_actual_private_evidence():
    value=summary();check_public_result(value)
    result=check_phase_contract('production_pixiv_a2_v1',value)
    assert not result.passed
    assert any(item.code=='a2_evidence_invalid' for item in result.errors)


@pytest.mark.parametrize('fault',['pending','budget','binding','owner','apply'])
def test_completion_rejects_material_gaps_and_authority_claims(fault):
    value=summary()
    if fault=='pending':value['coverage']['counts']={'metadata_complete':8,'metadata_pending':2}
    if fault=='budget':value['budget']['charged_or_reserved_usd']=10.01
    if fault=='binding':value['production']['bound_media']=7
    if fault=='owner':value['project_lead_acceptance']='accepted'
    if fault=='apply':value['launcher']['apply_enabled']=True
    with pytest.raises(ValueError):check_public_result(value)
