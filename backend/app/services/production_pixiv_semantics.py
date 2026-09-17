"""Standard roles and semantic work context for the production Pixiv adapter.

Accepted localization can supply role/context hints and search equivalence.
It never supplies identity equivalence, creator ownership, or a cannot-link
override. Artwork/page provenance is distinct from a fictional work context.
"""
from collections import defaultdict
from dataclasses import replace
import json

from .pixiv_metadata_projection_service import canonical_fingerprint
from .source_metadata_registry_service import canonical_source_key, parse_parenthetical_name

VOCABULARY_SCHEMA = 'violet.production-pixiv-semantic-vocabulary.v1'


def _context_candidate_matches(candidate,raw_value):
    # F7a may preserve a second observed spelling as display/normalized value.
    # Reuse only its role in this exact metadata group; identity still requires
    # the resolver's separate guarded decision. Never apply this across groups.
    key=canonical_source_key(raw_value)
    return bool(key) and any(canonical_source_key(candidate.get(field))==key
        for field in ('raw_value','display_name','normalized_value','canonical_key'))


def _specific_role_candidates(candidates):
    """An unknown prefix observation cannot contradict a typed name answer.

    F7a preserves deterministic popularity-prefix candidates alongside the
    model's contextual answer. Keep real disagreements between typed roles;
    only remove non-specific observations when a typed answer exists.
    """
    from .source_concept_resolver_service import role_from_source_role
    typed=[row for row in candidates if role_from_source_role(row['candidate_role'])
        in {'character','person','work','artist'}]
    return typed or candidates


def build_semantic_vocabulary(translation_rows, taxonomy_rows=()):
    from ..utils.search_parser import _translation_alias_trusted_for_search
    hints=defaultdict(list)
    for row in translation_rows:
        if row.get('status')=='rejected' or not _translation_alias_trusted_for_search(row):
            continue
        category=str(row.get('category') or 'general').casefold()
        role={'copyright':'work','character':'character','artist':'artist'}.get(category,'general')
        raw_aliases=row.get('aliases_json') or []
        if isinstance(raw_aliases,str):
            try:raw_aliases=json.loads(raw_aliases)
            except (TypeError,ValueError):raw_aliases=[]
        aliases=[row.get('canonical_name'),row.get('display_name'),*(raw_aliases if isinstance(raw_aliases,list) else [])]
        base,context=parse_parenthetical_name(row.get('canonical_name') or '') or (None,None)
        evidence={'role':role,'canonical_name':row['canonical_name'],'context':context if role=='character' else None,
            'source':'accepted_search_translation','translation_source':row.get('source'),
            'identity_equivalence_authorized':False}
        for alias in aliases:
            key=canonical_source_key(alias)
            if key:hints[key].append(evidence)
    for row in taxonomy_rows:
        summary=row.get('source_summary') or {}
        if row.get('status')!='resolved' or summary.get('selected_reason')!='external_tag_category_lookup':
            continue
        role={'copyright':'work','character':'character','artist':'artist','general':'general'}.get(row.get('candidate_namespace'))
        if not role:continue
        key=canonical_source_key(row.get('raw_tag') or row.get('normalized_tag'))
        if key:hints[key].append({'role':role,'context':None,'canonical_name':key,
            'source':'existing_external_taxonomy','evidence':summary,'identity_equivalence_authorized':False})
    payload={'schema_version':VOCABULARY_SCHEMA,'hints':{key:sorted({canonical_fingerprint(row):row for row in rows}.values(),
        key=canonical_fingerprint) for key,rows in sorted(hints.items())}}
    payload['canonical_fingerprint']=canonical_fingerprint(payload)
    return payload


def adapt_production_semantics(consumer, vocabulary=None, role_facts=None):
    hints={}
    if vocabulary is not None:
        payload={k:v for k,v in vocabulary.items() if k!='canonical_fingerprint'}
        if payload.get('schema_version')!=VOCABULARY_SCHEMA or canonical_fingerprint(payload)!=vocabulary.get('canonical_fingerprint'):
            raise ValueError('production_pixiv_vocabulary_fingerprint_invalid')
        hints=vocabulary['hints']
    adapted=[]
    role_records={}
    completion_outcomes={}
    if (role_facts and role_facts.get('completion_by_aggregate') and any(
        row.get('validated_response') for row in role_facts.get('completion_records',{}).values())):
        # Reconstruct targets from source context; stored target_coverage is
        # only a report. The baseline reconstruction contains no completions.
        from .production_pixiv_role_extraction import _original_completion_questions,role_target_coverage
        from .source_name_candidate_extraction_service import validate_extraction_record
        from dataclasses import asdict
        originals,grounded=_original_completion_questions(consumer,vocabulary,role_facts)
        for aggregate,key in role_facts['completion_by_aggregate'].items():
            if aggregate in grounded:continue
            record=role_facts['completion_records'][key]
            if not record.get('validated_response'):continue
            verdict,rows,*_=validate_extraction_record(record['validated_response'],originals[key].unit_group)
            completion_outcomes[aggregate]=role_target_coverage(originals[key],{
                'verdict':verdict.extraction_verdict,'candidates':[asdict(row) for row in rows],
                'validated_response':record['validated_response']})['outcomes']
    if role_facts:
        if role_facts.get('schema_version')!='violet.production-pixiv-role-result.v1':
            raise ValueError('production_role_facts_schema_invalid')
        for value in role_facts['records'].values():
            role_records[canonical_source_key(value['raw_value'])]=value
    for signal in consumer.signals:
        if signal.role_hint=='artist':
            adapted.append(signal);continue
        evidence={**signal.evidence_payload,'production_original_context':signal.work_context_key,
            'production_candidate_scope':'pixiv:work:'+signal.evidence_payload['work_id']}
        # Pixiv stable work/page keys describe the artwork. The original value
        # remains in evidence; parenthetical franchise context is retained.
        context=signal.parenthetical_context
        role=signal.role_hint;trust=signal.trust_tier;status=signal.status
        if signal.origin_type=='pixiv_tag_observation' and status!='rejected':
            candidates=hints.get(canonical_source_key(signal.raw_value),[])
            # A provider's generic category for a bare spelling does not prove
            # that the same Pixiv tag is descriptive in this artwork context.
            candidates=[row for row in candidates if not (
                row['role']=='general' and row['source']=='existing_external_taxonomy')]
            roles={row['role'] for row in candidates}
            contexts={canonical_source_key(row.get('context')) for row in candidates if row.get('context')}
            evidence['production_role_hint_evidence']=candidates
            if role=='unknown' and len(roles)==1:
                candidate_role=next(iter(roles))
                if candidate_role in ('character','work'):
                    role=candidate_role;trust='medium'
                    if not context and len(contexts)==1:context=next(iter(contexts))
                elif candidate_role=='general':
                    trust=status='rejected'
                    evidence['production_non_identity_reason']='accepted_general_search_term'
                else:
                    evidence['production_non_identity_reason']='tag_does_not_prove_creator_ownership'
            elif len(roles)>1:
                evidence['production_non_identity_reason']='conflicting_semantic_role_hints'
            if role=='unknown' and not candidates:
                fact=role_records.get(canonical_source_key(signal.raw_value))
                if fact:
                    from .source_concept_resolver_service import role_from_source_role,_trust_for_f7a_candidate
                    from types import SimpleNamespace
                    matching=_specific_role_candidates([row for row in fact['candidates']
                        if canonical_source_key(row['raw_value'])==canonical_source_key(signal.raw_value)])
                    inferred_roles={role_from_source_role(row['candidate_role']) for row in matching}
                    evidence['production_role_extraction']=fact
                    if len(inferred_roles)==1 and next(iter(inferred_roles)) in ('character','person','work'):
                        role=next(iter(inferred_roles))
                        trust,status=_trust_for_f7a_candidate(SimpleNamespace(**{**matching[0],'status':'active'}))
                        proposed={canonical_source_key(row.get('work_context_key')) for row in matching if row.get('work_context_key')}
                        if not context and len(proposed)==1:
                            context=next(iter(proposed));evidence['production_model_proposed_context']=context
                    elif fact['verdict'] in ('rejected_general_only','rejected_popularity_or_meta_only','no_explicit_name'):
                        trust=status='rejected'
                        evidence['production_non_identity_reason']='existing_extractor_non_name_verdict'
            contextual=None
            if role_facts and role_facts.get('context_by_aggregate'):
                context_key=role_facts['context_by_aggregate'].get(signal.evidence_payload.get('aggregate_fingerprint'))
                contextual=role_facts.get('context_records',{}).get(context_key)
            if role_facts and role_facts.get('completion_by_aggregate'):
                completion_key=role_facts['completion_by_aggregate'].get(signal.evidence_payload.get('aggregate_fingerprint'))
                completed=role_facts.get('completion_records',{}).get(completion_key)
                if completed and role in {'unknown','person'} and any(_context_candidate_matches(row,signal.raw_value)
                    for row in completed['candidates']):contextual=completed
                outcome=completion_outcomes.get(signal.evidence_payload.get('aggregate_fingerprint'),{}).get(signal.raw_value,{})
                if (outcome.get('disposition')=='non_name' and role=='unknown' and not candidates
                    and not (contextual and any(_context_candidate_matches(row,signal.raw_value)
                        for row in contextual['candidates']))):
                    trust=status='rejected';contextual=None
                    evidence['production_non_identity_reason']='explicit_completion_target_non_name'
                    evidence['production_completion_target_outcome']={'extraction_key':completion_key,'outcome':outcome}
                repair_key=role_facts.get('coverage_repair_by_aggregate',{}).get(signal.evidence_payload.get('aggregate_fingerprint'))
                repair=role_facts.get('coverage_repair_records',{}).get(repair_key)
                if (completed and repair and role in {'unknown','person'}
                    and repair.get('parent_extraction_key')==completion_key
                    and signal.raw_value in repair.get('target_coverage',{}).get('requested_raw_tags',[])
                    and not any(_context_candidate_matches(row,signal.raw_value) for row in completed['candidates'])):
                    outcome=repair['target_coverage'].get('outcomes',{}).get(signal.raw_value,{})
                    if any(_context_candidate_matches(row,signal.raw_value) for row in repair['candidates']):
                        contextual=repair
                    elif outcome.get('disposition')=='non_name' and not candidates:
                        trust=status='rejected';contextual=None
                        evidence['production_non_identity_reason']='explicit_target_coverage_non_name'
                    evidence['production_role_coverage_repair']={'extraction_key':repair_key,
                        'parent_extraction_key':completion_key,'outcome':outcome}
            if contextual and not candidates:
                matches=_specific_role_candidates([row for row in contextual['candidates']
                    if _context_candidate_matches(row,signal.raw_value)])
                from .source_concept_resolver_service import role_from_source_role,_trust_for_f7a_candidate
                from types import SimpleNamespace
                roles={role_from_source_role(row['candidate_role']) for row in matches}
                if len(roles)==1 and next(iter(roles)) in {'character','person','work'}:
                    best=max(matches,key=lambda row:row['confidence'])
                    role=next(iter(roles));trust,status=_trust_for_f7a_candidate(SimpleNamespace(**{**best,'status':'active'}))
                    contexts={canonical_source_key(row.get('work_context_key') or row.get('parenthetical_context'))
                        for row in matches if row.get('work_context_key') or row.get('parenthetical_context')}
                    if not context and len(contexts)==1:
                        context=next(iter(contexts));evidence['production_model_proposed_context']=context
                    evidence['production_contextual_role_extraction']=contextual
            if role=='work':context=None
        adapted.append(replace(signal,role_hint=role,work_context_key=context,trust_tier=trust,status=status,evidence_payload=evidence))
    # A tag's presence proves source provenance, not that it names a work.
    # Saved model answers can use clothing/general tags as work_context.
    # Require a typed work observation in the same artwork before retaining
    # that proposed context. Source parentheses and accepted vocabulary
    # contexts are independent evidence and are not model proposals.
    def validate_contexts(rows):
        works_by_scope=defaultdict(set)
        for signal in rows:
            if signal.role_hint=='work' and signal.status!='rejected' and signal.trust_tier!='rejected':
                works_by_scope[signal.evidence_payload['work_id']].add(canonical_source_key(signal.raw_value))
        verified=[]
        for signal in rows:
            evidence=signal.evidence_payload
            proposed=evidence.get('production_model_proposed_context')
            if proposed and signal.work_context_key==proposed and proposed not in works_by_scope[evidence['work_id']]:
                evidence={**evidence,'production_rejected_model_context':{
                    'value':proposed,'reason':'no_typed_work_observation_in_same_artwork'}}
                signal=replace(signal,work_context_key=None,evidence_payload=evidence)
            verified.append(signal)
        return verified
    # Supersession is checked against the actual previous public semantics,
    # including rejection of unsupported model-proposed work contexts.
    adapted=validate_contexts(adapted)
    if role_facts and role_facts.get('semantic_corrections'):
        from .production_pixiv_corrections import apply_semantic_corrections
        adapted=validate_contexts(apply_semantic_corrections(replace(consumer,signals=tuple(adapted)),role_facts).signals)
    identity=[{'key':s.signal_key,'role':s.role_hint,'context':s.work_context_key,'trust':s.trust_tier,
               'status':s.status,'evidence':s.evidence_payload} for s in adapted]
    return replace(consumer,signals=tuple(adapted),input_fingerprint=canonical_fingerprint({
        'base_input':consumer.input_fingerprint,'production_semantics':identity}))
