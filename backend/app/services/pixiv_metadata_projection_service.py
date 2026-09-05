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
from .pixiv_identity_policy import (
    canonical_pixiv_creator_id,
    canonical_pixiv_page_count,
    canonical_pixiv_page_domain,
    canonical_pixiv_page_index,
    canonical_pixiv_work_id,
)
from .pixiv_metadata_ingestion_service import (
    PIXIV_LEGACY_NORMALIZER_VERSION,
    PIXIV_METADATA_NORMALIZER_VERSION,
    QUERY_VISIBLE_OBSERVATION_STATUSES,
    PixivMetadataState,
    is_pixiv_creator_observation_compatible_with_parent,
    is_trusted_complete_pixiv_metadata_record,
    stable_pixiv_source_record_fingerprint,
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
PIXIV_AGGREGATE_VERSION = "scv2_px1_pixiv_aggregate_v2"
_CURRENT_V2_PROVENANCE_SOURCES = frozenset(
    {
        "gallery_dl_authenticated_metadata",
        "compatible_complete_record_reuse",
    }
)

_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s\"'=:(\[])(?:[a-z]:[\\/]|\\\\)")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'=:(\[])/(?!/)")
_FILE_URI = re.compile(r"(?i)\bfile:(?://|\\\\)")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SECRET_MARKER = re.compile(
    r"(?i)(?:authorization\s*[:=]|set-cookie\s*[:=]|cookie\s*[:=]|"
    r"bearer\s+\S+|api[_-]?key\s*[:=]|client[_-]?secret\s*[:=]|"
    r"refresh[_-]?token\s*[:=]|access[_-]?token\s*[:=]|"
    r"password\s*[:=]|credential(?:s)?\s*[:=]|secret\s*[:=])"
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
    work = canonical_pixiv_work_id(work_id)
    if work is None:
        raise PixivMetadataProjectionError("pixiv_work_id_invalid")
    page_domain = canonical_pixiv_page_domain(page_index=page_index)
    if page_domain is None:
        raise PixivMetadataProjectionError("pixiv_page_index_invalid")
    page, _page_count = page_domain
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
        or _POSIX_ABSOLUTE_PATH.search(text)
        or _FILE_URI.search(text)
        or _SECRET_MARKER.search(text)
    ):
        return None, True
    return text, False


def assert_public_safe_projection(value: Any) -> None:
    """Reject a completed public projection containing private or raw material."""

    canonical_json_bytes(value)
    forbidden_keys = frozenset(
        {
            "raw_metadata_json",
            "raw_provider_payload",
            "raw",
            "source_url",
            "local_path",
            "filename",
            "credential",
            "credentials",
            "authorization",
            "client_secret",
            "password",
            "cookie",
            "access_token",
            "refresh_token",
        }
    )

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if isinstance(key, str) and key.casefold() in forbidden_keys:
                    raise PixivMetadataProjectionError(
                        "public_projection_forbidden_field"
                    )
                visit(nested)
            return
        if isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
            return
        if not isinstance(item, str):
            return
        if (
            "\x00" in item
            or _WINDOWS_PATH.search(item)
            or _POSIX_ABSOLUTE_PATH.search(item)
            or _FILE_URI.search(item)
            or _SECRET_MARKER.search(item)
        ):
            raise PixivMetadataProjectionError("public_projection_private_text")

    visit(value)


def _allowlisted_provenance(record: Any) -> dict[str, Any]:
    provenance = _mapping(record, "provenance")
    raw = _mapping(record, "raw_metadata_json")
    stable = provenance.get("stable_identity_key")
    stable = stable if isinstance(stable, Mapping) else {}
    stable_page_index = canonical_pixiv_page_index(stable.get("page_index"))
    source_record_fingerprint = stable_pixiv_source_record_fingerprint(record)
    source, _ = _safe_observation_text(provenance.get("source"))
    parser_version, _ = _safe_observation_text(provenance.get("parser_version"))
    raw_normalizer_version = normalize_source_text(
        raw.get("_pixiv_metadata_normalizer_version")
    )
    if not raw_normalizer_version:
        normalizer_version = PIXIV_LEGACY_NORMALIZER_VERSION
    elif raw_normalizer_version == PIXIV_METADATA_NORMALIZER_VERSION:
        normalizer_version = PIXIV_METADATA_NORMALIZER_VERSION
    else:
        normalizer_version = "unsupported_unknown"
    return {
        "source": source,
        "parser_version": parser_version,
        "metadata_normalizer_version": normalizer_version,
        "stable_identity_key": {
            "provider": normalize_source_text(stable.get("provider")).casefold() or None,
            "work_id": canonical_pixiv_work_id(stable.get("work_id")),
            "page_index": stable_page_index,
        },
        "source_record_fingerprint": (
            source_record_fingerprint
            if _HEX64.fullmatch(source_record_fingerprint)
            else None
        ),
    }


def _page_count_details(record: Any) -> tuple[int | None, bool]:
    raw = _mapping(record, "raw_metadata_json")
    field = next(
        (name for name in ("page_count", "count", "num_pages") if name in raw),
        None,
    )
    if field is None:
        return None, False
    candidate = raw[field]
    page_count = canonical_pixiv_page_count(candidate)
    page_index = canonical_pixiv_page_index(_value(record, "source_page_index"))
    if page_count is None or page_index is None:
        return None, True
    if canonical_pixiv_page_domain(
        page_index=page_index, page_count=page_count
    ) is None:
        return None, True
    return page_count, False


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
    raw_creator_id = _value(record, "artist_id")
    creator_id = canonical_pixiv_creator_id(raw_creator_id)
    creator_id_invalid = raw_creator_id not in (None, "") and creator_id is None
    page_index = canonical_pixiv_page_index(_value(record, "source_page_index"))
    if page_index is None:
        raise PixivMetadataProjectionError("pixiv_page_index_invalid")
    page_count, page_count_invalid = _page_count_details(record)
    raw_normalizer_version = normalize_source_text(
        raw.get("_pixiv_metadata_normalizer_version")
    )
    if not raw_normalizer_version:
        normalizer_version = PIXIV_LEGACY_NORMALIZER_VERSION
    elif raw_normalizer_version == PIXIV_METADATA_NORMALIZER_VERSION:
        normalizer_version = PIXIV_METADATA_NORMALIZER_VERSION
    else:
        normalizer_version = "unsupported_unknown"
    return {
        "projection_version": PIXIV_AGGREGATE_VERSION,
        "normalizer_version": normalizer_version,
        "provider": normalize_source_text(_value(record, "provider")).casefold(),
        "work_id": canonical_pixiv_work_id(_value(record, "source_work_id")),
        "page_index": page_index,
        "page_count": page_count,
        "page_count_invalid": page_count_invalid,
        "metadata_kind": normalize_source_text(_value(record, "metadata_kind")),
        "data_type_label": normalize_source_text(_value(record, "data_type_label")),
        "status": normalize_source_text(_value(record, "status")),
        "title": title,
        "provider_creator_id": creator_id,
        "provider_creator_id_invalid": creator_id_invalid,
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


def _provenance_identity_matches(projection: Mapping[str, Any]) -> bool:
    provenance = projection.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    stable = provenance.get("stable_identity_key")
    stable = stable if isinstance(stable, Mapping) else {}
    return bool(
        stable.get("provider") == "pixiv"
        and stable.get("work_id") == projection.get("work_id")
        and stable.get("page_index") == projection.get("page_index")
    )


def _select_authoritative_work_fact(
    entries: Sequence[Mapping[str, Any]],
    *,
    field: str,
    current_conflict_reason: str,
    legacy_conflict_reason: str,
    legacy_mismatch_reason: str,
    conflict_reasons: set[str],
    deferred_reasons: set[str],
) -> tuple[Any, str]:
    current_values = {
        entry["projection"].get(field)
        for entry in entries
        if entry.get("authority") == "current_v2"
        and entry["projection"].get(field) is not None
    }
    legacy_values = {
        entry["projection"].get(field)
        for entry in entries
        if entry.get("authority") == "legacy_unknown"
        and entry["projection"].get(field) is not None
    }
    if len(current_values) > 1:
        conflict_reasons.add(current_conflict_reason)
        return None, "conflict"
    if len(current_values) == 1:
        selected = next(iter(current_values))
        if any(value != selected for value in legacy_values):
            deferred_reasons.add(legacy_mismatch_reason)
        return selected, "current_v2"
    if len(legacy_values) > 1:
        conflict_reasons.add(legacy_conflict_reason)
        return None, "conflict"
    if len(legacy_values) == 1:
        return next(iter(legacy_values)), "legacy_unknown"
    return None, "none"


def derive_pixiv_work_consistency(records: Sequence[Any]) -> dict[str, Any]:
    """Select deterministic work facts before any page-local aggregate exists."""

    if not records:
        raise PixivMetadataProjectionError("pixiv_source_records_missing")
    entries: list[dict[str, Any]] = []
    work_ids: set[str] = set()
    page_indexes: set[int] = set()
    conflict_reasons: set[str] = set()
    deferred_reasons: set[str] = set()
    for record in records:
        if normalize_source_text(_value(record, "provider")).casefold() != "pixiv":
            raise PixivMetadataProjectionError("pixiv_source_provider_invalid")
        work_id = canonical_pixiv_work_id(_value(record, "source_work_id"))
        if work_id is None:
            raise PixivMetadataProjectionError("pixiv_work_id_invalid")
        raw_page_index = _value(record, "source_page_index")
        stable_pixiv_work_page_key(work_id, raw_page_index)
        page_index = canonical_pixiv_page_index(raw_page_index)
        if page_index is None:  # stable_pixiv_work_page_key already rejects this.
            raise PixivMetadataProjectionError("pixiv_page_index_invalid")
        work_ids.add(work_id)
        page_indexes.add(page_index)
        projection = _record_business_projection(record)
        trusted = is_trusted_complete_pixiv_metadata_record(record)
        normalizer = projection["normalizer_version"]
        provenance_matches = _provenance_identity_matches(projection)
        provenance = projection["provenance"]
        provenance_source = provenance.get("source")
        parser_version = provenance.get("parser_version")
        current_provenance_supported = bool(
            provenance_source in _CURRENT_V2_PROVENANCE_SOURCES
            and parser_version
        )
        if (
            trusted
            and normalizer == PIXIV_METADATA_NORMALIZER_VERSION
            and provenance_matches
            and current_provenance_supported
        ):
            authority = "current_v2"
        elif trusted and normalizer == PIXIV_LEGACY_NORMALIZER_VERSION:
            authority = "legacy_unknown"
            deferred_reasons.add("legacy_unknown_provenance")
        elif normalizer == PIXIV_METADATA_NORMALIZER_VERSION and projection.get(
            "status"
        ) in {
            PixivMetadataState.COMPLETE.value,
            "observed",
            "active",
            "accepted",
        }:
            authority = "incompatible_current_v2"
            if not provenance_matches:
                conflict_reasons.add(
                    "current_v2_provenance_identity_incompatible"
                )
            if provenance_source not in _CURRENT_V2_PROVENANCE_SOURCES:
                conflict_reasons.add("current_v2_provenance_source_incompatible")
            if not parser_version:
                conflict_reasons.add("current_v2_parser_provenance_missing")
        elif trusted:
            authority = "unsupported_trusted_provenance"
            deferred_reasons.add("unsupported_normalizer_provenance")
        else:
            authority = "untrusted"
        if projection.get("provider_creator_id_invalid"):
            if authority in {"current_v2", "incompatible_current_v2"}:
                conflict_reasons.add("invalid_trusted_provider_creator_id")
            elif authority == "legacy_unknown":
                deferred_reasons.add("invalid_legacy_provider_creator_id")
            else:
                deferred_reasons.add("invalid_untrusted_provider_creator_id")
        if projection.get("page_count_invalid"):
            if authority in {"current_v2", "incompatible_current_v2"}:
                conflict_reasons.add("invalid_trusted_provider_page_count")
            else:
                deferred_reasons.add("invalid_untrusted_provider_page_count")
        if authority == "untrusted" and (
            projection.get("provider_creator_id") is not None
            or projection.get("page_count") is not None
        ):
            deferred_reasons.add("untrusted_work_fact_ignored")
        entries.append(
            {
                "projection": projection,
                "trusted": trusted,
                "authority": authority,
            }
        )
    if len(work_ids) != 1:
        raise PixivMetadataProjectionError("pixiv_work_scope_mixed")
    work_id = next(iter(work_ids))
    current_parser_versions = {
        str(entry["projection"]["provenance"]["parser_version"])
        for entry in entries
        if entry["authority"] == "current_v2"
        and entry["projection"]["provenance"].get("parser_version")
    }
    if len(current_parser_versions) > 1:
        conflict_reasons.add("work_current_v2_parser_version_conflict")
    creator_id, creator_source = _select_authoritative_work_fact(
        entries,
        field="provider_creator_id",
        current_conflict_reason="work_conflicting_provider_creator_ids",
        legacy_conflict_reason="work_conflicting_legacy_provider_creator_ids",
        legacy_mismatch_reason="legacy_creator_id_conflict_ignored",
        conflict_reasons=conflict_reasons,
        deferred_reasons=deferred_reasons,
    )
    page_count, page_count_source = _select_authoritative_work_fact(
        entries,
        field="page_count",
        current_conflict_reason="work_conflicting_provider_page_counts",
        legacy_conflict_reason="work_conflicting_legacy_provider_page_counts",
        legacy_mismatch_reason="legacy_page_count_conflict_ignored",
        conflict_reasons=conflict_reasons,
        deferred_reasons=deferred_reasons,
    )
    if page_count is not None and any(index >= int(page_count) for index in page_indexes):
        conflict_reasons.add("work_page_index_out_of_provider_range")
    if creator_source == "conflict":
        creator_id = None
    current_creator_candidates = sorted(
        {
            str(entry["projection"]["provider_creator_id"])
            for entry in entries
            if entry["authority"] == "current_v2"
            and entry["projection"].get("provider_creator_id") is not None
        }
    )
    legacy_creator_candidates = sorted(
        {
            str(entry["projection"]["provider_creator_id"])
            for entry in entries
            if entry["authority"] == "legacy_unknown"
            and entry["projection"].get("provider_creator_id") is not None
        }
    )
    authority_counts = Counter(str(entry["authority"]) for entry in entries)
    normalizer_versions = sorted(
        {str(entry["projection"]["normalizer_version"]) for entry in entries}
    )
    provenance_sources = sorted(
        {
            str(entry["projection"]["provenance"]["source"])
            for entry in entries
            if entry["projection"]["provenance"].get("source")
        }
    )
    parser_versions = sorted(
        {
            str(entry["projection"]["provenance"]["parser_version"])
            for entry in entries
            if entry["projection"]["provenance"].get("parser_version")
        }
    )
    return {
        "work_id": work_id,
        "known_page_indexes": sorted(page_indexes),
        "provider_creator_id": creator_id,
        "provider_creator_id_candidates": (
            current_creator_candidates
            if current_creator_candidates
            else legacy_creator_candidates
        ),
        "provider_page_count": page_count,
        "creator_identity_authority": creator_source,
        "page_count_authority": page_count_source,
        "normalizer_versions": normalizer_versions,
        "provenance_sources": provenance_sources,
        "parser_versions": parser_versions,
        "record_authority_counts": dict(sorted(authority_counts.items())),
        "provenance_compatible": not conflict_reasons,
        "conflict_reasons": sorted(conflict_reasons),
        "deferred_reasons": sorted(deferred_reasons),
    }


def build_canonical_pixiv_work_page_aggregate(
    records: Sequence[Any],
    *,
    name_observations: Sequence[Any] = (),
    tag_observations: Sequence[Any] = (),
    known_page_indexes: Sequence[int] = (),
    work_consistency: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical aggregate from existing source-layer rows.

    Numeric row IDs and provider-record keys may correlate rows inside this
    function, but they never enter the business projection or fingerprint.
    """

    if not records:
        raise PixivMetadataProjectionError("pixiv_source_records_missing")
    selected_work_consistency = dict(
        work_consistency or derive_pixiv_work_consistency(records)
    )

    record_entries: list[dict[str, Any]] = []
    work_pages: set[tuple[str, int]] = set()
    tokens: set[str] = set()
    for index, record in enumerate(records):
        provider = normalize_source_text(_value(record, "provider")).casefold()
        if provider != "pixiv":
            raise PixivMetadataProjectionError("pixiv_source_provider_invalid")
        work_id = str(_value(record, "source_work_id") or "").strip()
        page_index = canonical_pixiv_page_index(
            _value(record, "source_page_index")
        )
        if page_index is None:
            raise PixivMetadataProjectionError("pixiv_page_index_invalid")
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
    if selected_work_consistency.get("work_id") != work_id:
        raise PixivMetadataProjectionError("pixiv_work_consistency_scope_mismatch")
    stable_key = stable_pixiv_work_page_key(work_id, page_index)
    entry_by_token = {entry["token"]: entry for entry in record_entries}

    conflict_reasons: set[str] = set()
    deferred_reasons: set[str] = set()
    conflict_reasons.update(
        str(value) for value in selected_work_consistency.get("conflict_reasons", ())
    )
    deferred_reasons.update(
        str(value) for value in selected_work_consistency.get("deferred_reasons", ())
    )
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
    selected_creator_id = canonical_pixiv_creator_id(
        selected_work_consistency.get("provider_creator_id")
    )
    creator_candidates = sorted(
        {
            str(value)
            for value in selected_work_consistency.get(
                "provider_creator_id_candidates", ()
            )
            if canonical_pixiv_creator_id(value) is not None
        }
    )
    creator_conflict = any(
        reason
        in {
            "work_conflicting_provider_creator_ids",
            "work_conflicting_legacy_provider_creator_ids",
            "invalid_trusted_provider_creator_id",
            "current_v2_provenance_identity_incompatible",
            "current_v2_provenance_source_incompatible",
            "current_v2_parser_provenance_missing",
            "work_current_v2_parser_version_conflict",
        }
        for reason in conflict_reasons
    )
    creator_ids = [selected_creator_id] if selected_creator_id and not creator_conflict else []

    display_observations: list[dict[str, Any]] = []
    account_observations: list[dict[str, Any]] = []
    title_observations: list[dict[str, Any]] = []
    tag_values: list[dict[str, Any]] = []

    for entry in trusted_entries:
        source_fingerprint = entry["fingerprint"]
        entry_creator_id = entry["projection"]["provider_creator_id"]
        if (
            selected_creator_id
            and entry_creator_id
            and entry_creator_id != selected_creator_id
        ):
            deferred_reasons.add("non_authoritative_creator_observation_ignored")
            continue
        creator_id = selected_creator_id or entry_creator_id
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
        parent_creator_id = parent["projection"]["provider_creator_id"]
        if (
            selected_creator_id
            and parent_creator_id
            and parent_creator_id != selected_creator_id
        ):
            deferred_reasons.add("non_authoritative_creator_observation_ignored")
            continue
        creator_id = selected_creator_id or parent_creator_id
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

    selected_page_count = selected_work_consistency.get("provider_page_count")
    page_counts = [int(selected_page_count)] if selected_page_count is not None else []
    known_scope_values = list(known_page_indexes) + list(
        selected_work_consistency.get("known_page_indexes", ())
    ) + [page_index]
    known_scope: list[int] = []
    for value in known_scope_values:
        try:
            stable_pixiv_work_page_key(work_id, value)
        except PixivMetadataProjectionError as exc:
            raise PixivMetadataProjectionError(
                "pixiv_known_page_scope_invalid"
            ) from exc
        known_scope.append(int(value))
    known_scope = sorted(set(known_scope))

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
            "conflicting_provider_creator_ids": (
                creator_candidates if creator_conflict else []
            ),
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
        "provenance": {
            "normalizer_versions": list(
                selected_work_consistency.get("normalizer_versions", ())
            ),
            "provenance_sources": list(
                selected_work_consistency.get("provenance_sources", ())
            ),
            "parser_versions": list(
                selected_work_consistency.get("parser_versions", ())
            ),
            "creator_identity_authority": selected_work_consistency.get(
                "creator_identity_authority", "none"
            ),
            "page_count_authority": selected_work_consistency.get(
                "page_count_authority", "none"
            ),
            "record_authority_counts": dict(
                selected_work_consistency.get("record_authority_counts", {})
            ),
            "work_fact_compatible": not bool(
                selected_work_consistency.get("conflict_reasons")
            ),
        },
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
    raw_creator_id = creator.get("provider_creator_id")
    creator_id = canonical_pixiv_creator_id(raw_creator_id)
    if raw_creator_id not in (None, "") and creator_id is None:
        raise PixivMetadataProjectionError("pixiv_creator_id_invalid")
    creator_conflict = bool(creator.get("conflicting_provider_creator_ids"))
    context_key = stable_key
    inputs: list[SourceConceptSignalInput] = []

    if creator_id and not creator_conflict:
        anchor_value = f"Pixiv creator {creator_id}"
        inputs.append(
            SourceConceptSignalInput(
                origin_type="pixiv_creator_identity_anchor",
                origin_key=_signal_origin_key(
                    stable_key, "creator_identity", creator_id
                ),
                provider="pixiv",
                raw_value=anchor_value,
                display_value=anchor_value,
                canonical_value=f"pixiv_creator_{creator_id}",
                role_hint="artist",
                work_context_key=None,
                source_kind="pixiv_stable_creator_identity",
                trust_tier="strong",
                confidence=1.0,
                status="active",
                evidence_payload={
                    "stable_creator_id": creator_id,
                    "observation_kind": "stable_creator_identity",
                    "mutable_observation": False,
                    "aggregate_fingerprint": supplied_fingerprint,
                    "work_id": aggregate["work_id"],
                    "page_index": aggregate["page_index"],
                    "creator_conflict": False,
                },
                source_record_id=supplied_fingerprint,
            )
        )

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
                        stable_key,
                        field_name,
                        observation.get("canonical_key"),
                        stable_id,
                        observation.get("source_fingerprint"),
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
                    stable_key,
                    "title",
                    observation.get("canonical_key"),
                    observation.get("source_fingerprint"),
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
                    observation.get("source_fingerprint"),
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
        "source_state": {
            "disposition": aggregate.get("disposition"),
            "conflict_reasons": list(aggregate.get("conflict_reasons") or ()),
            "deferred_reasons": list(aggregate.get("deferred_reasons") or ()),
            "metadata_completeness": dict(
                aggregate.get("metadata_completeness") or {}
            ),
            "provenance": dict(aggregate.get("provenance") or {}),
        },
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
    *,
    work_ids: Iterable[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Read Pixiv source rows and build aggregates without writing the session."""

    query = session.query(SourceMetadataRecord).filter(
        SourceMetadataRecord.provider == "pixiv"
    )
    if work_ids is not None:
        raw_work_ids = tuple(work_ids)
        normalized_work_ids = [
            canonical_pixiv_work_id(value) for value in raw_work_ids
        ]
        if any(value is None for value in normalized_work_ids):
            raise PixivMetadataProjectionError("pixiv_work_filter_invalid")
        canonical_work_ids = tuple(
            sorted(
                {str(value) for value in normalized_work_ids},
                key=int,
            )
        )
        if not canonical_work_ids:
            return ()
        query = query.filter(SourceMetadataRecord.source_work_id.in_(canonical_work_ids))
    queried_records = query.all()
    records = [
        record
        for record in queried_records
        if normalize_source_text(record.status)
        != PixivMetadataState.NOT_APPLICABLE.value
    ]
    grouped: dict[tuple[str, int], list[SourceMetadataRecord]] = defaultdict(list)
    for record in records:
        if not record.source_work_id or record.source_page_index is None:
            raise PixivMetadataProjectionError("pixiv_source_record_identity_incomplete")
        work_id = canonical_pixiv_work_id(record.source_work_id)
        if work_id is None:
            raise PixivMetadataProjectionError("pixiv_work_id_invalid")
        stable_pixiv_work_page_key(work_id, record.source_page_index)
        page_index = canonical_pixiv_page_index(record.source_page_index)
        if page_index is None:
            raise PixivMetadataProjectionError("pixiv_page_index_invalid")
        grouped[(work_id, page_index)].append(record)

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
    rows_by_work: dict[str, list[SourceMetadataRecord]] = defaultdict(list)
    for work_id, page_index in grouped:
        known_pages_by_work[work_id].append(page_index)
        rows_by_work[work_id].extend(grouped[(work_id, page_index)])
    work_consistency_by_work = {
        work_id: derive_pixiv_work_consistency(rows)
        for work_id, rows in sorted(rows_by_work.items())
    }

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
                work_consistency=work_consistency_by_work[work_id],
            )
        )
    return tuple(aggregates)


def summarize_pixiv_aggregate_dispositions(
    aggregates: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    counts = Counter(str(item.get("disposition") or "unknown") for item in aggregates)
    return dict(sorted(counts.items()))
