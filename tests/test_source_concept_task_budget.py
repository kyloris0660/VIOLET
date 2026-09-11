import pytest
from app.services.source_concept_budget import AdjudicationBudget, AdjudicationBudgetBlocked


def ledger(tmp_path,cap=0.002):
    return AdjudicationBudget(tmp_path/'budget.json',model='gpt-4.1-mini',cap_usd=cap,input_per_million=0.4,output_per_million=1.6)


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
