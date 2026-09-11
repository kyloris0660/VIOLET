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
                    matching=[row for row in fact['candidates'] if canonical_source_key(row['raw_value'])==canonical_source_key(signal.raw_value)]
                    inferred_roles={role_from_source_role(row['candidate_role']) for row in matching}
                    evidence['production_role_extraction']=fact
                    if len(inferred_roles)==1 and next(iter(inferred_roles)) in ('character','person','work'):
                        role=next(iter(inferred_roles))
                        trust,status=_trust_for_f7a_candidate(SimpleNamespace(**{**matching[0],'status':'active'}))
                        proposed={canonical_source_key(row.get('work_context_key')) for row in matching if row.get('work_context_key')}
                        if not context and len(proposed)==1:context=next(iter(proposed))
                    elif fact['verdict'] in ('rejected_general_only','rejected_popularity_or_meta_only','no_explicit_name'):
                        trust=status='rejected'
                        evidence['production_non_identity_reason']='existing_extractor_non_name_verdict'
            contextual=None
            if role_facts and role_facts.get('context_by_aggregate'):
                context_key=role_facts['context_by_aggregate'].get(signal.evidence_payload.get('aggregate_fingerprint'))
                contextual=role_facts.get('context_records',{}).get(context_key)
            if contextual and not candidates:
                matches=[row for row in contextual['candidates']
                    if canonical_source_key(row['raw_value'])==canonical_source_key(signal.raw_value)]
                from .source_concept_resolver_service import role_from_source_role,_trust_for_f7a_candidate
                from types import SimpleNamespace
                roles={role_from_source_role(row['candidate_role']) for row in matches}
                if len(roles)==1 and next(iter(roles)) in {'character','person','work'}:
                    best=max(matches,key=lambda row:row['confidence'])
                    role=next(iter(roles));trust,status=_trust_for_f7a_candidate(SimpleNamespace(**{**best,'status':'active'}))
                    contexts={canonical_source_key(row.get('work_context_key')) for row in matches if row.get('work_context_key')}
                    if not context and len(contexts)==1:context=next(iter(contexts))
                    evidence['production_contextual_role_extraction']=contextual
            if role=='work':context=None
        adapted.append(replace(signal,role_hint=role,work_context_key=context,trust_tier=trust,status=status,evidence_payload=evidence))
    identity=[{'key':s.signal_key,'role':s.role_hint,'context':s.work_context_key,'trust':s.trust_tier,
               'status':s.status,'evidence':s.evidence_payload} for s in adapted]
    return replace(consumer,signals=tuple(adapted),input_fingerprint=canonical_fingerprint({
        'base_input':consumer.input_fingerprint,'production_semantics':identity}))
