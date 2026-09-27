"""Validate the existing task's reviewed amendment before constructing a payer."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def authorized_task_cap(private, ledger):
    def require(condition):
        if not condition: raise ValueError('a2_budget_authority_invalid')
    cap=ledger.get('cap_microusd')
    if cap==10000000:
        require(not ledger.get('cap_amendments'))
        return 10
    require(cap==30000000)
    authority=json.loads((ROOT/'docs/state/production-pixiv-a2-budget-authority.json').read_text(encoding='utf-8'))
    require(ledger.get('cap_amendments')==[authority])
    raw=(Path(private)/'closeout43-budget-before-private.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest()==authority['ledger_before_sha256'])
    original=json.loads(raw); calls=original['calls']; count=authority['call_count_before']
    require(original['cap_microusd']==authority['previous_cap_microusd'] and len(calls)==count)
    charge=lambda row:row.get('charged_microusd',row['reserved_microusd'])
    require(sum(map(charge,calls))==authority['charged_before_microusd'])
    prefix=ledger['calls'][:count]
    require(len(prefix)==count and all(new['id']==old['id'] and charge(new)==charge(old)
        for new,old in zip(prefix,calls)))
    return 30
