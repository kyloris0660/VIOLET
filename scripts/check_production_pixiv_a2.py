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


def evidence_path(root,name):
    path=(root/name).resolve(strict=True)
    require(path.is_relative_to(root.resolve()) and path.is_file(),'private_evidence_location')
    return path


def read(root,name):
    return json.loads(evidence_path(root,name).read_text(encoding='utf-8'))


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
    cap=value['budget']['cap_usd']
    require(cap in (10,30) and 0<value['budget']['charged_or_reserved_usd']<=cap,'budget')
    require(value['quality']['failed_cases']==0 and value['quality']['case_count']>0,'quality')
    require(value['workload']['query_count']>=240 and value['workload']['failed_queries']==0,'workload')
    require(value['browser']['originals_loaded']>0 and value['browser']['thumbnails_loaded']>0,'real_media')
    require(value['launcher']['new_process'] and value['launcher']['apply_enabled'] is False,'launcher')
    require(set(value)=={'contract_id','target_met','safe_to_merge','route_approved','project_lead_acceptance',
        'candidate_head','coverage','production','budget','quality','workload','browser','launcher','validation','recovery'},'public_fields')
    require(not re.search(r'(?i)([A-Z]:[\\/]|postgres(?:ql)?://|password|api_key|raw_metadata|source_url)',json.dumps(value)),'public_privacy')


def validation_evidence(private,record,candidate):
    from scripts.production_pixiv_a2_evidence import pytest_outcome
    summary={}
    all_failures=set();remediated=set()
    for label in ('focused','postgresql','non_e2e'):
        gate=record[label];command=read(private,gate['command'])
        log=evidence_path(private,gate['log']).read_text(encoding='utf-8')
        require(command['argv'][1:3]==['-m','pytest'],'validation_command')
        require(command.get('status')=='finished','validation_finished')
        if label!='non_e2e':require(command['source_head']==candidate,'validation_candidate')
        actual,failures=pytest_outcome(command,log,evidence_path(private,gate['xml']) if gate.get('xml') else None)
        require(all(actual[key]==gate[key] for key in ('passed','failed','skipped')) and actual['passed']>0,'validation_counts')
        if label!='non_e2e':require(not failures and actual['failed']==0,'focused_or_postgresql_failure')
        else:
            all_failures=failures
            require(command['argv'][3:]==['tests','--ignore=tests/e2e','-q'],'full_suite_command')
            admission=read(private,'full-non-e2e-admission-private.json')
            require(admission['source_head']==command['source_head'] and admission['full_suite_invocation']==1,'full_suite_admission')
        summary[label]={**actual,'source_head':command['source_head']}
    for item in record.get('remediation',[]):
        command=read(private,item['command']);log=evidence_path(private,item['log']).read_text(encoding='utf-8')
        require(command['source_head']==candidate and command['argv'][1:3]==['-m','pytest'],'remediation_candidate')
        actual,failures=pytest_outcome(command,log,evidence_path(private,item['xml']) if item.get('xml') else None)
        require(not failures and actual['passed']>0,'remediation_failure')
        remediated.update(re.findall(r'^(\S+::\S+) PASSED(?:\s|$)',log,re.MULTILINE))
    known={HISTORICAL_NODE} & all_failures
    if known:
        require('missing_original_ai_execution_evidence' in evidence_path(private,record['non_e2e']['log']).read_text(encoding='utf-8'),'historical_reason')
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
    from app.services.production_pixiv_release_inputs import verify_t0_scope
    verify_t0_scope(scope,read(private,'t0-inventory-private.json'),backup['database'],backup['system_identifier'])
    require(coverage['scope_fingerprint']==scope['canonical_fingerprint'],'fixed_scope')
    expected={row['media_id']:(row['work_id'],row['page_index']) for row in scope['mappings']}
    observed={row['media_id']:(row['work_id'],row['page_index']) for row in coverage['items']}
    require(expected==observed and len(observed)==len(coverage['items'])==scope['media_count'],'fixed_media_mapping')
    require(dict(Counter(row['disposition'] for row in coverage['items']))==coverage['counts'],'disposition_counts')
    events=[json.loads(line) for line in (private/'metadata-dispatch-private.jsonl').read_text(encoding='utf-8').splitlines()]
    attempts=Counter(row['work_id'] for row in events if row['event']=='dispatch')
    require(all(count<=3 for count in attempts.values()),'metadata_attempt_limit')
    require(set(attempts)<={row['work_id'] for row in scope['mappings'] if row['work_id']},'metadata_request_scope')
    timing=read(private,'metadata-dispatch-timing-audit-private.json')
    require(timing['all_actual_http_request_intervals_verified'] is False
        and timing['ordinary_gaps_below_two_seconds']==1594 and timing['ordinary_gaps_below_1_99_seconds']==4
        and timing['minimum_wall_clock_dispatch_gap_seconds']==1.740068,'accepted_historical_timing_gap_retained')
    from app.services.source_concept_budget import AdjudicationBudget
    ledger=read(private,'llm-budget-private.json')
    cap=ledger['cap_microusd']/1000000
    if cap==30:
        amendment=next((r for r in ledger.get('cap_amendments',[]) if r['previous_cap_microusd']==10000000
            and r['cap_microusd']==30000000),None)
        require(amendment and amendment['authorization_source']=='43-CODEX-A2-QUALITY-CLOSEOUT.zh-CN.md'
            and amendment['charged_before_microusd']==9998387 and amendment['call_count_before']==6110,'budget_amendment')
        original=read(private,'closeout43-budget-before-private.json')
        require([r['id'] for r in ledger['calls'][:6110]]==[r['id'] for r in original['calls']],'budget_original_calls_retained')
        require(all(r.get('charged_microusd',r['reserved_microusd'])==old.get('charged_microusd',old['reserved_microusd'])
            for r,old in zip(ledger['calls'],original['calls'])),'budget_original_cost_retained')
    budget=AdjudicationBudget(private/'llm-budget-private.json',model=ledger['model'],cap_usd=cap,
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
    protected=[read(private,name) for name in recovery['preservation_evidence']]
    require(len(protected)>=3 and all(row['tables']==protected[0]['tables'] for row in protected[1:]),'raw_independent_preservation')
    batches=read(private,recovery['batch_evidence'])
    require(batches.get('direct_projection') and batches['direct_projection']==batches.get('resumed_projection')
        and sum(batches['batch_sizes'])==batches['aggregate_count'],'raw_batch_projection')
    source=read(private,recovery['source_evidence'])
    require(source['valid_bindings_before']>0 and {r['change'] for r in source['cases']}=={'update','delete'}
        and all(r['valid_bindings_after_mutation']==0 and r.get('valid_bindings_after_rollback')==source['valid_bindings_before']
            and r.get('original_revision')==r.get('restored_revision') for r in source['cases']),'raw_source_recovery')
    browser=read(private,manifest['browser']);launch=read(private,manifest['launcher'])
    from scripts.production_pixiv_a2_evidence import verify_browser_actions,verify_launcher_action
    browser_actions=verify_browser_actions(browser)
    verify_launcher_action(launch,repo,head)
    require(browser['candidate_head']==launch['candidate_head']==head and browser['api_result_sets_verified'],'fresh_browser_candidate')
    require(launch['before_pid']!=launch['after_pid'] and launch['after_pid']>0
        and launch['database']==backup['database'] and launch['healthy'],'launcher_identity')
    actual_identity=launch.get('server_identity',{})
    require(actual_identity.get('pid')==launch['after_pid'] and actual_identity.get('db_name')==backup['database']
        and Path(actual_identity.get('code_root','')).resolve()==repo,'launcher_actual_service_identity')
    images=[i for page in browser.get('pages',[]) for i in page.get('images',[])]
    originals=[i for i in images if re.search(r'/api/media/\d+/file',i.get('src','')) and i.get('width',0)>0 and i.get('height',0)>0]
    thumbnails=[i for i in images if '/thumbnail' in i.get('src','') and i.get('width',0)>0 and i.get('height',0)>0]
    require(originals and thumbnails and not browser.get('page_errors'),'browser_actual_image_loads')
    for name in browser['screenshots']:
        require((private/name).is_file() and (private/name).stat().st_size>1000,'browser_screenshot')
    quality=read(private,manifest['quality']);workload=read(private,manifest['workload'])
    require(quality['candidate_head']==workload['candidate_head']==head,'quality_candidate')
    from scripts.production_pixiv_a2_evidence import recompute_quality,latency_statistics
    quality_actual=recompute_quality(quality,read(private,quality['oracle_input']),
        suggestion_oracle=read(private,'independent-suggestion-oracle-v3-private.json'),
        creator_oracle=read(private,'independent-creator-homonym-oracle-private.json'),
        baseline=read(private,'full-production-final-1-combined-quality-private.json'))
    require(quality['independent_answer_sources'] and quality_actual['case_count']>=80
        and quality_actual['failed_cases']==0,'independent_quality')
    require(len(workload['queries'])>=240 and all(row['status_code']==200 for row in workload['queries']),'actual_workload')
    baseline=read(private,manifest['workload_baseline'])
    source_latency=latency_statistics(workload['source_layer_measurements'])
    http_latency=latency_statistics(workload['queries'])
    require(source_latency==workload['accepted_source_layer_latency_ms'] and http_latency==workload['latency_ms'],'query_statistics')
    p95_gate=750
    require(source_latency['p95_ms']<=p95_gate and source_latency['max_ms']<=3000,'full_scale_source_search_performance')
    validation=validation_evidence(private,read(private,manifest['validation']),head)
    value={'contract_id':CONTRACT,'target_met':True,'safe_to_merge':False,'route_approved':False,
        'project_lead_acceptance':'pending','candidate_head':head,
        'coverage':{key:coverage[key] for key in ('media_count','mapped_media','mapped_works','mapped_work_pages','counts')},
        'production':{'active_runs':final['after']['active_runs'],'bindings':final['after']['bindings'],
            'bound_media':len(bound),'duplicate_support_count':final['after']['duplicate_support_count']},
        'budget':{'model':ledger['model'],'cap_usd':cap,'charged_or_reserved_usd':budget['charged_or_reserved_usd'],
            'call_count':budget['call_count'],'unknown_usage_count':budget['unknown_usage_count']},
        'quality':quality_actual,
        'workload':{'query_count':len(workload['queries']),'failed_queries':0,**http_latency,
            'source_layer_latency_ms':source_latency,'applicable_source_layer_p95_gate_ms':p95_gate},
        'browser':{**{key:browser[key] for key in ('originals_loaded','thumbnails_loaded')},**browser_actions},
        'launcher':{'new_process':True,'apply_enabled':launch['apply_enabled']},'validation':validation,
        'recovery':{'independent_restore':True,'owned_rollback_replay':True,'independent_support_preserved':True,'batch_business_equivalent':True,
            'historical_http_spacing_evidence':'Lead accepted task 43 exception; not reconstructable',
            'historical_command_gaps_below_two_seconds':1594,'historical_minimum_command_gap_seconds':1.740068}}
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
