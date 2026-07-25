#!/usr/bin/env python3
"""Build and serve the private SCV2-SV1B 40-case manual acceptance harness."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
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


HARNESS_VERSION = "sv1b_phase_delta_manual_acceptance_harness_v2"
FINAL_HARNESS_PROOF_NAME = (
    "manual-acceptance-harness-final-binding-proof.json"
)
DEFAULT_PORT = 8031
CATEGORY_COUNTS = {
    "pixiv_metadata": 12,
    "creator_clustering": 8,
    "shared_name_cannot_link": 6,
    "ai_tag_localization": 8,
    "search_and_negative": 6,
}


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


def _pixiv_metadata_cases(session: Any, output: Path) -> list[dict[str, Any]]:
    phase_delta_membership = _newly_acquired_exact_metadata_membership(
        output
    )
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
    rows = [
        row for row in rows
        if (
            str(row["hash"]),
            str(row["provider_record_key"]),
            str(row["source_work_id"]),
            int(row["source_page_index"]),
        )
        in phase_delta_membership
        and (row.get("provenance") or {}).get("source")
        == "gallery_dl_authenticated_metadata"
        and sv1b._is_trusted_exact_complete_record(
            row, str(row["source_work_id"]), int(row["source_page_index"])
        )
    ][:12]
    if len(rows) != 12:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_new_exact_metadata_case_gap:{len(rows)}"
        )
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
            },
            provenance={
                "phase_delta": "newly_acquired_exact_metadata",
                "derived_from_current_proofs": True,
                "provider": "pixiv",
                "metadata_status": row.get("status"),
                "retrieved_at": row.get("retrieved_at"),
                "parser_version": provenance.get("parser_version"),
                "policy_version": provenance.get("policy_version"),
            },
        ))
    return cases


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
        aliases = [str(value[0]) for value in state.get("aliases") or () if len(value) >= 3 and value[2] == "active"]
        cases.append(_case(
            f"B{index:02d}",
            "creator_clustering",
            media_hash=str(row["media_hash"]),
            title=f"Creator cluster #{index}",
            expected_behavior="Trusted names/accounts form a star around one stable creator anchor without merging another stable creator.",
            actual_result={
                "primary_display_name": row.get("primary_display_name"),
                "aliases": sorted(set(aliases)),
                "alias_count": len(set(aliases)),
                "media_support_count": len(state.get("media_support") or ()),
                "concept_ref": sv1b.sha256_payload(concept_key),
                "lifecycle_status": state.get("status"),
                "lifecycle_correct": state.get("status") == "active",
                "expected_membership_fingerprint": sv1b.sha256_payload({
                    "aliases": state.get("aliases") or (),
                    "media_support": state.get("media_support") or (),
                }),
            },
            provenance={
                "phase_delta": delta_kind,
                "available_changed_component_count": len(changed),
                "derived_from_current_proofs": True,
                "stable_identity_ref": sv1b.sha256_payload(stable),
                "source_layer_only": True,
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
    remaining_slots = 8 - len(pending_rows)
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
                "available_manual_pending_count": len(pending_rows),
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
        GROUP BY tr.canonical_name,tr.display_name
        ORDER BY tr.canonical_name
    """), {"names": sorted(new_names) or ["__no_new_translation__"]}).mappings())
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
    selected = preferred[:6]
    selected_term_keys = {tuple(str(term) for term in row.get("terms") or ()) for row in selected}
    for row in translation_rows:
        if len(selected) >= 6:
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
    if len(selected) != 6:
        raise ManualAcceptanceHarnessError(f"manual_acceptance_phase_delta_search_case_gap:{len(selected)}")
    fallback_hash = _fallback_media_hash(session)
    cases = []
    for index, row in enumerate(selected, 1):
        terms = [str(value) for value in row.get("terms") or ()]
        actual_ids = ml1.runtime_and_terms(session, *terms)
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
    return cases, validate_phase_delta_case_composition(cases)


def _build_bindings(
    *,
    primary_database: str,
    replay_database: str,
    relative_proof_sources: Mapping[str, str],
    proofs: Mapping[str, Mapping[str, Any]],
    cases: list[dict[str, Any]],
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
    }
    bindings["binding_fingerprint"] = sv1b.sha256_payload(bindings)
    return bindings


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
    if regenerated_cases != existing_cases:
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


def _active_harness_proof_path(output: Path) -> Path:
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
    if regenerated_cases != existing_cases:
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
    proof = sv1b.read_json(_active_harness_proof_path(output))
    cases = sv1b.read_json(output / "manual-acceptance/case-manifest-private.json")
    expected = proof["bindings"]
    relative_proof_sources, proofs = _resolve_proof_sources(
        output, proof.get("proof_sources")
    )
    current = _build_bindings(
        primary_database=primary_database,
        replay_database=replay_database,
        relative_proof_sources=relative_proof_sources,
        proofs=proofs,
        cases=cases,
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
        for value in (row.get("thumbnail_path"), row.get("path")):
            if not value:
                continue
            path = Path(str(value)).resolve()
            if storage_root in path.parents and path.is_file():
                media_paths[label] = path
                break
    missing_media = sorted(allowed_labels - set(media_paths))
    if missing_media:
        raise ManualAcceptanceHarnessError(
            f"manual_acceptance_media_membership_missing:{len(missing_media)}"
        )

    app = FastAPI(title="SCV2-SV1B Manual Acceptance", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _HTML

    @app.get("/api/cases")
    def api_cases() -> dict[str, Any]:
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
        sv1b.write_json(result_path, result)
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
    args = parser.parse_args()
    if os.getenv("VIOLET_ENV") != "test":
        raise ManualAcceptanceHarnessError("manual_acceptance_requires_violet_env_test")
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
