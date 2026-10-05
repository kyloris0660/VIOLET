import copy,json,sys
from pathlib import Path
from urllib.parse import urlencode
import pytest
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'backend'),str(root/'tests')]
from test_production_pixiv_a2_evidence import browser_fixture,quality_fixture,workload_fixture,workload_launch_fixture
from scripts.production_pixiv_a2_service_evidence import verify_service_observation,verify_browser_service,verify_quality_service

def observation():
    return {key:value for key,value in workload_fixture()[0].items() if key not in {'queries','source_layer_measurements'}}

def browser():
    base=workload_launch_fixture()['base_url']
    value=json.loads(json.dumps(browser_fixture()).replace('http://127.0.0.1/',base+'/'))
    value.update(observation())
    for action in value['actions']:
        if 'image' in action:action['image'].update(rendered_width=700,rendered_height=900,decoded=True,intersects_viewport=True)
    value['pages']=[{'url':base+'/media/1','images':[{'src':base+'/api/media/1/file'}]}]
    value['search'].update(url=base+'/?q=a',request_url=base+'/api/search?q=a')
    value['old_tag'].update(query='1girl',request_url=base+'/api/search?q=1girl')
    value['recovery_page']['url']=base+'/admin#dynamic-library-sync-section'
    return value

def quality():
    value=quality_fixture()[0];value.update(observation())
    value['collections']={'core':dict(observation(),collection_id='core')}
    for query,row in value['queries'].items():
        row.update(total=2,method='GET',collection_id='core',pages=[{'page':1,'status_code':200,'total':2,'ids':[10,11],
            'request_url':value['base_url']+'/api/search?'+urlencode({'q':query,'limit':256,'page':1})}])
    return value

@pytest.mark.parametrize('factory,verify',[(browser,verify_browser_service),(quality,verify_quality_service)])
@pytest.mark.parametrize('change',['valid','pid','head','database','root','after','port','external_base','missing_after'])
def test_service_binding(factory,verify,change):
    value=factory();launch=workload_launch_fixture()
    if change=='valid':assert verify(value,launch)['same_candidate_service'];return
    if change=='pid':value['server_identity']['pid']=999
    elif change=='head':value['server_identity']['git_sha']='b'*7
    elif change=='database':value['database']='other'
    elif change=='root':value['server_identity']['code_root']=str(root/'other')
    elif change=='after':value['server_identity_after']['pid']=999
    elif change=='port':value['server_identity']['port']=8999
    elif change=='external_base':value['base_url']='https://elsewhere.invalid'
    else:del value['server_identity_after']
    with pytest.raises(ValueError):verify(value,launch)

@pytest.mark.parametrize('change',['image','page','fullscreen','chip','search','old_tag','suggestion','recovery'])
def test_browser_urls_cannot_use_another_service(change):
    value=browser();outside='http://127.0.0.1:8999'
    if change=='image':value['pages'][0]['images'][0]['src']=outside+'/api/media/1/file'
    elif change=='page':value['pages'][0]['url']=outside+'/media/1'
    elif change=='fullscreen':value['actions'][1]['image']['src']=outside+'/api/media/1/file'
    elif change=='chip':value['source_chip']['href']=outside+'/?q=x'
    elif change=='suggestion':value['suggestion_display']['request_url']=outside+'/api/media/20'
    elif change=='recovery':value['recovery_page']['request_url']=outside+'/api/admin/dynamic-library-sync/recovery-items'
    else:value[change]['request_url']=outside+'/api/search?q=a'
    with pytest.raises(ValueError):verify_browser_service(value,workload_launch_fixture())

@pytest.mark.parametrize('change',['foreign_collection','missing_collection','unknown_collection','duplicate_page','old_origin','wrong_query','wrong_page','wrong_ids','duplicate_ids','changed_total','no_pages'])
def test_quality_pages_are_bound_to_the_actual_collection(change):
    value=quality();row=value['queries']['"a"'];page=row['pages'][0]
    if change=='foreign_collection':value['collections']['core']['server_identity_after']['pid']=999
    elif change=='missing_collection':value['collections']={}
    elif change=='unknown_collection':row['collection_id']='missing'
    elif change=='duplicate_page':row['pages'].append(copy.deepcopy(page))
    elif change=='old_origin':page['request_url']=page['request_url'].replace(':8012',':8999')
    elif change=='wrong_query':page['request_url']=page['request_url'].replace('%22a%22','other')
    elif change=='wrong_page':page['page']=2
    elif change=='wrong_ids':page['ids']=[10,12]
    elif change=='duplicate_ids':page['ids']=[10,10]
    elif change=='changed_total':page['total']=3
    else:row['pages']=[]
    with pytest.raises(ValueError):verify_quality_service(value,workload_launch_fixture())

def test_quality_multiple_collections_and_empty_query_are_valid():
    value=quality();value['collections']['suggestion']=dict(observation(),collection_id='suggestion')
    query='id:20 "draft"'
    value['queries'][query]={'status_code':200,'ids':[],'total':0,'method':'GET','collection_id':'suggestion',
        'pages':[{'status_code':200,'ids':[],'total':0,'page':1,'request_url':value['base_url']+'/api/search?'+urlencode({'q':query,'limit':256,'page':1})}]}
    assert verify_quality_service(value,workload_launch_fixture())['collections']==2

@pytest.mark.parametrize('field,value',[('decoded',False),('rendered_width',0),('rendered_height',0),('intersects_viewport',False),
    ('rendered_width',float('nan')),('rendered_height',float('inf')),('rendered_width',True)])
def test_browser_natural_dimensions_do_not_prove_visible_pixels(field,value):
    evidence=browser();evidence['actions'][1]['image'][field]=value
    with pytest.raises(ValueError,match='original_not_rendered'):verify_browser_service(evidence,workload_launch_fixture())

@pytest.mark.parametrize('kind',['browser','quality'])
def test_release_entry_helpers_enforce_the_service_binding(kind):
    from scripts.production_pixiv_a2_evidence import verify_browser_actions,recompute_quality
    value=browser() if kind=='browser' else quality()
    def verify():
        if kind=='browser':return verify_browser_actions(value,launch=workload_launch_fixture())
        return recompute_quality(value,{'identity_pairs':[{'names':['a','b'],'expected':'must_link'}],
            'identity_precision_controls':[{'names':['a','b'],'forbidden_media_ids':[999],'source_evidence':'independent unrelated fixture'}]},launch=workload_launch_fixture())
    verify()
    value['server_identity']['pid']=999
    with pytest.raises(ValueError,match='candidate_service_changed'):verify()

import copy,json,sys
from pathlib import Path
import pytest
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'backend'),str(root/'tests')]
from test_production_pixiv_review60 import state_repo
from scripts.production_pixiv_a2_state import validate
from scripts.check_documentation_state import DocumentationStateError

def test_public_summary_without_private_contract_cannot_complete(tmp_path):
    repo,state,_=state_repo(tmp_path)
    with pytest.raises(DocumentationStateError,match='result_private_contract'):validate(state,repo)

@pytest.mark.parametrize('change',['matching','contract_failed','summary_mismatch','private_is_file'])
def test_state_uses_fixed_private_contract_and_exact_derived_result(tmp_path,monkeypatch,change):
    repo,state,_=state_repo(tmp_path);private=repo/'.local_manifests/pixiv-a2';private.parent.mkdir()
    if change=='private_is_file':private.write_text('not evidence')
    else:private.mkdir()
    value=json.loads((repo/state['result_path']).read_text(encoding='utf-8'));calls=[]
    def derive(actual_private,actual_repo):
        calls.append((actual_private,actual_repo))
        assert actual_private==private.resolve() and actual_repo==repo
        if change=='contract_failed':raise ValueError('actual gate failed')
        result=copy.deepcopy(value)
        if change=='summary_mismatch':result['budget']['charged_or_reserved_usd']=2
        return result
    monkeypatch.setattr('scripts.check_production_pixiv_a2.derive_result',derive)
    if change=='matching':
        validate(state,repo);assert calls==[(private.resolve(),repo)]
    else:
        with pytest.raises(DocumentationStateError,match='result_'):validate(state,repo)
        assert len(calls)==(0 if change=='private_is_file' else 1)
