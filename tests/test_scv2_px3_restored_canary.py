"""Mutation tests for the private restored-copy evidence gate; fictional data."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from scripts.check_scv2_px3_restored_canary import validate_observations
from scripts.plan_scv2_px3_controlled_canary import accepted_apply_request


def fictional_observations():
    plan = {'status':'planned','applied':False,'source_mode':'existing_source_metadata','run_key':'fictional-run',
            'input_selection':{'percentage':1,'eligible_work_count':100,'selected_work_count':1,'canonical_fingerprint':'a'*64},
            'selection_fingerprint':'a'*64,'product_result_fingerprint':'b'*64,
            'media_binding':{'local_binding_fingerprint':'c'*64,'planned_signal_source_media_binding_count':2,'planned_media_binding_count':1}}
    plan['canonical_fingerprint']=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    d = {
        'copy-dry-run':plan,'copy-accepted-request':accepted_apply_request(plan,canary_percent=1),
        'copy-apply':{'run_key':'fictional-run','media_binding':{'binding_write_count':2},'product_result_fingerprint':'b'*64,'persistence':{'rollback_available':True}},
        'copy-final-database':{'database_oid':2,'bound_media_ids':[1],'binding_rows':2,'persisted_binding_write_count':2,'result_fingerprint':'b'*64,'active_runs':1,'frozen_metadata_and_truth_unchanged':True,'runtime_metadata_writes_denied':True},
        'copy-replay':{'idempotent_replay':True,'media_binding':{'binding_write_count':0}},
        'copy-baseline-probes':[{'media_id':1,'search':[],'identity':[],'and_search':[]}],
        'copy-after-probes':[{'media_id':1,'creator_id':'fictional','and_query':'fictional title','search':[1],'identity':[1],'and_search':[1],'detail':[{'local_media_support':[1]}]}],
        'copy-rollback-baseline':{'snapshots':{'fixture':{'count':1}},'search_detail_equals_baseline':True,'binding_rows':0,'active_runs':0},
        'copy-migration':{'base_snapshot':{'fixture':{'count':1}}},
        'copy-rollback':{'run_key':'fictional-run','rolled_back':True,'status':'rolled_back','deleted_core_rows':{'media_bindings':2,'resolution_runs':1},'product_audit_rows_retained':True,'forbidden_truth_table_write_count':0},
        'copy-restore':{'database_oid':2},
        'original-backup':{'source_identity':{'database_oid':1}},
        'copy-runtime-isolation':{'copy_oid':2,'source_metadata_refresh_denied':True,'truth_write_denied':True,
            'single_process':True,'original_database_connection_config_absent':True,'redis_enabled':False,
            'background_jobs':False,'original_media_access':False,'writable_tables':['blombooru_source_concepts'],
            'task_directory':str(Path.cwd()/'fictional-task'),'runtime_storage_root':str(Path.cwd()/'fictional-task'/'storage')},
        'copy-repeated-rollback':{'idempotent_replay':True},
        'copy-other-selection-rejected':{'reason':'px3_other_active_selection_requires_rollback'},
        'copy-browser':{'passed':True,'reapply_verified':True,'rollback_baseline_verified':True,'initial_full_detail_requests':0,'original_media_network_requests':0,'page_errors':[]},
    }
    return {k+'-private.json':v for k,v in d.items()}


def test_restored_evidence_never_authorizes_original_apply():
    result=validate_observations(fictional_observations())
    assert result['passed'] and result['original_database_apply_authorized'] is False


@pytest.mark.parametrize('mutation', ['fresh_count','replay','search_empty','false_positive','rollback','active_selection','metadata','browser_media','eager_detail','accepted_plan',
    'rollback_response','rollback_run','rollback_count','isolation_database','isolation_storage','isolation_grant','isolation_original',
    'duplicate_probe','omitted_probe','identity_false_positive','and_false_positive'])
def test_restored_gate_rejects_missing_or_inconsistent_behavior(mutation):
    d=copy.deepcopy(fictional_observations())
    if mutation=='fresh_count':d['copy-final-database-private.json']['persisted_binding_write_count']=0
    if mutation=='replay':d['copy-replay-private.json']['media_binding']['binding_write_count']=1
    if mutation=='search_empty':d['copy-after-probes-private.json'][0]['search']=[]
    if mutation=='false_positive':d['copy-after-probes-private.json'][0]['search']=[1,2]
    if mutation=='rollback':d['copy-rollback-baseline-private.json']['snapshots']={}
    if mutation=='active_selection':d['copy-final-database-private.json']['active_runs']=2
    if mutation=='metadata':d['copy-final-database-private.json']['runtime_metadata_writes_denied']=False
    if mutation=='browser_media':d['copy-browser-private.json']['original_media_network_requests']=1
    if mutation=='eager_detail':d['copy-browser-private.json']['initial_full_detail_requests']=1
    if mutation=='accepted_plan':d['copy-accepted-request-private.json']['accepted_binding_fingerprint']='0'*64
    if mutation=='rollback_response':d['copy-rollback-private.json']['rolled_back']=False
    if mutation=='rollback_run':d['copy-rollback-private.json']['run_key']='other'
    if mutation=='rollback_count':d['copy-rollback-private.json']['deleted_core_rows']['media_bindings']=0
    if mutation=='isolation_database':d['copy-runtime-isolation-private.json']['copy_oid']=1
    if mutation=='isolation_storage':d['copy-runtime-isolation-private.json']['runtime_storage_root']=str(Path.cwd()/'original')
    if mutation=='isolation_grant':d['copy-runtime-isolation-private.json']['writable_tables'].append('blombooru_media')
    if mutation=='isolation_original':d['copy-runtime-isolation-private.json']['original_database_connection_config_absent']=False
    if mutation=='duplicate_probe':
        for name in ('copy-baseline-probes-private.json','copy-after-probes-private.json'):
            d[name].append(copy.deepcopy(d[name][0]))
        plan=d['copy-dry-run-private.json'];plan['media_binding']['planned_media_binding_count']=2
        plan.pop('canonical_fingerprint')
        plan['canonical_fingerprint']=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        d['copy-accepted-request-private.json']=accepted_apply_request(plan,canary_percent=1)
        d['copy-final-database-private.json']['bound_media_ids']=[1,2]
    if mutation=='omitted_probe':d['copy-final-database-private.json']['bound_media_ids']=[2]
    if mutation=='identity_false_positive':d['copy-after-probes-private.json'][0]['identity']=[1,2]
    if mutation=='and_false_positive':d['copy-after-probes-private.json'][0]['and_search']=[1,2]
    with pytest.raises(ValueError):validate_observations(d)
