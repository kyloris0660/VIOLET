import copy
import pytest

from app.services.production_pixiv_release_inputs import (
    verify_t0_scope, verify_full_input, semantic_input_identity, verify_semantic_manifest)
from app.services.production_pixiv_service import build_fixed_scope


@pytest.mark.parametrize('changed',['none','candidate','context','raw'])
def test_role_release_replays_original_response_and_full_context(tmp_path,changed):
    import json
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_release_provenance import verify_role_response_sources
    value,vocabulary,facts,provider,budget=partial_facts(tmp_path)
    if changed=='candidate':next(iter(facts['completion_records'].values()))['candidates'][0]['confidence']=0.01
    elif changed=='context':
        from dataclasses import replace
        value=replace(value,signals=value.signals[:-1])
    elif changed=='raw':
        path=next((tmp_path/'roles'/'raw').glob('*.json'))
        raw=json.loads(path.read_text());raw['content']=json.dumps({'records':[]});path.write_text(json.dumps(raw))
    if changed=='none':
        result=verify_role_response_sources(value,vocabulary,facts,tmp_path/'roles',json.loads(budget.path.read_text()))
        assert result['record_count']==1 and result['new_provider_calls']==0
    else:
        with pytest.raises(ValueError):verify_role_response_sources(value,vocabulary,facts,tmp_path/'roles',json.loads(budget.path.read_text()))
    assert len(provider.calls)==1


def test_scope_recomputed_from_independent_t0_rejects_self_consistent_truncation():
    media=[{'id':1,'filename':'12345678_p0.jpg'},{'id':2,'filename':'22222222_p0.jpg'}]
    inventory={'media':media,'metadata':[], 'summary':{'identity':{'database':'prod','system_identifier':'system'},
        'media_total':2,'watermark':{'t0':'T0'}}}
    scope=build_fixed_scope(media,watermark='T0')
    verify_t0_scope(scope,inventory,'prod','system')
    shortened=build_fixed_scope(media[:1],watermark='T0')
    with pytest.raises(ValueError,match='accepted_t0'):
        verify_t0_scope(shortened,inventory,'prod','system')


def test_full_live_input_rejects_subset_revision_change_and_page_mismatch():
    live=[{'work_id':'12345678','page_index':0,'disposition':'complete','revision':2},
          {'work_id':'22222222','page_index':1,'disposition':'complete','revision':3}]
    coverage={'items':[{'work_id':r['work_id'],'page_index':r['page_index'],'disposition':'metadata_complete'} for r in live]}
    verify_full_input(live,live,coverage)
    for changed in (live[:1], [{**live[0],'revision':1},live[1]], []):
        with pytest.raises(ValueError,match='snapshot_changed'):
            verify_full_input(changed,live,coverage)
    with pytest.raises(ValueError,match='work_page_media'):
        verify_full_input(live,live,{'items':coverage['items'][:1]})


def test_semantic_release_binds_current_sources_roles_context_versions_and_judgments():
    from app.services.production_pixiv_release_inputs import semantic_versions
    versions=semantic_versions()
    aggregates=[{'source_revision':4,'canonical_fingerprint':'aggregate'}]; vocabulary={'aliases':['test']}
    facts={'schema_version':versions['role_schema'], 'records':{'legacy':{'candidates':[],
        'schema_version':versions['role_schema'],'extractor_version':versions['extractor'],
        'prompt_version':versions['prompt'],'decision_schema':versions['extraction_schema'],'model':versions['model']}}}
    judgments=[{'decision':'needs_review','error_state':None}]
    manifest={'input_identity':semantic_input_identity(aggregates,vocabulary,facts,judgments),'candidate_head':'a'*40,
        'processing':{'remaining_missing_pair_count':0,'error_count':0,'selected_pair_count':1,'judgment_count':1}}
    # Compatible inherited results require a current completed processing
    # receipt, not invented local provider calls for accepted old caches.
    with pytest.raises(ValueError,match='original_source_replay_required'):
        verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'a'*40)
    stale_pair_prompt=copy.deepcopy(manifest)
    stale_pair_prompt['input_identity']['versions']['pair_prompt']='source_concept_llm_pair_adjudication_v1'
    with pytest.raises(ValueError,match='input_version_or_candidate'):
        verify_semantic_manifest(stale_pair_prompt,aggregates,vocabulary,facts,judgments,'a'*40)
    for key in ('aggregates','role_facts','judgments','versions'):
        stale=copy.deepcopy(manifest);stale['input_identity'][key]='old'
        with pytest.raises(ValueError,match='input_version_or_candidate'):
            verify_semantic_manifest(stale,aggregates,vocabulary,facts,judgments,'a'*40)
    with pytest.raises(ValueError,match='role_artifact_required'):
        semantic_input_identity(aggregates,vocabulary,{},judgments)
    with pytest.raises(ValueError,match='input_version_or_candidate'):
        verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'b'*40)
    manifest['processing']['remaining_missing_pair_count']=1
    with pytest.raises(ValueError,match='processing_incomplete'):
        verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'a'*40)
