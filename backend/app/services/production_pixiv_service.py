"""Fixed production Pixiv scope, role adapter, and atomic owned replacement.

This orchestrates the existing source registry, resolver, and product tables.
It creates neither Entity truth nor another acquisition queue. No provider or
media I/O happens in this module.
"""
from collections import Counter, defaultdict
from dataclasses import replace
import re
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text

from ..models import Media, SourceMetadataRecord, SourceConceptProductRun
from .pixiv_filename_prior_service import distinct_work_pages, parse_approved_fields
from .pixiv_identity_policy import canonical_pixiv_work_id
from .pixiv_metadata_clustering_service import (
    PIXIV_AGGREGATE_SCHEMA, PIXIV_SIGNAL_BUNDLE_SCHEMA, PX2_CONSUMER_CONTRACT_SCHEMA,
    finish_pixiv_clustering, validate_px1_consumer_artifacts,
)
from .pixiv_metadata_projection_service import (
    canonical_fingerprint, project_pixiv_aggregate_to_source_concept_signals,
)
from .source_concept_resolver_service import LLMAdjudicationConfig, resolve_source_concepts

PRODUCTION_POLICY = 'production_pixiv_fixed_scope_adjudication_v4'
SUPPORT_NAMESPACE = 'production_pixiv'
SCOPE_SCHEMA = 'violet.production-pixiv-fixed-scope.v1'
SELECTION_SCHEMA = 'violet.production-pixiv-fixed-scope-selection.v1'


def build_fixed_scope(media_rows, *, watermark, trusted_bindings=()):
    """Map database facts only; contradictory exact sources stay unresolved."""
    exact = defaultdict(set)
    for binding in trusted_bindings:
        exact[int(binding['media_id'])].add((str(binding['work_id']), int(binding['page_index'])))
    mappings = []
    for row in sorted(media_rows, key=lambda item: int(item['id'])):
        media_id = int(row['id'])
        priors = distinct_work_pages(parse_approved_fields((('filename', row.get('filename')), ('stored_path', row.get('path')))))
        proven = exact[media_id]
        if len(proven) == 1:
            selected = next(iter(proven))
            disposition = 'trusted_source_mapping'
        elif len(proven) > 1:
            selected, disposition = None, 'conflicting_trusted_sources'
        elif len(priors) == 1:
            selected, disposition = priors[0], 'metadata_required'
        elif priors:
            selected, disposition = None, 'conflicting_filename_priors'
        else:
            selected, disposition = None, 'not_applicable'
        mappings.append({'media_id':media_id, 'work_id':selected[0] if selected else None,
                         'page_index':selected[1] if selected else None,
                         'priors':[list(pair) for pair in priors], 'disposition':disposition})
    scope = {'schema_version':SCOPE_SCHEMA,'watermark':str(watermark), 'mappings':mappings,
             'media_count':len(mappings),'disposition_counts':dict(Counter(row['disposition'] for row in mappings))}
    scope['canonical_fingerprint'] = canonical_fingerprint(scope)
    return scope


def build_production_inputs(session, scope, *, work_ids=None):
    """Freeze proven local facts, while accounting for every T0 Media row.

    A missing remote page remains a queue disposition; it cannot poison the
    valid pages of that work or supply fictional role/context evidence.
    """
    from .pixiv_product_media_binding import verified_local_binding_provenance
    from .pixiv_metadata_projection_service import build_canonical_pixiv_aggregates_from_session
    payload={key:value for key,value in scope.items() if key!='canonical_fingerprint'}
    if scope.get('schema_version')!=SCOPE_SCHEMA or canonical_fingerprint(payload)!=scope.get('canonical_fingerprint'):
        raise ValueError('production_pixiv_scope_fingerprint_invalid')
    mappings={row['media_id']:row for row in scope['mappings']}
    live_media={row[0] for row in session.query(Media.id).all()}
    selected_works=set(work_ids) if work_ids is not None else None
    records=defaultdict(list); eligible=[]
    for row in session.query(SourceMetadataRecord).filter(
        SourceMetadataRecord.provider=='pixiv',SourceMetadataRecord.media_id.in_(sorted(mappings))).all():
        mapping=mappings[row.media_id]
        if row.media_id not in live_media or not mapping['work_id']:
            continue
        if (row.source_work_id,row.source_page_index)!=(mapping['work_id'],mapping['page_index']):
            continue
        records[row.media_id].append(row)
        if (selected_works is None or row.source_work_id in selected_works) and verified_local_binding_provenance(row):
            eligible.append(row)
    aggregates=build_canonical_pixiv_aggregates_from_session(session,source_record_ids=[row.id for row in eligible])
    by_page={(row['work_id'],row['page_index']):row for row in aggregates}
    eligible_ids={row.id for row in eligible}
    items=[]
    for media_id,mapping in sorted(mappings.items()):
        source=records[media_id]
        states=sorted({row.status for row in source})
        aggregate=by_page.get((mapping['work_id'],mapping['page_index']))
        if media_id not in live_media:
            disposition='media_removed_after_t0'
        elif not mapping['work_id']:
            disposition=mapping['disposition']
        elif selected_works is not None and mapping['work_id'] not in selected_works:
            disposition='outside_selected_checkpoint'
        elif any(row.id in eligible_ids for row in source):
            disposition='metadata_complete' if aggregate['disposition']=='complete' else 'metadata_source_conflict'
        elif 'metadata_retryable' in states:
            disposition='metadata_retryable'
        elif 'normalization_failed' in states:
            disposition='normalization_failed'
        elif 'provider_identity_mismatch' in states:
            disposition='provider_identity_mismatch'
        elif 'deferred_nonblocking_source_page_mismatch' in states:
            disposition='deferred_nonblocking_source_page_mismatch'
        elif 'terminal_remote_unavailable' in states:
            disposition='terminal_remote_unavailable'
        elif 'metadata_pending' in states or not states:
            disposition='metadata_pending'
        else:
            disposition='unverified_source'
        items.append({'media_id':media_id,'work_id':mapping['work_id'],'page_index':mapping['page_index'],
            'disposition':disposition,'source_states':states,'source_record_ids':sorted(row.id for row in source),
            'eligible_record_ids':sorted(row.id for row in source if row.id in eligible_ids)})
    work_rows=defaultdict(list)
    for item in items:
        if item['work_id']:work_rows[item['work_id']].append(item)
    work_ledger=[{'work_id':work,'media_ids':sorted(row['media_id'] for row in rows),
        'page_indexes':sorted({row['page_index'] for row in rows}),
        'disposition_counts':dict(Counter(row['disposition'] for row in rows))}
        for work,rows in sorted(work_rows.items(),key=lambda pair:int(pair[0]))]
    coverage={'schema_version':'violet.production-pixiv-coverage.v1','scope_fingerprint':scope['canonical_fingerprint'],
        'watermark':scope['watermark'],'media_count':len(items),'mapped_works':len(work_ledger),
        'mapped_media':sum(bool(row['work_id']) for row in items),
        'mapped_work_pages':len({(row['work_id'],row['page_index']) for row in items if row['work_id']}),
        'tail_media_ids':sorted(live_media-set(mappings)),
        'counts':dict(Counter(row['disposition'] for row in items)),
        'aggregates':len(aggregates),'aggregate_dispositions':dict(Counter(row['disposition'] for row in aggregates)),
        'input_fingerprint':canonical_fingerprint(aggregates),'items':items,'works':work_ledger}
    coverage['canonical_fingerprint']=canonical_fingerprint(coverage)
    return aggregates,coverage


def production_consumer(aggregates):
    """Adapt artist context while preserving raw facts and all strict PX guards."""
    aggregates = sorted(aggregates, key=lambda row: row['stable_work_page_key'])
    bundles = []
    for aggregate in aggregates:
        bundle = project_pixiv_aggregate_to_source_concept_signals(aggregate)
        for signal in bundle['signals']:
            if signal['role_hint'] == 'artist' and signal['work_context_key'] is not None:
                signal['evidence'] = {**signal['evidence'],
                    'production_adapter_version':PRODUCTION_POLICY,
                    'original_work_context_key':signal['work_context_key']}
                signal['work_context_key'] = None
        bundle['canonical_fingerprint'] = canonical_fingerprint({key:value for key,value in bundle.items() if key != 'canonical_fingerprint'})
        bundles.append(bundle)
    contract = {'schema_version':PX2_CONSUMER_CONTRACT_SCHEMA,
                'aggregate_schema_version':PIXIV_AGGREGATE_SCHEMA,
                'signal_bundle_schema_version':PIXIV_SIGNAL_BUNDLE_SCHEMA,
                'aggregate_artifact_fingerprint':canonical_fingerprint(aggregates),
                'signal_bundle_artifact_fingerprint':canonical_fingerprint(bundles),
                'canonical_json_round_trip_stable':True,'database_row_identity_excluded':True,
                'runtime_order_identity_excluded':True,'wall_clock_identity_excluded':True,
                'cluster_materialization_performed':False}
    return validate_px1_consumer_artifacts(aggregates=aggregates, signal_bundles=bundles, consumer_contract=contract)


def _with_adjudicated_work_context(signals,judgments,run_id):
    """Reuse guarded work components as context, without assigning characters.

    This first pass uses the same resolver and accepted work judgments. It
    cannot promote unknown roles, override cannot links, or turn weak inferred
    context into explicit evidence. The second pass resolves the full input.
    """
    from .source_concept_resolver_service import _context_candidates_by_scope,signal_context_key
    works=tuple(signal for signal in signals if signal.role_hint=='work')
    work_keys={signal.signal_key for signal in works}
    accepted=[row for row in judgments if row['left_signal_key'] in work_keys and row['right_signal_key'] in work_keys]
    if not accepted:return signals
    resolved=resolve_source_concepts(works,run_id=run_id+':work-context',llm_judgments=accepted,
        llm_config=LLMAdjudicationConfig(enabled=False,max_calls=0),concept_namespace=SUPPORT_NAMESPACE)
    aliases={};groups=[];owners=defaultdict(set)
    for concept in resolved.concepts:
        names={signal.canonical_key for signal in concept.signals if signal.canonical_key}
        for name in names:owners[name].add(concept.concept_key)
        groups.append(names)
    for names in groups:
        if len(names)<2:continue
        if any(len(owners[name])!=1 for name in names):continue
        canonical=min(names)
        for name in names:aliases[name]=canonical
    if not aliases:return signals
    contexts=_context_candidates_by_scope(signals,context_alias_by_key=aliases)
    adapted=[]
    for signal in signals:
        # Remove any caller-provided derived context before computing it from
        # this exact input and this pass's guarded, materialized work groups.
        evidence={key:value for key,value in signal.evidence_payload.items() if key!='production_adjudicated_work_context'}
        clean=replace(signal,evidence_payload=evidence)
        if signal.role_hint in {'character','person','unknown'}:
            context,reason=signal_context_key(clean,contexts,aliases)
            if context and reason:
                evidence['production_adjudicated_work_context']={'key':context,'reason':reason,
                    'work_resolution_run_id':resolved.run_id,'identity_equivalence_authorized_for_character':False}
        adapted.append(replace(clean,evidence_payload=evidence))
    return tuple(adapted)


def build_production_clustering(consumer, *, judgments=(), vocabulary=None, role_facts=None):
    from .production_pixiv_semantics import adapt_production_semantics
    consumer = adapt_production_semantics(consumer,vocabulary,role_facts)
    # Never upsert another consumer's globally keyed support. These keys are
    # constant across batches, scopes and resumes; only provenance is scoped.
    signal_keys = {signal.signal_key: SUPPORT_NAMESPACE + ':' + signal.signal_key
                   for signal in consumer.signals}
    judgments = tuple({**dict(judgment), **{
        key: signal_keys.get(judgment[key], judgment[key])
        for key in ('left_signal_key', 'right_signal_key')}} for judgment in judgments)
    # Judge identity excludes volatile run/pair counters, costs and cache hits.
    semantic = sorted([{'left':j['left_signal_key'],'right':j['right_signal_key'],
                        'decision':j.get('decision'),'confidence':j.get('confidence'),
                        'cache_key':j.get('cache_key'),'error_state':j.get('error_state')}
                       for j in judgments], key=lambda item:(item['left'],item['right']))
    identity = canonical_fingerprint({'input':consumer.input_fingerprint,'policy':PRODUCTION_POLICY,'judgments':semantic})
    run_id = 'production-pixiv:' + identity[:32]
    signals = tuple(replace(signal, signal_key=signal_keys[signal.signal_key],
        created_by_run_id=run_id, source_run_id=run_id,
        evidence_payload={**{key:value for key,value in signal.evidence_payload.items()
                             if key!='production_adjudicated_work_context'},
            'production_support_namespace': SUPPORT_NAMESPACE,
            'original_signal_key': signal.signal_key,
            'production_candidate_scope': 'pixiv:work:' + signal.evidence_payload['work_id']})
        for signal in consumer.signals)
    signals=_with_adjudicated_work_context(signals,judgments,run_id)
    consumer = replace(consumer, signals=signals, bundle_signal_keys={
        key: tuple(signal_keys[value] for value in values)
        for key, values in consumer.bundle_signal_keys.items()})
    result = resolve_source_concepts(signals, run_id=run_id,
                llm_config=LLMAdjudicationConfig(enabled=False, max_calls=0), llm_judgments=judgments,
                concept_namespace=SUPPORT_NAMESPACE)
    run = finish_pixiv_clustering(consumer, result)
    return replace(run, business_projection_fingerprint=canonical_fingerprint({
        'resolved_business':run.business_projection_fingerprint,'production_policy':PRODUCTION_POLICY,
        'judgments_fingerprint':canonical_fingerprint(semantic)}))


def scope_selection(scope, run):
    payload = {key:value for key,value in scope.items() if key != 'canonical_fingerprint'}
    if scope.get('schema_version') != SCOPE_SCHEMA or canonical_fingerprint(payload) != scope.get('canonical_fingerprint'):
        raise ValueError('production_pixiv_scope_fingerprint_invalid')
    mapped_works = {row['work_id'] for row in scope['mappings'] if row['work_id']}
    selected = sorted({row['work_id'] for row in run.consumer.aggregates}, key=int)
    if not set(selected).issubset(mapped_works):
        raise ValueError('production_pixiv_work_outside_scope')
    selection = {'schema_version':SELECTION_SCHEMA,'scope_fingerprint':scope['canonical_fingerprint'],
                 'watermark':scope['watermark'], 'policy_version':PRODUCTION_POLICY,
                 'fixed_media_ids':sorted(row['media_id'] for row in scope['mappings'] if row['work_id']),
                 'fixed_bindings':sorted([row['media_id'],row['work_id'],row['page_index']]
                     for row in scope['mappings'] if row['work_id']),
                 'selected_work_ids':selected,'selected_work_count':len(selected)}
    selection['canonical_fingerprint'] = canonical_fingerprint(selection)
    return validate_scope_selection(run, selection)


def validate_scope_selection(run, selection):
    payload = {key:value for key,value in selection.items() if key != 'canonical_fingerprint'}
    expected_keys = {'schema_version','scope_fingerprint','watermark','policy_version','fixed_media_ids','fixed_bindings','selected_work_ids','selected_work_count'}
    if set(payload) != expected_keys or payload.get('schema_version') != SELECTION_SCHEMA or payload.get('policy_version') != PRODUCTION_POLICY:
        raise ValueError('production_pixiv_selection_schema_invalid')
    if canonical_fingerprint(payload) != selection.get('canonical_fingerprint'):
        raise ValueError('production_pixiv_selection_fingerprint_invalid')
    ids = payload['fixed_media_ids']
    works = payload['selected_work_ids']
    bindings = payload['fixed_bindings']
    if not isinstance(ids,list) or any(type(value) is not int or value <= 0 for value in ids) or ids != sorted(set(ids)):
        raise ValueError('production_pixiv_selection_media_invalid')
    if (not isinstance(bindings,list) or any(not isinstance(row,list) or len(row)!=3
        or type(row[0]) is not int or row[0]<=0 or canonical_pixiv_work_id(row[1])!=row[1]
        or type(row[2]) is not int or row[2]<0 for row in bindings)
        or [row[0] for row in bindings]!=ids):
        raise ValueError('production_pixiv_selection_binding_invalid')
    if any(canonical_pixiv_work_id(work) != work for work in works) or works != sorted(set(works),key=int):
        raise ValueError('production_pixiv_selection_work_invalid')
    if payload['selected_work_count'] != len(works) or set(works) != {row['work_id'] for row in run.consumer.aggregates}:
        raise ValueError('production_pixiv_selection_run_mismatch')
    if not isinstance(payload['scope_fingerprint'],str) or not re.fullmatch('[0-9a-f]{64}',payload['scope_fingerprint']) or not payload['watermark']:
        raise ValueError('production_pixiv_selection_scope_invalid')
    return dict(selection)


def replace_production_projection(session, run, *, scope, apply=False, accepted_plan=None):
    """Replace owned support in one transaction; readers see old or new scope.

    The existing product run audit rows are retained. A1's owned rollback and
    source revision guards are exercised, never removed or manually bypassed.
    """
    from .pixiv_product_integration_service import apply_pixiv_product_plan, rollback_pixiv_product_run
    selection = scope_selection(scope, run)
    scope_key = 'pixiv:production:' + scope['canonical_fingerprint'][:32]
    if apply and session.bind.dialect.name == 'postgresql':
        session.execute(text('SELECT pg_advisory_xact_lock(74125152)'))
    active = session.query(SourceConceptProductRun).filter(
        SourceConceptProductRun.source_mode.in_(['existing_source_metadata','production_scope']),
        SourceConceptProductRun.status == 'active').order_by(SourceConceptProductRun.id)
    if apply:
        active = active.populate_existing().with_for_update()
    previous = active.all()
    if any(row.source_mode == 'production_scope' and row.scope_key != scope_key for row in previous):
        raise ValueError('production_pixiv_other_fixed_scope_active')
    plan = apply_pixiv_product_plan(session, run, scope_key=scope_key, source_mode='production_scope',
                                   input_selection=selection, apply=False)
    plan['replaces'] = [{'run_key':row.run_key,'result_fingerprint':row.result_fingerprint} for row in previous if row.run_key != plan['run_key']]
    plan['production_policy_version'] = PRODUCTION_POLICY
    plan['replacement_fingerprint'] = canonical_fingerprint({key:plan[key] for key in
        ('selection_fingerprint','product_result_fingerprint','media_binding','replaces','production_policy_version')})
    if not apply:
        return plan
    if not accepted_plan or accepted_plan.get('replacement_fingerprint') != plan['replacement_fingerprint']:
        raise ValueError('production_pixiv_accepted_replacement_changed')
    try:
        withdrawals = []
        for row in previous:
            if row.run_key != plan['run_key']:
                withdrawals.append(rollback_pixiv_product_run(session,row.run_key,commit=False))
        result = apply_pixiv_product_plan(session, run, scope_key=scope_key, source_mode='production_scope',
            input_selection=selection, apply=True, commit=False,
            accepted_selection_fingerprint=plan['selection_fingerprint'],
            accepted_product_fingerprint=plan['product_result_fingerprint'],
            accepted_binding_fingerprint=plan['media_binding']['local_binding_fingerprint'])
        session.commit()
        from ..utils.cache import invalidate_source_concept_search_cache
        invalidate_source_concept_search_cache()
    except Exception:
        session.rollback()
        raise
    return {**result,'withdrawals':withdrawals,'replacement_fingerprint':plan['replacement_fingerprint'],
            'production_policy_version':PRODUCTION_POLICY}
