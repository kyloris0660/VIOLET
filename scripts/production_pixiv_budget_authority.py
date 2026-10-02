"""Validate the existing task's reviewed amendment before constructing a payer."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PREFIX_COMPATIBILITY = 'docs/state/production-pixiv-a2-budget-prefix-compatibility.json'
PREFIX_COMPATIBILITY_SHA256 = 'f1102dced0a9de3f6b6ba0d1db5d0742e58b8f84e9bb2c946ddc8b2924c5798f'


def _canonical(value):
    # JSON comparison preserves booleans, nulls and numbers as distinct types.
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _original_call_preserved(before,after,backfills):
    original=dict(before);current=dict(after)
    # Revocation can remove trust, never grant it. Original HTTP status, usage,
    # fee, key and every other paid-call field remain byte-equivalent JSON.
    if current.get('business_valid') is False and original.get('business_valid',True) is True:
        current.pop('business_valid')
        original.pop('business_valid',None)
        if 'business_validation_reason' not in original:
            reason=current.pop('business_validation_reason',None)
            if reason is not None and (not isinstance(reason,str) or not reason.strip()):return False
    if 'logical_keys' not in original and 'logical_keys' in current:
        row=backfills.get(original['id'])
        if not row or row['key']!=original.get('key'):return False
        if hashlib.sha256(_canonical(current.pop('logical_keys')).encode()).hexdigest()!=row['logical_keys_sha256']:
            return False
    return _canonical(original)==_canonical(current)


def authorized_task_cap(private, ledger):
    def require(condition):
        if not condition: raise ValueError('a2_budget_authority_invalid')
    cap=ledger.get('cap_microusd')
    if cap==10000000:
        require(not ledger.get('cap_amendments'))
        return 10
    require(cap==30000000)
    authority=json.loads((ROOT/'docs/state/production-pixiv-a2-budget-authority.json').read_text(encoding='utf-8'))
    require(_canonical(ledger.get('cap_amendments'))==_canonical([authority]))
    raw=(Path(private)/'closeout43-budget-before-private.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest()==authority['ledger_before_sha256'])
    original=json.loads(raw); calls=original['calls']; count=authority['call_count_before']
    require(original['cap_microusd']==authority['previous_cap_microusd'] and len(calls)==count)
    charge=lambda row:row.get('charged_microusd',row['reserved_microusd'])
    require(sum(map(charge,calls))==authority['charged_before_microusd'])
    prefix=ledger['calls'][:count]
    backfills={}
    if any('logical_keys' not in old and 'logical_keys' in new for old,new in zip(calls,prefix)):
        policy_raw=(ROOT/PREFIX_COMPATIBILITY).read_bytes()
        require(hashlib.sha256(policy_raw).hexdigest()==PREFIX_COMPATIBILITY_SHA256)
        policy=json.loads(policy_raw)
        require(policy.get('schema_version')=='violet.production-pixiv-a2.original-call-backfill.v1'
            and policy.get('ledger_before_sha256')==authority['ledger_before_sha256'])
        backfills={row['id']:row for row in policy['backfill']}
        require(len(backfills)==len(policy['backfill'])==1665)
    require(len(prefix)==count and all(_original_call_preserved(old,new,backfills)
        for old,new in zip(calls,prefix)))
    return 30
