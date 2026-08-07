#!/usr/bin/env python3
"""Build and serve the private SCV2-SV1B 40-case manual acceptance harness."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts import run_phase45_scv2_ml1_multilingual_alias_source_metadata_closure as ml1  # noqa: E402
from scripts import run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure as sv1b  # noqa: E402
from app.services.creator_identity_policy import (  # noqa: E402
    CREATOR_IDENTITY_POLICY_VERSION,
    is_placeholder_creator_name,
)
from app.services.tag_localization_policy import (  # noqa: E402
    LOCALIZATION_REVOCATION_POLICY_VERSION,
    MANUALLY_REVOKED_TRANSLATION_TAG_ORDER,
    MANUALLY_REVOKED_TRANSLATION_TAGS,
    effective_localization_disposition,
)


HARNESS_VERSION = "sv1b_phase_delta_manual_acceptance_harness_v2"
FINAL_HARNESS_PROOF_NAME = (
    "manual-acceptance-harness-final-binding-proof.json"
)
AUDIT_CLOSEOUT_FINAL_BINDING_V2_NAME = (
    "manual-acceptance-harness-audit-closeout-final-binding-v2-proof.json"
)
AUDIT_CLOSEOUT_VALIDATION_PROOF_NAME = (
    "fresh-replay-v2-audit-closeout-validation-proof.json"
)
AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME = (
    "manual-acceptance-harness-audit-closeout-final-binding-v3-proof.json"
)
AUDIT_CLOSEOUT_VALIDATION_PROOF_V3_NAME = (
    "fresh-replay-v2-audit-closeout-validation-v3-proof.json"
)
AUDIT_CLOSEOUT_FINAL_BINDING_V4_NAME = (
    "manual-acceptance-harness-audit-closeout-final-binding-v4-proof.json"
)
AUDIT_CLOSEOUT_VALIDATION_PROOF_V4_NAME = (
    "fresh-replay-v2-audit-closeout-validation-v4-proof.json"
)
MANUAL_ACCEPTANCE_REPAIR_VALIDATION_V5_NAME = (
    "manual-acceptance-repair-v5-validation-proof.json"
)
MANUAL_ACCEPTANCE_FINAL_BINDING_V5_NAME = (
    "manual-acceptance-harness-final-binding-v5-proof.json"
)
STRICT_BROWSER_PREVALIDATION_V5_NAME = (
    "manual-acceptance-v5-strict-browser-prevalidation-proof.json"
)
EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT = (
    "6e18cbdd046b91681563f2538a3f17256f299feb5b955af14d5d76f9f409b0d5"
)
DEFAULT_PORT = 8031
CATEGORY_COUNTS = {
    "pixiv_metadata": 12,
    "creator_clustering": 8,
    "shared_name_cannot_link": 6,
    "ai_tag_localization": 8,
    "search_and_negative": 6,
}

V5_PROTECTED_SOURCE_PATHS_BASE = (
    "run-identity.json",
    "database-ownership-and-baseline-proof.json",
    "acquisition-closure-and-package-proof.json",
    "acquired-nonderived-evidence-package-private.json",
    "candidate-page-media-manifest-private.jsonl",
    "primary-creator-family-outcomes-private.json",
    "primary-search-workload-and-results-private.json",
    "localization/localization-manifest-private.json",
    "localization/localization-manual-review-pending.json",
)


class ManualAcceptanceHarnessError(RuntimeError):
    pass


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def _safe_media_label(media_hash: str) -> str:
    return sv1b.sha256_payload({"media": str(media_hash)})


def _case(
    case_id: str,
    category: str,
    *,
    media_hash: str,
    title: str,
    expected_behavior: str,
    actual_result: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "title": title,
        "safe_media_label": _safe_media_label(media_hash),
        "expected_behavior": expected_behavior,
        "actual_result": dict(actual_result),
        "provenance": dict(provenance),
    }


def _fallback_media_hash(session: Any) -> str:
    value = session.execute(text("""
        SELECT hash FROM blombooru_media
        WHERE hash IS NOT NULL AND (mime_type LIKE 'image/%' OR CAST(file_type AS text)='image')
        ORDER BY hash LIMIT 1
    """)).scalar()
    if not value:
        raise ManualAcceptanceHarnessError("manual_acceptance_image_media_missing")
    return str(value)


def _newly_acquired_exact_metadata_membership(
    output: Path,
) -> set[tuple[str, str, str, int]]:
    closure = sv1b.read_json(
        output / "acquisition-closure-and-package-proof.json"
    )
    package = sv1b.read_json(
        output / "acquired-nonderived-evidence-package-private.json"
    )
    if (
        closure.get("passed") is not True
        or not closure.get("accepted_acquisition_package_fingerprint")
        or package.get("schema_version")
        != "sv1b.stable-replay-evidence.v2"
        or package.get("package_fingerprint")
        != (closure.get("package") or {}).get(
            "stable_package_fingerprint"
        )
        or any(
            int((package.get("external_route_budget") or {}).get(key) or 0)
            != 0
            for key in (
                "gallery_dl_requests",
                "llm_calls",
                "media_downloads",
                "provider_requests",
                "thumbnail_downloads",
            )
        )
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_acquisition_package_binding_invalid"
        )
    members = {
        (
            str(row.get("media_content_key") or ""),
            str(row.get("provider_record_key") or ""),
            str(row.get("source_work_id") or ""),
            int(row.get("source_page_index") or 0),
        )
        for row in (
            (package.get("tables") or {}).get(
                "source_metadata_records"
            )
            or ()
        )
        if row.get("provider") == "pixiv"
        and row.get("metadata_kind") == "pixiv_ingestion_gate"
        and row.get("data_type_label")
        == "authenticated_provider_metadata"
        and row.get("status") == "metadata_complete"
        and (row.get("provenance") or {}).get("source")
        == "gallery_dl_authenticated_metadata"
        and row.get("media_content_key")
        and row.get("provider_record_key")
        and row.get("source_work_id") is not None
        and row.get("source_page_index") is not None
    }
    if len(members) < 12:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_acquisition_package_phase_delta_gap"
        )
    return members


def _validated_pixiv_metadata_rows(
    session: Any, output: Path
) -> list[dict[str, Any]]:
    phase_delta_membership = _newly_acquired_exact_metadata_membership(output)
    package = sv1b.read_json(
        output / "acquired-nonderived-evidence-package-private.json"
    )
    package_rows = {
        (
            str(row.get("media_content_key") or ""),
            str(row.get("provider_record_key") or ""),
            str(row.get("source_work_id") or ""),
            int(row.get("source_page_index") or 0),
        ): row
        for row in (
            (package.get("tables") or {}).get("source_metadata_records")
            or ()
        )
    }
    candidate_rows = sv1b.read_jsonl(
        output / "candidate-page-media-manifest-private.jsonl"
    )
    candidate_membership = {
        (
            str(row.get("media_stable_key") or ""),
            str(row.get("stable_work_id") or ""),
            int(row.get("requested_page_index") or 0),
        )
        for row in candidate_rows
        if row.get("provider") == "pixiv"
    }
    rows = list(session.execute(text("""
        SELECT r.id,r.provider,r.provider_record_key,r.source_work_id,r.source_page_index,
               r.metadata_kind,r.data_type_label,r.raw_metadata_json,
               r.artist_id,r.artist_name,r.title,r.status,r.retrieved_at,r.provenance,
               m.hash,
               (SELECT COUNT(*) FROM blombooru_source_tag_observations o
                WHERE o.source_metadata_record_id=r.id AND o.status IN ('observed','active','accepted')) AS tag_count
        FROM blombooru_source_metadata_records r
        JOIN blombooru_media m ON m.id=r.media_id
        WHERE r.provider='pixiv'
          AND r.status IN ('metadata_complete','observed','active','accepted')
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
          AND r.source_work_id IS NOT NULL AND r.source_page_index IS NOT NULL
        ORDER BY r.provider_record_key,m.hash
    """)).mappings())
    eligible: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        member = (
            str(row["hash"]),
            str(row["provider_record_key"]),
            str(row["source_work_id"]),
            int(row["source_page_index"]),
        )
        if member not in phase_delta_membership:
            continue
        package_row = package_rows.get(member)
        if package_row is None:
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_media_source_package_membership_missing"
            )
        if (
            (row.get("provenance") or {}).get("source")
            != "gallery_dl_authenticated_metadata"
            or not sv1b._is_trusted_exact_complete_record(
                row,
                str(row["source_work_id"]),
                int(row["source_page_index"]),
            )
        ):
            continue
        if (
            str(package_row.get("artist_id") or "")
            != str(row.get("artist_id") or "")
            or (
                str(row["hash"]),
                str(row["source_work_id"]),
                int(row["source_page_index"]),
            )
            not in candidate_membership
        ):
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_media_work_page_creator_binding_mismatch"
            )
        row["source_binding_fingerprint"] = sv1b.sha256_payload(
            {
                "media_content_key": str(row["hash"]),
                "provider_record_key": str(row["provider_record_key"]),
                "work_id": str(row["source_work_id"]),
                "page_index": int(row["source_page_index"]),
                "creator_stable_id": str(row.get("artist_id") or ""),
            }
        )
        eligible.append(row)

    bindings_by_media: dict[str, set[tuple[str, int, str]]] = {}
    for row in eligible:
        bindings_by_media.setdefault(str(row["hash"]), set()).add(
            (
                str(row["source_work_id"]),
                int(row["source_page_index"]),
                str(row.get("artist_id") or ""),
            )
        )
    unambiguous = [
        row
        for row in eligible
        if len(bindings_by_media[str(row["hash"])]) == 1
    ]
    selected: list[dict[str, Any]] = []
    selected_media: set[str] = set()
    for row in unambiguous:
        media_hash = str(row["hash"])
        if media_hash in selected_media:
            continue
        selected.append(row)
        selected_media.add(media_hash)
        if len(selected) == 12:
            break
    if len(selected) != 12:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_new_exact_metadata_case_gap:{len(selected)}"
        )
    return selected


def _pixiv_metadata_cases(session: Any, output: Path) -> list[dict[str, Any]]:
    rows = _validated_pixiv_metadata_rows(session, output)
    cases = []
    for index, row in enumerate(rows, 1):
        provenance = row.get("provenance") or {}
        cases.append(_case(
            f"A{index:02d}",
            "pixiv_metadata",
            media_hash=str(row["hash"]),
            title=f"Pixiv work/page metadata #{index}",
            expected_behavior="The displayed work, page, creator, title, and provider tags belong to this exact media page.",
            actual_result={
                "work_id": row.get("source_work_id"),
                "page_index": row.get("source_page_index"),
                "creator_stable_id": row.get("artist_id"),
                "creator_display_name": row.get("artist_name"),
                "work_title": row.get("title"),
                "provider_tag_count": int(row.get("tag_count") or 0),
                "record_status": row.get("status"),
                "media_source_binding_verified": True,
                "source_binding_fingerprint": row[
                    "source_binding_fingerprint"
                ],
            },
            provenance={
                "phase_delta": "newly_acquired_exact_metadata",
                "derived_from_current_proofs": True,
                "provider": "pixiv",
                "metadata_status": row.get("status"),
                "retrieved_at": row.get("retrieved_at"),
                "parser_version": provenance.get("parser_version"),
                "policy_version": provenance.get("policy_version"),
                "media_content_key_ref": sv1b.sha256_payload(
                    str(row["hash"])
                ),
            },
        ))
    return cases


def validate_pixiv_case_media_source_bindings(
    cases: Iterable[Mapping[str, Any]],
    session: Any,
    output: Path,
) -> dict[str, Any]:
    metadata_cases = [
        dict(row) for row in cases if row.get("category") == "pixiv_metadata"
    ]
    rows = _validated_pixiv_metadata_rows(session, output)
    expected = {
        _safe_media_label(str(row["hash"])): row for row in rows
    }
    labels = [str(row.get("safe_media_label") or "") for row in metadata_cases]
    if len(labels) != 12 or len(set(labels)) != 12:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_metadata_media_binding_not_one_to_one"
        )
    for case in metadata_cases:
        row = expected.get(str(case["safe_media_label"]))
        actual = case.get("actual_result") or {}
        if row is None or (
            str(actual.get("work_id") or "") != str(row["source_work_id"])
            or int(actual.get("page_index") or 0)
            != int(row["source_page_index"])
            or str(actual.get("creator_stable_id") or "")
            != str(row.get("artist_id") or "")
            or str(actual.get("source_binding_fingerprint") or "")
            != str(row["source_binding_fingerprint"])
        ):
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_metadata_case_binding_drift"
            )
    membership = sorted(
        str((row.get("actual_result") or {})["source_binding_fingerprint"])
        for row in metadata_cases
    )
    return {
        "case_count": len(metadata_cases),
        "unique_media_count": len(set(labels)),
        "binding_membership_fingerprint": sv1b.sha256_payload(membership),
        "passed": True,
    }


def _creator_clustering_cases(
    session: Any, output: Path, primary_database: str
) -> list[dict[str, Any]]:
    current_outcomes = sv1b.read_json(output / "primary-creator-family-outcomes-private.json")
    accepted_outcomes = sv1b.read_jsonl(sv1b.ML2_PRIVATE / "family-closure-ledger.jsonl")
    accepted_mapping = sv1b._family_identity_mapping(
        "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715", accepted_outcomes
    )
    accepted_mapping = {
        stable: concept for stable, concept in accepted_mapping.items()
        if concept in sv1b.accepted_family_concept_keys()
    }
    accepted_state = sv1b._creator_family_state(
        "blombooru_scv2_ml2_identity_closure_reviewfix_test_20260715", accepted_mapping
    )
    current_mapping = sv1b._family_identity_mapping(primary_database, current_outcomes)
    current_state = sv1b._creator_family_state(primary_database, current_mapping)
    changed = [
        stable for stable in sorted(current_state)
        if stable not in accepted_state
        or sv1b.canonical_json(current_state[stable]) != sv1b.canonical_json(accepted_state[stable])
    ]
    preservation_fillers = [
        stable for stable in sorted(set(current_state).intersection(accepted_state))
        if stable not in changed
    ]
    selected = [(stable, "new_or_materially_changed_creator_component") for stable in changed[:8]]
    selected.extend(
        (stable, "accepted_family_preservation_filler")
        for stable in preservation_fillers[: 8 - len(selected)]
    )
    if len(selected) != 8:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_creator_component_case_gap:{len(selected)}"
        )
    cases = []
    for index, (stable, delta_kind) in enumerate(selected, 1):
        state = current_state[stable]
        concept_key = str(state["concept_key"])
        row = session.execute(text("""
            SELECT c.primary_display_name,
                   MIN(m.hash) FILTER (WHERE m.hash IS NOT NULL AND
                     (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')) AS media_hash
            FROM blombooru_source_concepts c
            LEFT JOIN blombooru_source_concept_evidence e
              ON e.concept_id=c.id AND e.status='active'
              AND e.evidence_type='trusted_creator_media_support'
            LEFT JOIN blombooru_media m ON m.id=e.media_id
            WHERE c.concept_key=:concept_key
            GROUP BY c.id,c.primary_display_name
        """), {"concept_key": concept_key}).mappings().one()
        if not row.get("media_hash"):
            raise ManualAcceptanceHarnessError("manual_acceptance_creator_component_media_missing")
        raw_aliases = [
            str(value[0])
            for value in state.get("aliases") or ()
            if len(value) >= 3 and value[2] == "active"
        ]
        excluded_placeholder_aliases = sorted(
            {value for value in raw_aliases if is_placeholder_creator_name(value)}
        )
        aliases = sorted(
            {
                value
                for value in raw_aliases
                if not is_placeholder_creator_name(value)
            }
        )
        identity_union_created = len(aliases) > 1
        cases.append(_case(
            f"B{index:02d}",
            "creator_clustering",
            media_hash=str(row["media_hash"]),
            title=f"Creator cluster #{index}",
            expected_behavior=(
                "Only non-placeholder names/accounts tied to the same audited provider stable creator ID may share identity; "
                "otherwise the names remain independent searchable evidence."
            ),
            actual_result={
                "primary_display_name": row.get("primary_display_name"),
                "identity_aliases": aliases,
                "identity_alias_count": len(aliases),
                "policy_excluded_placeholder_aliases": (
                    excluded_placeholder_aliases
                ),
                "identity_union_created": identity_union_created,
                "conservative_independence_preserved": not identity_union_created,
                "identity_union_basis": (
                    "same_provider_stable_creator_id"
                    if identity_union_created
                    else "insufficient_multiple_strong_aliases"
                ),
                "stable_identity_anchor_present": bool(stable),
                "media_count_used_as_identity_evidence": False,
                "string_similarity_used_as_identity_evidence": False,
                "media_support_count": len(state.get("media_support") or ()),
                "concept_ref": sv1b.sha256_payload(concept_key),
                "lifecycle_status": state.get("status"),
                "lifecycle_correct": state.get("status") == "active",
                "expected_membership_fingerprint": sv1b.sha256_payload({
                    "identity_aliases": aliases,
                    "stable_identity_anchor": stable,
                    "policy_version": CREATOR_IDENTITY_POLICY_VERSION,
                }),
            },
            provenance={
                "phase_delta": delta_kind,
                "available_changed_component_count": len(changed),
                "derived_from_current_proofs": True,
                "stable_identity_ref": sv1b.sha256_payload(stable),
                "source_layer_only": True,
                "creator_identity_policy_version": (
                    CREATOR_IDENTITY_POLICY_VERSION
                ),
                "search_result_union_is_identity_union": False,
            },
        ))
    return cases


def _shared_name_cases(session: Any, output: Path) -> list[dict[str, Any]]:
    initial_pages = sv1b.read_jsonl(output / "candidate-page-media-manifest-private.jsonl")
    initially_open = {
        (str(row["media_stable_key"]), str(row["stable_work_id"]), int(row["requested_page_index"]))
        for row in initial_pages
        if str(row.get("acquisition_state")) in {
            sv1b.PAGE_OUTCOME_UNACQUIRED,
            sv1b.PAGE_OUTCOME_CONFLICTING,
            sv1b.PAGE_OUTCOME_UNEXPLAINED,
        }
    }
    observed = session.execute(text("""
        SELECT o.canonical_name_key,m.hash,r.source_work_id,r.source_page_index
        FROM blombooru_source_name_observations o
        JOIN blombooru_source_metadata_records r ON r.id=o.source_metadata_record_id
        JOIN blombooru_media m ON m.id=r.media_id
        WHERE o.status IN ('observed','active','accepted') AND r.provider='pixiv'
    """)).mappings()
    delta_aliases = {
        str(row["canonical_name_key"])
        for row in observed
        if (str(row["hash"]), str(row["source_work_id"]), int(row["source_page_index"] or 0))
        in initially_open
    }
    rows = list(session.execute(text("""
        SELECT i.search_key,COUNT(DISTINCT i.concept_id) AS concept_count,
               ARRAY_AGG(DISTINCT i.concept_id ORDER BY i.concept_id) AS concept_ids,
               COUNT(DISTINCT f.pair_id) FILTER
                 (WHERE f.relation='cannot_link' AND f.status='blocked') AS cannot_pair_count,
               MIN(m.hash) AS media_hash
        FROM blombooru_source_concept_search_index i
        JOIN blombooru_source_concepts c ON c.id=i.concept_id AND c.status='active'
        LEFT JOIN blombooru_source_concept_evidence e
          ON e.concept_id=c.id AND e.status='active' AND e.media_id IS NOT NULL
        LEFT JOIN blombooru_media m ON m.id=e.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        LEFT JOIN blombooru_source_concept_fallback_search_index f
          ON f.alias_key=i.search_key
        WHERE i.status='active'
        GROUP BY i.search_key
        HAVING COUNT(DISTINCT i.concept_id)>1 AND MIN(m.hash) IS NOT NULL
           AND COUNT(DISTINCT f.pair_id) FILTER
             (WHERE f.relation='cannot_link' AND f.status='blocked')>0
        ORDER BY CASE WHEN i.search_key=ANY(:delta_aliases) THEN 0 ELSE 1 END,
                 concept_count DESC,i.search_key LIMIT 6
    """), {"delta_aliases": sorted(delta_aliases) or ["__no_delta_alias__"]}).mappings())
    if len(rows) != 6:
        raise ManualAcceptanceHarnessError(f"manual_acceptance_shared_name_case_gap:{len(rows)}")
    selected_delta_count = sum(str(row["search_key"]) in delta_aliases for row in rows)
    cases = []
    for index, row in enumerate(rows, 1):
        actual_ids = ml1.runtime_and_terms(session, str(row["search_key"]))
        concept_ids = {int(value) for value in row.get("concept_ids") or ()}
        identity_union_created = len(concept_ids) != int(row.get("concept_count") or 0)
        cannot_link_safety = bool(
            not identity_union_created
            and int(row.get("cannot_pair_count") or 0) > 0
            and len(concept_ids) > 1
        )
        cases.append(_case(
            f"C{index:02d}",
            "shared_name_cannot_link",
            media_hash=str(row["media_hash"]),
            title=f"Shared-name/cannot-link #{index}",
            expected_behavior="A shared display name may return a supported result union, but cannot-linked creator concepts must remain separate identities.",
            actual_result={
                "shared_alias": row.get("search_key"),
                "separate_active_concept_count": int(row.get("concept_count") or 0),
                "cannot_link_pair_count": int(row.get("cannot_pair_count") or 0),
                "runtime_result_count": len(actual_ids),
                "identity_union_created": identity_union_created,
                "cannot_link_safety_passed": cannot_link_safety,
                "expected_component_membership_fingerprint": sv1b.sha256_payload(sorted(concept_ids)),
                "lifecycle_correct": len(concept_ids) == int(row.get("concept_count") or 0),
            },
            provenance={
                "phase_delta": (
                    "newly_acquired_alias_or_graph_edge"
                    if str(row["search_key"]) in delta_aliases
                    else "baseline_shared_name_preservation_filler"
                ),
                "available_phase_delta_case_count": selected_delta_count,
                "derived_from_current_proofs": True,
                "source": "active SourceConcept search index plus cannot-link overlay",
                "search_result_union_is_identity_union": identity_union_created,
            },
        ))
    return cases


def _localization_cases(session: Any, output: Path) -> list[dict[str, Any]]:
    manifest = sv1b.read_json(output / "localization/localization-manifest-private.json")
    new_names = {str(row["canonical_name"]) for row in manifest.get("eligible_rows") or ()}
    exclusions = {
        str(row["canonical_name"]): dict(row)
        for row in manifest.get("explicit_exclusions") or ()
    }
    pending_path = output / "localization/localization-manual-review-pending.json"
    pending_proof = (
        sv1b.read_json(pending_path)
        if pending_path.is_file()
        else {"manual_localization_review_pending": []}
    )
    pending = {
        str(row["canonical_name"]): dict(row)
        for row in pending_proof.get("manual_localization_review_pending") or ()
    }
    if len(pending) > 8:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_localization_pending_threshold_exceeded:{len(pending)}"
        )
    revoked_names = list(MANUALLY_REVOKED_TRANSLATION_TAG_ORDER)
    revoked_rows = list(session.execute(text("""
        SELECT t.name AS canonical_name,CAST(t.category AS text) AS category,
               MIN(m.hash) AS media_hash,COUNT(DISTINCT mt.media_id) AS media_count,
               MIN(tr.display_name) AS historical_display_name,
               COUNT(tr.id) FILTER (
                 WHERE tr.language='zh-CN' AND tr.status IN ('translated','reviewed')
               ) AS historical_accepted_translation_count
        FROM blombooru_tags t
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        JOIN blombooru_media m ON m.id=mt.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        LEFT JOIN blombooru_tag_translations tr
          ON tr.canonical_name=t.name AND tr.language='zh-CN'
        WHERE t.name=ANY(:revoked_names)
        GROUP BY t.name,t.category
        ORDER BY array_position(:revoked_names, t.name)
    """), {"revoked_names": revoked_names}).mappings())
    if {str(row["canonical_name"]) for row in revoked_rows} != set(
        revoked_names
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_revoked_localization_media_membership_gap"
        )
    pending_rows = list(session.execute(text("""
        SELECT t.name AS canonical_name,CAST(t.category AS text) AS category,
               MIN(m.hash) AS media_hash,COUNT(DISTINCT mt.media_id) AS media_count
        FROM blombooru_tags t
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        JOIN blombooru_media m ON m.id=mt.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        WHERE t.name=ANY(:pending_names)
        GROUP BY t.name,t.category
        ORDER BY t.name
    """), {"pending_names": sorted(pending) or ["__no_pending__"]}).mappings())
    if {str(row["canonical_name"]) for row in pending_rows} != set(pending):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_pending_localization_media_membership_gap"
        )
    total_manual_pending_count = len(pending_rows) + len(revoked_rows)
    remaining_slots = 8 - total_manual_pending_count
    proper_noun_target = min(2, remaining_slots)
    translation_target = remaining_slots - proper_noun_target
    rows = list(session.execute(text("""
        SELECT tr.canonical_name,tr.display_name,tr.source,tr.status,tr.category,
               MIN(m.hash) AS media_hash,COUNT(DISTINCT mt.media_id) AS media_count
        FROM blombooru_tag_translations tr
        JOIN blombooru_tags t ON t.name=tr.canonical_name
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        JOIN blombooru_media m ON m.id=mt.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        WHERE tr.language='zh-CN' AND tr.status='translated' AND COALESCE(tr.display_name,'')<>''
          AND tr.canonical_name=ANY(:new_names)
          AND NOT (tr.canonical_name=ANY(:revoked_names))
          AND tr.needs_review=false
          AND NOT EXISTS (
            SELECT 1 FROM blombooru_tag_translations other
            WHERE other.language=tr.language AND other.status<>'rejected'
              AND other.display_name=tr.display_name
              AND other.canonical_name<>tr.canonical_name
          )
        GROUP BY tr.canonical_name,tr.display_name,tr.source,tr.status,tr.category
        ORDER BY tr.canonical_name LIMIT :translation_target
    """), {
        "new_names": sorted(new_names) or ["__no_new_translation__"],
        "revoked_names": revoked_names,
        "translation_target": translation_target,
    }).mappings())
    exclusion_rows = list(session.execute(text("""
        SELECT t.name AS canonical_name,CAST(t.category AS text) AS category,
               MIN(m.hash) AS media_hash,COUNT(DISTINCT mt.media_id) AS media_count,
               COUNT(tr.id) FILTER (
                 WHERE tr.language='zh-CN' AND tr.status IN ('translated','reviewed')
               ) AS accepted_translation_count
        FROM blombooru_tags t
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        JOIN blombooru_media m ON m.id=mt.media_id
          AND (m.mime_type LIKE 'image/%' OR CAST(m.file_type AS text)='image')
        LEFT JOIN blombooru_tag_translations tr ON tr.canonical_name=t.name
        WHERE t.name=ANY(:excluded_names)
        GROUP BY t.name,t.category
        HAVING COUNT(tr.id) FILTER (
          WHERE tr.language='zh-CN' AND tr.status IN ('translated','reviewed')
        )=0
        ORDER BY t.name LIMIT :proper_noun_target
    """), {
        "excluded_names": sorted(exclusions) or ["__no_exclusion__"],
        "proper_noun_target": proper_noun_target,
    }).mappings())
    if (
        len(rows) != translation_target
        or len(exclusion_rows) != proper_noun_target
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_localization_delta_case_gap:"
            f"pending={len(pending_rows)}:translations={len(rows)}:"
            f"exclusions={len(exclusion_rows)}"
        )
    cases = []
    for index, row in enumerate(pending_rows, 1):
        canonical = str(row["canonical_name"])
        disposition = pending[canonical]
        actual_ids = ml1.runtime_and_terms(session, canonical)
        cases.append(_case(
            f"D{index:02d}",
            "ai_tag_localization",
            media_hash=str(row["media_hash"]),
            title=f"Manual localization review pending #{index}",
            expected_behavior=(
                "The canonical tag remains searchable and visible without a fake Chinese "
                "translation while the exact failed localization is presented for owner review."
            ),
            actual_result={
                "canonical_tag": canonical,
                "display_name": canonical,
                "translation_status": "manual_localization_review_pending",
                "validator_verdict": disposition.get("validator_verdict"),
                "failure_reason": disposition.get("failure_reason"),
                "model_output": disposition.get("model_output"),
                "call_attempt_history": disposition.get("call_attempt_history"),
                "proposed_manual_review_question": disposition.get(
                    "proposed_manual_review_question"
                ),
                "canonical_fallback_behavior": disposition.get(
                    "canonical_fallback_behavior"
                ),
                "independent_media_count": int(row.get("media_count") or 0),
                "runtime_result_count": len(actual_ids),
            },
            provenance={
                "phase_delta": "manual_localization_review_pending",
                "available_manual_pending_count": total_manual_pending_count,
                "derived_from_current_proofs": True,
                "policy_version": disposition.get("policy_version"),
                "tag_category": row.get("category"),
                "media_tag_source": "ai_wd",
            },
        ))
    next_index = len(cases) + 1
    for index, row in enumerate(rows, next_index):
        actual_ids = ml1.runtime_and_terms(session, str(row["display_name"]))
        cases.append(_case(
            f"D{index:02d}",
            "ai_tag_localization",
            media_hash=str(row["media_hash"]),
            title=f"AI-tag Chinese localization #{index}",
            expected_behavior="The accepted Chinese label preserves the original canonical tag and returns every independently supported media row.",
            actual_result={
                "canonical_tag": row.get("canonical_name"),
                "chinese_display_name": row.get("display_name"),
                "translation_status": row.get("status"),
                "independent_media_count": int(row.get("media_count") or 0),
                "runtime_result_count": len(actual_ids),
            },
            provenance={
                "phase_delta": "newly_generated_translation",
                "derived_from_current_proofs": True,
                "translation_source": row.get("source"),
                "tag_category": row.get("category"),
                "media_tag_source": "ai_wd",
            },
        ))
    next_index = len(cases) + 1
    for index, row in enumerate(revoked_rows, next_index):
        canonical = str(row["canonical_name"])
        disposition = effective_localization_disposition(canonical)
        canonical_ids = ml1.runtime_and_terms(session, canonical)
        rejected_alias_ids = ml1.runtime_and_terms(
            session, str(row.get("historical_display_name") or "")
        )
        cases.append(_case(
            f"D{index:02d}",
            "ai_tag_localization",
            media_hash=str(row["media_hash"]),
            title=f"Owner-revoked symbol localization #{index - next_index + 1}",
            expected_behavior=(
                "The opaque symbol tag uses its canonical display/search fallback; "
                "the owner-rejected Chinese label is not an accepted alias."
            ),
            actual_result={
                "canonical_tag": canonical,
                "display_name": canonical,
                "translation_status": disposition["translation_status"],
                "canonical_fallback_behavior": True,
                "accepted_chinese_alias_exposed": False,
                "historical_translation_retained_as_forensic_only": True,
                "historical_accepted_translation_count": int(
                    row.get("historical_accepted_translation_count") or 0
                ),
                "independent_media_count": int(row.get("media_count") or 0),
                "canonical_runtime_result_count": len(canonical_ids),
                "rejected_alias_runtime_result_count": len(rejected_alias_ids),
            },
            provenance={
                "phase_delta": "manual_localization_review_pending",
                "available_manual_pending_count": total_manual_pending_count,
                "derived_from_current_proofs": True,
                "policy_version": LOCALIZATION_REVOCATION_POLICY_VERSION,
                "reason_code": disposition["reason_code"],
                "tag_category": row.get("category"),
                "media_tag_source": "ai_wd",
            },
        ))
    next_index = len(cases) + 1
    for offset, row in enumerate(exclusion_rows, next_index):
        canonical = str(row["canonical_name"])
        policy = exclusions[canonical]
        actual_ids = ml1.runtime_and_terms(session, canonical)
        cases.append(_case(
            f"D{offset:02d}",
            "ai_tag_localization",
            media_hash=str(row["media_hash"]),
            title=f"Proper-noun exclusion/display #{offset - next_index + 1}",
            expected_behavior=(
                "An AI proper-noun tag remains canonical source text, has no accepted LLM translation, "
                "and stays searchable only as a visual/source descriptor rather than identity truth."
            ),
            actual_result={
                "canonical_tag": canonical,
                "display_name": canonical,
                "translation_status": "excluded_by_policy",
                "accepted_translation_count": int(row.get("accepted_translation_count") or 0),
                "independent_media_count": int(row.get("media_count") or 0),
                "runtime_result_count": len(actual_ids),
            },
            provenance={
                "phase_delta": "proper_noun_exclusion_display",
                "derived_from_current_proofs": True,
                "reason_code": policy.get("reason_code"),
                "policy_version": policy.get("policy_version"),
                "tag_category": row.get("category"),
                "media_tag_source": "ai_wd",
            },
        ))
    return cases


def _search_cases(session: Any, output: Path) -> list[dict[str, Any]]:
    rows = sv1b.read_json(output / "primary-search-workload-and-results-private.json")
    localization_manifest = sv1b.read_json(output / "localization/localization-manifest-private.json")
    new_names = {str(row["canonical_name"]) for row in localization_manifest.get("eligible_rows") or ()}
    translation_rows = list(session.execute(text("""
        SELECT tr.canonical_name,tr.display_name,COUNT(DISTINCT mt.media_id) AS media_count
        FROM blombooru_tag_translations tr
        JOIN blombooru_tags t ON t.name=tr.canonical_name
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        WHERE tr.language='zh-CN' AND tr.status IN ('translated','reviewed')
          AND tr.needs_review=false AND tr.canonical_name=ANY(:names)
          AND NOT (tr.canonical_name=ANY(:revoked_names))
        GROUP BY tr.canonical_name,tr.display_name
        ORDER BY tr.canonical_name
    """), {
        "names": sorted(new_names) or ["__no_new_translation__"],
        "revoked_names": list(MANUALLY_REVOKED_TRANSLATION_TAG_ORDER),
    }).mappings())
    delta_terms = {
        str(value).casefold()
        for row in translation_rows
        for value in (row["canonical_name"], row["display_name"])
        if value
    }
    desired = (
        ("creator_and_character", 2),
        ("creator_and_work_title", 1),
        ("provider_source_tag", 1),
        ("negative_query", 2),
    )
    preferred = [
        row
        for category, count in desired
        for row in [
            item for item in rows
            if item.get("category") == category
            and any(str(term).casefold() in delta_terms for term in item.get("terms") or ())
        ][:count]
    ]
    selected = preferred[:4]
    selected_term_keys = {tuple(str(term) for term in row.get("terms") or ()) for row in selected}
    for row in translation_rows:
        if len(selected) >= 4:
            break
        terms = (str(row["display_name"]),)
        if terms in selected_term_keys:
            continue
        selected.append({
            "category": "new_localization_search",
            "terms": list(terms),
            "expected_result_count": int(row.get("media_count") or 0),
            "and_leakage_count": 0,
            "supported_query_missing_result_count": 0,
            "case_ref": sv1b.sha256_payload({"new_localization_search": terms}),
            "phase_delta_source": "new_localization",
        })
        selected_term_keys.add(terms)
    if len(selected) != 4:
        raise ManualAcceptanceHarnessError(f"manual_acceptance_phase_delta_search_case_gap:{len(selected)}")
    revoked_search_rows = list(session.execute(text("""
        SELECT t.name AS canonical_name,COUNT(DISTINCT mt.media_id) AS media_count,
               MIN(tr.display_name) AS historical_display_name
        FROM blombooru_tags t
        JOIN blombooru_media_tags mt ON mt.tag_id=t.id AND mt.source='ai_wd'
        LEFT JOIN blombooru_tag_translations tr
          ON tr.canonical_name=t.name AND tr.language='zh-CN'
        WHERE t.name=ANY(:revoked_names)
        GROUP BY t.name
        ORDER BY array_position(:revoked_names, t.name)
    """), {
        "revoked_names": list(MANUALLY_REVOKED_TRANSLATION_TAG_ORDER),
    }).mappings())
    if [str(row["canonical_name"]) for row in revoked_search_rows] != list(
        MANUALLY_REVOKED_TRANSLATION_TAG_ORDER
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_revoked_search_membership_gap"
        )
    for row in revoked_search_rows:
        canonical = str(row["canonical_name"])
        rejected_alias = str(row.get("historical_display_name") or "")
        selected.append({
            "category": "manual_localization_canonical_fallback_search",
            "terms": [canonical],
            "expected_result_count": int(row.get("media_count") or 0),
            "and_leakage_count": 0,
            "supported_query_missing_result_count": 0,
            "rejected_display_alias": rejected_alias,
            "case_ref": sv1b.sha256_payload({
                "manual_localization_canonical_fallback_search": canonical,
            }),
            "phase_delta_source": "manual_localization_canonical_fallback",
        })
    fallback_hash = _fallback_media_hash(session)
    cases = []
    for index, row in enumerate(selected, 1):
        terms = [str(value) for value in row.get("terms") or ()]
        actual_ids = ml1.runtime_and_terms(session, *terms)
        rejected_alias_ids = (
            ml1.runtime_and_terms(
                session, str(row.get("rejected_display_alias") or "")
            )
            if row.get("rejected_display_alias")
            else set()
        )
        media_hash = fallback_hash
        if actual_ids:
            value = session.execute(text(
                "SELECT hash FROM blombooru_media WHERE id=ANY(:ids) "
                "AND (mime_type LIKE 'image/%' OR CAST(file_type AS text)='image') ORDER BY hash LIMIT 1"
            ), {"ids": sorted(actual_ids)}).scalar()
            if value:
                media_hash = str(value)
        cases.append(_case(
            f"E{index:02d}",
            "search_and_negative",
            media_hash=media_hash,
            title=f"Search/AND/negative #{index}",
            expected_behavior=(
                "The multi-term query equals the independent media-level intersection."
                if len(terms) > 1
                else "The negative query returns no unsupported media."
                if row.get("category") == "negative_query"
                else "The query returns the independently supported media set."
            ),
            actual_result={
                "query_terms": terms,
                "expected_result_count": int(row.get("expected_result_count") or 0),
                "actual_result_count": len(actual_ids),
                "and_leakage_count": int(row.get("and_leakage_count") or 0),
                "supported_query_missing_result_count": int(
                    row.get("supported_query_missing_result_count") or 0
                ),
                "canonical_fallback_search": (
                    row.get("category")
                    == "manual_localization_canonical_fallback_search"
                ),
                "rejected_chinese_alias_result_count": len(
                    rejected_alias_ids
                ),
            },
            provenance={
                "phase_delta": str(row.get("phase_delta_source") or "new_metadata_or_localization_workload"),
                "supported_by_phase_delta": True,
                "derived_from_current_proofs": True,
                "runtime_path": "parse_search_query + apply_endpoint_equivalent_text_search",
                "independent_expected_membership": True,
                "case_ref": row.get("case_ref"),
            },
        ))
    return cases


def _database_binding(database: str) -> dict[str, Any]:
    tables = (
        "blombooru_media",
        "blombooru_tags",
        "blombooru_media_tags",
        "blombooru_tag_translations",
        *sv1b.CORE_SOURCE_TABLES,
    )
    proof = sv1b.database_fingerprint(database, tables)
    return {
        "database_identity": database,
        "fingerprint": proof["fingerprint"],
        "media_count": proof["tables"]["blombooru_media"]["count"],
    }


def _proof_bindings(proofs: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    acquisition = proofs["acquisition-closure-and-package-proof.json"]
    localization = proofs["localization-closure-proof.json"]
    primary_graph = proofs["primary-source-graph-derivation-proof.json"]
    replay_graph = proofs["replay-source-graph-derivation-proof.json"]
    graph_comparison = proofs["primary-replay-source-graph-comparison-proof.json"]
    primary_search = proofs["primary-search-validation-proof.json"]
    replay_search = proofs["replay-search-validation-proof.json"]
    search_comparison = proofs["primary-replay-search-comparison-proof.json"]
    return {
        "acquired_metadata_package_fingerprint": str(
            (acquisition.get("package") or {}).get(
                "acquired_metadata_package_fingerprint"
            )
            or ""
        ),
        "graph_fingerprint": sv1b.sha256_payload({
            "primary": primary_graph,
            "replay": replay_graph,
            "comparison": graph_comparison,
        }),
        "search_fingerprint": sv1b.sha256_payload({
            "primary": primary_search,
            "replay": replay_search,
            "comparison": search_comparison,
        }),
        "localization_fingerprint": sv1b.sha256_payload(localization),
    }


REQUIRED_PROOF_SLOTS = (
    "acquisition-closure-and-package-proof.json",
    "localization-closure-proof.json",
    "primary-source-graph-derivation-proof.json",
    "replay-source-graph-derivation-proof.json",
    "primary-replay-source-graph-comparison-proof.json",
    "primary-search-validation-proof.json",
    "replay-search-validation-proof.json",
    "primary-replay-search-comparison-proof.json",
)


def _resolve_proof_sources(
    output: Path,
    proof_sources: Mapping[str, str | Path] | None,
) -> tuple[dict[str, str], dict[str, Mapping[str, Any]]]:
    overrides = dict(proof_sources or {})
    unknown = sorted(set(overrides) - set(REQUIRED_PROOF_SLOTS))
    if unknown:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_unknown_proof_slot:{unknown}"
        )
    relative_sources: dict[str, str] = {}
    proofs: dict[str, Mapping[str, Any]] = {}
    for slot in REQUIRED_PROOF_SLOTS:
        source = Path(overrides.get(slot, slot))
        if source.is_absolute():
            raise ManualAcceptanceHarnessError(
                f"manual_acceptance_absolute_proof_source_forbidden:{slot}"
            )
        path = (output / source).resolve()
        if output != path.parent and output not in path.parents:
            raise ManualAcceptanceHarnessError(
                f"manual_acceptance_proof_source_escape:{slot}"
            )
        relative_sources[slot] = path.relative_to(output).as_posix()
        proofs[slot] = sv1b.read_json(path)
    return relative_sources, proofs


def _write_json_exclusive_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_fresh_replay_v2 as fresh_runner,
    )

    fresh_runner.write_json_exclusive_atomic(path, dict(payload))


def _v5_protected_source_paths(root: Path | None = None) -> tuple[str, ...]:
    if root is not None:
        preparation_path = root / "manual-acceptance-v5-input-copy-proof.json"
        if preparation_path.is_file():
            preparation = sv1b.read_json(preparation_path)
            values = tuple(
                str(value)
                for value in preparation.get("protected_source_paths") or ()
            )
            if values:
                return tuple(
                    dict.fromkeys(
                        (*values, "manual-acceptance-v5-input-copy-proof.json")
                    )
                )
    return tuple(
        dict.fromkeys((*V5_PROTECTED_SOURCE_PATHS_BASE, *REQUIRED_PROOF_SLOTS))
    )


def _exact_file_sha_map(root: Path, relative_paths: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    resolved_root = root.resolve()
    for relative in relative_paths:
        path = (resolved_root / relative).resolve()
        if resolved_root not in path.parents or not path.is_file():
            raise ManualAcceptanceHarnessError(
                f"manual_acceptance_protected_source_missing:{relative}"
            )
        values[str(relative)] = sv1b.sha256_file(path)
    return dict(sorted(values.items()))


def prepare_v5_evidence_root(
    prior_output: Path,
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    """Copy only immutable accepted inputs into one new non-overwriting root."""

    prior_output = prior_output.resolve()
    output = sv1b.validate_output_root(output)
    sv1b.validate_owned_output_root(
        prior_output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    prior_harness = sv1b.read_json(
        prior_output / "manual-acceptance-harness-proof.json"
    )
    proof_source_map = {
        str(key): str(value)
        for key, value in (prior_harness.get("proof_sources") or {}).items()
    }
    protected_paths = tuple(
        dict.fromkeys(
            (*V5_PROTECTED_SOURCE_PATHS_BASE, *proof_source_map.values())
        )
    )
    source_shas = _exact_file_sha_map(prior_output, protected_paths)
    temporary = output.parent / f".{output.name}.preparing-{os.getpid()}"
    if temporary.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_v5_prepare_temporary_exists"
        )
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        for relative in source_shas:
            source = prior_output / relative
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        copied_shas = _exact_file_sha_map(temporary, source_shas)
        if copied_shas != source_shas:
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_v5_prepare_copy_drift"
            )
        os.replace(temporary, output)
    except Exception:
        if temporary.is_dir():
            shutil.rmtree(temporary)
        raise
    result = {
        "proof_version": "sv1b_manual_acceptance_v5_input_copy_v1",
        "prior_evidence_directory_name": prior_output.name,
        "proof_source_map": proof_source_map,
        "protected_source_paths": list(protected_paths),
        "protected_source_file_sha256": source_shas,
        "protected_source_membership_fingerprint": sv1b.sha256_payload(
            source_shas
        ),
        "passed": True,
    }
    _write_json_exclusive_atomic(
        output / "manual-acceptance-v5-input-copy-proof.json", result
    )
    return result


V4_HISTORICAL_PROTECTED_PATHS = (
    AUDIT_CLOSEOUT_VALIDATION_PROOF_V4_NAME,
    AUDIT_CLOSEOUT_FINAL_BINDING_V4_NAME,
    "manual-acceptance/case-manifest-private.json",
    "manual-acceptance/manual-acceptance-result.json",
)
EXPECTED_V4_OWNER_RESULT_SHA256 = (
    "6ad0d4d78815de0984a4e563490be91e985e9f109facb462c8528896867ae2b9"
)


def build_manual_acceptance_repair_v5_audit(
    output: Path,
    prior_output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    output = output.resolve()
    prior_output = prior_output.resolve()
    sv1b.validate_owned_output_root(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    sv1b.validate_owned_output_root(
        prior_output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    audit_path = output / MANUAL_ACCEPTANCE_REPAIR_VALIDATION_V5_NAME
    if audit_path.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_audit_already_exists"
        )
    cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    regenerated, composition = _generate_cases(
        output, primary_database=primary_database
    )
    protected_source_shas = _exact_file_sha_map(
        output, _v5_protected_source_paths(output)
    )
    historical_v4_shas = _exact_file_sha_map(
        prior_output, V4_HISTORICAL_PROTECTED_PATHS
    )
    by_id = {str(row["case_id"]): row for row in cases}
    d05 = by_id.get("D05") or {}
    d06 = by_id.get("D06") or {}
    e05 = by_id.get("E05") or {}
    e06 = by_id.get("E06") or {}
    checks = {
        "case_manifest_regenerated_equal": _case_manifests_equal(
            regenerated, cases
        ),
        "case_membership_exact": (
            len(cases) == 40
            and len({str(row.get("case_id") or "") for row in cases}) == 40
        ),
        "metadata_media_source_binding_passed": (
            (composition.get("metadata_media_source_binding") or {}).get(
                "passed"
            )
            is True
        ),
        "creator_policy_passed": all(
            (row.get("provenance") or {}).get(
                "creator_identity_policy_version"
            )
            == CREATOR_IDENTITY_POLICY_VERSION
            for row in cases
            if row.get("category") == "creator_clustering"
        ),
        "d05_d06_canonical_fallback": all(
            (row.get("actual_result") or {}).get(
                "translation_status"
            )
            == "manual_localization_review_pending"
            and (row.get("actual_result") or {}).get(
                "canonical_fallback_behavior"
            )
            is True
            and (row.get("actual_result") or {}).get(
                "accepted_chinese_alias_exposed"
            )
            is False
            for row in (d05, d06)
        ),
        "e05_e06_canonical_search_only": all(
            (row.get("actual_result") or {}).get(
                "canonical_fallback_search"
            )
            is True
            and int(
                (row.get("actual_result") or {}).get(
                    "rejected_chinese_alias_result_count"
                )
                or 0
            )
            == 0
            for row in (e05, e06)
        ),
        "old_v4_result_exact": (
            historical_v4_shas[
                "manual-acceptance/manual-acceptance-result.json"
            ]
            == EXPECTED_V4_OWNER_RESULT_SHA256
        ),
        "no_external_route_budget": all(
            int(
                (
                    sv1b.read_json(
                        output
                        / "acquired-nonderived-evidence-package-private.json"
                    ).get("external_route_budget")
                    or {}
                ).get(key)
                or 0
            )
            == 0
            for key in (
                "gallery_dl_requests",
                "llm_calls",
                "media_downloads",
                "provider_requests",
                "thumbnail_downloads",
            )
        ),
    }
    body = {
        "proof_version": "sv1b_manual_acceptance_repair_v5_validation_v1",
        "git_head": _git_head(),
        "prior_evidence_directory_name": prior_output.name,
        "acceptance_case_manifest_fingerprint": sv1b.sha256_payload(cases),
        "acceptance_case_manifest_file_sha256": sv1b.sha256_file(
            output / "manual-acceptance/case-manifest-private.json"
        ),
        "phase_delta_composition": composition,
        "protected_source_file_sha256": protected_source_shas,
        "protected_source_membership_fingerprint": sv1b.sha256_payload(
            protected_source_shas
        ),
        "historical_v4_file_sha256": historical_v4_shas,
        "historical_v4_membership_fingerprint": sv1b.sha256_payload(
            historical_v4_shas
        ),
        "primary_database": _database_binding(primary_database),
        "replay_database": _database_binding(replay_database),
        "creator_identity_policy_version": CREATOR_IDENTITY_POLICY_VERSION,
        "localization_revocation_policy_version": (
            LOCALIZATION_REVOCATION_POLICY_VERSION
        ),
        "checks": checks,
        "passed": all(checks.values()),
    }
    if body["passed"] is not True:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_audit_failed"
        )
    body["proof_fingerprint"] = sv1b.sha256_payload(body)
    _write_json_exclusive_atomic(audit_path, body)
    return body


def _validate_manual_acceptance_repair_v5_audit(
    output: Path,
    prior_output: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    path = output / MANUAL_ACCEPTANCE_REPAIR_VALIDATION_V5_NAME
    if not path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_audit_missing"
        )
    audit = sv1b.read_json(path)
    declared = str(audit.get("proof_fingerprint") or "")
    calculated = sv1b.sha256_payload(
        {key: value for key, value in audit.items() if key != "proof_fingerprint"}
    )
    current_source_shas = _exact_file_sha_map(
        output, _v5_protected_source_paths(output)
    )
    current_v4_shas = _exact_file_sha_map(
        prior_output, V4_HISTORICAL_PROTECTED_PATHS
    )
    if (
        audit.get("passed") is not True
        or not all((audit.get("checks") or {}).values())
        or audit.get("git_head") != _git_head()
        or declared != calculated
        or audit.get("protected_source_file_sha256") != current_source_shas
        or audit.get("historical_v4_file_sha256") != current_v4_shas
        or audit.get("acceptance_case_manifest_file_sha256")
        != sv1b.sha256_file(
            output / "manual-acceptance/case-manifest-private.json"
        )
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_audit_invalid"
        )
    return audit, {
        "proof_fingerprint": declared,
        "proof_file_sha256": sv1b.sha256_file(path),
        "git_head": str(audit["git_head"]),
    }


def validate_phase_delta_case_composition(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = [dict(row) for row in cases]
    by_category: dict[str, list[dict[str, Any]]] = {
        category: [row for row in values if row.get("category") == category]
        for category in CATEGORY_COUNTS
    }
    if {category: len(rows) for category, rows in by_category.items()} != CATEGORY_COUNTS:
        raise ManualAcceptanceHarnessError("manual_acceptance_phase_delta_category_membership_invalid")
    if any(
        (row.get("provenance") or {}).get("phase_delta") != "newly_acquired_exact_metadata"
        for row in by_category["pixiv_metadata"]
    ):
        raise ManualAcceptanceHarnessError("manual_acceptance_metadata_not_phase_delta")

    creator = by_category["creator_clustering"]
    creator_delta_count = sum(
        (row.get("provenance") or {}).get("phase_delta") == "new_or_materially_changed_creator_component"
        for row in creator
    )
    creator_available = max(
        (int((row.get("provenance") or {}).get("available_changed_component_count") or 0) for row in creator),
        default=0,
    )
    if creator_delta_count != min(8, creator_available):
        raise ManualAcceptanceHarnessError("manual_acceptance_creator_delta_priority_invalid")
    for row in creator:
        actual = row.get("actual_result") or {}
        aliases = actual.get("identity_aliases") or ()
        if (
            actual.get("media_count_used_as_identity_evidence") is not False
            or actual.get("string_similarity_used_as_identity_evidence")
            is not False
            or any(is_placeholder_creator_name(value) for value in aliases)
            or (
                actual.get("identity_union_created") is True
                and (
                    actual.get("identity_union_basis")
                    != "same_provider_stable_creator_id"
                    or actual.get("stable_identity_anchor_present") is not True
                )
            )
            or (
                actual.get("identity_union_created") is not True
                and actual.get("conservative_independence_preserved")
                is not True
            )
        ):
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_creator_identity_policy_invalid"
            )

    shared = by_category["shared_name_cannot_link"]
    shared_delta_count = sum(
        (row.get("provenance") or {}).get("phase_delta") == "newly_acquired_alias_or_graph_edge"
        for row in shared
    )
    shared_available = max(
        (int((row.get("provenance") or {}).get("available_phase_delta_case_count") or 0) for row in shared),
        default=0,
    )
    if shared_delta_count != min(6, shared_available):
        raise ManualAcceptanceHarnessError("manual_acceptance_shared_name_delta_priority_invalid")

    localization = by_category["ai_tag_localization"]
    new_translation_count = sum(
        (row.get("provenance") or {}).get("phase_delta") == "newly_generated_translation"
        for row in localization
    )
    proper_noun_exclusion_count = sum(
        (row.get("provenance") or {}).get("phase_delta") == "proper_noun_exclusion_display"
        for row in localization
    )
    manual_pending_count = sum(
        (row.get("provenance") or {}).get("phase_delta")
        == "manual_localization_review_pending"
        for row in localization
    )
    manual_pending_available = max(
        (
            int(
                (row.get("provenance") or {}).get(
                    "available_manual_pending_count"
                )
                or 0
            )
            for row in localization
        ),
        default=0,
    )
    expected_proper_noun_count = min(2, 8 - manual_pending_count)
    if (
        manual_pending_count != manual_pending_available
        or new_translation_count
        != 8 - manual_pending_count - expected_proper_noun_count
        or proper_noun_exclusion_count != expected_proper_noun_count
    ):
        raise ManualAcceptanceHarnessError("manual_acceptance_localization_delta_composition_invalid")

    search = by_category["search_and_negative"]
    if any((row.get("provenance") or {}).get("supported_by_phase_delta") is not True for row in search):
        raise ManualAcceptanceHarnessError("manual_acceptance_search_not_phase_delta_supported")
    if any((row.get("provenance") or {}).get("derived_from_current_proofs") is not True for row in values):
        raise ManualAcceptanceHarnessError("manual_acceptance_case_proof_derivation_missing")
    if any(row.get("actual_result", {}).get("lifecycle_correct") is not True for row in creator):
        raise ManualAcceptanceHarnessError("manual_acceptance_creator_lifecycle_invalid")
    if any(
        row.get("actual_result", {}).get("identity_union_created") is not False
        or row.get("actual_result", {}).get("cannot_link_safety_passed") is not True
        or row.get("actual_result", {}).get("lifecycle_correct") is not True
        for row in shared
    ):
        raise ManualAcceptanceHarnessError("manual_acceptance_shared_name_safety_invalid")
    return {
        "metadata_new_exact_count": 12,
        "creator_changed_or_new_count": creator_delta_count,
        "creator_preservation_filler_count": 8 - creator_delta_count,
        "shared_name_new_alias_or_edge_count": shared_delta_count,
        "shared_name_baseline_filler_count": 6 - shared_delta_count,
        "new_translation_case_count": new_translation_count,
        "proper_noun_exclusion_display_case_count": proper_noun_exclusion_count,
        "manual_localization_review_pending_case_count": manual_pending_count,
        "phase_delta_supported_search_case_count": 6,
        "derived_from_current_proofs": True,
    }


def _generate_cases(
    output: Path,
    *,
    primary_database: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    engine = sv1b.engine_for(primary_database)
    session = sessionmaker(bind=engine)()
    try:
        cases = [
            *_pixiv_metadata_cases(session, output),
            *_creator_clustering_cases(
                session, output, primary_database
            ),
            *_shared_name_cases(session, output),
            *_localization_cases(session, output),
            *_search_cases(session, output),
        ]
        media_binding_proof = validate_pixiv_case_media_source_bindings(
            cases, session, output
        )
        session.rollback()
    finally:
        session.close()
        engine.dispose()
    counts = Counter(str(row["category"]) for row in cases)
    if len(cases) != 40 or dict(counts) != CATEGORY_COUNTS:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_composition_invalid:"
            f"count={len(cases)}:categories={dict(counts)}"
        )
    case_ids = [str(row["case_id"]) for row in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_id_duplicate"
        )
    composition = validate_phase_delta_case_composition(cases)
    composition["metadata_media_source_binding"] = media_binding_proof
    return cases, composition


def _build_bindings(
    *,
    primary_database: str,
    replay_database: str,
    relative_proof_sources: Mapping[str, str],
    proofs: Mapping[str, Mapping[str, Any]],
    cases: list[dict[str, Any]],
    audit_binding: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    primary_binding = _database_binding(primary_database)
    replay_binding = _database_binding(replay_database)
    if (
        primary_binding["media_count"] != sv1b.EXPECTED_MEDIA_COUNT
        or replay_binding["media_count"] != sv1b.EXPECTED_MEDIA_COUNT
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_database_membership_invalid"
        )
    bindings = {
        "git_head": _git_head(),
        "primary_database": primary_binding,
        "replay_database": replay_binding,
        "media_manifest_fingerprint": (
            sv1b.ACCEPTED_MANIFEST_FINGERPRINT
        ),
        **_proof_bindings(proofs),
        "proof_source_map_fingerprint": sv1b.sha256_payload(
            relative_proof_sources
        ),
        "acceptance_case_manifest_fingerprint": (
            sv1b.sha256_payload(cases)
        ),
        "creator_identity_policy_version": CREATOR_IDENTITY_POLICY_VERSION,
        "localization_revocation_policy_version": (
            LOCALIZATION_REVOCATION_POLICY_VERSION
        ),
        "revoked_localization_membership_fingerprint": sv1b.sha256_payload(
            list(MANUALLY_REVOKED_TRANSLATION_TAG_ORDER)
        ),
    }
    if audit_binding is not None:
        bindings["audit_validation"] = dict(audit_binding)
    bindings["binding_fingerprint"] = sv1b.sha256_payload(bindings)
    return bindings


def _validated_audit_v3_binding(output: Path) -> dict[str, str]:
    audit_path = output / AUDIT_CLOSEOUT_VALIDATION_PROOF_V3_NAME
    if not audit_path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_validation_v3_missing"
        )
    audit = sv1b.read_json(audit_path)
    declared = str(audit.get("proof_fingerprint") or "")
    calculated = sv1b.sha256_payload(
        {
            key: value
            for key, value in audit.items()
            if key != "proof_fingerprint"
        }
    )
    checks = {
        "proof_version": (
            audit.get("proof_version")
            == "sv1b_audit_closeout_read_only_validation_v3"
        ),
        "passed": audit.get("passed") is True,
        "self_fingerprint": declared == calculated,
        "git_head": audit.get("git_head") == _git_head(),
        "stable_reference": (
            audit.get("stable_reference_integrity", {}).get("passed") is True
        ),
        "phase_acquired_support": (
            audit.get("primary_identity_crosscheck", {}).get("passed") is True
            and audit.get("primary_identity_crosscheck", {}).get(
                "phase_acquired_identity_unsupported_count"
            )
            == 0
        ),
        "round_trip": audit.get("round_trip", {}).get("passed") is True,
    }
    if not all(checks.values()):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_validation_v3_invalid:"
            f"{sorted(key for key, value in checks.items() if not value)}"
        )
    return {
        "proof_path": AUDIT_CLOSEOUT_VALIDATION_PROOF_V3_NAME,
        "proof_fingerprint": declared,
        "proof_file_sha256": sv1b.sha256_file(audit_path),
        "git_head": str(audit["git_head"]),
    }


def _validated_audit_v4_binding(
    output: Path,
    *,
    expected_file_sha256: str | None = None,
) -> dict[str, str]:
    """Use the runner's canonical full-evidence v4 validation path."""

    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_fresh_replay_v2 as fresh_runner,
    )

    try:
        return fresh_runner.validate_audit_closeout_v4(
            output,
            expected_file_sha256=expected_file_sha256,
        )
    except fresh_runner.FreshReplayV2Error as exc:
        raise ManualAcceptanceHarnessError(str(exc)) from exc


def _case_manifests_equal(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> bool:
    return sv1b.sha256_payload(left) == sv1b.sha256_payload(right)


def build_harness(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
    proof_sources: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    output = output.resolve()
    sv1b.validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    if proof_sources is None:
        preparation_path = output / "manual-acceptance-v5-input-copy-proof.json"
        if preparation_path.is_file():
            proof_sources = sv1b.read_json(preparation_path).get(
                "proof_source_map"
            )
    relative_proof_sources, proofs = _resolve_proof_sources(
        output, proof_sources
    )
    failed = [name for name, proof in proofs.items() if proof.get("passed") is not True]
    if (
        proofs["localization-closure-proof.json"].get(
            "localization_accounting_closed"
        )
        is not True
        or proofs["localization-closure-proof.json"].get(
            "downstream_progression_allowed"
        )
        is not True
    ):
        failed.append("localization-closure-proof.json")
    if failed:
        raise ManualAcceptanceHarnessError(f"manual_acceptance_required_proof_failed:{sorted(failed)}")

    primary_binding = _database_binding(primary_database)
    replay_binding = _database_binding(replay_database)
    if (
        primary_binding["media_count"] != sv1b.EXPECTED_MEDIA_COUNT
        or replay_binding["media_count"] != sv1b.EXPECTED_MEDIA_COUNT
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_database_membership_invalid"
        )
    cases, phase_delta_composition = _generate_cases(
        output, primary_database=primary_database
    )
    counts = Counter(str(row["category"]) for row in cases)

    manual_root = output / "manual-acceptance"
    manual_root.mkdir(parents=True, exist_ok=False)
    case_manifest_path = manual_root / "case-manifest-private.json"
    sv1b.write_json(case_manifest_path, cases)
    case_fingerprint = sv1b.sha256_payload(cases)
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=cases,
    )
    proof = {
        "harness_version": HARNESS_VERSION,
        "required": True,
        "status": "pending_user",
        "case_count": len(cases),
        "category_case_counts": dict(counts),
        "phase_delta_composition": phase_delta_composition,
        "creator_identity_policy": {
            "policy_version": CREATOR_IDENTITY_POLICY_VERSION,
            "media_count_identity_truth_allowed": False,
            "string_similarity_identity_truth_allowed": False,
            "placeholder_identity_alias_allowed": False,
        },
        "localization_revocation_policy": {
            "policy_version": LOCALIZATION_REVOCATION_POLICY_VERSION,
            "revoked_canonical_membership_fingerprint": sv1b.sha256_payload(
                list(MANUALLY_REVOKED_TRANSLATION_TAG_ORDER)
            ),
            "revoked_count": len(MANUALLY_REVOKED_TRANSLATION_TAG_ORDER),
            "canonical_fallback_required": True,
        },
        "actual_backend_services_used": True,
        "accepted_storage_read_only": True,
        "result_private_and_uncommitted": True,
        "absolute_paths_exposed": False,
        "provider_urls_exposed": False,
        "localhost_url": f"http://127.0.0.1:{int(port)}",
        "acceptance_case_manifest_fingerprint": case_fingerprint,
        "proof_sources": relative_proof_sources,
        "bindings": bindings,
        "result_path_relative": "manual-acceptance/manual-acceptance-result.json",
        "manual_acceptance_required": True,
        "manual_acceptance_status": "pending_user",
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "passed": True,
    }
    sv1b.write_json(output / "manual-acceptance-harness-proof.json", proof)
    return proof


def finalize_manual_acceptance_repair_v5_binding(
    output: Path,
    prior_output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    output = output.resolve()
    prior_output = prior_output.resolve()
    sv1b.validate_owned_output_root(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    final_path = output / MANUAL_ACCEPTANCE_FINAL_BINDING_V5_NAME
    if final_path.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_binding_already_exists"
        )
    source = sv1b.read_json(output / "manual-acceptance-harness-proof.json")
    cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    regenerated, composition = _generate_cases(
        output, primary_database=primary_database
    )
    if not _case_manifests_equal(regenerated, cases):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_case_regeneration_drift"
        )
    audit, audit_binding = _validate_manual_acceptance_repair_v5_audit(
        output, prior_output
    )
    relative_sources, proofs = _resolve_proof_sources(
        output, source.get("proof_sources")
    )
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_sources,
        proofs=proofs,
        cases=cases,
        audit_binding=audit_binding,
    )
    bindings.update(
        {
            "protected_source_file_sha256": audit[
                "protected_source_file_sha256"
            ],
            "historical_v4_file_sha256": audit[
                "historical_v4_file_sha256"
            ],
            "acceptance_case_manifest_file_sha256": audit[
                "acceptance_case_manifest_file_sha256"
            ],
        }
    )
    bindings["binding_fingerprint"] = sv1b.sha256_payload(
        {
            key: value
            for key, value in bindings.items()
            if key != "binding_fingerprint"
        }
    )
    checks = {
        "source_harness_passed": source.get("passed") is True,
        "audit_passed": audit.get("passed") is True,
        "exact_git_head_bound": bindings["git_head"] == _git_head(),
        "case_manifest_regenerated_equal": True,
        "metadata_media_source_binding_passed": (
            (composition.get("metadata_media_source_binding") or {}).get(
                "passed"
            )
            is True
        ),
        "old_v4_result_preserved": (
            bindings["historical_v4_file_sha256"][
                "manual-acceptance/manual-acceptance-result.json"
            ]
            == EXPECTED_V4_OWNER_RESULT_SHA256
        ),
    }
    final = {
        **source,
        "proof_version": "sv1b_manual_acceptance_repair_v5_binding_v1",
        "binding_version": "sv1b_manual_acceptance_final_binding_v5",
        "prior_evidence_directory_name": prior_output.name,
        "supersedes_v4_binding_fingerprint": sv1b.read_json(
            prior_output / AUDIT_CLOSEOUT_FINAL_BINDING_V4_NAME
        )["bindings"]["binding_fingerprint"],
        "audit_validation": audit_binding,
        "phase_delta_composition": composition,
        "bindings": bindings,
        "localhost_url": f"http://127.0.0.1:{int(port)}",
        "checks": checks,
        "passed": all(checks.values()),
    }
    if final["passed"] is not True:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_binding_failed"
        )
    _write_json_exclusive_atomic(final_path, final)
    return final


def _validated_manual_acceptance_repair_v5_binding(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    final_path = output / MANUAL_ACCEPTANCE_FINAL_BINDING_V5_NAME
    if not final_path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_binding_missing"
        )
    final = sv1b.read_json(final_path)
    prior_name = str(final.get("prior_evidence_directory_name") or "")
    prior_output = (output.parent / prior_name).resolve()
    if output.parent.resolve() != prior_output.parent or not prior_output.is_dir():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_prior_root_invalid"
        )
    _audit, audit_binding = _validate_manual_acceptance_repair_v5_audit(
        output, prior_output
    )
    cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    regenerated, composition = _generate_cases(
        output, primary_database=primary_database
    )
    if not _case_manifests_equal(regenerated, cases):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_case_regeneration_drift"
        )
    relative_sources, proofs = _resolve_proof_sources(
        output, final.get("proof_sources")
    )
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_sources,
        proofs=proofs,
        cases=cases,
        audit_binding=audit_binding,
    )
    bindings.update(
        {
            "protected_source_file_sha256": _exact_file_sha_map(
                output, _v5_protected_source_paths(output)
            ),
            "historical_v4_file_sha256": _exact_file_sha_map(
                prior_output, V4_HISTORICAL_PROTECTED_PATHS
            ),
            "acceptance_case_manifest_file_sha256": sv1b.sha256_file(
                output / "manual-acceptance/case-manifest-private.json"
            ),
        }
    )
    bindings["binding_fingerprint"] = sv1b.sha256_payload(
        {
            key: value
            for key, value in bindings.items()
            if key != "binding_fingerprint"
        }
    )
    if (
        final.get("passed") is not True
        or not all((final.get("checks") or {}).values())
        or final.get("bindings") != bindings
        or (composition.get("metadata_media_source_binding") or {}).get(
            "passed"
        )
        is not True
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_repair_v5_binding_invalid"
        )
    return bindings


def finalize_harness_binding(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    output = output.resolve()
    sv1b.validate_owned_output_root(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    final_path = output / FINAL_HARNESS_PROOF_NAME
    if final_path.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_final_binding_already_exists"
        )
    source = sv1b.read_json(
        output / "manual-acceptance-harness-proof.json"
    )
    if source.get("passed") is not True:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_source_harness_failed"
        )
    existing_cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    regenerated_cases, phase_delta_composition = _generate_cases(
        output, primary_database=primary_database
    )
    if not _case_manifests_equal(
        regenerated_cases, existing_cases
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_regeneration_drift"
        )
    relative_proof_sources, proofs = _resolve_proof_sources(
        output, source.get("proof_sources")
    )
    failed = [
        name
        for name, proof in proofs.items()
        if proof.get("passed") is not True
    ]
    if failed:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_required_proof_failed:{sorted(failed)}"
        )
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=regenerated_cases,
    )
    final = dict(source)
    final.update(
        {
            "binding_version": (
                "sv1b_manual_acceptance_final_binding_v1"
            ),
            "supersedes_harness_proof_fingerprint": (
                sv1b.sha256_payload(source)
            ),
            "case_manifest_regenerated_equal": True,
            "phase_delta_composition": phase_delta_composition,
            "proof_sources": relative_proof_sources,
            "bindings": bindings,
            "localhost_url": f"http://127.0.0.1:{int(port)}",
            "passed": True,
        }
    )
    sv1b.write_json(final_path, final)
    return final


def finalize_audit_closeout_binding_v2(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    """Create the one non-overwriting audit-closeout binding after public fixes."""

    output = output.resolve()
    sv1b.validate_owned_output_root(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    old_path = output / FINAL_HARNESS_PROOF_NAME
    final_path = output / AUDIT_CLOSEOUT_FINAL_BINDING_V2_NAME
    audit_path = output / AUDIT_CLOSEOUT_VALIDATION_PROOF_NAME
    if final_path.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v2_already_exists"
        )
    if not old_path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_final_binding_v1_missing"
        )
    if not audit_path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_validation_missing"
        )
    old = sv1b.read_json(old_path)
    audit = sv1b.read_json(audit_path)
    if (
        old.get("passed") is not True
        or old.get("binding_version")
        != "sv1b_manual_acceptance_final_binding_v1"
        or audit.get("passed") is not True
        or audit.get("proof_version")
        != "sv1b_audit_closeout_read_only_validation_v2"
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_source_invalid"
        )
    existing_cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    case_fingerprint = sv1b.sha256_payload(existing_cases)
    if case_fingerprint != EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_case_manifest_mismatch"
        )
    regenerated_cases, phase_delta_composition = _generate_cases(
        output,
        primary_database=primary_database,
    )
    if not _case_manifests_equal(regenerated_cases, existing_cases):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_regeneration_drift"
        )
    relative_proof_sources, proofs = _resolve_proof_sources(
        output,
        old.get("proof_sources"),
    )
    failed = [
        name
        for name, proof in proofs.items()
        if proof.get("passed") is not True
    ]
    if failed:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_required_proof_failed:{sorted(failed)}"
        )
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=regenerated_cases,
    )
    immutable_old = dict(old["bindings"])
    immutable_new = dict(bindings)
    for value in (immutable_old, immutable_new):
        value.pop("git_head", None)
        value.pop("binding_fingerprint", None)
    protected_bindings_unchanged = immutable_old == immutable_new
    audit_fingerprint = str(audit.get("proof_fingerprint") or "")
    validation_bound = bool(
        len(audit_fingerprint) == 64
        and audit.get("protected_evidence_unchanged") is True
        and audit.get("stable_reference_integrity", {}).get("passed") is True
        and audit.get("round_trip", {}).get("passed") is True
    )
    checks = {
        "old_binding_v1_passed": old.get("passed") is True,
        "case_manifest_exact": (
            case_fingerprint == EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT
        ),
        "case_manifest_regenerated_equal": True,
        "protected_bindings_unchanged": protected_bindings_unchanged,
        "audit_validation_bound": validation_bound,
        "new_git_head_bound": bindings.get("git_head") == _git_head(),
    }
    final = dict(old)
    final.update(
        {
            "proof_version": "sv1b_manual_acceptance_audit_closeout_binding_v2",
            "binding_version": (
                "sv1b_manual_acceptance_audit_closeout_final_binding_v2"
            ),
            "supersedes_final_binding_fingerprint": old["bindings"][
                "binding_fingerprint"
            ],
            "supersedes_final_binding_file_sha256": sv1b.sha256_file(
                old_path
            ),
            "audit_closeout_validation": {
                "proof_path": AUDIT_CLOSEOUT_VALIDATION_PROOF_NAME,
                "proof_fingerprint": audit_fingerprint,
                "proof_file_sha256": sv1b.sha256_file(audit_path),
            },
            "case_manifest_regenerated_equal": True,
            "phase_delta_composition": phase_delta_composition,
            "proof_sources": relative_proof_sources,
            "bindings": bindings,
            "localhost_url": f"http://127.0.0.1:{int(port)}",
            "checks": checks,
            "passed": all(checks.values()),
        }
    )
    if final["passed"] is not True:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v2_failed"
        )
    sv1b.write_json(final_path, final)
    return final


def finalize_audit_closeout_binding_v3(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    """Bind the exact audit proof bytes and current HEAD without overwriting v2."""

    output = output.resolve()
    sv1b.validate_owned_output_root(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    v2_path = output / AUDIT_CLOSEOUT_FINAL_BINDING_V2_NAME
    final_path = output / AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME
    if final_path.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v3_already_exists"
        )
    if not v2_path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v2_missing"
        )
    v2 = sv1b.read_json(v2_path)
    audit_binding = _validated_audit_v3_binding(output)
    existing_cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    case_fingerprint = sv1b.sha256_payload(existing_cases)
    if case_fingerprint != EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_case_manifest_mismatch"
        )
    regenerated_cases, phase_delta_composition = _generate_cases(
        output, primary_database=primary_database
    )
    if not _case_manifests_equal(regenerated_cases, existing_cases):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_regeneration_drift"
        )
    relative_sources, proofs = _resolve_proof_sources(
        output, v2.get("proof_sources")
    )
    current_without_audit = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_sources,
        proofs=proofs,
        cases=regenerated_cases,
    )
    immutable_v2 = dict(v2["bindings"])
    immutable_current = dict(current_without_audit)
    for value in (immutable_v2, immutable_current):
        value.pop("git_head", None)
        value.pop("binding_fingerprint", None)
        value.pop("audit_validation", None)
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_sources,
        proofs=proofs,
        cases=regenerated_cases,
        audit_binding=audit_binding,
    )
    checks = {
        "binding_v2_passed": v2.get("passed") is True,
        "case_manifest_exact": (
            case_fingerprint == EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT
        ),
        "case_manifest_regenerated_equal": True,
        "protected_bindings_unchanged": immutable_v2 == immutable_current,
        "audit_validation_bound": (
            bindings["audit_validation"] == audit_binding
        ),
        "new_git_head_bound": bindings["git_head"] == _git_head(),
    }
    final = dict(v2)
    final.update(
        {
            "proof_version": (
                "sv1b_manual_acceptance_audit_closeout_binding_v3"
            ),
            "binding_version": (
                "sv1b_manual_acceptance_audit_closeout_final_binding_v3"
            ),
            "supersedes_final_binding_fingerprint": v2["bindings"][
                "binding_fingerprint"
            ],
            "supersedes_final_binding_file_sha256": sv1b.sha256_file(
                v2_path
            ),
            "audit_closeout_validation": audit_binding,
            "phase_delta_composition": phase_delta_composition,
            "proof_sources": relative_sources,
            "bindings": bindings,
            "localhost_url": f"http://127.0.0.1:{int(port)}",
            "checks": checks,
            "passed": all(checks.values()),
        }
    )
    if final["passed"] is not True:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v3_failed"
        )
    sv1b.write_json(final_path, final)
    return final


def finalize_audit_closeout_binding_v4(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    port: int = DEFAULT_PORT,
    audit_binding: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Bind canonical audit bytes and all evidence once without replacing v3."""

    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_fresh_replay_v2 as fresh_runner,
    )

    output = output.resolve()
    sv1b.validate_owned_output_root(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    v3_path = output / AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME
    final_path = output / AUDIT_CLOSEOUT_FINAL_BINDING_V4_NAME
    if final_path.exists():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v4_already_exists"
        )
    if not v3_path.is_file():
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v3_missing"
        )
    v3 = sv1b.read_json(v3_path)
    canonical_audit = _validated_audit_v4_binding(output)
    if audit_binding is not None and dict(audit_binding) != canonical_audit:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_v4_recovery_drift"
        )
    existing_cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    case_fingerprint = sv1b.sha256_payload(existing_cases)
    if case_fingerprint != EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_case_manifest_mismatch"
        )
    regenerated_cases, phase_delta_composition = _generate_cases(
        output,
        primary_database=primary_database,
    )
    if not _case_manifests_equal(regenerated_cases, existing_cases):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_regeneration_drift"
        )
    relative_sources, proofs = _resolve_proof_sources(
        output,
        v3.get("proof_sources"),
    )
    current_without_audit = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_sources,
        proofs=proofs,
        cases=regenerated_cases,
    )
    immutable_v3 = dict(v3["bindings"])
    immutable_current = dict(current_without_audit)
    for value in (immutable_v3, immutable_current):
        value.pop("git_head", None)
        value.pop("binding_fingerprint", None)
        value.pop("audit_validation", None)
    bindings = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_sources,
        proofs=proofs,
        cases=regenerated_cases,
        audit_binding=canonical_audit,
    )
    checks = {
        "binding_v3_passed": v3.get("passed") is True,
        "binding_v3_file_unchanged": (
            sv1b.sha256_file(v3_path)
            == fresh_runner.EXPECTED_FINAL_BINDING_V3_FILE_SHA256
        ),
        "case_manifest_exact": (
            case_fingerprint == EXPECTED_AUDIT_CASE_MANIFEST_FINGERPRINT
        ),
        "case_manifest_regenerated_equal": True,
        "protected_bindings_unchanged": (
            immutable_v3 == immutable_current
        ),
        "canonical_audit_validation_bound": (
            bindings["audit_validation"] == canonical_audit
        ),
        "new_git_head_bound": bindings["git_head"] == _git_head(),
    }
    final = dict(v3)
    final.update(
        {
            "proof_version": (
                "sv1b_manual_acceptance_audit_closeout_binding_v4"
            ),
            "binding_version": (
                "sv1b_manual_acceptance_audit_closeout_final_binding_v4"
            ),
            "supersedes_final_binding_fingerprint": v3["bindings"][
                "binding_fingerprint"
            ],
            "supersedes_final_binding_file_sha256": sv1b.sha256_file(
                v3_path
            ),
            "audit_closeout_validation": canonical_audit,
            "phase_delta_composition": phase_delta_composition,
            "proof_sources": relative_sources,
            "bindings": bindings,
            "localhost_url": f"http://127.0.0.1:{int(port)}",
            "checks": checks,
            "passed": all(checks.values()),
        }
    )
    if final["passed"] is not True:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_audit_closeout_binding_v4_failed"
        )
    fresh_runner.write_json_exclusive_atomic(final_path, final)
    return final


def _active_harness_proof_path(output: Path) -> Path:
    repair_v5 = output / MANUAL_ACCEPTANCE_FINAL_BINDING_V5_NAME
    if repair_v5.is_file():
        return repair_v5
    audit_v4 = output / AUDIT_CLOSEOUT_FINAL_BINDING_V4_NAME
    if audit_v4.is_file():
        return audit_v4
    audit_v3 = output / AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME
    if audit_v3.is_file():
        return audit_v3
    audit_v2 = output / AUDIT_CLOSEOUT_FINAL_BINDING_V2_NAME
    if audit_v2.is_file():
        return audit_v2
    final = output / FINAL_HARNESS_PROOF_NAME
    if final.is_file():
        return final
    return output / "manual-acceptance-harness-proof.json"


def _prevalidation_bindings(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    source = sv1b.read_json(
        output / "manual-acceptance-harness-proof.json"
    )
    existing_cases = sv1b.read_json(
        output / "manual-acceptance/case-manifest-private.json"
    )
    regenerated_cases, _composition = _generate_cases(
        output, primary_database=primary_database
    )
    if not _case_manifests_equal(
        regenerated_cases, existing_cases
    ):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_case_regeneration_drift"
        )
    relative_proof_sources, proofs = _resolve_proof_sources(
        output, source.get("proof_sources")
    )
    current = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=existing_cases,
    )
    expected = dict(source["bindings"])
    for value in (current, expected):
        value.pop("git_head", None)
        value.pop("binding_fingerprint", None)
    if current != expected:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_prevalidation_evidence_drift"
        )
    return _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=existing_cases,
    )


def _current_bindings(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
) -> dict[str, Any]:
    active_path = _active_harness_proof_path(output)
    if active_path.name == MANUAL_ACCEPTANCE_FINAL_BINDING_V5_NAME:
        return _validated_manual_acceptance_repair_v5_binding(
            output,
            primary_database=primary_database,
            replay_database=replay_database,
        )
    proof = sv1b.read_json(active_path)
    cases = sv1b.read_json(output / "manual-acceptance/case-manifest-private.json")
    expected = proof["bindings"]
    relative_proof_sources, proofs = _resolve_proof_sources(
        output, proof.get("proof_sources")
    )
    audit_binding = (
        _validated_audit_v4_binding(
            output,
            expected_file_sha256=str(
                proof.get("audit_closeout_validation", {}).get(
                    "proof_file_sha256"
                )
                or ""
            ),
        )
        if active_path.name == AUDIT_CLOSEOUT_FINAL_BINDING_V4_NAME
        else (
            _validated_audit_v3_binding(output)
            if active_path.name == AUDIT_CLOSEOUT_FINAL_BINDING_V3_NAME
            else None
        )
    )
    current = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=cases,
        audit_binding=audit_binding,
    )
    if current != expected:
        raise ManualAcceptanceHarnessError("manual_acceptance_binding_invalidated")
    return current


def normalize_submission(
    payload: Mapping[str, Any],
    case_ids: set[str],
) -> list[dict[str, str]]:
    submitted = payload.get("results")
    if not isinstance(submitted, list) or len(submitted) != len(case_ids):
        raise ManualAcceptanceHarnessError("results_invalid")
    by_id = {
        str(row.get("case_id")): row
        for row in submitted
        if isinstance(row, Mapping)
    }
    if set(by_id) != case_ids:
        raise ManualAcceptanceHarnessError("case_membership_mismatch")
    normalized = []
    for case_id in sorted(case_ids):
        row = by_id[case_id]
        decision = str(row.get("decision") or "pending").casefold()
        comment = str(row.get("comment") or "")[:2000]
        if decision not in {"pass", "fail", "pending"}:
            raise ManualAcceptanceHarnessError("decision_invalid")
        normalized.append({"case_id": case_id, "decision": decision, "comment": comment})
    return normalized


def _resolve_accepted_media_path(
    stored_path: str,
    storage_root: Path,
) -> Path | None:
    candidate = Path(stored_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (storage_root / candidate).resolve()
    )
    root = storage_root.resolve()
    if root not in resolved.parents or not resolved.is_file():
        return None
    return resolved


def _media_content_hash_matches(path: Path, expected_hash: str) -> bool:
    expected = str(expected_hash or "").strip().casefold()
    algorithm = {32: "md5", 40: "sha1", 64: "sha256"}.get(len(expected))
    if algorithm is None or any(ch not in "0123456789abcdef" for ch in expected):
        return False
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def _validated_media_display_path(
    row: Mapping[str, Any], storage_root: Path
) -> Path | None:
    """Bind the original bytes to the DB hash before using a derived thumbnail."""

    original = _resolve_accepted_media_path(
        str(row.get("path") or ""), storage_root
    )
    if original is None:
        return None
    if not _media_content_hash_matches(original, str(row.get("hash") or "")):
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_media_content_hash_mismatch"
        )
    thumbnail = _resolve_accepted_media_path(
        str(row.get("thumbnail_path") or ""), storage_root
    )
    return thumbnail or original


def create_app(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    prevalidation: bool = False,
):
    from fastapi import Body, FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    output = output.resolve()
    binding_loader = (
        _prevalidation_bindings
        if prevalidation
        else _current_bindings
    )
    bindings = binding_loader(
        output,
        primary_database=primary_database,
        replay_database=replay_database,
    )
    cases = sv1b.read_json(output / "manual-acceptance/case-manifest-private.json")
    case_ids = {str(row["case_id"]) for row in cases}
    allowed_labels = {str(row["safe_media_label"]) for row in cases}
    storage_root = sv1b.ACCEPTED_STORAGE.resolve()
    engine = sv1b.engine_for(primary_database)
    media_paths: dict[str, Path] = {}
    try:
        with engine.connect() as connection:
            rows = list(connection.execute(text(
                "SELECT hash,path,thumbnail_path FROM blombooru_media WHERE hash IS NOT NULL"
            )).mappings())
    finally:
        engine.dispose()
    for row in rows:
        label = _safe_media_label(str(row["hash"]))
        if label not in allowed_labels:
            continue
        path = _validated_media_display_path(row, storage_root)
        if path is not None:
            media_paths[label] = path
    missing_media = sorted(allowed_labels - set(media_paths))
    if missing_media:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_media_membership_missing:{len(missing_media)}"
        )

    app = FastAPI(title="SCV2-SV1B Manual Acceptance", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        refreshed = binding_loader(
            output,
            primary_database=primary_database,
            replay_database=replay_database,
        )
        if refreshed != bindings:
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_reload_binding_drift"
            )
        return _HTML

    @app.get("/api/cases")
    def api_cases() -> dict[str, Any]:
        refreshed = binding_loader(
            output,
            primary_database=primary_database,
            replay_database=replay_database,
        )
        if refreshed != bindings:
            raise ManualAcceptanceHarnessError(
                "manual_acceptance_cases_binding_drift"
            )
        return {
            "harness_version": HARNESS_VERSION,
            "bindings": bindings,
            "cases": cases,
        }

    @app.get("/media/{safe_label}")
    def media(safe_label: str):
        path = media_paths.get(safe_label)
        if path is None:
            raise HTTPException(status_code=404, detail="media_not_found")
        return FileResponse(path)

    @app.post("/api/export")
    def export(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        refreshed = binding_loader(
            output,
            primary_database=primary_database,
            replay_database=replay_database,
        )
        if refreshed != bindings:
            raise HTTPException(status_code=409, detail="binding_invalidated")
        try:
            normalized = normalize_submission(payload, case_ids)
        except ManualAcceptanceHarnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = {
            "harness_version": HARNESS_VERSION,
            "bindings": bindings,
            "acceptance_case_manifest_fingerprint": bindings[
                "acceptance_case_manifest_fingerprint"
            ],
            "per_case_result": normalized,
            "user_comments": str(payload.get("overall_comment") or "")[:5000],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "manual_acceptance_status": "submitted_user_review",
        }
        result_path = output / "manual-acceptance/manual-acceptance-result.json"
        if result_path.exists():
            raise HTTPException(
                status_code=409,
                detail="manual_acceptance_result_already_exists",
            )
        _write_json_exclusive_atomic(result_path, result)
        return {"saved": True, "relative_path": "manual-acceptance/manual-acceptance-result.json"}

    return app


_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCV2-SV1B 手工验收</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#111827;color:#e5e7eb}main{max-width:1180px;margin:auto;padding:24px}
.case{display:grid;grid-template-columns:260px 1fr;gap:18px;background:#1f2937;border:1px solid #374151;border-radius:12px;padding:16px;margin:16px 0}
img{width:260px;height:260px;object-fit:contain;background:#0b1020;border-radius:8px}.meta{white-space:pre-wrap;background:#111827;padding:10px;border-radius:8px;overflow:auto}
textarea{width:100%;min-height:70px;background:#111827;color:#e5e7eb;border:1px solid #4b5563}.controls{display:flex;gap:16px;margin:10px 0}
button{padding:10px 16px}.binding{font-family:monospace;word-break:break-all;font-size:12px}</style></head>
<body><main><h1>SCV2-SV1B 40-case 手工验收</h1><p>逐项选择 Pass / Fail / Pending，可填写 Comment。导出不会修改数据库。</p>
<div id="binding" class="binding"></div><div id="cases"></div><h2>总体备注</h2><textarea id="overall"></textarea><p><button id="export">导出结果</button> <span id="status"></span></p>
<script>
const state={}; const el=(name,text)=>{const n=document.createElement(name);if(text!==undefined)n.textContent=text;return n};
fetch('/api/cases').then(r=>r.json()).then(data=>{document.getElementById('binding').textContent='Binding: '+data.bindings.binding_fingerprint;
const root=document.getElementById('cases');data.cases.forEach(c=>{state[c.case_id]={case_id:c.case_id,decision:'pending',comment:''};const box=el('section');box.className='case';
const img=el('img');img.src='/media/'+encodeURIComponent(c.safe_media_label);img.alt='验收媒体 '+c.case_id;box.appendChild(img);const body=el('div');body.appendChild(el('h2',c.case_id+' · '+c.title));body.appendChild(el('h3','预期行为'));body.appendChild(el('p',c.expected_behavior));body.appendChild(el('h3','实际结果'));const actual=el('div',JSON.stringify(c.actual_result,null,2));actual.className='meta';body.appendChild(actual);body.appendChild(el('h3','证据来源'));const prov=el('div',JSON.stringify(c.provenance,null,2));prov.className='meta';body.appendChild(prov);const controls=el('div');controls.className='controls';['pass','fail','pending'].forEach(v=>{const label=el('label');const radio=el('input');radio.type='radio';radio.name='d_'+c.case_id;radio.value=v;if(v==='pending')radio.checked=true;radio.onchange=()=>state[c.case_id].decision=v;label.append(radio,document.createTextNode(' '+v.toUpperCase()));controls.appendChild(label)});body.appendChild(controls);const comment=el('textarea');comment.placeholder='Comment';comment.oninput=()=>state[c.case_id].comment=comment.value;body.appendChild(comment);box.appendChild(body);root.appendChild(box)})});
document.getElementById('export').onclick=()=>fetch('/api/export',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({results:Object.values(state),overall_comment:document.getElementById('overall').value})}).then(async r=>{const x=await r.json();if(!r.ok)throw Error(x.detail||'export failed');document.getElementById('status').textContent='已保存: '+x.relative_path}).catch(e=>document.getElementById('status').textContent='失败: '+e.message);
</script></main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--primary-db", required=True)
    parser.add_argument("--replay-db", required=True)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("APP_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--prevalidate", action="store_true")
    parser.add_argument("--prepare-v5-from", type=Path)
    parser.add_argument("--audit-v5-from", type=Path)
    parser.add_argument("--finalize-v5-from", type=Path)
    args = parser.parse_args()
    if os.getenv("VIOLET_ENV") != "test":
        raise ManualAcceptanceHarnessError("manual_acceptance_requires_violet_env_test")
    requested_operations = sum(
        bool(value)
        for value in (
            args.build,
            args.prepare_v5_from,
            args.audit_v5_from,
            args.finalize_v5_from,
        )
    )
    if requested_operations > 1:
        raise ManualAcceptanceHarnessError(
            "manual_acceptance_operation_membership_invalid"
        )
    if args.prepare_v5_from:
        result = prepare_v5_evidence_root(
            args.prepare_v5_from,
            args.output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.build:
        proof = build_harness(
            args.output,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
            port=args.port,
        )
        print(json.dumps({
            "status": proof["status"],
            "case_count": proof["case_count"],
            "localhost_url": proof["localhost_url"],
            "private_values_exposed": False,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if args.audit_v5_from:
        proof = build_manual_acceptance_repair_v5_audit(
            args.output,
            args.audit_v5_from,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
        )
        print(json.dumps({
            "passed": proof["passed"],
            "proof_fingerprint": proof["proof_fingerprint"],
            "git_head": proof["git_head"],
        }, sort_keys=True))
        return 0
    if args.finalize_v5_from:
        proof = finalize_manual_acceptance_repair_v5_binding(
            args.output,
            args.finalize_v5_from,
            primary_database=args.primary_db,
            replay_database=args.replay_db,
            port=args.port,
        )
        print(json.dumps({
            "passed": proof["passed"],
            "binding_fingerprint": proof["bindings"][
                "binding_fingerprint"
            ],
            "git_head": proof["bindings"]["git_head"],
        }, sort_keys=True))
        return 0
    import uvicorn

    app = create_app(
        args.output,
        primary_database=args.primary_db,
        replay_database=args.replay_db,
        prevalidation=args.prevalidate,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
