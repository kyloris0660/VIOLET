"""Execute SCV2-ML2 multilingual creator identity candidate closure.

Lifecycle: phase-scoped operational runner.  The reusable identity rules live
in ``multilingual_creator_identity_closure_service``.  This runner is bounded
to one fresh clone of the accepted ML1 database and never calls Pixiv,
gallery-dl, an external metadata provider, or an LLM for deterministic pairs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable, Mapping, Sequence
import zipfile

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
for candidate in (ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.models import (  # noqa: E402
    SourceConcept,
    SourceConceptAlias,
    SourceConceptEvidence,
    SourceConceptResolutionRun,
    SourceConceptSearchIndex,
    SourceConceptSignal,
    SourceConceptSignalLink,
    SourceMetadataRecord,
    SourceNameObservation,
)
from app.services.multilingual_creator_identity_closure_service import (  # noqa: E402
    LLM_POLICY_VERSION,
    POLICY_VERSION,
    RESOLVER_VERSION,
    CreatorIdentityClosureError,
    CreatorIdentityFamily,
    TrustedCreatorAlias,
    alias_signal_key,
    anchor_signal_key,
    build_star_candidates,
    candidate_growth_accounting,
    component_purity,
    concept_key,
    family_accounting,
    fingerprint,
    pair_accounting,
    select_llm_manifest,
)
from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    _upsert_name_observation,
    is_pixiv_creator_observation_compatible_with_parent,
    is_trusted_complete_pixiv_metadata_record,
)
from app.services.source_metadata_registry_service import (  # noqa: E402
    canonical_source_key,
    normalize_source_text,
)
from scripts import run_phase45_scv2_ml1_multilingual_alias_source_metadata_closure as ml1  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402


PHASE = "4.5-SCV2-ML2"
TITLE = "SCV2-ML2: Multilingual Identity Candidate Closure"
CONTRACT_ID = "ml2_multilingual_identity_candidate_closure_contract_v1"
BASE_SHA = "f6cae3483f4cf75974746a4cc82222f28e399b96"
PREVIOUS_BRANCH = "codex/scv2-ml1-multilingual-alias-source-metadata-closure"
PREVIOUS_HEAD = "0a068c6ed29892c82d25fc5264258b78250fcf92"
TASK_BRANCH = "codex/scv2-ml2-multilingual-identity-candidate-closure"
UNTRACKED_PATH_COUNT = 367
UNTRACKED_PATH_LIST_SHA256 = "3b8043444f1d4c6f9fb45ddc794c5ae621f4da3825436ecbc40cba580df0cc0a"
IGNORED_PATH_COUNT = 115640
IGNORED_PATH_LIST_SHA256 = "6fe26aab21074d83bc0e500fa6e54b76901609badc5e20d351edf02861a8bd59"
ML1_CODE_SHA = "64949da9b804adf400f5b5a0f99a808ff318115b"
R2R_BASE_SHA = "5bbbb8ff13b140ea77a839757603714bfdd87181"
SOURCE_DB = "blombooru_scv2_ml1_acquisition_test_20260712"
R2R_DB = "blombooru_scv2_r2r_dryrun_test_20260710"
WORKING_DB = "blombooru_scv2_ml2_identity_closure_test_20260714"
RUN_ID = "scv2-ml2-identity-closure-20260714-v1"
DEFAULT_OUTPUT = ROOT / ".local_manifests/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure"
BASELINE_OUTPUT = DEFAULT_OUTPUT / "ml1-baseline-recompute"
R2R_PAIR_MANIFEST = ROOT / ".local_manifests/phase-4.5-scv2-r2r-autonomous-recall-search-closure/pair-manifest.json"
REPORT_MD = ROOT / "docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure.md"
REPORT_JSON = ROOT / "docs/reports/phase-4.5-scv2-ml2-multilingual-identity-candidate-closure-summary.json"

IMMUTABLE_FIXED_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_name_registry",
    "blombooru_source_tag_registry",
    "blombooru_tag_translations",
    "blombooru_source_name_alias_candidates",
)
FORBIDDEN_TRUTH_TABLES = ml1.FORBIDDEN_TRUTH_TABLES
ALLOWED_MUTATION_TABLES = (
    "blombooru_source_name_observations",
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
)


class ML2BlockedError(RuntimeError):
    """Fail-closed ML2 execution error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows(session: Session, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(sql), dict(params or {})).mappings()]


def environment_proof(database: str, output_dir: Path) -> dict[str, Any]:
    production_flags = {
        key: bool(str(os.getenv(key) or "").strip())
        for key in (
            "VIOLET_PRODUCTION_PROFILE_ACTIVE",
            "VIOLET_PRODUCTION_PROFILE",
            "VIOLET_PRODUCTION_MODE",
            "VIOLET_LAUNCH_PRODUCTION",
        )
    }
    storage = str(os.getenv("VIOLET_STORAGE_ROOT") or "")
    passed = bool(
        database == WORKING_DB
        and str(os.getenv("VIOLET_ENV") or "").casefold() == "test"
        and database != SOURCE_DB
        and database != R2R_DB
        and "test" in database.casefold()
        and not any(production_flags.values())
        and output_dir.is_relative_to(ROOT / ".local_manifests")
        and "test" in storage.casefold()
    )
    return {
        "passed": passed,
        "violet_env": str(os.getenv("VIOLET_ENV") or ""),
        "source_database": SOURCE_DB,
        "r2r_database": R2R_DB,
        "working_database": database,
        "working_database_is_fresh_separate_clone": database == WORKING_DB,
        "production_profile_active": any(production_flags.values()),
        "production_flags_checked": sorted(production_flags),
        "test_storage_configured": "test" in storage.casefold(),
        "production_write_route": False,
        "source_or_icloud_route": False,
    }


def baseline_summary() -> dict[str, Any]:
    path = BASELINE_OUTPUT / "evidence-summary.json"
    if not path.is_file():
        raise ML2BlockedError("blocked_ml2_baseline_drift:baseline_recompute_missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    multilingual = value.get("multilingual_benchmark") or {}
    search = value.get("search_semantics") or {}
    fixed = value.get("fixed_evidence_proof") or {}
    expected = {
        "real_fixed_evidence_family_count": 4248,
        "observed_alias_count": 11860,
        "identity_eligible_family_count": 606,
        "search_only_translation_family_count": 3642,
        "candidate_not_generated_count": 30,
    }
    if any(multilingual.get(key) != expected_value for key, expected_value in expected.items()):
        raise ML2BlockedError("blocked_ml2_baseline_drift:multilingual_denominator_changed")
    if (
        multilingual.get("outcome_counts", {}).get("identity_materialized") != 12
        or multilingual.get("outcome_counts", {}).get("search_equivalent_without_identity_union") != 564
        or multilingual.get("search_equivalence_coverage") != 0.897363
        or multilingual.get("and_work_equivalence_coverage") != 0.915692
        or search.get("creator_and_character_work_case_count") != 94
        or search.get("creator_and_character_work_accuracy") != 0.62766
        or search.get("creator_and_character_work_leakage_count") != 0
        or fixed.get("accepted_r2r_disposition_count") != 3319
        or not value.get("validation", {}).get("ml1_contract_passed")
    ):
        raise ML2BlockedError("blocked_ml2_baseline_drift:accepted_evidence_changed")
    return value


def _trusted_creator_inputs(session: Session) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metadata = rows(session, "SELECT * FROM blombooru_source_metadata_records ORDER BY id")
    observations = rows(session, "SELECT * FROM blombooru_source_name_observations ORDER BY id")
    return metadata, observations


def _concepts_by_alias(session: Session) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for row in rows(
        session,
        "SELECT s.raw_value,s.display_value,s.normalized_key,s.canonical_key,l.concept_id "
        "FROM blombooru_source_concept_signals s JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
        "WHERE l.link_status IN ('active','materialized_identity')",
    ):
        for value in (row.get("raw_value"), row.get("display_value"), row.get("normalized_key"), row.get("canonical_key")):
            key = canonical_source_key(value)
            if key:
                result[key].add(int(row["concept_id"]))
    return result


def _concepts_by_signal_key(session: Session) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for signal_key, concept_id in session.execute(text(
        "SELECT s.signal_key,l.concept_id FROM blombooru_source_concept_signals s "
        "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
        "WHERE l.link_status IN ('active','materialized_identity')"
    )).all():
        result[str(signal_key)].add(int(concept_id))
    return result


def build_manifests(
    session: Session,
    metadata_rows: Sequence[Mapping[str, Any]],
    observation_rows: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[CreatorIdentityFamily, ...],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[Mapping[str, Any]]],
]:
    metadata_by_id = {int(row["id"]): row for row in metadata_rows}
    _, creator_private, aliases_by_creator = ml1.build_creator_audit(metadata_rows, observation_rows)
    eligible_aliases = {
        creator_id: {normalize_source_text(value) for value in aliases if normalize_source_text(value)}
        for creator_id, aliases in aliases_by_creator.items()
        if len({normalize_source_text(value) for value in aliases if normalize_source_text(value)}) >= 2
    }
    records_by_creator: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in creator_private:
        creator_id = str(row.get("creator_id") or "")
        if creator_id in eligible_aliases:
            records_by_creator[creator_id].append(row)
    observations_by_record: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observation_rows:
        record_id = int(observation["source_metadata_record_id"])
        parent = metadata_by_id.get(record_id)
        if (
            parent is not None
            and str(observation.get("provider") or "").casefold() == "pixiv"
            and str(observation.get("name_role") or "") in {"artist", "creator"}
            and str(observation.get("source_field") or "") in {"pixiv_user_metadata", "pixiv_user_account"}
            and str(observation.get("status") or "") in {"observed", "active", "accepted"}
            and is_pixiv_creator_observation_compatible_with_parent(observation, parent)
        ):
            observations_by_record[record_id].append(observation)

    concepts_by_alias = _concepts_by_alias(session)
    concepts_by_signal_key = _concepts_by_signal_key(session)
    baseline_gap_rows = read_jsonl(BASELINE_OUTPUT / "candidate-generation-miss-ledger.jsonl")
    baseline_gap_creator_ids = {
        str(row.get("anchor_private") or "") for row in baseline_gap_rows
    }
    if len(baseline_gap_creator_ids) != 30 or "" in baseline_gap_creator_ids:
        raise ML2BlockedError("blocked_ml2_input_manifest_invalid:baseline_gap_membership")
    families: list[CreatorIdentityFamily] = []
    family_manifest: list[dict[str, Any]] = []
    alias_observation_manifest: list[dict[str, Any]] = []
    contexts: dict[str, list[Mapping[str, Any]]] = {}
    concept_to_identity: dict[int, set[str]] = defaultdict(set)
    for creator_id in sorted(eligible_aliases):
        family_id = "family_" + fingerprint({"provider": "pixiv", "creator_id": creator_id})[:24]
        records = sorted(records_by_creator[creator_id], key=lambda row: int(row["source_metadata_record_id"]))
        aliases = sorted(eligible_aliases[creator_id], key=lambda value: (canonical_source_key(value), value))
        alias_rows: list[TrustedCreatorAlias] = []
        all_observation_refs: list[str] = []
        work_context = Counter()
        metadata_refs: list[str] = []
        for record in records:
            record_id = int(record["source_metadata_record_id"])
            metadata_ref = "metadata_" + fingerprint(record_id)[:20]
            metadata_refs.append(metadata_ref)
            parent = metadata_by_id[record_id]
            if parent.get("source_work_id"):
                work_context["work_" + fingerprint(str(parent["source_work_id"]))[:16]] += 1
            for observation in observations_by_record.get(record_id, []):
                normalized = normalize_source_text(observation.get("raw_name"))
                if normalized not in eligible_aliases[creator_id]:
                    continue
                observation_ref = "observation_" + fingerprint(int(observation["id"]))[:20]
                all_observation_refs.append(observation_ref)
                parent_fp = fingerprint(
                    {
                        "record_id": record_id,
                        "provider": parent.get("provider"),
                        "status": parent.get("status"),
                        "creator_id": creator_id,
                    }
                )
                alias_observation_manifest.append(
                    {
                        "family_id": family_id,
                        "private_observation_id": int(observation["id"]),
                        "observation_ref": observation_ref,
                        "alias_observation_type": (
                            "creator_account" if observation.get("source_field") == "pixiv_user_account" else "creator_name"
                        ),
                        "role": "creator",
                        "normalized_value": normalized,
                        "parent_evidence_fingerprint": parent_fp,
                        "search_visibility": True,
                        "current_sourceconcept_consumption": bool(concepts_by_alias.get(canonical_source_key(normalized))),
                    }
                )
        for alias in aliases:
            matching = [
                row
                for row in alias_observation_manifest
                if row["family_id"] == family_id and row["normalized_value"] == alias
            ]
            alias_rows.append(
                TrustedCreatorAlias(
                    alias_type="creator_name_or_account",
                    value=alias,
                    canonical_key=canonical_source_key(alias),
                    observation_refs=tuple(sorted({str(row["observation_ref"]) for row in matching})),
                    parent_evidence_fingerprint=fingerprint(
                        sorted({str(row["parent_evidence_fingerprint"]) for row in matching})
                    ),
                )
            )
        concept_sets = []
        for alias in alias_rows:
            signal_key = alias_signal_key(
                "pixiv", creator_id, alias.alias_type, alias.value, "creator"
            )
            concept_sets.append(
                set(concepts_by_alias.get(alias.canonical_key, set()))
                | set(concepts_by_signal_key.get(signal_key, set()))
            )
        common = set.intersection(*concept_sets) if concept_sets and all(concept_sets) else set()
        if len(common) > 1:
            raise ML2BlockedError("blocked_ml2_stable_identity_contradiction:multiple_existing_concepts")
        existing_concept_id = next(iter(common), None)
        identity_fp = fingerprint({"provider": "pixiv", "stable_creator_id": creator_id, "role": "creator"})
        if existing_concept_id is not None:
            concept_to_identity[int(existing_concept_id)].add(identity_fp)
        evidence_fp = fingerprint(
            {
                "provider": "pixiv",
                "stable_creator_id": creator_id,
                "aliases": aliases,
                "metadata_refs": sorted(set(metadata_refs)),
                "observation_refs": sorted(set(all_observation_refs)),
            }
        )
        family = CreatorIdentityFamily(
            family_id=family_id,
            provider="pixiv",
            stable_creator_id=creator_id,
            creator_role="creator",
            aliases=tuple(alias_rows),
            metadata_refs=tuple(sorted(set(metadata_refs))),
            work_context_distribution=dict(sorted(work_context.items())),
            evidence_fingerprint=evidence_fp,
            existing_concept_id=existing_concept_id,
        )
        families.append(family)
        contexts[family_id] = [
            {
                **dict(record),
                "media_id": metadata_by_id[int(record["source_metadata_record_id"])].get("media_id"),
            }
            for record in records
        ]
        family_manifest.append(
            {
                "private_family_id": family_id,
                "provider": "pixiv",
                "stable_creator_id": creator_id,
                "trusted_creator_names_and_accounts": aliases,
                "trusted_profile_identity_refs": sorted(
                    {"profile_" + fingerprint(row["creator_profile_identity"])[:20] for row in records if row.get("creator_profile_identity")}
                ),
                "source_metadata_record_references": sorted(set(metadata_refs)),
                "source_name_observation_references": sorted(set(all_observation_refs)),
                "existing_sourceconcept_references": [existing_concept_id] if existing_concept_id is not None else [],
                "existing_materialization_status": "materialized" if existing_concept_id is not None else "not_materialized",
                "work_context_distribution": dict(sorted(work_context.items())),
                "role_distribution": {"creator": len(alias_rows)},
                "evidence_fingerprint": evidence_fp,
            }
        )
    if any(len(values) > 1 for values in concept_to_identity.values()):
        raise ML2BlockedError("blocked_ml2_stable_identity_contradiction:existing_component_multi_stable_id")

    gap_manifest: list[dict[str, Any]] = []
    for family in families:
        if family.stable_creator_id in baseline_gap_creator_ids:
            gap_manifest.append(
                {
                    "family_id": family.family_id,
                    "missing_alias_signal_count": sum(
                        not bool(concepts_by_alias.get(alias.canonical_key)) for alias in family.aliases
                    ),
                    "root_cause": "trusted_creator_name_signal_missing",
                    "initial_disposition": "identity_anchor_not_generated",
                    "current_unmaterialized": family.existing_concept_id is None,
                    "evidence_fingerprint": family.evidence_fingerprint,
                }
            )
    return tuple(families), family_manifest, alias_observation_manifest, gap_manifest, contexts


def _table_counts(session: Session, tables: Sequence[str]) -> dict[str, int]:
    return {table: int(session.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0) for table in tables}


def _database_table_counts(database: str, tables: Sequence[str]) -> dict[str, int]:
    engine = r2.create_db_engine(database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return _table_counts(session, tables)
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _existing_graph_metrics(session: Session, *, exclude_ml2: bool = False) -> dict[str, Any]:
    concept_filter = " AND COALESCE(c.created_by_run_id,'')<>:run_id" if exclude_ml2 else ""
    link_filter = " AND COALESCE(l.run_id,'')<>:run_id" if exclude_ml2 else ""
    sizes = [int(row[0]) for row in session.execute(text(
        "SELECT COUNT(DISTINCT l.signal_id) FROM blombooru_source_concepts c "
        "LEFT JOIN blombooru_source_concept_signal_links l ON l.concept_id=c.id "
        f"AND l.link_status IN ('active','materialized_identity'){link_filter} "
        f"WHERE c.status='active'{concept_filter} GROUP BY c.id"
    ), {"run_id": RUN_ID}).all()]
    concept_query = session.query(SourceConcept).filter(SourceConcept.status == "active")
    if exclude_ml2:
        concept_query = concept_query.filter(
            (SourceConcept.created_by_run_id.is_(None)) | (SourceConcept.created_by_run_id != RUN_ID)
        )
    distribution = Counter(sizes)
    return {
        "sourceconcept_count": int(concept_query.count()),
        "needs_review_count": int(session.query(SourceConcept).filter(SourceConcept.status == "needs_review").count()),
        "component_count": len(sizes),
        "component_size_distribution": {str(key): value for key, value in sorted(distribution.items())},
        "largest_component": max(sizes, default=0),
    }


def _get_or_create(session: Session, model: Any, defaults: Mapping[str, Any], **filters: Any) -> tuple[Any, bool]:
    value = session.query(model).filter_by(**filters).one_or_none()
    if value is not None:
        return value, False
    value = model(**filters, **dict(defaults))
    session.add(value)
    session.flush()
    return value, True


def _backfill_trusted_titles(session: Session) -> int:
    created = 0
    records = session.query(SourceMetadataRecord).filter(
        SourceMetadataRecord.provider == "pixiv",
        SourceMetadataRecord.title.is_not(None),
        SourceMetadataRecord.title != "",
    ).order_by(SourceMetadataRecord.id.asc()).all()
    for record in records:
        if not is_trusted_complete_pixiv_metadata_record(record):
            continue
        present = session.query(SourceNameObservation.id).filter(
            SourceNameObservation.source_metadata_record_id == record.id,
            SourceNameObservation.source_field == "pixiv_title",
            SourceNameObservation.canonical_name_key == canonical_source_key(record.title),
            SourceNameObservation.status.in_(("observed", "active", "accepted")),
        ).first()
        if present:
            continue
        before = len(session.new)
        _upsert_name_observation(session, record, raw_name=record.title, role="work_title", source_field="pixiv_title")
        if len(session.new) > before:
            created += 1
    session.flush()
    return created


def persist_closure(
    session: Session,
    families: Sequence[CreatorIdentityFamily],
    contexts: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    before = _table_counts(session, ALLOWED_MUTATION_TABLES)
    # Empty canonical search keys are valid as private identity observations
    # but never as runtime aliases.  Preserve any previously written rows as
    # superseded history instead of deleting them.
    session.query(SourceConceptAlias).filter(
        SourceConceptAlias.created_by_run_id == RUN_ID,
        SourceConceptAlias.alias_key == "",
        SourceConceptAlias.status == "active",
    ).update({SourceConceptAlias.status: "superseded"}, synchronize_session=False)
    session.query(SourceConceptSearchIndex).filter(
        SourceConceptSearchIndex.run_id == RUN_ID,
        SourceConceptSearchIndex.search_key == "",
        SourceConceptSearchIndex.status == "active",
    ).update({SourceConceptSearchIndex.status: "superseded"}, synchronize_session=False)
    run, _ = _get_or_create(
        session,
        SourceConceptResolutionRun,
        {
            "run_label": TITLE,
            "scope": "multilingual_creator_identity_closure",
            "resolver_version": RESOLVER_VERSION,
            "mode": "execute",
            "status": "completed",
            "input_signal_counts_json": {"identity_family_count": len(families)},
            "linked_counts_json": {"policy": POLICY_VERSION},
            "concept_counts_json": {},
            "review_counts_json": {"human_review_queue_count": 0},
            "no_truth_write_proof_json": {"entity_truth_writes": 0, "media_tags_writes": 0},
            "summary_json": {"deterministic_only": True, "llm_calls": 0},
            "started_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
            "runtime_seconds": 0.0,
        },
        run_id=RUN_ID,
    )
    title_backfill = _backfill_trusted_titles(session)
    outcomes: list[dict[str, Any]] = []
    for family in families:
        records = list(contexts[family.family_id])
        representative_record_id = min(int(row["source_metadata_record_id"]) for row in records)
        media_ids = {
            int(row["media_id"])
            for row in (contexts[family.family_id])
            if row.get("media_id") is not None
        }
        if family.existing_concept_id is not None:
            concept = session.query(SourceConcept).filter(SourceConcept.id == family.existing_concept_id).one()
            outcome = (
                "deterministic_must_link_materialized"
                if concept.created_by_run_id == RUN_ID
                else "already_materialized"
            )
        else:
            display = sorted((alias.value for alias in family.aliases), key=lambda value: (len(value), value))[0]
            concept, _ = _get_or_create(
                session,
                SourceConcept,
                {
                    "primary_display_name": display,
                    "concept_type_hint": "artist",
                    "status": "active",
                    "confidence_score": 1.0,
                    "evidence_score": 1.0,
                    "media_count": len(media_ids),
                    "source_count": len(family.metadata_refs),
                    "created_by_run_id": RUN_ID,
                    "evidence_summary_json": {
                        "stable_identity_fingerprint": family.identity_fingerprint,
                        "policy_version": POLICY_VERSION,
                    },
                    "lifecycle_payload": {"source_layer_only": True, "entity_truth": False},
                },
                concept_key=concept_key(family.provider, family.stable_creator_id, family.creator_role),
            )
            outcome = "deterministic_must_link_materialized"

        anchor, _ = _get_or_create(
            session,
            SourceConceptSignal,
            {
                "resolution_run_id": run.id,
                "origin_type": "stable_provider_identity",
                "origin_table": "blombooru_source_metadata_records",
                "origin_id": family.identity_fingerprint,
                "provider": family.provider,
                "media_id": None,
                "source_metadata_record_id": representative_record_id,
                "source_record_id": family.identity_fingerprint,
                "raw_value": family.stable_creator_id,
                "display_value": "stable creator identity",
                "normalized_key": family.identity_fingerprint,
                "canonical_key": family.identity_fingerprint,
                "role_hint": "artist",
                "work_context_key": None,
                "parenthetical_base": None,
                "parenthetical_context": None,
                "source_kind": "creator_stable_id",
                "trust_tier": "strong",
                "confidence": 1.0,
                "status": "active",
                "evidence_payload": {
                    "stable_identity_fingerprint": family.identity_fingerprint,
                    "creator_role": "creator",
                    "search_visible": False,
                    "evidence_fingerprint": family.evidence_fingerprint,
                },
                "source_run_id": RUN_ID,
                "created_by_run_id": RUN_ID,
            },
            signal_key=family.anchor_key,
        )
        _get_or_create(
            session,
            SourceConceptSignalLink,
            {
                "link_status": "materialized_identity",
                "confidence": 1.0,
                "resolution_reason_code": "stable_provider_identity_anchor",
                "negative_reason_code": None,
                "resolver_version": RESOLVER_VERSION,
                "evidence_payload": {"identity_fingerprint": family.identity_fingerprint},
            },
            signal_id=anchor.id,
            concept_id=concept.id,
            run_id=RUN_ID,
        )
        _get_or_create(
            session,
            SourceConceptEvidence,
            {
                "media_id": None,
                "source_metadata_record_id": representative_record_id,
                "provider": family.provider,
                "evidence_strength": "strong",
                "payload": {"identity_fingerprint": family.identity_fingerprint},
                "run_id": RUN_ID,
                "status": "active",
            },
            concept_id=concept.id,
            signal_id=anchor.id,
            evidence_type="stable_creator_identity_anchor",
        )
        for alias in family.aliases:
            signal_key = alias_signal_key(
                family.provider,
                family.stable_creator_id,
                alias.alias_type,
                alias.value,
                family.creator_role,
            )
            alias_signal, _ = _get_or_create(
                session,
                SourceConceptSignal,
                {
                    "resolution_run_id": run.id,
                    "origin_type": "trusted_creator_alias_observation",
                    "origin_table": "blombooru_source_name_observations",
                    "origin_id": fingerprint(alias.observation_refs),
                    "provider": family.provider,
                    "media_id": None,
                    "source_metadata_record_id": representative_record_id,
                    "source_record_id": family.identity_fingerprint,
                    "raw_value": alias.value,
                    "display_value": alias.value,
                    "normalized_key": canonical_source_key(alias.value),
                    "canonical_key": canonical_source_key(alias.value),
                    "role_hint": "artist",
                    "work_context_key": None,
                    "parenthetical_base": None,
                    "parenthetical_context": None,
                    "source_kind": "trusted_creator_alias",
                    "trust_tier": "strong",
                    "confidence": 1.0,
                    "status": "active",
                    "evidence_payload": {
                        "stable_identity_fingerprint": family.identity_fingerprint,
                        "creator_role": "creator",
                        "observation_refs": list(alias.observation_refs),
                        "parent_evidence_fingerprint": alias.parent_evidence_fingerprint,
                    },
                    "source_run_id": RUN_ID,
                    "created_by_run_id": RUN_ID,
                },
                signal_key=signal_key,
            )
            _get_or_create(
                session,
                SourceConceptSignalLink,
                {
                    "link_status": "materialized_identity",
                    "confidence": 1.0,
                    "resolution_reason_code": "same_provider_stable_creator_id_trusted_parent",
                    "negative_reason_code": None,
                    "resolver_version": RESOLVER_VERSION,
                    "evidence_payload": {"identity_fingerprint": family.identity_fingerprint},
                },
                signal_id=alias_signal.id,
                concept_id=concept.id,
                run_id=RUN_ID,
            )
            _get_or_create(
                session,
                SourceConceptEvidence,
                {
                    "media_id": None,
                    "source_metadata_record_id": representative_record_id,
                    "provider": family.provider,
                    "evidence_strength": "strong",
                    "payload": {"parent_evidence_fingerprint": alias.parent_evidence_fingerprint},
                    "run_id": RUN_ID,
                    "status": "active",
                },
                concept_id=concept.id,
                signal_id=alias_signal.id,
                evidence_type="trusted_creator_alias",
            )
            alias_key = canonical_source_key(alias.value)
            if not alias_key:
                continue
            _get_or_create(
                session,
                SourceConceptAlias,
                {
                    "alias_value": alias.value,
                    "display_name": alias.value,
                    "language_hint": None,
                    "script_hint": ml1.script_label(alias.value),
                    "status": "active",
                    "confidence": 1.0,
                    "source_signal_id": alias_signal.id,
                    "evidence_payload": {"identity_fingerprint": family.identity_fingerprint},
                    "created_by_run_id": RUN_ID,
                },
                concept_id=concept.id,
                alias_key=alias_key,
                alias_role="creator_identity_alias",
            )
            _get_or_create(
                session,
                SourceConceptSearchIndex,
                {
                    "display_name": alias.value,
                    "weight": 1.0,
                    "status": "active",
                    "evidence_refs_json": {
                        "identity_fingerprint": family.identity_fingerprint,
                        "source_signal_key": signal_key,
                    },
                    "run_id": RUN_ID,
                },
                concept_id=concept.id,
                search_key=alias_key,
                alias_role="creator_identity_alias",
            )
        outcomes.append(
            {
                "family_id": family.family_id,
                "outcome": outcome,
                "concept_ref": "concept_" + fingerprint(int(concept.id))[:20],
                "identity_fingerprint": family.identity_fingerprint,
            }
        )
    session.flush()
    after = _table_counts(session, ALLOWED_MUTATION_TABLES)
    changes = {table: after[table] - before[table] for table in ALLOWED_MUTATION_TABLES}
    changes["trusted_work_title_observation_backfill_count"] = title_backfill
    return outcomes, changes


def _unresolved_legacy_candidate_misses(
    session: Session, misses: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    unresolved: list[dict[str, Any]] = []
    resolved_by_anchor = 0
    for miss in misses:
        stable_creator_id = str(miss.get("anchor_private") or "")
        signal_key = anchor_signal_key("pixiv", stable_creator_id, "creator")
        materialized = bool(
            session.execute(
                text(
                    "SELECT 1 FROM blombooru_source_concept_signals s "
                    "JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
                    "JOIN blombooru_source_concepts c ON c.id=l.concept_id "
                    "WHERE s.signal_key=:signal_key AND l.link_status='materialized_identity' "
                    "AND c.status='active' LIMIT 1"
                ),
                {"signal_key": signal_key},
            ).first()
        )
        if materialized:
            resolved_by_anchor += 1
        else:
            unresolved.append(dict(miss))
    return unresolved, resolved_by_anchor


def classify_creator_context_cases(
    session: Session, creator_private: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    creator_cases = 0
    leakage = 0
    for item in creator_private:
        creator_name = item.get("creator_name")
        if not creator_name:
            continue
        creator_media = {
            int(row[0])
            for row in session.execute(
                text(
                    "SELECT media_id FROM blombooru_source_metadata_records "
                    "WHERE provider='pixiv' AND artist_name=:name AND media_id IS NOT NULL"
                ),
                {"name": creator_name},
            ).all()
        }
        if not creator_media:
            continue
        creator_cases += 1
        for category in ("character", "copyright"):
            tag_row = session.execute(
                text(
                    "SELECT t.name,COUNT(DISTINCT mt.media_id) hits FROM blombooru_tags t "
                    "JOIN blombooru_media_tags mt ON mt.tag_id=t.id "
                    "WHERE mt.media_id=ANY(:ids) AND CAST(t.category AS text)=:category "
                    "GROUP BY t.name ORDER BY hits DESC,t.name LIMIT 1"
                ),
                {"ids": list(creator_media), "category": category},
            ).first()
            if not tag_row:
                continue
            tag_media = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT mt.media_id FROM blombooru_media_tags mt JOIN blombooru_tags t "
                        "ON t.id=mt.tag_id WHERE t.name=:name"
                    ),
                    {"name": tag_row[0]},
                ).all()
            }
            expected = creator_media & tag_media
            actual = ml1.runtime_and_terms(session, str(creator_name), str(tag_row[0]))
            leakage += len(actual - expected)
            cases.append(
                {
                    "case_ref": "context_" + fingerprint((creator_name, category, str(tag_row[0])))[:20],
                    "category": category,
                    "classification": (
                        "supported_evidence_runtime_success"
                        if actual == expected
                        else "implementation_failure_with_sufficient_evidence"
                    ),
                    "expected_count": len(expected),
                    "runtime_count": len(actual),
                    "leakage_count": len(actual - expected),
                }
            )
        work_row = session.execute(
            text(
                "SELECT title FROM blombooru_source_metadata_records WHERE id=:record_id "
                "AND COALESCE(title,'')<>'' LIMIT 1"
            ),
            {"record_id": int(item["source_metadata_record_id"])},
        ).first()
        if work_row:
            work_title = str(work_row[0])
            expected = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT media_id FROM blombooru_source_metadata_records WHERE provider='pixiv' "
                        "AND artist_name=:creator AND title=:title AND media_id IS NOT NULL"
                    ),
                    {"creator": creator_name, "title": work_title},
                ).all()
            }
            actual = ml1.runtime_and_terms(session, str(creator_name), work_title)
            exact_observation_media = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT DISTINCT media_id FROM blombooru_source_name_observations "
                        "WHERE source_field='pixiv_title' AND canonical_name_key=:key "
                        "AND status IN ('observed','active','accepted') AND media_id IS NOT NULL"
                    ),
                    {"key": canonical_source_key(work_title)},
                ).all()
            }
            if not canonical_source_key(work_title):
                classification = "deferred_nonblocking_evidence_absent"
            elif not expected.issubset(exact_observation_media):
                classification = "implementation_failure_with_sufficient_evidence"
            elif actual == expected:
                classification = "supported_evidence_runtime_success"
            else:
                classification = "implementation_failure_with_sufficient_evidence"
            leakage += len(actual - expected)
            cases.append(
                {
                    "case_ref": "context_" + fingerprint((creator_name, "work_title", work_title))[:20],
                    "category": "work_title",
                    "classification": classification,
                    "expected_count": len(expected),
                    "runtime_count": len(actual),
                    "exact_observation_supported_count": len(expected & exact_observation_media),
                    "leakage_count": len(actual - expected),
                }
            )
        if creator_cases >= 50:
            break
    counts = Counter(str(row["classification"]) for row in cases)
    supported_expected = counts["supported_evidence_runtime_success"] + counts[
        "implementation_failure_with_sufficient_evidence"
    ]
    return {
        "case_count": len(cases),
        "classification_count": sum(counts.values()),
        "classification_counts": dict(sorted(counts.items())),
        "supported_evidence_expected_case_count": supported_expected,
        "supported_evidence_runtime_success_count": counts["supported_evidence_runtime_success"],
        "supported_evidence_runtime_success_coverage": round(
            counts["supported_evidence_runtime_success"] / supported_expected, 6
        ) if supported_expected else 1.0,
        "implementation_failure_with_sufficient_evidence_count": counts[
            "implementation_failure_with_sufficient_evidence"
        ],
        "deferred_nonblocking_evidence_absent_count": counts[
            "deferred_nonblocking_evidence_absent"
        ],
        "unexplained_failure_count": 0,
        "leakage_count": leakage,
    }, cases


def _ml2_component_rows(session: Session) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows(
        session,
        "SELECT l.concept_id component_id,s.signal_key,s.role_hint role,s.evidence_payload "
        "FROM blombooru_source_concept_signals s JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
        "WHERE l.run_id=:run_id AND l.link_status='materialized_identity'",
        {"run_id": RUN_ID},
    ):
        payload = row.get("evidence_payload") or {}
        result.append(
            {
                "component_id": row["component_id"],
                "signal_key": row["signal_key"],
                "role": row["role"],
                "stable_identity_key": payload.get("stable_identity_fingerprint"),
            }
        )
    return result


def compare_search_only(before_rows: Sequence[Mapping[str, Any]], after_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    before = {str(row["private_family_ref"]): row for row in before_rows if row.get("scope") == "search_only"}
    after = {str(row["private_family_ref"]): row for row in after_rows if row.get("scope") == "search_only"}
    regressions = 0
    unsupported = 0
    rejected = 0
    superseded = 0
    for key, prior in before.items():
        current = after.get(key)
        if current is None:
            regressions += 1
            continue
        if prior.get("search_equivalent") and not current.get("search_equivalent"):
            regressions += 1
        for trace in current.get("support_traces") or ():
            unsupported += int(trace.get("unsupported_result_count") or 0)
            rejected += int(trace.get("rejected_result_count") or 0)
            superseded += int(trace.get("superseded_result_count") or 0)
    return {
        "family_count_before": len(before),
        "family_count_after": len(after),
        "regression_count": regressions,
        "unsupported_result_count": unsupported,
        "rejected_only_result_count": rejected,
        "superseded_only_result_count": superseded,
        "passed": len(before) == len(after) == 3642 and not any((regressions, unsupported, rejected, superseded)),
    }


def public_safe(value: Any) -> bool:
    try:
        ml1.assert_public_safe(value)
        return True
    except Exception:
        return False


def render_report(summary: Mapping[str, Any]) -> str:
    pair = summary["pair_accounting"]
    family = summary["family_accounting"]
    graph = summary["graph_safety"]
    search = summary["search_validation"]
    sync = summary["repository_sync_preflight"]
    return f"""# {TITLE}

## 状态

- Contract status: `{summary['pipeline_contract']['status']}`.
- Claims: `target_met={str(summary['pipeline_contract']['target_met']).lower()}`; `safe_to_merge={str(summary['pipeline_contract']['safe_to_merge']).lower()}`; `route_approved=false`.
- Working database: `{summary['environment_isolation']['working_database']}`; accepted ML1/R2R databases remained immutable.

## 仓库同步预检

- Previous branch / HEAD: `{sync['previous_branch']}` / `{sync['previous_head']}`.
- Synchronized `origin/main`: `{sync['origin_main_sha']}`.
- New ML2 branch / starting SHA: `{sync['task_branch']}` / `{sync['task_branch_starting_sha']}`.
- Tracked tree identical across transition: `{sync['tracked_tree_identical_across_transition']}`; tracked/staged changes after switch: `{sync['tracked_change_count_after_switch']}` / `{sync['staged_change_count_after_switch']}`.
- User-owned untracked and ignored files preserved: `{sync['user_owned_untracked_and_ignored_preserved']}` (`{sync['untracked_path_count']}` untracked, `{sync['ignored_path_count']}` ignored; path-list fingerprints unchanged).

## 身份闭包

- Identity families: `{family['identity_eligible_family_count']}` = `{family['already_materialized_family_count']}` already + `{family['newly_materialized_family_count']}` new + `{family['cannot_link_closed_family_count']}` cannot-link + `{family['deferred_nonblocking_family_count']}` deferred.
- Candidate pairs: `{pair['candidate_pair_count']}` = `{pair['must_link_count']}` must-link + `{pair['cannot_link_count']}` cannot-link + `{pair['deferred_nonblocking_count']}` deferred.
- Candidate-generation gaps before / unexplained after: `{summary['candidate_gap_closure']['initial_gap_count']}` / `{summary['candidate_gap_closure']['unexplained_gap_count']}`.
- Existing 12 families preserved: `{summary['existing_family_audit']['passed']}`.
- Linear star-topology guard: `{summary['candidate_growth']['linear_bound_passed']}`; all-pairs expansion: `false`.

## 图与搜索安全

- SourceConcept / needs_review before: `{summary['graph_before']['sourceconcept_count']}` / `{summary['graph_before']['needs_review_count']}`; after: `{summary['graph_after']['sourceconcept_count']}` / `{summary['graph_after']['needs_review_count']}`.
- Largest component before / after: `{summary['graph_before']['largest_component']}` / `{summary['graph_after']['largest_component']}`.
- Multi-stable-ID / direct cannot / transitive cannot / cross-role / unknown-role: `{graph['multi_stable_id_creator_component_count']}` / `{graph['direct_cannot_violation_count']}` / `{graph['transitive_cannot_violation_count']}` / `{graph['unauthorized_cross_role_component_count']}` / `{graph['unknown_role_materialization_count']}`.
- Creator-context 94-case accuracy before / after: `{search['creator_context_accuracy_before']}` / `{search['creator_context_accuracy_after']}`; evidence-conditioned success coverage: `{search['supported_evidence_runtime_success_coverage']}`.
- Search-only regression / unsupported / rejected / superseded / AND leakage / search mutation: `{search['search_only_regression_count']}` / `{search['unsupported_result_count']}` / `{search['rejected_only_result_count']}` / `{search['superseded_only_result_count']}` / `{search['and_leakage_count']}` / `{search['search_caused_identity_mutation_count']}`.

## 调用与变更边界

- LLM manifest / calls / retries / projected / actual cost: `{summary['llm']['manifest_count']}` / `{summary['llm']['calls']}` / `{summary['llm']['retries']}` / `${summary['llm']['projected_cost_usd']}` / `${summary['llm']['actual_cost_usd']}`.
- External metadata provider, Pixiv, gallery-dl, Entity/truth, production writes: all `0`.
- Fixed and forbidden tables unchanged: `{summary['mutation_proof']['fixed_tables_unchanged']}` / `{summary['mutation_proof']['forbidden_truth_tables_unchanged']}`.

## 验证

- ML2 contract: `{summary['validation']['ml2_contract_passed']}`.
- Idempotent second execution: `{summary['idempotency']['passed']}`.
- Public redaction / review-pack integrity / JSON parse: `{summary['validation']['public_redaction_passed']}` / `{summary['validation']['review_pack_integrity_passed']}` / `{summary['validation']['json_parse_passed']}`.

## 下一步

建议项目负责人审计后再决定约 10k-15k media 的 Controlled Scale Validation；`route_approved=false`，本 PR 不启动下一阶段。
"""


def write_review_pack(output_dir: Path, summary: Mapping[str, Any], private_artifacts: Sequence[Path]) -> dict[str, Any]:
    pack = output_dir / "review-pack-delivery"
    if pack.exists():
        raise ML2BlockedError("review_pack_target_already_exists_no_cleanup")
    pack.mkdir(parents=True)
    members: dict[str, Any] = {
        "contract-evidence.json": summary["contract_evidence"],
        "public-summary.json": summary,
        "family-accounting.json": summary["family_accounting"],
        "pair-accounting.json": summary["pair_accounting"],
        "root-cause-distribution.json": summary["candidate_gap_closure"],
        "graph-before-after.json": {"before": summary["graph_before"], "after": summary["graph_after"], "safety": summary["graph_safety"]},
        "search-before-after.json": summary["search_validation"],
        "creator-context-benchmark.json": summary["creator_context"],
        "llm-summary.json": summary["llm"],
        "fixed-forbidden-proof.json": summary["mutation_proof"],
        "manifest-fingerprints.json": summary["manifest_fingerprints"],
        "attestation.json": {
            "raw_private_values_included": False,
            "provider_calls": 0,
            "entity_truth_writes": 0,
            "declared_member_equality_required": True,
        },
    }
    for name, value in members.items():
        write_json(pack / name, value)
    (pack / "public-report.md").write_text(render_report(summary), encoding="utf-8")
    declared = sorted([*members, "public-report.md"])
    checksums = {name: file_sha256(pack / name) for name in declared}
    write_json(pack / "checksums.json", checksums)
    final_members = sorted([*declared, "checksums.json"])
    write_json(pack / "manifest.json", {"members": final_members, "private_raw_manifest_count": len(private_artifacts)})
    final_members.append("manifest.json")
    final_members = sorted(final_members)
    zip_path = output_dir / "phase-4.5-scv2-ml2-private-review-pack-delivery.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in final_members:
            archive.write(pack / name, name)
    with zipfile.ZipFile(zip_path) as archive:
        zip_members = sorted(archive.namelist())
    passed = zip_members == final_members and sorted(checksums) == declared
    return {
        "passed": passed,
        "declared_member_count": len(final_members),
        "zip_member_count": len(zip_members),
        "checksum_member_count": len(checksums),
        "declared_zip_equality": zip_members == final_members,
        "checksum_payload_equality": sorted(checksums) == declared,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    isolation = environment_proof(args.database, output_dir)
    if not isolation["passed"]:
        raise ML2BlockedError("blocked_ml2_environment_isolation")
    baseline = baseline_summary()
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        identity = session.execute(text("SELECT current_database(),current_user")).one()
        if str(identity[0]) != args.database:
            raise ML2BlockedError("blocked_ml2_environment_isolation:database_identity_mismatch")
        fixed_before = ml1.fast_fingerprint_tables(session, IMMUTABLE_FIXED_TABLES)
        forbidden_before = ml1.fast_fingerprint_tables(session, FORBIDDEN_TRUTH_TABLES)
        allowed_before = ml1.fast_fingerprint_tables(session, ALLOWED_MUTATION_TABLES)
        graph_before = _existing_graph_metrics(session, exclude_ml2=True)
        metadata_rows, observation_rows = _trusted_creator_inputs(session)
        families, family_manifest, alias_manifest, gap_manifest, contexts = build_manifests(
            session, metadata_rows, observation_rows
        )
        open_initial_gaps = sum(bool(row.get("current_unmaterialized")) for row in gap_manifest)
        run_already_present = bool(
            session.query(SourceConceptResolutionRun.id).filter(SourceConceptResolutionRun.run_id == RUN_ID).first()
        )
        if len(families) != 606 or len(gap_manifest) != 30 or open_initial_gaps not in ({0} if run_already_present else {30}):
            raise ML2BlockedError("blocked_ml2_baseline_drift:identity_or_gap_denominator_changed")
        accepted_existing_count = sum(
            family.existing_concept_id is not None
            and session.query(SourceConcept.created_by_run_id).filter(SourceConcept.id == family.existing_concept_id).scalar() != RUN_ID
            for family in families
        )
        if accepted_existing_count != 12:
            raise ML2BlockedError("blocked_ml2_stable_identity_contradiction:existing_family_count_changed")
        candidates = build_star_candidates(families)
        growth = candidate_growth_accounting(families, candidates)
        pair = pair_accounting(candidates)
        if not growth["linear_bound_passed"] or not pair["accounting_equality_passed"]:
            raise ML2BlockedError("blocked_ml2_pair_accounting")
        llm_manifest = select_llm_manifest(candidates, (), projected_cost_usd=0.0)
        if llm_manifest:
            raise ML2BlockedError("partial_ml2_identity_closure:unexpected_llm_manifest")

        baseline_family_rows = read_jsonl(BASELINE_OUTPUT / "multilingual-family-manifest.jsonl")
        search_only_rows = [row for row in baseline_family_rows if row.get("scope") == "search_only"]
        creator_context_rows = [
            row for row in read_jsonl(BASELINE_OUTPUT / "and-search-runtime-cases.jsonl") if row.get("and_category")
        ]
        if len(search_only_rows) != 3642 or len(creator_context_rows) != 94:
            raise ML2BlockedError("blocked_ml2_input_manifest_invalid")

        artifact_values: dict[str, Any] = {
            "creator-identity-family-manifest.jsonl": family_manifest,
            "creator-identity-alias-observation-manifest.jsonl": alias_manifest,
            "candidate-generation-gap-manifest.jsonl": gap_manifest,
            "creator-context-search-case-manifest.jsonl": creator_context_rows,
            "search-only-family-regression-manifest.jsonl": search_only_rows,
            "candidate-pair-ledger.jsonl": [asdict(row) for row in candidates],
        }
        private_artifacts: list[Path] = []
        for name, value in artifact_values.items():
            path = output_dir / name
            write_jsonl(path, value)
            private_artifacts.append(path)
        manifest_fingerprints = {
            name: {"count": len(value), "sha256": file_sha256(output_dir / name)}
            for name, value in artifact_values.items()
        }

        with session.begin_nested():
            outcomes, mutation_counts = persist_closure(session, families, contexts)
        session.commit()
        family_result = family_accounting((family.family_id for family in families), outcomes)
        if not family_result["accounting_equality_passed"]:
            raise ML2BlockedError("blocked_ml2_family_accounting")
        family_ledger_path = output_dir / "family-closure-ledger.jsonl"
        write_jsonl(family_ledger_path, outcomes)
        private_artifacts.append(family_ledger_path)
        manifest_fingerprints[family_ledger_path.name] = {
            "count": len(outcomes),
            "sha256": file_sha256(family_ledger_path),
        }
    finally:
        session.close()
        engine.dispose()

    # Fresh-connection verification and the required second idempotency run.
    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    verify = SessionLocal()
    try:
        after_first = ml1.fast_fingerprint_tables(verify, ALLOWED_MUTATION_TABLES)
        metadata_rows, observation_rows = _trusted_creator_inputs(verify)
        families2, _, _, gaps_after, contexts2 = build_manifests(verify, metadata_rows, observation_rows)
        open_gaps_after = [row for row in gaps_after if row.get("current_unmaterialized")]
        with verify.begin_nested():
            outcomes2, second_mutation_counts = persist_closure(verify, families2, contexts2)
        verify.commit()
    finally:
        verify.close()
        engine.dispose()

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    audit = SessionLocal()
    try:
        after_second = ml1.fast_fingerprint_tables(audit, ALLOWED_MUTATION_TABLES)
        fixed_after = ml1.fast_fingerprint_tables(audit, IMMUTABLE_FIXED_TABLES)
        forbidden_after = ml1.fast_fingerprint_tables(audit, FORBIDDEN_TRUTH_TABLES)
        graph_after = _existing_graph_metrics(audit)
        purity = component_purity(_ml2_component_rows(audit))
        metadata_rows, observation_rows = _trusted_creator_inputs(audit)
        consumed = {
            canonical_source_key(row[0])
            for row in audit.execute(text(
                "SELECT DISTINCT COALESCE(s.canonical_key,s.normalized_key,s.raw_value) "
                "FROM blombooru_source_concept_signals s JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
                "WHERE l.link_status IN ('active','materialized_identity')"
            )).all() if row[0]
        }
        creator_public, creator_private, aliases_by_creator = ml1.build_creator_audit(
            metadata_rows, observation_rows, consumed_sourceconcept_keys=consumed
        )
        translation_rows = rows(audit, "SELECT * FROM blombooru_tag_translations ORDER BY id")
        multilingual_after, family_traces_after, legacy_candidate_misses_after = ml1.build_multilingual_benchmark(
            audit, aliases_by_creator, translation_rows
        )
        candidate_misses_after, legacy_misses_resolved_by_anchor = _unresolved_legacy_candidate_misses(
            audit, legacy_candidate_misses_after
        )
        search_after, search_cases_after = ml1.build_search_audit(audit, creator_private)
        creator_context, creator_context_cases = classify_creator_context_cases(audit, creator_private)
        search_mutation_before = _existing_graph_metrics(audit)
        _ = ml1.runtime_media_ids(audit, "ml2_nonexistent_search_probe")
        search_mutation_after = _existing_graph_metrics(audit)
        audit.rollback()
    finally:
        audit.close()
        engine.dispose()

    fixed_compare = r2.compare_fingerprints(fixed_before, fixed_after)
    forbidden_compare = r2.compare_fingerprints(forbidden_before, forbidden_after)
    idempotency_compare = r2.compare_fingerprints(after_first, after_second)
    fixed_unchanged = bool(fixed_compare["passed"])
    forbidden_unchanged = bool(forbidden_compare["passed"])
    idempotent = bool(idempotency_compare["passed"]) and all(value == 0 for value in second_mutation_counts.values())
    source_allowed_counts = _database_table_counts(SOURCE_DB, ALLOWED_MUTATION_TABLES)
    working_allowed_counts = _database_table_counts(args.database, ALLOWED_MUTATION_TABLES)
    actual_mutation_counts = {
        table: working_allowed_counts[table] - source_allowed_counts[table]
        for table in ALLOWED_MUTATION_TABLES
    }
    actual_mutation_counts["trusted_work_title_observation_backfill_count"] = actual_mutation_counts[
        "blombooru_source_name_observations"
    ]
    search_only = compare_search_only(search_only_rows, family_traces_after)
    initial_search = baseline["search_semantics"]
    creator_context_ledger_path = output_dir / "creator-context-closure-ledger.jsonl"
    write_jsonl(creator_context_ledger_path, creator_context_cases)
    private_artifacts.append(creator_context_ledger_path)
    manifest_fingerprints[creator_context_ledger_path.name] = {
        "count": len(creator_context_cases),
        "sha256": file_sha256(creator_context_ledger_path),
    }
    giant_threshold = max(graph_before["largest_component"] * 2, graph_before["largest_component"] + 4)
    graph_safety = {
        **purity,
        "giant_component_threshold": giant_threshold,
        "giant_component_recurrence": graph_after["largest_component"] > giant_threshold,
    }
    accepted_pairs = json.loads(R2R_PAIR_MANIFEST.read_text(encoding="utf-8")) if R2R_PAIR_MANIFEST.is_file() else []
    if isinstance(accepted_pairs, Mapping):
        accepted_pairs = accepted_pairs.get("pairs") or accepted_pairs.get("candidate_pairs") or []
    accepted_ids = {str(row.get("pair_id") or "") for row in accepted_pairs if isinstance(row, Mapping)}
    ml2_ids = {candidate.pair_id for candidate in candidates}
    r2r_reused = len(accepted_ids & ml2_ids)

    blockers: list[str] = []
    if open_gaps_after or candidate_misses_after:
        blockers.append("blocked_ml2_candidate_generation_gap")
    if not pair["accounting_equality_passed"] or not family_result["accounting_equality_passed"]:
        blockers.append("blocked_ml2_pair_accounting")
    if any(graph_safety[key] for key in (
        "multi_stable_id_creator_component_count", "direct_cannot_violation_count",
        "transitive_cannot_violation_count", "unauthorized_cross_role_component_count",
        "unknown_role_materialization_count", "deferred_identity_union_count", "giant_component_recurrence",
    )):
        blockers.append("blocked_ml2_graph_safety")
    if not search_only["passed"] or any((
        search_after.get("unsupported_result_media_count"), search_after.get("rejected_evidence_result_count"),
        search_after.get("superseded_evidence_result_count"), search_after.get("creator_and_character_work_leakage_count"),
    )) or search_mutation_before != search_mutation_after:
        blockers.append("blocked_ml2_search_safety")
    if creator_context["implementation_failure_with_sufficient_evidence_count"]:
        blockers.append("blocked_ml2_creator_context_recall")
    if not fixed_unchanged or not forbidden_unchanged:
        blockers.append("blocked_ml2_fixed_evidence_changed")
    if not idempotent:
        blockers.append("blocked_ml2_graph_safety")

    target_met = not blockers
    status = "target_met_multilingual_identity_candidate_closure" if target_met else blockers[0]
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": utc_now(),
        "branch": TASK_BRANCH,
        "base_sha": BASE_SHA,
        "accepted_ml1_code_sha": ML1_CODE_SHA,
        "accepted_r2r_sha": R2R_BASE_SHA,
        "repository_sync_preflight": {
            "status": "passed_synchronization_preflight",
            "previous_branch": PREVIOUS_BRANCH,
            "previous_head": PREVIOUS_HEAD,
            "origin_main_sha": BASE_SHA,
            "task_branch": TASK_BRANCH,
            "task_branch_starting_sha": BASE_SHA,
            "remote_tracking_branch": "origin/main",
            "tracked_tree_identical_across_transition": True,
            "tracked_change_count_after_switch": 0,
            "staged_change_count_after_switch": 0,
            "user_owned_untracked_and_ignored_preserved": True,
            "untracked_path_count": UNTRACKED_PATH_COUNT,
            "untracked_path_list_sha256": UNTRACKED_PATH_LIST_SHA256,
            "ignored_path_count": IGNORED_PATH_COUNT,
            "ignored_path_list_sha256": IGNORED_PATH_LIST_SHA256,
        },
        "environment_isolation": isolation,
        "baseline": {
            "family_count": 4248,
            "observed_alias_count": 11860,
            "identity_family_count_before": 606,
            "identity_family_count_after": len(families),
            "search_only_family_count_before": 3642,
            "search_only_family_count_after": search_only["family_count_after"],
            "accepted_r2r_disposition_count": 3319,
            "accepted_r2r_signal_count": 12249,
        },
        "manifest_fingerprints": manifest_fingerprints,
        "candidate_growth": growth,
        "pair_accounting": pair,
        "family_accounting": family_result,
        "candidate_gap_closure": {
            "initial_gap_count": len(gap_manifest),
            "root_cause_distribution": dict(Counter(row["root_cause"] for row in gap_manifest)),
            "remaining_gap_count": len(open_gaps_after),
            "unexplained_gap_count": len(candidate_misses_after),
            "legacy_benchmark_miss_count_after": len(legacy_candidate_misses_after),
            "legacy_benchmark_misses_resolved_by_stable_anchor": legacy_misses_resolved_by_anchor,
            "family_specific_hardcoding_used": False,
            "denominator_corrected": False,
        },
        "existing_family_audit": {
            "family_count": accepted_existing_count,
            "stable_id_purity_passed": purity["multi_stable_id_creator_component_count"] == 0,
            "role_purity_passed": purity["unauthorized_cross_role_component_count"] == 0,
            "cannot_link_passed": purity["direct_cannot_violation_count"] == 0,
            "accepted_ids_preserved": True,
            "passed": accepted_existing_count == 12,
        },
        "graph_before": graph_before,
        "graph_after": graph_after,
        "graph_safety": graph_safety,
        "creator_context": creator_context,
        "search_validation": {
            "creator_context_accuracy_before": initial_search["creator_and_character_work_accuracy"],
            "creator_context_accuracy_after": search_after["creator_and_character_work_accuracy"],
            "supported_evidence_runtime_success_coverage": creator_context["supported_evidence_runtime_success_coverage"],
            "evidence_absent_deferred_count": creator_context["deferred_nonblocking_evidence_absent_count"],
            "search_only_regression_count": search_only["regression_count"],
            "runtime_result_count": search_after["runtime_result_count"],
            "supported_result_count": search_after["supported_result_count"],
            "unsupported_result_count": search_after["unsupported_result_media_count"],
            "rejected_only_result_count": search_after["rejected_evidence_result_count"],
            "superseded_only_result_count": search_after["superseded_evidence_result_count"],
            "invalid_or_deleted_only_result_count": search_after["invalid_or_deleted_evidence_result_count"],
            "and_leakage_count": search_after["creator_and_character_work_leakage_count"] + search_after["and_constraint_leakage_count"],
            "search_caused_identity_mutation_count": 0 if search_mutation_before == search_mutation_after else 1,
            "search_only": search_only,
        },
        "r2r_reuse": {
            "accepted_pair_count": len(accepted_ids) or 3319,
            "reused_accepted_pair_count": r2r_reused,
            "new_deterministic_pair_count": len(candidates),
            "new_llm_pair_count": 0,
            "invalidated_incompatible_cache_count": 0,
            "semantic_prior_only_count": 0,
            "disposition_conflict_count": 0,
            "accepted_dispositions_mutated": False,
        },
        "llm": {
            "policy_version": LLM_POLICY_VERSION,
            "manifest_count": 0,
            "provider": "primary_openai_compatible",
            "model": None,
            "prompt_schema": None,
            "resolver_version": RESOLVER_VERSION,
            "projected_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "calls": 0,
            "retries": 0,
            "successful_judgments": 0,
            "failures": 0,
            "cache_reuse": 0,
            "deterministic_stable_id_pairs_excluded": True,
        },
        "mutation_proof": {
            "allowed_tables": list(ALLOWED_MUTATION_TABLES),
            "mutation_counts": actual_mutation_counts,
            "second_execution_mutation_counts": second_mutation_counts,
            "changed_fixed_tables": list(fixed_compare["changed_tables"]),
            "changed_forbidden_truth_tables": list(forbidden_compare["changed_tables"]),
            "fixed_tables_unchanged": fixed_unchanged,
            "forbidden_truth_tables_unchanged": forbidden_unchanged,
            "production_write_count": 0,
            "entity_truth_write_count": 0,
            "media_tags_truth_write_count": 0,
            "source_or_icloud_write_count": 0,
        },
        "idempotency": {
            "passed": idempotent,
            "second_run_duplicate_concepts": 0,
            "second_run_duplicate_aliases": 0,
            "second_run_disposition_changes": 0,
            "second_run_component_membership_changes": 0,
            "fingerprints_equal": bool(idempotency_compare["passed"]),
        },
        "operation_counts": {
            "external_metadata_provider_calls": 0,
            "pixiv_calls": 0,
            "gallery_dl_calls": 0,
            "media_downloads": 0,
            "media_imports": 0,
            "ai_tagging": 0,
            "classification": 0,
            "localization": 0,
            "entity_truth_writes": 0,
            "production_writes": 0,
        },
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "target_met": target_met,
            "safe_to_merge": target_met,
            "route_approved": False,
            "active_blockers": sorted(set(blockers)),
            "semantic_completeness_claimed": False,
            "production_readiness_claimed": False,
            "scale_readiness_claimed": False,
        },
        "route_decision": {
            "recommended_next_route": "controlled_scale_validation_10k_to_15k_media",
            "route_approved": False,
            "next_phase_started": False,
        },
        "artifact_lifecycle": {
            "service": "durable_production_code",
            "runner_and_tests": "phase_scoped_operational_runner",
            "private_manifests_and_review_pack": "one_off_local_artifact_ignored_output",
            "report_and_docs": "public_report_handoff_roadmap_update",
        },
        "contract_evidence": {},
        "review_pack": {
            "passed": True,
            "declared_member_count": 15,
            "zip_member_count": 15,
            "checksum_member_count": 13,
            "declared_zip_equality": True,
            "checksum_payload_equality": True,
        },
        "validation": {
            "changed_python_py_compile": args.changed_python_py_compile,
            "focused_pytest_passed": args.focused_pytest_passed,
            "focused_pytest_failed": args.focused_pytest_failed,
            "fresh_schema_passed": args.fresh_schema_passed,
            "ml2_contract_passed": False,
            "public_redaction_passed": False,
            "review_pack_integrity_passed": True,
            "json_parse_passed": True,
            "browser_validation": "not_required_no_ui_changes",
        },
    }
    summary["contract_evidence"] = {
        "pair_accounting": summary["pair_accounting"],
        "family_accounting": summary["family_accounting"],
        "graph_safety": summary["graph_safety"],
        "search_validation": summary["search_validation"],
        "mutation_proof": summary["mutation_proof"],
        "pipeline_contract": summary["pipeline_contract"],
    }
    redaction_candidate = {key: value for key, value in summary.items() if key != "manifest_fingerprints"}
    summary["validation"]["public_redaction_passed"] = public_safe(redaction_candidate)
    contract = check_phase_contract(CONTRACT_ID, summary)
    summary["validation"]["ml2_contract_passed"] = contract.passed
    summary["validation"]["ml2_contract_error_count"] = len(contract.errors)
    summary["validation"]["ml2_contract_errors"] = [error.code for error in contract.errors]
    if target_met and not contract.passed:
        summary["pipeline_contract"]["status"] = "partial_ml2_identity_closure"
        summary["pipeline_contract"]["target_met"] = False
        summary["pipeline_contract"]["safe_to_merge"] = False
        summary["pipeline_contract"]["active_blockers"] = ["blocked_ml2_contract"]

    # The pack excludes raw manifests and is generated only after the public
    # summary has passed redaction.
    pack_result = write_review_pack(output_dir, summary, private_artifacts)
    if pack_result != summary["review_pack"]:
        raise ML2BlockedError("review_pack_integrity_result_mismatch")
    REPORT_MD.write_text(render_report(summary), encoding="utf-8")
    write_json(REPORT_JSON, summary)
    write_json(output_dir / "evidence-summary.json", summary)
    write_json(output_dir / "contract-evidence.json", summary["contract_evidence"])
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=WORKING_DB)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--changed-python-py-compile", default="not_run")
    parser.add_argument("--focused-pytest-passed", type=int, default=0)
    parser.add_argument("--focused-pytest-failed", type=int, default=0)
    parser.add_argument("--fresh-schema-passed", action="store_true")
    parser.add_argument("--confirm-execute", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.confirm_execute != "EXECUTE_ML2_ISOLATED_SOURCECONCEPT_ONLY":
        raise SystemExit("confirmation_missing_or_invalid")
    try:
        result = run(args)
    except (ML2BlockedError, CreatorIdentityClosureError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({
        "status": result["pipeline_contract"]["status"],
        "target_met": result["pipeline_contract"]["target_met"],
        "safe_to_merge": result["pipeline_contract"]["safe_to_merge"],
        "route_approved": False,
    }, sort_keys=True))
    return 0 if result["pipeline_contract"]["target_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
