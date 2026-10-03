"""Preserve historical rows while deriving an explicit current role view.

The source inventory is produced by the native raw/ledger replayer. This
module does not grant source authority to an uploaded diagnostic or a cache
ticket. The release gate independently replays all current projections.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

from .pixiv_metadata_projection_service import canonical_fingerprint
from .production_pixiv_role_sources import candidate_identity, validate_record_projection, requested_targets,original_target_accounting,logical_target
from .source_name_candidate_extraction_service import SourceCandidateInputGroup, validate_extraction_record

RECORD_KINDS = ('records', 'context_records', 'completion_records', 'coverage_repair_records', 'correction_records')
MAPPINGS = {'context_by_aggregate': 'context_records', 'completion_by_aggregate': 'completion_records',
            'coverage_repair_by_aggregate': 'coverage_repair_records'}
VIEW_SCHEMA = 'violet.production-pixiv-original-role-current-view.v1'


def original_role_attempt_index(ledger,original_requests):
    """Count spent targets across batches using real reconstructed questions.

    This is read-only accounting, not eligibility or additional authority.
    A failed/missing answer still consumed its original attempt.
    """
    from .production_pixiv_release_provenance import replay_role_request_messages
    from .production_pixiv_role_sources import role_logical_keys
    by_id={row['id']:row for row in ledger['calls']};result=defaultdict(dict)
    for row in ledger['calls']:
        for logical in row.get('logical_keys',[]):result[logical][row['id']]=row
    for fingerprint,request in original_requests.items():
        groups=[SourceCandidateInputGroup(**g) for g in request['request_groups']]
        actual=canonical_fingerprint({'model':'gpt-4.1-mini','messages':replay_role_request_messages(groups),
            'temperature':0.0,'max_tokens':6000})
        if actual!=fingerprint:raise ValueError('role_attempt_original_request_changed')
        expected={row['id'] for row in ledger['calls'] if row['key']=='role-extraction:'+fingerprint}
        if set(request['attempt_ids'])!=expected:raise ValueError('role_attempt_original_call_set_changed')
        for logical in role_logical_keys(groups):
            for ticket in expected:result[logical][ticket]=by_id[ticket]
    return {key:list(attempts.values()) for key,attempts in result.items()}


def historical_role_facts(facts):
    result={k: v for k, v in facts.items() if k not in {'source_recovery', 'current_record_projections',
        'current_role_terminal_targets','current_role_response_coverage','additional_role_records','current_record_mappings'}}
    for kind,rows in facts.get('additional_role_records',{}).items():
        if kind not in RECORD_KINDS or set(rows)&set(result.get(kind,{})):
            raise ValueError('role_current_additional_record_collision')
        result[kind]={**result.get(kind,{}),**rows}
    return result


def current_role_facts(facts):
    """One filter used before roles, work context, correction and graph inputs."""
    recovery = facts.get('source_recovery')
    if recovery is None:
        return facts
    if recovery.get('schema_version') != VIEW_SCHEMA:
        raise ValueError('role_current_source_view_schema_changed')
    if recovery.get('fixed_record_denominator') != sum(len(facts.get(kind, {})) for kind in RECORD_KINDS):
        raise ValueError('role_current_source_view_fixed_denominator_changed')
    if set(facts.get('current_record_mappings', {})) - set(MAPPINGS):
        raise ValueError('role_current_mapping_kind_changed')
    history=historical_role_facts(facts)
    originals = {k: v for kind in RECORD_KINDS for k, v in history.get(kind, {}).items()}
    entries = recovery.get('records', {})
    if set(entries) != set(originals):
        raise ValueError('role_current_source_view_denominator_changed')
    projections = facts.get('current_record_projections', {})
    if set(projections) - set(originals):
        raise ValueError('role_current_source_view_unknown_projection')
    permitted = {'current_adopted', 'superseded_by_valid_answer', 'archive_only_unavailable'}
    for key, entry in entries.items():
        if (entry.get('original_record_fingerprint') != canonical_fingerprint(originals[key])
                or entry.get('disposition') not in permitted):
            raise ValueError('role_current_source_view_original_changed')
        current = projections.get(key, originals[key])
        if entry.get('current_projection_fingerprint') != canonical_fingerprint(current):
            raise ValueError('role_current_source_view_projection_changed')
    result = historical_role_facts(facts)
    for kind in RECORD_KINDS:
        result[kind] = {key: projections.get(key, row) for key, row in history.get(kind, {}).items()
                        if entries[key]['disposition'] == 'current_adopted'}
    for mapping, kind in MAPPINGS.items():
        mappings={**facts.get(mapping,{}),**facts.get('current_record_mappings',{}).get(mapping,{})}
        if set(facts.get('current_record_mappings',{}).get(mapping,{}).values())-set(result[kind]):
            raise ValueError('role_current_mapping_unadmitted_record')
        result[mapping] = {aggregate: key for aggregate, key in mappings.items() if key in result[kind]}
    if 'role_reused_target_answers' in facts:
        result['role_reused_target_answers']={aggregate:{raw:answer for raw,answer in answers.items()
            if answer.get('extraction_key') in result.get('records',{})}
            for aggregate,answers in facts['role_reused_target_answers'].items()}
    if 'current_role_terminal_targets' in facts:
        result['role_terminal_targets']={aggregate:dict(answers) for aggregate,answers in facts.get('role_terminal_targets',{}).items()}
        for aggregate,answers in facts['current_role_terminal_targets'].items():
            result['role_terminal_targets'].setdefault(aggregate,{}).update(answers)
    return result


def adopt_role_coverage_answers(facts,records,mapping):
    """Append real new answers without replacing any original row or mapping."""
    if not facts.get('source_recovery'):raise ValueError('role_current_recovery_view_required')
    if set(mapping.values())-set(records):raise ValueError('role_current_new_answer_missing')
    originals=historical_role_facts(facts);kind='coverage_repair_records'
    additional=deepcopy(facts.get('additional_role_records',{}));projections=deepcopy(facts.get('current_record_projections',{}))
    entries=deepcopy(facts['source_recovery']['records'])
    for key,record in records.items():
        if record.get('extraction_key')!=key or not record.get('unit_source_proofs'):
            raise ValueError('role_current_new_answer_source_proof_missing')
        if any(key in originals.get(other, {}) for other in RECORD_KINDS if other != kind):
            raise ValueError('role_current_additional_record_collision')
        if key in originals.get(kind,{}):
            if (record == projections.get(key, originals[kind][key])
                    and entries[key]['disposition'] == 'current_adopted'):
                continue
            projections[key]=deepcopy(record);original=originals[kind][key]
        else:
            additional.setdefault(kind,{})[key]=deepcopy(record);original=record
        entries[key]={'kind':kind,'disposition':'current_adopted','original_record_fingerprint':canonical_fingerprint(original),
            'current_projection_fingerprint':canonical_fingerprint(record),'direct_original_source_count':len(record['unit_source_proofs']),
            'legitimate_inheritance':record.get('inherited_valid_response_keys',[]),'rejected_inheritance':[],
            'derived_projection':key in projections,'currently_needed_original_input':True,'identity_eligibility_changed':False}
    mappings=deepcopy(facts.get('current_record_mappings',{}));mappings.setdefault('coverage_repair_by_aggregate',{}).update(mapping)
    result={**facts,'additional_role_records':additional,'current_record_projections':projections,'current_record_mappings':mappings,
        'source_recovery':{**facts['source_recovery'],'records':entries}}
    current_role_facts(result)
    return result


def verify_archive_nonuse(signals,facts):
    archived={key for key,row in facts.get('source_recovery',{}).get('records',{}).items()
        if row['disposition']=='archive_only_unavailable'}
    def inspect(value):
        if isinstance(value,dict):
            if value.get('extraction_key') in archived:
                raise ValueError('role_archive_source_entered_current_semantics')
            for child in value.values():inspect(child)
        elif isinstance(value,(list,tuple)):
            for child in value:inspect(child)
    for signal in signals:inspect(signal.evidence_payload)
    return {'archived_records':len(archived),'signals_checked':len(signals),'archived_current_role_inputs':0}


def derive_current_role_view(facts, inventory):
    """Derive unit projections; missing targets and illegal inheritance survive."""
    from .production_pixiv_role_extraction import role_target_coverage, _merge_valid_target_answers, BudgetedExtractionProvider
    history=historical_role_facts(facts)
    originals = {k: v for kind in RECORD_KINDS for k, v in history.get(kind, {}).items()}
    if set(inventory['records']) != set(originals):
        raise ValueError('role_source_inventory_denominator_changed')
    mapped = {key for mapping in MAPPINGS for key in {**facts.get(mapping,{}),
        **facts.get('current_record_mappings',{}).get(mapping,{})}.values()}
    # Global spelling facts and correction answers are still active inputs;
    # contextual history with no current mapping is kept as superseded history.
    needed = {*facts.get('records', {}), *facts.get('correction_records', {}), *mapped}
    entries = {}; projections = {}; targets = []
    for kind in RECORD_KINDS:
        for key, original in history.get(kind, {}).items():
            row = inventory['records'][key]
            if row['original_record_fingerprint'] != canonical_fingerprint(original):
                raise ValueError('role_source_inventory_original_changed')
            sources = list(row['direct_sources']); inheritance_errors = []
            parents = []
            for parent_key in original.get('inherited_valid_response_keys', []):
                parent = originals.get(parent_key)
                inherited = inventory['records'].get(parent_key, {}).get('direct_sources', [])
                if (not parent or not inherited or parent.get('parent_extraction_key') != original.get('parent_extraction_key')
                        or not sources or any(s['group']['tags'] != sources[0]['group']['tags'] for s in inherited)):
                    inheritance_errors.append({'key': parent_key, 'error': 'different_original_question_or_context'})
                    continue
                parents.append(parent_key);sources.extend(inherited)
            deterministic = original.get('origin') == 'existing_f7a_deterministic'
            current = deepcopy(original);error = None;derived = False
            if sources:
                try:
                    validate_record_projection(current, sources)
                    if inheritance_errors:
                        raise ValueError('role_source_wrong_question_inheritance')
                except ValueError as exc:
                    error = str(exc)
                    # This is a new projection of immutable real answers. It
                    # never edits an illegal model candidate into a valid one.
                    direct = sorted(row['direct_sources'],key=lambda s:s['unit_source_proof'].get('original_call_order',0))
                    group = SourceCandidateInputGroup(**direct[0]['group'])
                    unit = SimpleNamespace(unit_group=group, raw_values=requested_targets(group))
                    combined = None
                    for source in direct:
                        response = source['answer']['validated_response']
                        verdict, candidates, *_ = validate_extraction_record(response, group)
                        merged = _merge_valid_target_answers(unit, response, candidates, combined)
                        verdict, candidates, *_ = validate_extraction_record(merged, group)
                        combined = {'verdict': verdict.extraction_verdict,
                            'candidates': [asdict(c) for c in candidates], 'validated_response': merged}
                    current = {**original, **combined, 'inherited_valid_response_keys': parents,
                        'source_admission': 'validated_original_unit',
                        'original_record_fingerprint': canonical_fingerprint(original),
                        'unit_source_proofs': [s['unit_source_proof'] for s in direct]}
                    # Sources from legal prior target questions can retain
                    # answers, but cannot silently repair a wrong inheritance.
                    if not inheritance_errors:
                        valid = {candidate_identity(c) for s in sources for c in s['candidates']}
                        retained = [c for c in original.get('candidates', []) if candidate_identity(c) in valid]
                        if retained:
                            current['candidates'] = retained
                    validate_record_projection(current, sources)
                    derived = True
                if 'target_coverage' in original:
                    coverage=original_target_accounting(current,sources)
                    if coverage!=original['target_coverage']:
                        current['target_coverage']=coverage;derived=True
            elif not deterministic:
                error = 'original_unit_source_unavailable'
            active = key in needed
            if not sources and not deterministic:
                disposition = 'archive_only_unavailable'
            elif active:
                disposition = 'current_adopted'
            else:
                disposition = 'superseded_by_valid_answer'
            if current != original:
                projections[key] = current
            entry = {'kind': kind, 'disposition': disposition,
                'original_record_fingerprint': canonical_fingerprint(original),
                'current_projection_fingerprint': canonical_fingerprint(current),
                'direct_original_source_count': len(row['direct_sources']),
                'legitimate_inheritance': parents, 'rejected_inheritance': inheritance_errors,
                'derived_projection': derived, 'original_projection_error': error,
                'currently_needed_original_input': active, 'identity_eligibility_changed': False}
            entries[key] = entry
            if row['direct_sources'] or row.get('original_group'):
                direct = row['direct_sources'][0] if row['direct_sources'] else None
                group = SourceCandidateInputGroup(**(direct['group'] if direct else row['original_group']))
                if direct:coverage=original_target_accounting(current,sources)
                else:coverage={'requested_raw_tags':list(requested_targets(group)),'outcomes':{}}
                for raw in coverage['requested_raw_tags']:
                    outcome = coverage['outcomes'].get(raw)
                    targets.append({'record_key': key, 'raw_value': raw, 'question': original['input_fingerprint'],
                        'logical_target':logical_target(raw,group),
                        'disposition': outcome['disposition'] if outcome else 'missing',
                        'outcome': outcome, 'record_use': disposition,
                        'logical_targets': (direct['unit_source_proof']['logical_targets'] if direct else
                            BudgetedExtractionProvider.logical_keys([group])),
                        'source_paths': [s['path'] for s in sources], 'identity_equivalence_authorized': False})
    recovered = {**facts, 'current_record_projections': projections,
        'source_recovery': {'schema_version': VIEW_SCHEMA, 'original_facts_fingerprint': canonical_fingerprint(facts),
            'records': entries, 'fixed_record_denominator': sum(len(facts.get(k,{})) for k in RECORD_KINDS),
            'historical_rows_preserved': True, 'original_call_states_changed': False}}
    current_role_facts(recovered)
    return recovered, {'records': entries, 'targets': targets, 'new_provider_calls': 0,
        'counts': {status: sum(e['disposition'] == status for e in entries.values()) for status in
            ('current_adopted', 'superseded_by_valid_answer', 'archive_only_unavailable')}}
