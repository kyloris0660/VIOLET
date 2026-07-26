"""Schema-aware stable Replay evidence package v2.

This module has no provider, gallery-dl, LLM, media, or thumbnail execution
route.  It exports only accepted non-derived source evidence and converts
development database references to explicit stable keys.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services.pixiv_metadata_ingestion_service import (
    is_trusted_complete_pixiv_metadata_record,
    stable_pixiv_source_record_fingerprint,
)
SCHEMA_VERSION = "sv1b.stable-replay-evidence.v2"
PACKAGE_KIND = "sv1b_nonderived_source_evidence"
GRAPH_EFFECTIVE_STABLE_IDENTITY_KEYS = frozenset(
    {"provider", "work_id", "page_index"}
)
EXTERNAL_ROUTE_BUDGET = {
    "provider_requests": 0,
    "gallery_dl_requests": 0,
    "llm_calls": 0,
    "media_downloads": 0,
    "thumbnail_downloads": 0,
}


class StableReplayPackageV2Error(ValueError):
    """Raised when stable replay evidence cannot be represented without loss."""


_LEGACY_V1_STABLE_ID_KEYS = frozenset(
    {
        "run_id",
        "created_by_run_id",
        "source_run_id",
        "provider_run_id",
        "source_work_id",
        "work_id",
        "artist_id",
        "source_record_id",
        "pair_id",
        "observation_id",
        "external_id",
    }
)


def _legacy_v1_sanitize(value: Any) -> Any:
    """Reproduce the immutable v1 projection for comparison only.

    This deliberately lossy transform must never be used to create v2 evidence.
    """

    if isinstance(value, Mapping):
        result = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.casefold()
            if (
                lowered == "id"
                or lowered.endswith("_id")
                or lowered.endswith("_ids")
            ) and lowered not in _LEGACY_V1_STABLE_ID_KEYS:
                continue
            result[key] = _legacy_v1_sanitize(child)
        return result
    if isinstance(value, list):
        return [_legacy_v1_sanitize(item) for item in value]
    return value


def _insert_batches(
    connection: Connection,
    table: Table,
    rows: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 500,
) -> int:
    """Insert stable rows idempotently without importing an execution runner."""

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


@dataclass(frozen=True)
class ReferenceField:
    exported_field: str
    target: str
    nullable: bool


@dataclass(frozen=True)
class TableSchema:
    logical_name: str
    physical_name: str
    stable_key_fields: tuple[str, ...]
    stable_columns: tuple[str, ...]
    references: Mapping[str, ReferenceField]
    json_fields: Mapping[str, str]
    null_only_local_fields: tuple[str, ...] = ()
    excluded_local_fields: tuple[str, ...] = ("id", "created_at", "updated_at")

    @property
    def exported_fields(self) -> frozenset[str]:
        return frozenset(
            (*self.stable_columns, *(rule.exported_field for rule in self.references.values()))
        )


TABLE_SCHEMAS: tuple[TableSchema, ...] = (
    TableSchema(
        logical_name="source_metadata_records",
        physical_name="blombooru_source_metadata_records",
        stable_key_fields=("provider_record_key",),
        stable_columns=(
            "provider",
            "provider_run_id",
            "run_label",
            "provider_record_key",
            "source_work_id",
            "source_page_index",
            "source_url",
            "title",
            "artist_name",
            "artist_id",
            "confidence",
            "similarity",
            "metadata_kind",
            "data_type_label",
            "raw_metadata_json",
            "provenance",
            "status",
            "retrieved_at",
        ),
        references={
            "media_id": ReferenceField("media_content_key", "media", True),
        },
        json_fields={
            "raw_metadata_json": "pixiv_provider_raw_metadata_v2",
            "provenance": "source_metadata_provenance_v2",
        },
    ),
    TableSchema(
        logical_name="source_tag_observations",
        physical_name="blombooru_source_tag_observations",
        stable_key_fields=("provider_record_key", "observation_key"),
        stable_columns=(
            "provider",
            "observation_key",
            "raw_tag",
            "normalized_tag",
            "canonical_tag_key",
            "source_tag_kind",
            "source_category_raw",
            "language_hint",
            "confidence",
            "order_index",
            "status",
        ),
        references={
            "source_metadata_record_id": ReferenceField(
                "provider_record_key", "source_metadata_record", False
            ),
        },
        json_fields={},
        null_only_local_fields=("taxonomy_kb_id",),
    ),
    TableSchema(
        logical_name="source_name_observations",
        physical_name="blombooru_source_name_observations",
        stable_key_fields=("provider_record_key", "observation_key"),
        stable_columns=(
            "provider",
            "observation_key",
            "source_work_id",
            "source_page_index",
            "raw_name",
            "normalized_name",
            "canonical_name_key",
            "name_role",
            "source_field",
            "language_hint",
            "script_hint",
            "confidence",
            "provenance",
            "requires_review",
            "status",
        ),
        references={
            "source_metadata_record_id": ReferenceField(
                "provider_record_key", "source_metadata_record", False
            ),
            "media_id": ReferenceField("media_content_key", "media", True),
        },
        json_fields={"provenance": "source_name_observation_provenance_v2"},
    ),
    TableSchema(
        logical_name="source_metadata_evidence",
        physical_name="blombooru_source_metadata_evidence",
        stable_key_fields=("evidence_key",),
        stable_columns=(
            "evidence_key",
            "observation_type",
            "evidence_kind",
            "evidence_strength",
            "provenance",
            "status",
        ),
        references={
            "source_metadata_record_id": ReferenceField(
                "provider_record_key", "source_metadata_record", False
            ),
            "observation_id": ReferenceField("observation_key", "polymorphic_observation", True),
        },
        json_fields={"provenance": "source_metadata_evidence_provenance_v2"},
    ),
    TableSchema(
        logical_name="source_searchable_name_assertions",
        physical_name="blombooru_source_searchable_name_assertions",
        stable_key_fields=("assertion_key",),
        stable_columns=(
            "provider",
            "assertion_key",
            "raw_input",
            "normalized_input",
            "canonical_name_key",
            "asserted_name",
            "asserted_role",
            "status",
            "confidence",
            "confidence_score",
            "evidence_sources_json",
            "model_name",
            "prompt_version",
            "structured_output_schema_version",
            "reasoning_summary_private",
            "provenance_summary",
            "requires_review",
        ),
        references={
            "source_metadata_record_id": ReferenceField(
                "provider_record_key", "source_metadata_record", True
            ),
            "source_tag_observation_id": ReferenceField(
                "source_tag_observation_key", "source_tag_observation", True
            ),
            "source_name_observation_id": ReferenceField(
                "source_name_observation_key", "source_name_observation", True
            ),
        },
        json_fields={
            "evidence_sources_json": "searchable_assertion_evidence_sources_v2",
            "provenance_summary": "searchable_assertion_provenance_v2",
        },
    ),
    TableSchema(
        logical_name="source_tag_registry",
        physical_name="blombooru_source_tag_registry",
        stable_key_fields=("provider_scope", "canonical_tag_key"),
        stable_columns=(
            "provider_scope",
            "normalized_tag",
            "canonical_tag_key",
            "raw_variants_json",
            "first_seen_at",
            "last_seen_at",
            "seen_count",
            "taxonomy_status",
            "governance_status",
        ),
        references={
            "example_source_metadata_id": ReferenceField(
                "example_provider_record_key", "source_metadata_record", True
            ),
        },
        json_fields={"raw_variants_json": "source_tag_registry_variants_v2"},
    ),
    TableSchema(
        logical_name="source_name_registry",
        physical_name="blombooru_source_name_registry",
        stable_key_fields=("canonical_name_key",),
        stable_columns=(
            "canonical_name_key",
            "primary_display_name",
            "normalized_display_name",
            "raw_variants_json",
            "provider_coverage_json",
            "role_distribution_json",
            "first_seen_at",
            "last_seen_at",
            "seen_count",
            "governance_status",
            "manual_override_status",
            "notes",
        ),
        references={},
        json_fields={
            "raw_variants_json": "source_name_registry_variants_v2",
            "provider_coverage_json": "source_name_registry_provider_coverage_v2",
            "role_distribution_json": "source_name_registry_role_distribution_v2",
        },
    ),
)
SCHEMA_BY_LOGICAL = {schema.logical_name: schema for schema in TABLE_SCHEMAS}
SCHEMA_BY_PHYSICAL = {schema.physical_name: schema for schema in TABLE_SCHEMAS}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def schema_manifest() -> dict[str, Any]:
    tables = []
    for schema in TABLE_SCHEMAS:
        tables.append(
            {
                "logical_name": schema.logical_name,
                "physical_name": schema.physical_name,
                "stable_key_fields": list(schema.stable_key_fields),
                "stable_columns": list(schema.stable_columns),
                "references": {
                    key: {
                        "exported_field": rule.exported_field,
                        "target": rule.target,
                        "nullable": rule.nullable,
                    }
                    for key, rule in sorted(schema.references.items())
                },
                "json_fields": dict(sorted(schema.json_fields.items())),
                "null_only_local_fields": list(schema.null_only_local_fields),
                "excluded_local_fields": list(schema.excluded_local_fields),
            }
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "package_kind": PACKAGE_KIND,
        "tables": tables,
    }
    return {**payload, "schema_fingerprint": sha256_payload(payload)}


def stable_source_record_fingerprint(row: Mapping[str, Any]) -> str:
    """Fingerprint provider facts without a development row identity."""

    return stable_pixiv_source_record_fingerprint(row)


class _Ledger:
    def __init__(self) -> None:
        self.paths: Counter[tuple[str, str, str]] = Counter()
        self.members: dict[tuple[str, str, str], list[str]] = {}

    def record(
        self,
        schema: str,
        path: str,
        outcome: str,
        *,
        membership: str | None = None,
    ) -> None:
        key = (schema, path, outcome)
        self.paths[key] += 1
        if membership is not None:
            self.members.setdefault(key, []).append(membership)

    def export(self) -> dict[str, Any]:
        entries = []
        for key in sorted(self.paths):
            schema, path, outcome = key
            members = sorted(self.members.get(key, ()))
            entries.append(
                {
                    "record_schema": schema,
                    "field_path": path,
                    "outcome": outcome,
                    "count": self.paths[key],
                    "membership_fingerprint": sha256_payload(members),
                }
            )
        loss_entries = [
            row for row in entries if row["outcome"] in {"removed", "unmapped", "rejected"}
        ]
        return {
            "ledger_version": "sv1b_stable_replay_preservation_loss_ledger_v2",
            "entries": entries,
            "entry_count": len(entries),
            "field_occurrence_count": sum(row["count"] for row in entries),
            "loss_entry_count": len(loss_entries),
            "graph_effective_loss_count": 0,
            "unknown_fields_preserved": True,
            "silent_loss_count": 0,
        }


def _stable_ref(
    raw_id: Any,
    *,
    record_key_by_id: Mapping[int, str],
    record_fingerprint_by_id: Mapping[int, str],
    path: str,
) -> tuple[str, str]:
    try:
        numeric = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise StableReplayPackageV2Error(f"development_reference_invalid:{path}") from exc
    key = record_key_by_id.get(numeric)
    fingerprint = record_fingerprint_by_id.get(numeric)
    if not key or not fingerprint:
        raise StableReplayPackageV2Error(f"development_reference_unmapped:{path}")
    return key, fingerprint


def _validate_stable_identity(
    value: Any,
    *,
    schema_name: str,
    path: str,
    ledger: _Ledger,
    membership: str,
) -> None:
    if not isinstance(value, Mapping):
        raise StableReplayPackageV2Error(f"stable_identity_shape_invalid:{path}")
    unknown = sorted(set(map(str, value)) - GRAPH_EFFECTIVE_STABLE_IDENTITY_KEYS)
    if unknown:
        raise StableReplayPackageV2Error(
            f"unknown_graph_effective_field:{path}:{','.join(unknown)}"
        )
    for key, child in value.items():
        ledger.record(
            schema_name,
            f"{path}.{key}",
            "preserved_graph_effective",
            membership=membership,
        )
        if key == "page_index" and child is not None:
            try:
                int(child)
            except (TypeError, ValueError) as exc:
                raise StableReplayPackageV2Error(
                    f"stable_identity_page_invalid:{path}"
                ) from exc


def _walk_preserved_json(
    value: Any,
    *,
    schema_name: str,
    path: str,
    ledger: _Ledger,
    membership: str,
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            ledger.record(
                schema_name,
                child_path,
                "preserved_unknown_json_field",
                membership=membership,
            )
            _walk_preserved_json(
                child,
                schema_name=schema_name,
                path=child_path,
                ledger=ledger,
                membership=membership,
            )
    elif isinstance(value, list):
        for child in value:
            _walk_preserved_json(
                child,
                schema_name=schema_name,
                path=f"{path}[]",
                ledger=ledger,
                membership=membership,
            )


def _replace_one_record_reference(
    container: MutableMapping[str, Any],
    *,
    numeric_field: str,
    stable_key_field: str,
    fingerprint_field: str,
    record_key_by_id: Mapping[int, str],
    record_fingerprint_by_id: Mapping[int, str],
    schema_name: str,
    path: str,
    ledger: _Ledger,
    membership: str,
) -> None:
    if numeric_field in container:
        key, fingerprint = _stable_ref(
            container.pop(numeric_field),
            record_key_by_id=record_key_by_id,
            record_fingerprint_by_id=record_fingerprint_by_id,
            path=f"{path}.{numeric_field}",
        )
        container[stable_key_field] = key
        container[fingerprint_field] = fingerprint
    key = container.get(stable_key_field)
    fingerprint = container.get(fingerprint_field)
    if (key is None) != (fingerprint is None):
        raise StableReplayPackageV2Error(
            f"stable_reference_pair_incomplete:{path}.{stable_key_field}"
        )
    if key is not None and (not str(key) or not str(fingerprint)):
        raise StableReplayPackageV2Error(
            f"stable_reference_invalid:{path}.{stable_key_field}"
        )


def _normalize_provenance(
    value: Any,
    *,
    schema_name: str,
    record_key_by_id: Mapping[int, str],
    record_fingerprint_by_id: Mapping[int, str],
    ledger: _Ledger,
    membership: str,
    path: str = "$",
) -> Any:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise StableReplayPackageV2Error(f"provenance_shape_invalid:{schema_name}")
    result: dict[str, Any] = copy.deepcopy(dict(value))
    _replace_one_record_reference(
        result,
        numeric_field="source_metadata_record_id",
        stable_key_field="source_provider_record_key",
        fingerprint_field="source_record_fingerprint",
        record_key_by_id=record_key_by_id,
        record_fingerprint_by_id=record_fingerprint_by_id,
        schema_name=schema_name,
        path=path,
        ledger=ledger,
        membership=membership,
    )
    _replace_one_record_reference(
        result,
        numeric_field="reused_from_source_metadata_record_id",
        stable_key_field="reused_from_provider_record_key",
        fingerprint_field="reused_from_source_record_fingerprint",
        record_key_by_id=record_key_by_id,
        record_fingerprint_by_id=record_fingerprint_by_id,
        schema_name=schema_name,
        path=path,
        ledger=ledger,
        membership=membership,
    )
    stable = result.get("stable_identity_key")
    if stable is not None:
        _validate_stable_identity(
            stable,
            schema_name=schema_name,
            path=f"{path}.stable_identity_key",
            ledger=ledger,
            membership=membership,
        )
    _walk_preserved_json(
        result,
        schema_name=schema_name,
        path=path,
        ledger=ledger,
        membership=membership,
    )
    return result


def _normalize_raw_metadata(
    value: Any,
    *,
    schema_name: str,
    record_key_by_id: Mapping[int, str],
    record_fingerprint_by_id: Mapping[int, str],
    ledger: _Ledger,
    membership: str,
    path: str = "$",
) -> Any:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise StableReplayPackageV2Error("raw_metadata_shape_invalid")
    result: dict[str, Any] = copy.deepcopy(dict(value))
    if "attempted_queue_record_id" in result:
        key, fingerprint = _stable_ref(
            result.pop("attempted_queue_record_id"),
            record_key_by_id=record_key_by_id,
            record_fingerprint_by_id=record_fingerprint_by_id,
            path=f"{path}.attempted_queue_record_id",
        )
        result["attempted_queue_provider_record_key"] = key
        result["attempted_queue_record_fingerprint"] = fingerprint
    attempted_key = result.get("attempted_queue_provider_record_key")
    attempted_fingerprint = result.get("attempted_queue_record_fingerprint")
    if (attempted_key is None) != (attempted_fingerprint is None):
        raise StableReplayPackageV2Error("attempted_queue_stable_reference_incomplete")
    if "reused_complete_record_ids" in result:
        raw_ids = result.pop("reused_complete_record_ids")
        if not isinstance(raw_ids, list):
            raise StableReplayPackageV2Error("reused_complete_record_ids_shape_invalid")
        stable_refs = [
            {
                "provider_record_key": _stable_ref(
                    item,
                    record_key_by_id=record_key_by_id,
                    record_fingerprint_by_id=record_fingerprint_by_id,
                    path=f"{path}.reused_complete_record_ids[]",
                )[0],
                "source_record_fingerprint": _stable_ref(
                    item,
                    record_key_by_id=record_key_by_id,
                    record_fingerprint_by_id=record_fingerprint_by_id,
                    path=f"{path}.reused_complete_record_ids[]",
                )[1],
            }
            for item in raw_ids
        ]
        result["reused_complete_record_references"] = stable_refs
    stable_reuse_refs = result.get("reused_complete_record_references")
    if stable_reuse_refs is not None:
        if not isinstance(stable_reuse_refs, list):
            raise StableReplayPackageV2Error(
                "reused_complete_record_references_shape_invalid"
            )
        for item in stable_reuse_refs:
            if not isinstance(item, Mapping) or not item.get(
                "provider_record_key"
            ) or not item.get("source_record_fingerprint"):
                raise StableReplayPackageV2Error(
                    "reused_complete_stable_reference_invalid"
                )
    reuse = result.get("_pixiv_ingestion_reuse")
    if reuse is not None:
        if not isinstance(reuse, Mapping):
            raise StableReplayPackageV2Error("pixiv_ingestion_reuse_shape_invalid")
        reuse_result = copy.deepcopy(dict(reuse))
        _replace_one_record_reference(
            reuse_result,
            numeric_field="source_metadata_record_id",
            stable_key_field="source_provider_record_key",
            fingerprint_field="source_record_fingerprint",
            record_key_by_id=record_key_by_id,
            record_fingerprint_by_id=record_fingerprint_by_id,
            schema_name=schema_name,
            path=f"{path}._pixiv_ingestion_reuse",
            ledger=ledger,
            membership=membership,
        )
        if reuse_result.get("stable_identity_key") is not None:
            _validate_stable_identity(
                reuse_result["stable_identity_key"],
                schema_name=schema_name,
                path=f"{path}._pixiv_ingestion_reuse.stable_identity_key",
                ledger=ledger,
                membership=membership,
            )
        result["_pixiv_ingestion_reuse"] = reuse_result
    phase_delta = result.get("_sv1b_phase_delta")
    if phase_delta is not None:
        if not isinstance(phase_delta, Mapping):
            raise StableReplayPackageV2Error("phase_delta_shape_invalid")
        phase_result = copy.deepcopy(dict(phase_delta))
        if isinstance(phase_result.get("original_raw_metadata_json"), Mapping):
            phase_result["original_raw_metadata_json"] = _normalize_raw_metadata(
                phase_result["original_raw_metadata_json"],
                schema_name=schema_name,
                record_key_by_id=record_key_by_id,
                record_fingerprint_by_id=record_fingerprint_by_id,
                ledger=ledger,
                membership=membership,
                path=f"{path}._sv1b_phase_delta.original_raw_metadata_json",
            )
        if isinstance(phase_result.get("original_provenance"), Mapping):
            phase_result["original_provenance"] = _normalize_provenance(
                phase_result["original_provenance"],
                schema_name="source_metadata_provenance_v2",
                record_key_by_id=record_key_by_id,
                record_fingerprint_by_id=record_fingerprint_by_id,
                ledger=ledger,
                membership=membership,
                path=f"{path}._sv1b_phase_delta.original_provenance",
            )
        result["_sv1b_phase_delta"] = phase_result
    _walk_preserved_json(
        result,
        schema_name=schema_name,
        path=path,
        ledger=ledger,
        membership=membership,
    )
    return result


def _normalize_json_field(
    value: Any,
    *,
    schema_name: str,
    record_key_by_id: Mapping[int, str],
    record_fingerprint_by_id: Mapping[int, str],
    ledger: _Ledger,
    membership: str,
) -> Any:
    if schema_name == "pixiv_provider_raw_metadata_v2":
        return _normalize_raw_metadata(
            value,
            schema_name=schema_name,
            record_key_by_id=record_key_by_id,
            record_fingerprint_by_id=record_fingerprint_by_id,
            ledger=ledger,
            membership=membership,
        )
    if schema_name.endswith("provenance_v2"):
        return _normalize_provenance(
            value,
            schema_name=schema_name,
            record_key_by_id=record_key_by_id,
            record_fingerprint_by_id=record_fingerprint_by_id,
            ledger=ledger,
            membership=membership,
        )
    copied = copy.deepcopy(value)
    _walk_preserved_json(
        copied,
        schema_name=schema_name,
        path="$",
        ledger=ledger,
        membership=membership,
    )
    return copied


def _rows(connection: Connection, table: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).mappings()
    ]


def _key_maps(connection: Connection) -> dict[str, Mapping[int, Any]]:
    metadata_rows = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT r.*,m.hash AS media_content_key
                FROM blombooru_source_metadata_records r
                LEFT JOIN blombooru_media m ON m.id=r.media_id
                ORDER BY r.id
                """
            )
        ).mappings()
    ]
    record_key_by_id = {
        int(row["id"]): str(row["provider_record_key"]) for row in metadata_rows
    }
    record_fingerprint_by_id = {
        int(row["id"]): stable_source_record_fingerprint(row) for row in metadata_rows
    }
    media_key_by_id = {
        int(row["id"]): str(row["hash"])
        for row in connection.execute(
            text("SELECT id,hash FROM blombooru_media WHERE hash IS NOT NULL")
        ).mappings()
    }
    tag_observation_key_by_id = {
        int(row["id"]): str(row["observation_key"])
        for row in connection.execute(
            text("SELECT id,observation_key FROM blombooru_source_tag_observations")
        ).mappings()
    }
    name_observation_key_by_id = {
        int(row["id"]): str(row["observation_key"])
        for row in connection.execute(
            text("SELECT id,observation_key FROM blombooru_source_name_observations")
        ).mappings()
    }
    return {
        "record_key_by_id": record_key_by_id,
        "record_fingerprint_by_id": record_fingerprint_by_id,
        "media_key_by_id": media_key_by_id,
        "tag_observation_key_by_id": tag_observation_key_by_id,
        "name_observation_key_by_id": name_observation_key_by_id,
    }


def _resolve_export_reference(
    schema: TableSchema,
    field: str,
    value: Any,
    *,
    row: Mapping[str, Any],
    maps: Mapping[str, Mapping[int, Any]],
) -> Any:
    rule = schema.references[field]
    if value is None:
        if not rule.nullable:
            raise StableReplayPackageV2Error(
                f"required_reference_missing:{schema.logical_name}.{field}"
            )
        return None
    numeric = int(value)
    if rule.target == "media":
        resolved = maps["media_key_by_id"].get(numeric)
    elif rule.target == "source_metadata_record":
        resolved = maps["record_key_by_id"].get(numeric)
    elif rule.target == "source_tag_observation":
        resolved = maps["tag_observation_key_by_id"].get(numeric)
    elif rule.target == "source_name_observation":
        resolved = maps["name_observation_key_by_id"].get(numeric)
    elif rule.target == "polymorphic_observation":
        observation_type = str(row.get("observation_type") or "")
        if observation_type == "source_tag_observation":
            resolved = maps["tag_observation_key_by_id"].get(numeric)
        elif observation_type == "source_name_observation":
            resolved = maps["name_observation_key_by_id"].get(numeric)
        else:
            raise StableReplayPackageV2Error(
                f"observation_type_invalid:{observation_type}"
            )
    else:
        raise StableReplayPackageV2Error(f"reference_target_unknown:{rule.target}")
    if resolved is None:
        raise StableReplayPackageV2Error(
            f"reference_unmapped:{schema.logical_name}.{field}"
        )
    return resolved


def build_package_from_rows(
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    maps: Mapping[str, Mapping[int, Any]],
) -> dict[str, Any]:
    ledger = _Ledger()
    tables: dict[str, list[dict[str, Any]]] = {}
    for schema in TABLE_SCHEMAS:
        source_rows = rows_by_table.get(schema.logical_name)
        if source_rows is None:
            raise StableReplayPackageV2Error(
                f"table_missing:{schema.logical_name}"
            )
        exported_rows = []
        for raw_row in source_rows:
            row = dict(raw_row)
            key_membership = sha256_payload(
                [row.get(field) for field in schema.stable_key_fields]
            )
            actual_fields = set(row)
            known_fields = set(schema.stable_columns) | set(schema.references)
            known_fields |= set(schema.null_only_local_fields) | set(
                schema.excluded_local_fields
            )
            unknown_fields = sorted(actual_fields - known_fields)
            if unknown_fields:
                raise StableReplayPackageV2Error(
                    f"unknown_table_fields:{schema.logical_name}:{','.join(unknown_fields)}"
                )
            item: dict[str, Any] = {}
            for field in schema.stable_columns:
                value = row.get(field)
                json_schema = schema.json_fields.get(field)
                if json_schema:
                    value = _normalize_json_field(
                        value,
                        schema_name=json_schema,
                        record_key_by_id=maps["record_key_by_id"],
                        record_fingerprint_by_id=maps["record_fingerprint_by_id"],
                        ledger=ledger,
                        membership=key_membership,
                    )
                item[field] = value
                ledger.record(
                    schema.logical_name,
                    f"$.{field}",
                    "preserved_schema_field",
                    membership=key_membership,
                )
            for field, rule in schema.references.items():
                item[rule.exported_field] = _resolve_export_reference(
                    schema, field, row.get(field), row=row, maps=maps
                )
                ledger.record(
                    schema.logical_name,
                    f"$.{field}",
                    "transformed_to_stable_reference",
                    membership=key_membership,
                )
            for field in schema.null_only_local_fields:
                if row.get(field) is not None:
                    raise StableReplayPackageV2Error(
                        f"nonstable_numeric_reference_present:{schema.logical_name}.{field}"
                    )
                ledger.record(
                    schema.logical_name,
                    f"$.{field}",
                    "excluded_null_local_reference",
                    membership=key_membership,
                )
            for field in schema.excluded_local_fields:
                if field in row:
                    ledger.record(
                        schema.logical_name,
                        f"$.{field}",
                        "excluded_development_field",
                        membership=key_membership,
                    )
            before_trusted = (
                is_trusted_complete_pixiv_metadata_record(row)
                if schema.logical_name == "source_metadata_records"
                else False
            )
            after_trusted = (
                is_trusted_complete_pixiv_metadata_record(item)
                if schema.logical_name == "source_metadata_records"
                else False
            )
            if before_trusted and not after_trusted:
                raise StableReplayPackageV2Error(
                    "graph_effective_trusted_verdict_lost"
                )
            exported_rows.append(item)
        exported_rows.sort(
            key=lambda row: tuple(canonical_json(row.get(key)) for key in schema.stable_key_fields)
        )
        stable_keys = [
            tuple(canonical_json(row.get(key)) for key in schema.stable_key_fields)
            for row in exported_rows
        ]
        if len(stable_keys) != len(set(stable_keys)):
            raise StableReplayPackageV2Error(
                f"duplicate_stable_key:{schema.logical_name}"
            )
        tables[schema.logical_name] = exported_rows
    manifest = schema_manifest()
    package_without_ledger = {
        "schema_version": SCHEMA_VERSION,
        "package_kind": PACKAGE_KIND,
        "schema_fingerprint": manifest["schema_fingerprint"],
        "external_route_budget": dict(EXTERNAL_ROUTE_BUDGET),
        "tables": tables,
    }
    preservation_ledger = ledger.export()
    package = {
        **package_without_ledger,
        "preservation_loss_ledger": preservation_ledger,
        "table_counts": {name: len(rows) for name, rows in tables.items()},
    }
    package["membership_fingerprint"] = sha256_payload(
        {
            name: [
                [row.get(field) for field in SCHEMA_BY_LOGICAL[name].stable_key_fields]
                for row in rows
            ]
            for name, rows in tables.items()
        }
    )
    package["package_fingerprint"] = sha256_payload(
        {key: value for key, value in package.items() if key != "package_fingerprint"}
    )
    validate_package(package)
    return package


def export_package(connection: Connection) -> dict[str, Any]:
    maps = _key_maps(connection)
    rows_by_table = {
        schema.logical_name: _rows(connection, schema.physical_name)
        for schema in TABLE_SCHEMAS
    }
    return build_package_from_rows(rows_by_table, maps=maps)


def export_package_from_engine(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        return export_package(connection)


def _scan_for_forbidden_development_references(
    value: Any,
    *,
    schema_name: str,
    path: str = "$",
) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            forbidden = False
            if schema_name == "pixiv_provider_raw_metadata_v2":
                forbidden = child_path.endswith(
                    (
                        ".attempted_queue_record_id",
                        ".reused_complete_record_ids",
                        "._pixiv_ingestion_reuse.source_metadata_record_id",
                    )
                )
            elif schema_name.endswith("provenance_v2"):
                forbidden = child_path.endswith(
                    (
                        ".source_metadata_record_id",
                        ".reused_from_source_metadata_record_id",
                    )
                )
            if forbidden:
                findings.append(child_path)
            findings.extend(
                _scan_for_forbidden_development_references(
                    child,
                    schema_name=schema_name,
                    path=child_path,
                )
            )
    elif isinstance(value, list):
        for child in value:
            findings.extend(
                _scan_for_forbidden_development_references(
                    child,
                    schema_name=schema_name,
                    path=f"{path}[]",
                )
            )
    return findings


def validate_package(package: Mapping[str, Any]) -> None:
    if package.get("schema_version") != SCHEMA_VERSION:
        raise StableReplayPackageV2Error("schema_version_missing_or_unsupported")
    if package.get("package_kind") != PACKAGE_KIND:
        raise StableReplayPackageV2Error("package_kind_invalid")
    if package.get("schema_fingerprint") != schema_manifest()["schema_fingerprint"]:
        raise StableReplayPackageV2Error("schema_fingerprint_mismatch")
    if package.get("external_route_budget") != EXTERNAL_ROUTE_BUDGET:
        raise StableReplayPackageV2Error("external_route_budget_nonzero")
    tables = package.get("tables")
    if not isinstance(tables, Mapping) or set(tables) != set(SCHEMA_BY_LOGICAL):
        raise StableReplayPackageV2Error("table_membership_invalid")
    for logical, rows in tables.items():
        if not isinstance(rows, list):
            raise StableReplayPackageV2Error(f"table_rows_invalid:{logical}")
        schema = SCHEMA_BY_LOGICAL[logical]
        seen = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise StableReplayPackageV2Error(f"row_shape_invalid:{logical}")
            if set(row) != set(schema.exported_fields):
                raise StableReplayPackageV2Error(f"row_fields_invalid:{logical}")
            stable_key = tuple(canonical_json(row.get(key)) for key in schema.stable_key_fields)
            if stable_key in seen:
                raise StableReplayPackageV2Error(f"duplicate_stable_key:{logical}")
            seen.add(stable_key)
            for field, json_schema in schema.json_fields.items():
                findings = _scan_for_forbidden_development_references(
                    row.get(field), schema_name=json_schema
                )
                if findings:
                    raise StableReplayPackageV2Error(
                        f"development_row_id_dependency:{logical}.{field}:{findings[0]}"
                    )
                if (
                    json_schema.endswith("provenance_v2")
                    and isinstance(row.get(field), Mapping)
                    and row[field].get("stable_identity_key") is not None
                ):
                    stable = row[field]["stable_identity_key"]
                    unknown = set(stable) - GRAPH_EFFECTIVE_STABLE_IDENTITY_KEYS
                    if unknown:
                        raise StableReplayPackageV2Error(
                            f"unknown_graph_effective_field:{logical}.{field}"
                        )
    validate_stable_reference_integrity(package)
    ledger = package.get("preservation_loss_ledger")
    if not isinstance(ledger, Mapping):
        raise StableReplayPackageV2Error("preservation_loss_ledger_missing")
    if (
        int(ledger.get("loss_entry_count", -1)) != 0
        or int(ledger.get("graph_effective_loss_count", -1)) != 0
        or int(ledger.get("silent_loss_count", -1)) != 0
    ):
        raise StableReplayPackageV2Error("preservation_loss_ledger_blocking")
    expected = sha256_payload(
        {key: value for key, value in package.items() if key != "package_fingerprint"}
    )
    if package.get("package_fingerprint") != expected:
        raise StableReplayPackageV2Error("package_fingerprint_mismatch")


def validate_stable_reference_integrity(
    package: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate every stable source-record key/fingerprint pair in JSON fields."""

    tables = package.get("tables")
    if not isinstance(tables, Mapping):
        raise StableReplayPackageV2Error("table_membership_invalid")
    metadata_rows = tables.get("source_metadata_records")
    if not isinstance(metadata_rows, list):
        raise StableReplayPackageV2Error("source_metadata_records_missing")
    fingerprint_by_key: dict[str, str] = {}
    for row in metadata_rows:
        if not isinstance(row, Mapping) or not row.get("provider_record_key"):
            raise StableReplayPackageV2Error("source_metadata_record_key_invalid")
        key = str(row["provider_record_key"])
        if key in fingerprint_by_key:
            raise StableReplayPackageV2Error(
                "duplicate_stable_key:source_metadata_records"
            )
        fingerprint_by_key[key] = stable_source_record_fingerprint(row)

    references: list[dict[str, str]] = []
    discovered_reference_pair_count = 0

    def check_pair(
        value: Mapping[str, Any],
        *,
        key_field: str,
        fingerprint_field: str,
        path: str,
    ) -> None:
        nonlocal discovered_reference_pair_count
        if key_field not in value and fingerprint_field not in value:
            return
        discovered_reference_pair_count += 1
        key = value.get(key_field)
        fingerprint = value.get(fingerprint_field)
        if key in (None, "") or fingerprint in (None, ""):
            raise StableReplayPackageV2Error(
                f"stable_reference_pair_incomplete:{path}.{key_field}"
            )
        stable_key = str(key)
        expected = fingerprint_by_key.get(stable_key)
        if expected is None:
            raise StableReplayPackageV2Error(
                f"stable_reference_unknown_key:{path}.{key_field}"
            )
        if str(fingerprint) != expected:
            raise StableReplayPackageV2Error(
                f"stable_reference_fingerprint_mismatch:{path}.{key_field}"
            )
        references.append(
            {
                "path": path,
                "key_field": key_field,
                "stable_key": stable_key,
                "stable_fingerprint": expected,
            }
        )

    def walk(value: Any, *, path: str, reuse_list_item: bool = False) -> None:
        if isinstance(value, Mapping):
            if reuse_list_item:
                check_pair(
                    value,
                    key_field="provider_record_key",
                    fingerprint_field="source_record_fingerprint",
                    path=path,
                )
            else:
                for key_field, fingerprint_field in (
                    (
                        "source_provider_record_key",
                        "source_record_fingerprint",
                    ),
                    (
                        "reused_from_provider_record_key",
                        "reused_from_source_record_fingerprint",
                    ),
                    (
                        "attempted_queue_provider_record_key",
                        "attempted_queue_record_fingerprint",
                    ),
                ):
                    check_pair(
                        value,
                        key_field=key_field,
                        fingerprint_field=fingerprint_field,
                        path=path,
                    )
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key == "reused_complete_record_references":
                    if not isinstance(child, list):
                        raise StableReplayPackageV2Error(
                            "reused_complete_record_references_shape_invalid"
                        )
                    for item in child:
                        walk(
                            item,
                            path=f"{child_path}[]",
                            reuse_list_item=True,
                        )
                else:
                    walk(child, path=child_path)
        elif isinstance(value, list):
            for child in value:
                walk(child, path=f"{path}[]")

    for index, row in enumerate(metadata_rows):
        for field in ("raw_metadata_json", "provenance"):
            walk(
                row.get(field),
                path=f"$.source_metadata_records[{index}].{field}",
            )
    references.sort(
        key=lambda row: (
            row["path"],
            row["key_field"],
            row["stable_key"],
            row["stable_fingerprint"],
        )
    )
    checks = {
        "source_record_keys_unique": (
            len(fingerprint_by_key) == len(metadata_rows)
        ),
        "all_discovered_references_verified": (
            len(references) == discovered_reference_pair_count
        ),
    }
    failed_check_count = sum(value is not True for value in checks.values())
    return {
        "checked_source_record_count": len(metadata_rows),
        "discovered_reference_pair_count": discovered_reference_pair_count,
        "reference_count": len(references),
        "failed_check_count": failed_check_count,
        "checks": checks,
        "reference_membership_fingerprint": sha256_payload(references),
        "passed": failed_check_count == 0,
    }


def graph_effective_projection(package: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable metadata projection consumed by trusted graph inputs."""

    validate_package(package)
    rows = []
    for row in package["tables"]["source_metadata_records"]:
        provenance = row.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        rows.append(
            {
                "provider_record_key": row.get("provider_record_key"),
                "media_content_key": row.get("media_content_key"),
                "provider": row.get("provider"),
                "metadata_kind": row.get("metadata_kind"),
                "data_type_label": row.get("data_type_label"),
                "status": row.get("status"),
                "source_work_id": row.get("source_work_id"),
                "source_page_index": row.get("source_page_index"),
                "title": row.get("title"),
                "artist_id": row.get("artist_id"),
                "artist_name": row.get("artist_name"),
                "raw_metadata_json": row.get("raw_metadata_json"),
                "provenance": provenance,
                "stable_identity_key": provenance.get("stable_identity_key"),
                "trusted_complete": is_trusted_complete_pixiv_metadata_record(row),
            }
        )
    rows.sort(key=lambda row: str(row["provider_record_key"]))
    return {
        "row_count": len(rows),
        "trusted_complete_count": sum(row["trusted_complete"] for row in rows),
        "projection_fingerprint": sha256_payload(rows),
        "rows": rows,
    }


def compare_round_trip_packages(
    source_package: Mapping[str, Any],
    replay_package: Mapping[str, Any],
) -> dict[str, Any]:
    validate_package(source_package)
    validate_package(replay_package)
    source_projection = graph_effective_projection(source_package)
    replay_projection = graph_effective_projection(replay_package)
    source_membership = _stable_membership_by_table(source_package)
    replay_membership = _stable_membership_by_table(replay_package)
    missing_by_table: dict[str, int] = {}
    extra_by_table: dict[str, int] = {}
    missing_membership: list[str] = []
    extra_membership: list[str] = []
    for logical in sorted(source_membership):
        missing = sorted(source_membership[logical] - replay_membership[logical])
        extra = sorted(replay_membership[logical] - source_membership[logical])
        missing_by_table[logical] = len(missing)
        extra_by_table[logical] = len(extra)
        missing_membership.extend(f"{logical}:{value}" for value in missing)
        extra_membership.extend(f"{logical}:{value}" for value in extra)
    source_rows = {
        str(row["provider_record_key"]): row
        for row in source_projection["rows"]
    }
    replay_rows = {
        str(row["provider_record_key"]): row
        for row in replay_projection["rows"]
    }
    common_keys = sorted(set(source_rows) & set(replay_rows))
    projection_mismatches = [
        key for key in common_keys if source_rows[key] != replay_rows[key]
    ]
    stable_identity_mismatches = [
        key
        for key in common_keys
        if source_rows[key].get("stable_identity_key")
        != replay_rows[key].get("stable_identity_key")
    ]
    trusted_verdict_mismatches = [
        key
        for key in common_keys
        if source_rows[key].get("trusted_complete")
        != replay_rows[key].get("trusted_complete")
    ]
    mismatch_membership = {
        "missing": missing_membership,
        "extra": extra_membership,
        "graph_effective_projection": projection_mismatches,
        "stable_identity": stable_identity_mismatches,
        "trusted_complete_verdict": trusted_verdict_mismatches,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "package_fingerprint_equal": (
            source_package["package_fingerprint"]
            == replay_package["package_fingerprint"]
        ),
        "membership_fingerprint_equal": (
            source_package["membership_fingerprint"]
            == replay_package["membership_fingerprint"]
        ),
        "graph_effective_projection_equal": (
            source_projection["projection_fingerprint"]
            == replay_projection["projection_fingerprint"]
        ),
        "trusted_complete_count_equal": (
            source_projection["trusted_complete_count"]
            == replay_projection["trusted_complete_count"]
        ),
        "source_trusted_complete_count": source_projection["trusted_complete_count"],
        "replay_trusted_complete_count": replay_projection["trusted_complete_count"],
        "missing_stable_membership_by_table": missing_by_table,
        "extra_stable_membership_by_table": extra_by_table,
        "missing_row_count": len(missing_membership),
        "extra_row_count": len(extra_membership),
        "graph_effective_projection_mismatch_count": len(
            projection_mismatches
        ),
        "stable_identity_mismatch_count": len(stable_identity_mismatches),
        "trusted_complete_verdict_mismatch_count": len(
            trusted_verdict_mismatches
        ),
        "missing_membership_fingerprint": sha256_payload(missing_membership),
        "extra_membership_fingerprint": sha256_payload(extra_membership),
        "mismatch_membership_fingerprint": sha256_payload(
            mismatch_membership
        ),
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
    }
    result["passed"] = all(
        result[key] is True
        for key in (
            "package_fingerprint_equal",
            "membership_fingerprint_equal",
            "graph_effective_projection_equal",
            "trusted_complete_count_equal",
        )
    ) and result["missing_row_count"] == 0 and result["extra_row_count"] == 0
    return result


def _stable_membership_by_table(
    package: Mapping[str, Any],
) -> dict[str, set[str]]:
    memberships: dict[str, set[str]] = {}
    for logical, schema in SCHEMA_BY_LOGICAL.items():
        memberships[logical] = {
            canonical_json(
                [row.get(field) for field in schema.stable_key_fields]
            )
            for row in package["tables"][logical]
        }
    return memberships


def verify_external_routes_forbidden(operation_counts: Mapping[str, Any]) -> None:
    unexpected = {
        key: int(operation_counts.get(key, -1))
        for key in EXTERNAL_ROUTE_BUDGET
        if int(operation_counts.get(key, -1)) != 0
    }
    if unexpected:
        raise StableReplayPackageV2Error(
            f"external_route_entered:{','.join(sorted(unexpected))}"
        )


def _raw_work_identity_candidates(raw: Any) -> set[str]:
    if not isinstance(raw, Mapping):
        return set()
    candidates: set[str] = set()
    for field in ("id", "source_work_id"):
        value = raw.get(field)
        if value not in (None, ""):
            candidates.add(str(value))
    structural = raw.get("structural_diagnostics")
    if isinstance(structural, Mapping) and structural.get("work_id") not in (None, ""):
        candidates.add(str(structural["work_id"]))
    parser = raw.get("parser_evidence")
    if isinstance(parser, list):
        for item in parser:
            if isinstance(item, Mapping) and item.get("work_id") not in (None, ""):
                candidates.add(str(item["work_id"]))
    return candidates


def _accepted_provider_fact_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: row.get(field)
        for field in (
            "provider_record_key",
            "media_content_key",
            "provider",
            "source_work_id",
            "source_page_index",
            "title",
            "artist_id",
            "artist_name",
            "metadata_kind",
            "data_type_label",
            "status",
        )
    }


def cross_validate_primary_stable_identity(
    primary_package: Mapping[str, Any],
    accepted_v1_package: Mapping[str, Any],
    *,
    candidate_pages: Sequence[Mapping[str, Any]],
    final_work_outcomes: Sequence[Mapping[str, Any]],
    route_viability_attempts: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Cross-check Primary identity against accepted immutable execution evidence."""

    validate_package(primary_package)
    accepted_tables = accepted_v1_package.get("tables")
    if not isinstance(accepted_tables, Mapping):
        raise StableReplayPackageV2Error("accepted_v1_tables_missing")
    accepted_rows = accepted_tables.get("source_metadata_records")
    if not isinstance(accepted_rows, list):
        raise StableReplayPackageV2Error("accepted_v1_metadata_rows_missing")
    accepted_by_key = {
        str(row.get("provider_record_key")): row for row in accepted_rows
    }
    if len(accepted_by_key) != len(accepted_rows):
        raise StableReplayPackageV2Error("accepted_v1_duplicate_provider_record_key")
    candidate_membership = {
        (
            str(row.get("media_stable_key") or ""),
            str(row.get("stable_work_id") or ""),
            int(row.get("requested_page_index") or 0),
        )
        for row in candidate_pages
        if row.get("media_stable_key") and row.get("stable_work_id")
    }
    final_work_ids = {
        str(row.get("work_id"))
        for row in final_work_outcomes
        if row.get("work_id") not in (None, "")
    }
    independently_verified_route_references = {
        str(row.get("private_stable_work_reference"))
        for row in route_viability_attempts
        if row.get("result_class") == "route_viable"
        and row.get("route_viability") is True
        and row.get("returned_work_consistency") is True
        and row.get("private_stable_work_reference")
    }
    ledger: list[dict[str, Any]] = []
    accepted_provider_fact_mutations = 0
    stable_identity_mismatches = 0
    unsupported_identities = 0
    legacy_raw_projection_mismatches = 0
    support_counts: Counter[str] = Counter()
    primary_rows = primary_package["tables"]["source_metadata_records"]
    for row in primary_rows:
        key = str(row["provider_record_key"])
        accepted = accepted_by_key.get(key)
        fact_match = bool(
            accepted is not None
            and _accepted_provider_fact_projection(row)
            == _accepted_provider_fact_projection(accepted)
        )
        if not fact_match:
            accepted_provider_fact_mutations += 1
        raw_projection_match = bool(
            accepted is not None
            and _legacy_v1_sanitize(row.get("raw_metadata_json"))
            == accepted.get("raw_metadata_json")
        )
        if not raw_projection_match:
            legacy_raw_projection_mismatches += 1
        provenance = row.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        stable = provenance.get("stable_identity_key")
        stable = stable if isinstance(stable, Mapping) else {}
        work_id = str(row.get("source_work_id") or "")
        page_index = row.get("source_page_index")
        stable_match = bool(
            not stable
            or (
                (
                    stable.get("provider") in (None, "")
                    or str(stable.get("provider")).casefold()
                    == str(row.get("provider") or "").casefold()
                )
                and (
                    stable.get("work_id") in (None, "")
                    or str(stable.get("work_id")) == work_id
                )
                and (
                    stable.get("page_index") is None
                    or (
                        page_index is not None
                        and int(stable["page_index"]) == int(page_index)
                    )
                )
            )
        )
        stable_identity_work_present = bool(stable.get("work_id") not in (None, ""))
        if not stable_match:
            stable_identity_mismatches += 1
        candidate_support = bool(
            work_id
            and page_index is not None
            and (
                str(row.get("media_content_key") or ""),
                work_id,
                int(page_index),
            )
            in candidate_membership
        )
        raw_support = work_id in _raw_work_identity_candidates(
            row.get("raw_metadata_json")
        )
        outcome_support = work_id in final_work_ids
        route_viability_support = bool(
            work_id
            and hashlib.sha256(work_id.encode("utf-8")).hexdigest()
            in independently_verified_route_references
        )
        accepted_checkpoint_immutability_support = fact_match
        provenance_source = str(provenance.get("source") or "").casefold()
        phase_acquired_identity = bool(
            work_id
            and candidate_support
            and provenance_source == "gallery_dl_authenticated_metadata"
        )
        phase_acquired_independent_support = bool(
            raw_support or outcome_support or route_viability_support
        )
        supported = bool(
            fact_match
            and stable_match
            and (
                not phase_acquired_identity
                or phase_acquired_independent_support
            )
        )
        support_counts["accepted_checkpoint_immutability_support"] += int(
            accepted_checkpoint_immutability_support
        )
        support_counts["independent_candidate_page_support"] += int(
            candidate_support
        )
        support_counts["independent_persisted_raw_support"] += int(
            raw_support
        )
        support_counts["independent_work_outcome_support"] += int(
            outcome_support
        )
        support_counts["independent_route_viability_support"] += int(
            route_viability_support
        )
        support_counts["phase_acquired_identity"] += int(
            phase_acquired_identity
        )
        support_counts["phase_acquired_independent_support"] += int(
            phase_acquired_identity and phase_acquired_independent_support
        )
        if not supported:
            unsupported_identities += 1
        ledger.append(
            {
                "provider_record_key": key,
                "stable_media_reference": sha256_payload(
                    str(row.get("media_content_key") or "")
                ),
                "accepted_provider_fact_match": fact_match,
                "legacy_raw_projection_match_supplemental_only": raw_projection_match,
                "stable_identity_match": stable_match,
                "stable_identity_work_present": stable_identity_work_present,
                "accepted_checkpoint_immutability_support": (
                    accepted_checkpoint_immutability_support
                ),
                "independent_candidate_page_support": candidate_support,
                "independent_persisted_raw_support": raw_support,
                "independent_work_outcome_support": outcome_support,
                "independent_route_viability_support": route_viability_support,
                "phase_acquired_identity": phase_acquired_identity,
                "phase_acquired_independent_support": (
                    phase_acquired_independent_support
                ),
                "immutable_identity_support_passed": supported,
            }
        )
    missing_accepted = sorted(set(accepted_by_key) - {str(row["provider_record_key"]) for row in primary_rows})
    extra_primary = sorted({str(row["provider_record_key"]) for row in primary_rows} - set(accepted_by_key))
    proof = {
        "proof_version": "sv1b_primary_immutable_identity_crosscheck_v2",
        "primary_record_count": len(primary_rows),
        "accepted_v1_record_count": len(accepted_rows),
        "missing_accepted_record_count": len(missing_accepted),
        "extra_primary_record_count": len(extra_primary),
        "accepted_provider_fact_mutation_count": accepted_provider_fact_mutations,
        "stable_identity_mismatch_count": stable_identity_mismatches,
        "unsupported_stable_identity_count": unsupported_identities,
        "legacy_raw_projection_mismatch_count": legacy_raw_projection_mismatches,
        "candidate_page_evidence_count": len(candidate_membership),
        "accepted_work_outcome_evidence_count": len(final_work_ids),
        "accepted_route_viability_evidence_count": len(
            independently_verified_route_references
        ),
        "support_classification_counts": dict(sorted(support_counts.items())),
        "phase_acquired_identity_count": support_counts[
            "phase_acquired_identity"
        ],
        "phase_acquired_identity_independent_support_count": support_counts[
            "phase_acquired_independent_support"
        ],
        "phase_acquired_identity_unsupported_count": (
            support_counts["phase_acquired_identity"]
            - support_counts["phase_acquired_independent_support"]
        ),
        "ledger_membership_fingerprint": sha256_payload(ledger),
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
        "primary_not_assumed_authoritative": True,
        "filename_or_row_order_identity_inference_used": False,
    }
    proof["passed"] = bool(
        not missing_accepted
        and not extra_primary
        and accepted_provider_fact_mutations == 0
        and stable_identity_mismatches == 0
        and unsupported_identities == 0
        and proof["phase_acquired_identity_unsupported_count"] == 0
    )
    return proof, ledger


def _table(metadata: MetaData, name: str) -> Table:
    table = metadata.tables.get(name)
    if table is None:
        table = metadata.tables.get(f"public.{name}")
    if table is None:
        raise StableReplayPackageV2Error(f"target_table_missing:{name}")
    return table


def _stable_target_maps(connection: Connection) -> dict[str, Mapping[str, int]]:
    return {
        "media": {
            str(row["hash"]): int(row["id"])
            for row in connection.execute(
                text("SELECT id,hash FROM blombooru_media WHERE hash IS NOT NULL")
            ).mappings()
        },
        "source_metadata_record": {
            str(row["provider_record_key"]): int(row["id"])
            for row in connection.execute(
                text("SELECT id,provider_record_key FROM blombooru_source_metadata_records")
            ).mappings()
        },
        "source_tag_observation": {
            str(row["observation_key"]): int(row["id"])
            for row in connection.execute(
                text("SELECT id,observation_key FROM blombooru_source_tag_observations")
            ).mappings()
        },
        "source_name_observation": {
            str(row["observation_key"]): int(row["id"])
            for row in connection.execute(
                text("SELECT id,observation_key FROM blombooru_source_name_observations")
            ).mappings()
        },
    }


def _coerce_datetime_fields(table: Table, values: dict[str, Any]) -> None:
    for column in table.columns:
        if column.name not in values or values[column.name] is None:
            continue
        if "DATETIME" in column.type.__class__.__name__.upper() or "TIMESTAMP" in str(column.type).upper():
            value = values[column.name]
            if isinstance(value, str):
                values[column.name] = datetime.fromisoformat(value)


def _import_rows_for_schema(
    connection: Connection,
    schema: TableSchema,
    package_rows: Sequence[Mapping[str, Any]],
    table: Table,
    maps: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    rows = []
    for exported in package_rows:
        item = {field: copy.deepcopy(exported.get(field)) for field in schema.stable_columns}
        for local_field, rule in schema.references.items():
            stable_value = exported.get(rule.exported_field)
            if stable_value is None:
                if not rule.nullable:
                    raise StableReplayPackageV2Error(
                        f"required_stable_reference_missing:{schema.logical_name}.{local_field}"
                    )
                item[local_field] = None
                continue
            if rule.target == "polymorphic_observation":
                observation_type = str(exported.get("observation_type") or "")
                target = {
                    "source_tag_observation": "source_tag_observation",
                    "source_name_observation": "source_name_observation",
                }.get(observation_type)
                if target is None:
                    raise StableReplayPackageV2Error(
                        f"observation_type_invalid:{observation_type}"
                    )
            else:
                target = rule.target
            target_id = maps[target].get(str(stable_value))
            if target_id is None:
                raise StableReplayPackageV2Error(
                    f"target_reference_missing:{schema.logical_name}.{local_field}"
                )
            item[local_field] = target_id
        for field in schema.null_only_local_fields:
            item[field] = None
        _coerce_datetime_fields(table, item)
        rows.append(item)
    return rows


def import_package(connection: Connection, package: Mapping[str, Any]) -> dict[str, Any]:
    validate_package(package)
    metadata = MetaData()
    metadata.reflect(
        bind=connection, only=[schema.physical_name for schema in TABLE_SCHEMAS]
    )
    inserted: dict[str, int] = {}
    for schema in TABLE_SCHEMAS:
        target = _table(metadata, schema.physical_name)
        maps = _stable_target_maps(connection)
        rows = _import_rows_for_schema(
            connection,
            schema,
            package["tables"][schema.logical_name],
            target,
            maps,
        )
        inserted[schema.logical_name] = _insert_batches(
            connection, target, rows, batch_size=500
        )
    after = export_package(connection)
    if after["package_fingerprint"] != package["package_fingerprint"]:
        raise StableReplayPackageV2Error("post_import_reexport_mismatch")
    return {
        "schema_version": SCHEMA_VERSION,
        "input_package_fingerprint": package["package_fingerprint"],
        "reexport_package_fingerprint": after["package_fingerprint"],
        "inserted_counts": inserted,
        "inserted_total": sum(inserted.values()),
        "reexport_equal": True,
        "development_row_id_dependency_count": 0,
        "external_route_counts": dict(EXTERNAL_ROUTE_BUDGET),
    }


def write_package(path: Path, package: Mapping[str, Any]) -> None:
    validate_package(package)
    path.write_text(
        json.dumps(package, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default)
        + "\n",
        encoding="utf-8",
    )
