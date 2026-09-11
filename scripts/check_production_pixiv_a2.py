"""Derive the A2 delivery result from this task's actual private evidence.

This gate reports engineering delivery only. It cannot accept the phase for
the owner or project lead, authorize a merge, or advance the route to A3.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
CONTRACT='production_pixiv_a2_v1'
HISTORICAL_NODE='tests/test_phase45_scv2_sv1_controlled_scale_promotion_readiness.py::test_ai_accounting_keeps_original_and_current_invocation_separate'
OPEN_STATES={'metadata_pending','metadata_retryable','normalization_failed','provider_identity_mismatch','unverified_source'}


def require(value,reason):
    if not value:raise ValueError('a2_'+reason)


def read(root,name):
    path=(root/name).resolve(strict=True)
    require(path.is_relative_to(root.resolve()) and path.is_file(),'private_evidence_location')
    return json.loads(path.read_text(encoding='utf-8'))


def check_public_result(value,root=ROOT):
    require(value.get('contract_id')==CONTRACT and value.get('target_met') is True,'engineering_completion')
    require(value.get('safe_to_merge') is False and value.get('route_approved') is False
        and value.get('project_lead_acceptance')=='pending','authority')
    require(re.fullmatch('[0-9a-f]{40}',value.get('candidate_head','')),'candidate')
    coverage=value['coverage'];production=value['production']
    require(sum(coverage['counts'].values())==coverage['media_count']>0,'media_accounting')
    require(not any(coverage['counts'].get(state,0) for state in OPEN_STATES),'open_metadata')
    require(production['active_runs']==1 and production['duplicate_support_count']==0,'active_support')
    require(production['bound_media']==coverage['counts'].get('metadata_complete',0)>5,'full_materialization')
    require(0<value['budget']['charged_or_reserved_usd']<=10 and value['budget']['cap_usd']==10,'budget')
    require(value['quality']['failed_cases']==0 and value['quality']['case_count']>0,'quality')
    require(value['workload']['query_count']>=240 and value['workload']['failed_queries']==0,'workload')
    require(value['browser']['originals_loaded']>0 and value['browser']['thumbnails_loaded']>0,'real_media')
    require(value['launcher']['new_process'] and value['launcher']['apply_enabled'] is False,'launcher')
    require(set(value)=={'contract_id','target_met','safe_to_merge','route_approved','project_lead_acceptance',
        'candidate_head','coverage','production','budget','quality','workload','browser','launcher','validation','recovery'},'public_fields')
    require(not re.search(r'(?i)([A-Z]:[\\/]|postgres(?:ql)?://|password|api_key|raw_metadata|source_url)',json.dumps(value)),'public_privacy')


def validation_evidence(private,record,candidate):
    summary={}
    all_failures=set();remediated=set()
    for label in ('focused','postgresql','non_e2e'):
        gate=record[label];command=read(private,gate['command'])
        log=(private/gate['log']).read_text(encoding='utf-8')
        require(command['argv'][1:3]==['-m','pytest'],'validation_command')
        require(command.get('status')=='finished','validation_finished')
        if label!='non_e2e':require(command['source_head']==candidate,'validation_candidate')
        actual={key:int((re.findall(r'(\d+) '+key+r'\b',log) or ['0'])[-1]) for key in ('passed','failed','skipped')}
        require(all(actual[key]==gate[key] for key in actual) and actual['passed']>0,'validation_counts')
        failures=set(re.findall(r'^FAILED (\S+)',log,re.MULTILINE))
        if label!='non_e2e':require(not failures and actual['failed']==0,'focused_or_postgresql_failure')
        else:
            all_failures=failures
            require(command['argv'][3:]==['tests','--ignore=tests/e2e','-q'],'full_suite_command')
            admission=read(private,'full-non-e2e-admission-private.json')
            require(admission['source_head']==command['source_head'] and admission['full_suite_invocation']==1,'full_suite_admission')
        summary[label]={**actual,'source_head':command['source_head']}
    for item in record.get('remediation',[]):
        command=read(private,item['command']);log=(private/item['log']).read_text(encoding='utf-8')
        require(command['source_head']==candidate and command['argv'][1:3]==['-m','pytest'],'remediation_candidate')
        require(not re.findall(r'^FAILED ',log,re.MULTILINE),'remediation_failure')
        remediated.update(re.findall(r'^(\S+::\S+) PASSED(?:\s|$)',log,re.MULTILINE))
    known={HISTORICAL_NODE} & all_failures
    if known:
        require('missing_original_ai_execution_evidence' in (private/record['non_e2e']['log']).read_text(encoding='utf-8'),'historical_reason')
    for failure in all_failures-known:
        mapped=record.get('node_mappings',{}).get(failure,{'nodes':[failure]})
        require(set(mapped['nodes'])<=remediated and mapped['nodes'],'unresolved_full_suite_failure')
    summary['non_e2e']['known_historical_failures']=len(known)
    summary['non_e2e']['resolved_initial_failures']=len(all_failures-known)
    require(record.get('full_non_e2e_invocations')==1,'one_full_suite')
    return summary


def derive_result(private,repo=ROOT):
    private=Path(private).resolve(strict=True);repo=Path(repo).resolve(strict=True)
    manifest=read(private,'a2-final-evidence-private.json')
    head=manifest['candidate_head']
    from scripts.trusted_git import candidate_behavior_carry_forward
    require(candidate_behavior_carry_forward(repo,head),'behavior_carry_forward')
    backup=read(private,'backup-private.json');restore=read(private,'restore-private.json')
    dump=Path(backup['dump_path'])
    require(dump.is_file() and dump.stat().st_size==backup['bytes']>0,'backup_file')
    require(hashlib.sha256(dump.read_bytes()).hexdigest()==backup['sha256']==restore['backup_sha256'],'backup_digest')
    require(restore['restore_passed'] and restore['target']!=backup['database'] and restore['original_database_overwritten'] is False,'independent_restore')
    scope=read(private,'fixed-scope-private.json');coverage=read(private,manifest['coverage'])
    require(coverage['scope_fingerprint']==scope['canonical_fingerprint'],'fixed_scope')
    expected={row['media_id']:(row['work_id'],row['page_index']) for row in scope['mappings']}
    observed={row['media_id']:(row['work_id'],row['page_index']) for row in coverage['items']}
    require(expected==observed and len(observed)==len(coverage['items'])==scope['media_count'],'fixed_media_mapping')
    require(dict(Counter(row['disposition'] for row in coverage['items']))==coverage['counts'],'disposition_counts')
    events=[json.loads(line) for line in (private/'metadata-dispatch-private.jsonl').read_text(encoding='utf-8').splitlines()]
    attempts=Counter(row['work_id'] for row in events if row['event']=='dispatch')
    require(all(count<=3 for count in attempts.values()),'metadata_attempt_limit')
    require(set(attempts)<={row['work_id'] for row in scope['mappings'] if row['work_id']},'metadata_request_scope')
    from app.services.source_concept_budget import AdjudicationBudget
    ledger=read(private,'llm-budget-private.json')
    budget=AdjudicationBudget(private/'llm-budget-private.json',model=ledger['model'],cap_usd=10,
        input_per_million=0.4,output_per_million=1.6).summary()
    require(ledger['model']=='gpt-4.1-mini' and not any(row['status']=='reserved' for row in ledger['calls']),'settled_budget')
    final=read(private,manifest['production'])
    require(final['production'] is True and final['database']==backup['database']
        and final['system_identifier']==backup['system_identifier'] and final['source_head']==head,'production_identity')
    require(final['result']['applied'] and final['scope_fingerprint']==scope['canonical_fingerprint'],'production_apply')
    bound={row['media_id'] for row in coverage['items'] if row['disposition']=='metadata_complete'}
    require(bound==set(final['after']['bound_media_ids']),'actual_full_media_bindings')
    recovery=read(private,manifest['recovery'])
    for key in ('copy_apply','copy_replay','copy_rollback','copy_repeated_rollback','copy_reapply'):
        operation=read(private,recovery[key])
        require(operation['source_head']==head and operation['database']==restore['target'],'recovery_identity')
        if key in {'copy_apply','copy_reapply'}:require(operation['result']['applied'],'recovery_apply')
        if key in {'copy_replay','copy_repeated_rollback'}:require(operation['result']['idempotent_replay'],'recovery_replay')
        if key=='copy_rollback':require(operation['result']['rolled_back'],'recovery_rollback')
    require(recovery['independent_support_preserved'] and recovery['batch_business_equivalent']
        and recovery['source_update_delete_verified'],'recovery_seams')
    browser=read(private,manifest['browser']);launch=read(private,manifest['launcher'])
    require(browser['candidate_head']==launch['candidate_head']==head and browser['api_result_sets_verified'],'fresh_browser_candidate')
    require(launch['before_pid']!=launch['after_pid'] and launch['after_pid']>0
        and launch['database']==backup['database'] and launch['healthy'],'launcher_identity')
    for name in browser['screenshots']:
        require((private/name).is_file() and (private/name).stat().st_size>1000,'browser_screenshot')
    quality=read(private,manifest['quality']);workload=read(private,manifest['workload'])
    require(quality['candidate_head']==workload['candidate_head']==head,'quality_candidate')
    require(quality['independent_answer_sources'] and all(row['passed'] for row in quality['cases']),'independent_quality')
    require(len(workload['queries'])>=240 and all(row['status_code']==200 for row in workload['queries']),'actual_workload')
    baseline=read(private,manifest['workload_baseline'])
    source_latency=workload['accepted_source_layer_latency_ms']
    p95_gate=max(750,3*baseline['accepted_source_layer_latency_ms']['p95_ms'])
    require(source_latency['p95_ms']<=p95_gate and source_latency['max_ms']<=3000,'full_scale_source_search_performance')
    validation=validation_evidence(private,read(private,manifest['validation']),head)
    value={'contract_id':CONTRACT,'target_met':True,'safe_to_merge':False,'route_approved':False,
        'project_lead_acceptance':'pending','candidate_head':head,
        'coverage':{key:coverage[key] for key in ('media_count','mapped_media','mapped_works','mapped_work_pages','counts')},
        'production':{'active_runs':final['after']['active_runs'],'bindings':final['after']['bindings'],
            'bound_media':len(bound),'duplicate_support_count':final['after']['duplicate_support_count']},
        'budget':{'model':ledger['model'],'cap_usd':10,'charged_or_reserved_usd':budget['charged_or_reserved_usd'],
            'call_count':budget['call_count'],'unknown_usage_count':budget['unknown_usage_count']},
        'quality':{'case_count':len(quality['cases']),'failed_cases':0,'categories':quality['category_counts']},
        'workload':{'query_count':len(workload['queries']),'failed_queries':0,**workload['latency_ms'],
            'source_layer_latency_ms':source_latency,'applicable_source_layer_p95_gate_ms':p95_gate},
        'browser':{key:browser[key] for key in ('originals_loaded','thumbnails_loaded')},
        'launcher':{'new_process':True,'apply_enabled':launch['apply_enabled']},'validation':validation,
        'recovery':{'independent_restore':True,'owned_rollback_replay':True,'independent_support_preserved':True,'batch_business_equivalent':True}}
    check_public_result(value,root=repo)
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    value=derive_result(args.evidence)
    rendered=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(rendered,encoding='utf-8')
    print(rendered)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend'))
    main()
