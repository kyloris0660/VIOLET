"""Reuse F7a text extraction for unresolved production Pixiv tag roles.

Only source tag strings are supplied. Neither media, paths, nor credentials
enter prompts. Unit answers and raw replies survive interruption independently
of batch composition, and every actual call uses the same A2 budget ledger.
"""
from dataclasses import asdict, replace
from collections import defaultdict
import asyncio
import json
from pathlib import Path
from threading import Lock

from .llm_translation_provider import BaseLLMProvider, LLMHTTPStatusError, LLMTransportError
from .pixiv_metadata_projection_service import canonical_fingerprint
from .source_metadata_registry_service import canonical_source_key
from .production_pixiv_semantics import adapt_production_semantics
from .source_concept_budget import AdjudicationBudgetBlocked
from .source_concept_resolver_service import _atomic_write_json
from .source_name_candidate_extraction_service import (
    SourceCandidateInputGroup,build_extraction_units,deterministic_bundle_for_unit,
    SourceExtractionUnit,
    group_prompt_payload,extraction_messages,run_extraction_sync,validate_extraction_record,
    PROMPT_VERSION,EXTRACTOR_VERSION,SCHEMA_VERSION,
    SourceNameCandidateExtractionError,
)

ROLE_SCHEMA='violet.production-pixiv-role-result.v1'
COMPLETION_ORIGIN='production_pixiv_residual_roles_v1'
COVERAGE_REPAIR_ORIGIN='production_pixiv_role_coverage_repair_v2'
COMPLETION_PROMPT=(
    'Production residual role completion v1. The requested raw tags are listed in each record data_type_label. '
    'Classify every requested spelling using ALL of its actual tags as context. '
    'Earlier deterministic hints were partial and did not classify the remaining tags. '
    'Do not merely repeat parenthetical or popularity-prefix rules. Multilingual spellings are not redundant: '
    'preserve each requested raw_value separately, even if several refer to the same name. '
    'Use character for a fictional character and work_title for its franchise/game/series. '
    'Provide work_context from an actual context tag when supported. Do not invent a context. '
    'Keep an uncertain name unknown_name_like needs_review; reject descriptions and meta tags. '
    'Do not create identity equivalence or Entity truth.'
)
COVERAGE_REPAIR_PROMPT=(
    COMPLETION_PROMPT+' This is one coverage repair for previously omitted targets, not a re-judgment of answered names. '
    'Return target_dispositions for EVERY requested raw spelling, with raw_value, disposition, and reason_code. '
    'disposition must be candidate, unknown, or non_name. For candidate, also return the matching F7a candidate. '
    'For unknown, preserve uncertainty; for non_name, give a short reason without inventing identity. '
    'Aggregate rejected_summary counts do not account for individual requested tags. '
    'Do not collapse multilingual spellings or answer only names in the surrounding context.'
    ' The record schema is extended for this task: target_dispositions is a REQUIRED array at the same level as candidates. '
    'Example for a descriptive tag: "target_dispositions":[{"raw_value":"red dress","disposition":"non_name","reason_code":"clothing"}]. '
    'The array must cover the exact requested spellings, including rejected descriptions. Short reason_code values are required; no prose rationale.'
)


class ProductionRoleProviderPaused(RuntimeError):
    pass


class ExtractionDispatchGate:
    """At most two owned text workers share one systemic-failure pause."""
    def __init__(self):
        self.lock=Lock();self.reason=None;self.transport_failures=0

    def check(self):
        with self.lock:
            if self.reason:raise ProductionRoleProviderPaused(self.reason)

    def returned(self,error=None):
        with self.lock:
            if error is None:self.transport_failures=0
            elif isinstance(error,LLMHTTPStatusError) and error.status_code in {401,403,429}:
                self.reason='role_provider_authentication_or_rate_limit'
            elif isinstance(error,(LLMTransportError,TimeoutError,asyncio.CancelledError)):
                self.transport_failures+=1
                if self.transport_failures>=3:self.reason='role_provider_repeated_transport_failure'
            if self.reason:raise ProductionRoleProviderPaused(self.reason) from error


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


def _production_messages(messages):
    payload=json.loads(messages[1]['content'])
    selected=[row for row in payload.get('records',[]) if row.get('data_origin') in {COMPLETION_ORIGIN,COVERAGE_REPAIR_ORIGIN}]
    if not selected:return messages
    origins={row['data_origin'] for row in selected}
    if len(origins)!=1:raise ValueError('mixed_production_role_prompt_versions')
    origin=next(iter(origins))
    prompt=COVERAGE_REPAIR_PROMPT if origin==COVERAGE_REPAIR_ORIGIN else COMPLETION_PROMPT
    for row in selected:row.pop('deterministic_hints',None)
    payload['production_prompt_adapter']=origin
    system=messages[0]['content']
    if origin==COVERAGE_REPAIR_ORIGIN:
        # F7a's base output restriction conflicts with this task's additional
        # per-target ledger. Change only the repair adapter, never old prompts.
        system=system.replace('Only include ambiguous_items or error_code when needed. ',
            'Also include target_dispositions for every requested raw spelling. Include ambiguous_items or error_code when needed. ')
        system=system.replace('Do not output verbose reasons, prose explanations, chain-of-thought, or per-tag rationale text.',
            'Do not output verbose reasons, prose explanations, or chain-of-thought. Short per-target reason_code values are required.')
        payload['required_record_fields']=['group_key','provider','verdict','candidates','rejected_summary','target_dispositions']
        payload['target_disposition_fields']=['raw_value','disposition','reason_code']
        for row in selected:
            row['requested_raw_tags']=json.loads(row['data_type_label'].split(': ',1)[1])
    if not system.endswith(prompt):system+='\n'+prompt
    return [{**messages[0],'content':system},
        {**messages[1],'content':json.dumps(payload,ensure_ascii=False,sort_keys=True)}]


def _unit_path(cache_dir,unit):
    return Path(cache_dir)/'units'/f'{canonical_fingerprint(unit.extraction_key)}.json'


def _adapt_response_record(row,unit):
    from .source_metadata_registry_service import parse_parenthetical_name
    from .source_name_candidate_extraction_service import popularity_suffix_prefix
    row=json.loads(json.dumps(row))
    supported=set()
    compound_prefixes=defaultdict(list)
    for tag in unit.unit_group.tags:
        raw=tag.get('raw_tag') or ''
        supported.add(canonical_source_key(raw))
        for delimiter in (':','：'):
            if delimiter in raw:
                prefix,suffix=raw.split(delimiter,1)
                if prefix.strip() and suffix.strip():
                    compound_prefixes[canonical_source_key(prefix)].append(raw)
        parsed=parse_parenthetical_name(raw)
        if parsed:supported.update(canonical_source_key(value) for value in parsed)
        popularity=popularity_suffix_prefix(raw)
        if popularity:supported.add(canonical_source_key(popularity.get('extracted_prefix')))
    # Some preserved responses put a structured candidate inside its target
    # disposition. Recover that answer through the same validator. The literal
    # request proves source/action provenance; missing confidence stays missing
    # so F7a retains its existing zero-confidence review downgrade.
    dispositions=row.get('target_dispositions')
    if isinstance(dispositions,list) and isinstance(row.get('candidates'),list):
        literal={canonical_source_key(t.get('raw_tag')) for t in unit.unit_group.tags}
        grouped=defaultdict(list)
        for disposition in dispositions:
            if isinstance(disposition,dict):grouped[canonical_source_key(disposition.get('raw_value'))].append(disposition)
        existing={canonical_source_key(c.get('raw_value')) for c in row['candidates'] if isinstance(c,dict)}
        for raw,answers in grouped.items():
            if raw not in literal or raw in existing or len(answers)!=1:continue
            answer=answers[0];candidate=answer.get('candidate')
            if answer.get('disposition')!='candidate' or not isinstance(candidate,dict):continue
            if canonical_source_key(candidate.get('raw_value'))!=raw:continue
            candidate=dict(candidate)
            candidate.setdefault('source_field','source_tag_observation')
            candidate.setdefault('extraction_action','normal_tag_candidate')
            candidate['production_candidate_response_location']='target_dispositions.candidate'
            try:
                validate_extraction_record({**row,'candidates':[candidate]},unit.unit_group)
            except (ValueError,TypeError,SourceNameCandidateExtractionError):
                # An invalid nested item cannot poison a previously valid
                # sibling cache. Its original disposition remains diagnostic.
                continue
            row['candidates'].append(candidate)
    for candidate in row.get('candidates') or []:
        if not isinstance(candidate,dict):continue
        raw_key=canonical_source_key(candidate.get('raw_value'))
        compound=sorted(set(compound_prefixes.get(raw_key,[])))
        if len(compound)==1 and (candidate.get('role') or candidate.get('candidate_role')) in {'character','person'}:
            # A model-extracted character prefix is grounded in this literal
            # compound tag. Keep the full observed spelling as provenance and
            # its reported display name; no outfit suffix becomes a franchise
            # and no identity-equivalence judgment is supplied here.
            prefix=candidate['raw_value']
            candidate.setdefault('display_name',prefix)
            candidate.setdefault('normalized_value',prefix)
            candidate['raw_value']=compound[0]
            candidate['production_original_extracted_span']=prefix
        if isinstance(candidate.get('confidence'),str):
            value={'high':0.9,'medium':0.7,'low':0.4}.get(candidate['confidence'].casefold())
            if value is not None:candidate['confidence']=value
        # The actual input proves these names came from source tags. A model's
        # generic field label must not turn an explicit tag into a weak title.
        # No provider role or name is invented; F7a still validates the answer.
        if canonical_source_key(candidate.get('raw_value')) in supported:
            reported_role=candidate.get('role') or candidate.get('candidate_role')
            if reported_role=='work_context':
                # A context label does not establish whether its name is a
                # place, character or work. Preserve the source spelling as
                # unresolved instead of discarding valid sibling answers or
                # promoting it to a work identity.
                candidate['production_reported_role']=reported_role
                candidate['production_reported_status']=candidate.get('status') or candidate.get('candidate_status')
                candidate['role']='unknown_name_like'
                candidate['status']='needs_review'
            if candidate.get('source_field') in {'provider_tag','provider_field','pixiv_tag'}:
                candidate['source_field']='source_tag_observation'
            if candidate.get('extraction_action')=='normal_tag':
                candidate['extraction_action']='normal_tag_candidate'
    return row


def _revalidate_cached_response(cached,unit):
    previous=cached.get('validated_response')
    if not previous:return cached
    adapted=_adapt_response_record(previous,unit)
    if adapted==previous:return cached
    verdict,candidates,*_=validate_extraction_record(adapted,unit.unit_group)
    return {**cached,'verdict':verdict.extraction_verdict,
        'candidates':[asdict(candidate) for candidate in candidates],
        'validated_response':adapted,'response_adapter_version':'production_tag_provenance_v2',
        'original_cached_response_fingerprint':canonical_fingerprint(previous)}


def _read_unit_cache(path,unit,model):
    cached=json.loads(path.read_text(encoding='utf-8'))
    expected=_identity(unit,model)
    changed={key for key,value in expected.items() if cached.get(key)!=value}
    if not changed:return _revalidate_cached_response(cached,unit),False
    # F7a deduplicates case variants under the same extraction key, but its
    # representative spelling can change when more occurrences arrive. Reuse
    # the original question only when reconstructing it proves the exact
    # stored prompt fingerprint and every policy/model identity still matches.
    if (changed=={'input_fingerprint'} and unit.unit_group.tags
        and canonical_source_key(cached.get('raw_value'))==canonical_source_key(unit.normalized_value)):
        original=replace(unit,normalized_value=cached['raw_value'],unit_group=replace(unit.unit_group,
            tags=tuple({**tag,'raw_tag':cached['raw_value']} for tag in unit.unit_group.tags)))
        if _identity(original,model)['input_fingerprint']==cached['input_fingerprint']:
            return _revalidate_cached_response(cached,original),True
    raise ValueError('production_role_unit_cache_identity_changed')


def _record(unit,model,verdict,candidates,*,origin):
    return {**_identity(unit,model),'raw_value':unit.normalized_value,'origin':origin,
        'verdict':verdict.extraction_verdict,'candidates':[asdict(candidate) for candidate in candidates],
        'identity_equivalence_authorized':False,'entity_truth_written':False}


def _unit_answer_complete(unit,record):
    return (unit.unit_group.data_origin not in {COMPLETION_ORIGIN,COVERAGE_REPAIR_ORIGIN}
        or not role_target_coverage(unit,record)['missing_raw_tags'])


def _merge_valid_target_answers(unit,row,candidates,previous=None):
    """Retain accepted siblings while admitting only validated missing answers."""
    from .production_pixiv_semantics import _context_candidate_matches
    def checked(response,validated):
        raw=response.get('target_dispositions',[]);groups=defaultdict(list);invalid=False
        if not isinstance(raw,list):raw=[];invalid=True
        for answer in raw:
            if not isinstance(answer,dict) or answer.get('raw_value') not in unit.raw_values:
                invalid=True;continue
            groups[answer['raw_value']].append(answer)
        accepted={};missing={}
        for name,answers in groups.items():
            answer=answers[0]
            valid=len(answers)==1 and isinstance(answer.get('disposition'),str) and (
                (answer.get('disposition') in {'unknown','non_name'}
                 and isinstance(answer.get('reason_code'),str) and answer['reason_code'].strip())
                or (answer.get('disposition')=='candidate'
                    and any(_context_candidate_matches(c,name) for c in validated)))
            if valid:accepted[name]=answer
            else:
                invalid=True
                missing[name]={'raw_value':name,'disposition':'invalid_response_retained_as_missing'}
        return accepted,missing,invalid or bool(response.get('target_dispositions_validation_error'))
    old=(previous or {}).get('validated_response',{})
    protected=set(role_target_coverage(unit,previous)['outcomes']) if previous else set()
    old_valid,old_missing,old_error=checked(old,(previous or {}).get('candidates',[]))
    new_valid,new_missing,new_error=checked(row,[asdict(c) for c in candidates])
    dispositions={**old_missing,**old_valid}
    for name,answer in new_missing.items():
        if name not in protected:dispositions.setdefault(name,answer)
    dispositions.update({name:answer for name,answer in new_valid.items() if name not in protected})
    incoming=[c for c in row.get('candidates',[])
        if not any(_context_candidate_matches(c,name) for name in protected)]
    return {**row,'candidates':list({canonical_fingerprint(c):c for c in
        [*old.get('candidates',[]),*incoming]}.values()),
        'target_dispositions':[dispositions[name] for name in sorted(dispositions)],
        'target_dispositions_validation_error':old_error or new_error}


class BudgetedExtractionProvider(BaseLLMProvider):
    def __init__(self,provider,budget,cache_dir,units,gate=None):
        self.provider=provider;self.budget=budget;self.model=provider.model
        if self.model!=budget.identity['model']:
            raise ValueError('production_role_actual_model_mismatch')
        self.cache_dir=Path(cache_dir);self.units={unit.unit_group.group_key:unit for unit in units}
        self.last_usage={};self.calls=0;self.raw_cache_hits=0;self.gate=gate or ExtractionDispatchGate()

    def is_available(self):return self.provider.is_available()
    def get_provider_name(self):return self.provider.get_provider_name()
    async def translate_tags(self,tags):raise RuntimeError('role_extraction_has_no_translation_write_route')

    @staticmethod
    def logical_keys(groups):
        keys=[]
        for group in groups:
            targets=[tag.get('raw_tag','') for tag in group.tags]
            if group.data_type_label and ': ' in group.data_type_label:
                try:targets=json.loads(group.data_type_label.split(': ',1)[1])
                except (ValueError,TypeError):pass
            context=sorted(canonical_source_key(tag.get('raw_tag','')) for tag in group.tags)
            keys.extend('role-target:'+canonical_fingerprint({'raw':canonical_source_key(raw),'context':context}) for raw in targets)
        return sorted(set(keys))

    def validate_saved(self,saved):
        """Rebuild the paid question, including groups absent from this resume batch."""
        from types import SimpleNamespace
        if saved.get('request_messages') is not None:
            expected=canonical_fingerprint({'model':self.model,'messages':saved['request_messages'],
                'temperature':saved['temperature'],'max_tokens':saved['max_tokens']})
            if expected!=saved['input_fingerprint']:raise ValueError('role_raw_question_identity_mismatch')
            groups=[SourceCandidateInputGroup(**row) for row in saved['request_groups']]
            current=_production_messages(extraction_messages(groups))
            if current!=saved['request_messages']:raise ValueError('role_raw_current_prompt_mismatch')
        else:
            rows=json.loads(saved['content'])['records']
            groups=[self.units[row['group_key']].unit_group for row in rows]
            expected=canonical_fingerprint({'model':self.model,'messages':_production_messages(extraction_messages(groups)),
                'temperature':0.0,'max_tokens':6000})
            if expected!=saved['input_fingerprint']:raise ValueError('role_raw_question_identity_mismatch')
        if saved.get('wire_messages'):
            wire=canonical_fingerprint(saved['wire_messages'])
            if wire!=saved.get('wire_fingerprint'):raise ValueError('role_raw_wire_identity_mismatch')
        error=None
        try:
            rows=json.loads(saved['content'])['records']
            if not isinstance(rows,list) or len(rows)!=len(groups):raise ValueError('role_response_group_count')
            by_key={row['group_key']:row for row in rows}
            if set(by_key)!={g.group_key for g in groups}:raise ValueError('role_response_group_identity')
            for group in groups:
                row=_adapt_response_record(by_key[group.group_key],SimpleNamespace(unit_group=group))
                verdict,candidates,*_=validate_extraction_record(row,group)
                if verdict.extraction_verdict.startswith('extraction_error'):raise ValueError('role_response_error_verdict')
                if group.data_origin in {COMPLETION_ORIGIN,COVERAGE_REPAIR_ORIGIN}:
                    targets=json.loads(group.data_type_label.split(': ',1)[1])
                    coverage=role_target_coverage(SimpleNamespace(raw_values=targets),{
                        'verdict':verdict.extraction_verdict,'candidates':[asdict(c) for c in candidates],
                        'validated_response':row})
                    if coverage['missing_raw_tags']:raise ValueError('role_response_missing_target_dispositions')
        except (ValueError,TypeError,KeyError,SourceNameCandidateExtractionError) as exc:error=str(exc)
        return groups,error

    def recover_saved(self,saved):
        groups,error=self.validate_saved(saved)
        response=saved.get('budget_response',{})
        self.budget.recover_response(key=response.get('key','role-extraction:'+saved['input_fingerprint']),
            reservation=response.get('reservation'),usage=saved.get('usage',{}),business_valid=error is None,
            logical_keys=self.logical_keys(groups))
        self.save_units(saved['content'])
        return error

    def adapted_content(self,content):
        try:
            payload=json.loads(content)
            rows=payload.get('records',[]) if isinstance(payload,dict) else []
        except (ValueError,TypeError):return content
        for index,row in enumerate(rows):
            if not isinstance(row,dict):continue
            unit=self.units.get(row.get('group_key'))
            if not unit:continue
            rows[index]=_adapt_response_record(row,unit)
        return json.dumps(payload,ensure_ascii=False)

    def save_units(self,content):
        try:
            payload=json.loads(self.adapted_content(content))
            rows=payload.get('records',[]) if isinstance(payload,dict) else []
        except (ValueError,TypeError):return
        for row in rows:
            if not isinstance(row,dict):continue
            unit=self.units.get(row.get('group_key'))
            if not unit:continue
            path=_unit_path(self.cache_dir,unit)
            previous=None
            if path.exists():
                previous,_=_read_unit_cache(path,unit,self.model)
                if _unit_answer_complete(unit,previous):continue
            try:
                verdict,candidates,*_=validate_extraction_record(row,unit.unit_group)
            except (ValueError,TypeError,SourceNameCandidateExtractionError) as original_error:
                # Validate siblings individually through the same F7a schema.
                # No malformed target is converted into a positive or unknown
                # answer. Missing targets remain available to bounded repair.
                valid=[]
                for candidate in row.get('candidates',[]) if isinstance(row.get('candidates'),list) else []:
                    try:
                        validate_extraction_record({**row,'candidates':[candidate]},unit.unit_group)
                    except (ValueError,TypeError,SourceNameCandidateExtractionError):continue
                    valid.append(candidate)
                if not valid:continue
                partial={**row,'candidates':valid}
                try:verdict,candidates,*_=validate_extraction_record(partial,unit.unit_group)
                except (ValueError,TypeError,SourceNameCandidateExtractionError):continue
                partial['partial_validation_error']=str(original_error)
                row=partial
            if verdict.extraction_verdict.startswith('extraction_error'):continue
            row=_merge_valid_target_answers(unit,row,candidates,previous)
            try:verdict,candidates,*_=validate_extraction_record(row,unit.unit_group)
            except (ValueError,TypeError,SourceNameCandidateExtractionError):continue
            _atomic_write_json(path,
                {**_record(unit,self.model,verdict,candidates,origin='existing_f7a_extractor_primary_model'),
                 'validated_response':row})

    def replay_saved_raw(self):
        recovered_before=sum(_unit_path(self.cache_dir,unit).exists() for unit in self.units.values())
        paths=[*(self.cache_dir/'raw').glob('*.json'),*(self.cache_dir/'raw'/'attempts').glob('*.json')]
        for path in sorted(paths):
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('model')!=self.model:continue
            if saved.get('request_messages') is not None:
                self.recover_saved(saved)
                continue
            try:
                rows=json.loads(saved['content'])['records']
                groups=[self.units[row['group_key']].unit_group for row in rows]
            except (ValueError,KeyError,TypeError):continue
            # Old raw envelopes predate stored request identities. Rebuild the
            # exact F7a question and prove its hash before accepting any row.
            messages=_production_messages(extraction_messages(groups))
            expected=canonical_fingerprint({'model':self.model,'messages':messages,
                'temperature':0.0,'max_tokens':6000})
            if expected==saved['input_fingerprint']:
                self.recover_saved(saved)
        return sum(_unit_path(self.cache_dir,unit).exists() for unit in self.units.values())-recovered_before

    async def complete_chat(self,messages,*,temperature=0.0,max_tokens=6000):
        messages=_production_messages(messages)
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
                if cached.get('validated_response') and _unit_answer_complete(unit,cached):
                    known[row['group_key']]=cached['validated_response']
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
        previous=[json.loads(p.read_text(encoding='utf-8')) for p in
            ([path] if path.exists() else [])+list((self.cache_dir/'raw'/'attempts').glob(signature+'.*.json'))]
        feedback=None
        if previous:
            cached=max(previous,key=lambda row:row.get('budget_response',{}).get('attempt',0))
            if cached.get('input_fingerprint')!=signature or cached.get('model')!=self.model:
                raise ValueError('role_raw_cache_identity_mismatch')
            feedback=self.recover_saved(cached)
            if feedback is None:
                self.raw_cache_hits+=1
                return self.adapted_content(cached['content'])
            if any((lambda unit: _unit_path(self.cache_dir,unit).exists() and
                    _unit_answer_complete(unit,_read_unit_cache(_unit_path(self.cache_dir,unit),unit,self.model)[0]))
                    (self.units[row['group_key']]) for row in requested):
                return await self.complete_chat(messages,temperature=temperature,max_tokens=max_tokens)
        self.gate.check()
        groups=[self.units[row['group_key']].unit_group for row in requested]
        wire=messages if feedback is None else [*messages,{'role':'user','content':
            'The previous response failed schema validation: '+feedback[:500]+'. Return the requested records using the allowed roles, source fields and exact supported raw names. Preserve uncertainty; do not invent identity.'}]
        reservation=self.budget.reserve('role-extraction:'+signature,wire,max_output_tokens=max_tokens,
            logical_keys=self.logical_keys(groups))
        self.provider.last_usage={}
        response_saved=False
        try:
            self.calls+=1
            content=await self.provider.complete_chat(wire,temperature=temperature,max_tokens=max_tokens)
            self.last_usage=dict(getattr(self.provider,'last_usage',{}))
            saved={'input_fingerprint':signature,'model':self.model,'content':content,'usage':self.last_usage,
                'request_messages':messages,'request_groups':[asdict(g) for g in groups],
                'temperature':temperature,'max_tokens':max_tokens,'wire_messages':wire,
                'wire_fingerprint':canonical_fingerprint(wire),'budget_response':self.budget.response_identity(reservation)}
            destination=path if not path.exists() else self.cache_dir/'raw'/'attempts'/f'{signature}.{reservation}.json'
            _atomic_write_json(destination,saved)
            response_saved=True
            self.save_units(content)
            _,validation_error=self.validate_saved(saved)
        except BaseException as exc:
            if not response_saved:self.budget.settle(reservation,getattr(self.provider,'last_usage',{}),success=False)
            self.gate.returned(exc)
            raise
        self.budget.settle(reservation,self.last_usage,success=validation_error is None)
        self.gate.returned()
        return self.adapted_content(content)


def extract_production_roles(units,*,provider,budget,cache_dir,batch_size=20,progress=None,workers=1,provider_factory=None,_gate=None):
    if type(batch_size) is not int or not 1 <= batch_size <= 50:
        raise ValueError('production_role_batch_size_invalid')
    if type(workers) is not int or workers not in (1,2):raise ValueError('production_role_workers_invalid')
    if workers==2:
        if provider_factory is None:raise ValueError('independent_provider_factory_required')
        return _extract_roles_two_workers(units,provider=provider,provider_factory=provider_factory,budget=budget,
            cache_dir=cache_dir,batch_size=batch_size,progress=progress)
    cache_dir=Path(cache_dir)
    wrapped=BudgetedExtractionProvider(provider,budget,cache_dir,units,gate=_gate)
    # A crash or a fixed response adapter may leave good paid raw replies
    # without a unit cache. Replay those first, including changed batch shapes.
    raw_recovered=wrapped.replay_saved_raw()
    records={};pending=[];cached=0;deterministic=0;blocked=None;canonical_reuse=0
    for unit in units:
        path=_unit_path(cache_dir,unit)
        if path.exists():
            value,variant=_read_unit_cache(path,unit,wrapped.model)
            canonical_reuse+=int(variant)
            records[unit.extraction_key]=value;cached+=1
            if not _unit_answer_complete(unit,value):pending.append(unit)
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
            if path.exists():records[unit.extraction_key]=_read_unit_cache(path,unit,wrapped.model)[0]
        if progress:progress({'completed_units':len(records),'total_units':len(units),'provider_calls':wrapped.calls,
            'budget':budget.summary(),'blocked':blocked})
        if blocked:break
    return {'schema_version':ROLE_SCHEMA,'records':records,'summary':{'total_units':len(units),'completed_units':len(records),
        'cache_hits':cached,'deterministic_units':deterministic,'new_provider_calls':wrapped.calls,'raw_cache_hits':wrapped.raw_cache_hits,
        'paid_raw_units_recovered_locally':raw_recovered,
        'original_question_case_variant_cache_hits':canonical_reuse,
        'blocked':blocked,'remaining_units':len(units)-len(records),'task_budget':budget.summary()}}


def _extract_roles_two_workers(units,*,provider,provider_factory,budget,cache_dir,batch_size,progress):
    from concurrent.futures import ThreadPoolExecutor
    if len({unit.extraction_key for unit in units})!=len(units):raise ValueError('duplicate_parallel_role_unit')
    # Recover a paid envelope before partitioning: its original request may
    # contain units assigned to both workers. Each unit then has one writer.
    recovered=BudgetedExtractionProvider(provider,budget,cache_dir,units).replay_saved_raw()
    gate=ExtractionDispatchGate();lock=Lock();latest={}
    providers=[provider_factory(),provider_factory()]
    if providers[0] is providers[1]:raise ValueError('shared_mutable_provider_forbidden')
    def report(index,value):
        with lock:
            latest[index]=value
            if progress:progress({'completed_units':sum(row['completed_units'] for row in latest.values()),
                'total_units':len(units),'provider_calls':sum(row['provider_calls'] for row in latest.values()),
                'budget':budget.summary(),'blocked':next((row['blocked'] for row in latest.values() if row['blocked']),None),
                'text_workers':2})
    def run(index):
        return extract_production_roles(units[index::2],provider=providers[index],budget=budget,cache_dir=cache_dir,
            batch_size=batch_size,progress=lambda value:report(index,value),_gate=gate)
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='pixiv-role') as executor:
        results=list(executor.map(run,range(2)))
    records={key:value for result in results for key,value in result['records'].items()}
    counters=('total_units','completed_units','cache_hits','deterministic_units','new_provider_calls',
        'raw_cache_hits','paid_raw_units_recovered_locally','original_question_case_variant_cache_hits','remaining_units')
    summary={key:sum(result['summary'][key] for result in results) for key in counters}
    summary['paid_raw_units_recovered_locally']+=recovered
    summary.update(blocked=next((result['summary']['blocked'] for result in results if result['summary']['blocked']),None),
        text_workers=2,task_budget=budget.summary())
    return {'schema_version':ROLE_SCHEMA,'records':records,'summary':summary}


def plan_contextual_role_extraction(consumer,vocabulary,role_facts):
    """One supplementary question per distinct real metadata tag context.

    Successful context-free answers remain reusable. Context answers apply
    only to the frozen aggregates that actually supplied the complete tag set;
    a same-name occurrence in another work cannot inherit them.
    """
    adapted=adapt_production_semantics(consumer,vocabulary,role_facts)
    signals=defaultdict(list)
    for signal in adapted.signals:
        if signal.origin_type=='pixiv_tag_observation':
            signals[signal.evidence_payload['aggregate_fingerprint']].append(signal)
    units={};mapping={};skipped=0
    for aggregate,rows in sorted(signals.items()):
        # Explicit parenthetical/accepted roles are already grounded. The
        # supplementary question addresses unresolved, generic-person or
        # low-confidence/no-name single-string interpretations.
        needs=any(row.role_hint in {'unknown','person'} and (
            row.status!='rejected' or row.evidence_payload.get('production_non_identity_reason')=='existing_extractor_non_name_verdict')
            for row in rows)
        if not needs:skipped+=1;continue
        selected=[row for row in rows if row.evidence_payload.get('production_non_identity_reason')!='accepted_general_search_term'
            and row.evidence_payload.get('source_field')!='popularity_tag']
        tags=tuple({'raw_tag':raw,'source_tag_kind':'provider_tag'} for raw in sorted({row.raw_value for row in selected},key=lambda raw:(canonical_source_key(raw),raw)))
        if not tags:skipped+=1;continue
        signature=canonical_fingerprint({'schema':'production_pixiv_contextual_roles_v1','tags':tags})
        extraction_key='production-context:'+signature
        mapping[aggregate]=extraction_key
        group=SourceCandidateInputGroup(group_key='a2-context:'+signature[:24],provider='pixiv',tags=tags,
            data_origin='production_metadata_contextual_supplement',source_work_id_present=True)
        units[extraction_key]=SourceExtractionUnit(extraction_key=extraction_key,normalized_value='metadata context '+signature,
            canonical_key=signature,raw_values=tuple(row['raw_tag'] for row in tags),provider='pixiv',source_field='pixiv_tag',
            role_hint=None,context_key=signature,language_hint=None,script_hint=None,occurrences=(),llm_required=True,
            deterministic_resolution='one_contextual_supplement',unit_group=group)
    return list(units.values()),mapping,{'contextual_units':len(units),'aggregate_occurrences':len(mapping),
        'already_grounded_aggregates':skipped,'context_derived_identity_equivalence':False}


def extract_contextual_production_roles(consumer,vocabulary,role_facts,*,provider,budget,cache_dir,progress=None,batch_size=5,unit_limit=0):
    if type(batch_size) is not int or not 1<=batch_size<=10:
        raise ValueError('production_context_batch_size_invalid')
    if type(unit_limit) is not int or unit_limit<0:raise ValueError('production_context_unit_limit_invalid')
    units,mapping,plan=plan_contextual_role_extraction(consumer,vocabulary,role_facts)
    if unit_limit:units=units[:unit_limit]
    extracted=extract_production_roles(units,provider=provider,budget=budget,cache_dir=cache_dir,batch_size=batch_size,progress=progress)
    return {**role_facts,'context_records':{**role_facts.get('context_records',{}),**extracted['records']},
        'context_by_aggregate':{**role_facts.get('context_by_aggregate',{}),
            **{aggregate:key for aggregate,key in mapping.items() if key in extracted['records']}},
        'context_summary':{**extracted['summary'],**plan,'selected_units':len(units)}}


def plan_contextual_role_completion(consumer,vocabulary,role_facts):
    """One residual completion after a partial context answer, never a loop."""
    from .source_name_candidate_extraction_service import is_meta_or_descriptive_rejection,popularity_suffix_prefix
    adapted=adapt_production_semantics(consumer,vocabulary,role_facts)
    grouped=defaultdict(list)
    for signal in adapted.signals:
        if signal.origin_type=='pixiv_tag_observation':
            grouped[signal.evidence_payload['aggregate_fingerprint']].append(signal)
    units={};mapping={};skipped=0;target_count=0;prior_context_count=0
    for aggregate,signals in sorted(grouped.items()):
        if aggregate in role_facts.get('completion_by_aggregate',{}):skipped+=1;continue
        original_key=role_facts.get('context_by_aggregate',{}).get(aggregate)
        prior_context_count+=int(original_key in role_facts.get('context_records',{}))
        targets=sorted({signal.raw_value for signal in signals if signal.role_hint in {'unknown','person'}
            and signal.evidence_payload.get('production_non_identity_reason')!='accepted_general_search_term'
            and not is_meta_or_descriptive_rejection(signal.raw_value) and not popularity_suffix_prefix(signal.raw_value)},
            key=lambda raw:(canonical_source_key(raw),raw))
        if not targets:continue
        tags=tuple({'raw_tag':raw,'source_tag_kind':'provider_tag'} for raw in sorted({signal.raw_value for signal in signals},
            key=lambda raw:(canonical_source_key(raw),raw)))
        signature=canonical_fingerprint({'schema':COMPLETION_ORIGIN,'tags':tags,'targets':targets})
        extraction_key='production-context-completion:'+signature
        mapping[aggregate]=extraction_key;target_count+=len(targets)
        group=SourceCandidateInputGroup(group_key='a2-residual:'+signature[:24],provider='pixiv',tags=tags,
            data_origin=COMPLETION_ORIGIN,source_work_id_present=True,
            data_type_label='Requested unresolved raw tags: '+json.dumps(targets,ensure_ascii=False))
        units[extraction_key]=SourceExtractionUnit(extraction_key=extraction_key,normalized_value='remaining roles '+signature,
            canonical_key=signature,raw_values=tuple(targets),provider='pixiv',source_field='pixiv_tag',role_hint=None,
            context_key=signature,language_hint=None,script_hint=None,occurrences=(),llm_required=True,
            deterministic_resolution='one_residual_context_completion',unit_group=group)
    return list(units.values()),mapping,{'residual_context_units':len(units),'aggregate_occurrences':len(mapping),
        'requested_tag_occurrences':target_count,'previously_completed_aggregates':skipped,
        'existing_context_answers_available':prior_context_count,
        'identity_equivalence_authorized':False}


def complete_contextual_production_roles(consumer,vocabulary,role_facts,*,provider,budget,cache_dir,progress=None,batch_size=5,unit_limit=0,workers=1,provider_factory=None):
    if type(batch_size) is not int or not 1<=batch_size<=10:raise ValueError('production_context_batch_size_invalid')
    if type(unit_limit) is not int or unit_limit<0:raise ValueError('production_context_unit_limit_invalid')
    units,mapping,plan=plan_contextual_role_completion(consumer,vocabulary,role_facts)
    if unit_limit:units=units[:unit_limit]
    extracted=extract_production_roles(units,provider=provider,budget=budget,cache_dir=cache_dir,batch_size=batch_size,progress=progress,
        workers=workers,provider_factory=provider_factory)
    by_key={unit.extraction_key:unit for unit in units}
    accounted={key:role_target_coverage(by_key[key],record) for key,record in extracted['records'].items()}
    return {**role_facts,'completion_records':{**role_facts.get('completion_records',{}),**extracted['records']},
        'completion_by_aggregate':{**role_facts.get('completion_by_aggregate',{}),
            **{aggregate:key for aggregate,key in mapping.items() if key in extracted['records']}},
        'completion_summary':{**extracted['summary'],**plan,'selected_units':len(units),
            'parsed_response_units':len(extracted['records']),
            'units_with_unaccounted_targets':sum(bool(row['missing_raw_tags']) for row in accounted.values()),
            'unaccounted_requested_tag_occurrences':sum(len(accounted[key]['missing_raw_tags'])
                for key in mapping.values() if key in accounted),
            'unit_response_count_is_not_target_completion':True}}


def role_target_coverage(unit,record):
    """A valid envelope or aggregate rejection count does not answer each tag."""
    from .production_pixiv_semantics import _context_candidate_matches
    raw_dispositions=record.get('validated_response',{}).get('target_dispositions',[])
    invalid_dispositions=not isinstance(raw_dispositions,list) or bool(
        record.get('validated_response',{}).get('target_dispositions_validation_error'))
    if not isinstance(raw_dispositions,list):raw_dispositions=[]
    by_raw=defaultdict(list)
    for row in raw_dispositions:
        if isinstance(row,dict) and row.get('raw_value') in unit.raw_values:
            by_raw[row['raw_value']].append(row)
        else:invalid_dispositions=True
    outcomes={};missing=[]
    for raw in unit.raw_values:
        matched=[row for row in record.get('candidates',[]) if _context_candidate_matches(row,raw)]
        if matched:
            outcomes[raw]={'disposition':'candidate','reported_roles':sorted({row['candidate_role'] for row in matched})}
            continue
        rows=by_raw.get(raw,[])
        if (len(rows)==1 and isinstance(rows[0].get('disposition'),str) and rows[0]['disposition'] in {'unknown','non_name'}
            and isinstance(rows[0].get('reason_code'),str) and rows[0]['reason_code'].strip()):
            outcomes[raw]={'disposition':rows[0]['disposition'],'reason_code':rows[0]['reason_code']}
        elif (not rows and not invalid_dispositions
            and record.get('verdict') in {'no_explicit_name','rejected_general_only','rejected_popularity_or_meta_only'}):
            # Unlike anonymous counts in a mixed positive answer, a validated
            # whole-group non-name verdict covers all names in that question.
            outcomes[raw]={'disposition':'non_name','reason_code':'f7a_whole_group_'+record['verdict']}
        else:missing.append(raw)
    return {'requested_raw_tags':list(unit.raw_values),'outcomes':outcomes,'missing_raw_tags':missing,
        'fully_accounted':not missing,'identity_equivalence_authorized':False}


def _original_completion_questions(consumer,vocabulary,role_facts,*,require_complete=False):
    # Reconstruct the exact original question, not a new interpretation of its
    # targets. Legacy caches remain immutable and their answered names survive.
    baseline={**role_facts,'completion_records':{},'completion_by_aggregate':{},
        'coverage_repair_records':{},'coverage_repair_by_aggregate':{}}
    units,mapping,_=plan_contextual_role_completion(consumer,vocabulary,baseline)
    if require_complete and set(mapping)-set(role_facts.get('completion_by_aggregate',{})):
        raise ValueError('expected_role_completion_mapping_missing')
    by_key={unit.extraction_key:unit for unit in units}
    grounded=set()
    for aggregate,key in role_facts.get('completion_by_aggregate',{}).items():
        if aggregate not in mapping:
            # The currently accepted baseline already supplies every role
            # needed here. No new question or historical target claim is made.
            grounded.add(aggregate);continue
        if mapping.get(aggregate)!=key or key not in by_key:
            raise ValueError('original_role_question_reconstruction_changed')
    return by_key,grounded


def plan_role_coverage_repair(consumer,vocabulary,role_facts):
    originals,grounded=_original_completion_questions(consumer,vocabulary,role_facts)
    # Preserve the whole finite denominator, but spend a shared limited budget
    # on currently unresolved, non-rejected names before already rejected tags.
    adapted=adapt_production_semantics(consumer,vocabulary,role_facts)
    unresolved=defaultdict(set)
    for signal in adapted.signals:
        if signal.origin_type=='pixiv_tag_observation' and signal.role_hint in {'unknown','person'} and signal.status!='rejected':
            unresolved[signal.evidence_payload['aggregate_fingerprint']].add(signal.raw_value)
    priority=defaultdict(int);priority_occurrences=0
    units={};mapping={};parents={};target_occurrences=0;already_attempted=0
    for aggregate,parent in sorted(role_facts.get('completion_by_aggregate',{}).items()):
        if aggregate in grounded:continue
        original=originals[parent];record=role_facts['completion_records'][parent]
        targets=role_target_coverage(original,record)['missing_raw_tags']
        previous_key=role_facts.get('coverage_repair_by_aggregate',{}).get(aggregate)
        previous=role_facts.get('coverage_repair_records',{}).get(previous_key)
        if previous:
            already_attempted+=1
            if previous.get('parent_extraction_key')!=parent:
                raise ValueError('role_coverage_repair_parent_changed')
            answered=previous.get('target_coverage',{}).get('outcomes',{})
            targets=[raw for raw in targets if raw not in answered]
        targets=[raw for raw in targets if raw not in role_facts.get('role_terminal_targets',{}).get(aggregate,{})]
        targets=[raw for raw in targets if raw not in role_facts.get('role_reused_target_answers',{}).get(aggregate,{})]
        if not targets:continue
        signature=canonical_fingerprint({'schema':COVERAGE_REPAIR_ORIGIN,'parent':parent,
            'parent_response':canonical_fingerprint(record),'tags':original.unit_group.tags,'targets':targets})
        key='production-role-coverage-repair:'+signature
        mapping[aggregate]=key;parents[key]=parent;target_occurrences+=len(targets)
        current_missing=len(set(targets)&unresolved[aggregate])
        priority[key]=max(priority[key],current_missing);priority_occurrences+=current_missing
        group=replace(original.unit_group,group_key='a2-role-coverage:'+signature[:24],
            data_origin=COVERAGE_REPAIR_ORIGIN,
            data_type_label='Requested unresolved raw tags: '+json.dumps(targets,ensure_ascii=False))
        units[key]=replace(original,extraction_key=key,normalized_value='missing role dispositions '+signature,
            canonical_key=signature,raw_values=tuple(targets),context_key=signature,unit_group=group,
            deterministic_resolution='bounded_missing_target_coverage_repair')
    ordered=sorted(units.values(),key=lambda unit:(-priority[unit.extraction_key],unit.extraction_key))
    return ordered,mapping,{'repair_units':len(units),'aggregate_occurrences':len(mapping),
        'requested_tag_occurrences':target_occurrences,'already_attempted_aggregates':already_attempted,
        'currently_unresolved_non_rejected_target_occurrences':priority_occurrences,
        'priority_policy':'current_unresolved_names_first_no_denominator_truncation',
        'currently_grounded_aggregates_without_new_question':len(grounded),
        'parent_extraction_keys':parents,'identity_equivalence_authorized':False}


def summarize_role_response_coverage(consumer,vocabulary,role_facts,*,require_complete=False):
    originals,grounded=_original_completion_questions(consumer,vocabulary,role_facts,require_complete=require_complete)
    counts=defaultdict(int);remaining=[];complete=0
    for aggregate,parent in sorted(role_facts.get('completion_by_aggregate',{}).items()):
        if aggregate in grounded:continue
        coverage=role_target_coverage(originals[parent],role_facts['completion_records'][parent])
        outcomes=dict(coverage['outcomes'])
        repair_key=role_facts.get('coverage_repair_by_aggregate',{}).get(aggregate)
        repair=role_facts.get('coverage_repair_records',{}).get(repair_key)
        if repair:
            if repair.get('parent_extraction_key')!=parent:raise ValueError('role_coverage_repair_parent_changed')
            for raw,value in repair.get('target_coverage',{}).get('outcomes',{}).items():
                if raw in coverage['missing_raw_tags']:outcomes[raw]=value
        for raw,terminal in role_facts.get('role_terminal_targets',{}).get(aggregate,{}).items():
            if raw in originals[parent].raw_values and raw not in outcomes:
                outcomes[raw]={'disposition':'attempt_limit_reached','reason_code':terminal['reason_code']}
        for raw,reused in role_facts.get('role_reused_target_answers',{}).get(aggregate,{}).items():
            if raw in originals[parent].raw_values and (raw not in outcomes or outcomes[raw]['disposition']=='attempt_limit_reached'):
                outcomes[raw]={'disposition':reused['disposition'],'reason_code':reused['reason_code']}
        missing=[raw for raw in originals[parent].raw_values if raw not in outcomes]
        for value in outcomes.values():counts[value['disposition']]+=1
        counts['unaccounted']+=len(missing)
        if missing:remaining.append({'aggregate_fingerprint':aggregate,'raw_tags':missing,
            'repair_attempted':repair is not None})
        else:complete+=1
    return {'counts':dict(counts),'requested_tag_occurrences':sum(counts.values()),
        'original_response_aggregate_count':len(role_facts.get('completion_by_aggregate',{})),
        'currently_grounded_aggregates_without_new_question':len(grounded),
        'historical_targets_not_rederived_for_grounded_aggregates':True,
        'fully_accounted_aggregates':complete,'unaccounted_aggregate_count':len(remaining),
        'unaccounted_targets':remaining,'response_completion_does_not_assert_identity':True}


def _merge_coverage_records(role_facts,records,mapping):
    for aggregate,key in mapping.items():
        previous_key=role_facts.get('coverage_repair_by_aggregate',{}).get(aggregate)
        previous=role_facts.get('coverage_repair_records',{}).get(previous_key)
        if key in records and previous and key!=previous_key:
            current=records[key]
            candidates={canonical_fingerprint(row):row for row in [*previous.get('candidates',[]),*current['candidates']]}
            current['candidates']=list(candidates.values())
            current['target_coverage']['outcomes']={**previous.get('target_coverage',{}).get('outcomes',{}),
                **current['target_coverage']['outcomes']}
            current['target_coverage']['requested_raw_tags']=sorted(set(
                previous.get('target_coverage',{}).get('requested_raw_tags',[])) |
                set(current['target_coverage']['requested_raw_tags']))
            current['inherited_valid_response_keys']=[*previous.get('inherited_valid_response_keys',[]),previous_key]
    return {**role_facts,'coverage_repair_records':{**role_facts.get('coverage_repair_records',{}),**records},
        'coverage_repair_by_aggregate':{**role_facts.get('coverage_repair_by_aggregate',{}),
            **{aggregate:key for aggregate,key in mapping.items() if key in records}}}


def _reuse_existing_target_answers(consumer,vocabulary,facts):
    """Account for valid non-name answers already applied by this adapter."""
    reused={aggregate:dict(rows) for aggregate,rows in facts.get('role_reused_target_answers',{}).items()}
    records={canonical_source_key(row['raw_value']):row for row in facts['records'].values()}
    for signal in adapt_production_semantics(consumer,vocabulary,facts).signals:
        if (signal.origin_type!='pixiv_tag_observation' or signal.status!='rejected'
            or signal.evidence_payload.get('production_non_identity_reason')!='existing_extractor_non_name_verdict'):
            continue
        record=records.get(canonical_source_key(signal.raw_value))
        if not record or record['candidates'] or record.get('model')!='gpt-4.1-mini':continue
        if (record.get('schema_version')!=ROLE_SCHEMA or record.get('prompt_version')!=PROMPT_VERSION
            or record.get('extractor_version')!=EXTRACTOR_VERSION):continue
        reused.setdefault(signal.evidence_payload['aggregate_fingerprint'],{})[signal.raw_value]={
            'disposition':'non_name','reason_code':'compatible_single_spelling_non_name_already_applied',
            'extraction_key':record['extraction_key'],'response_fingerprint':canonical_fingerprint(record),
            'original_context_response_claimed_complete':False,'new_provider_calls':0}
    return {**facts,'role_reused_target_answers':reused}


def repair_missing_role_coverage(consumer,vocabulary,role_facts,*,provider,budget,cache_dir,progress=None,
    batch_size=5,unit_limit=0,workers=1,provider_factory=None):
    if type(batch_size) is not int or not 1<=batch_size<=10:raise ValueError('production_context_batch_size_invalid')
    if type(unit_limit) is not int or unit_limit<0:raise ValueError('production_context_unit_limit_invalid')
    units,mapping,plan=plan_role_coverage_repair(consumer,vocabulary,role_facts)
    # Settle saved envelopes before any unit-cache shortcut, including a
    # response whose final attempt reached the limit before the crash.
    recovered=BudgetedExtractionProvider(provider,budget,cache_dir,units).replay_saved_raw()
    cached={}
    for unit in units:
        path=_unit_path(cache_dir,unit)
        if path.is_file():
            record,_=_read_unit_cache(path,unit,provider.model)
            cached[unit.extraction_key]={**record,'parent_extraction_key':plan['parent_extraction_keys'][unit.extraction_key],
                'target_coverage':role_target_coverage(unit,record)}
    if cached:role_facts=_merge_coverage_records(role_facts,cached,mapping)
    units,mapping,plan=plan_role_coverage_repair(consumer,vocabulary,role_facts)
    # Exhaustion belongs to the actual target, not to every other unit sharing
    # its next transport batch. Keep the finite denominator and paid attempts;
    # only this unresolved target receives an explicit terminal disposition.
    index=budget.logical_attempt_index()
    terminal={aggregate:dict(values) for aggregate,values in role_facts.get('role_terminal_targets',{}).items()}
    by_key={unit.extraction_key:unit for unit in units}
    for aggregate,key in mapping.items():
        unit=by_key[key]
        for raw in unit.raw_values:
            group=replace(unit.unit_group,data_type_label='Requested unresolved raw tags: '+json.dumps([raw],ensure_ascii=False))
            logical=BudgetedExtractionProvider.logical_keys([group])[0]
            attempts=index.get(logical,[])
            if len(attempts)>=3 and all(row['status']!='reserved' for row in attempts):
                terminal.setdefault(aggregate,{})[raw]={'reason_code':'three_prior_logical_attempts_exhausted',
                    'logical_key':logical,'attempt_ids':[row['id'] for row in attempts],
                    'identity_confirmed':False,'unknown_rejudged':False}
    role_facts={**role_facts,'role_terminal_targets':terminal}
    units,mapping,plan=plan_role_coverage_repair(consumer,vocabulary,role_facts)
    recovered+=BudgetedExtractionProvider(provider,budget,cache_dir,units).replay_saved_raw()
    extra_cached={}
    for unit in units:
        path=_unit_path(cache_dir,unit)
        if path.is_file():
            record,_=_read_unit_cache(path,unit,provider.model)
            extra_cached[unit.extraction_key]={**record,'parent_extraction_key':plan['parent_extraction_keys'][unit.extraction_key],
                'target_coverage':role_target_coverage(unit,record)}
    if extra_cached:role_facts=_merge_coverage_records(role_facts,extra_cached,mapping)
    cached.update(extra_cached)
    role_facts=_reuse_existing_target_answers(consumer,vocabulary,role_facts)
    units,mapping,plan=plan_role_coverage_repair(consumer,vocabulary,role_facts)
    if unit_limit:units=units[:unit_limit]
    extracted=extract_production_roles(units,provider=provider,budget=budget,cache_dir=cache_dir,
        batch_size=batch_size,progress=progress,workers=workers,provider_factory=provider_factory)
    by_key={unit.extraction_key:unit for unit in units}
    records={key:{**record,'parent_extraction_key':plan['parent_extraction_keys'][key],
        'target_coverage':role_target_coverage(by_key[key],record)} for key,record in extracted['records'].items()}
    result={**_merge_coverage_records(role_facts,records,mapping),
        'coverage_repair_summary':{**extracted['summary'],**{key:value for key,value in plan.items()
            if key!='parent_extraction_keys'},'selected_units':len(units),'prior_valid_unit_cache_reused':len(cached),
            'prior_paid_raw_units_recovered_locally':recovered}}
    result['role_response_coverage']=summarize_role_response_coverage(consumer,vocabulary,result)
    return result
