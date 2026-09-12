import copy
import pytest

from app.services.production_pixiv_release_inputs import (
    verify_t0_scope, verify_full_input, semantic_input_identity, verify_semantic_manifest)
from app.services.production_pixiv_service import build_fixed_scope


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
    verify_semantic_manifest(manifest,aggregates,vocabulary,facts,judgments,'a'*40)
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
