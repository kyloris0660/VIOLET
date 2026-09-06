"""A1 本机证据投影。复用 PX3 实际响应，不把副本成功提升为原库成功。"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'production_pixiv_a1_v1'


def require(value, reason):
    if not value:
        raise ValueError('a1_' + reason)


def read(root, name):
    path = root / name
    require(path.is_file() and not path.is_symlink(), 'evidence_file')
    require(path.stat().st_size <= 32 * 1024 * 1024, 'evidence_size')
    return json.loads(path.read_text(encoding='utf-8'))


def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args], text=True, encoding='utf-8').strip()


def check_public_result(result):
    require(result.get('contract_id') == CONTRACT, 'contract')
    require(result.get('target_met') is True, 'target')
    require(result.get('safe_to_merge') is False and result.get('route_approved') is False, 'authority')
    require(result.get('project_lead_acceptance') == 'pending', 'lead_acceptance')
    require(re.fullmatch('[0-9a-f]{40}', result.get('candidate_head','')), 'candidate')
    for phase in ('copy','production'):
        values = result[phase]
        require(0 < values['works'] <= values['pages'] <= values['media'], 'coverage')
        require(values['active_runs']==1 and values['bindings']>0, 'active_bindings')
    require(result['production']['works']==result['copy']['works'], 'selection_count')
    require(result['launcher']['restarted'] and not result['launcher']['apply_enabled'], 'launcher')
    require(result['browser']['originals_loaded']>0 and result['browser']['thumbnails_loaded']>0, 'real_media')
    # 仅固定结构的脱敏投影可以公开；凭据和文件 provenance 仅存在于本机证据。
    require(set(result) == {'contract_id','target_met','safe_to_merge','route_approved',
        'project_lead_acceptance','candidate_head','copy','production','launcher','browser','validation'}, 'public_fields')
    encoded = json.dumps(result, ensure_ascii=False)
    require(not re.search(r'(?i)([A-Z]:[\\/]|postgres(?:ql)?://|password|source_url|raw_metadata|provider_record_key)',encoded), 'public_privacy')


def lifecycle(root, label):
    plan = read(root, label+'-dry-run-private.json')
    applied = read(root, label+'-apply-private.json')
    replay = read(root, label+'-replay-private.json')
    rollback = read(root, label+'-rollback-private.json')
    repeated = read(root, label+'-repeated-rollback-private.json')
    reapplied = read(root, label+'-reapply-private.json')
    final = read(root, label+'-final-database-private.json')
    before = read(root, label+'-baseline-probes-private.json')
    after = read(root, label+'-after-probes-private.json')
    from scripts.plan_scv2_px3_controlled_canary import accepted_apply_request
    require(read(root,label+'-accepted-request-private.json') == accepted_apply_request(plan,canary_percent=1), 'actual_request')
    require(applied['applied'] and reapplied['applied'], 'apply')
    require(replay['idempotent_replay'] and repeated['idempotent_replay'] and rollback['rolled_back'], 'replay_rollback')
    require(all(row['run_key']==plan['run_key'] for row in (applied,replay,rollback,repeated,reapplied)), 'run_ownership')
    require(all(row['product_result_fingerprint']==plan['product_result_fingerprint'] for row in (applied,replay,reapplied)), 'product_identity')
    require(final['active_runs']==1 and final['bindings']==plan['media_binding']['planned_signal_source_media_binding_count'], 'binding_count')
    require(sorted({row['media_id'] for row in after}) == final['bound_media_ids'], 'media_accounting')
    require({row['work_id'] for row in after} == set(plan['input_selection']['selected_work_ids']), 'work_accounting')
    require(len(before)==len(after)>0, 'probes')
    for prior,current in zip(before,after):
        require(prior['media_id']==current['media_id'], 'probe_identity')
        expected={r['media_id'] for r in after if r['creator_id']==current['creator_id']}
        require(set(current['search'])==set(prior['search'])|expected, 'search_set')
        expected_and={r['media_id'] for r in after if r['creator_id']==current['creator_id'] and r['title']==current['title']}
        require(set(current['and_search'])==set(prior['and_search'])|expected_and, 'and_set')
        require(any(c.get('local_media_support') for c in current['detail']), 'detail_support')
    return dict(works=len({r['work_id'] for r in after}),pages=len({(r['work_id'],r['page']) for r in after}),
        media=len(final['bound_media_ids']),bindings=final['bindings'],active_runs=final['active_runs'])


KNOWN_HISTORICAL_NODE = 'tests/test_phase45_scv2_sv1_controlled_scale_promotion_readiness.py::test_ai_accounting_keeps_original_and_current_invocation_separate'


def reconcile_failures(private, validation):
    initial_log=(private/validation['non_e2e']['log']).read_text(encoding='utf-8')
    initial=set(re.findall(r'^FAILED (\S+)',initial_log,re.MULTILINE))
    known=set(validation['known_baseline_failures'])
    resolved=set(validation['resolved_initial_failures'])
    require(known == {KNOWN_HISTORICAL_NODE}, 'known_failure_scope')
    require(initial==known|resolved and not known&resolved, 'unresolved_test_failures')
    require(validation['non_e2e']['failed']==len(initial), 'initial_failure_accounting')
    passed=set()
    for record in validation['remediation']:
        log=(private/record['log']).read_text(encoding='utf-8')
        command=read(private,record['command'])
        if validation.get('candidate_head'):
            require(command.get('source_head')==validation['candidate_head']
                and command.get('behavior_carry_forward') is True, 'remediation_candidate')
        require(command['argv'][1:3] == ['-m','pytest'], 'remediation_command')
        selected=command['tests']
        require(all(node in command['argv'] for node in selected), 'remediation_selected_command')
        actual=set(re.findall(r'^(\S+::\S+) PASSED(?:\s|$)',log,re.MULTILINE))
        require(actual and all(any(node==choice or node.startswith(choice+'[') or node.startswith(choice+'::') for choice in selected) for node in actual), 'remediation_executed_selection')
        failed=set(re.findall(r'^FAILED (\S+)',log,re.MULTILINE))
        require(failed<=known, 'remediation_failure')
        if failed:
            require('missing_original_ai_execution_evidence' in log, 'historical_failure_reason')
        passed.update(actual)
    mappings=validation.get('node_mappings',{})
    require(set(mappings)<=resolved, 'remediation_mapping_scope')
    for case in resolved:
        mapping=mappings.get(case)
        targets=mapping['nodes'] if mapping else [case]
        require(targets and (not mapping or mapping.get('reason')), 'remediation_mapping_reason')
        require(set(targets)<=passed, 'remediation_exact_node_coverage')
    return known, resolved


def derive_result(private, repo=ROOT):
    backup,restore=read(private,'backup-private.json'),read(private,'restore-private.json')
    require(backup['backup_exit_code']==0 and restore['exit_code']==0, 'backup_restore_exit')
    require(backup['source_table_counts']==restore['table_counts'], 'restored_counts')
    require(restore['independent_database'] and backup['identity']!=restore['identity'], 'restore_isolation')
    dump=Path(backup['backup'])
    require(dump.stat().st_size==backup['backup_bytes']>0, 'backup_size')
    require(hashlib.sha256(dump.read_bytes()).hexdigest()==backup['backup_sha256']==restore['backup_sha256'], 'backup_digest')
    copy,production=lifecycle(private,'copy'),lifecycle(private,'production')
    launch=read(private,'bounded-launcher-verification-private.json')
    browser=read(private,'bounded-browser-verification-private.json')
    validation=read(private,'bounded-validation-private.json')
    head=launch['candidate_head']
    require(re.fullmatch('[0-9a-f]{40}',head), 'candidate_head')
    from scripts.trusted_git import candidate_behavior_carry_forward
    require(candidate_behavior_carry_forward(repo, head), 'behavior_carry_forward')
    state = read(repo, 'docs/state/current-phase.json')
    require(state.get('candidate_head') == state.get('production_candidate_head') == head, 'state_candidate')
    require(launch['before_pid'] != launch['after_pid'] and launch['before_pid']>0 and launch['after_pid']>0, 'process_restart')
    identity=dict(name=backup['identity'][0],user=backup['identity'][1],server_system_identifier=backup['identity'][5])
    require(launch['database_identity']==identity and launch['healthy'] and launch['fresh_session_search'], 'runtime_identity')
    require(browser['candidate_head']==head and browser['api_result_sets_verified'], 'browser_candidate')
    for item in browser['screenshots']:
        screenshot=private / item
        require(screenshot.is_file() and screenshot.stat().st_size>1000, 'screenshot')
    require(validation['candidate_head']==head, 'validation_candidate')
    for gate in ('focused', 'postgresql'):
        command=read(private,validation[gate]['command'])
        require(command['source_head']==head and command.get('behavior_carry_forward') is True, 'tested_behavior_candidate')
    historical_head=validation['non_e2e']['candidate_head']
    require(git(repo,'rev-parse',historical_head+'^{commit}')==historical_head
        and git(repo,'merge-base',historical_head,head)==historical_head, 'historical_suite_candidate')
    for gate in ('focused','postgresql','non_e2e'):
        text=(private/validation[gate]['log']).read_text(encoding='utf-8')
        for key in ('passed','failed','skipped'):
            matches=re.findall(r'(\d+) '+key+r'\b',text)
            actual=int(matches[-1]) if matches else 0
            require(actual==validation[gate][key], 'validation_counts')
        require(validation[gate]['passed']>0, 'validation_missing')
    require(validation['postgresql']['failed']==0, 'postgresql_failures')
    focused_log=(private/validation['focused']['log']).read_text(encoding='utf-8')
    focused_failed=set(re.findall(r'^FAILED (\S+)',focused_log,re.MULTILINE))
    followup=validation.get('focused_followup',{})
    if focused_failed:
        followup_log=(private/followup['log']).read_text(encoding='utf-8')
        require(set(followup['tests'])==focused_failed and not re.findall(r'^FAILED ',followup_log,re.MULTILINE), 'focused_followup')
        require(re.search(r'\b'+str(len(focused_failed))+r' passed\b',followup_log), 'focused_followup_count')
    known, resolved = reconcile_failures(private, validation)
    result=dict(contract_id=CONTRACT,target_met=True,safe_to_merge=False,route_approved=False,
        project_lead_acceptance='pending',candidate_head=head,copy=copy,production=production,
        launcher=dict(restarted=True,apply_enabled=launch['apply_enabled']),
        browser=dict(originals_loaded=browser['originals_loaded'],thumbnails_loaded=browser['thumbnails_loaded']),
        validation={key:{count:validation[key][count] for count in ('passed','failed','skipped')} for key in ('focused','postgresql','non_e2e')})
    result['validation']['focused']['followup_passed']=len(focused_failed)
    result['validation']['non_e2e']['resolved_initial_failures']=len(resolved)
    result['validation']['non_e2e']['known_baseline_failures']=len(known)
    result['validation']['non_e2e']['candidate_head']=historical_head
    result['validation']['non_e2e']['scope']='historical_full_suite_with_exact_targeted_reconciliation'
    check_public_result(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=derive_result(args.evidence)
    rendered=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.write_text(rendered,encoding='utf-8')
    print(rendered)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    main()
