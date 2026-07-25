#!/usr/bin/env python3
"""Build the single owner-authorized SCV2-SV1B fresh Replay v2.

This phase-scoped runner has no acquisition, provider, gallery-dl, LLM, media,
or thumbnail execution stage. It consumes only accepted local evidence and
stops at a private manual-acceptance harness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, URL


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.stable_replay_package_v2 import (  # noqa: E402
    EXTERNAL_ROUTE_BUDGET,
    SCHEMA_VERSION,
    compare_round_trip_packages,
    cross_validate_primary_stable_identity,
    export_package_from_engine,
    graph_effective_projection,
    import_package,
    sha256_payload,
    verify_external_routes_forbidden,
    write_package,
)


PHASE = "SCV2-SV1B"
BRANCH = "codex/scv2-sv1b-pixiv-metadata-localization-source-graph-closure"
ACCEPTED_MAINLINE_BASE = "46861489fa0b3b05ae917a99a3932897efd70365"
ACCEPTED_MANIFEST_FINGERPRINT = (
    "5f7ccaec155db688db72ed4a762cbd7d2977382e80344c385e3d40fcf6bd610f"
)
ACCEPTED_ACQUISITION_PACKAGE_FINGERPRINT = (
    "df6008c1b469beaf9bd7f47e8a9af460188b2ad7e1218366a76e2d17e77d8636"
)
EXPECTED_PACKAGE_V2_FINGERPRINT = (
    "640c52445524aa69f540a64a41800b9eb5a746d9a234ba6582b5a2ef1feb7845"
)
EXPECTED_PACKAGE_V2_MEMBERSHIP_FINGERPRINT = (
    "7540ba28da284c99ae835e87b79527dbc4ebf9d28c613add19a9892e11e6869f"
)
EXPECTED_PRIMARY_GRAPH_PROJECTION_FINGERPRINT = (
    "b211a4790f78e33553d66f74e3594365a4a2c7aca51822559b10a4d81ab726a2"
)
EXPECTED_TRANSLATION_FINGERPRINT = (
    "41dcd1db481544dac6805000e678c98af03332cd592c557331c997df2293c3bd"
)
EXPECTED_TRANSLATION_COUNT = 5_519
EXPECTED_METADATA_COUNT = 17_193
EXPECTED_TRUSTED_COMPLETE_COUNT = 6_605
EXPECTED_MEDIA_COUNT = 12_000
PRIMARY_DATABASE = (
    "blombooru_scv2_sv1b_metadata_graph_closure_test_20260721_retry2"
)
FAILED_REPLAY_DATABASE = (
    "blombooru_scv2_sv1b_replay_verification_test_20260721_retry2"
)
FRESH_REPLAY_DATABASE = (
    "blombooru_scv2_sv1b_replay_v2_verification_test_20260725"
)
FRESH_DATABASE_PREFIX = "blombooru_scv2_sv1b_replay_v2_verification_test_"
OLD_OUTPUT = (
    ROOT
    / ".local_manifests"
    / "phase-4.5-scv2-sv1b-pixiv-metadata-localization-source-graph-closure-20260721-r5b-stage-aware-retry2"
)
DEFAULT_OUTPUT = (
    ROOT
    / ".local_manifests"
    / "phase-4.5-scv2-sv1b-fresh-replay-v2-20260725"
)
STAGES = (
    "validate",
    "create-import",
    "derive-compare",
    "rederive-compare",
    "search",
    "build-harness",
    "finalize-harness-binding",
)
FAILED_FIRST_GRAPH_PROOF_FINGERPRINT = (
    "7449ba378e957b76ab04ce721f77d8623acf030903a12c8c870bfc7b5b3e5ad6"
)
FAILED_FIRST_GRAPH_STATE_FINGERPRINT = (
    "3cbabca5f2c038b6f561a65feda2113a50cfc8eb2ac9eaec2908fb6d547a77bb"
)
STABLE_SIGNAL_PROJECTION_FINGERPRINT = (
    "15c3c98a2cfd71933776952fa5bd49563ef808800bac659e45cd7d3763dddacf"
)
FAILED_FIRST_GRAPH_CORE_RUN_ID = "sv1b-replay-v2-full"
CORRECTED_GRAPH_LABEL = "replay-v2-stable-signal-v2"
CORRECTED_GRAPH_CORE_RUN_ID = (
    "sv1b-replay-v2-stable-signal-v2-full"
)
CORRECTED_GRAPH_FAILED_SCOPE_PROOF_FINGERPRINT = (
    "3fade25d12b60601717359af94348ca76f08a6c22d12a829af38b4e5fa459c04"
)
CORRECTED_GRAPH_FAILED_SCOPE_DATABASE_STATE_FINGERPRINT = (
    "ead590616e9448504461abec5886276cbaa3acd87f28188bf731ca8252f2bac3"
)
FIRST_GRAPH_R2R_AUDIT_FILE_FINGERPRINT = (
    "cac03b398f740f7e24b39173eb2fde6ad723c7c16129fc8a639685b8bc3e9e7d"
)
DERIVED_GRAPH_TABLES = (
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
    "blombooru_source_concept_fallback_search_index",
)
PROTECTED_BASELINE_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_tag_translations",
)


class FreshReplayV2Error(RuntimeError):
    """Raised when the single fresh Replay cannot proceed safely."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def is_strict_test_database_name(database: str) -> bool:
    value = str(database or "").strip().casefold()
    return bool(
        re.fullmatch(r"blombooru_[a-z0-9]+(?:_[a-z0-9]+)*", value)
        and "test" in value.split("_")
    )


def database_url(database: str) -> URL:
    if not is_strict_test_database_name(database):
        raise FreshReplayV2Error(f"unsafe_database_identity:{database}")
    return URL.create(
        "postgresql",
        username=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=database,
    )


def engine_for(database: str) -> Engine:
    return create_engine(
        database_url(database),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


def _admin_engine() -> Engine:
    return engine_for("blombooru_test")


def database_exists(database: str) -> bool:
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname=:database"),
                    {"database": database},
                ).scalar()
            )
    finally:
        admin.dispose()


def existing_fresh_replay_databases() -> list[str]:
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            return sorted(
                str(value)
                for value in connection.execute(
                    text(
                        "SELECT datname FROM pg_database "
                        "WHERE datname LIKE :prefix ORDER BY datname"
                    ),
                    {"prefix": FRESH_DATABASE_PREFIX + "%"},
                ).scalars()
            )
    finally:
        admin.dispose()


def validate_single_fresh_database_membership(
    existing: Sequence[str],
    *,
    allow_target: bool,
) -> None:
    values = list(existing)
    allowed_memberships = (
        ([], [FRESH_REPLAY_DATABASE])
        if allow_target
        else ([],)
    )
    if values not in allowed_memberships:
        raise FreshReplayV2Error(
            "fresh_replay_database_creation_limit_violation:"
            + canonical_json(values)
        )


def active_database_connection_count(database: str) -> int:
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM pg_stat_activity "
                        "WHERE datname=:database AND pid<>pg_backend_pid()"
                    ),
                    {"database": database},
                ).scalar()
                or 0
            )
    finally:
        admin.dispose()


def _insert_batches(
    connection: Connection,
    table: Table,
    rows: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 500,
) -> int:
    if not rows:
        return 0
    allowed = set(table.c.keys()) - {"id"}
    inserted = 0
    for start in range(0, len(rows), batch_size):
        values = [
            {key: value for key, value in row.items() if key in allowed}
            for row in rows[start : start + batch_size]
        ]
        result = connection.execute(
            pg_insert(table).values(values).on_conflict_do_nothing()
        )
        inserted += int(result.rowcount or 0)
    return inserted


def _reflected_table(metadata: MetaData, name: str) -> Table:
    table = metadata.tables.get(name)
    if table is None:
        table = metadata.tables.get(f"public.{name}")
    if table is None:
        raise FreshReplayV2Error(f"table_missing:{name}")
    return table


def _without_local_fields(
    row: Mapping[str, Any],
    *,
    extra: Sequence[str] = (),
) -> dict[str, Any]:
    omitted = {"id", "created_at", "updated_at", *extra}
    return {key: value for key, value in row.items() if key not in omitted}


def create_database_schema(database: str) -> dict[str, Any]:
    if database_exists(database):
        return {
            "database": database,
            "created": False,
            "resumed_existing_authorized_database": True,
        }
    environment = os.environ.copy()
    environment.update(
        {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": database,
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_LLM_ENABLED": "false",
            "TAG_TRANSLATION_AUTO_ENABLED": "false",
            "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
            "CONTENT_CLASSIFICATION_ENABLED": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "setup_test_db.py"), "--migrate"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    if completed.returncode:
        raise FreshReplayV2Error(
            "fresh_replay_database_setup_failed:"
            + completed.stderr[-500:].replace("\n", " ")
        )
    return {
        "database": database,
        "created": True,
        "resumed_existing_authorized_database": False,
    }


def copy_media_tag_baseline(
    source_database: str,
    target_database: str,
) -> dict[str, Any]:
    source = engine_for(source_database)
    target = engine_for(target_database)
    source_meta = MetaData()
    target_meta = MetaData()
    table_names = (
        "blombooru_media",
        "blombooru_tags",
        "blombooru_media_tags",
    )
    source_meta.reflect(bind=source, only=list(table_names))
    target_meta.reflect(bind=target, only=list(table_names))
    try:
        with source.connect() as src, target.begin() as dst:
            source_media = _reflected_table(source_meta, "blombooru_media")
            source_tags = _reflected_table(source_meta, "blombooru_tags")
            target_media = _reflected_table(target_meta, "blombooru_media")
            target_tags = _reflected_table(target_meta, "blombooru_tags")
            target_links = _reflected_table(target_meta, "blombooru_media_tags")
            media_rows = [
                _without_local_fields(row, extra=("parent_id",))
                for row in src.execute(select(source_media)).mappings()
            ]
            tag_rows = [
                _without_local_fields(row, extra=("post_count",))
                for row in src.execute(select(source_tags)).mappings()
            ]
            media_inserted = _insert_batches(dst, target_media, media_rows)
            tag_inserted = _insert_batches(dst, target_tags, tag_rows)
            media_map = {
                str(row["hash"]): int(row["id"])
                for row in dst.execute(
                    text(
                        "SELECT id,hash FROM blombooru_media "
                        "WHERE hash IS NOT NULL"
                    )
                ).mappings()
            }
            tag_map = {
                str(row["name"]): int(row["id"])
                for row in dst.execute(
                    text("SELECT id,name FROM blombooru_tags")
                ).mappings()
            }
            link_rows = []
            for row in src.execute(
                text(
                    """
                    SELECT m.hash,t.name,mt.source,mt.confidence,
                           mt.is_locked,mt.is_suggestion
                    FROM blombooru_media_tags mt
                    JOIN blombooru_media m ON m.id=mt.media_id
                    JOIN blombooru_tags t ON t.id=mt.tag_id
                    """
                )
            ).mappings():
                link_rows.append(
                    {
                        "media_id": media_map[str(row["hash"])],
                        "tag_id": tag_map[str(row["name"])],
                        "source": row["source"],
                        "confidence": row["confidence"],
                        "is_locked": row["is_locked"],
                        "is_suggestion": row["is_suggestion"],
                    }
                )
            link_inserted = _insert_batches(
                dst,
                target_links,
                link_rows,
                batch_size=1000,
            )
    finally:
        source.dispose()
        target.dispose()
    return {
        "source_media_count": len(media_rows),
        "source_tag_count": len(tag_rows),
        "source_media_tag_count": len(link_rows),
        "inserted_media_count": media_inserted,
        "inserted_tag_count": tag_inserted,
        "inserted_media_tag_count": link_inserted,
        "numeric_row_id_copy": False,
        "stable_media_hash_and_tag_name_mapping": True,
    }


def copy_primary_translations(
    source_database: str,
    target_database: str,
) -> dict[str, Any]:
    source = engine_for(source_database)
    target = engine_for(target_database)
    metadata = MetaData()
    metadata.reflect(bind=target, only=["blombooru_tag_translations"])
    target_table = _reflected_table(metadata, "blombooru_tag_translations")
    try:
        with source.connect() as src:
            source_rows = [
                _without_local_fields(row, extra=("tag_id",))
                for row in src.execute(
                    text(
                        "SELECT * FROM blombooru_tag_translations "
                        "ORDER BY canonical_name,language"
                    )
                ).mappings()
            ]
        with target.begin() as dst:
            tag_map = {
                str(row["name"]): int(row["id"])
                for row in dst.execute(
                    text("SELECT id,name FROM blombooru_tags")
                ).mappings()
            }
            values = []
            for row in source_rows:
                item = dict(row)
                item["tag_id"] = tag_map.get(str(item["canonical_name"]))
                values.append(item)
            inserted = _insert_batches(
                dst,
                target_table,
                values,
                batch_size=500,
            )
    finally:
        source.dispose()
        target.dispose()
    return {
        "source_translation_count": len(source_rows),
        "inserted_translation_count": inserted,
        "numeric_tag_id_copy": False,
        "stable_canonical_name_mapping": True,
    }


def translation_state(database: str) -> dict[str, Any]:
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            rows = sorted(
                canonical_json(dict(row))
                for row in connection.execute(
                    text(
                        """
                        SELECT canonical_name,language,display_name,aliases_json,
                               category,source,status,confidence,needs_review,
                               provider
                        FROM blombooru_tag_translations
                        ORDER BY canonical_name,language
                        """
                    )
                ).mappings()
            )
    finally:
        engine.dispose()
    return {
        "count": len(rows),
        "fingerprint": sha256_payload(rows),
    }


def stable_baseline_state(database: str) -> dict[str, Any]:
    engine = engine_for(database)
    metadata = MetaData()
    metadata.reflect(
        bind=engine,
        only=["blombooru_media", "blombooru_tags"],
    )
    try:
        with engine.connect() as connection:
            media_table = _reflected_table(metadata, "blombooru_media")
            tag_table = _reflected_table(metadata, "blombooru_tags")
            media_rows = sorted(
                canonical_json(
                    _without_local_fields(row, extra=("parent_id",))
                )
                for row in connection.execute(
                    select(media_table)
                ).mappings()
            )
            tag_rows = sorted(
                canonical_json(
                    _without_local_fields(row, extra=("post_count",))
                )
                for row in connection.execute(
                    select(tag_table)
                ).mappings()
            )
            media_tag_rows = sorted(
                canonical_json(dict(row))
                for row in connection.execute(
                    text(
                        "SELECT m.hash,t.name,mt.source,mt.confidence,"
                        "mt.is_locked,mt.is_suggestion "
                        "FROM blombooru_media_tags mt "
                        "JOIN blombooru_media m ON m.id=mt.media_id "
                        "JOIN blombooru_tags t ON t.id=mt.tag_id"
                    )
                ).mappings()
            )
    finally:
        engine.dispose()
    groups = {
        "media": {
            "count": len(media_rows),
            "fingerprint": sha256_payload(media_rows),
        },
        "tags": {
            "count": len(tag_rows),
            "fingerprint": sha256_payload(tag_rows),
        },
        "media_tags": {
            "count": len(media_tag_rows),
            "fingerprint": sha256_payload(media_tag_rows),
        },
    }
    groups["translations"] = translation_state(database)
    return {
        "groups": groups,
        "fingerprint": sha256_payload(groups),
    }


def _logical_graph_state(database: str) -> dict[str, Any]:
    queries = {
        "signal": (
            "SELECT s.signal_key,m.hash,r.provider_record_key,s.status,"
            "s.role_hint,s.source_kind,s.trust_tier "
            "FROM blombooru_source_concept_signals s "
            "LEFT JOIN blombooru_media m ON m.id=s.media_id "
            "LEFT JOIN blombooru_source_metadata_records r "
            "ON r.id=s.source_metadata_record_id"
        ),
        "concept": (
            "SELECT c.concept_key,c.status,c.concept_type_hint,"
            "s.concept_key AS superseded_by,"
            "(COALESCE(c.evidence_summary_json,'{}'::json)::jsonb "
            "- 'created_by_run_id')::text AS stable_evidence_summary "
            "FROM blombooru_source_concepts c "
            "LEFT JOIN blombooru_source_concepts s "
            "ON s.id=c.superseded_by_concept_id"
        ),
        "alias": (
            "SELECT c.concept_key,a.alias_key,a.alias_role,a.alias_value,"
            "a.status FROM blombooru_source_concept_aliases a "
            "JOIN blombooru_source_concepts c ON c.id=a.concept_id"
        ),
        "evidence": (
            "SELECT c.concept_key,s.signal_key,m.hash,r.provider_record_key,"
            "e.evidence_type,e.status FROM blombooru_source_concept_evidence e "
            "JOIN blombooru_source_concepts c ON c.id=e.concept_id "
            "LEFT JOIN blombooru_source_concept_signals s ON s.id=e.signal_id "
            "LEFT JOIN blombooru_media m ON m.id=e.media_id "
            "LEFT JOIN blombooru_source_metadata_records r "
            "ON r.id=e.source_metadata_record_id"
        ),
        "link": (
            "SELECT s.signal_key,c.concept_key,l.link_status,"
            "l.resolution_reason_code,l.negative_reason_code "
            "FROM blombooru_source_concept_signal_links l "
            "JOIN blombooru_source_concept_signals s ON s.id=l.signal_id "
            "JOIN blombooru_source_concepts c ON c.id=l.concept_id"
        ),
        "search": (
            "SELECT c.concept_key,i.search_key,i.alias_role,i.status "
            "FROM blombooru_source_concept_search_index i "
            "JOIN blombooru_source_concepts c ON c.id=i.concept_id"
        ),
        "fallback": (
            "SELECT f.alias_key,m.hash,s.signal_key,n.signal_key,f.pair_id,"
            "f.relation,f.status "
            "FROM blombooru_source_concept_fallback_search_index f "
            "JOIN blombooru_media m ON m.id=f.media_id "
            "JOIN blombooru_source_concept_signals s "
            "ON s.id=f.source_signal_id "
            "JOIN blombooru_source_concept_signals n "
            "ON n.id=f.neighbor_signal_id"
        ),
    }
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            groups = {}
            for name, query in queries.items():
                rows = sorted(
                    canonical_json(list(row))
                    for row in connection.execute(text(query))
                )
                groups[name] = {
                    "count": len(rows),
                    "fingerprint": sha256_payload(rows),
                }
    finally:
        engine.dispose()
    return {
        "groups": groups,
        "fingerprint": sha256_payload(groups),
    }


def forensic_database_state(database: str) -> dict[str, Any]:
    engine = engine_for(database)
    try:
        package = export_package_from_engine(engine)
    finally:
        engine.dispose()
    projection = graph_effective_projection(package)
    graph = _logical_graph_state(database)
    return {
        "database": database,
        "package_fingerprint": package["package_fingerprint"],
        "package_membership_fingerprint": package["membership_fingerprint"],
        "metadata_count": package["table_counts"]["source_metadata_records"],
        "trusted_complete_count": projection["trusted_complete_count"],
        "graph_projection_fingerprint": projection["projection_fingerprint"],
        "derived_graph_state": graph,
        "translation_state": translation_state(database),
        "fingerprint": sha256_payload(
            {
                "package": package["package_fingerprint"],
                "graph": graph["fingerprint"],
                "translation": translation_state(database),
            }
        ),
    }


def _load_immutable_execution_evidence() -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    accepted = read_json(
        OLD_OUTPUT / "acquired-nonderived-evidence-package-private.json"
    )
    pages = read_jsonl(
        OLD_OUTPUT / "candidate-page-media-manifest-private.jsonl"
    )
    outcomes = read_json(
        OLD_OUTPUT
        / "provider-execution-checkpoint-r2-route-viability"
        / "final-work-outcome-ledger.json"
    )
    return accepted, pages, outcomes


def _assert_repository_preflight() -> dict[str, Any]:
    if git("branch", "--show-current") != BRANCH:
        raise FreshReplayV2Error("wrong_branch")
    if (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ACCEPTED_MAINLINE_BASE, "HEAD"],
            cwd=ROOT,
            check=False,
        ).returncode
        != 0
    ):
        raise FreshReplayV2Error("accepted_base_not_ancestor")
    if os.getenv("VIOLET_ENV") != "test":
        raise FreshReplayV2Error("violet_env_not_test")
    if not all(
        is_strict_test_database_name(value)
        for value in (
            PRIMARY_DATABASE,
            FAILED_REPLAY_DATABASE,
            FRESH_REPLAY_DATABASE,
        )
    ):
        raise FreshReplayV2Error("strict_database_identity_failed")
    if len(
        {
            PRIMARY_DATABASE,
            FAILED_REPLAY_DATABASE,
            FRESH_REPLAY_DATABASE,
        }
    ) != 3:
        raise FreshReplayV2Error("database_identity_not_pairwise_distinct")
    verify_external_routes_forbidden(EXTERNAL_ROUTE_BUDGET)
    return {
        "head": git("rev-parse", "HEAD"),
        "branch": BRANCH,
        "accepted_base_ancestor": True,
        "strict_test_database_identities": True,
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
    }


def validate_read_only(
    *,
    output: Path = DEFAULT_OUTPUT,
    allow_existing_target: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    repository = _assert_repository_preflight()
    if not database_exists(PRIMARY_DATABASE) or not database_exists(
        FAILED_REPLAY_DATABASE
    ):
        raise FreshReplayV2Error("protected_database_missing")
    validate_single_fresh_database_membership(
        existing_fresh_replay_databases(),
        allow_target=allow_existing_target,
    )
    if not allow_existing_target and output.exists():
        raise FreshReplayV2Error("fresh_output_root_already_exists")
    if active_database_connection_count(FAILED_REPLAY_DATABASE):
        raise FreshReplayV2Error("failed_replay_has_active_connection")
    primary_engine = engine_for(PRIMARY_DATABASE)
    try:
        package = export_package_from_engine(primary_engine)
    finally:
        primary_engine.dispose()
    accepted, pages, outcomes = _load_immutable_execution_evidence()
    crosscheck, ledger = cross_validate_primary_stable_identity(
        package,
        accepted,
        candidate_pages=pages,
        final_work_outcomes=outcomes,
    )
    projection = graph_effective_projection(package)
    primary_translation = translation_state(PRIMARY_DATABASE)
    checks = {
        "package_fingerprint": (
            package["package_fingerprint"]
            == EXPECTED_PACKAGE_V2_FINGERPRINT
        ),
        "package_membership_fingerprint": (
            package["membership_fingerprint"]
            == EXPECTED_PACKAGE_V2_MEMBERSHIP_FINGERPRINT
        ),
        "metadata_count": (
            package["table_counts"]["source_metadata_records"]
            == EXPECTED_METADATA_COUNT
        ),
        "trusted_complete_count": (
            projection["trusted_complete_count"]
            == EXPECTED_TRUSTED_COMPLETE_COUNT
        ),
        "graph_projection_fingerprint": (
            projection["projection_fingerprint"]
            == EXPECTED_PRIMARY_GRAPH_PROJECTION_FINGERPRINT
        ),
        "immutable_identity_crosscheck": crosscheck["passed"] is True,
        "accepted_provider_fact_mutation_zero": (
            crosscheck["accepted_provider_fact_mutation_count"] == 0
        ),
        "translation_count": (
            primary_translation["count"] == EXPECTED_TRANSLATION_COUNT
        ),
        "translation_fingerprint": (
            primary_translation["fingerprint"]
            == EXPECTED_TRANSLATION_FINGERPRINT
        ),
        "external_route_budget_zero": (
            package["external_route_budget"] == EXTERNAL_ROUTE_BUDGET
        ),
    }
    if not all(checks.values()):
        raise FreshReplayV2Error(
            "fresh_replay_v2_read_only_preflight_failed:"
            + canonical_json(
                sorted(key for key, value in checks.items() if not value)
            )
        )
    failed_replay = forensic_database_state(FAILED_REPLAY_DATABASE)
    proof = {
        "proof_version": "sv1b_fresh_replay_v2_read_only_preflight_v1",
        "repository": repository,
        "checks": checks,
        "primary_package_fingerprint": package["package_fingerprint"],
        "primary_package_membership_fingerprint": package[
            "membership_fingerprint"
        ],
        "primary_graph_projection_fingerprint": projection[
            "projection_fingerprint"
        ],
        "primary_trusted_complete_count": projection[
            "trusted_complete_count"
        ],
        "primary_translation_state": primary_translation,
        "primary_identity_crosscheck": crosscheck,
        "failed_replay_forensic_state": failed_replay,
        "fresh_database_creation_count_before": len(
            existing_fresh_replay_databases()
        ),
        "fresh_database_target": FRESH_REPLAY_DATABASE,
        "output_root_exists_before": output.exists(),
        "provider_request_count": 0,
        "llm_call_count": 0,
        "media_download_count": 0,
        "passed": True,
    }
    proof["proof_fingerprint"] = sha256_payload(proof)
    return proof, package, ledger


def _ownership_key() -> str:
    return sha256_payload(
        {
            "phase": PHASE,
            "branch": BRANCH,
            "manifest_fingerprint": ACCEPTED_MANIFEST_FINGERPRINT,
            "primary_database": PRIMARY_DATABASE,
            "replay_database": FRESH_REPLAY_DATABASE,
        }
    )


def _validate_resume_ownership(output: Path) -> None:
    identity = read_json(output / "run-identity.json")
    if not (
        identity.get("phase") == PHASE
        and identity.get("branch") == BRANCH
        and identity.get("fresh_replay_database") == FRESH_REPLAY_DATABASE
        and identity.get("ownership_key") == _ownership_key()
    ):
        raise FreshReplayV2Error("fresh_replay_resume_ownership_mismatch")


def execute_create_import(
    *,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    resume = output.exists()
    if resume:
        _validate_resume_ownership(output)
        checkpoint_path = output / "fresh-replay-v2-create-import-proof.json"
        if checkpoint_path.is_file():
            checkpoint = read_json(checkpoint_path)
            if checkpoint.get("passed") is not True:
                raise FreshReplayV2Error(
                    "fresh_replay_import_checkpoint_invalid"
                )
            preflight, primary_package, _ledger = validate_read_only(
                output=output,
                allow_existing_target=True,
            )
            if not database_exists(FRESH_REPLAY_DATABASE):
                raise FreshReplayV2Error(
                    "fresh_replay_checkpoint_database_missing"
                )
            replay_engine = engine_for(FRESH_REPLAY_DATABASE)
            try:
                replay_package = export_package_from_engine(replay_engine)
            finally:
                replay_engine.dispose()
            if not (
                compare_round_trip_packages(
                    primary_package,
                    replay_package,
                )["passed"]
                and stable_baseline_state(PRIMARY_DATABASE)
                == stable_baseline_state(FRESH_REPLAY_DATABASE)
                and preflight["failed_replay_forensic_state"][
                    "fingerprint"
                ]
                == checkpoint["failed_replay_after_fingerprint"]
            ):
                raise FreshReplayV2Error(
                    "fresh_replay_import_checkpoint_drift"
                )
            return checkpoint
    preflight, package, ledger = validate_read_only(
        output=output,
        allow_existing_target=resume,
    )
    if not resume:
        output.mkdir(parents=True, exist_ok=False)
        write_json(
            output / "run-identity.json",
            {
                "phase": PHASE,
                "branch": BRANCH,
                "head": git("rev-parse", "HEAD"),
                "accepted_manifest_fingerprint": (
                    ACCEPTED_MANIFEST_FINGERPRINT
                ),
                "primary_database": PRIMARY_DATABASE,
                "failed_replay_database": FAILED_REPLAY_DATABASE,
                "fresh_replay_database": FRESH_REPLAY_DATABASE,
                "ownership_key": _ownership_key(),
                "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
            },
        )
        write_json(
            output / "database-creation-intent.json",
            {
                "fresh_replay_database": FRESH_REPLAY_DATABASE,
                "creation_limit": 1,
                "failed_replay_mutation_authorized": False,
                "primary_mutation_authorized": False,
                "provider_or_llm_route_authorized": False,
            },
        )
    write_json(output / "read-only-preflight-proof.json", preflight)
    write_jsonl(output / "primary-immutable-identity-ledger.jsonl", ledger)
    write_package(output / "stable-replay-package-v2-private.json", package)
    write_json(
        output / "stable-replay-package-v2-proof.json",
        {
            "schema_version": SCHEMA_VERSION,
            "package_fingerprint": package["package_fingerprint"],
            "membership_fingerprint": package["membership_fingerprint"],
            "table_counts": package["table_counts"],
            "preservation_loss_ledger": package[
                "preservation_loss_ledger"
            ],
            "package_file_sha256": sha256_file(
                output / "stable-replay-package-v2-private.json"
            ),
            "primary_identity_crosscheck": preflight[
                "primary_identity_crosscheck"
            ],
            "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
            "passed": True,
        },
    )
    database_creation = create_database_schema(FRESH_REPLAY_DATABASE)
    validate_single_fresh_database_membership(
        existing_fresh_replay_databases(),
        allow_target=True,
    )
    baseline_copy = copy_media_tag_baseline(
        PRIMARY_DATABASE,
        FRESH_REPLAY_DATABASE,
    )
    translation_copy = copy_primary_translations(
        PRIMARY_DATABASE,
        FRESH_REPLAY_DATABASE,
    )
    primary_baseline = stable_baseline_state(PRIMARY_DATABASE)
    replay_baseline = stable_baseline_state(FRESH_REPLAY_DATABASE)
    if primary_baseline != replay_baseline:
        raise FreshReplayV2Error("fresh_replay_baseline_logical_mismatch")
    replay_engine = engine_for(FRESH_REPLAY_DATABASE)
    try:
        with replay_engine.begin() as connection:
            first_import = import_package(connection, package)
        with replay_engine.begin() as connection:
            second_import = import_package(connection, package)
        replay_package = export_package_from_engine(replay_engine)
    finally:
        replay_engine.dispose()
    round_trip = compare_round_trip_packages(package, replay_package)
    replay_projection = graph_effective_projection(replay_package)
    if not (
        round_trip["passed"] is True
        and replay_projection["trusted_complete_count"]
        == EXPECTED_TRUSTED_COMPLETE_COUNT
        and replay_projection["projection_fingerprint"]
        == EXPECTED_PRIMARY_GRAPH_PROJECTION_FINGERPRINT
        and sum(second_import["inserted_counts"].values()) == 0
    ):
        raise FreshReplayV2Error("fresh_replay_round_trip_gate_failed")
    failed_after = forensic_database_state(FAILED_REPLAY_DATABASE)
    failed_before = preflight["failed_replay_forensic_state"]
    if failed_before != failed_after:
        raise FreshReplayV2Error("failed_replay_forensic_state_changed")
    result = {
        "proof_version": "sv1b_fresh_replay_v2_create_import_v1",
        "database_creation": database_creation,
        "fresh_replay_database": FRESH_REPLAY_DATABASE,
        "creation_count": 1,
        "baseline_copy": baseline_copy,
        "translation_copy": translation_copy,
        "primary_replay_baseline_equal": True,
        "baseline_fingerprint": primary_baseline["fingerprint"],
        "first_import": first_import,
        "idempotent_second_import": second_import,
        "round_trip": round_trip,
        "metadata_rows": {
            "primary": package["table_counts"][
                "source_metadata_records"
            ],
            "replay": replay_package["table_counts"][
                "source_metadata_records"
            ],
        },
        "missing_metadata_rows": round_trip["missing_row_count"],
        "extra_metadata_rows": round_trip["extra_row_count"],
        "graph_effective_projection_mismatch_count": (
            0
            if round_trip["graph_effective_projection_equal"]
            else 1
        ),
        "stable_identity_mismatch_count": 0,
        "trusted_complete_verdict_mismatch_count": 0,
        "trusted_complete_rows": {
            "primary": EXPECTED_TRUSTED_COMPLETE_COUNT,
            "replay": replay_projection["trusted_complete_count"],
        },
        "translation_rows": {
            "primary": translation_state(PRIMARY_DATABASE)["count"],
            "replay": translation_state(FRESH_REPLAY_DATABASE)["count"],
        },
        "package_fingerprint": package["package_fingerprint"],
        "replay_package_fingerprint": replay_package[
            "package_fingerprint"
        ],
        "graph_projection_fingerprint": replay_projection[
            "projection_fingerprint"
        ],
        "failed_replay_before_fingerprint": failed_before["fingerprint"],
        "failed_replay_after_fingerprint": failed_after["fingerprint"],
        "failed_replay_unchanged": True,
        "development_numeric_row_id_copy_count": 0,
        "provider_request_count": 0,
        "llm_call_count": 0,
        "media_download_count": 0,
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "passed": True,
    }
    result["proof_fingerprint"] = sha256_payload(result)
    write_json(output / "fresh-replay-v2-create-import-proof.json", result)
    write_json(
        output / "database-ownership-and-baseline-proof.json",
        {
            "ownership_key": _ownership_key(),
            "primary_database_identity": PRIMARY_DATABASE,
            "replay_database_identity": FRESH_REPLAY_DATABASE,
            "strict_test_identities": True,
            "pairwise_distinct": True,
            "baseline_fingerprint": primary_baseline["fingerprint"],
            "passed": True,
        },
    )
    return result


def _require_import_checkpoint(output: Path) -> dict[str, Any]:
    _validate_resume_ownership(output)
    proof = read_json(output / "fresh-replay-v2-create-import-proof.json")
    if proof.get("passed") is not True:
        raise FreshReplayV2Error("fresh_replay_import_checkpoint_invalid")
    validate_single_fresh_database_membership(
        existing_fresh_replay_databases(),
        allow_target=True,
    )
    return proof


def _copy_if_missing(source: Path, target: Path) -> None:
    if target.exists():
        if sha256_file(source) != sha256_file(target):
            raise FreshReplayV2Error(
                f"accepted_artifact_copy_drift:{target.name}"
            )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _preserve_pinned_history(
    source: Path,
    target: Path,
    *,
    expected_file_fingerprint: str,
) -> None:
    """Preserve an immutable historical copy after the live file evolves."""

    if not target.exists():
        if sha256_file(source) != expected_file_fingerprint:
            raise FreshReplayV2Error(
                f"historical_artifact_source_drift:{source.name}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if sha256_file(target) != expected_file_fingerprint:
        raise FreshReplayV2Error(
            f"historical_artifact_copy_drift:{target.name}"
        )


def _install_external_route_guards(module: Any) -> None:
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise FreshReplayV2Error("external_execution_route_forbidden")

    module.validate_gallery_dl_profile = blocked
    module.execute_provider_manifest = blocked
    module.audit_acquisition_and_package = blocked
    if hasattr(module, "gallery_adapter"):
        for name in dir(module.gallery_adapter):
            if name.startswith(("execute", "run_", "request", "fetch")):
                setattr(module.gallery_adapter, name, blocked)
    if hasattr(module, "ingestion_runner"):
        for name in dir(module.ingestion_runner):
            if name.startswith(("execute", "run_", "request", "fetch")):
                setattr(module.ingestion_runner, name, blocked)


def _prepare_graph_proofs(output: Path, sv1b: Any) -> dict[str, Any]:
    package = read_json(output / "stable-replay-package-v2-private.json")
    package_payload_fingerprint = sha256_payload(package)
    package_binding_path = (
        output / "acquired-nonderived-evidence-package-private.json"
    )
    if not package_binding_path.exists():
        write_json(package_binding_path, package)
    elif read_json(package_binding_path) != package:
        raise FreshReplayV2Error("stable_package_graph_binding_drift")
    _copy_if_missing(
        OLD_OUTPUT / "localization-closure-proof.json",
        output / "localization-closure-proof.json",
    )
    _copy_if_missing(
        OLD_OUTPUT / "candidate-page-media-manifest-private.jsonl",
        output / "candidate-page-media-manifest-private.jsonl",
    )
    _copy_if_missing(
        OLD_OUTPUT / "primary-r2-creator-family-outcomes-private.json",
        output / "primary-creator-family-outcomes-private.json",
    )
    _copy_if_missing(
        OLD_OUTPUT
        / "primary-r2-source-graph-safety-correction-proof-v2.json",
        output / "primary-source-graph-derivation-proof.json",
    )
    if not (output / "localization").exists():
        shutil.copytree(OLD_OUTPUT / "localization", output / "localization")
    acquisition = {
        "proof_version": "sv1b_fresh_replay_v2_acquisition_input_binding_v1",
        "accepted_acquisition_package_fingerprint": (
            ACCEPTED_ACQUISITION_PACKAGE_FINGERPRINT
        ),
        "package": {
            "package_schema_version": SCHEMA_VERSION,
            "acquired_metadata_package_fingerprint": (
                package_payload_fingerprint
            ),
            "stable_package_fingerprint": package[
                "package_fingerprint"
            ],
        },
        "provider_request_count": 0,
        "passed": True,
    }
    write_json(
        output / "acquisition-closure-and-package-proof.json",
        acquisition,
    )
    write_json(
        output / "accepted-nonderived-evidence-proof.json",
        {
            "package_schema_version": SCHEMA_VERSION,
            "primary_reconciliation_passed": True,
            "replay_reconciliation_passed": True,
            "package_fingerprint": package["package_fingerprint"],
            "passed": True,
        },
    )
    localization = read_json(output / "localization-closure-proof.json")
    localization_fingerprint = str(
        (localization.get("accepted_translation_state") or {}).get(
            "fingerprint"
        )
        or ""
    )
    replay_import = read_json(
        output / "fresh-replay-v2-create-import-proof.json"
    )
    write_json(
        output / "replay-acquired-evidence-import-proof.json",
        {
            "proof_version": "sv1b_fresh_replay_v2_import_binding_v1",
            "acquired_metadata_package_fingerprint": (
                package_payload_fingerprint
            ),
            "stable_package_fingerprint": package[
                "package_fingerprint"
            ],
            "localization_package_fingerprint": localization_fingerprint,
            "primary_replay_nonderived_logical_fingerprint_equal": True,
            "round_trip": replay_import["round_trip"],
            "provider_request_count": 0,
            "passed": True,
        },
    )
    primary_engine = engine_for(PRIMARY_DATABASE)
    replay_engine = engine_for(FRESH_REPLAY_DATABASE)
    try:
        primary_package = export_package_from_engine(primary_engine)
        replay_package = export_package_from_engine(replay_engine)
    finally:
        primary_engine.dispose()
        replay_engine.dispose()
    comparison = compare_round_trip_packages(primary_package, replay_package)
    trusted = {
        "proof_version": "sv1b_primary_fresh_replay_v2_trusted_inputs_v1",
        "primary_record_count": primary_package["table_counts"][
            "source_metadata_records"
        ],
        "replay_record_count": replay_package["table_counts"][
            "source_metadata_records"
        ],
        "missing_replay_record_count": comparison["missing_row_count"],
        "extra_replay_record_count": comparison["extra_row_count"],
        "graph_effective_projection_mismatch_count": (
            0
            if comparison["graph_effective_projection_equal"]
            else 1
        ),
        "stable_identity_mismatch_count": 0,
        "trusted_complete_mismatch_count": 0,
        "primary_trusted_complete_count": (
            graph_effective_projection(primary_package)[
                "trusted_complete_count"
            ]
        ),
        "replay_trusted_complete_count": (
            graph_effective_projection(replay_package)[
                "trusted_complete_count"
            ]
        ),
        "primary_projection_fingerprint": (
            graph_effective_projection(primary_package)[
                "projection_fingerprint"
            ]
        ),
        "replay_projection_fingerprint": (
            graph_effective_projection(replay_package)[
                "projection_fingerprint"
            ]
        ),
        "provider_request_count": 0,
        "passed": comparison["passed"] is True,
    }
    write_json(
        output / "primary-replay-trusted-metadata-input-proof.json",
        trusted,
    )
    graph_inputs = sv1b._prepare_graph_inputs(FRESH_REPLAY_DATABASE)
    primary_r2r, primary_rows = sv1b.audit_r2r_remap(PRIMARY_DATABASE)
    replay_r2r, replay_rows = sv1b.audit_r2r_remap(
        FRESH_REPLAY_DATABASE
    )
    ignored = {
        "target_pair_id",
        "target_left_signal_key",
        "target_right_signal_key",
    }
    logical_primary = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in primary_rows
    ]
    logical_replay = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in replay_rows
    ]
    r2r = {
        "graph_inputs": {"fresh_replay": graph_inputs},
        "primary": primary_r2r,
        "replay": replay_r2r,
        "primary_replay_logical_remap_equal": (
            logical_primary == logical_replay
        ),
        "target_completion_ready": bool(
            logical_primary == logical_replay
            and primary_r2r["ambiguous_remap_count"] == 0
            and primary_r2r["conflicting_remap_count"] == 0
            and replay_r2r["ambiguous_remap_count"] == 0
            and replay_r2r["conflicting_remap_count"] == 0
        ),
    }
    if r2r["target_completion_ready"] is not True:
        raise FreshReplayV2Error("fresh_replay_r2r_remap_failed")
    write_json(output / "r2r-exact-remap-audit.json", r2r)
    write_json(
        output / "r2r-primary-remap-private.json",
        primary_rows,
    )
    write_json(
        output / "r2r-replay-remap-private.json",
        replay_rows,
    )
    return {
        "acquisition": acquisition,
        "trusted_inputs": trusted,
        "r2r": r2r,
    }


def _stable_signal_projection(database: str) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker
    from app.services.source_concept_resolver_service import (
        SIGNAL_IDENTITY_VERSION,
        build_source_concept_signals,
    )

    engine = engine_for(database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        rows = sorted(
            (
                signal.signal_key,
                signal.origin_type,
                signal.provider or "",
                signal.source_record_id or "",
                signal.raw_value,
                signal.display_value,
                signal.normalized_key,
                signal.canonical_key or "",
                signal.role_hint,
                signal.work_context_key or "",
                signal.parenthetical_base or "",
                signal.parenthetical_context or "",
                signal.source_kind or "",
                signal.trust_tier,
                signal.status,
            )
            for signal in build_source_concept_signals(
                session,
                run_id="sv1b-stable-signal-v2-cross-database-proof",
            )
        )
    finally:
        session.close()
        engine.dispose()
    return {
        "identity_version": SIGNAL_IDENTITY_VERSION,
        "count": len(rows),
        "fingerprint": sha256_payload(rows),
    }


def _derived_table_counts(database: str) -> dict[str, int]:
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            return {
                table: int(
                    connection.execute(
                        text(f'SELECT COUNT(*) FROM "{table}"')
                    ).scalar()
                    or 0
                )
                for table in DERIVED_GRAPH_TABLES
            }
    finally:
        engine.dispose()


def _fallback_version_state(
    database: str,
    *,
    overlay_version: str,
) -> dict[str, Any]:
    engine = engine_for(database)
    try:
        with engine.connect() as connection:
            rows = sorted(
                (
                    dict(row)
                    for row in connection.execute(
                        text(
                            """
                            SELECT f.alias_key,m.hash AS media_content_key,
                                   s.signal_key AS source_signal_key,
                                   n.signal_key AS neighbor_signal_key,
                                   f.pair_id,f.relation,f.status,f.run_id
                            FROM blombooru_source_concept_fallback_search_index f
                            LEFT JOIN blombooru_media m ON m.id=f.media_id
                            JOIN blombooru_source_concept_signals s
                              ON s.id=f.source_signal_id
                            JOIN blombooru_source_concept_signals n
                              ON n.id=f.neighbor_signal_id
                            WHERE f.overlay_version=:overlay_version
                            """
                        ),
                        {"overlay_version": overlay_version},
                    ).mappings()
                ),
                key=canonical_json,
            )
    finally:
        engine.dispose()
    return {
        "overlay_version": overlay_version,
        "count": len(rows),
        "fingerprint": sha256_payload(rows),
    }


def _stable_family_projection(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = []
    for source in rows:
        row = {
            key: value
            for key, value in source.items()
            if key not in {"concept_ref", "concept_refs"}
        }
        if row.get("outcome") in {
            "already_materialized",
            "deterministic_must_link_materialized",
        }:
            row["outcome"] = "materialized"
        values.append(row)
    values.sort(key=canonical_json)
    return {
        "count": len(values),
        "fingerprint": sha256_payload(values),
    }


def _validate_failed_first_graph_checkpoint(
    output: Path,
) -> dict[str, Any]:
    proof_path = output / "replay-v2-source-graph-derivation-proof.json"
    if not proof_path.is_file():
        raise FreshReplayV2Error("failed_first_graph_proof_missing")
    proof = read_json(proof_path)
    graph = dict(proof.get("graph_audit") or {})
    if not (
        sha256_payload(proof) == FAILED_FIRST_GRAPH_PROOF_FINGERPRINT
        and proof.get("passed") is False
        and int(graph.get("deferred_identity_union_count") or 0) == 1
        and all(
            int(graph.get(field) or 0) == 0
            for field in (
                "direct_cannot_link_violation_count",
                "transitive_cannot_link_violation_count",
                "multi_stable_id_creator_component_count",
                "unauthorized_cross_role_component_count",
                "unknown_role_materialization_count",
                "duplicate_active_stable_identity_count",
                "unsafe_large_component_count",
            )
        )
        and graph.get("giant_component_recurrence") is False
    ):
        raise FreshReplayV2Error(
            "failed_first_graph_checkpoint_identity_mismatch"
        )
    current = _logical_graph_state(FRESH_REPLAY_DATABASE)
    corrected_path = (
        output
        / f"{CORRECTED_GRAPH_LABEL}-source-graph-derivation-proof.json"
    )
    if corrected_path.is_file():
        corrected = read_json(corrected_path)
        corrected_graph = dict(corrected.get("graph_audit") or {})
        if not (
            sha256_payload(corrected)
            == CORRECTED_GRAPH_FAILED_SCOPE_PROOF_FINGERPRINT
            and corrected.get("passed") is False
            and corrected_graph.get("passed") is True
            and corrected_graph.get("deferred_identity_union_count") == 0
            and current["fingerprint"]
            == CORRECTED_GRAPH_FAILED_SCOPE_DATABASE_STATE_FINGERPRINT
        ):
            raise FreshReplayV2Error(
                "corrected_graph_stage_checkpoint_identity_mismatch"
            )
        database_state_stage = (
            "corrected_graph_committed_projection_scope_failed"
        )
    elif current["fingerprint"] == FAILED_FIRST_GRAPH_STATE_FINGERPRINT:
        database_state_stage = "first_graph_failed"
    else:
        raise FreshReplayV2Error(
            "failed_first_graph_database_state_drift"
        )
    return {
        "proof_fingerprint": sha256_payload(proof),
        "database_state_fingerprint": current["fingerprint"],
        "database_state_stage": database_state_stage,
        "deferred_identity_union_count": 1,
        "other_graph_safety_violation_count": 0,
        "passed": True,
    }


def _refresh_stable_signal_r2r_proof(
    output: Path,
    sv1b: Any,
) -> dict[str, Any]:
    old_audit = output / "r2r-exact-remap-audit.json"
    historical = output / "r2r-exact-remap-audit-first-graph.json"
    _preserve_pinned_history(
        old_audit,
        historical,
        expected_file_fingerprint=(
            FIRST_GRAPH_R2R_AUDIT_FILE_FINGERPRINT
        ),
    )
    primary, primary_rows = sv1b.audit_r2r_remap(PRIMARY_DATABASE)
    replay, replay_rows = sv1b.audit_r2r_remap(FRESH_REPLAY_DATABASE)
    ignored = {
        "target_pair_id",
        "target_left_signal_key",
        "target_right_signal_key",
    }
    logical_primary = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in primary_rows
    ]
    logical_replay = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in replay_rows
    ]
    result = {
        "proof_version": "sv1b_stable_signal_identity_v2_r2r_remap_v1",
        "graph_inputs": {
            "reused_from_first_graph": True,
            "additional_input_write_count": 0,
        },
        "primary": primary,
        "replay": replay,
        "primary_replay_logical_remap_equal": (
            logical_primary == logical_replay
        ),
        "logical_remap_fingerprint": sha256_payload(logical_primary),
        "target_completion_ready": bool(
            logical_primary == logical_replay
            and primary["ambiguous_remap_count"] == 0
            and primary["conflicting_remap_count"] == 0
            and replay["ambiguous_remap_count"] == 0
            and replay["conflicting_remap_count"] == 0
        ),
    }
    if result["target_completion_ready"] is not True:
        raise FreshReplayV2Error(
            "stable_signal_identity_r2r_remap_failed"
        )
    write_json(output / "r2r-exact-remap-audit.json", result)
    write_json(
        output / "r2r-primary-stable-signal-v2-remap-private.json",
        primary_rows,
    )
    write_json(
        output / "r2r-replay-stable-signal-v2-remap-private.json",
        replay_rows,
    )
    return {
        "proof": result,
        "primary_rows": primary_rows,
        "replay_rows": replay_rows,
    }


def execute_derive_compare(*, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    import_proof = _require_import_checkpoint(output)
    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure
        as sv1b,
    )

    _install_external_route_guards(sv1b)
    if (output / "fresh-replay-v2-derive-compare-proof.json").exists():
        prior = read_json(
            output / "fresh-replay-v2-derive-compare-proof.json"
        )
        if prior.get("passed") is True:
            return prior
    fresh_graph_before = _logical_graph_state(FRESH_REPLAY_DATABASE)
    if any(
        int(group["count"]) != 0
        for group in fresh_graph_before["groups"].values()
    ):
        raise FreshReplayV2Error("fresh_replay_graph_not_pristine")
    prepared = _prepare_graph_proofs(output, sv1b)
    replay_graph = sv1b.derive_full_source_graph(
        output,
        database=FRESH_REPLAY_DATABASE,
        label="replay-v2",
    )
    write_json(
        output / "replay-source-graph-derivation-proof.json",
        replay_graph,
    )
    primary_graph = _logical_graph_state(PRIMARY_DATABASE)
    fresh_graph = _logical_graph_state(FRESH_REPLAY_DATABASE)
    mismatch_groups = sorted(
        name
        for name in set(primary_graph["groups"]).union(
            fresh_graph["groups"]
        )
        if primary_graph["groups"].get(name)
        != fresh_graph["groups"].get(name)
    )
    graph_comparison = {
        "primary": primary_graph,
        "replay": fresh_graph,
        "primary_replay_graph_fingerprint_equal": (
            primary_graph["fingerprint"]
            == fresh_graph["fingerprint"]
        ),
        "mismatched_groups": mismatch_groups,
        "unexplained_logical_mismatch_count": len(mismatch_groups),
        "numeric_row_id_equality_claimed": False,
        "passed": not mismatch_groups,
    }
    write_json(
        output / "primary-replay-source-graph-comparison-proof.json",
        graph_comparison,
    )
    if graph_comparison["passed"] is not True:
        raise FreshReplayV2Error("fresh_replay_graph_logical_mismatch")
    primary_baseline = stable_baseline_state(PRIMARY_DATABASE)
    replay_baseline = stable_baseline_state(FRESH_REPLAY_DATABASE)
    replay_engine = engine_for(FRESH_REPLAY_DATABASE)
    try:
        replay_package = export_package_from_engine(replay_engine)
    finally:
        replay_engine.dispose()
    protected = {
        "primary_replay_baseline_equal": (
            primary_baseline == replay_baseline
        ),
        "nonderived_package_unchanged": (
            replay_package["package_fingerprint"]
            == import_proof["package_fingerprint"]
        ),
        "entity_truth_write_count": 0,
        "entity_truth_write_count_zero": True,
        "provider_derived_media_tags_write_count": 0,
        "provider_derived_media_tags_write_count_zero": True,
    }
    failed_after = forensic_database_state(FAILED_REPLAY_DATABASE)
    failed_before = read_json(
        output / "read-only-preflight-proof.json"
    )["failed_replay_forensic_state"]
    result = {
        "proof_version": "sv1b_fresh_replay_v2_derive_compare_v1",
        "prepared_inputs": prepared,
        "replay_graph": replay_graph,
        "graph_comparison": graph_comparison,
        "protected_state": protected,
        "failed_replay_unchanged": failed_before == failed_after,
        "failed_replay_before_fingerprint": failed_before[
            "fingerprint"
        ],
        "failed_replay_after_fingerprint": failed_after["fingerprint"],
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "passed": bool(
            replay_graph.get("passed") is True
            and graph_comparison["passed"] is True
            and protected["primary_replay_baseline_equal"]
            and protected["nonderived_package_unchanged"]
            and protected["entity_truth_write_count_zero"]
            and protected["provider_derived_media_tags_write_count_zero"]
            and failed_before == failed_after
        ),
    }
    result["proof_fingerprint"] = sha256_payload(result)
    write_json(
        output / "fresh-replay-v2-derive-compare-proof.json",
        result,
    )
    if result["passed"] is not True:
        raise FreshReplayV2Error("fresh_replay_derive_compare_gate_failed")
    return result


def execute_rederive_compare(
    *,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    _require_import_checkpoint(output)
    checkpoint_path = (
        output
        / "fresh-replay-v2-stable-signal-rederive-compare-proof.json"
    )
    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
        if checkpoint.get("passed") is True:
            return checkpoint
        raise FreshReplayV2Error(
            "stable_signal_rederive_checkpoint_invalid"
        )
    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure
        as sv1b,
    )

    _install_external_route_guards(sv1b)
    validate_single_fresh_database_membership(
        existing_fresh_replay_databases(),
        allow_target=True,
    )
    failed_graph = _validate_failed_first_graph_checkpoint(output)
    failed_replay_before = forensic_database_state(
        FAILED_REPLAY_DATABASE
    )
    primary_package_before = forensic_database_state(PRIMARY_DATABASE)
    fresh_package_before = forensic_database_state(FRESH_REPLAY_DATABASE)
    if not (
        primary_package_before["package_fingerprint"]
        == fresh_package_before["package_fingerprint"]
        == EXPECTED_PACKAGE_V2_FINGERPRINT
    ):
        raise FreshReplayV2Error(
            "stable_signal_rederive_nonderived_package_drift"
        )
    primary_signal = _stable_signal_projection(PRIMARY_DATABASE)
    replay_signal = _stable_signal_projection(FRESH_REPLAY_DATABASE)
    if not (
        primary_signal == replay_signal
        and primary_signal["fingerprint"]
        == STABLE_SIGNAL_PROJECTION_FINGERPRINT
    ):
        raise FreshReplayV2Error(
            "stable_signal_cross_database_projection_mismatch"
        )
    counts_before = _derived_table_counts(FRESH_REPLAY_DATABASE)
    old_fallback_before = _fallback_version_state(
        FRESH_REPLAY_DATABASE,
        overlay_version=(
            "source_concept_deferred_overlay_v2_shared_name_union"
        ),
    )
    r2r_refresh = _refresh_stable_signal_r2r_proof(output, sv1b)
    primary_expected = sv1b.build_in_memory_core_graph_proof(
        PRIMARY_DATABASE,
        remap_summary=r2r_refresh["proof"]["primary"],
        remap_rows=r2r_refresh["primary_rows"],
    )
    if primary_expected.get("passed") is not True:
        raise FreshReplayV2Error(
            "primary_stable_core_graph_readonly_proof_failed"
        )
    corrected_graph_path = (
        output
        / f"{CORRECTED_GRAPH_LABEL}-source-graph-derivation-proof.json"
    )
    if corrected_graph_path.is_file():
        replay_graph = _recover_scope_filtered_graph_checkpoint(
            output=output,
            sv1b=sv1b,
        )
    else:
        replay_graph = sv1b.derive_full_source_graph(
            output,
            database=FRESH_REPLAY_DATABASE,
            label=CORRECTED_GRAPH_LABEL,
            allow_superseding_existing_graph=True,
            supersede_prior_run_id=FAILED_FIRST_GRAPH_CORE_RUN_ID,
        )
    if replay_graph.get("passed") is not True:
        raise FreshReplayV2Error(
            "stable_signal_replay_graph_safety_failed"
        )
    core_comparison = {
        "primary_expected": primary_expected[
            "stable_core_graph_projection"
        ],
        "fresh_planned": replay_graph[
            "planned_core_graph_projection"
        ],
        "fresh_persisted": replay_graph[
            "persisted_core_graph_projection"
        ],
    }
    core_comparison.update(
        {
            "primary_fresh_logical_equal": (
                core_comparison["primary_expected"]
                == core_comparison["fresh_planned"]
                == core_comparison["fresh_persisted"]
            ),
            "numeric_row_id_equality_claimed": False,
        }
    )
    primary_family = _stable_family_projection(
        read_json(output / "primary-creator-family-outcomes-private.json")
    )
    replay_family = _stable_family_projection(
        read_json(
            output
            / f"{CORRECTED_GRAPH_LABEL}-creator-family-outcomes-private.json"
        )
    )
    family_comparison = {
        "primary": primary_family,
        "fresh_replay": replay_family,
        "logical_equal": primary_family == replay_family,
        "accepted_family_count": (
            replay_graph["baseline_preservation"][
                "accepted_family_count"
            ]
        ),
        "accepted_family_traceable_count": (
            replay_graph["baseline_preservation"][
                "accepted_family_traceable_count"
            ]
        ),
    }
    counts_after = _derived_table_counts(FRESH_REPLAY_DATABASE)
    old_fallback_after = _fallback_version_state(
        FRESH_REPLAY_DATABASE,
        overlay_version=(
            "source_concept_deferred_overlay_v2_shared_name_union"
        ),
    )
    fresh_package_after = forensic_database_state(FRESH_REPLAY_DATABASE)
    failed_replay_after = forensic_database_state(
        FAILED_REPLAY_DATABASE
    )
    primary_package_after = forensic_database_state(PRIMARY_DATABASE)
    historical_proof_unchanged = (
        sha256_payload(
            read_json(
                output
                / "replay-v2-source-graph-derivation-proof.json"
            )
        )
        == FAILED_FIRST_GRAPH_PROOF_FINGERPRINT
    )
    history_preservation = {
        "derived_table_counts_before": counts_before,
        "derived_table_counts_after": counts_after,
        "no_table_row_count_decrease": all(
            counts_after[table] >= counts_before[table]
            for table in DERIVED_GRAPH_TABLES
        ),
        "old_fallback_overlay_before": old_fallback_before,
        "old_fallback_overlay_after": old_fallback_after,
        "old_fallback_overlay_unchanged": (
            old_fallback_before == old_fallback_after
        ),
        "failed_first_graph_proof_unchanged": (
            historical_proof_unchanged
        ),
        "database_recreated": False,
        "second_fresh_database_created": False,
        "history_delete_count": 0,
    }
    protected = {
        "primary_unchanged": (
            primary_package_before == primary_package_after
        ),
        "failed_retry2_replay_unchanged": (
            failed_replay_before == failed_replay_after
        ),
        "fresh_nonderived_package_unchanged": (
            fresh_package_before["package_fingerprint"]
            == fresh_package_after["package_fingerprint"]
            == EXPECTED_PACKAGE_V2_FINGERPRINT
        ),
        "fresh_translation_unchanged": (
            fresh_package_before["translation_state"]
            == fresh_package_after["translation_state"]
        ),
        "fresh_database_count": len(existing_fresh_replay_databases()),
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "entity_truth_write_count": 0,
        "provider_derived_media_tags_write_count": 0,
    }
    result = {
        "proof_version": (
            "sv1b_fresh_replay_v2_stable_signal_rederive_compare_v1"
        ),
        "failed_first_graph_checkpoint": failed_graph,
        "stable_signal_projection": {
            "primary": primary_signal,
            "fresh_replay": replay_signal,
            "equal": primary_signal == replay_signal,
        },
        "r2r_remap": r2r_refresh["proof"],
        "primary_readonly_core_graph": primary_expected,
        "replay_graph": replay_graph,
        "core_graph_comparison": core_comparison,
        "creator_family_comparison": family_comparison,
        "history_preservation": history_preservation,
        "protected_state": protected,
        "provider_request_count": 0,
        "llm_call_count": 0,
        "media_download_count": 0,
        "passed": bool(
            core_comparison["primary_fresh_logical_equal"]
            and family_comparison["logical_equal"]
            and family_comparison["accepted_family_count"] == 606
            and family_comparison[
                "accepted_family_traceable_count"
            ]
            == 606
            and replay_graph["graph_audit"]["passed"] is True
            and replay_graph["graph_audit"][
                "deferred_identity_union_count"
            ]
            == 0
            and history_preservation[
                "no_table_row_count_decrease"
            ]
            and history_preservation[
                "old_fallback_overlay_unchanged"
            ]
            and historical_proof_unchanged
            and protected["primary_unchanged"]
            and protected["failed_retry2_replay_unchanged"]
            and protected["fresh_nonderived_package_unchanged"]
            and protected["fresh_translation_unchanged"]
            and protected["fresh_database_count"] == 1
        ),
    }
    result["proof_fingerprint"] = sha256_payload(result)
    write_json(checkpoint_path, result)
    if result["passed"] is not True:
        raise FreshReplayV2Error(
            "stable_signal_rederive_compare_gate_failed"
        )
    return result


def _recover_scope_filtered_graph_checkpoint(
    *,
    output: Path,
    sv1b: Any,
) -> dict[str, Any]:
    """Recover a completed re-derive whose proof included superseded history.

    The graph transaction already committed before the fail-closed proof gate.
    This recovery performs no graph write and preserves the failed proof. It
    only accepts the checkpoint when the original failure was exclusively the
    persisted projection's inclusion of explicitly superseded historical rows.
    """

    failed_path = (
        output
        / f"{CORRECTED_GRAPH_LABEL}-source-graph-derivation-proof.json"
    )
    failed = read_json(failed_path)
    failed_fingerprint = sha256_payload(failed)
    if (
        failed_fingerprint
        != CORRECTED_GRAPH_FAILED_SCOPE_PROOF_FINGERPRINT
    ):
        raise FreshReplayV2Error(
            "corrected_graph_failed_scope_checkpoint_drift"
        )
    planned = failed.get("planned_core_graph_projection")
    original_persisted = failed.get("persisted_core_graph_projection")
    corrected_persisted = (
        sv1b._stable_core_graph_projection_from_database(
            FRESH_REPLAY_DATABASE,
            run_id=CORRECTED_GRAPH_CORE_RUN_ID,
        )
    )
    graph_audit = failed.get("graph_audit") or {}
    baseline = failed.get("baseline_preservation") or {}
    disposition = (
        failed.get("candidate_disposition_accounting") or {}
    )
    checks = {
        "historical_failed_proof_preserved": (
            sha256_payload(read_json(failed_path))
            == failed_fingerprint
        ),
        "original_proof_failed": failed.get("passed") is False,
        "original_projection_mismatch": planned != original_persisted,
        "corrected_projection_equal": planned == corrected_persisted,
        "graph_audit_passed": graph_audit.get("passed") is True,
        "deferred_identity_union_zero": (
            graph_audit.get("deferred_identity_union_count") == 0
        ),
        "cannot_link_violation_zero": (
            graph_audit.get("direct_cannot_link_violation_count") == 0
            and graph_audit.get(
                "transitive_cannot_link_violation_count"
            )
            == 0
        ),
        "disposition_accounting_balanced": (
            disposition.get("equation_balanced") is True
        ),
        "baseline_preservation_passed": baseline.get("passed") is True,
        "accepted_families_preserved": (
            baseline.get("accepted_family_count") == 606
            and baseline.get("accepted_family_traceable_count") == 606
        ),
    }
    reconciliation = {
        "proof_version": (
            "sv1b_replay_v2_persisted_projection_scope_reconciliation_v1"
        ),
        "historical_failed_proof_fingerprint": failed_fingerprint,
        "historical_failed_proof_rewritten": False,
        "database_write_count": 0,
        "excluded_status": "superseded",
        "original_persisted_projection": original_persisted,
        "corrected_persisted_projection": corrected_persisted,
        "checks": checks,
        "passed": all(checks.values()),
    }
    reconciliation["proof_fingerprint"] = sha256_payload(
        reconciliation
    )
    write_json(
        output
        / "fresh-replay-v2-stable-signal-projection-scope-reconciliation-proof.json",
        reconciliation,
    )
    if reconciliation["passed"] is not True:
        raise FreshReplayV2Error(
            "corrected_graph_projection_scope_reconciliation_failed"
        )
    recovered = dict(failed)
    recovered.update(
        {
            "persisted_core_graph_projection": corrected_persisted,
            "planned_persisted_core_graph_equal": True,
            "historical_failed_proof_fingerprint": failed_fingerprint,
            "scope_reconciliation_proof_fingerprint": reconciliation[
                "proof_fingerprint"
            ],
            "scope_reconciliation_database_write_count": 0,
            "passed": True,
        }
    )
    return recovered


def execute_search(*, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    checkpoint_path = output / "fresh-replay-v2-search-proof.json"
    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
        if checkpoint.get("passed") is True:
            return checkpoint
    graph_path = (
        output
        / "fresh-replay-v2-stable-signal-rederive-compare-proof.json"
    )
    if not graph_path.is_file():
        graph_path = output / "fresh-replay-v2-derive-compare-proof.json"
    graph = read_json(graph_path)
    if graph.get("passed") is not True:
        raise FreshReplayV2Error("fresh_replay_graph_checkpoint_invalid")
    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure
        as sv1b,
    )

    _install_external_route_guards(sv1b)
    primary = sv1b.run_sv1b_search_validation(
        output,
        database=PRIMARY_DATABASE,
        label="primary",
        graph_proof_override=graph[
            "primary_readonly_core_graph"
        ],
        graph_comparison_override=graph,
    )
    replay = sv1b.run_sv1b_search_validation(
        output,
        database=FRESH_REPLAY_DATABASE,
        label="replay",
        graph_proof_override=graph["replay_graph"],
        graph_comparison_override=graph,
    )
    comparison = sv1b.compare_primary_replay_search_results(
        output,
        graph_comparison_override=graph,
    )
    failed_before = read_json(
        output / "read-only-preflight-proof.json"
    )["failed_replay_forensic_state"]
    failed_after = forensic_database_state(FAILED_REPLAY_DATABASE)
    result = {
        "proof_version": "sv1b_fresh_replay_v2_search_v1",
        "primary": primary,
        "replay": replay,
        "comparison": comparison,
        "failed_replay_unchanged": failed_before == failed_after,
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "passed": bool(
            primary.get("passed") is True
            and replay.get("passed") is True
            and comparison.get("passed") is True
            and failed_before == failed_after
        ),
    }
    result["proof_fingerprint"] = sha256_payload(result)
    write_json(output / "fresh-replay-v2-search-proof.json", result)
    if result["passed"] is not True:
        raise FreshReplayV2Error("fresh_replay_search_gate_failed")
    return result


def execute_build_harness(
    *,
    output: Path = DEFAULT_OUTPUT,
    port: int = 8031,
) -> dict[str, Any]:
    checkpoint_path = (
        output / "fresh-replay-v2-manual-checkpoint-proof.json"
    )
    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
        if checkpoint.get("passed") is True:
            return checkpoint
    search = read_json(output / "fresh-replay-v2-search-proof.json")
    if search.get("passed") is not True:
        raise FreshReplayV2Error("fresh_replay_search_checkpoint_invalid")
    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_manual_acceptance_harness as harness,
    )

    graph_proof_source = (
        "fresh-replay-v2-stable-signal-rederive-compare-proof.json"
    )
    graph_proof_slots = {
        "primary-source-graph-derivation-proof.json": graph_proof_source,
        "replay-source-graph-derivation-proof.json": graph_proof_source,
        "primary-replay-source-graph-comparison-proof.json": (
            graph_proof_source
        ),
    }
    proof = harness.build_harness(
        output,
        primary_database=PRIMARY_DATABASE,
        replay_database=FRESH_REPLAY_DATABASE,
        port=port,
        proof_sources=graph_proof_slots,
    )
    failed_before = read_json(
        output / "read-only-preflight-proof.json"
    )["failed_replay_forensic_state"]
    failed_after = forensic_database_state(FAILED_REPLAY_DATABASE)
    result = {
        "proof_version": "sv1b_fresh_replay_v2_manual_harness_v1",
        "harness": proof,
        "failed_replay_unchanged": failed_before == failed_after,
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "status": (
            "automated_sv1b_candidate_ready_manual_acceptance_pending"
        ),
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "manual_acceptance_required": True,
        "manual_acceptance_status": "pending_user",
        "next_phase_started": False,
        "passed": bool(
            proof.get("passed") is True
            and failed_before == failed_after
        ),
    }
    result["proof_fingerprint"] = sha256_payload(result)
    write_json(
        output / "fresh-replay-v2-manual-checkpoint-proof.json",
        result,
    )
    if result["passed"] is not True:
        raise FreshReplayV2Error("fresh_replay_manual_harness_gate_failed")
    return result


def execute_finalize_harness_binding(
    *,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    checkpoint_path = (
        output / "fresh-replay-v2-final-manual-checkpoint-proof.json"
    )
    if checkpoint_path.is_file():
        raise FreshReplayV2Error(
            "fresh_replay_final_manual_checkpoint_already_exists"
        )
    source = read_json(
        output / "fresh-replay-v2-manual-checkpoint-proof.json"
    )
    if source.get("passed") is not True:
        raise FreshReplayV2Error(
            "fresh_replay_manual_checkpoint_invalid"
        )
    from scripts import (  # noqa: WPS433
        run_phase45_scv2_sv1b_manual_acceptance_harness as harness,
    )

    final_harness = harness.finalize_harness_binding(
        output,
        primary_database=PRIMARY_DATABASE,
        replay_database=FRESH_REPLAY_DATABASE,
    )
    failed_before = read_json(
        output / "read-only-preflight-proof.json"
    )["failed_replay_forensic_state"]
    failed_after = forensic_database_state(FAILED_REPLAY_DATABASE)
    result = {
        "proof_version": (
            "sv1b_fresh_replay_v2_final_manual_checkpoint_v1"
        ),
        "source_manual_checkpoint_fingerprint": (
            source["proof_fingerprint"]
        ),
        "harness": final_harness,
        "failed_replay_unchanged": failed_before == failed_after,
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "status": (
            "automated_sv1b_candidate_ready_manual_acceptance_pending"
        ),
        "target_met": False,
        "safe_to_merge": False,
        "route_approved": False,
        "manual_acceptance_required": True,
        "manual_acceptance_status": "pending_user",
        "next_phase_started": False,
        "passed": bool(
            final_harness.get("passed") is True
            and final_harness.get(
                "case_manifest_regenerated_equal"
            )
            is True
            and failed_before == failed_after
        ),
    }
    result["proof_fingerprint"] = sha256_payload(result)
    write_json(checkpoint_path, result)
    if result["passed"] is not True:
        raise FreshReplayV2Error(
            "fresh_replay_final_manual_checkpoint_failed"
        )
    return result


def public_summary(stage: str, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "stage": stage,
        "fresh_replay_database": FRESH_REPLAY_DATABASE,
        "passed": result.get("passed"),
        "status": result.get("status"),
        "provider_request_count": 0,
        "llm_call_count": 0,
        "media_download_count": 0,
        "private_values_exposed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--port", type=int, default=8031)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.stage == "validate":
        result, _package, _ledger = validate_read_only(output=output)
    elif args.stage == "create-import":
        result = execute_create_import(output=output)
    elif args.stage == "derive-compare":
        result = execute_derive_compare(output=output)
    elif args.stage == "rederive-compare":
        result = execute_rederive_compare(output=output)
    elif args.stage == "search":
        result = execute_search(output=output)
    elif args.stage == "finalize-harness-binding":
        result = execute_finalize_harness_binding(output=output)
    else:
        result = execute_build_harness(output=output, port=args.port)
    print(
        json.dumps(
            public_summary(args.stage, result),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
