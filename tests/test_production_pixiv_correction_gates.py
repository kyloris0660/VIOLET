"""Regression coverage for correction lineage and independent release evidence."""
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path
import pytest
from scripts.production_pixiv_a2_evidence import recompute_quality,verify_browser_actions



@pytest.mark.parametrize('decision',['must_link','cannot_link'])
def test_previously_recalled_media_cannot_disappear_from_both_projection_and_api(decision):
    separated=decision=='cannot_link'
    value={'projection_rows':[['a','work',None,1,10,'w'],['a','work',None,1,12,'w'],
                             ['b','work',None,2 if separated else 1,11,'w']],
           'queries':{},'cases':[{'names':['a','b'],'expected':decision,'passed':True,
                                 'category':'required_separation' if separated else 'supported_multilingual_identity'}]}
    oracle={'identity_pairs':[{'names':['a','b'],'expected':decision}]}
    if separated:
        rows={'"a"':[10,12],'"b"':[11],'"a" -"b"':[10,12],'"b" -"a"':[11],'"a" "b"':[]}
        oracle['separation_controls']=[{'names':['a','b'],'exclusive_media':{'a':[10],'b':[11]},'source_evidence':'independent controls'}]
    else:rows={'"a"':[10,11,12],'"b"':[10,11,12]}
    value['queries']={query:{'status_code':200,'ids':ids} for query,ids in rows.items()}
    baseline=copy.deepcopy(value)
    assert recompute_quality(value,oracle,baseline=baseline)['failed_cases']==0
    value['projection_rows']=[r for r in value['projection_rows'] if r[4]!=12]
    for response in value['queries'].values():response['ids']=[mid for mid in response['ids'] if mid!=12]
    with pytest.raises(ValueError,match='summary_disagrees_with_raw'):
        recompute_quality(value,oracle,baseline=baseline)


@pytest.mark.parametrize('target',['/','/?q=','/wrong?q=x'])
def test_chip_without_a_real_gallery_query_is_rejected(target):
    from test_production_pixiv_a2_evidence import browser_fixture
    browser=browser_fixture()
    browser['source_chip'].update(href='http://127.0.0.1'+target,navigated_url='http://127.0.0.1'+target)
    with pytest.raises(ValueError,match='source_chip'):
        verify_browser_actions(browser)


@pytest.mark.parametrize('mutation',['omitted','tampered'])
def test_correction_history_is_required_and_verified(mutation,tmp_path,monkeypatch):
    from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
    from app.services.production_pixiv_pair_correction import plan_corrected_pairs
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    ledger=json.loads((tmp_path/'budget.json').read_text())
    old=copy.deepcopy(rows)
    if mutation=='omitted':old=[]
    else:old[0]['input_signal_summary']['left']['work_context_key']='invented-prior-context'
    changed=[replace(s,work_context_key='corrected-context') for s in signals]
    with pytest.raises(ValueError):plan_corrected_pairs(edges,changed,old,cfg,ledger)
    assert provider.calls==1


def test_repeated_corrections_keep_the_transitive_three_attempt_lifetime(tmp_path,monkeypatch):
    from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
    from app.services.production_pixiv_pair_correction import plan_corrected_pairs
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    for context in ('correction-one','correction-two'):
        signals=[replace(s,work_context_key=context) for s in signals]
        cfg,plan=plan_corrected_pairs(edges,signals,rows,cfg,json.loads((tmp_path/'budget.json').read_text()))
        assert plan['dispatchable_call_ceiling']==1
        rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    assert provider.calls==3
    signals=[replace(s,work_context_key='correction-three') for s in signals]
    _,plan=plan_corrected_pairs(edges,signals,rows,cfg,json.loads((tmp_path/'budget.json').read_text()))
    assert plan['dispatchable_call_ceiling']==0


def test_complete_unrelated_prior_cannot_reset_an_exhausted_target(tmp_path,monkeypatch):
    from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
    from app.services.production_pixiv_pair_correction import plan_corrected_pairs,read_correction_prior
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    for context in ('one','two'):
        signals=[replace(s,work_context_key=context) for s in signals]
        cfg,_=plan_corrected_pairs(edges,signals,rows,cfg,json.loads((tmp_path/'budget.json').read_text()))
        rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    changed=[replace(s,work_context_key='three') for s in signals]
    _,legitimate=plan_corrected_pairs(edges,changed,rows,cfg,json.loads((tmp_path/'budget.json').read_text()))
    assert legitimate['dispatchable_call_ceiling']==0
    other=[replace(s,signal_key='foreign:'+s.signal_key,work_context_key='unrelated') for s in signals]
    foreign_edges=[replace(e,left_signal_key='foreign:'+e.left_signal_key,right_signal_key='foreign:'+e.right_signal_key) for e in edges]
    foreign,_=service.run_bounded_llm_adjudication(foreign_edges,signals=other,config=replace(cfg,logical_predecessors={}))
    name=write_prior(tmp_path,foreign);verified,_=read_correction_prior(tmp_path,name)
    with pytest.raises(ValueError,match='correction_prior_source_occurrence_missing'):
        plan_corrected_pairs(edges,changed,verified,cfg,json.loads((tmp_path/'budget.json').read_text()))


@pytest.mark.parametrize('mutation',['none','aggregates','vocabulary','unrelated_role_identity','prior_bytes',
    'supersedes','plan_scope','base_roles','missing_provenance'])
def test_prior_binding_uses_the_actual_correction_input_and_plan(tmp_path,monkeypatch,mutation):
    import hashlib
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    from app.services.production_pixiv_corrections import signal_semantics
    from app.services.production_pixiv_role_extraction import ROLE_SCHEMA
    from app.services.production_pixiv_semantics import adapt_production_semantics,build_semantic_vocabulary
    from app.services import production_pixiv_service,production_pixiv_release_provenance
    from app.services.production_pixiv_pair_correction import bind_correction_prior,read_correction_prior
    from test_production_pixiv_role_coverage import context
    consumer=context(['Hero']);aggregates=[{'scope':'actual'}];vocabulary=build_semantic_vocabulary([])
    previous={'schema_version':ROLE_SCHEMA,'records':{}}
    previous_path=tmp_path/'previous-roles.json';previous_path.write_text(json.dumps(previous),encoding='utf-8')
    digest=hashlib.sha256(previous_path.read_bytes()).hexdigest()
    signal=adapt_production_semantics(consumer,vocabulary,previous).signals[0]
    request={'aggregate_fingerprint':'aggregate-12345678','raw_targets':['Hero'],
        'supersedes':{'Hero':signal_semantics(signal)},'conflict_evidence':['specific conflict'],'authorization':'fixture owner'}
    plan={'source_role_facts':previous_path.name,'source_role_facts_sha256':digest,
        'requests':[copy.deepcopy(request)],'scope_aggregates':['aggregate-12345678']}
    facts={**previous,'semantic_corrections':[request],'correction_provenance':{
        'prior_role_facts':previous_path.name,'prior_sha256':digest,'plan':'correction-plan.json'}}
    prior_name=write_prior(tmp_path,[{'left_signal_key':'a','right_signal_key':'b'}])
    manifest_path=tmp_path/'prior-semantic-manifest-private.json';manifest=json.loads(manifest_path.read_text())
    manifest['input_identity'].update({k:canonical_fingerprint(v) for k,v in (
        ('aggregates',aggregates),('vocabulary',vocabulary),('role_facts',previous))})
    if mutation=='unrelated_role_identity':manifest['input_identity']['role_facts']=canonical_fingerprint({'other':'roles'})
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8');_,prior=read_correction_prior(tmp_path,prior_name)
    replayed=[]
    # Scope/identity validation is isolated here; actual selection and raw
    # cache replay have separate integration coverage and the real full run.
    def replay(old_aggregates,old_vocabulary,old_facts,rows,old_manifest,private,**kwargs):
        assert old_aggregates==[{'scope':'actual'}] and old_vocabulary==build_semantic_vocabulary([])
        assert old_facts==previous and old_manifest==manifest
        replayed.append(True)
        return {'work_sources':{'selected_pair_count':1},'remaining_sources':{'selected_pair_count':0}}
    monkeypatch.setattr(production_pixiv_service,'production_consumer',lambda value:consumer)
    monkeypatch.setattr(production_pixiv_release_provenance,'_replay_source_selection',replay)
    if mutation=='aggregates':aggregates=[{'scope':'unrelated'}]
    elif mutation=='vocabulary':vocabulary={**vocabulary,'unrelated':True}
    elif mutation=='prior_bytes':previous_path.write_text('{}',encoding='utf-8')
    elif mutation=='supersedes':
        request['supersedes']['Hero']['role_hint']='work';plan['requests']=[copy.deepcopy(request)]
    elif mutation=='plan_scope':plan['scope_aggregates']=['foreign']
    elif mutation=='base_roles':facts['records']={'other':'changed'}
    elif mutation=='missing_provenance':del facts['correction_provenance']
    (tmp_path/'correction-plan.json').write_text(json.dumps(plan),encoding='utf-8')
    if mutation=='none':
        result=bind_correction_prior(aggregates,vocabulary,facts,prior,tmp_path)
        assert result['selected_pair_count']==1 and result['new_correction_aggregate_count']==1 and replayed==[True]
    else:
        with pytest.raises(ValueError,match='correction_prior_|semantic_correction_previous_fact_changed'):
            bind_correction_prior(aggregates,vocabulary,facts,prior,tmp_path)
        assert not replayed


def valid_preservation():
    import hashlib
    from scripts.production_pixiv_a2_evidence import PRESERVED_TABLES,PRESERVED_NONEMPTY
    tables={name:{'rows':1 if name in PRESERVED_NONEMPTY else 0,
        'sha256':'a'*64 if name in PRESERVED_NONEMPTY else hashlib.sha256(b'').hexdigest()} for name in PRESERVED_TABLES}
    return [{'tables':copy.deepcopy(tables),'candidate_head':'a'*40,'database':'copy_test',
        'system_identifier':'123','operation_id':'copy-final','checkpoint':checkpoint,
        'started_at':f'2026-09-18T0{index}:00:00+00:00','finished_at':f'2026-09-18T0{index}:00:01+00:00'}
        for index,checkpoint in enumerate(('before','after-rollback','after-reapply'))]


@pytest.mark.parametrize('mutation',['none','empty','missing','negative','boolean','bad_hash','fake_empty',
    'all_empty','old_head','wrong_database','wrong_system','wrong_operation','same_checkpoint','same_time','changed_rows'])
def test_preservation_requires_complete_bound_snapshots(mutation):
    from scripts.production_pixiv_a2_evidence import verify_preservation_snapshots
    snapshots=valid_preservation();row=snapshots[1];media=row['tables']['blombooru_media']
    if mutation=='empty':
        for item in snapshots:item['tables']={}
    elif mutation=='missing':del row['tables']['blombooru_albums']
    elif mutation=='negative':media['rows']=-1
    elif mutation=='boolean':media['rows']=True
    elif mutation=='bad_hash':media['sha256']='abc'
    elif mutation=='fake_empty':row['tables']['blombooru_albums']['sha256']='b'*64
    elif mutation=='all_empty':
        for item in snapshots:
            for table in item['tables'].values():table.update(rows=0,sha256=__import__('hashlib').sha256(b'').hexdigest())
    elif mutation=='old_head':row['candidate_head']='b'*40
    elif mutation=='wrong_database':row['database']='original'
    elif mutation=='wrong_system':row['system_identifier']='456'
    elif mutation=='wrong_operation':row['operation_id']='previous'
    elif mutation=='same_checkpoint':row['checkpoint']='before'
    elif mutation=='same_time':row['started_at']=snapshots[0]['started_at']
    elif mutation=='changed_rows':media['rows']+=1
    kwargs=dict(candidate='a'*40,database='copy_test',system_identifier='123',operation='copy-final')
    if mutation=='none':assert verify_preservation_snapshots(snapshots,**kwargs)['table_count']==17
    else:
        with pytest.raises(ValueError,match='a2_'):verify_preservation_snapshots(snapshots,**kwargs)


def write_prior(tmp_path,rows):
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    prefix='prior';name=prefix+'-judgments-private.json'
    processing={'selected_pair_count':len(rows),'judgment_count':len(rows),'error_count':0,'remaining_missing_pair_count':0}
    manifest={'input_identity':{'judgments':canonical_fingerprint(rows)},'processing':processing,
        'adjudication_receipt':'prior-processing.json','work_selection':'prior-work.json','remaining_selection':'prior-other.json'}
    for filename,value in ((name,rows),('prior-semantic-manifest-private.json',manifest),
        ('prior-processing.json',{**processing,'work_stage':processing,'remaining_stage':{'selected_pair_count':0}}),
        ('prior-work.json',rows),('prior-other.json',[])):
        (tmp_path/filename).write_text(json.dumps(value),encoding='utf-8')
    return name


@pytest.mark.parametrize('mutation',['none','removed','altered','selection_removed','processing_count','manifest_count','error'])
def test_complete_prior_binds_retained_selection_and_processing(tmp_path,mutation):
    from app.services.production_pixiv_pair_correction import read_correction_prior
    rows=[{'left_signal_key':'a','right_signal_key':'b'},{'left_signal_key':'a','right_signal_key':'c'}]
    name=write_prior(tmp_path,rows)
    filename=name
    if mutation=='removed':value=rows[:1]
    elif mutation=='altered':value=[{**rows[0],'right_signal_key':'d'},rows[1]]
    elif mutation=='selection_removed':filename='prior-work.json';value=rows[:1]
    elif mutation in ('processing_count','error'):
        filename='prior-processing.json';value=json.loads((tmp_path/filename).read_text());value['judgment_count' if mutation=='processing_count' else 'error_count']=1
    elif mutation=='manifest_count':
        filename='prior-semantic-manifest-private.json';value=json.loads((tmp_path/filename).read_text());value['processing']['selected_pair_count']=1
    if mutation!='none':(tmp_path/filename).write_text(json.dumps(value),encoding='utf-8')
    if mutation=='none':assert read_correction_prior(tmp_path,name)[0]==rows
    else:
        with pytest.raises(ValueError,match='correction_prior_'):read_correction_prior(tmp_path,name)


def test_corrected_release_cannot_omit_admission(tmp_path):
    from app.services.production_pixiv_release_provenance import verify_release_sources
    with pytest.raises(ValueError,match='correction_admission_required'):
        verify_release_sources([],{}, {'semantic_corrections':[{}]},[],{},tmp_path)


def test_pair_reservation_counts_disjoint_predecessors_as_one_lifetime(tmp_path):
    from app.services.source_concept_budget import AdjudicationBudget,AdjudicationBudgetBlocked
    budget=AdjudicationBudget(tmp_path/'budget.json',model='test',cap_usd=10,input_per_million=.4,output_per_million=1.6)
    keys=['decision-input:'+str(i) for i in range(3)]
    for key in keys:
        ticket=budget.reserve(key,[])
        budget.settle(ticket,{'prompt_tokens':1,'completion_tokens':1},success=False)
    with pytest.raises(AdjudicationBudgetBlocked,match='logical_attempts_exhausted'):
        budget.reserve('decision-input:corrected',[],logical_keys=keys)


@pytest.mark.parametrize('mutation',['none','missing_stage','changed_prior','changed_lineage','changed_snapshot','reset_attempt'])
def test_release_replays_correction_admission_after_paid_answer(tmp_path,monkeypatch,mutation):
    from test_production_pixiv_adjudication import config,MeteredProvider,_eligible_llm_edges,service
    from app.services.production_pixiv_pair_correction import read_correction_prior,plan_corrected_pairs,verify_correction_admission
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    signals,edges=_eligible_llm_edges(1);provider=MeteredProvider()
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:(provider,{}))
    cfg=replace(config(tmp_path),prompt_version=service.PRODUCTION_PAIR_PROMPT_VERSION)
    rows,_=service.run_bounded_llm_adjudication(edges,signals=signals,config=cfg)
    name=write_prior(tmp_path,rows);_,proof=read_correction_prior(tmp_path,name)
    before=json.loads((tmp_path/'budget.json').read_text())
    changed=[replace(s,work_context_key='corrected-work') for s in signals]
    configured,plan=plan_corrected_pairs(edges,changed,rows,cfg,before)
    history={'prior':proof,'stages':{'work':{'ledger':'before.json','ledger_fingerprint':canonical_fingerprint(before),'admission':'admission.json'}}}
    service.run_bounded_llm_adjudication(edges,signals=changed,config=configured)
    after=json.loads((tmp_path/'budget.json').read_text())
    if mutation=='missing_stage':history['stages']={}
    elif mutation=='changed_prior':history['prior']['judgments_sha256']='a'*64
    elif mutation=='changed_lineage':plan['logical_predecessors']={}
    elif mutation=='changed_snapshot':before['calls']=[]
    elif mutation=='reset_attempt':after['calls'][-1]['logical_keys']=[]
    for filename,value in [('before.json',before),('admission.json',plan)]:
        (tmp_path/filename).write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.setattr(service,'primary_openai_provider_from_settings',lambda:pytest.fail('offline release'))
    if mutation=='none':assert verify_correction_admission('work',edges,changed,cfg,after,history,tmp_path)['new_provider_calls']==0
    else:
        with pytest.raises(ValueError,match='correction_'):
            verify_correction_admission('work',edges,changed,cfg,after,history,tmp_path)
    assert provider.calls==2


def test_metadata_dispatch_symlink_cannot_read_outside_evidence_root(tmp_path):
    from scripts.check_production_pixiv_a2 import evidence_path
    private=tmp_path/'private';private.mkdir()
    external=tmp_path/'external.jsonl';external.write_text('{}\n',encoding='utf-8')
    link=private/'metadata-dispatch-private.jsonl'
    try:link.symlink_to(external)
    except OSError as exc:pytest.skip('symlink creation unavailable: '+str(exc.winerror))
    with pytest.raises(ValueError):evidence_path(private,link.name)
