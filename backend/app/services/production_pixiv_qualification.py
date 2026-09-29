"""Source-scoped identity eligibility, independent of immutable model answers.

The protected owner decision is data. No literal, character, or Media ID is a
resolver rule. Existing rejected-trust routing excludes identity participation
while the source signal, model role/status and parenthetical evidence remain.
"""
import json
from dataclasses import asdict, replace
from pathlib import Path

from .pixiv_metadata_projection_service import canonical_fingerprint
from .production_pixiv_corrections import signal_semantics

AUTHORITY_PATH=Path(__file__).resolve().parents[3]/'docs/state/production-pixiv-a2-identity-qualification.json'


def load_qualification_authority():
    value=json.loads(AUTHORITY_PATH.read_text(encoding='utf-8'))
    if (value.get('schema_version')!='violet.production-identity-qualification.v1'
        or value.get('decision')!='suspend_identity_inference'
        or not value.get('authority') or not value.get('decision_version')
        or not isinstance(value.get('targets'),list) or not value['targets']):
        raise ValueError('identity_qualification_authority_invalid')
    return value


def apply_identity_qualification(signals, original_signals, selection, *, authority=None):
    authority=authority or load_qualification_authority()
    targets=authority['targets']
    if selection!=authority:
        raise ValueError('identity_qualification_selection_not_authorized')
    keys=[row.get('signal_key') for row in targets]
    if not all(keys) or len(keys)!=len(set(keys)):
        raise ValueError('identity_qualification_duplicate_target')
    current={s.signal_key:s for s in signals};original={s.signal_key:s for s in original_signals}
    if len(current)!=len(signals) or len(original)!=len(original_signals) or not set(keys)<=current.keys()&original.keys():
        raise ValueError('identity_qualification_target_missing_or_duplicate')
    by_key={row['signal_key']:row for row in targets}
    for row in targets:
        signal=current[row['signal_key']];source=original[row['signal_key']]
        if (signal.origin_type!='pixiv_tag_observation'
            or signal.evidence_payload.get('aggregate_fingerprint')!=row.get('aggregate_fingerprint')
            or row.get('original_input_fingerprint')!=canonical_fingerprint(asdict(source))
            or row.get('supersedes')!=signal_semantics(signal)
            or not row.get('logical_target') or not row.get('reason')
            or signal.evidence_payload.get('production_role_hint_evidence')):
            raise ValueError('identity_qualification_source_or_semantics_changed')
    result=[]
    for signal in signals:
        row=by_key.get(signal.signal_key)
        if row is None:
            result.append(signal);continue
        evidence={**signal.evidence_payload,'identity_qualification':{
            'disposition':'identity_suspended','decision_version':authority['decision_version'],
            'authority_fingerprint':canonical_fingerprint(authority),'target':row,
            'source_parenthetical_context':signal.parenthetical_context,
            'original_role_and_status_retained':True},
            'non_concept_reason':'identity_qualification_suspended'}
        evidence.pop('production_adjudicated_work_context',None)
        # This is resolver trust, not a replacement role/completion judgment.
        result.append(replace(signal,trust_tier='rejected',evidence_payload=evidence))
    return tuple(result)


def verify_release_qualification(facts):
    if facts.get('identity_qualification')!=load_qualification_authority():
        raise ValueError('semantic_identity_qualification_missing_or_changed')
