"""Provider-neutral evidence persistence helpers.

This module is durable provider/evidence infrastructure: it accepts already
validated provider-neutral contract plans and performs duplicate-safe writes to
the Phase 4.1 cache/evidence/candidate tables. It does not call providers,
upload files, create Entities, create confirmed assignments, mutate media tags,
or run localization.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from ..enums import EntityCandidateGeneratorEnum, EntityCandidateStatusEnum, EntityEvidenceTypeEnum, EntityTypeEnum
from ..models import EntityEvidence, MediaEntityAssignment, MediaEntityCandidate, ProviderCache
from .provider_evidence_contract import (
    EvidencePersistencePlan,
    EvidenceStrength,
    LocalizationStatus,
    ManualValidationStatus,
    SourceMatchClass,
    assert_public_payload_safe,
)


PHASE44C1 = "4.4-C1"
SOURCE_TYPE_EXTERNAL = "external"
PROVIDER_CACHE_REF_PREFIX = "provider_cache"


class EvidencePersistenceError(RuntimeError):
    """Raised when a provider evidence plan is unsafe or conflicts."""

    def __init__(self, code: str, *, media_id: int | None = None, detail: str | None = None):
        self.code = code
        self.media_id = media_id
        self.detail = detail
        suffix = f":{detail}" if detail else ""
        media_suffix = f" media_id={media_id}" if media_id is not None else ""
        super().__init__(f"{code}{media_suffix}{suffix}")


@dataclass(frozen=True)
class EvidencePersistenceOptions:
    """Options for a bounded evidence persistence pass."""

    write_candidates: bool = True
    strict: bool = True


def provider_cache_payload_ref(plan: EvidencePersistencePlan) -> str:
    """Return the deterministic ProviderCache natural-key reference."""
    query = plan.provider_query
    return f"{PROVIDER_CACHE_REF_PREFIX}:{query.provider_key}:{query.query_type}:{query.query_hash}"


def provider_cache_response_payload(plan: EvidencePersistencePlan) -> dict[str, Any]:
    """Build the public-safe JSON payload stored in ProviderCache."""
    payload = {
        "phase": PHASE44C1,
        "artifact_lifecycle": "durable_provider_evidence_infrastructure",
        "provider_query": plan.provider_query.to_public_dict(),
        "source_match": plan.source_match.to_public_dict(),
        "extracted_metadata": plan.extracted_metadata.to_public_dict(),
        "planned_entity_candidates": [candidate.to_public_dict() for candidate in plan.planned_entity_candidates],
        "confirmed_assignment_allowed": False,
        "entity_auto_create_allowed": False,
        "localization_status": plan.extracted_metadata.localization_status.value,
        "provider_cache_payload_ref": provider_cache_payload_ref(plan),
    }
    assert_public_payload_safe(payload)
    return payload


def evidence_summary(plan: EvidencePersistencePlan) -> str:
    """Build a deterministic public-safe EntityEvidence summary."""
    source = plan.source_match
    metadata = plan.extracted_metadata
    parts = [
        f"Phase {PHASE44C1} validated provider evidence",
        f"provider={plan.provider_query.provider_key}",
        f"media_id={plan.media_id}",
        f"match_class={source.match_class.value}",
        f"evidence_strength={source.evidence_strength.value}",
        f"manual_validation_status={source.manual_validation_status.value}",
        f"result_id={source.provider_result_id}",
        f"source_host={source.source_host}",
        f"score={source.score_value}",
        f"minimum_similarity={source.provider_minimum_similarity}",
        "artist=" + ",".join(metadata.artist_raw),
        "work=" + ",".join(metadata.work_raw),
        "character=" + ",".join(metadata.character_raw),
        "localization_status=pending",
        "confirmed_assignment_allowed=false",
        "entity_auto_create_allowed=false",
    ]
    summary = "; ".join(parts)
    assert_public_payload_safe(summary)
    return summary


def candidate_review_reason(plan: EvidencePersistencePlan, source_field: str) -> str:
    reason = (
        f"Phase {PHASE44C1} source-backed provider metadata; "
        f"source_field={source_field}; evidence_strength=strong; "
        "localization_status=pending; confirmed_assignment_allowed=false"
    )
    assert_public_payload_safe(reason)
    return reason


def _public_json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _url_host(value: str | None) -> str | None:
    if not value:
        return None
    return urlsplit(value).netloc.lower() or None


def _require_url_host_consistency(plan: EvidencePersistencePlan) -> None:
    source = plan.source_match
    host = source.source_host
    for field_name, url in (("source_url", source.source_url), ("post_url", source.post_url)):
        url_host = _url_host(url)
        if host and url_host and host != url_host:
            raise EvidencePersistenceError(
                "source_host_url_mismatch",
                media_id=plan.media_id,
                detail=f"{field_name} host {url_host!r} != source_host {host!r}",
            )


def validate_persistence_ready_plan(plan: EvidencePersistencePlan) -> None:
    """Fail closed unless a contract plan is safe for C1 positive writes."""
    assert_public_payload_safe(plan.to_public_dict())
    assert_public_payload_safe(plan.provider_query.request_shape_redacted)
    assert_public_payload_safe(provider_cache_response_payload(plan))
    _require_url_host_consistency(plan)

    query = plan.provider_query
    source = plan.source_match
    metadata = plan.extracted_metadata
    if query.query_hash_status != "present_valid" or not query.query_hash:
        raise EvidencePersistenceError("missing_or_invalid_query_hash", media_id=plan.media_id)
    if query.request_shape_status != "present" or not query.request_shape_redacted:
        raise EvidencePersistenceError("missing_or_invalid_request_shape", media_id=plan.media_id)
    if plan.provider_provenance_status != "ready" or not plan.provider_cache_persistence_allowed:
        raise EvidencePersistenceError("provider_provenance_not_ready", media_id=plan.media_id)
    if source.source_identifier_status != "present":
        raise EvidencePersistenceError("missing_source_identifier", media_id=plan.media_id)
    if source.match_class != SourceMatchClass.exact_or_near_exact:
        raise EvidencePersistenceError("unsupported_match_class", media_id=plan.media_id)
    if source.evidence_strength != EvidenceStrength.strong:
        raise EvidencePersistenceError("unsupported_evidence_strength", media_id=plan.media_id)
    if source.manual_validation_status != ManualValidationStatus.validated_correct:
        raise EvidencePersistenceError("manual_validation_not_validated_correct", media_id=plan.media_id)
    if source.score_value is not None and not math.isfinite(source.score_value):
        raise EvidencePersistenceError("non_finite_score", media_id=plan.media_id)
    if metadata.localization_status != LocalizationStatus.pending or not plan.localization_pending:
        raise EvidencePersistenceError("localization_not_pending", media_id=plan.media_id)
    if plan.confirmed_assignment_allowed:
        raise EvidencePersistenceError("confirmed_assignment_allowed", media_id=plan.media_id)
    if plan.entity_auto_create_allowed:
        raise EvidencePersistenceError("entity_auto_create_allowed", media_id=plan.media_id)
    if any(candidate.entity_id is not None for candidate in plan.planned_entity_candidates):
        raise EvidencePersistenceError("candidate_links_entity", media_id=plan.media_id)
    if not plan.entity_evidence_planned or not plan.provider_cache_planned:
        raise EvidencePersistenceError("positive_persistence_not_planned", media_id=plan.media_id)


def _empty_summary(*, apply: bool) -> dict[str, Any]:
    return {
        "phase": PHASE44C1,
        "mode": "apply" if apply else "dry_run",
        "success": True,
        "items": [],
        "counts": {
            "ProviderCache": {"planned": 0, "inserted": 0, "existing": 0, "skipped": 0},
            "EntityEvidence": {"planned": 0, "inserted": 0, "existing": 0, "skipped": 0},
            "MediaEntityCandidate": {"planned": 0, "inserted": 0, "existing": 0, "skipped": 0},
            "MediaEntityAssignment": {"inserted": 0},
            "Entity": {"inserted": 0},
        },
        "candidate_deferred_schema_constraint": False,
        "confirmed_assignment_created": False,
        "entity_created": False,
    }


def _bump(summary: dict[str, Any], table: str, key: str, amount: int = 1) -> None:
    summary["counts"][table][key] = summary["counts"][table].get(key, 0) + amount


def _find_provider_cache(db: Session, plan: EvidencePersistencePlan) -> ProviderCache | None:
    query = plan.provider_query
    return (
        db.query(ProviderCache)
        .filter(
            ProviderCache.provider == query.provider_key,
            ProviderCache.query_hash == query.query_hash,
            ProviderCache.query_type == query.query_type,
        )
        .first()
    )


def _provider_cache_conflict(existing: ProviderCache, plan: EvidencePersistencePlan) -> bool:
    return not (
        existing.response_status == "ok"
        and existing.error_class is None
        and _public_json_equal(existing.request_shape_redacted or {}, plan.provider_query.request_shape_redacted)
        and _public_json_equal(existing.response_json_redacted or {}, provider_cache_response_payload(plan))
    )


def _find_evidence(db: Session, plan: EvidencePersistencePlan) -> EntityEvidence | None:
    return (
        db.query(EntityEvidence)
        .filter(
            EntityEvidence.provider == plan.provider_query.provider_key,
            EntityEvidence.source_type == SOURCE_TYPE_EXTERNAL,
            EntityEvidence.evidence_type == EntityEvidenceTypeEnum.reverse_search,
            EntityEvidence.media_id == plan.media_id,
            EntityEvidence.query_hash == plan.provider_query.query_hash,
            EntityEvidence.payload_ref == provider_cache_payload_ref(plan),
        )
        .first()
    )


def _evidence_conflict(existing: EntityEvidence, plan: EvidencePersistencePlan) -> bool:
    return not (
        existing.entity_id is None
        and existing.tag_id is None
        and bool(existing.privacy_redacted)
        and existing.score == plan.source_match.score_value
        and existing.summary == evidence_summary(plan)
    )


def _find_candidate(
    db: Session,
    *,
    plan: EvidencePersistencePlan,
    entity_type: str,
    candidate_name: str,
) -> MediaEntityCandidate | None:
    return (
        db.query(MediaEntityCandidate)
        .filter(
            MediaEntityCandidate.media_id == plan.media_id,
            MediaEntityCandidate.entity_id.is_(None),
            MediaEntityCandidate.entity_type == EntityTypeEnum(entity_type),
            MediaEntityCandidate.candidate_name == candidate_name,
            MediaEntityCandidate.generator == EntityCandidateGeneratorEnum.external,
            MediaEntityCandidate.status == EntityCandidateStatusEnum.suggested,
        )
        .first()
    )


def _candidate_conflict(existing: MediaEntityCandidate, evidence_id: int, plan: EvidencePersistencePlan) -> bool:
    return not (
        existing.evidence_id == evidence_id
        and existing.label == "source_backed_provider_metadata"
        and existing.score == plan.source_match.score_value
    )


def _insert_provider_cache(db: Session, plan: EvidencePersistencePlan) -> ProviderCache:
    row = ProviderCache(
        provider=plan.provider_query.provider_key,
        query_hash=plan.provider_query.query_hash,
        query_type=plan.provider_query.query_type,
        request_shape_redacted=deepcopy(plan.provider_query.request_shape_redacted),
        response_status="ok",
        response_json_redacted=provider_cache_response_payload(plan),
        error_class=None,
    )
    db.add(row)
    db.flush()
    return row


def _insert_evidence(db: Session, plan: EvidencePersistencePlan) -> EntityEvidence:
    row = EntityEvidence(
        provider=plan.provider_query.provider_key,
        source_type=SOURCE_TYPE_EXTERNAL,
        evidence_type=EntityEvidenceTypeEnum.reverse_search,
        media_id=plan.media_id,
        entity_id=None,
        tag_id=None,
        query_hash=plan.provider_query.query_hash,
        payload_ref=provider_cache_payload_ref(plan),
        score=plan.source_match.score_value,
        summary=evidence_summary(plan),
        privacy_redacted=True,
    )
    db.add(row)
    db.flush()
    return row


def _insert_candidate(
    db: Session,
    *,
    plan: EvidencePersistencePlan,
    entity_type: str,
    candidate_name: str,
    source_field: str,
    evidence_id: int,
) -> MediaEntityCandidate:
    row = MediaEntityCandidate(
        media_id=plan.media_id,
        entity_id=None,
        entity_type=EntityTypeEnum(entity_type),
        label="source_backed_provider_metadata",
        candidate_name=candidate_name,
        score=plan.source_match.score_value,
        status=EntityCandidateStatusEnum.suggested,
        generator=EntityCandidateGeneratorEnum.external,
        evidence_id=evidence_id,
        review_reason=candidate_review_reason(plan, source_field),
    )
    db.add(row)
    db.flush()
    return row


def persist_provider_evidence_plans(
    db: Session,
    plans: Iterable[EvidencePersistencePlan],
    *,
    apply: bool,
    options: EvidencePersistenceOptions | None = None,
) -> dict[str, Any]:
    """Dry-run or apply idempotent ProviderCache/Evidence/Candidate writes."""
    options = options or EvidencePersistenceOptions()
    plans = list(plans)
    summary = _empty_summary(apply=apply)

    validation_errors: list[dict[str, Any]] = []
    for plan in plans:
        try:
            validate_persistence_ready_plan(plan)
        except EvidencePersistenceError as exc:
            validation_errors.append({"media_id": plan.media_id, "code": exc.code, "detail": exc.detail})
            summary["items"].append({"media_id": plan.media_id, "status": "blocked", "blocked_reason": exc.code})
    strict_validation = options.strict or apply
    if validation_errors and strict_validation:
        summary["success"] = False
        raise EvidencePersistenceError(
            "persistence_plan_validation_failed",
            detail=json.dumps(validation_errors, sort_keys=True),
        )

    if validation_errors:
        summary["success"] = False

    manage_transaction = bool(apply and not db.in_transaction())
    try:
        for plan in plans:
            if any(error["media_id"] == plan.media_id for error in validation_errors):
                continue
            item = {
                "media_id": plan.media_id,
                "status": "planned" if not apply else "applied",
                "provider_cache": None,
                "entity_evidence": None,
                "media_entity_candidates": [],
            }

            _bump(summary, "ProviderCache", "planned")
            existing_cache = _find_provider_cache(db, plan)
            if existing_cache:
                if _provider_cache_conflict(existing_cache, plan):
                    raise EvidencePersistenceError("conflict_existing_provider_cache", media_id=plan.media_id)
                _bump(summary, "ProviderCache", "existing")
                item["provider_cache"] = {"action": "existing", "id": existing_cache.id}
            elif apply:
                cache = _insert_provider_cache(db, plan)
                _bump(summary, "ProviderCache", "inserted")
                item["provider_cache"] = {"action": "inserted", "id": cache.id}
            else:
                item["provider_cache"] = {"action": "would_insert", "id": None}

            _bump(summary, "EntityEvidence", "planned")
            existing_evidence = _find_evidence(db, plan)
            if existing_evidence:
                if _evidence_conflict(existing_evidence, plan):
                    raise EvidencePersistenceError("conflict_existing_entity_evidence", media_id=plan.media_id)
                _bump(summary, "EntityEvidence", "existing")
                evidence_id = existing_evidence.id
                item["entity_evidence"] = {"action": "existing", "id": evidence_id}
            elif apply:
                evidence = _insert_evidence(db, plan)
                _bump(summary, "EntityEvidence", "inserted")
                evidence_id = evidence.id
                item["entity_evidence"] = {"action": "inserted", "id": evidence_id}
            else:
                evidence_id = None
                item["entity_evidence"] = {"action": "would_insert", "id": None}

            planned_candidates = list(plan.planned_entity_candidates)
            if options.write_candidates:
                _bump(summary, "MediaEntityCandidate", "planned", len(planned_candidates))
                for candidate in planned_candidates:
                    existing_candidate = _find_candidate(
                        db,
                        plan=plan,
                        entity_type=candidate.entity_type,
                        candidate_name=candidate.candidate_name,
                    )
                    if existing_candidate and evidence_id is not None:
                        if _candidate_conflict(existing_candidate, evidence_id, plan):
                            raise EvidencePersistenceError(
                                "conflict_existing_media_entity_candidate",
                                media_id=plan.media_id,
                                detail=candidate.candidate_name,
                            )
                        _bump(summary, "MediaEntityCandidate", "existing")
                        item["media_entity_candidates"].append(
                            {
                                "action": "existing",
                                "id": existing_candidate.id,
                                "entity_type": candidate.entity_type,
                                "candidate_name": candidate.candidate_name,
                            }
                        )
                    elif apply:
                        if evidence_id is None:
                            raise EvidencePersistenceError("candidate_requires_evidence", media_id=plan.media_id)
                        row = _insert_candidate(
                            db,
                            plan=plan,
                            entity_type=candidate.entity_type,
                            candidate_name=candidate.candidate_name,
                            source_field=candidate.source_field,
                            evidence_id=evidence_id,
                        )
                        _bump(summary, "MediaEntityCandidate", "inserted")
                        item["media_entity_candidates"].append(
                            {
                                "action": "inserted",
                                "id": row.id,
                                "entity_type": candidate.entity_type,
                                "candidate_name": candidate.candidate_name,
                            }
                        )
                    else:
                        item["media_entity_candidates"].append(
                            {
                                "action": "would_insert",
                                "id": None,
                                "entity_type": candidate.entity_type,
                                "candidate_name": candidate.candidate_name,
                            }
                        )
            else:
                summary["candidate_deferred_schema_constraint"] = True
                _bump(summary, "MediaEntityCandidate", "skipped", len(planned_candidates))

            summary["items"].append(item)

        if apply:
            assignment_count = (
                db.query(MediaEntityAssignment)
                .filter(MediaEntityAssignment.media_id.in_([plan.media_id for plan in plans]))
                .count()
            )
            if assignment_count:
                summary["success"] = False
                summary["confirmed_assignment_created"] = True
                raise EvidencePersistenceError("confirmed_assignment_detected", detail=str(assignment_count))
            if manage_transaction:
                db.commit()
    except Exception:
        if apply:
            db.rollback()
        raise

    assert_public_payload_safe(summary)
    return summary
