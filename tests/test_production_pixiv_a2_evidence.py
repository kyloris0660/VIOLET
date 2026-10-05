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
    value['projection_rows'] += [['ArtistTwin','artist',None,mid,mid,'w'] for mid in (20,21)]
    value['creator_projection_rows']=[{'provider':'pixiv','provider_creator_id':name,'concept_id':mid,'media_id':mid}
        for name,mid in [('left',20),('right',21)]]
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
    launch=workload_launch_fixture()
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    import sys
    for row in source:
        row.update(include_production_alias_evidence=True,started_perf_ns=1000000,finished_perf_ns=2000000,
            ids=[],result_fingerprint=canonical_fingerprint([]),execution={'candidate_head':launch['candidate_head'],
                'database':launch['database'],'system_identifier':'system','code_root':launch['code_root'],
                'python_executable':sys.executable,'pid':123})
    identity={'pid':launch['identity_pid'],'port':8012,'db_name':launch['database'],
        'git_sha':launch['candidate_head'][:7],'code_root':launch['code_root']}
    return {'queries':rows,'source_layer_measurements':source,'candidate_head':launch['candidate_head'],
        'database':launch['database'],'base_url':launch['base_url'],'server_identity':identity,
        'server_identity_after':copy.deepcopy(identity)},{'queries':copy.deepcopy(rows)},cases


def workload_launch_fixture():
    from pathlib import Path
    return {'candidate_head':'a'*40,'identity_pid':123,'database':'copy_test','base_url':'http://127.0.0.1:8012',
        'code_root':str(Path(__file__).resolve().parent)}


@pytest.mark.parametrize('change',['none','repeated_fast_query','changed_terms','wrong_limit','source_duplicate','source_missing','source_terms','source_flags'])
def test_workload_covers_frozen_queries_parameters_and_source_repetitions(change):
    from scripts.production_pixiv_a2_evidence import recompute_workload
    value,baseline,cases=workload_fixture()
    if change=='none':
        assert recompute_workload(value,baseline,cases,launch=workload_launch_fixture())==({'p50_ms':1,'p95_ms':1,'max_ms':1},)*2
        return
    if change=='repeated_fast_query':value['queries'][1]=copy.deepcopy(value['queries'][0])
    elif change=='changed_terms':value['queries'][1]['terms']=['a']
    elif change=='wrong_limit':value['queries'][1]['request_url']=value['queries'][1]['request_url'].replace('limit=64','limit=1')
    elif change=='source_duplicate':value['source_layer_measurements'][-1]=copy.deepcopy(value['source_layer_measurements'][0])
    elif change=='source_missing':value['source_layer_measurements'].pop()
    elif change=='source_terms':value['source_layer_measurements'][0]['terms']=['other']
    else:value['source_layer_measurements'][0]['include_needs_review']=True
    with pytest.raises(ValueError):recompute_workload(value,baseline,cases,launch=workload_launch_fixture())


@pytest.mark.parametrize('change',['external','relative','wrong_port','mixed_origin','wrong_pid','wrong_head',
    'wrong_database','changed_after','wrong_root','base_mismatch'])
def test_workload_is_bound_to_the_same_observed_candidate_service(change):
    from scripts.production_pixiv_a2_evidence import recompute_workload
    value,baseline,cases=workload_fixture();launch=workload_launch_fixture()
    if change in {'external','relative','wrong_port','mixed_origin'}:
        replacement={'external':'https://external.invalid','relative':'','wrong_port':'http://127.0.0.1:8999',
            'mixed_origin':'http://localhost:8012'}[change]
        for row in (value['queries'][:1] if change=='mixed_origin' else value['queries']):
            row['request_url']=row['request_url'].replace(launch['base_url'],replacement)
    elif change=='wrong_pid':value['server_identity']['pid']=124
    elif change=='wrong_head':value['candidate_head']='b'*40
    elif change=='wrong_database':value['database']='other'
    elif change=='changed_after':value['server_identity_after']['pid']=124
    elif change=='wrong_root':value['server_identity']['code_root']=str(__import__('pathlib').Path(launch['code_root']).parent)
    else:value['base_url']='http://127.0.0.1:8999'
    with pytest.raises(ValueError,match='a2_workload_'):
        recompute_workload(value,baseline,cases,launch=launch)


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


def browser_fixture():
    browser={'attempt_id':'fresh-browser-attempt','actions':[],'search':{'ids':[1],'api_ids':[1]},'old_tag':{'dom_ids':[1],'api_ids':[1]},
        'source_chip':{'kind':'source_concept','param':'q','conceptIds':'1','href':'http://127.0.0.1/?q=x','navigated_url':'http://127.0.0.1/?q=x',
            'search':{'query':'x','request_url':'http://127.0.0.1/api/search?q=x','status_code':200,'dom_ids':[1],'api_ids':[1]}},
        'recovery_page':{'status':200,'method':'GET','mutation_performed':False,'text':'rows',
            'request_url':'http://127.0.0.1/api/admin/dynamic-library-sync/recovery-items?root_id=2'}}
    for mid in (1,2,3):
        browser['actions'].append({'action':'thumbnail_to_detail','media_id':mid})
        browser['actions'] += [{'action':'open_fullscreen','media_id':mid,'overlay_active':True,
            'image':{'src':f'http://127.0.0.1/api/media/{mid}/file','width':900,'height':700}}]
        browser['actions'] += [{'action':kind,'media_id':mid} for kind in ('close_fullscreen','return_gallery')]
    for row in browser['actions']:row.update(attempt_id=browser['attempt_id'],flow_id='flow-'+str(row['media_id']))
    browser['old_tag'].update(url='http://127.0.0.1/?q=1girl',query='1girl',request_url='http://127.0.0.1/api/search?q=1girl',attempt_id=browser['attempt_id'])
    browser['suggestion_display']={'media_id':20,'attempt_id':browser['attempt_id'],'url':'http://127.0.0.1/media/20',
        'request_url':'http://127.0.0.1/api/media/20','status_code':200,'api_media_id':20,'mutation_performed':False,'tag':'draft',
        'observed_items':[{'id':90,'media_id':20,'text':'draft','title':'suggestion','tag_name':'SPAN','href':None,'classes':'border-dashed','visible':True}],
        'api_items':[{'id':90,'name':'draft','is_suggestion':True}]}
    chip=browser['source_chip']
    chip.update(media_id=3,attempt_id=browser['attempt_id'],display_name='x',name_text='x',value='x',
        detail={'media_id':3,'api_media_id':3,'status_code':200,'url':'http://127.0.0.1/media/3',
                'request_url':'http://127.0.0.1/api/source-assertions/media/3','attempt_id':browser['attempt_id'],
                'source_concepts':[{'concept_id':1,'display_name':'x','search_value':'x','status':'active',
                    'evidence_items':[{'id':42,'media_scope':'current_media'}],
                    'local_media_support':[{'media_id':3,'source_metadata_record_id':10}]}]})
    chip['search'].update(api_ids=[1,3],dom_ids=[1,3],source_concept_expansions=[{'concept_id':1}],
        all_api_ids=[1,3],total=2,pages=[{'page':1,'limit':64,'request_url':'http://127.0.0.1/api/search?q=x&page=1&limit=64',
            'status_code':200,'total':2,'ids':[1,3]}])
    bind_api_body(chip)
    return browser


def test_browser_requires_loaded_fullscreen_and_actual_dom_sets():
    from scripts.production_pixiv_a2_evidence import verify_browser_actions
    browser=browser_fixture()
    assert verify_browser_actions(browser)['fullscreen_samples']==3
    browser['actions'][1]['image']['width']=0
    with pytest.raises(ValueError,match='fullscreen_original_not_loaded'):verify_browser_actions(browser)


@pytest.mark.parametrize('mutation',['reversed','separate_attempts','separate_flows','missing_attempt','close_before_open'])
def test_browser_requires_complete_ordered_navigation_in_one_attempt(mutation):
    from scripts.production_pixiv_a2_evidence import verify_browser_actions
    browser=browser_fixture()
    if mutation=='reversed':browser['actions'].reverse()
    elif mutation=='missing_attempt':del browser['attempt_id']
    elif mutation=='close_before_open':browser['actions'][1],browser['actions'][2]=browser['actions'][2],browser['actions'][1]
    else:
        field='attempt_id' if mutation=='separate_attempts' else 'flow_id'
        for index,row in enumerate(browser['actions']):row[field]=str(index)
    with pytest.raises(ValueError,match='a2_browser_attempt|a2_media_navigation_order'):
        verify_browser_actions(browser)


def test_launcher_uses_recorded_process_and_profile_not_historical_pid_liveness(tmp_path,monkeypatch):
    from scripts.production_pixiv_a2_evidence import verify_launcher_action
    launch={'after_pid':123,'database':'prod',
        'normal_entry_invocation':{'executable':str(tmp_path/'V.I.O.L.E.T. Production Launcher.exe'),
            'arguments':[],'action':'Restart','sha256':'a'*64},
        'server_process_at_action':{'ProcessId':123,'ParentProcessId':45,'CreationDate':'observed-time','CommandLine':'python run.py'},
        'profile_at_action':{'candidate_head':'b'*40,'pixiv_product_enabled':True,'pixiv_product_apply_enabled':False,
            'database':'prod','code_root':str(tmp_path),'sha256':'c'*64}}
    from test_production_pixiv_review68 import bind_launcher_fixture
    bind_launcher_fixture(launch,tmp_path,monkeypatch)
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
    import sys
    from scripts.check_production_pixiv_a2 import ROOT,validation_evidence
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
    required=json.loads((ROOT/'docs/state/production-pixiv-a2-required-tests.json').read_text())
    for label in ('focused','postgresql'):
        xml=tmp_path/(label+'.xml')
        xml.write_text('<testsuites><testsuite><testcase name="test_unrelated"/></testsuite></testsuites>')
        write(label+'-command.json',{**command,'cwd':str(ROOT),
            'argv':[sys.executable,'-m','pytest',*required[label],'-q','--junitxml='+str(xml)]})
        record[label]={**gate,'command':label+'-command.json','xml':xml.name}
    with pytest.raises(ValueError,match='unverified_remediation_node_mapping'):
        validation_evidence(tmp_path,record,'current')


from urllib.parse import urlencode
from scripts.production_pixiv_a2_evidence import verify_source_chip_concept_binding
import hashlib

def bind_api_body(chip):
    detail = chip['detail']
    body = json.dumps({'media_id': detail['api_media_id'], 'source_concepts': detail['source_concepts']}, ensure_ascii=False)
    detail.update(body_text=body, body_sha256=hashlib.sha256(body.encode('utf-8')).hexdigest())

def chip_browser(value='x', total_ids=(1, 3), limit=64):
    browser = browser_fixture()
    chip = browser['source_chip']
    token = value.strip()
    import re
    query = '"' + token.replace('"', '') + '"' if re.search(r'^-|[\s:"*?\[\]()]', token) else token
    chip.update(media_id=3, attempt_id=browser['attempt_id'], display_name=value, name_text=value, value=value,
                href='http://127.0.0.1/?' + urlencode({'q': query}),
                navigated_url='http://127.0.0.1/?' + urlencode({'q': query}),
                detail={'media_id':3, 'api_media_id':3, 'status_code':200, 'url':'http://127.0.0.1/media/3',
                        'request_url':'http://127.0.0.1/api/source-assertions/media/3', 'attempt_id':browser['attempt_id'],
                        'source_concepts':[{'concept_id':1, 'display_name':value, 'search_value':value, 'status':'active',
                                            'evidence_items':[{'id':42, 'media_scope':'current_media'}],
                                            'local_media_support':[{'media_id':3, 'source_metadata_record_id':10}]}]})
    pages = [{'page':number + 1, 'limit':limit, 'status_code':200, 'total':len(total_ids),
              'ids':list(total_ids[start:start+limit]),
              'request_url':'http://127.0.0.1/api/search?' + urlencode({'q':query, 'page':number+1, 'limit':limit})}
             for number, start in enumerate(range(0, len(total_ids), limit))]
    chip['search'].update(query=query, request_url=pages[0]['request_url'], status_code=200,
                          api_ids=pages[0]['ids'], dom_ids=pages[0]['ids'],
                          source_concept_expansions=[{'concept_id':1, 'display_name':value}],
                          all_api_ids=sorted(total_ids), total=len(total_ids), pages=pages)
    bind_api_body(chip)
    return browser

@pytest.mark.parametrize('value', ['x', 'two names', ' x ', '-leading', 'a"b', '名前(作品)'])
def test_real_frontend_token_rules_remain_accepted(value):
    from scripts.production_pixiv_a2_evidence import verify_browser_actions
    assert verify_browser_actions(chip_browser(value))['source_chip_concept_binding']['media_id'] == 3

def test_complete_second_page_contains_clicked_media():
    result = verify_source_chip_concept_binding(chip_browser(total_ids=(1, 2, 3), limit=2))
    assert result['complete_result_count'] == 3 and result['page_count'] == 2

def test_normalized_group_keeps_all_actual_concept_ids():
    browser = chip_browser()
    chip = browser['source_chip']
    second = copy.deepcopy(chip['detail']['source_concepts'][0])
    second.update(concept_id=2, display_name='ｘ')
    chip['detail']['source_concepts'].append(second)
    chip['conceptIds'] = '1,2'
    bind_api_body(chip)
    assert verify_source_chip_concept_binding(browser)['concept_ids'] == [1, 2]

@pytest.mark.parametrize('change', [
    'missing_detail', 'wrong_media', 'wrong_api_media', 'wrong_api_origin', 'wrong_attempt',
    'wrong_id', 'duplicate_ids', 'wrong_visible_name', 'wrong_api_name', 'disabled_search',
    'empty_evidence', 'wrong_evidence_scope', 'missing_local_support', 'wrong_local_support',
    'wrong_expansion', 'wrong_self_consistent_query', 'missing_pages', 'missing_last_page',
    'duplicate_media', 'detail_media_absent', 'wrong_page_query', 'wrong_total',
    'wrong_first_page', 'changed_body', 'missing_body', 'changed_api_concept_attachment',
])
def test_chip_binding_rejects_each_independent_evidence_gap(change):
    browser = chip_browser(total_ids=(1, 2, 3), limit=2)
    chip = browser['source_chip']
    detail = chip['detail']
    search = chip['search']
    concept = detail['source_concepts'][0]
    if change == 'missing_detail': chip.pop('detail')
    elif change == 'wrong_media': detail['media_id'] = 99
    elif change == 'wrong_api_media': detail['api_media_id'] = 99
    elif change == 'wrong_api_origin': detail['request_url'] = 'http://elsewhere/api/source-assertions/media/3'
    elif change == 'wrong_attempt': detail['attempt_id'] = 'other'
    elif change == 'wrong_id': chip['conceptIds'] = '2'
    elif change == 'duplicate_ids': chip['conceptIds'] = '1,1'
    elif change == 'wrong_visible_name': chip['name_text'] = 'unrelated'
    elif change == 'wrong_api_name': concept['display_name'] = 'unrelated'; bind_api_body(chip)
    elif change == 'disabled_search': concept['search_value'] = None; bind_api_body(chip)
    elif change == 'empty_evidence': concept['evidence_items'] = []; bind_api_body(chip)
    elif change == 'wrong_evidence_scope': concept['evidence_items'][0]['media_scope'] = 'linked_media'; bind_api_body(chip)
    elif change == 'missing_local_support': concept.pop('local_media_support'); bind_api_body(chip)
    elif change == 'wrong_local_support': concept['local_media_support'][0]['media_id'] = 99; bind_api_body(chip)
    elif change == 'wrong_expansion': search['source_concept_expansions'] = [{'concept_id':99}]
    elif change == 'wrong_self_consistent_query':
        chip['href'] = chip['navigated_url'] = 'http://127.0.0.1/?q=unrelated'
        search.update(query='unrelated', request_url='http://127.0.0.1/api/search?q=unrelated')
        for page in search['pages']: page['request_url'] = page['request_url'].replace('q=x', 'q=unrelated')
    elif change == 'missing_pages': search.pop('pages')
    elif change == 'missing_last_page': search['pages'].pop()
    elif change == 'duplicate_media': search['pages'][1]['ids'] = [2]
    elif change == 'detail_media_absent': search['pages'][1]['ids'] = [4]; search['all_api_ids'] = [1, 2, 4]
    elif change == 'wrong_page_query': search['pages'][1]['request_url'] = search['pages'][1]['request_url'].replace('q=x', 'q=other')
    elif change == 'wrong_total': search['pages'][1]['total'] = 9
    elif change == 'wrong_first_page': search['api_ids'] = [99]
    elif change == 'changed_body': detail['body_text'] += ' '
    elif change == 'missing_body': detail.pop('body_text')
    else: concept['search_value'] = 'unrecorded'
    with pytest.raises(ValueError, match='source_chip'):
        verify_source_chip_concept_binding(browser)


def fixed_workload_fixture(tmp_path):
    from test_production_pixiv_runtime_snapshot import bound_runtime
    root, head, production, git, runtime, binding = bound_runtime(tmp_path)
    value, baseline, cases = workload_fixture()
    launch = workload_launch_fixture()
    launch.update(candidate_head=head, code_root=str(runtime), fixed_runtime_binding=binding)
    value["candidate_head"] = head
    for identity in (value["server_identity"], value["server_identity_after"]):
        identity.update(git_sha=binding["deployment_head"][:7], code_root=str(runtime))
    for row in value["source_layer_measurements"]:
        row["execution"].update(candidate_head=head, code_root=str(root))
    return value, baseline, cases, launch, root, production, git, runtime


@pytest.mark.parametrize("change", [
    "none", "source_root_foreign", "source_root_runtime", "source_root_relative",
    "source_head_runtime", "source_head_other", "source_database", "source_system",
    "source_python_missing", "source_pid_zero", "source_pid_bool", "runtime_root_business",
    "runtime_head_business", "binding_business_head", "binding_missing",
    "runtime_source_drift", "business_source_drift", "original_profile_drift", "runtime_profile_drift",
])
def test_workload_source_sampler_uses_exact_native_proved_business_root(tmp_path, change):
    from scripts.production_pixiv_a2_evidence import recompute_workload
    value, baseline, cases, launch, root, production, git, runtime = fixed_workload_fixture(tmp_path)
    execution = value["source_layer_measurements"][0]["execution"]
    if change == "source_root_foreign": execution["code_root"] = str(tmp_path)
    elif change == "source_root_runtime": execution["code_root"] = str(runtime)
    elif change == "source_root_relative": execution["code_root"] = root.name
    elif change == "source_head_runtime": execution["candidate_head"] = launch["fixed_runtime_binding"]["deployment_head"]
    elif change == "source_head_other": execution["candidate_head"] = "b" * 40
    elif change == "source_database": execution["database"] = "another_test"
    elif change == "source_system": execution["system_identifier"] = "different-system"
    elif change == "source_python_missing": execution["python_executable"] = ""
    elif change == "source_pid_zero": execution["pid"] = 0
    elif change == "source_pid_bool": execution["pid"] = True
    elif change == "runtime_root_business":
        for identity in (value["server_identity"], value["server_identity_after"]): identity["code_root"] = str(root)
    elif change == "runtime_head_business":
        for identity in (value["server_identity"], value["server_identity_after"]): identity["git_sha"] = value["candidate_head"][:7]
    elif change == "binding_business_head": launch["fixed_runtime_binding"]["business_source_head"] = "b" * 40
    elif change == "binding_missing": del launch["fixed_runtime_binding"]
    elif change == "runtime_source_drift": (runtime / "run.py").write_bytes(b"print('unreviewed runtime')\n")
    elif change == "business_source_drift": (root / "run.py").write_bytes(b"print('unreviewed business')\n")
    elif change == "original_profile_drift":
        profile = json.loads(production.read_bytes()); profile["db"]["name"] = "different_database"
        production.write_text(json.dumps(profile), encoding="utf-8")
    elif change == "runtime_profile_drift":
        from scripts.production_pixiv_runtime_snapshot import PROFILE
        profile_path = runtime / PROFILE
        profile = json.loads(profile_path.read_bytes()); profile["pixiv_product_apply_enabled"] = True
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
    if change == "none":
        assert recompute_workload(value, baseline, cases, launch=launch, system_identifier="system") == ({"p50_ms": 1, "p95_ms": 1, "max_ms": 1},) * 2
    else:
        with pytest.raises(ValueError, match="a2_workload_"):
            recompute_workload(value, baseline, cases, launch=launch, system_identifier="system")
