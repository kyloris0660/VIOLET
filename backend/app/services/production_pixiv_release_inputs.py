"""A2 release admission over the accepted T0 and complete semantic artifacts."""
from types import SimpleNamespace

from .pixiv_metadata_projection_service import canonical_fingerprint


def verify_t0_scope(scope, inventory, database, system_identifier):
    from .production_pixiv_service import build_fixed_scope
    from .pixiv_metadata_ingestion_service import is_trusted_complete_pixiv_metadata_record
    from .pixiv_product_media_binding import verified_local_binding_provenance
    summary = inventory['summary']
    if summary['identity'] != {'database': database, 'system_identifier': system_identifier}:
        raise ValueError('production_t0_database_identity_changed')
    records = [SimpleNamespace(provider='pixiv', **row) for row in inventory['metadata']]
    trusted = [dict(media_id=r.media_id, work_id=r.source_work_id, page_index=r.source_page_index)
               for r in records if r.media_id and is_trusted_complete_pixiv_metadata_record(r)
               and verified_local_binding_provenance(r)]
    expected = build_fixed_scope(inventory['media'], watermark=summary['watermark']['t0'], trusted_bindings=trusted)
    if len(inventory['media']) != summary['media_total'] or expected != scope:
        raise ValueError('production_scope_differs_from_accepted_t0_inventory')
    return {'inventory_fingerprint': canonical_fingerprint(inventory),
            'scope_fingerprint': expected['canonical_fingerprint']}


def verify_full_input(aggregates, live, coverage):
    if not aggregates or canonical_fingerprint(aggregates) != canonical_fingerprint(live):
        raise ValueError('complete_source_snapshot_changed_refresh_required')
    expected_pages = {(row['work_id'], row['page_index']) for row in coverage['items']
                      if row['disposition'] == 'metadata_complete'}
    actual_pages = {(row['work_id'], row['page_index']) for row in aggregates if row['disposition'] == 'complete'}
    if expected_pages != actual_pages:
        raise ValueError('complete_source_work_page_media_set_mismatch')


def semantic_versions():
    from .production_pixiv_service import PRODUCTION_POLICY
    from .production_pixiv_role_extraction import ROLE_SCHEMA, PROMPT_VERSION, EXTRACTOR_VERSION, SCHEMA_VERSION, COMPLETION_ORIGIN, COVERAGE_REPAIR_ORIGIN
    from .source_concept_resolver_service import RESOLVER_VERSION, LLM_CACHE_POLICY_VERSION, LLM_DECISION_SCHEMA_VERSION, LLM_ADJUDICATION_POLICY_VERSION
    return dict(production_policy=PRODUCTION_POLICY, role_schema=ROLE_SCHEMA, prompt=PROMPT_VERSION,
                extractor=EXTRACTOR_VERSION, extraction_schema=SCHEMA_VERSION, completion=COMPLETION_ORIGIN,
                coverage_repair=COVERAGE_REPAIR_ORIGIN, resolver=RESOLVER_VERSION,
                cache_policy=LLM_CACHE_POLICY_VERSION, decision_schema=LLM_DECISION_SCHEMA_VERSION,
                adjudication_policy=LLM_ADJUDICATION_POLICY_VERSION, model='gpt-4.1-mini', fallback=False)


def semantic_input_identity(aggregates, vocabulary, role_facts, judgments):
    if not isinstance(role_facts, dict) or role_facts.get('schema_version') != semantic_versions()['role_schema']:
        raise ValueError('complete_semantic_role_artifact_required')
    if not role_facts.get('records') and not role_facts.get('completion_records'):
        raise ValueError('complete_semantic_role_artifact_required')
    if not isinstance(judgments, (list, tuple)):
        raise ValueError('complete_semantic_judgment_artifact_required')
    versions=semantic_versions()
    expected_record={'schema_version':versions['role_schema'],'extractor_version':versions['extractor'],
                     'prompt_version':versions['prompt'],'decision_schema':versions['extraction_schema'],
                     'model':versions['model']}
    for name in ('records','context_records','completion_records','coverage_repair_records'):
        for record in role_facts.get(name,{}).values():
            if any(record.get(k)!=v for k,v in expected_record.items()):
                raise ValueError('semantic_role_record_version_or_model_changed')
    aggregate_keys={row['canonical_fingerprint'] for row in aggregates}
    for mapping,records in (('context_by_aggregate','context_records'),('completion_by_aggregate','completion_records'),
                            ('coverage_repair_by_aggregate','coverage_repair_records')):
        if any(aggregate not in aggregate_keys or key not in role_facts.get(records,{})
               for aggregate,key in role_facts.get(mapping,{}).items()):
            raise ValueError('semantic_role_source_context_mapping_changed')
    return {'schema_version': 'violet.production-pixiv-semantic-inputs.v1',
            'aggregates': canonical_fingerprint(aggregates), 'vocabulary': canonical_fingerprint(vocabulary),
            'role_facts': canonical_fingerprint(role_facts), 'judgments': canonical_fingerprint(judgments),
            'versions': semantic_versions()}


def verify_semantic_manifest(manifest, aggregates, vocabulary, facts, judgments, candidate_head):
    expected = semantic_input_identity(aggregates, vocabulary, facts, judgments)
    if manifest.get('input_identity') != expected or manifest.get('candidate_head') != candidate_head:
        raise ValueError('semantic_artifact_input_version_or_candidate_changed')
    processing = manifest.get('processing', {})
    if (processing.get('remaining_missing_pair_count') != 0 or processing.get('error_count') != 0
        or processing.get('selected_pair_count') != processing.get('judgment_count')):
        raise ValueError('semantic_pair_processing_incomplete')
    if any(row.get('error_state') for row in judgments):
        raise ValueError('semantic_judgment_business_error')
    if processing['judgment_count'] != len(judgments):
        raise ValueError('semantic_judgment_count_changed')
    return expected
