import copy
import json
import pytest
from scripts.production_pixiv_a2_evidence import pytest_outcome,latency_statistics,recompute_quality


@pytest.mark.parametrize('field',['log','xml'])
@pytest.mark.parametrize('absolute',[False,True])
def test_validation_rejects_external_log_or_xml_before_parsing(tmp_path,field,absolute):
    from scripts.check_production_pixiv_a2 import validation_evidence
    private=tmp_path/'private';private.mkdir()
    external=tmp_path/'external';external.write_text('1 passed',encoding='utf-8')
    (private/'command.json').write_text(json.dumps({'argv':['python','-m','pytest'],
        'status':'finished','source_head':'candidate'}),encoding='utf-8')
    (private/'inside.log').write_text('1 passed',encoding='utf-8')
    gate={'command':'command.json','log':'inside.log','xml':'inside.xml','passed':1,'failed':0,'skipped':0}
    gate[field]=str(external) if absolute else '../external'
    with pytest.raises(ValueError,match='private_evidence_location'):
        validation_evidence(private,{'focused':gate},'candidate')


@pytest.mark.parametrize('log,code', [
    ('ERROR tests/test_a.py::test_a\n1 passed, 1 error',1),
    ('1 passed',2),('INTERNALERROR interrupted\n1 passed',3),
    ('1 passed, 1 failed',1),
])
def test_pytest_errors_nonzero_exit_and_unaccounted_nodes_fail(log,code):
    with pytest.raises(ValueError):pytest_outcome({'status':'finished','exit_code':code},log)


def test_named_historical_failure_is_accounted_without_demanding_exit_zero():
    counts,nodes=pytest_outcome({'status':'finished','exit_code':1},'FAILED tests/a.py::test_old\n4 passed, 1 failed, 2 skipped')
    assert nodes=={'tests/a.py::test_old'} and counts=={'passed':4,'failed':1,'skipped':2,'errors':0}


def test_xml_teardown_error_cannot_hide_behind_success_summary(tmp_path):
    path=tmp_path/'tests.xml'
    path.write_text('<testsuite><testcase name="test"><error message="teardown"/></testcase></testsuite>')
    with pytest.raises(ValueError,match='xml_log_count_mismatch'):
        pytest_outcome({'status':'finished','exit_code':0},'1 passed',path)


def test_parametrized_error_text_is_not_a_pytest_outcome(tmp_path):
    path=tmp_path/'tests.xml';path.write_text('<testsuite><testcase name="sample"/></testsuite>')
    log='tests/test_guard.py::test_error[1 passed, 1 error] PASSED\n=== 1 passed in 0.01s ==='
    counts,failures=pytest_outcome({'status':'finished','exit_code':0},log,path)
    assert counts=={'passed':1,'failed':0,'skipped':0,'errors':0} and not failures


def test_latency_recomputed_from_each_measurement_and_rejects_nonfinite():
    stats=latency_statistics([{'ms':v} for v in range(100)])
    assert stats=={'p50_ms':49.5,'p95_ms':95,'max_ms':99}
    with pytest.raises(ValueError):latency_statistics([{'ms':float('nan')}])


def quality_fixture():
    return {'projection_rows':[['A','character',None,1,10,'work'],['B','character',None,1,11,'work']],
        'queries':{'"a"':{'ids':[10,11],'status_code':200},'"b"':{'ids':[10,11],'status_code':200}},
        'cases':[{'names':['a','b'],'expected':'must_link','category':'supported_multilingual_identity','passed':True}]},


def test_frozen_quality_is_recomputed_even_if_boolean_stays_true():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    assert recompute_quality(value,oracle)['failed_cases']==0
    for mutate in (lambda v:v['queries']['"b"'].update(ids=[11]),
                   lambda v:v['projection_rows'][1].__setitem__(3,2),
                   lambda v:v['cases'][0].update(expected='cannot_link')):
        changed=copy.deepcopy(value);mutate(changed)
        with pytest.raises(ValueError):recompute_quality(changed,oracle)


def test_suggestion_expected_set_cannot_be_rewritten_with_the_observed_result():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    sample={'media_id':20,'suggested_tag':'draft','accepted_control_tag':'kept'}
    case={'category':'suggestion_suggested_positive','kind':'suggested_positive','media_id':20,
        'query':'id:20 "draft"','expected_ids':[],'actual_ids':[],'total':0,'passed':True}
    value['cases'].append(case)
    assert recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})['failed_cases']==0
    case.update(expected_ids=[20],actual_ids=[20],total=1)
    with pytest.raises(ValueError,match='frozen_suggestion_expectation_changed'):
        recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})


def test_absent_former_identity_case_cannot_disappear_from_denominator():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    baseline=copy.deepcopy(value)
    value['cases']=[];value['projection_rows']=value['projection_rows'][:1]
    with pytest.raises(ValueError,match='quality_case_missing'):
        recompute_quality(value,oracle,baseline=baseline)


def test_every_search_equivalence_sample_must_recall_its_actual_media():
    value=quality_fixture()[0]
    oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}],
        'search_only_families':[{'family_id':'family','names':['alias-a','alias-b']}]}
    for mid in (10,11):
        for name in ('alias-a','alias-b'):
            value['queries'][f'id:{mid} "{name}"']={'status_code':200,'ids':[mid]}
    value['cases'].append({'category':'accepted_search_equivalence_only','accepted_family_id':'family',
        'samples':[{'media_id':10},{'media_id':11}],'passed':True})
    assert recompute_quality(value,oracle)['failed_cases']==0
    for name in ('alias-a','alias-b'):
        value['queries'][f'id:11 "{name}"']['ids']=[]
    with pytest.raises(ValueError,match='summary_disagrees_with_raw'):
        recompute_quality(value,oracle)


def test_separation_checks_query_behavior_and_allows_legitimate_cooccurrence():
    value=quality_fixture()[0]
    value['projection_rows'][1][3]=2
    value['cases'][0].update(expected='cannot_link',category='required_separation')
    oracle={'identity_pairs':[{'names':['a','b'],'expected':'cannot_link'}]}
    for query,ids in {'"a"':[10,12],'"b"':[11,12],'"a" -"b"':[10],
        '"b" -"a"':[11],'"a" "b"':[12]}.items():
        value['queries'][query]={'status_code':200,'ids':ids}
    assert recompute_quality(value,oracle)['failed_cases']==0
    # Separate concepts and own-side recall alone cannot hide a broad HTTP
    # union regression: actual exclusion requests expose the inconsistency.
    value['queries']['"a"']['ids']=[10,11,12]
    value['queries']['"b"']['ids']=[10,11,12]
    with pytest.raises(ValueError,match='summary_disagrees_with_raw'):
        recompute_quality(value,oracle)


def test_browser_requires_loaded_fullscreen_and_actual_dom_sets():
    from scripts.production_pixiv_a2_evidence import verify_browser_actions
    browser={'actions':[],'search':{'ids':[1],'api_ids':[1]},'old_tag':{'dom_ids':[1],'api_ids':[1]},
        'source_chip':{'kind':'source_concept','param':'q','conceptIds':'1','href':'http://127.0.0.1/?q=x','navigated_url':'http://127.0.0.1/?q=x'},
        'recovery_page':{'status':200,'method':'GET','mutation_performed':False,'text':'rows',
            'request_url':'http://127.0.0.1/api/admin/dynamic-library-sync/recovery-items?root_id=2'}}
    for mid in (1,2,3):
        browser['actions'] += [{'action':'open_fullscreen','media_id':mid,'overlay_active':True,
            'image':{'src':f'http://127.0.0.1/api/media/{mid}/file','width':900,'height':700}}]
        browser['actions'] += [{'action':kind,'media_id':mid} for kind in ('thumbnail_to_detail','close_fullscreen','return_gallery')]
    assert verify_browser_actions(browser)['fullscreen_samples']==3
    browser['actions'][0]['image']['width']=0
    with pytest.raises(ValueError,match='fullscreen_original_not_loaded'):verify_browser_actions(browser)


def test_launcher_uses_recorded_process_and_profile_not_historical_pid_liveness(tmp_path):
    from scripts.production_pixiv_a2_evidence import verify_launcher_action
    launch={'after_pid':123,'database':'prod',
        'normal_entry_invocation':{'executable':str(tmp_path/'V.I.O.L.E.T. Production Launcher.exe'),
            'arguments':[],'action':'Restart','sha256':'a'*64},
        'server_process_at_action':{'ProcessId':123,'ParentProcessId':45,'CreationDate':'observed-time','CommandLine':'python run.py'},
        'profile_at_action':{'candidate_head':'b'*40,'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False,
            'database':'prod','code_root':str(tmp_path),'sha256':'c'*64}}
    assert verify_launcher_action(launch,tmp_path,'b'*40)
    launch['profile_at_action']['candidate_head']='old'
    with pytest.raises(ValueError,match='profile_observation_changed'):verify_launcher_action(launch,tmp_path,'b'*40)
