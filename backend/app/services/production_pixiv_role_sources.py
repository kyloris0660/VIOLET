"""Role-only, read-only admission of a paid original answer unit.

Settlement, unit provenance, target coverage and identity eligibility are
separate. A rejected batch is never made successful by this contract. Pair
judgments continue to use AdjudicationBudget's strict whole-call contract.
"""
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace

from .pixiv_metadata_projection_service import canonical_fingerprint
from .source_concept_budget import AdjudicationBudget
from .source_metadata_registry_service import canonical_source_key, parse_parenthetical_name
from .source_name_candidate_extraction_service import (
    group_prompt_payload, validate_extraction_record,
    SourceNameCandidateExtractionError,
)

SOURCE_CONTRACT = 'violet.production-pixiv-original-role-unit.v1'
DERIVED_CACHE_VERSION = 'original-source-unit-v1'
# Existing F7a tag-family labels are retained verbatim; the immutable request
# proves the actual provider is Pixiv. Title/caption/artist/AI fields are not
# interchangeable with a tag. No new field or trust adapter is introduced.
_TAG_ORIGINS = {'source_tag_observation', 'pixiv_tag', 'normal_tag', 'booru_tag'}
_VALIDATION_ERRORS = (ValueError, TypeError, KeyError, SourceNameCandidateExtractionError)
_RETRY_PREFIX = 'The previous response failed schema validation: '
_RETRY_SUFFIX = ('. Return the requested records using the allowed roles, source fields and exact supported '
                 'raw names. Preserve uncertainty; do not invent identity.')


def requested_targets(group):
    if group.data_type_label and ': ' in group.data_type_label:
        targets = json.loads(group.data_type_label.split(': ', 1)[1])
        if not isinstance(targets, list) or any(not isinstance(t, str) or not t for t in targets):
            raise ValueError('role_source_targets_invalid')
        return tuple(targets)
    return tuple(t['raw_tag'] for t in group.tags)


def logical_target(raw,group):
    return 'role-target:'+canonical_fingerprint({'raw':canonical_source_key(raw),
        'context':sorted(canonical_source_key(t.get('raw_tag','')) for t in group.tags)})


def role_logical_keys(groups):
    return sorted({logical_target(raw,group) for group in groups for raw in requested_targets(group)})


def candidate_identity(candidate):
    """Compare semantic payloads across a legitimate partial-answer merge."""
    payload = {k: v for k, v in candidate.items() if k not in {'extraction_verdict', 'group_key'}}
    prefix = 'source-name-candidate:' + str(candidate.get('group_key')) + ':'
    if str(payload.get('candidate_key', '')).startswith(prefix):
        payload['candidate_key'] = payload['candidate_key'][len(prefix):]
    return canonical_fingerprint(payload)


def validate_answer_unit(raw, group):
    """Reproduce only approved adapters and F7a-valid siblings from the raw."""
    from . import production_pixiv_role_extraction as roles
    if not isinstance(raw, dict) or raw.get('group_key') != group.group_key:
        raise ValueError('role_source_unit_group_changed')
    if group.provider!='pixiv' or raw.get('provider',group.provider)!=group.provider:
        raise ValueError('role_source_unit_provider_changed')
    adapted = roles._adapt_response_record(raw, SimpleNamespace(unit_group=group))
    partial_error = None
    supported = {canonical_source_key(t.get('raw_tag')) for t in group.tags}
    for tag in group.tags:
        parsed = parse_parenthetical_name(tag.get('raw_tag') or '')
        if parsed:
            supported.update(canonical_source_key(v) for v in parsed)
        from .source_name_candidate_extraction_service import popularity_suffix_prefix
        prefix = popularity_suffix_prefix(tag.get('raw_tag') or '')
        if prefix:
            supported.add(canonical_source_key(prefix.get('extracted_prefix')))
    def checked(row):
        for candidate in row.get('candidates', []) or []:
            if not isinstance(candidate, dict) or canonical_source_key(candidate.get('raw_value')) not in supported:
                raise ValueError('role_source_candidate_outside_original_input')
        result=validate_extraction_record(row, group)
        if any(c.origin_type not in _TAG_ORIGINS for c in result[1]):
            raise ValueError('role_source_candidate_wrong_source_field')
        return result
    try:
        verdict, candidates, *_ = checked(adapted)
    except _VALIDATION_ERRORS as exc:
        # Isolate only candidate errors. Invalid envelope/truth/verdict/source
        # fields still fail when the same envelope is validated with siblings.
        valid = []
        for candidate in adapted.get('candidates', []) if isinstance(adapted.get('candidates'), list) else []:
            try:
                checked({**adapted, 'candidates': [candidate]})
            except _VALIDATION_ERRORS:
                continue
            valid.append(candidate)
        if not valid:
            raise ValueError('role_source_unit_no_valid_sibling:' + str(exc)) from exc
        partial_error = str(exc)
        adapted = {**adapted, 'candidates': valid, 'partial_validation_error': partial_error}
        verdict, candidates, *_ = checked(adapted)
    if verdict.extraction_verdict.startswith('extraction_error'):
        raise ValueError('role_source_unit_error_verdict')
    record = {'verdict': verdict.extraction_verdict, 'candidates': [asdict(c) for c in candidates],
              'validated_response': adapted}
    coverage = roles.role_target_coverage(SimpleNamespace(raw_values=requested_targets(group), unit_group=group), record)
    variants = list(record['candidates'])
    original_verdict = None
    # These two historical compatibility paths were already approved. They
    # authenticate retained old projections, not a new identity inference.
    try:
        old_verdict, old_candidates, *_ = validate_extraction_record(raw, group)
        original_verdict = old_verdict.extraction_verdict
        variants.extend(asdict(c) for c in old_candidates if canonical_source_key(c.raw_value) in supported
            and c.origin_type in _TAG_ORIGINS)
    except _VALIDATION_ERRORS:
        pass
    historical = json.loads(json.dumps(adapted))
    for candidate in historical.get('candidates', []):
        if isinstance(candidate, dict) and candidate.get('production_original_extracted_span'):
            candidate['raw_value'] = candidate.pop('production_original_extracted_span')
    try:
        _, historical_candidates, *_ = validate_extraction_record(historical, group)
        variants.extend(asdict(c) for c in historical_candidates if canonical_source_key(c.raw_value) in supported
            and c.origin_type in _TAG_ORIGINS)
    except _VALIDATION_ERRORS:
        pass
    return {**record, 'coverage': coverage, 'compatible_candidates': variants,
            'original_verdict': original_verdict, 'partial_validation_error': partial_error}


def validate_record_projection(record, sources):
    """A ticket or string match cannot authenticate a changed fact projection."""
    if not sources:
        raise ValueError('role_source_record_original_answer_missing')
    candidates = {candidate_identity(c) for s in sources for c in s['candidates']}
    verdicts = {v for s in sources for v in (s['verdict'], s.get('original_verdict')) if v}
    verdicts.update(c.get('extraction_verdict') for s in sources for c in s['candidates'])
    if record.get('verdict') not in verdicts:
        raise ValueError('role_source_record_verdict_changed')
    if any(candidate_identity(c) not in candidates or c.get('extraction_verdict') not in verdicts
           for c in record.get('candidates', [])):
        raise ValueError('role_source_record_candidate_changed')
    dispositions = {canonical_fingerprint(d) for s in sources for d in s.get('dispositions', []) if isinstance(d, dict)}
    if any(canonical_fingerprint(d) not in dispositions
           for d in record.get('validated_response', {}).get('target_dispositions', [])):
        raise ValueError('role_source_record_disposition_changed')


def original_target_accounting(record,sources):
    """Coverage of actual direct/inherited questions, never a stored summary."""
    from .production_pixiv_role_extraction import role_target_coverage
    if not sources:raise ValueError('role_source_original_target_question_missing')
    groups=[s['group'] for s in sources]
    from .source_name_candidate_extraction_service import SourceCandidateInputGroup
    groups=[SourceCandidateInputGroup(**g) if isinstance(g,dict) else g for g in groups]
    direct=[(source,group) for source,group in zip(sources,groups)
        if canonical_fingerprint(group_prompt_payload(group))==record.get('input_fingerprint')]
    if not direct:raise ValueError('role_source_original_target_question_missing')
    group=direct[0][1];targets=requested_targets(group)
    current=role_target_coverage(SimpleNamespace(unit_group=group,raw_values=targets),record)
    if not record.get('inherited_valid_response_keys'):return current
    # The current narrow question cannot reproduce an older explicit unknown
    # or non-name disposition. Read that outcome from its real admitted parent
    # answer, retaining the first valid answer across attempts.
    outcomes={};all_targets=set(targets)
    prior=[(source,question) for source,question in zip(sources,groups)
        if canonical_fingerprint(group_prompt_payload(question))!=record['input_fingerprint']]
    for source,question in sorted(prior,key=lambda item:item[0]['unit_source_proof']['original_call_order']):
        all_targets.update(requested_targets(question))
        coverage=source['answer']['coverage']
        for raw,outcome in coverage['outcomes'].items():outcomes.setdefault(raw,outcome)
    for raw,outcome in current['outcomes'].items():outcomes.setdefault(raw,outcome)
    targets=sorted(all_targets);missing=[raw for raw in targets if raw not in outcomes]
    return {**current,'requested_raw_tags':targets,'outcomes':outcomes,
        'missing_raw_tags':missing,'fully_accounted':not missing}


class RoleSourceContract:
    """Bound role source exception; never writes or upgrades ledger entries."""
    def __init__(self, ledger, *, legacy_calls=None,schema_history=None):
        self.ledger = ledger
        if ledger.get('model') != 'gpt-4.1-mini':
            raise ValueError('role_source_actual_model_changed')
        charged = AdjudicationBudget._charged(ledger)
        if charged > ledger['cap_microusd']:
            raise ValueError('role_source_budget_exceeded')
        self.calls = {c['id']: c for c in ledger['calls']}
        self.call_order={c['id']:index for index,c in enumerate(ledger['calls'])}
        self.by_key = defaultdict(list)
        for call in ledger['calls']:
            self.by_key[call['key']].append(call)
        self.legacy_calls = legacy_calls
        self.schema_history=schema_history;self._schema_history=None

    def _prior_success(self,call):
        # A recorded schema annotation cannot authenticate itself. This narrow
        # path additionally requires the immutable pre-amendment paid success,
        # unchanged debit/request fields and a revalidated real answer unit.
        if (call['status']!='success' or call.get('business_valid') is not False
                or call.get('business_validation_reason')!='saved_response_failed_current_schema'):
            return None
        if self._schema_history is None:
            self._schema_history=self.schema_history() if callable(self.schema_history) else (self.schema_history or {})
        history=self._schema_history;old=history.get('calls',{}).get(call['id'])
        annotations={'business_valid','business_validation_reason','logical_keys'}
        if (not old or not AdjudicationBudget._eligible_cached_response(old)
                or {k:v for k,v in call.items() if k not in annotations}!=
                   {k:v for k,v in old.items() if k not in annotations}):return None
        return {'anchor_path':history.get('path'),'anchor_sha256':history.get('sha256'),
            'original_call_fingerprint':canonical_fingerprint(old)}

    def admit(self, saved, groups, raw_bytes, *, path=None):
        from . import production_pixiv_role_extraction as roles
        from .production_pixiv_release_provenance import replay_role_request_messages
        # The caller must supply actual file bytes, never a fabricated digest.
        if json.loads(raw_bytes) != json.loads(json.dumps(saved)):
            raise ValueError('role_source_raw_bytes_changed')
        if saved.get('model') != 'gpt-4.1-mini':
            raise ValueError('role_source_model_changed')
        if saved.get('temperature', 0.0) != 0.0 or saved.get('max_tokens', 6000) != 6000:
            raise ValueError('role_source_request_parameters_changed')
        messages = replay_role_request_messages(groups)
        fingerprint = canonical_fingerprint({'model': saved['model'], 'messages': messages,
            'temperature': saved.get('temperature', 0.0), 'max_tokens': saved.get('max_tokens', 6000)})
        if fingerprint != saved.get('input_fingerprint') or (
                saved.get('request_messages') is not None and messages != saved['request_messages']):
            raise ValueError('role_source_original_question_changed')
        if len({g.group_key for g in groups}) != len(groups):
            raise ValueError('role_source_duplicate_request_group')
        if saved.get('request_groups') is not None and [asdict(g) for g in groups] != saved['request_groups']:
            # JSON represents tuple fields as lists.
            if json.loads(json.dumps([asdict(g) for g in groups])) != saved['request_groups']:
                raise ValueError('role_source_original_groups_changed')
        wire = saved.get('wire_messages', messages)
        if 'wire_messages' in saved and canonical_fingerprint(wire) != saved.get('wire_fingerprint'):
            raise ValueError('role_source_wire_fingerprint_changed')
        if wire != messages:
            if (not isinstance(wire, list) or len(wire) != len(messages) + 1 or wire[:-1] != messages
                    or set(wire[-1]) != {'role', 'content'} or wire[-1]['role'] != 'user'
                    or not isinstance(wire[-1]['content'], str) or not wire[-1]['content'].startswith(_RETRY_PREFIX)
                    or not wire[-1]['content'].endswith(_RETRY_SUFFIX)
                    or len(wire[-1]['content']) > len(_RETRY_PREFIX) + 500 + len(_RETRY_SUFFIX)):
                raise ValueError('role_source_unapproved_wire_request')
        rows = json.loads(saved['content']).get('records')
        if not isinstance(rows, list):
            raise ValueError('role_source_real_return_required')
        rows_by_key = defaultdict(list)
        for row in rows:
            if isinstance(row, dict):
                rows_by_key[row.get('group_key')].append(row)
        units = {}; errors = {}; original_failures={}
        for group in groups:
            matches = rows_by_key.get(group.group_key, [])
            if len(matches) != 1:
                errors[group.group_key] = 'role_source_response_unit_missing_or_duplicate'
                continue
            try:
                old_verdict,old_candidates,*_=validate_extraction_record(matches[0], group)
                if group.data_origin in {roles.COMPLETION_ORIGIN,roles.COVERAGE_REPAIR_ORIGIN,
                        roles.CORRECTION_ORIGIN,'production_pixiv_role_coverage_repair_v1'}:
                    old_coverage=roles.role_target_coverage(SimpleNamespace(unit_group=group,raw_values=requested_targets(group)),
                        {'verdict':old_verdict.extraction_verdict,'candidates':[asdict(c) for c in old_candidates],
                         'validated_response':matches[0]})
                    if old_coverage['missing_raw_tags']:
                        original_failures[group.group_key]={'missing_original_targets':old_coverage['missing_raw_tags']}
            except _VALIDATION_ERRORS as exc:
                original_failures[group.group_key]={'original_schema_error':str(exc)}
            try:
                units[group.group_key] = validate_answer_unit(matches[0], group)
            except _VALIDATION_ERRORS as exc:
                errors[group.group_key] = str(exc)
        batch_error = (bool(errors) or len(rows) != len(groups)
            or set(rows_by_key) != {g.group_key for g in groups}
            or any(u['partial_validation_error'] for u in units.values())
            or any(u['coverage']['missing_raw_tags'] for g in groups if g.data_origin in {
                roles.COMPLETION_ORIGIN, roles.COVERAGE_REPAIR_ORIGIN, roles.CORRECTION_ORIGIN,
                'production_pixiv_role_coverage_repair_v1'} for u in [units.get(g.group_key)] if u))
        response = saved.get('budget_response') or {}
        key = 'role-extraction:' + fingerprint
        if response and response.get('key') != key:
            raise ValueError('role_source_call_key_changed')
        if response:
            matches = [self.calls.get(response.get('reservation'))]
            if matches[0] is None or matches[0]['key'] != key:
                raise ValueError('role_source_original_attempt_missing')
            if 'attempt' in response and response['attempt'] != next(
                    i + 1 for i, c in enumerate(self.by_key[key]) if c['id'] == matches[0]['id']):
                raise ValueError('role_source_attempt_order_changed')
        else:
            matches = self.by_key[key]
        expected_logical = roles.BudgetedExtractionProvider.logical_keys(groups)
        source_attempts = []; legacy_replay = {}; call_errors = [];schema_history_proofs={}
        for call in matches:
            try:
                if call['status'] not in {'success', 'failed'}:
                    raise ValueError('role_source_original_attempt_not_settled')
                if call.get('source_revoked') or call.get('revoked'):
                    raise ValueError('role_source_revoked')
                if call['charged_microusd'] > call['reserved_microusd']:
                    raise ValueError('role_source_original_fee_exceeded')
                if call['output_token_ceiling'] != 6000 or call['input_token_ceiling'] != (
                        len(json.dumps(wire, ensure_ascii=False).encode('utf-8')) + 512):
                    raise ValueError('role_source_original_reservation_changed')
                usage = saved.get('usage', {})
                if call['usage_known']:
                    if (not isinstance(usage, dict) or any(type(usage.get(k)) is not int for k in
                            ('prompt_tokens', 'completion_tokens')) or call['usage'] != {
                            k: usage[k] for k in ('prompt_tokens', 'completion_tokens')}):
                        raise ValueError('role_source_original_usage_changed')
                elif usage:
                    raise ValueError('role_source_original_usage_changed')
                if not AdjudicationBudget._eligible_cached_response(call):
                    # A genuine returned schema/coverage failure can have
                    # good siblings. Other invalidations are never admitted.
                    history=self._prior_success(call)
                    if ((not batch_error and not original_failures and not history)
                            or not call['usage_known'] or call.get('business_valid') is not False
                            or call.get('business_validation_reason') not in {None, 'saved_response_failed_current_schema'}):
                        raise ValueError('role_source_call_failure_not_unit_schema')
                    if history:schema_history_proofs[call['id']]=history
                if 'logical_keys' not in call:
                    anchored = self.legacy_calls() if callable(self.legacy_calls) else (self.legacy_calls or {})
                    if anchored.get(call['id']) != call:
                        raise ValueError('role_source_legacy_logical_anchor_missing')
                    legacy_replay[call['id']] = {'request_fingerprint': fingerprint,
                        'derived_logical_keys': expected_logical, 'original_ledger_unchanged': True}
                elif (not isinstance(call['logical_keys'], list) or sorted(call['logical_keys']) != expected_logical):
                    raise ValueError('role_source_original_logical_keys_changed')
                source_attempts.append(call['id'])
            except ValueError as exc:
                call_errors.append(str(exc))
        if not source_attempts:
            raise ValueError('role_source_call_not_admitted:' + ','.join(sorted(set(call_errors or ['missing_attempt']))))
        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        result = []
        for group in groups:
            answer = units.get(group.group_key)
            if not answer:
                continue
            proof = {'schema_version': SOURCE_CONTRACT, 'raw_sha256': raw_sha, 'path': path,
                'request_fingerprint': fingerprint, 'wire_fingerprint': canonical_fingerprint(wire),
                'question_fingerprint': canonical_fingerprint(group_prompt_payload(group)),
                'group_key': group.group_key, 'attempts': source_attempts,
                'original_call_order':min(self.call_order[a] for a in source_attempts),
                'settlements': [{k: self.calls[a].get(k) for k in ('id', 'key', 'status', 'business_valid',
                    'business_validation_reason', 'usage_known', 'usage', 'charged_microusd', 'reserved_microusd')}
                    for a in source_attempts], 'logical_targets': roles.BudgetedExtractionProvider.logical_keys([group]),
                'batch_schema_valid': not batch_error, 'whole_call_upgraded': False,
                'original_unadapted_failures':original_failures,
                'immutable_schema_history':{a:schema_history_proofs[a] for a in source_attempts if a in schema_history_proofs},
                'unit_projection_fingerprint': canonical_fingerprint({k: answer[k] for k in
                    ('verdict', 'candidates', 'validated_response')}),
                'coverage': answer['coverage'], 'identity_equivalence_authorized': False}
            result.append({'group': group, 'candidates': answer['compatible_candidates'],
                'verdict': answer['verdict'], 'original_verdict': answer['original_verdict'],
                'dispositions': answer['validated_response'].get('target_dispositions', []),
                'path': path, 'attempts': source_attempts, 'fingerprint': canonical_fingerprint(saved),
                'answer': answer, 'unit_source_proof': proof})
        return {'sources': result, 'errors': errors, 'batch_schema_valid': not batch_error,
                'anchored_legacy_logical_replay': legacy_replay}
