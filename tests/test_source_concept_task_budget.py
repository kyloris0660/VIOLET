import pytest
from app.services.source_concept_budget import AdjudicationBudget, AdjudicationBudgetBlocked


def ledger(tmp_path,cap=0.002):
    return AdjudicationBudget(tmp_path/'budget.json',model='gpt-4.1-mini',cap_usd=cap,input_per_million=0.4,output_per_million=1.6)


@pytest.mark.parametrize('change',[
    {'charged_microusd':0}, {'charged_microusd':-1}, {'charged_microusd':True},
    {'usage':None}, {'usage':{'prompt_tokens':-1,'completion_tokens':50}},
    {'usage':{'prompt_tokens':'100','completion_tokens':50}},
    {'usage_known':False,'usage':None,'charged_microusd':0},
    {'status':'reserved','charged_microusd':0},
])
def test_saved_debit_is_rederived_before_all_admission_and_recovery(tmp_path,change):
    import json
    book=ledger(tmp_path,30)
    ticket=book.reserve('paid',[])
    book.settle(ticket,{'prompt_tokens':100,'completion_tokens':50},success=True)
    state=json.loads(book.path.read_text());state['calls'][0].update(change)
    book.path.write_text(json.dumps(state));before=book.path.read_bytes()
    for operation in (book.summary,lambda:book.reserve('new',[]),
                      lambda:book.recover_response(key='paid',reservation=ticket,usage={},business_valid=True)):
        with pytest.raises(AdjudicationBudgetBlocked):operation()
    assert book.path.read_bytes()==before


@pytest.mark.parametrize('usage',[
    {'prompt_tokens':-1,'completion_tokens':2}, {'prompt_tokens':True,'completion_tokens':2},
    {'prompt_tokens':1}, {'prompt_tokens':1.0,'completion_tokens':2},
])
def test_invalid_usage_cannot_release_reservation(tmp_path,usage):
    book=ledger(tmp_path);ticket=book.reserve('new',[]);before=book.path.read_bytes()
    with pytest.raises(AdjudicationBudgetBlocked,match='usage_invalid'):
        book.settle(ticket,usage,success=True)
    assert book.path.read_bytes()==before and book.summary()['charged_or_reserved_usd']>0


def test_restart_retains_unknown_call_and_prevents_duplicate(tmp_path):
    book=ledger(tmp_path)
    reservation=book.reserve('pair-a',[{'role':'user','content':'hello'}])
    resumed=ledger(tmp_path)
    with pytest.raises(AdjudicationBudgetBlocked,match='outcome_unknown'):
        resumed.reserve('pair-a',[])
    with pytest.raises(AdjudicationBudgetBlocked,match='budget_exhausted'):
        resumed.reserve('pair-b',[])
    resumed.settle(reservation,{},success=False)
    assert resumed.summary()['unknown_usage_count']==1
    assert resumed.summary()['charged_or_reserved_usd']>0


def test_usage_releases_reserve_and_budget_cannot_reset(tmp_path):
    book=ledger(tmp_path)
    ticket=book.reserve('a',[])
    book.settle(ticket,{'prompt_tokens':100,'completion_tokens':50},success=True)
    assert ledger(tmp_path).summary()['charged_or_reserved_usd']==0.00012
    with pytest.raises(AdjudicationBudgetBlocked,match='identity_changed'):
        ledger(tmp_path,cap=10).reserve('b',[])
    with pytest.raises(AdjudicationBudgetBlocked,match='success_requires_cache_recovery'):
        ledger(tmp_path).reserve('a',[])
def test_parallel_admission_respects_one_shared_reservation_cap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.services.source_concept_budget import AdjudicationBudget,AdjudicationBudgetBlocked
    budget=AdjudicationBudget(tmp_path/'parallel.json',model='test',cap_usd=0.015,input_per_million=0.4,output_per_million=1.6)
    barrier=Barrier(2)
    def reserve(index):
        barrier.wait(timeout=5)
        try:return budget.reserve(str(index),[{'role':'user','content':'x'*100}],max_output_tokens=6000)
        except AdjudicationBudgetBlocked:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
    assert len([row for row in results if row])==1
    assert budget.summary()['charged_or_reserved_usd']<=0.015


def test_explicit_cap_amendment_preserves_calls_and_is_idempotent(tmp_path):
    import json
    original=ledger(tmp_path,10)
    ticket=original.reserve('old-paid',[])
    original.settle(ticket,{'prompt_tokens':100,'completion_tokens':50},success=True)
    before=json.loads(original.path.read_text())
    expanded=ledger(tmp_path,30)
    with pytest.raises(AdjudicationBudgetBlocked,match='identity_changed'):expanded.summary()
    receipt=expanded.increase_cap(previous_cap_usd=10,authorization_source='task43',authorization_id='owner43')
    after=json.loads(original.path.read_text())
    assert after['calls']==before['calls'] and receipt['charged_before_microusd']==120
    assert receipt['call_count_before']==1 and after['cap_microusd']==30000000
    assert expanded.increase_cap(previous_cap_usd=10,authorization_source='task43',authorization_id='owner43')==receipt
    assert len(json.loads(original.path.read_text())['cap_amendments'])==1
    with pytest.raises(AdjudicationBudgetBlocked,match='identity_changed'):original.reserve('old-worker',[])
    with pytest.raises(ValueError,match='thirty_dollars'):ledger(tmp_path,30.01)


def test_failed_atomic_amendment_leaves_original_budget_bytes(tmp_path,monkeypatch):
    import app.services.source_concept_budget as module
    book=ledger(tmp_path,10);ticket=book.reserve('before',[]);book.settle(ticket,{},success=False)
    old=book.path.read_bytes();expanded=ledger(tmp_path,30)
    def interrupted(*args):raise OSError('simulated before replacement')
    monkeypatch.setattr(module.os,'replace',interrupted)
    with pytest.raises(OSError):expanded.increase_cap(previous_cap_usd=10,authorization_source='task43',authorization_id='owner43')
    assert book.path.read_bytes()==old and book.summary()['call_count']==1


@pytest.mark.parametrize('usage',[{}, {'prompt_tokens':100,'completion_tokens':50}])
def test_saved_response_recovery_is_idempotent_and_charged_once(tmp_path,usage):
    book=ledger(tmp_path,10);ticket=book.reserve('request',[])
    assert book.recover_response(key='request',reservation=ticket,usage=usage,business_valid=True)
    charged=book.summary()['charged_or_reserved_usd']
    assert not book.recover_response(key='request',reservation=ticket,usage=usage,business_valid=True)
    assert book.summary()['call_count']==1 and book.summary()['charged_or_reserved_usd']==charged>0
    assert not book.settle(ticket,usage,success=True)
    with pytest.raises(ValueError,match='outcome_changed'):book.settle(ticket,usage,success=False)


def test_paid_invalid_answer_can_retry_but_not_reset_attempts_with_batch_key(tmp_path):
    book=ledger(tmp_path,10);ticket=book.reserve('batch1',[],logical_keys=['target-a'])
    book.settle(ticket,{},success=True)
    charged=book.summary()['charged_or_reserved_usd']
    book.recover_response(key='batch1',reservation=ticket,usage={},business_valid=False)
    assert book.summary()['charged_or_reserved_usd']==charged
    for key in ('batch2','batch3'):
        ticket=book.reserve(key,[],logical_keys=['target-a']);book.settle(ticket,{},success=False)
    with pytest.raises(AdjudicationBudgetBlocked,match='logical_attempts_exhausted'):
        book.reserve('renamed-run-batch4',[],logical_keys=['target-a'])
    assert book.summary()['call_count']==3


def test_response_before_save_stays_reserved_and_legacy_cache_needs_no_invented_call(tmp_path):
    book=ledger(tmp_path,10);ticket=book.reserve('lost-response',[])
    with pytest.raises(AdjudicationBudgetBlocked,match='outcome_unknown'):book.reserve('lost-response',[])
    assert not book.recover_response(key='accepted-historical-cache',usage={},business_valid=True)
    with pytest.raises(AdjudicationBudgetBlocked,match='key_mismatch'):
        book.recover_response(key='wrong',reservation=ticket,usage={},business_valid=True)
    with pytest.raises(AdjudicationBudgetBlocked,match='inflight_call'):
        ledger(tmp_path,30).increase_cap(previous_cap_usd=10,authorization_source='task43',authorization_id='owner43')


def test_prompt_revision_counts_legacy_pair_attempts_without_reset(tmp_path):
    book=ledger(tmp_path,10);logical='decision-input:original-source-question'
    for _ in range(2):
        ticket=book.reserve(logical,[]);book.settle(ticket,{'prompt_tokens':10,'completion_tokens':10},success=False)
    revised=logical+':prompt:revised-instructions'
    ticket=book.reserve(revised,[],logical_keys=[logical]);book.settle(ticket,{},success=False)
    with pytest.raises(AdjudicationBudgetBlocked,match='logical_attempts_exhausted'):
        book.reserve('another-version-or-run',[],logical_keys=[logical])
    assert book.summary()['call_count']==3


def test_prompt_revision_cannot_overlap_unsettled_legacy_question(tmp_path):
    book=ledger(tmp_path,10);logical='decision-input:original-source-question'
    book.reserve(logical,[])
    with pytest.raises(AdjudicationBudgetBlocked,match='logical_call_outcome_unknown'):
        book.reserve(logical+':prompt:revised',[],logical_keys=[logical])
    assert book.summary()['call_count']==1


def windows_replace_denied(code=5):
    error=PermissionError('simulated Windows atomic replacement denial')
    error.winerror=code
    return error


@pytest.mark.parametrize('winerror',[5,32,33])
@pytest.mark.parametrize('stage',['reserve','settle'])
def test_transient_windows_replace_retries_same_state_without_duplicate_call(tmp_path,monkeypatch,stage,winerror):
    import json,time
    from app.services import source_concept_budget as module
    book=ledger(tmp_path,10);old=book.reserve('old',[]);book.settle(old,{},success=False)
    ticket=book.reserve('new',[]) if stage=='settle' else None
    before=json.loads(book.path.read_text());replace=module.os.replace;attempts=[];waits=[]
    def transient(source,target):
        attempts.append((source,target))
        assert json.loads(book.path.read_text())==before
        if len(attempts)<=2:raise windows_replace_denied(winerror)
        return replace(source,target)
    monkeypatch.setattr(module.os,'replace',transient);monkeypatch.setattr(time,'sleep',waits.append)
    if stage=='reserve':ticket=book.reserve('new',[])
    else:book.settle(ticket,{'prompt_tokens':559,'completion_tokens':30},success=True)
    after=json.loads(book.path.read_text())
    assert len(attempts)==3 and len(set(attempts))==1 and waits==[0.05,0.1]
    assert len(after['calls'])==2 and after['calls'][0]==before['calls'][0]
    assert after['calls'][1]['id']==ticket
    assert after['calls'][1]['status']==('reserved' if stage=='reserve' else 'success')
    if stage=='settle':assert after['calls'][1]['charged_microusd']==272


@pytest.mark.parametrize('stage',['reserve','settle'])
def test_persistent_windows_denial_is_bounded_and_retains_recoverable_state(tmp_path,monkeypatch,stage):
    import json,time
    from app.services import source_concept_budget as module
    book=ledger(tmp_path,10);old=book.reserve('old',[]);book.settle(old,{},success=False)
    ticket=book.reserve('new',[]) if stage=='settle' else None
    before=book.path.read_bytes();attempts=[];waits=[]
    def denied(source,target):attempts.append((source,target));raise windows_replace_denied()
    with monkeypatch.context() as patch:
        patch.setattr(module.os,'replace',denied);patch.setattr(time,'sleep',waits.append)
        with pytest.raises(PermissionError):
            if stage=='reserve':book.reserve('new',[])
            else:book.settle(ticket,{'prompt_tokens':559,'completion_tokens':30},success=True)
    assert book.path.read_bytes()==before
    assert len(attempts)==6 and len(set(attempts))==1 and sum(waits)==pytest.approx(1.55)
    assert attempts[0][0].is_file()  # Failed temporary state remains diagnostic only.
    if stage=='reserve':assert len(json.loads(before)['calls'])==1
    else:
        assert book.recover_response(key='new',reservation=ticket,usage={'prompt_tokens':559,'completion_tokens':30},business_valid=True)
        assert not book.recover_response(key='new',reservation=ticket,usage={'prompt_tokens':559,'completion_tokens':30},business_valid=True)
        assert json.loads(book.path.read_text())['calls'][1]['charged_microusd']==272


def test_non_windows_permission_failure_is_not_silently_retried(tmp_path,monkeypatch):
    import time
    from app.services import source_concept_budget as module
    book=ledger(tmp_path,10);attempts=[];waits=[]
    def denied(*args):attempts.append(args);raise PermissionError('permanent permission denied')
    monkeypatch.setattr(module.os,'replace',denied);monkeypatch.setattr(time,'sleep',waits.append)
    with pytest.raises(PermissionError):book.reserve('new',[])
    assert len(attempts)==1 and not waits and not book.path.exists()
