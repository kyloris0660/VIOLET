"""Explicit, source-scoped supersession of evidenced semantic conflicts."""
import json
from dataclasses import asdict,replace
from collections import defaultdict
from .pixiv_metadata_projection_service import canonical_fingerprint
from .source_metadata_registry_service import canonical_source_key


def signal_semantics(signal):
    return {key:getattr(signal,key) for key in ('signal_key','raw_value','role_hint','work_context_key','status','trust_tier')}


def correction_question_identity(unit):
    from .source_name_candidate_extraction_service import group_prompt_payload
    return canonical_fingerprint({k:v for k,v in group_prompt_payload(unit.unit_group).items() if k!='group_key'})


def correction_units(consumer,facts,requests):
    from .production_pixiv_role_extraction import CORRECTION_ORIGIN,SourceCandidateInputGroup,SourceExtractionUnit
    by_aggregate=defaultdict(list)
    for signal in consumer.signals:
        if signal.origin_type=='pixiv_tag_observation':by_aggregate[signal.evidence_payload['aggregate_fingerprint']].append(signal)
    units=[];links={};seen=set()
    for request in requests:
        aggregate=request['aggregate_fingerprint'];raws=request['raw_targets']
        rows=by_aggregate.get(aggregate,[]);by_raw={s.raw_value:s for s in rows}
        if (aggregate in seen or not raws or len(set(raws))!=len(raws) or not set(raws)<=by_raw.keys()
            or not request.get('conflict_evidence') or not request.get('authorization')):
            raise ValueError('semantic_correction_scope_or_evidence_invalid')
        seen.add(aggregate)
        before={raw:signal_semantics(by_raw[raw]) for raw in raws}
        if request.get('supersedes')!=before:raise ValueError('semantic_correction_previous_fact_changed')
        tags=tuple({'raw_tag':raw,'source_tag_kind':'provider_tag'} for raw in sorted(by_raw,key=lambda s:(canonical_source_key(s),s)))
        signature=canonical_fingerprint({'origin':CORRECTION_ORIGIN,'tags':tags,'request':request})
        key='production-role-correction:'+signature
        group=SourceCandidateInputGroup(group_key='a2-correction:'+signature[:24],provider='pixiv',tags=tags,
            data_origin=CORRECTION_ORIGIN,source_work_id_present=True,
            data_type_label='Requested unresolved raw tags: '+json.dumps(raws,ensure_ascii=False))
        units.append(SourceExtractionUnit(extraction_key=key,normalized_value='role correction '+signature,
            canonical_key=signature,raw_values=tuple(raws),provider='pixiv',source_field='pixiv_tag',role_hint=None,
            context_key=signature,language_hint=None,script_hint=None,occurrences=(),llm_required=True,
            deterministic_resolution='bounded_evidenced_correction',unit_group=group))
        links[aggregate]=key
    return units,links


def apply_semantic_corrections(consumer,facts):
    requests=facts.get('semantic_corrections',[])
    if not requests:return consumer
    from .production_pixiv_role_extraction import role_target_coverage,_identity
    from .source_name_candidate_extraction_service import validate_extraction_record
    from .source_concept_resolver_service import role_from_source_role,_trust_for_f7a_candidate
    from types import SimpleNamespace
    units,links=correction_units(consumer,facts,requests);by_key={u.extraction_key:u for u in units}
    requests={r['aggregate_fingerprint']:r for r in requests};replayed={}
    for aggregate,key in links.items():
        source_key=facts.get('correction_equivalent_sources',{}).get(key,key)
        if source_key not in by_key or correction_question_identity(by_key[source_key])!=correction_question_identity(by_key[key]):
            raise ValueError('semantic_correction_reuse_question_changed')
        record=facts.get('correction_records',{}).get(source_key)
        if record is None:raise ValueError('semantic_correction_answer_missing')
        unit=by_key[source_key]
        if any(record.get(k)!=v for k,v in _identity(unit,'gpt-4.1-mini').items()):
            raise ValueError('semantic_correction_answer_identity_changed')
        verdict,candidates,*_=validate_extraction_record(record['validated_response'],unit.unit_group)
        replay={'verdict':verdict.extraction_verdict,'candidates':[asdict(c) for c in candidates],
                'validated_response':record['validated_response']}
        if replay['candidates']!=record['candidates']:raise ValueError('semantic_correction_answer_projection_changed')
        coverage=role_target_coverage(unit,replay)
        if coverage['missing_raw_tags']:raise ValueError('semantic_correction_answer_incomplete')
        replayed[aggregate]=(replay,coverage)
    signals=[]
    for signal in consumer.signals:
        aggregate=signal.evidence_payload.get('aggregate_fingerprint');request=requests.get(aggregate)
        if not request or signal.raw_value not in request['raw_targets']:
            signals.append(signal);continue
        if signal.parenthetical_context or signal.evidence_payload.get('production_role_hint_evidence'):
            raise ValueError('semantic_correction_cannot_override_independent_strong_fact')
        replay,coverage=replayed[aggregate];outcome=coverage['outcomes'][signal.raw_value]
        matches=[c for c in replay['candidates'] if canonical_source_key(c['raw_value'])==canonical_source_key(signal.raw_value)]
        # F7a adds deterministic fragments from *other* tags in the context.
        # The explicitly requested literal's model answer is the correction;
        # a sibling's reversed parenthesis or popularity prefix is not a
        # second answer to that literal. The original signal's own structured
        # parenthesis/accepted evidence was already protected above.
        explicit=[c for c in matches if c.get('evidence_payload',{}).get('llm_structured_extraction')]
        if explicit:matches=explicit
        roles={role_from_source_role(c['candidate_role']) for c in matches}
        role=signal.role_hint;context=signal.work_context_key;status=signal.status;trust=signal.trust_tier
        if len(roles)==1 and next(iter(roles)) in {'character','person','work'}:
            best=max(matches,key=lambda c:c['confidence']);role=next(iter(roles))
            trust,status=_trust_for_f7a_candidate(SimpleNamespace(**{**best,'status':'active'}))
            contexts={canonical_source_key(c.get('work_context_key')) for c in matches if c.get('work_context_key')}
            context=next(iter(contexts)) if len(contexts)==1 and role!='work' else None
        elif outcome['disposition']=='non_name':role='unknown';trust=status='rejected';context=None
        elif outcome['disposition']=='unknown' or roles=={'unknown'}:
            role='unknown';status='needs_review';trust='low';context=None
        elif len(roles)==1 and next(iter(roles))=='source_title':
            role='source_title';status='needs_review';trust='low';context=None
        else:raise ValueError('semantic_correction_conflicting_roles:'+signal.raw_value+':'+','.join(sorted(roles)))
        evidence={**signal.evidence_payload,'production_semantic_correction':{
            'request_fingerprint':canonical_fingerprint(request),'extraction_key':links[aggregate],
            'supersedes':request['supersedes'][signal.raw_value],'outcome':outcome,
            'response_fingerprint':canonical_fingerprint(replay),'identity_equivalence_authorized':False}}
        evidence.pop('production_model_proposed_context',None)
        if context:evidence['production_model_proposed_context']=context
        signals.append(replace(signal,role_hint=role,work_context_key=context,status=status,trust_tier=trust,evidence_payload=evidence))
    return replace(consumer,signals=tuple(signals))
