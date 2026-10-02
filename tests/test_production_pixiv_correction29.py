import copy
from dataclasses import asdict,replace
import pytest

from app.services.production_pixiv_qualification import apply_identity_qualification
from app.services.production_pixiv_corrections import signal_semantics
from app.services.pixiv_metadata_projection_service import canonical_fingerprint
from app.services import source_concept_resolver_service as resolver
from test_phase45_scv2_r2_constraint_aware_graph_remediation import _signal


def source(key='class',role='character',value='Class(Work)'):
    return replace(_signal(key,value,role=role,work_context='work',provider='pixiv'),
        origin_type='pixiv_tag_observation',parenthetical_base=value.split('(')[0],parenthetical_context='Work',
        evidence_payload={'aggregate_fingerprint':'aggregate-'+key,'work_id':'123',
            'production_candidate_scope':'pixiv:work:123'})


def authorization(signal):
    return {'schema_version':'violet.production-identity-qualification.v1','decision_version':'fixture-v1',
        'authority':'test owner','decision':'suspend_identity_inference','targets':[{
            'signal_key':signal.signal_key,'aggregate_fingerprint':signal.evidence_payload['aggregate_fingerprint'],
            'original_input_fingerprint':canonical_fingerprint(asdict(signal)),
            'supersedes':signal_semantics(signal),'logical_target':'role-target:original',
            'reason':'unsupported unique identity'}]}


@pytest.mark.parametrize('role',['work','character','unknown'])
def test_suspended_source_is_not_context_anchor_candidate_or_union_but_is_accounted(role):
    target=source(role=role);authority=authorization(target)
    hero=source('hero',value='Hero(Work)');other=source('other',value='Other(Work)')
    suspended=apply_identity_qualification([target,hero,other],[target,hero,other],authority,authority=authority)
    assert suspended[0].role_hint==target.role_hint and suspended[0].status==target.status
    assert suspended[0].parenthetical_context==target.parenthetical_context
    assert suspended[1:]==(hero,other)
    assert resolver.signal_identity_anchor(suspended[0],context_by_scope={}) is None
    assert resolver._signal_blocking_keys(suspended[0],context_by_scope={},alias_component_by_key={})==set()
    assert resolver._context_candidates_by_scope(suspended[:1])=={}
    result=resolver.resolve_source_concepts(suspended,run_id='qualification-fixture')
    assert len(result.signals)==3
    assert all(link.signal_key!=target.signal_key for link in result.links)
    assert all(target.signal_key not in (e.left_signal_key,e.right_signal_key) for e in result.edge_candidates)
    assert result.rejected_signals[0]['negative_reason_code']=='identity_qualification_suspended'
    assert len({link.concept_key for link in result.links})==2


@pytest.mark.parametrize('change',['extra','missing','duplicate','aggregate','input','semantic','strong'])
def test_qualification_scope_and_semantics_fail_closed(change):
    target=source();authority=authorization(target);selection=copy.deepcopy(authority);signals=[target]
    if change=='extra':selection['targets'].append({**selection['targets'][0],'signal_key':'outside'})
    elif change=='missing':signals=[]
    elif change=='duplicate':authority['targets'].append(copy.deepcopy(authority['targets'][0]));selection=copy.deepcopy(authority)
    elif change=='aggregate':signals=[replace(target,evidence_payload={**target.evidence_payload,'aggregate_fingerprint':'outside'})]
    elif change=='input':signals=[replace(target,confidence=0.4)]
    elif change=='semantic':signals=[replace(target,role_hint='work')]
    else:signals=[replace(target,evidence_payload={**target.evidence_payload,'production_role_hint_evidence':[{'role':'character'}]})]
    with pytest.raises(ValueError,match='identity_qualification'):
        apply_identity_qualification(signals,signals,selection,authority=authority)


def test_qualification_cannot_override_independent_strong_role_even_with_matching_digest():
    target=source();target=replace(target,evidence_payload={**target.evidence_payload,
        'production_role_hint_evidence':[{'role':'character','source':'accepted_search_translation'}]})
    authority=authorization(target)
    with pytest.raises(ValueError,match='source_or_semantics_changed'):
        apply_identity_qualification([target],[target],authority,authority=authority)


def test_original_completion_reconstruction_ignores_later_qualification(tmp_path):
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_role_extraction import _original_completion_questions
    consumer,vocab,facts,_,_=partial_facts(tmp_path,['Known','Missing'])
    before=_original_completion_questions(consumer,vocab,facts)
    after=_original_completion_questions(consumer,vocab,{**facts,'identity_qualification':{'later_owner_decision':True}})
    assert before==after


def test_current_authority_and_handoff_cannot_reenable_metadata_dispatch():
    import json
    from pathlib import Path
    from scripts.production_pixiv_a2_state import validate,render
    from scripts.check_documentation_state import DocumentationStateError
    root=Path(__file__).resolve().parents[1]
    state=json.loads((root/'docs/state/current-phase.json').read_text(encoding='utf-8'))
    state['target_met']=False
    assert state['authorities']['metadata_only_provider'] is False
    validate(state,root)
    assert 'metadata仅回放' in render(state) and 'metadata获取' not in render(state)
    state['authorities']['metadata_only_provider']=True
    with pytest.raises(DocumentationStateError,match='authority_map'):
        validate(state,root)


@pytest.mark.parametrize('change',['none','missing_anchor','changed_call','empty_keys','wrong_keys'])
def test_legacy_absent_logical_keys_require_exact_anchored_call_and_original_request(tmp_path,monkeypatch,change):
    import json
    from test_production_pixiv_role_coverage import partial_facts
    from app.services import production_pixiv_release_provenance as p
    consumer,vocab,facts,provider,budget=partial_facts(tmp_path)
    ledger=json.loads(budget.path.read_text());call=ledger['calls'][0]
    call.pop('logical_keys');original=copy.deepcopy(call)
    anchored={} if change=='missing_anchor' else {call['id']:original}
    monkeypatch.setattr(p,'_anchored_legacy_role_calls',lambda *a:anchored)
    if change=='changed_call':call['charged_microusd']+=1
    elif change=='empty_keys':call['logical_keys']=[]
    elif change=='wrong_keys':call['logical_keys']=['role-target:changed']
    if change=='none':
        result=p.verify_role_response_sources(consumer,vocab,facts,tmp_path/'roles',ledger)
        assert result['anchored_legacy_logical_replay'][call['id']]['derived_logical_keys']
        assert ledger['calls'][0]==original and len(provider.calls)==1
    else:
        with pytest.raises(ValueError,match='logical_keys_changed'):
            p.verify_role_response_sources(consumer,vocab,facts,tmp_path/'roles',ledger)


def test_legacy_ledger_anchor_rejects_changed_bytes_and_count(tmp_path):
    import json,hashlib
    from app.services.production_pixiv_release_provenance import _anchored_legacy_role_calls
    (tmp_path/'roles').mkdir();p=tmp_path/'closeout43-budget-before-private.json'
    p.write_text(json.dumps({'calls':[{'id':'original','key':'role-extraction:fixed'}]}))
    authority={'ledger_before_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'call_count_before':1}
    assert _anchored_legacy_role_calls(tmp_path/'roles',authority=authority)=={'original':{'id':'original','key':'role-extraction:fixed'}}
    with pytest.raises(ValueError,match='count_changed'):
        _anchored_legacy_role_calls(tmp_path/'roles',authority={**authority,'call_count_before':2})
    p.write_text(p.read_text()+' ')
    with pytest.raises(ValueError,match='anchor_changed'):
        _anchored_legacy_role_calls(tmp_path/'roles',authority=authority)


def projection():
    run={'id':1,'run_key':'run','scope_key':'scope','source_mode':'production_scope','policy_version':'policy',
        'result_fingerprint':'result','input_fingerprint':'input','business_fingerprint':'business',
        'resolver_run_id':'resolver','resolver_version':'version'}
    return {'active_runs':1,'run_keys':['run'],'run_metadata':[run],'bindings':1,'binding_rows':[[1,1,2,3,4,1]],
        'bound_media_ids':[4],'source_record_ids':[3],'duplicate_support_count':0}


@pytest.mark.parametrize('field',['scope_key','policy_version','result_fingerprint'])
def test_live_metadata_drift_and_synchronized_attachment_are_rejected(field):
    from scripts.production_pixiv_a2_evidence import verify_final_projection
    before=projection();approved={k:v for k,v in before['run_metadata'][0].items() if k!='id'}
    assert verify_final_projection(before,copy.deepcopy(before),approved_run=approved)==before
    actual=copy.deepcopy(before);actual['run_metadata'][0][field]='wrong'
    with pytest.raises(ValueError,match='live_final_projection_changed'):verify_final_projection(before,actual,approved_run=approved)
    with pytest.raises(ValueError,match='not_approved_candidate'):verify_final_projection(actual,actual,approved_run=approved)


@pytest.mark.parametrize('change',['missing_field','missing_metadata','duplicate','extra','wrong_binding_run'])
def test_active_run_metadata_shape_and_binding_ownership(change):
    from scripts.production_pixiv_a2_evidence import verify_final_projection
    actual=projection()
    if change=='missing_field':actual['run_metadata'][0].pop('scope_key')
    elif change=='missing_metadata':actual.pop('run_metadata')
    elif change in ('duplicate','extra'):actual['run_metadata'].append({**actual['run_metadata'][0],'id':2 if change=='extra' else 1})
    else:actual['binding_rows'][0][1]=2
    with pytest.raises(ValueError,match='live_final_projection_changed'):verify_final_projection(actual,copy.deepcopy(actual))


def test_metadata_dispatch_is_blocked_even_if_historical_manifest_is_valid():
    from scripts.production_pixiv_metadata_entrypoint import assert_metadata_dispatch_authorized
    with pytest.raises(ValueError,match='replay_only'):assert_metadata_dispatch_authorized()


def git_fixture(tmp_path,ignore):
    import subprocess
    def git(*args):return subprocess.check_output(['git','-C',str(tmp_path),*args],text=True).strip()
    git('init','-q');git('config','user.name','fixture');git('config','user.email','fixture@example.invalid')
    git('config','core.autocrlf','true')  # Match Windows text writes without global config.
    (tmp_path/'.gitignore').write_text(ignore)
    (tmp_path/'module.py').write_text('VALUE=1\n')
    git('add','.');git('commit','-qm','baseline')
    return git,git('rev-parse','HEAD')


@pytest.mark.parametrize('path',['.env','sitecustomize.py','startup.pth','settings.toml','package.json'])
def test_candidate_rejects_ignored_behavior_inputs(tmp_path,path):
    from scripts.trusted_git import candidate_behavior_carry_forward
    git,head=git_fixture(tmp_path,path+'\n')
    assert candidate_behavior_carry_forward(tmp_path,head)
    (tmp_path/path).write_text('harmless fixture, never executed\n')
    assert not candidate_behavior_carry_forward(tmp_path,head)
    assert (tmp_path/path).is_file()


def test_generated_bytecode_is_allowed_but_forged_cached_code_is_not(tmp_path):
    import py_compile,marshal
    from pathlib import Path
    from scripts.trusted_git import candidate_behavior_carry_forward
    git,head=git_fixture(tmp_path,'__pycache__/\n')
    cached=Path(py_compile.compile(str(tmp_path/'module.py'),doraise=True))
    assert candidate_behavior_carry_forward(tmp_path,head)
    raw=cached.read_bytes();cached.write_bytes(raw[:16]+marshal.dumps(compile('VALUE=2',str(tmp_path/'module.py'),'exec')))
    assert not candidate_behavior_carry_forward(tmp_path,head)


def test_protected_evidence_exception_does_not_exempt_root_configuration(tmp_path):
    import json
    from scripts.trusted_git import candidate_behavior_carry_forward
    git,_=git_fixture(tmp_path,'.local_manifests/\n.env\n')
    registry=tmp_path/'docs/state/production-pixiv-a2-ignored-inputs.json';registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({'evidence_directories':['evidence'],'repository_files':{},'private_files':{}}))
    git('add','.');git('commit','-qm','approved evidence directory');head=git('rev-parse','HEAD')
    evidence=tmp_path/'.local_manifests/evidence';evidence.mkdir(parents=True)
    (evidence/'raw.json').write_text('{}')
    (evidence/'replay.py').write_text('# inert evidence helper')
    assert candidate_behavior_carry_forward(tmp_path,head)
    (tmp_path/'.env').write_text('HARMLESS_TEST=1')
    assert not candidate_behavior_carry_forward(tmp_path,head)


def test_launcher_pin_can_advance_to_verified_candidate_without_allowing_config_drift(tmp_path):
    import json,hashlib
    from scripts.trusted_git import candidate_behavior_carry_forward
    git,_=git_fixture(tmp_path,'.local_manifests/\n')
    profile=tmp_path/'.local_manifests/production_launcher/production-profile.json';profile.parent.mkdir(parents=True)
    values={'candidate_head':'a'*40,'app_port':12345,'safe_startup':True}
    profile.write_text(json.dumps(values))
    stable={k:v for k,v in values.items() if k!='candidate_head'}
    registry=tmp_path/'docs/state/production-pixiv-a2-ignored-inputs.json';registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({'candidate_profile':{'private_file':'production_launcher/production-profile.json',
        'previous_full_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
        'stable_fields_sha256':hashlib.sha256(json.dumps(stable,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}}))
    git('add','.');git('commit','-qm','approved configuration');head=git('rev-parse','HEAD')
    values['candidate_head']=head;profile.write_text(json.dumps(values))
    assert candidate_behavior_carry_forward(tmp_path,head)
    values['app_port']=23456;profile.write_text(json.dumps(values))
    assert not candidate_behavior_carry_forward(tmp_path,head)
    values['app_port']=12345;values['candidate_head']='b'*40;profile.write_text(json.dumps(values))
    assert not candidate_behavior_carry_forward(tmp_path,head)


@pytest.mark.parametrize('change',['none','missing_node','extra_node','xml_missing','command_subset','missing_phase','new_failure','duplicate_node','unfinished','source_changed','behavior_changed','admission_source','admission_authority','admission_time','admission_count'])
def test_current_full_suite_requires_real_complete_collection_and_xml(tmp_path,change):
    import sys,json,hashlib
    from scripts.production_pixiv_a2_full_suite import verify_current_full_suite
    node='tests/test_fixture.py::test_case'
    command={'argv':[sys.executable,'-m','pytest','tests','--ignore=tests/e2e','-q','-p',
        'scripts.production_pixiv_a2_pytest_inventory','--junitxml='+str(tmp_path/'suite.xml')],
        'source_head':'a'*40,'source_head_after':'a'*40,'behavior_guard_after':True,'cwd':str(tmp_path),'status':'finished','exit_code':0,
        'started_at':'2026-09-29T00:00:00+00:00','finished_at':'2026-09-29T00:00:10+00:00',
        'inventory':'nodes.json','authorization':'owner-20260929-full-non-e2e'}
    inventory={'status':'finished','started_at':'2026-09-29T00:00:01+00:00','finished_at':'2026-09-29T00:00:09+00:00',
        'exit_code':0,'collected_nodeids':[node],'collection_outcomes':[],
        'reports':[{'nodeid':node,'when':phase,'outcome':'passed','wasxfail':None} for phase in ('setup','call','teardown')]}
    xml='<testsuite><testcase name="test_case"><properties><property name="a2_nodeid" value="'+node+'"/></properties></testcase></testsuite>'
    log='1 passed';gate={'command':'command.json','log':'suite.log','xml':'suite.xml','inventory':'nodes.json','passed':1,'failed':0,'skipped':0}
    gate['admission']='correction29-full-non-e2e-admission-private.json'
    admission={key:command[key] for key in ('source_head','cwd','started_at','authorization')}
    admission['additional_invocation']=1
    if change=='missing_node':inventory['collected_nodeids']=[]
    elif change=='extra_node':inventory['collected_nodeids'].append('tests/test_fixture.py::test_missing')
    elif change=='xml_missing':xml='<testsuite><testcase name="test_case"/></testsuite>'
    elif change=='command_subset':command['argv'][3]='tests/test_fixture.py'
    elif change=='missing_phase':inventory['reports'].pop()
    elif change=='duplicate_node':inventory['collected_nodeids'].append(node)
    elif change=='unfinished':command['status']='running'
    elif change=='source_changed':command['source_head_after']='b'*40
    elif change=='behavior_changed':command['behavior_guard_after']=False
    elif change=='admission_source':admission['source_head']='b'*40
    elif change=='admission_authority':admission['authorization']='different_owner_decision'
    elif change=='admission_time':admission['started_at']='2026-09-28T00:00:00+00:00'
    elif change=='admission_count':admission['additional_invocation']=2
    elif change=='new_failure':
        log='FAILED '+node+'\n1 failed';xml=xml.replace('</properties>','</properties><failure message="new failure"/>')
        gate.update(passed=0,failed=1);command['exit_code']=inventory['exit_code']=1
        inventory['reports'][1]['outcome']='failed'
    inventory['collected_sha256']=hashlib.sha256(json.dumps(inventory['collected_nodeids'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    journal=''.join(json.dumps(row)+'\n' for row in inventory['reports']).encode()
    (tmp_path/'reports.jsonl').write_bytes(journal)
    inventory.update(report_journal='reports.jsonl',report_journal_sha256=hashlib.sha256(journal).hexdigest())
    for name,value in [('command.json',command),('nodes.json',inventory),(gate['admission'],admission)]:
        (tmp_path/name).write_text(json.dumps(value))
    (tmp_path/'suite.xml').write_text(xml);(tmp_path/'suite.log').write_text(log)
    if change=='none':assert verify_current_full_suite(tmp_path,gate,candidate='a'*40,root=tmp_path)['collected_node_count']==1
    else:
        with pytest.raises(ValueError):verify_current_full_suite(tmp_path,gate,candidate='a'*40,root=tmp_path)
