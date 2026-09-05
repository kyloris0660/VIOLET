"""PX3 apply-boundary support edges over the existing SourceConcept evidence."""

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import aliased

from ..models import (
    Media, SourceMetadataRecord, SourceConceptEvidence,
    SourceConceptProductMediaBinding, SourceConceptProductRun, SourceConceptSignal,
)
from .pixiv_metadata_projection_service import (
    _record_business_projection, _provenance_identity_matches, canonical_fingerprint,
)
from .pixiv_metadata_ingestion_service import (
    is_trusted_complete_pixiv_metadata_record, normalize_gallery_dl_records,
    PixivMetadataGateError, PIXIV_LEGACY_NORMALIZER_VERSION,
)
from .pixiv_identity_policy import (
    canonical_pixiv_work_id, canonical_pixiv_page_index,
    canonical_pixiv_provider_marker_consensus,
)


def verified_local_binding_provenance(record, projection=None):
    """Verify stored provenance without rewriting PX1/PX2 business identity.

    Historical gallery-dl rows predate stable_identity_key. Their already
    stored provider payload may prove the exact work/page through the existing
    normalizer. Redacted receipts and external artifact pointers cannot.
    """
    if not is_trusted_complete_pixiv_metadata_record(record):
        return False
    projection = projection or _record_business_projection(record)
    if _provenance_identity_matches(projection):
        return True
    provenance = record.provenance or {}
    raw = record.raw_metadata_json or {}
    if (projection['normalizer_version'] != PIXIV_LEGACY_NORMALIZER_VERSION
        or 'stable_identity_key' in provenance
        or provenance.get('adapter') != 'gallery-dl'
        or provenance.get('metadata_only') is not True
        or provenance.get('original_downloaded') is not False
        or canonical_pixiv_provider_marker_consensus(raw) != 'pixiv'):
        return False
    work_fields = [raw[key] for key in ('id', 'illust_id', 'work_id', 'pid') if key in raw]
    page_fields = [raw[key] for key in ('num', 'page_index', 'page') if key in raw]
    if (not work_fields or not page_fields
        or any(canonical_pixiv_work_id(value) != record.source_work_id for value in work_fields)
        or any(canonical_pixiv_page_index(value) != record.source_page_index for value in page_fields)):
        return False
    try:
        pages = normalize_gallery_dl_records([raw], record.source_work_id)
    except PixivMetadataGateError:
        return False
    return bool(len(pages) == 1 and pages[0]['page_index'] == record.source_page_index
                and pages[0]['creator_id'] == projection['provider_creator_id']
                and pages[0]['title'] == projection['title']
                and pages[0]['creator_name'] == projection['creator_display_name'])


def plan_media_bindings(session, run, *, lock=False):
    aggregates = {(a['work_id'], a['page_index']): a for a in run.consumer.aggregates}
    sources = {}
    query = session.query(SourceMetadataRecord).join(
        Media, Media.id == SourceMetadataRecord.media_id
    ).filter(
        SourceMetadataRecord.provider == 'pixiv',
        SourceMetadataRecord.source_work_id.in_({key[0] for key in aggregates}),
    )
    if lock:
        query = query.populate_existing().with_for_update(of=SourceMetadataRecord)
    for record in query.all():
        aggregate = aggregates.get((record.source_work_id, record.source_page_index))
        if aggregate is None or not is_trusted_complete_pixiv_metadata_record(record):
            continue
        projection = _record_business_projection(record)
        if not verified_local_binding_provenance(record, projection):
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
    revisions = dict(session.query(
        SourceMetadataRecord.id, SourceMetadataRecord.binding_revision,
    ).filter(SourceMetadataRecord.id.in_({edge[1] for edge in edges})).all())
    for signal_key, record_id, media_id in edges:
        for evidence in by_key.get(signal_key, []):
            session.add(SourceConceptProductMediaBinding(
                product_run_id=product_run.id, evidence_id=evidence.id,
                source_metadata_record_id=record_id, media_id=media_id,
                source_revision=revisions[record_id],
            ))
            count += 1
    session.flush()
    return count


def current_binding_columns_condition(binding, run, record):
    return and_(
        binding.product_run_id == run.id,
        run.status == 'active',
        binding.source_metadata_record_id == record.id,
        record.media_id == binding.media_id,
        record.provider == 'pixiv',
        binding.source_revision == record.binding_revision,
    )


def active_binding_condition(evidence_id, media_id):
    """Correlated support predicate shared by ordinary search and detail."""
    binding = SourceConceptProductMediaBinding
    run = SourceConceptProductRun
    return exists().where(and_(
        binding.evidence_id == evidence_id,
        binding.media_id == media_id,
        current_binding_columns_condition(binding, run, SourceMetadataRecord),
    )).correlate_except(binding, run, SourceMetadataRecord)


def current_product_alias_condition(alias):
    """An alias cannot outlive every current source that supplied its name."""
    run = aliased(SourceConceptProductRun)
    binding = aliased(SourceConceptProductMediaBinding)
    record = aliased(SourceMetadataRecord)
    evidence = aliased(SourceConceptEvidence)
    signal = aliased(SourceConceptSignal)
    product_owned = exists().where(run.resolver_run_id == alias.created_by_run_id).correlate_except(run)
    current_support = exists().where(and_(
        evidence.concept_id == alias.concept_id,
        signal.id == evidence.signal_id,
        or_(signal.id == alias.source_signal_id, signal.normalized_key == alias.alias_key),
        evidence.id == binding.evidence_id,
        current_binding_columns_condition(binding, run, record),
    )).correlate_except(run,binding,record,evidence,signal)
    return or_(~product_owned, current_support)


def current_product_evidence_condition(evidence):
    """概念详情仅展示当前有效产品依据；历史运行审计保持原始版本。"""
    run = aliased(SourceConceptProductRun)
    binding = aliased(SourceConceptProductMediaBinding)
    record = aliased(SourceMetadataRecord)
    owned = exists().where(run.resolver_run_id == evidence.run_id).correlate_except(run)
    supported = exists().where(and_(binding.evidence_id == evidence.id,
        current_binding_columns_condition(binding,run,record))).correlate_except(binding,run,record)
    return or_(~owned,supported)
