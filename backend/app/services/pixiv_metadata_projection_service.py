"""Deterministic Pixiv source-layer aggregate and SourceConcept projection.

PX1 deliberately stops at public-safe, database-neutral signal drafts.  The
module reads the existing Pixiv ingestion/source-layer authority; it does not
call a provider, persist SourceConcept rows, or promote Entity truth.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from ..models import SourceMetadataRecord, SourceNameObservation, SourceTagObservation
from .creator_identity_policy import stable_creator_identity_key
from .pixiv_metadata_ingestion_service import (
    PIXIV_METADATA_NORMALIZER_VERSION,
    QUERY_VISIBLE_OBSERVATION_STATUSES,
    PixivMetadataState,
    is_pixiv_creator_observation_compatible_with_parent,
    is_trusted_complete_pixiv_metadata_record,
)
from .source_concept_resolver_service import (
    SourceConceptSignalDraft,
    SourceConceptSignalInput,
    build_source_concept_signal_drafts,
    parse_parenthetical,
    role_from_source_tag_category,
)
from .source_metadata_registry_service import canonical_source_key, normalize_source_text


PIXIV_AGGREGATE_SCHEMA = "violet.scv2-px1-pixiv-work-page-aggregate.v1"
PIXIV_SIGNAL_BUNDLE_SCHEMA = "violet.scv2-px1-source-concept-signal-bundle.v1"
PIXIV_PUBLIC_SUMMARY_SCHEMA = "violet.scv2-px1-pixiv-metadata-summary.v1"
PIXIV_AGGREGATE_VERSION = "scv2_px1_pixiv_aggregate_v1"

_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s\"'])(?:[a-z]:[\\/]|\\\\)")
_POSIX_PRIVATE_PATH = re.compile(r"(?:^|[\s\"'])(?:/users/|/home/|/private/|/mnt/)", re.I)
_SECRET_MARKER = re.compile(
    r"(?i)(?:authorization\s*[:=]|set-cookie\s*[:=]|cookie\s*[:=]|"
    r"bearer\s+\S+|api[_-]?key\s*[:=]|refresh[_-]?token\s*[:=]|"
    r"access[_-]?token\s*[:=]|password\s*[:=])"
)


class PixivMetadataProjectionError(ValueError):
    """Raised when source-layer inputs cannot form a safe Pixiv projection."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_pixiv_work_page_key(work_id: str, page_index: int) -> str:
    work = str(work_id or "").strip()
    if re.fullmatch(r"[1-9]\d{6,11}", work) is None:
        raise PixivMetadataProjectionError("pixiv_work_id_invalid")
    try:
        page = int(page_index)
    except (TypeError, ValueError) as exc:
        raise PixivMetadataProjectionError("pixiv_page_index_invalid") from exc
    if page < 0:
        raise PixivMetadataProjectionError("pixiv_page_index_invalid")
    return f"pixiv:work:{work}:page:{page}"


def _value(item: Any, field: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(field)
    return getattr(item, field, None)


def _mapping(item: Any, field: str) -> Mapping[str, Any]:
    value = _value(item, field)
    return value if isinstance(value, Mapping) else {}


def _safe_observation_text(value: Any) -> tuple[str | None, bool]:
    text = normalize_source_text(value)
    if not text:
        return None, False
    if (
        "\x00" in text
        or _WINDOWS_PATH.search(text)
        or _POSIX_PRIVATE_PATH.search(text)
        or _SECRET_MARKER.search(text)
    ):
        return None, True
    return text, False


def assert_public_safe_projection(value: Any) -> None:
    """Reject a completed public projection containing private or raw material."""

    serialized = canonical_json_bytes(value).decode("utf-8")
    lowered = serialized.casefold()
    forbidden_keys = (
        '"raw_metadata_json"',
        '"raw_provider_payload"',
        '"raw"',
        '"source_url"',
        '"local_path"',
        '"filename"',
        '"credential"',
    )
    if any(key in lowered for key in forbidden_keys):
        raise PixivMetadataProjectionError("public_projection_forbidden_field")
    if (
        "\x00" in serialized
        or _WINDOWS_PATH.search(serialized)
        or _POSIX_PRIVATE_PATH.search(serialized)
        or _SECRET_MARKER.search(serialized)
    ):
        raise PixivMetadataProjectionError("public_projection_private_text")


def _allowlisted_provenance(record: Any) -> dict[str, Any]:
    provenance = _mapping(record, "provenance")
    stable = provenance.get("stable_identity_key")
    stable = stable if isinstance(stable, Mapping) else {}
    return {
        "source": normalize_source_text(provenance.get("source")) or None,
        "parser_version": normalize_source_text(provenance.get("parser_version")) or None,
        "metadata_normalizer_version": (
            normalize_source_text(provenance.get("metadata_normalizer_version"))
            or None
        ),
        "stable_identity_key": {
            "provider": normalize_source_text(stable.get("provider")).casefold() or None,
            "work_id": str(stable.get("work_id") or "") or None,
            "page_index": (
                int(stable["page_index"])
                if stable.get("page_index") is not None
                else None
            ),
        },
        "source_record_fingerprint": (
            str(provenance.get("source_record_fingerprint") or "") or None
        ),
    }


def _page_count(record: Any) -> int | None:
    raw = _mapping(record, "raw_metadata_json")
    candidate = raw.get("page_count", raw.get("count", raw.get("num_pages")))
    if candidate in (None, ""):
        return None
    try:
        count = int(candidate)
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


def _record_business_projection(record: Any) -> dict[str, Any]:
    raw = _mapping(record, "raw_metadata_json")
    user = raw.get("user") if isinstance(raw.get("user"), Mapping) else {}
    account, account_redacted = _safe_observation_text(
        raw.get("creator_account")
        or raw.get("user_account")
        or raw.get("artist_account")
        or user.get("account")
    )
    title, title_redacted = _safe_observation_text(_value(record, "title"))
    artist_name, artist_redacted = _safe_observation_text(_value(record, "artist_name"))
    return {
        "projection_version": PIXIV_AGGREGATE_VERSION,
        "normalizer_version": (
            normalize_source_text(raw.get("_pixiv_metadata_normalizer_version"))
            or PIXIV_METADATA_NORMALIZER_VERSION
        ),
        "provider": normalize_source_text(_value(record, "provider")).casefold(),
        "work_id": str(_value(record, "source_work_id") or ""),
        "page_index": _value(record, "source_page_index"),
        "page_count": _page_count(record),
        "metadata_kind": normalize_source_text(_value(record, "metadata_kind")),
        "data_type_label": normalize_source_text(_value(record, "data_type_label")),
        "status": normalize_source_text(_value(record, "status")),
        "title": title,
        "provider_creator_id": str(_value(record, "artist_id") or "") or None,
        "creator_display_name": artist_name,
        "creator_account": account,
        "redacted_observation_count": sum(
            (title_redacted, artist_redacted, account_redacted)
        ),
        "provenance": _allowlisted_provenance(record),
    }


def _record_token(record: Any, fallback: int) -> str:
    explicit = _value(record, "record_token")
    if explicit not in (None, ""):
        return str(explicit)
    row_id = _value(record, "id")
    return f"row:{row_id}" if row_id is not None else f"input:{fallback}"


def _observation_parent_token(observation: Any) -> str | None:
    explicit = _value(observation, "parent_record_token")
    if explicit not in (None, ""):
        return str(explicit)
    row_id = _value(observation, "source_metadata_record_id")
    return f"row:{row_id}" if row_id is not None else None


def _observation_projection(
    *,
    value: str,
    source_field: str,
    creator_id: str | None,
    source_fingerprint: str,
    category: str | None = None,
) -> dict[str, Any]:
    result = {
        "value": value,
        "canonical_key": canonical_source_key(value),
        "source_field": source_field,
        "source_fingerprint": source_fingerprint,
    }
    if creator_id:
        result["provider_creator_id"] = creator_id
    if category:
        result["source_category"] = category
    return result


def _dedupe_observations(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[bytes, dict[str, Any]] = {}
    for item in items:
        projected = dict(item)
        by_key[canonical_json_bytes(projected)] = projected
    return [by_key[key] for key in sorted(by_key)]


_STATUS_REASON = {
    PixivMetadataState.CANDIDATE_DETECTED.value: "metadata_candidate_only",
    PixivMetadataState.PENDING.value: "metadata_pending",
    PixivMetadataState.RETRYABLE.value: "metadata_retryable",
    PixivMetadataState.TERMINAL.value: "terminal_remote_unavailable",
    PixivMetadataState.CONFLICT.value: "filename_identity_conflict",
    PixivMetadataState.NORMALIZATION_FAILED.value: "normalization_failed",
    PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value: "provider_identity_mismatch",
    PixivMetadataState.DEFERRED_PAGE_MISMATCH.value: "source_page_mismatch_deferred",
}


def build_canonical_pixiv_work_page_aggregate(
    records: Sequence[Any],
    *,
    name_observations: Sequence[Any] = (),
    tag_observations: Sequence[Any] = (),
    known_page_indexes: Sequence[int] = (),
) -> dict[str, Any]:
    """Build one canonical aggregate from existing source-layer rows.

    Numeric row IDs and provider-record keys may correlate rows inside this
    function, but they never enter the business projection or fingerprint.
    """

    if not records:
        raise PixivMetadataProjectionError("pixiv_source_records_missing")

    record_entries: list[dict[str, Any]] = []
    work_pages: set[tuple[str, int]] = set()
    tokens: set[str] = set()
    for index, record in enumerate(records):
        provider = normalize_source_text(_value(record, "provider")).casefold()
        if provider != "pixiv":
            raise PixivMetadataProjectionError("pixiv_source_provider_invalid")
        work_id = str(_value(record, "source_work_id") or "").strip()
        try:
            page_index = int(_value(record, "source_page_index"))
        except (TypeError, ValueError) as exc:
            raise PixivMetadataProjectionError("pixiv_page_index_invalid") from exc
        stable_pixiv_work_page_key(work_id, page_index)
        work_pages.add((work_id, page_index))
        projection = _record_business_projection(record)
        fingerprint = canonical_fingerprint(projection)
        token = _record_token(record, index)
        if token in tokens:
            raise PixivMetadataProjectionError("pixiv_record_token_duplicate")
        tokens.add(token)
        record_entries.append(
            {
                "record": record,
                "token": token,
                "projection": projection,
                "fingerprint": fingerprint,
                "trusted": is_trusted_complete_pixiv_metadata_record(record),
            }
        )
    if len(work_pages) != 1:
        raise PixivMetadataProjectionError("pixiv_work_page_scope_mixed")
    work_id, page_index = next(iter(work_pages))
    stable_key = stable_pixiv_work_page_key(work_id, page_index)
    entry_by_token = {entry["token"]: entry for entry in record_entries}

    conflict_reasons: set[str] = set()
    deferred_reasons: set[str] = set()
    redacted_count = sum(
        int(entry["projection"]["redacted_observation_count"])
        for entry in record_entries
    )
    lifecycle_states = sorted(
        {str(entry["projection"]["status"]) for entry in record_entries}
    )
    for state in lifecycle_states:
        reason = _STATUS_REASON.get(state)
        if not reason:
            continue
        if state in {
            PixivMetadataState.CONFLICT.value,
            PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value,
        }:
            conflict_reasons.add(reason)
        else:
            deferred_reasons.add(reason)

    trusted_entries = [entry for entry in record_entries if entry["trusted"]]
    creator_ids = sorted(
        {
            str(entry["projection"]["provider_creator_id"])
            for entry in trusted_entries
            if entry["projection"]["provider_creator_id"]
        }
    )
    if len(creator_ids) > 1:
        conflict_reasons.add("conflicting_provider_creator_ids")

    display_observations: list[dict[str, Any]] = []
    account_observations: list[dict[str, Any]] = []
    title_observations: list[dict[str, Any]] = []
    tag_values: list[dict[str, Any]] = []

    for entry in trusted_entries:
        source_fingerprint = entry["fingerprint"]
        creator_id = entry["projection"]["provider_creator_id"]
        if entry["projection"]["creator_display_name"]:
            display_observations.append(
                _observation_projection(
                    value=entry["projection"]["creator_display_name"],
                    source_field="source_metadata_record.artist_name",
                    creator_id=creator_id,
                    source_fingerprint=source_fingerprint,
                )
            )
        if entry["projection"]["creator_account"]:
            account_observations.append(
                _observation_projection(
                    value=entry["projection"]["creator_account"],
                    source_field="source_metadata_record.creator_account",
                    creator_id=creator_id,
                    source_fingerprint=source_fingerprint,
                )
            )
        if entry["projection"]["title"]:
            title_observations.append(
                _observation_projection(
                    value=entry["projection"]["title"],
                    source_field="source_metadata_record.title",
                    creator_id=None,
                    source_fingerprint=source_fingerprint,
                )
            )

    for observation in name_observations:
        parent_token = _observation_parent_token(observation)
        parent = entry_by_token.get(parent_token or "")
        if parent is None:
            deferred_reasons.add("orphan_name_observation")
            continue
        value, redacted = _safe_observation_text(_value(observation, "raw_name"))
        redacted_count += int(redacted)
        if not value:
            continue
        source_field = normalize_source_text(_value(observation, "source_field"))
        status = normalize_source_text(_value(observation, "status"))
        if status not in QUERY_VISIBLE_OBSERVATION_STATUSES or not parent["trusted"]:
            deferred_reasons.add("untrusted_or_nonvisible_name_observation")
            continue
        creator_id = parent["projection"]["provider_creator_id"]
        if source_field in {"pixiv_user_metadata", "pixiv_user_account"}:
            if not is_pixiv_creator_observation_compatible_with_parent(
                observation, parent["record"]
            ):
                deferred_reasons.add("creator_observation_parent_incompatible")
                continue
            projected = _observation_projection(
                value=value,
                source_field=source_field,
                creator_id=creator_id,
                source_fingerprint=parent["fingerprint"],
            )
            target = (
                account_observations
                if source_field == "pixiv_user_account"
                else display_observations
            )
            target.append(projected)
        elif source_field == "pixiv_title":
            title_observations.append(
                _observation_projection(
                    value=value,
                    source_field=source_field,
                    creator_id=None,
                    source_fingerprint=parent["fingerprint"],
                )
            )

    for observation in tag_observations:
        parent_token = _observation_parent_token(observation)
        parent = entry_by_token.get(parent_token or "")
        if parent is None:
            deferred_reasons.add("orphan_tag_observation")
            continue
        value, redacted = _safe_observation_text(_value(observation, "raw_tag"))
        redacted_count += int(redacted)
        if not value:
            continue
        if (
            not parent["trusted"]
            or normalize_source_text(_value(observation, "provider")).casefold()
            != "pixiv"
            or normalize_source_text(_value(observation, "status"))
            not in QUERY_VISIBLE_OBSERVATION_STATUSES
        ):
            deferred_reasons.add("untrusted_or_nonvisible_tag_observation")
            continue
        tag_values.append(
            _observation_projection(
                value=value,
                source_field=(
                    normalize_source_text(_value(observation, "source_tag_kind"))
                    or "provider_tag"
                ),
                creator_id=None,
                source_fingerprint=parent["fingerprint"],
                category=(
                    normalize_source_text(_value(observation, "source_category_raw"))
                    or None
                ),
            )
        )

    display_observations = _dedupe_observations(display_observations)
    account_observations = _dedupe_observations(account_observations)
    title_observations = _dedupe_observations(title_observations)
    tag_values = _dedupe_observations(tag_values)
    title_values = sorted({item["value"] for item in title_observations})
    if len(title_values) > 1:
        conflict_reasons.add("conflicting_title_observations")

    page_counts = sorted(
        {
            int(entry["projection"]["page_count"])
            for entry in record_entries
            if entry["projection"]["page_count"] is not None
        }
    )
    if len(page_counts) > 1:
        conflict_reasons.add("conflicting_page_count_observations")
    known_scope = sorted({int(value) for value in known_page_indexes} | {page_index})
    if any(value < 0 for value in known_scope):
        raise PixivMetadataProjectionError("pixiv_known_page_scope_invalid")

    if conflict_reasons:
        disposition = "conflict"
    elif trusted_entries:
        disposition = "complete"
    elif PixivMetadataState.TERMINAL.value in lifecycle_states:
        disposition = "terminal"
    elif PixivMetadataState.DEFERRED_PAGE_MISMATCH.value in lifecycle_states:
        disposition = "page_mismatch"
    elif PixivMetadataState.RETRYABLE.value in lifecycle_states:
        disposition = "retryable"
    elif PixivMetadataState.NORMALIZATION_FAILED.value in lifecycle_states:
        disposition = "unsupported"
    else:
        disposition = "incomplete"

    missing_fields: list[str] = []
    if len(creator_ids) != 1:
        missing_fields.append("stable_creator_identity")
    if not title_values:
        missing_fields.append("title_observation")
    if not tag_values:
        missing_fields.append("tag_observations")
    if not trusted_entries:
        missing_fields.append("trusted_complete_source_metadata")
    optional_complete = bool(title_values and tag_values)
    completeness = {
        "classification": (
            "complete"
            if disposition == "complete" and len(creator_ids) == 1 and optional_complete
            else "partial_or_uncertain"
        ),
        "trusted_complete_source": bool(trusted_entries),
        "stable_creator_identity_present": len(creator_ids) == 1,
        "title_present": bool(title_values),
        "tags_present": bool(tag_values),
        "missing_fields": missing_fields,
        "redacted_observation_count": redacted_count,
    }
    if redacted_count:
        deferred_reasons.add("unsafe_observation_redacted")

    aggregate: dict[str, Any] = {
        "schema_version": PIXIV_AGGREGATE_SCHEMA,
        "provider": "pixiv",
        "work_id": work_id,
        "page_index": page_index,
        "page_count_or_known_scope": {
            "page_count": page_counts[0] if len(page_counts) == 1 else None,
            "known_page_indexes": known_scope,
            "scope_kind": (
                "provider_page_count" if len(page_counts) == 1 else "known_page_set"
            ),
        },
        "stable_work_page_key": stable_key,
        "creator": {
            "provider_creator_id": creator_ids[0] if len(creator_ids) == 1 else None,
            "conflicting_provider_creator_ids": creator_ids if len(creator_ids) > 1 else [],
            "account_observations": account_observations,
            "display_name_observations": display_observations,
        },
        "title_observation": {
            "value": title_values[0] if len(title_values) == 1 else None,
            "observations": title_observations,
        },
        "tag_observations": tag_values,
        "source_fingerprints": sorted(
            {entry["fingerprint"] for entry in record_entries}
        ),
        "provenance_fingerprints": sorted(
            {
                canonical_fingerprint(entry["projection"]["provenance"])
                for entry in record_entries
            }
        ),
        "metadata_completeness": completeness,
        "lifecycle": {
            "states": lifecycle_states,
            "trusted_complete_record_count": len(trusted_entries),
            "source_record_count": len(record_entries),
        },
        "disposition": disposition,
        "conflict_reasons": sorted(conflict_reasons),
        "deferred_reasons": sorted(deferred_reasons),
    }
    aggregate["canonical_fingerprint"] = canonical_fingerprint(aggregate)
    assert_public_safe_projection(aggregate)
    return aggregate


def _signal_origin_key(*parts: Any) -> str:
    return canonical_fingerprint([str(part or "") for part in parts])


def _public_signal(signal: SourceConceptSignalDraft) -> dict[str, Any]:
    identity_anchor = (
        stable_creator_identity_key(signal) if signal.role_hint == "artist" else None
    )
    return {
        "signal_key": signal.signal_key,
        "origin_type": signal.origin_type,
        "provider": signal.provider,
        "source_record_id": signal.source_record_id,
        "raw_value": signal.raw_value,
        "display_value": signal.display_value,
        "normalized_key": signal.normalized_key,
        "canonical_key": signal.canonical_key,
        "role_hint": signal.role_hint,
        "work_context_key": signal.work_context_key,
        "parenthetical_base": signal.parenthetical_base,
        "parenthetical_context": signal.parenthetical_context,
        "source_kind": signal.source_kind,
        "trust_tier": signal.trust_tier,
        "confidence": signal.confidence,
        "status": signal.status,
        "identity_anchor": identity_anchor,
        "evidence": dict(signal.evidence_payload),
    }


def project_pixiv_aggregate_to_source_concept_signals(
    aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one aggregate into existing resolver signal drafts only."""

    if aggregate.get("schema_version") != PIXIV_AGGREGATE_SCHEMA:
        raise PixivMetadataProjectionError("pixiv_aggregate_schema_invalid")
    expected = dict(aggregate)
    supplied_fingerprint = str(expected.pop("canonical_fingerprint", ""))
    if supplied_fingerprint != canonical_fingerprint(expected):
        raise PixivMetadataProjectionError("pixiv_aggregate_fingerprint_invalid")
    stable_key = str(aggregate["stable_work_page_key"])
    creator = aggregate.get("creator")
    creator = creator if isinstance(creator, Mapping) else {}
    creator_id = str(creator.get("provider_creator_id") or "") or None
    creator_conflict = bool(creator.get("conflicting_provider_creator_ids"))
    context_key = stable_key
    inputs: list[SourceConceptSignalInput] = []

    for field_name, observations in (
        ("display_name", creator.get("display_name_observations") or []),
        ("account", creator.get("account_observations") or []),
    ):
        for observation in observations:
            value = str(observation.get("value") or "")
            stable_id = None if creator_conflict else creator_id
            inputs.append(
                SourceConceptSignalInput(
                    origin_type="pixiv_creator_observation",
                    origin_key=_signal_origin_key(
                        stable_key, field_name, observation.get("canonical_key"), stable_id
                    ),
                    provider="pixiv",
                    raw_value=value,
                    display_value=value,
                    canonical_value=str(observation.get("canonical_key") or value),
                    role_hint="artist",
                    work_context_key=None,
                    source_kind=f"pixiv_creator_{field_name}",
                    trust_tier="medium" if stable_id else "weak",
                    confidence=None,
                    status="needs_review",
                    evidence_payload={
                        "stable_creator_id": stable_id,
                        "observation_kind": field_name,
                        "mutable_observation": True,
                        "aggregate_fingerprint": supplied_fingerprint,
                        "work_id": aggregate["work_id"],
                        "page_index": aggregate["page_index"],
                        "creator_conflict": creator_conflict,
                    },
                    source_record_id=str(observation.get("source_fingerprint") or ""),
                )
            )

    title = aggregate.get("title_observation")
    title = title if isinstance(title, Mapping) else {}
    for observation in title.get("observations") or []:
        value = str(observation.get("value") or "")
        inputs.append(
            SourceConceptSignalInput(
                origin_type="pixiv_title_observation",
                origin_key=_signal_origin_key(
                    stable_key, "title", observation.get("canonical_key")
                ),
                provider="pixiv",
                raw_value=value,
                display_value=value,
                canonical_value=str(observation.get("canonical_key") or value),
                role_hint="source_title",
                work_context_key=context_key,
                source_kind="pixiv_title",
                trust_tier="weak",
                confidence=None,
                status="needs_review",
                evidence_payload={
                    "aggregate_fingerprint": supplied_fingerprint,
                    "work_id": aggregate["work_id"],
                    "page_index": aggregate["page_index"],
                    "context_bound": True,
                },
                source_record_id=str(observation.get("source_fingerprint") or ""),
            )
        )

    for observation in aggregate.get("tag_observations") or []:
        value = str(observation.get("value") or "")
        role = role_from_source_tag_category(observation.get("source_category"))
        parenthetical_base, parenthetical_context = parse_parenthetical(value)
        if role == "unknown" and parenthetical_base:
            role = "character"
        inputs.append(
            SourceConceptSignalInput(
                origin_type="pixiv_tag_observation",
                origin_key=_signal_origin_key(
                    stable_key,
                    "tag",
                    observation.get("canonical_key"),
                    observation.get("source_category"),
                ),
                provider="pixiv",
                raw_value=value,
                display_value=value,
                canonical_value=str(observation.get("canonical_key") or value),
                role_hint=role,
                work_context_key=context_key,
                source_kind=str(observation.get("source_field") or "provider_tag"),
                trust_tier="medium" if role != "unknown" else "weak",
                confidence=None,
                status="needs_review",
                evidence_payload={
                    "aggregate_fingerprint": supplied_fingerprint,
                    "work_id": aggregate["work_id"],
                    "page_index": aggregate["page_index"],
                    "context_bound": True,
                    "source_category": observation.get("source_category"),
                },
                source_record_id=str(observation.get("source_fingerprint") or ""),
                parenthetical_base=parenthetical_base,
                parenthetical_context=parenthetical_context,
            )
        )

    drafts = build_source_concept_signal_drafts(inputs)
    public_signals = [_public_signal(signal) for signal in drafts]
    bundle: dict[str, Any] = {
        "schema_version": PIXIV_SIGNAL_BUNDLE_SCHEMA,
        "provider": "pixiv",
        "stable_work_page_key": stable_key,
        "aggregate_fingerprint": supplied_fingerprint,
        "signals": public_signals,
        "signal_count": len(public_signals),
        "logical_keys": sorted(signal["signal_key"] for signal in public_signals),
        "strong_identity_anchor_count": sum(
            1 for signal in public_signals if signal["identity_anchor"]
        ),
        "name_only_identity_anchor_count": sum(
            1
            for signal in public_signals
            if signal["role_hint"] == "artist"
            and not creator_id
            and signal["identity_anchor"]
        ),
        "cross_context_union_count": 0,
        "cluster_materialization_performed": False,
        "entity_truth_promoted": False,
    }
    bundle["canonical_fingerprint"] = canonical_fingerprint(bundle)
    assert_public_safe_projection(bundle)
    return bundle


def build_canonical_pixiv_aggregates_from_session(
    session: Session,
) -> tuple[dict[str, Any], ...]:
    """Read Pixiv source rows and build aggregates without writing the session."""

    records = (
        session.query(SourceMetadataRecord)
        .filter(SourceMetadataRecord.provider == "pixiv")
        .all()
    )
    grouped: dict[tuple[str, int], list[SourceMetadataRecord]] = defaultdict(list)
    for record in records:
        if not record.source_work_id or record.source_page_index is None:
            raise PixivMetadataProjectionError("pixiv_source_record_identity_incomplete")
        grouped[(str(record.source_work_id), int(record.source_page_index))].append(record)

    names_by_record: dict[int, list[SourceNameObservation]] = defaultdict(list)
    tags_by_record: dict[int, list[SourceTagObservation]] = defaultdict(list)
    record_ids = [int(record.id) for record in records]
    if record_ids:
        for observation in (
            session.query(SourceNameObservation)
            .filter(SourceNameObservation.source_metadata_record_id.in_(record_ids))
            .all()
        ):
            names_by_record[int(observation.source_metadata_record_id)].append(observation)
        for observation in (
            session.query(SourceTagObservation)
            .filter(SourceTagObservation.source_metadata_record_id.in_(record_ids))
            .all()
        ):
            tags_by_record[int(observation.source_metadata_record_id)].append(observation)

    known_pages_by_work: dict[str, list[int]] = defaultdict(list)
    for work_id, page_index in grouped:
        known_pages_by_work[work_id].append(page_index)

    aggregates: list[dict[str, Any]] = []
    for (work_id, page_index), rows in sorted(grouped.items()):
        names = [item for row in rows for item in names_by_record[int(row.id)]]
        tags = [item for row in rows for item in tags_by_record[int(row.id)]]
        aggregates.append(
            build_canonical_pixiv_work_page_aggregate(
                rows,
                name_observations=names,
                tag_observations=tags,
                known_page_indexes=known_pages_by_work[work_id],
            )
        )
    return tuple(aggregates)


def summarize_pixiv_aggregate_dispositions(
    aggregates: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    counts = Counter(str(item.get("disposition") or "unknown") for item in aggregates)
    return dict(sorted(counts.items()))
