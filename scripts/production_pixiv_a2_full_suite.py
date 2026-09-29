"""Verify actual full collection/command/JUnit, retaining the first run."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


def verify_current_full_suite(private,gate,*,candidate,root):
    from scripts.check_production_pixiv_a2 import read,evidence_path,require,HISTORICAL_NODE
    from scripts.production_pixiv_a2_evidence import pytest_outcome
    import sys
    command=read(private,gate['command']);log=evidence_path(private,gate['log']).read_text(encoding='utf-8')
    xml=evidence_path(private,gate['xml']);inventory=read(private,gate['inventory'])
    require(inventory.get('status')=='finished','current_suite_inventory_unfinished')
    journal=evidence_path(private,inventory['report_journal']).read_bytes()
    require(hashlib.sha256(journal).hexdigest()==inventory['report_journal_sha256']
        and [json.loads(line) for line in journal.decode('utf-8').splitlines()]==inventory['reports'],
        'current_suite_raw_reports_changed')
    argv=command['argv']
    require(command.get('status')=='finished' and command.get('source_head_after')==candidate
        and command.get('behavior_guard_after') is True,'current_suite_finished_source')
    require(command['source_head']==candidate and Path(command['cwd']).resolve()==root.resolve()
        and Path(argv[0]).resolve()==Path(sys.executable).resolve(),'current_suite_source_runtime')
    require(argv[1:-1]==['-m','pytest','tests','--ignore=tests/e2e','-q','-p','scripts.production_pixiv_a2_pytest_inventory']
        and argv[-1].startswith('--junitxml=') and Path(argv[-1].split('=',1)[1]).resolve()==xml,
        'current_full_suite_command')
    require(command.get('inventory')==gate['inventory'] and command.get('authorization')=='owner-20260929-full-non-e2e',
        'current_suite_authorization')
    require(gate.get('admission')=='correction29-full-non-e2e-admission-private.json','current_suite_admission_location')
    admission=read(private,gate['admission'])
    require(admission.get('source_head')==candidate and admission.get('cwd')==command['cwd']
        and admission.get('started_at')==command['started_at']
        and admission.get('authorization')==command['authorization']
        and type(admission.get('additional_invocation')) is int and admission['additional_invocation']==1,
        'current_suite_admission_identity')
    for name in ('started_at','finished_at'):
        require(datetime.fromisoformat(command[name]).tzinfo is not None,'current_suite_time')
    require(datetime.fromisoformat(command['started_at'])<=datetime.fromisoformat(inventory['started_at'])
        <datetime.fromisoformat(inventory['finished_at'])<=datetime.fromisoformat(command['finished_at']),
        'current_suite_time_order')
    counts,failures=pytest_outcome(command,log,xml)
    nodes=inventory['collected_nodeids']
    require(isinstance(nodes,list) and nodes and len(nodes)==len(set(nodes)),'current_suite_duplicate_or_empty_collection')
    require(inventory['collected_sha256']==hashlib.sha256(json.dumps(nodes,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'current_suite_collection_digest')
    require(inventory['exit_code']==command['exit_code'],'current_suite_exit')
    reports=inventory['reports'];by_node={node:[] for node in nodes}
    for row in reports:
        require(row['nodeid'] in by_node,'current_suite_extra_node')
        by_node[row['nodeid']].append(row)
    for rows in by_node.values():
        phases=[r['when'] for r in rows]
        require(phases in (['setup','call','teardown'],['setup','teardown']), 'current_suite_incomplete_node')
        require(phases!=['setup','teardown'] or rows[0]['outcome']=='skipped','current_suite_setup_failure')
        require(not any(r['wasxfail'] for r in rows),'current_suite_unaccounted_xfail')
    cases=list(ElementTree.parse(xml).getroot().iter('testcase'));xml_nodes=[];collection_skips=[];failures_by_node={}
    for case in cases:
        identities=[p.get('value') for p in case.findall('./properties/property') if p.get('name')=='a2_nodeid']
        if not identities:
            require(case.find('skipped') is not None,'current_suite_xml_node_missing')
            collection_skips.append(case)
        else:
            require(len(identities)==1,'current_suite_xml_node_duplicate');xml_nodes.extend(identities)
            node=identities[0];require(node in by_node,'current_suite_xml_extra_node')
            expected=('failed' if any(r['outcome']=='failed' for r in by_node[node]) else
                'skipped' if any(r['outcome']=='skipped' for r in by_node[node]) else 'passed')
            outcome=('failed' if case.find('failure') is not None or case.find('error') is not None else
                'skipped' if case.find('skipped') is not None else 'passed')
            require(outcome==expected,'current_suite_xml_outcome_mismatch')
            if case.find('failure') is not None:failures_by_node[node]=ElementTree.tostring(case,encoding='unicode')
    require(Counter(xml_nodes)==Counter(nodes),'current_suite_xml_collection_mismatch')
    collection=inventory['collection_outcomes']
    require(all(row['outcome']=='skipped' for row in collection) and len(collection_skips)==len(collection),
        'current_suite_collection_outcome')
    known={HISTORICAL_NODE}&failures
    if known:require('missing_original_ai_execution_evidence' in failures_by_node.get(HISTORICAL_NODE,''),
        'current_suite_historical_reason')
    require(not failures-known,'current_suite_new_failure')
    require(all(counts[k]==gate[k] for k in ('passed','failed','skipped')),'current_suite_counts')
    return {**counts,'source_head':candidate,'collected_node_count':len(nodes),
        'collected_sha256':inventory['collected_sha256'],'known_historical_failures':len(known)}


def verify_full_suite_history(private,record):
    from scripts.check_production_pixiv_a2 import read,evidence_path,require
    history=record.get('full_suite_history',[])
    require(len(history)==2 and record.get('full_non_e2e_invocations')==len(history),'full_suite_history_count')
    require([r.get('authorization') for r in history]==['original-full-non-e2e','owner-20260929-full-non-e2e'],
        'full_suite_history_authorization')
    commands=[]
    for row,gate in zip(history,[record['non_e2e'],record['current_non_e2e']]):
        require(row['command']==gate['command'] and row['log']==gate['log'],'full_suite_history_identity')
        for key in ('command','log'):
            require(hashlib.sha256(evidence_path(private,row[key]).read_bytes()).hexdigest()==row[key+'_sha256'],
                'full_suite_history_digest')
        commands.append(read(private,row['command']))
    require(commands[0]['source_head']=='acc28adfb106ebafcfa0034607936e6da975af80'
        and commands[0]['source_head']!=commands[1]['source_head'],'full_suite_history_source')
    require(datetime.fromisoformat(commands[0]['finished_at'])<datetime.fromisoformat(commands[1]['started_at']),
        'full_suite_history_time')
