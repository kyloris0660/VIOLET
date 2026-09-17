"""Persistent admission and usage accounting for one bounded adjudication task."""
import json
import os
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import hashlib
import logging
import time


WINDOWS_REPLACE_RETRY_DELAYS=(0.05,0.1,0.2,0.4,0.8)


class AdjudicationBudgetBlocked(RuntimeError):
    pass


class AdjudicationBudget:
    def __init__(self, path, *, model, cap_usd, input_per_million, output_per_million):
        self.path = Path(path)
        self.identity = {'schema_version':'source_concept_task_budget_v1','model':model,
                         'cap_microusd':int(Decimal(str(cap_usd))*1000000),
                         'input_per_million':str(input_per_million),'output_per_million':str(output_per_million)}
        if not 0 < self.identity['cap_microusd'] <= 30000000:
            raise ValueError('adjudication_task_budget_outside_thirty_dollars')
        if not model or not all(Decimal(self.identity[key]).is_finite() and Decimal(self.identity[key])>0
                                for key in ('input_per_million','output_per_million')):
            raise ValueError('adjudication_model_pricing_required')

    @contextmanager
    def _locked(self, *, check_cap=True):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.with_suffix('.lock').open('a+b') as lock:
            lock.seek(0)
            if os.name=='nt':
                import msvcrt
                # Reading byte zero before acquiring it is denied while a
                # sibling worker owns the Windows byte-range lock. Metadata
                # inspection does not read the locked region.
                if os.fstat(lock.fileno()).st_size==0:
                    lock.write(b'0'); lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:
                state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {**self.identity,'calls':[]}
                if any(state.get(key)!=value for key,value in self.identity.items()
                       if check_cap or key!='cap_microusd'):
                    raise AdjudicationBudgetBlocked('adjudication_budget_identity_changed')
                self._charged(state)
                yield state
            finally:
                lock.seek(0)
                if os.name=='nt':
                    msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    def _write(self,state):
        temp=self.path.with_name(self.path.name+'.'+uuid4().hex+'.tmp')
        with temp.open('x',encoding='utf-8') as stream:
            json.dump(state,stream,ensure_ascii=False,sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        for attempt in range(len(WINDOWS_REPLACE_RETRY_DELAYS)+1):
            try:
                os.replace(temp,self.path)
                return
            except PermissionError as error:
                # Windows can briefly deny replacement while another reader
                # owns a non-delete-sharing handle. Keep the same fsynced
                # state and ledger lock; never create a second reservation.
                # A persistent denial still fails closed and retains the temp.
                code=getattr(error,'winerror',None)
                if code not in {5,32,33} or attempt==len(WINDOWS_REPLACE_RETRY_DELAYS):raise
                logging.getLogger(__name__).warning('budget_atomic_replace_retry winerror=%s retry=%s',code,attempt+1)
                time.sleep(WINDOWS_REPLACE_RETRY_DELAYS[attempt])

    def _cost(self,input_tokens,output_tokens):
        if any(type(value) is not int or value<0 for value in (input_tokens,output_tokens)):
            raise AdjudicationBudgetBlocked('adjudication_usage_invalid')
        amount=Decimal(input_tokens)*Decimal(self.identity['input_per_million'])+Decimal(output_tokens)*Decimal(self.identity['output_per_million'])
        return int(amount.to_integral_value(rounding=ROUND_CEILING))

    def increase_cap(self, *, previous_cap_usd, authorization_source, authorization_id):
        """Explicit, atomic amendment of this existing task, never a new ledger."""
        previous=int(Decimal(str(previous_cap_usd))*1000000)
        proposed=self.identity['cap_microusd']
        if not authorization_source or not authorization_id or not 0 < previous < proposed <= 30000000:
            raise ValueError('adjudication_budget_amendment_invalid')
        with self._locked(check_cap=False) as state:
            amendments=state.get('cap_amendments',[])
            prior=next((r for r in amendments if r['authorization_id']==authorization_id),None)
            terms={'previous_cap_microusd':previous,'cap_microusd':proposed,
                   'authorization_source':authorization_source,'authorization_id':authorization_id}
            if prior:
                if any(prior.get(k)!=v for k,v in terms.items()) or state['cap_microusd']!=proposed:
                    raise AdjudicationBudgetBlocked('adjudication_amendment_identity_changed')
                return prior
            if not self.path.is_file() or state['cap_microusd']!=previous:
                raise AdjudicationBudgetBlocked('adjudication_amendment_previous_cap_mismatch')
            if any(r['status']=='reserved' for r in state['calls']):
                raise AdjudicationBudgetBlocked('adjudication_amendment_inflight_call')
            receipt={**terms,'charged_before_microusd':self._charged(state),'call_count_before':len(state['calls']),
                     'ledger_before_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
                     'recorded_at':datetime.now(timezone.utc).isoformat()}
            state['cap_microusd']=proposed
            state['cap_amendments']=[*amendments,receipt]
            self._write(state)
            return receipt

    @staticmethod
    def _charged(state):
        """Re-derive every debit; a stored summary is never spend authority."""
        def integer(value):
            if type(value) is not int or value<0:
                raise AdjudicationBudgetBlocked('adjudication_ledger_integer_invalid')
            return value
        prices=[Decimal(str(state[key])) for key in ('input_per_million','output_per_million')]
        if any(not value.is_finite() or value<=0 for value in prices):
            raise AdjudicationBudgetBlocked('adjudication_ledger_pricing_invalid')
        def cost(a,b):
            return int((integer(a)*prices[0]+integer(b)*prices[1]).to_integral_value(rounding=ROUND_CEILING))
        total=0;seen=set()
        for row in state['calls']:
            if not row.get('id') or row['id'] in seen:
                raise AdjudicationBudgetBlocked('adjudication_ledger_attempt_identity_invalid')
            seen.add(row['id'])
            reserved=integer(row['reserved_microusd'])
            if reserved!=cost(row['input_token_ceiling'],row['output_token_ceiling']):
                raise AdjudicationBudgetBlocked('adjudication_reservation_cost_mismatch')
            if row['status']=='reserved':
                if 'charged_microusd' in row or 'usage_known' in row:
                    raise AdjudicationBudgetBlocked('adjudication_unsettled_charge_invalid')
                expected=reserved
            elif row['status'] in {'success','failed'}:
                if type(row.get('usage_known')) is not bool:
                    raise AdjudicationBudgetBlocked('adjudication_usage_known_required')
                if row['usage_known']:
                    usage=row.get('usage')
                    if not isinstance(usage,dict) or not {'prompt_tokens','completion_tokens'}<=usage.keys():
                        raise AdjudicationBudgetBlocked('adjudication_known_usage_required')
                    expected=cost(usage['prompt_tokens'],usage['completion_tokens'])
                else:
                    if row.get('usage') is not None:
                        raise AdjudicationBudgetBlocked('adjudication_unknown_usage_invalid')
                    expected=reserved
                if integer(row.get('charged_microusd'))!=expected:
                    raise AdjudicationBudgetBlocked('adjudication_charged_cost_mismatch')
            else:
                raise AdjudicationBudgetBlocked('adjudication_ledger_status_invalid')
            total+=expected
        return total

    def reserve(self,key,messages,*,max_output_tokens=600,logical_keys=()):
        # Byte-level BPE cannot produce more text tokens than UTF-8 bytes;
        # retain extra framing headroom and ignore input-cache discounts.
        input_ceiling=len(json.dumps(messages,ensure_ascii=False).encode('utf-8'))+512
        amount=self._cost(input_ceiling,max_output_tokens)
        logical_keys=sorted(set(logical_keys))
        if not key or any(not isinstance(item,str) or not item for item in logical_keys):
            raise ValueError('adjudication_logical_key_invalid')
        with self._locked() as state:
            previous=[row for row in state['calls'] if row['key']==key]
            if any(row['status']=='reserved' for row in previous):
                raise AdjudicationBudgetBlocked('adjudication_previous_call_outcome_unknown')
            if any(row['status']=='success' and row.get('business_valid',True) for row in previous):
                raise AdjudicationBudgetBlocked('adjudication_success_requires_cache_recovery')
            if len(previous)>=3:
                raise AdjudicationBudgetBlocked('adjudication_pair_attempts_exhausted')
            for logical in logical_keys:
                attempts=[r for r in state['calls'] if logical in r.get('logical_keys',[]) or r['key']==logical]
                if any(r['status']=='reserved' for r in attempts):
                    raise AdjudicationBudgetBlocked('adjudication_logical_call_outcome_unknown')
                if len(attempts)>=3:
                    raise AdjudicationBudgetBlocked('adjudication_logical_attempts_exhausted')
            if self._charged(state)+amount>self.identity['cap_microusd']:
                raise AdjudicationBudgetBlocked('adjudication_cumulative_budget_exhausted')
            reservation=uuid4().hex
            state['calls'].append({'id':reservation,'key':key,'status':'reserved','reserved_microusd':amount,
                                   'input_token_ceiling':input_ceiling,'output_token_ceiling':max_output_tokens,
                                   'logical_keys':logical_keys})
            self._write(state)
        return reservation

    def settle(self,reservation,usage,*,success):
        with self._locked() as state:
            row=next(row for row in state['calls'] if row['id']==reservation)
            known=isinstance(usage,dict) and all(type(usage.get(key)) is int and usage[key]>=0 for key in ('prompt_tokens','completion_tokens'))
            if usage and not known:
                raise AdjudicationBudgetBlocked('adjudication_usage_invalid')
            charged=self._cost(usage['prompt_tokens'],usage['completion_tokens']) if known else row['reserved_microusd']
            outcome=dict(status='success' if success else 'failed',usage_known=known,
                         usage={key:usage[key] for key in ('prompt_tokens','completion_tokens')} if known else None,
                         charged_microusd=charged)
            if row['status']!='reserved':
                if any(row.get(k)!=v for k,v in outcome.items()):
                    raise ValueError('adjudication_settlement_outcome_changed')
                return False
            row.update(**outcome,business_valid=bool(success))
            self._write(state)
            if charged>row['reserved_microusd']:
                raise AdjudicationBudgetBlocked('adjudication_usage_exceeded_conservative_reservation')
            return True

    def recover_response(self, *, key, usage, business_valid, reservation=None,logical_keys=()):
        """Bind saved raw to its attempt. Legacy caches may have no local call."""
        with self._locked() as state:
            matches=[r for r in state['calls'] if r['id']==reservation] if reservation else [r for r in state['calls'] if r['key']==key]
            if not matches:
                if reservation:raise AdjudicationBudgetBlocked('adjudication_response_attempt_missing')
                return False
            if any(r['key']!=key for r in matches):
                raise AdjudicationBudgetBlocked('adjudication_response_attempt_key_mismatch')
            if not reservation and len(matches)!=1:
                if all(r['status']!='reserved' for r in matches) and business_valid:
                    return False  # Already charged legacy success needs no synthetic receipt.
                raise AdjudicationBudgetBlocked('adjudication_legacy_response_attempt_ambiguous')
            row=matches[0]
            if logical_keys and not row.get('logical_keys'):
                row['logical_keys']=sorted(set(logical_keys))
                self._write(state)
            if row['status']!='reserved':
                if not business_valid and row.get('business_valid',True):
                    # Revalidate an already charged HTTP success. Preserve its
                    # original status, usage and cost, but release retry admission.
                    row['business_valid']=False
                    row['business_validation_reason']='saved_response_failed_current_schema'
                    self._write(state)
                return False
            ticket=row['id']
        return self.settle(ticket,usage,success=business_valid)

    def response_identity(self, reservation):
        with self._locked() as state:
            row=next(r for r in state['calls'] if r['id']==reservation)
            same=[r for r in state['calls'] if r['key']==row['key']]
            return {'reservation':reservation,'key':row['key'],
                    'attempt':next(i+1 for i,r in enumerate(same) if r['id']==reservation)}

    def logical_attempt_index(self):
        with self._locked() as state:
            index={}
            for row in state['calls']:
                for key in row.get('logical_keys',[]):
                    index.setdefault(key,[]).append({'id':row['id'],'status':row['status'],
                        'business_valid':row.get('business_valid',row['status']=='success')})
            return index

    def summary(self):
        with self._locked() as state:
            return {**self.identity,'call_count':len(state['calls']),
                    'charged_or_reserved_usd':self._charged(state)/1000000,
                    'unknown_usage_count':sum(not row.get('usage_known',False) for row in state['calls']),
                    'success_count':sum(row['status']=='success' for row in state['calls'])}
