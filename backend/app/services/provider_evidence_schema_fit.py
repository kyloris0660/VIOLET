"""Static, non-mutating schema-fit audit for Phase 4.4-C0."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PHASE44C0_SCHEMA_FIT_AUDIT: dict[str, Any] = {
    "phase": "4.4-C0",
    "schema_fit_status": "sufficient_with_json_payload",
    "non_mutating": True,
    "db_write_allowed": False,
    "summary": (
        "Phase 4.1 schema can support a narrow C1 persistence pass for "
        "validated high-confidence provider evidence by using ProviderCache "
        "JSON, EntityEvidence provenance, nullable-entity MediaEntityCandidate "
        "suggestions, and NegativeLookupCache for discarded/negative outcomes. "
        "ProviderCache and NegativeLookupCache planning still require a real "
        "query_hash and redacted request shape from local details/raw artifacts. "
        "First-class queryable columns for match_class, manual validation, "
        "and localization state should remain a follow-up design."
    ),
    "questions": {
        "provider_raw_redacted_cache": {
            "status": "sufficient_with_json_payload",
            "mapping": "ProviderCache.request_shape_redacted + ProviderCache.response_json_redacted",
            "notes": "Store redacted normalized contract payloads only when query_hash/request_shape are present; no API keys, local paths, filenames, or raw image bytes.",
        },
        "source_post_id_host_url": {
            "status": "sufficient_with_json_payload",
            "mapping": "ProviderCache.response_json_redacted.source_match plus EntityEvidence.payload_ref/summary",
            "notes": "ExternalIdentity requires an Entity row and should not be created in C1 unless an entity already exists and policy approves it.",
        },
        "score_rank_threshold_confidence": {
            "status": "sufficient_with_json_payload",
            "mapping": "ProviderCache.response_json_redacted.source_match; EntityEvidence.score for one normalized evidence score",
            "notes": "Provider-specific score semantics stay in JSON because scores are not comparable across providers.",
        },
        "artist_work_character_raw_metadata": {
            "status": "sufficient_with_json_payload",
            "mapping": "ProviderCache.response_json_redacted.extracted_metadata and optional MediaEntityCandidate rows with entity_id NULL",
            "notes": "Candidate rows can preserve suggested names without forcing trusted Entity creation.",
        },
        "localization_pending": {
            "status": "sufficient_with_json_payload",
            "mapping": "ProviderCache.response_json_redacted.extracted_metadata.localization_status",
            "notes": "EntityTranslation rows require Entity rows, so raw provider metadata localization remains pending until entity/alias policy approves it.",
        },
        "provider_provenance": {
            "status": "sufficient",
            "mapping": "provider, query_hash, query_type, payload_ref, evidence_type=reverse_search, source_type=external",
            "notes": "ProviderCache and EntityEvidence both preserve provider/query provenance.",
        },
        "manual_validation_status": {
            "status": "sufficient_with_json_payload",
            "mapping": "ProviderCache.response_json_redacted.source_match.manual_validation_status and EntityEvidence.summary",
            "notes": "A future additive column/table may be useful if validation state becomes a first-class query surface.",
        },
        "discard_negative_result": {
            "status": "sufficient",
            "mapping": "NegativeLookupCache.reason plus ProviderCache.response_json_redacted.source_match.match_class",
            "notes": "Low-confidence manually wrong results can be cached as negative/discard outcomes without positive candidates only when query metadata is present.",
        },
        "suggestion_only_candidate": {
            "status": "sufficient",
            "mapping": "MediaEntityCandidate with entity_id NULL, generator=external, status=suggested, evidence_id set",
            "notes": "Current schema does not force confirmed assignments for candidates.",
        },
        "trusted_entity_creation_pressure": {
            "status": "sufficient",
            "mapping": "Entity is not required for MediaEntityCandidate; ExternalIdentity does require Entity",
            "notes": "C1 should avoid ExternalIdentity unless attaching to an already approved/manual entity.",
        },
    },
    "per_table_mapping_plan": {
        "ProviderCache": {
            "c1_use": "Store provider-neutral ProviderQuery, SourceMatch, ExtractedProviderMetadata, run outcome, and redacted provider fields.",
            "c0_write": False,
        },
        "NegativeLookupCache": {
            "c1_use": "Store low-confidence manually wrong/discarded outcomes by provider/query hash.",
            "c0_write": False,
        },
        "EntityEvidence": {
            "c1_use": "Store reverse_search evidence for validated high-confidence matches, pointing payload_ref to ProviderCache.",
            "c0_write": False,
        },
        "MediaEntityCandidate": {
            "c1_use": "Create suggestion-only artist/work/character candidates with entity_id NULL after evidence exists.",
            "c0_write": False,
        },
        "MediaEntityAssignment": {
            "c1_use": "Out of scope; confirmed assignments remain blocked.",
            "c0_write": False,
        },
        "Entity": {
            "c1_use": "Out of scope unless a future manual/import/trusted policy explicitly creates or links one.",
            "c0_write": False,
        },
        "EntityExternalIdentity": {
            "c1_use": "Defer unless linking source/post IDs to an already approved Entity is explicitly designed.",
            "c0_write": False,
        },
        "EntityAlias_EntityTranslation_TagTranslation": {
            "c1_use": "Localization/alias work stays pending and must use existing overrideable localization/entity translation paths later.",
            "c0_write": False,
        },
    },
    "c1_persistence_recommendation": {
        "scope": "validated_high_confidence_evidence_only",
        "recommended_without_migration": True,
        "write_order": [
            "ProviderCache redacted contract payload with real query_hash/request_shape",
            "EntityEvidence reverse_search row for 2687 and 2670",
            "MediaEntityCandidate suggestion rows with entity_id NULL for artist/work/character metadata",
            "NegativeLookupCache rows for 2690, 2654, 2647 if C1 includes negative policy and real query metadata",
        ],
        "blocked": [
            "confirmed MediaEntityAssignment",
            "automatic Entity creation",
            "ExternalIdentity rows that require creating trusted Entity rows",
            "media_tags mutation",
            "TagTranslation mutation",
            "localization execution",
        ],
        "follow_up_design": [
            "first-class provider_result/match_class/manual_validation columns if broad querying becomes necessary",
            "provider-neutral conflict merge policy before second-provider persistence",
            "entity/alias localization workflow for raw provider proper nouns",
        ],
    },
}


def audit_provider_evidence_contract_fit() -> dict[str, Any]:
    """Return a copy of the static audit without DB access or side effects."""
    return deepcopy(PHASE44C0_SCHEMA_FIT_AUDIT)
