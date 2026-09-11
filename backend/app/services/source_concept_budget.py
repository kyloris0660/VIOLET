"""Persistent admission and usage accounting for one bounded adjudication task."""
import json
import os
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from uuid import uuid4


class AdjudicationBudgetBlocked(RuntimeError):
    pass


class AdjudicationBudget:
    def __init__(self, path, *, model, cap_usd, input_per_million, output_per_million):
        self.path = Path(path)
        self.identity = {'schema_version':'source_concept_task_budget_v1','model':model,
                         'cap_microusd':int(Decimal(str(cap_usd))*1000000),
                         'input_per_million':str(input_per_million),'output_per_million':str(output_per_million)}
        if not 0 < self.identity['cap_microusd'] <= 10000000:
            raise ValueError('adjudication_task_budget_outside_ten_dollars')
        if not model or not all(Decimal(self.identity[key]).is_finite() and Decimal(self.identity[key])>0
                                for key in ('input_per_million','output_per_million')):
            raise ValueError('adjudication_model_pricing_required')

    @contextmanager
    def _locked(self):
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
                if any(state.get(key)!=value for key,value in self.identity.items()):
                    raise AdjudicationBudgetBlocked('adjudication_budget_identity_changed')
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
        os.replace(temp,self.path)

    def _cost(self,input_tokens,output_tokens):
        amount=Decimal(input_tokens)*Decimal(self.identity['input_per_million'])+Decimal(output_tokens)*Decimal(self.identity['output_per_million'])
        return int(amount.to_integral_value(rounding=ROUND_CEILING))

    @staticmethod
    def _charged(state):
        return sum(row.get('charged_microusd',row['reserved_microusd']) for row in state['calls'])

    def reserve(self,key,messages,*,max_output_tokens=600):
        # Byte-level BPE cannot produce more text tokens than UTF-8 bytes;
        # retain extra framing headroom and ignore input-cache discounts.
        input_ceiling=len(json.dumps(messages,ensure_ascii=False).encode('utf-8'))+512
        amount=self._cost(input_ceiling,max_output_tokens)
        with self._locked() as state:
            previous=[row for row in state['calls'] if row['key']==key]
            if any(row['status']=='reserved' for row in previous):
                raise AdjudicationBudgetBlocked('adjudication_previous_call_outcome_unknown')
            if any(row['status']=='success' for row in previous):
                raise AdjudicationBudgetBlocked('adjudication_success_requires_cache_recovery')
            if len(previous)>=3:
                raise AdjudicationBudgetBlocked('adjudication_pair_attempts_exhausted')
            if self._charged(state)+amount>self.identity['cap_microusd']:
                raise AdjudicationBudgetBlocked('adjudication_cumulative_budget_exhausted')
            reservation=uuid4().hex
            state['calls'].append({'id':reservation,'key':key,'status':'reserved','reserved_microusd':amount,
                                   'input_token_ceiling':input_ceiling,'output_token_ceiling':max_output_tokens})
            self._write(state)
        return reservation

    def settle(self,reservation,usage,*,success):
        with self._locked() as state:
            row=next(row for row in state['calls'] if row['id']==reservation)
            if row['status']!='reserved':
                raise ValueError('adjudication_reservation_already_settled')
            known=isinstance(usage,dict) and all(type(usage.get(key)) is int and usage[key]>=0 for key in ('prompt_tokens','completion_tokens'))
            charged=self._cost(usage['prompt_tokens'],usage['completion_tokens']) if known else row['reserved_microusd']
            row.update(status='success' if success else 'failed',usage_known=known,
                       usage={key:usage[key] for key in ('prompt_tokens','completion_tokens') } if known else None,
                       charged_microusd=charged)
            self._write(state)
            if charged>row['reserved_microusd']:
                raise AdjudicationBudgetBlocked('adjudication_usage_exceeded_conservative_reservation')

    def summary(self):
        with self._locked() as state:
            return {**self.identity,'call_count':len(state['calls']),
                    'charged_or_reserved_usd':self._charged(state)/1000000,
                    'unknown_usage_count':sum(not row.get('usage_known',False) for row in state['calls']),
                    'success_count':sum(row['status']=='success' for row in state['calls'])}
