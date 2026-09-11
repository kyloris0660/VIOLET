"""Reuse F7a text extraction for unresolved production Pixiv tag roles.

Only source tag strings are supplied. Neither media, paths, nor credentials
enter prompts. Unit answers and raw replies survive interruption independently
of batch composition, and every actual call uses the same A2 budget ledger.
"""
from dataclasses import asdict, replace
import asyncio
import json
from pathlib import Path

from .llm_translation_provider import BaseLLMProvider, LLMHTTPStatusError, LLMTransportError
from .pixiv_metadata_projection_service import canonical_fingerprint
from .source_metadata_registry_service import canonical_source_key
from .production_pixiv_semantics import adapt_production_semantics
from .source_concept_budget import AdjudicationBudgetBlocked
from .source_concept_resolver_service import _atomic_write_json
from .source_name_candidate_extraction_service import (
    SourceCandidateInputGroup,build_extraction_units,deterministic_bundle_for_unit,
    group_prompt_payload,run_extraction_sync,validate_extraction_record,
    PROMPT_VERSION,EXTRACTOR_VERSION,SCHEMA_VERSION,
    SourceNameCandidateExtractionError,
)

ROLE_SCHEMA='violet.production-pixiv-role-result.v1'


class ProductionRoleProviderPaused(RuntimeError):
    pass


def plan_role_extraction(consumer,vocabulary):
    adapted=adapt_production_semantics(consumer,vocabulary)
    groups=[]
    for signal in adapted.signals:
        if signal.origin_type=='pixiv_tag_observation' and signal.role_hint=='unknown' and signal.status!='rejected':
            groups.append(SourceCandidateInputGroup(group_key=signal.signal_key,provider='pixiv',
                tags=({'raw_tag':signal.raw_value,'source_tag_kind':'provider_tag'},),
                data_origin='production_metadata_text_only'))
    units,summary=build_extraction_units(groups)
    # Occurrence counts are accounting, not different questions to the model.
    units=[replace(unit,unit_group=replace(unit.unit_group,group_key='a2-role:'+canonical_fingerprint(unit.extraction_key)[:24])) for unit in units]
    return units,summary


def _identity(unit,model):
    return {'schema_version':ROLE_SCHEMA,'extractor_version':EXTRACTOR_VERSION,'prompt_version':PROMPT_VERSION,
        'decision_schema':SCHEMA_VERSION,'model':model,'extraction_key':unit.extraction_key,
        'input_fingerprint':canonical_fingerprint(group_prompt_payload(unit.unit_group))}


def _unit_path(cache_dir,unit):
    return Path(cache_dir)/'units'/f'{canonical_fingerprint(unit.extraction_key)}.json'


def _read_unit_cache(path,unit,model):
    cached=json.loads(path.read_text(encoding='utf-8'))
    expected=_identity(unit,model)
    changed={key for key,value in expected.items() if cached.get(key)!=value}
    if not changed:return cached,False
    # F7a deduplicates case variants under the same extraction key, but its
    # representative spelling can change when more occurrences arrive. Reuse
    # the original question only when reconstructing it proves the exact
    # stored prompt fingerprint and every policy/model identity still matches.
    if (changed=={'input_fingerprint'} and unit.unit_group.tags
        and canonical_source_key(cached.get('raw_value'))==canonical_source_key(unit.normalized_value)):
        original=replace(unit,normalized_value=cached['raw_value'],unit_group=replace(unit.unit_group,
            tags=tuple({**tag,'raw_tag':cached['raw_value']} for tag in unit.unit_group.tags)))
        if _identity(original,model)['input_fingerprint']==cached['input_fingerprint']:
            return cached,True
    raise ValueError('production_role_unit_cache_identity_changed')


def _record(unit,model,verdict,candidates,*,origin):
    return {**_identity(unit,model),'raw_value':unit.normalized_value,'origin':origin,
        'verdict':verdict.extraction_verdict,'candidates':[asdict(candidate) for candidate in candidates],
        'identity_equivalence_authorized':False,'entity_truth_written':False}


class BudgetedExtractionProvider(BaseLLMProvider):
    def __init__(self,provider,budget,cache_dir,units):
        self.provider=provider;self.budget=budget;self.model=provider.model
        if self.model!=budget.identity['model']:
            raise ValueError('production_role_actual_model_mismatch')
        self.cache_dir=Path(cache_dir);self.units={unit.unit_group.group_key:unit for unit in units}
        self.last_usage={};self.calls=0;self.raw_cache_hits=0;self.transport_failures=0

    def is_available(self):return self.provider.is_available()
    def get_provider_name(self):return self.provider.get_provider_name()
    async def translate_tags(self,tags):raise RuntimeError('role_extraction_has_no_translation_write_route')

    def save_units(self,content):
        try:
            payload=json.loads(content)
            rows=payload.get('records',[]) if isinstance(payload,dict) else []
        except (ValueError,TypeError):return
        for row in rows:
            if not isinstance(row,dict):continue
            unit=self.units.get(row.get('group_key'))
            if not unit:continue
            try:
                verdict,candidates,*_=validate_extraction_record(row,unit.unit_group)
            except (ValueError,SourceNameCandidateExtractionError):continue
            if verdict.extraction_verdict.startswith('extraction_error'):continue
            _atomic_write_json(_unit_path(self.cache_dir,unit),
                {**_record(unit,self.model,verdict,candidates,origin='existing_f7a_extractor_primary_model'),
                 'validated_response':row})

    async def complete_chat(self,messages,*,temperature=0.0,max_tokens=6000):
        # F7a may split a partially invalid batch. Preserve the valid units
        # already saved by the callback instead of paying to classify them
        # again within the smaller request.
        payload=json.loads(messages[1]['content'])
        requested=payload.get('records',[])
        known={}
        for row in requested:
            unit=self.units.get(row['group_key'])
            path=_unit_path(self.cache_dir,unit) if unit else None
            if path and path.exists():
                cached,_=_read_unit_cache(path,unit,self.model)
                if cached.get('validated_response'):known[row['group_key']]=cached['validated_response']
        if known:
            missing=[row for row in requested if row['group_key'] not in known]
            if missing:
                narrowed={**payload,'record_count':len(missing),'records':missing}
                changed=[messages[0],{**messages[1],'content':json.dumps(narrowed,ensure_ascii=False,sort_keys=True)}]
                remainder=await self.complete_chat(changed,temperature=temperature,max_tokens=max_tokens)
                try:
                    response=json.loads(remainder)
                    known.update({row['group_key']:row for row in response.get('records',[])})
                except (ValueError,TypeError,KeyError,AttributeError):return remainder
            self.raw_cache_hits+=len(requested)-len(missing)
            return json.dumps({'records':[known.get(row['group_key'],{'group_key':row['group_key'],'verdict':'extraction_error'}) for row in requested]},ensure_ascii=False)
        signature=canonical_fingerprint({'model':self.model,'messages':messages,'temperature':temperature,'max_tokens':max_tokens})
        path=self.cache_dir/'raw'/f'{signature}.json'
        if path.exists():
            cached=json.loads(path.read_text(encoding='utf-8'))
            if cached.get('input_fingerprint')!=signature or cached.get('model')!=self.model:
                raise ValueError('role_raw_cache_identity_mismatch')
            self.raw_cache_hits+=1
            self.save_units(cached['content'])
            return cached['content']
        reservation=self.budget.reserve('role-extraction:'+signature,messages,max_output_tokens=max_tokens)
        self.provider.last_usage={}
        try:
            self.calls+=1
            content=await self.provider.complete_chat(messages,temperature=temperature,max_tokens=max_tokens)
            self.transport_failures=0
            self.last_usage=dict(getattr(self.provider,'last_usage',{}))
            _atomic_write_json(path,{'input_fingerprint':signature,'model':self.model,'content':content,'usage':self.last_usage})
            self.save_units(content)
        except BaseException as exc:
            self.budget.settle(reservation,getattr(self.provider,'last_usage',{}),success=False)
            if isinstance(exc,LLMHTTPStatusError) and exc.status_code in {401,403,429}:
                raise ProductionRoleProviderPaused('role_provider_authentication_or_rate_limit') from exc
            if isinstance(exc,(LLMTransportError,TimeoutError,asyncio.CancelledError)):
                self.transport_failures+=1
                if self.transport_failures>=3:
                    raise ProductionRoleProviderPaused('role_provider_repeated_transport_failure') from exc
            raise
        self.budget.settle(reservation,self.last_usage,success=True)
        return content


def extract_production_roles(units,*,provider,budget,cache_dir,batch_size=20,progress=None):
    if type(batch_size) is not int or not 1 <= batch_size <= 50:
        raise ValueError('production_role_batch_size_invalid')
    cache_dir=Path(cache_dir)
    wrapped=BudgetedExtractionProvider(provider,budget,cache_dir,units)
    records={};pending=[];cached=0;deterministic=0;blocked=None;canonical_reuse=0
    for unit in units:
        path=_unit_path(cache_dir,unit)
        if path.exists():
            value,variant=_read_unit_cache(path,unit,wrapped.model)
            canonical_reuse+=int(variant)
            records[unit.extraction_key]=value;cached+=1
        elif not unit.llm_required:
            bundle=deterministic_bundle_for_unit(unit,run_id='production-pixiv-roles',run_label='production-pixiv-roles')
            value=_record(unit,wrapped.model,bundle.record_verdicts[0],bundle.candidates,origin='existing_f7a_deterministic')
            _atomic_write_json(path,value);records[unit.extraction_key]=value;deterministic+=1
        else:pending.append(unit)
    for start in range(0,len(pending),batch_size):
        batch=pending[start:start+batch_size]
        try:
            run_extraction_sync(wrapped,[unit.unit_group for unit in batch],run_id='production-pixiv-roles',
                run_label='production-pixiv-roles',chunk_size=batch_size,retries=0,max_tokens=6000,
                timeout_seconds=90,provider_summary={'model_label':wrapped.model,'uses_fallback_provider':False})
        except (AdjudicationBudgetBlocked,ProductionRoleProviderPaused) as exc:
            blocked=str(exc)
        for unit in batch:
            path=_unit_path(cache_dir,unit)
            if path.exists():records[unit.extraction_key]=json.loads(path.read_text(encoding='utf-8'))
        if progress:progress({'completed_units':len(records),'total_units':len(units),'provider_calls':wrapped.calls,
            'budget':budget.summary(),'blocked':blocked})
        if blocked:break
    return {'schema_version':ROLE_SCHEMA,'records':records,'summary':{'total_units':len(units),'completed_units':len(records),
        'cache_hits':cached,'deterministic_units':deterministic,'new_provider_calls':wrapped.calls,'raw_cache_hits':wrapped.raw_cache_hits,
        'original_question_case_variant_cache_hits':canonical_reuse,
        'blocked':blocked,'remaining_units':len(units)-len(records),'task_budget':budget.summary()}}
