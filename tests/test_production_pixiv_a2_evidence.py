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


def test_approved_single_property_negative_revision_retains_positive_controls():
    value=quality_fixture()[0]
    family={'family_id':'property','names':['blue_eyes','蓝眼睛'],'sample_expectation_revisions':[{
        'media_id':718,'previous_expected_ids':[718],'expected_ids':[],
        'approval':'specific owner ruling','source_evidence':'only unaccepted suggestion'}]}
    case={'category':'accepted_search_equivalence_only','accepted_family_id':'property',
        'samples':[{'media_id':i} for i in (714,715,718)],'passed':True}
    value['cases'].append(case)
    for i in (714,715,718):
        for name in family['names']:
            value['queries'][f'id:{i} '+json.dumps(name,ensure_ascii=False)]={'status_code':200,'ids':[] if i==718 else [i]}
    oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}],'search_only_families':[family]}
    assert recompute_quality(value,oracle,baseline=copy.deepcopy(value))['failed_cases']==0
    value['queries']['id:714 "blue_eyes"']['ids']=[]
    with pytest.raises(ValueError,match='summary_disagrees_with_raw'):recompute_quality(value,oracle)


def quality_fixture():
    return {'projection_rows':[['A','character',None,1,10,'work'],['B','character',None,1,11,'work']],
        'queries':{'"a"':{'ids':[10,11],'status_code':200},'"b"':{'ids':[10,11],'status_code':200}},
        'cases':[{'names':['a','b'],'expected':'must_link','category':'supported_multilingual_identity','passed':True}]},


def add_suggestion_controls(value,sample):
    for kind,query in [('suggested_negative',f'id:{sample["media_id"]} -"{sample["suggested_tag"]}"'),
        ('accepted_positive_control',f'id:{sample["media_id"]} "{sample["accepted_control_tag"]}"')]:
        value['cases'].append({'category':'suggestion_'+kind,'kind':kind,'media_id':sample['media_id'],
            'query':query,'expected_ids':[sample['media_id']],'actual_ids':[sample['media_id']],'total':1,'passed':True})
        value['queries'][query]={'status_code':200,'ids':[sample['media_id']],'total':1}


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
    add_suggestion_controls(value,sample)
    case={'category':'suggestion_suggested_positive','kind':'suggested_positive','media_id':20,
        'query':'id:20 "draft"','expected_ids':[],'actual_ids':[],'total':0,'passed':True}
    value['cases'].append(case)
    value['queries'][case['query']]={'status_code':200,'ids':[],'total':0}
    assert recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})['failed_cases']==0
    case.update(expected_ids=[20],actual_ids=[20],total=1)
    with pytest.raises(ValueError,match='frozen_suggestion_expectation_changed'):
        recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})


@pytest.mark.parametrize('change',['missing','http_failure','wrong_ids','wrong_total'])
def test_suggestion_summary_cannot_replace_actual_query_receipt(change):
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    sample={'media_id':20,'suggested_tag':'draft','accepted_control_tag':'kept'}
    add_suggestion_controls(value,sample)
    query='id:20 "draft"'
    value['cases'].append({'category':'suggestion_suggested_positive','kind':'suggested_positive','media_id':20,
        'query':query,'expected_ids':[],'actual_ids':[],'total':0,'passed':True})
    value['queries'][query]={'status_code':200,'ids':[],'total':0}
    if change=='missing':del value['queries'][query]
    elif change=='http_failure':value['queries'][query]['status_code']=500
    elif change=='wrong_ids':value['queries'][query]['ids']=[20]
    else:value['queries'][query]['total']=1
    with pytest.raises(ValueError):recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})


@pytest.mark.parametrize('change',['valid','missing','http_failure','wrong_ids','extra_ids','wrong_total'])
def test_creator_union_uses_actual_query_receipt(change):
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    family={'query':'ArtistTwin','expected_union_media_ids':[20,21],
        'creators':[{'provider_creator_id':'left','expected_media_ids':[20]},
                    {'provider_creator_id':'right','expected_media_ids':[21]}]}
    value['cases'].append({'category':'bare_name_distinct_creator_accounts','query':'ArtistTwin',
        'expected_account_union_media_ids':[20,21],'actual_api_media_ids':[20,21],'passed':True,
        'accounts':[{'provider_creator_id':name,'concept_ids':[mid],'bound_media_ids':[mid],'missing_bound_media_ids':[]}
            for name,mid in [('left',20),('right',21)]]})
    query='"ArtistTwin"';value['queries'][query]={'status_code':200,'ids':[20,21],'total':2}
    if change=='valid':assert recompute_quality(value,oracle,creator_oracle={'selected_families':[family]})['failed_cases']==0
    else:
        if change=='missing':del value['queries'][query]
        elif change=='http_failure':value['queries'][query]['status_code']=500
        elif change=='wrong_ids':value['queries'][query]['ids']=[20]
        elif change=='extra_ids':value['queries'][query].update(ids=[20,21,999],total=3)
        else:value['queries'][query]['total']=3
        with pytest.raises(ValueError):recompute_quality(value,oracle,creator_oracle={'selected_families':[family]})


def test_absent_former_identity_case_cannot_disappear_from_denominator():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    baseline=copy.deepcopy(value)
    value['cases']=[];value['projection_rows']=value['projection_rows'][:1]
    with pytest.raises(ValueError,match='quality_case_missing'):
        recompute_quality(value,oracle,baseline=baseline)


@pytest.mark.parametrize('case',[
    {'category':'accepted_search_equivalence_only','accepted_family_id':'family'},
    {'category':'media_set_AND','names':['x','y']},
    {'category':'media_set_negative','names':['x','y']},
    {'category':'suggestion_suggested_positive','kind':'suggested_positive','media_id':20},
    {'category':'bare_name_distinct_creator_accounts','query':'ArtistTwin','expected_account_union_media_ids':[20,21]},
])
def test_all_frozen_quality_categories_retain_their_denominator(case):
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    baseline=copy.deepcopy(value);baseline['cases'].append(case)
    with pytest.raises(ValueError,match='quality_case_missing'):
        recompute_quality(value,oracle,baseline=baseline)


def test_duplicate_quality_cases_cannot_pad_the_acceptance_count():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    value['cases']*=80
    with pytest.raises(ValueError,match='quality_case_duplicate'):
        recompute_quality(value,oracle)


def test_old_missing_media_cannot_disappear_with_changed_source_projection():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    baseline=copy.deepcopy(value)
    baseline['cases'][0].update(passed=False,missing_recall_media_ids=[[12],[]])
    with pytest.raises(ValueError,match='summary_disagrees_with_raw'):
        recompute_quality(value,oracle,baseline=baseline)
    for row in value['queries'].values():row['ids'].append(12)
    assert recompute_quality(value,oracle,baseline=baseline)['failed_cases']==0


@pytest.mark.parametrize('missing',['search_family','suggestion','creator'])
def test_independent_nonidentity_oracles_require_each_case_without_baseline(missing):
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    kwargs={}
    if missing=='search_family':oracle['search_only_families']=[{'family_id':'f','names':['a','b']}]
    elif missing=='suggestion':kwargs['suggestion_oracle']={'samples':[{'media_id':20}]}
    else:kwargs['creator_oracle']={'selected_families':[{'query':'ArtistTwin'}]}
    with pytest.raises(ValueError,match='quality_case_missing'):recompute_quality(value,oracle,**kwargs)


def workload_fixture():
    from urllib.parse import urlencode
    cases=[{'case_id':str(i),'category':'name','terms':[name]} for i,name in enumerate(('a','b'))]
    rows=[{**case,'query':json.dumps(case['terms'][0]),'status_code':200,'ms':1,
        'request_url':'http://127.0.0.1:8012/api/search?'+urlencode({'q':json.dumps(case['terms'][0]),'limit':64})} for case in cases]
    source=[{'case_id':case['case_id'],'repeat':repeat,'terms':case['terms'],
        'include_needs_review':False,'include_evidence_fallback':True,'ms':1}
        for case in cases for repeat in range(3)]
    return {'queries':rows,'source_layer_measurements':source},{'queries':copy.deepcopy(rows)},cases


@pytest.mark.parametrize('change',['none','repeated_fast_query','changed_terms','wrong_limit','source_duplicate','source_missing','source_terms','source_flags'])
def test_workload_covers_frozen_queries_parameters_and_source_repetitions(change):
    from scripts.production_pixiv_a2_evidence import recompute_workload
    value,baseline,cases=workload_fixture()
    if change=='none':
        assert recompute_workload(value,baseline,cases)==({'p50_ms':1,'p95_ms':1,'max_ms':1},)*2
        return
    if change=='repeated_fast_query':value['queries'][1]=copy.deepcopy(value['queries'][0])
    elif change=='changed_terms':value['queries'][1]['terms']=['a']
    elif change=='wrong_limit':value['queries'][1]['request_url']=value['queries'][1]['request_url'].replace('limit=64','limit=1')
    elif change=='source_duplicate':value['source_layer_measurements'][-1]=copy.deepcopy(value['source_layer_measurements'][0])
    elif change=='source_missing':value['source_layer_measurements'].pop()
    elif change=='source_terms':value['source_layer_measurements'][0]['terms']=['other']
    else:value['source_layer_measurements'][0]['include_needs_review']=True
    with pytest.raises(ValueError):recompute_workload(value,baseline,cases)


@pytest.mark.parametrize('change',['none','two_seconds','early','clock_backwards','altered_history','missing_timezone'])
def test_historical_spacing_exception_does_not_exempt_new_dispatches(change):
    import hashlib
    from scripts.production_pixiv_a2_evidence import verify_forward_metadata_spacing
    original=(json.dumps({'event':'dispatch','work_id':'old','at':'2026-09-11T00:00:00+00:00'})+'\n').encode()
    audit={'journal_sha256':hashlib.sha256(original).hexdigest(),'total_acquisition_commands':1}
    data=original
    times={'two_seconds':'2026-09-11T00:00:02+00:00','early':'2026-09-11T00:00:01.9+00:00',
        'clock_backwards':'2026-09-10T23:59:59+00:00','missing_timezone':'2026-09-11T00:00:03'}
    if change in times:data+=(json.dumps({'event':'dispatch','work_id':'new','at':times[change]})+'\n').encode()
    if change=='altered_history':data=data.replace(b'old',b'changed')
    if change in {'none','two_seconds'}:
        result=verify_forward_metadata_spacing(data,audit)
        assert result['forward_dispatch_count']==(change=='two_seconds')
        assert result['provider_internal_http_intervals_claimed'] is False
    else:
        with pytest.raises(ValueError):verify_forward_metadata_spacing(data,audit)


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
    oracle={'identity_pairs':[{'names':['a','b'],'expected':'cannot_link'}],
        'separation_controls':[{'names':['a','b'],'exclusive_media':{'a':[10],'b':[11]},
                               'source_evidence':'independent fixture: 10 only A; 11 only B; 12 both'}]}
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
    # The old gate also accepted this internally consistent but wrong union.
    for query,ids in {'"a" -"b"':[],'"b" -"a"':[],'"a" "b"':[10,11,12]}.items():
        value['queries'][query]['ids']=ids
    with pytest.raises(ValueError,match='summary_disagrees_with_raw'):
        recompute_quality(value,oracle)
    value['cases'][0]['passed']=False
    assert recompute_quality(value,oracle)['failed_cases']==1
    with pytest.raises(ValueError,match='independent_separation_controls_required'):
        recompute_quality(value,{'identity_pairs':oracle['identity_pairs']})


def test_browser_requires_loaded_fullscreen_and_actual_dom_sets():
    from scripts.production_pixiv_a2_evidence import verify_browser_actions
    browser={'actions':[],'search':{'ids':[1],'api_ids':[1]},'old_tag':{'dom_ids':[1],'api_ids':[1]},
        'source_chip':{'kind':'source_concept','param':'q','conceptIds':'1','href':'http://127.0.0.1/?q=x','navigated_url':'http://127.0.0.1/?q=x',
            'search':{'query':'x','request_url':'http://127.0.0.1/api/search?q=x','status_code':200,'dom_ids':[1],'api_ids':[1]}},
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


@pytest.mark.parametrize('root_value',[None,'','.'])
def test_launcher_cannot_infer_unrecorded_code_root_from_working_directory(tmp_path,monkeypatch,root_value):
    from scripts.production_pixiv_a2_evidence import verify_launcher_action
    monkeypatch.chdir(tmp_path)
    launch={'after_pid':123,'database':'prod',
        'normal_entry_invocation':{'executable':str(tmp_path/'V.I.O.L.E.T. Production Launcher.exe'),
            'arguments':[],'action':'Restart','sha256':'a'*64},
        'server_process_at_action':{'ProcessId':123,'ParentProcessId':45,'CreationDate':'time','CommandLine':'python run.py'},
        'profile_at_action':{'candidate_head':'b'*40,'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False,
            'database':'prod','sha256':'c'*64}}
    if root_value is not None:launch['profile_at_action']['code_root']=root_value
    with pytest.raises(ValueError,match='profile_observation_changed'):verify_launcher_action(launch,tmp_path,'b'*40)


def test_unrelated_passing_node_cannot_resolve_a_historical_failure(tmp_path):
    from scripts.check_production_pixiv_a2 import validation_evidence
    def write(name,value):
        (tmp_path/name).write_text(json.dumps(value) if isinstance(value,dict) else value,encoding='utf-8')
    passed='tests/test_current.py::test_unrelated';failed='tests/test_bug.py::test_bug'
    command={'argv':['python','-m','pytest'],'status':'finished','exit_code':0,'source_head':'current'}
    write('current-command.json',command);write('current.log',passed+' PASSED\n1 passed\n')
    write('history-command.json',{**command,'argv':['python','-m','pytest','tests','--ignore=tests/e2e','-q'],
        'exit_code':1,'source_head':'history'})
    write('history.log','FAILED '+failed+'\n1 passed, 1 failed\n')
    write('full-non-e2e-admission-private.json',{'source_head':'history','full_suite_invocation':1})
    gate={'command':'current-command.json','log':'current.log','passed':1,'failed':0,'skipped':0}
    record={'focused':gate,'postgresql':gate,'non_e2e':{**gate,'command':'history-command.json','log':'history.log','failed':1},
        'remediation':[gate],'node_mappings':{failed:{'nodes':[passed]}},'full_non_e2e_invocations':1}
    with pytest.raises(ValueError,match='unverified_remediation_node_mapping'):
        validation_evidence(tmp_path,record,'current')
