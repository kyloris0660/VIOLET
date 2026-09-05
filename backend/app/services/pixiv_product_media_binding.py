"""PX3 apply-boundary support edges over the existing SourceConcept evidence."""

from sqlalchemy import and_, exists

from ..models import (
    Media, SourceMetadataRecord, SourceConceptEvidence,
    SourceConceptProductMediaBinding, SourceConceptProductRun, SourceConceptSignal,
)
from .pixiv_metadata_projection_service import (
    _record_business_projection, _provenance_identity_matches, canonical_fingerprint,
)
from .pixiv_metadata_ingestion_service import is_trusted_complete_pixiv_metadata_record


def plan_media_bindings(session, run):
    aggregates = {(a['work_id'], a['page_index']): a for a in run.consumer.aggregates}
    sources = {}
    for record in session.query(SourceMetadataRecord).join(
        Media, Media.id == SourceMetadataRecord.media_id
    ).filter(
        SourceMetadataRecord.provider == 'pixiv',
        SourceMetadataRecord.source_work_id.in_({key[0] for key in aggregates}),
    ).all():
        aggregate = aggregates.get((record.source_work_id, record.source_page_index))
        if aggregate is None or not is_trusted_complete_pixiv_metadata_record(record):
            continue
        projection = _record_business_projection(record)
        if not _provenance_identity_matches(projection):
            continue
        if canonical_fingerprint(projection) not in aggregate['source_fingerprints']:
            continue
        if canonical_fingerprint(projection['provenance']) not in aggregate['provenance_fingerprints']:
            continue
        sources.setdefault(aggregate['canonical_fingerprint'], []).append(record)
    edges = set()
    evidence_signals = {evidence.signal_key for evidence in run.resolution.evidence}
    # Signals carry frozen work/page + aggregate provenance, never database IDs.
    for signal in run.resolution.signals:
        if signal.signal_key not in evidence_signals:
            continue
        payload = signal.evidence_payload or {}
        aggregate = aggregates.get((payload.get('work_id'), payload.get('page_index')))
        if signal.provider != 'pixiv' or aggregate is None:
            continue
        if payload.get('aggregate_fingerprint') != aggregate['canonical_fingerprint']:
            raise ValueError('px3_binding_signal_provenance_mismatch')
        for record in sources.get(aggregate['canonical_fingerprint'], []):
            edges.add((signal.signal_key, record.id, record.media_id))
    return sorted(edges)


def binding_plan_summary(edges):
    return {
        'planned_signal_source_media_binding_count': len(edges),
        'planned_media_binding_count': len({edge[2] for edge in edges}),
        'planned_source_record_binding_count': len({edge[1] for edge in edges}),
        'local_binding_fingerprint': canonical_fingerprint(edges),
        'binding_write_count': 0,
    }


def persist_media_bindings(session, product_run, edges):
    by_key = {}
    for evidence, key in session.query(SourceConceptEvidence, SourceConceptSignal.signal_key).join(
        SourceConceptSignal, SourceConceptSignal.id == SourceConceptEvidence.signal_id
    ).filter(SourceConceptEvidence.run_id == product_run.resolver_run_id).all():
        by_key.setdefault(key, []).append(evidence)
    count = 0
    for signal_key, record_id, media_id in edges:
        for evidence in by_key.get(signal_key, []):
            session.add(SourceConceptProductMediaBinding(
                product_run_id=product_run.id, evidence_id=evidence.id,
                source_metadata_record_id=record_id, media_id=media_id,
            ))
            count += 1
    session.flush()
    return count


def active_binding_condition(evidence_id, media_id):
    """Correlated support predicate shared by ordinary search and detail."""
    binding = SourceConceptProductMediaBinding
    run = SourceConceptProductRun
    return exists().where(and_(
        binding.evidence_id == evidence_id,
        binding.media_id == media_id,
        binding.product_run_id == run.id,
        run.status == 'active',
        binding.source_metadata_record_id == SourceMetadataRecord.id,
        SourceMetadataRecord.media_id == binding.media_id,
        SourceMetadataRecord.provider == 'pixiv',
    )).correlate_except(binding, run, SourceMetadataRecord)
