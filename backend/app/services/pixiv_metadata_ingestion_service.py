"""Durable, explicit Pixiv metadata-on-import completeness workflow.

The service records filename-prior decisions in the existing provider-neutral
source metadata registry.  It does not download media, create Entity truth, or
run a hidden worker.  Network execution is exposed only through an explicit
bounded runner after credential-rotation and authentication gates pass.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from ..models import Media, SourceMetadataRecord, SourceNameObservation, SourceTagObservation
from .pixiv_filename_prior_service import PARSER_VERSION, PixivFilenamePrior, distinct_work_pages, parse_approved_fields
from .source_metadata_registry_service import canonical_source_key, normalize_source_text


ROTATION_CONFIRMATION_ENV = "VIOLET_CREDENTIAL_ROTATION_CONFIRMED"
MIN_REQUEST_SPACING_SECONDS = 2.0
QUEUE_METADATA_KIND = "pixiv_ingestion_gate"
COMPLETE_METADATA_KINDS = frozenset({"provider_metadata", "pixiv_metadata_acquisition", QUEUE_METADATA_KIND})
COMPLETE_RECORD_STATUSES = frozenset({"observed", "active", "accepted", "metadata_complete"})


class PixivMetadataState(str, Enum):
    NOT_APPLICABLE = "not_applicable_non_pixiv"
    CANDIDATE_DETECTED = "pixiv_candidate_detected"
    PENDING = "metadata_pending"
    RETRYABLE = "metadata_retryable"
    COMPLETE = "metadata_complete"
    TERMINAL = "terminal_remote_unavailable"
    CONFLICT = "filename_identity_conflict"
    NORMALIZATION_FAILED = "normalization_failed"


CLOSED_STATES = frozenset({PixivMetadataState.COMPLETE.value, PixivMetadataState.TERMINAL.value})
CANDIDATE_STATES = frozenset(state.value for state in PixivMetadataState if state is not PixivMetadataState.NOT_APPLICABLE)


class PixivMetadataGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueueDecision:
    media_id: int
    state: str
    parser_version: str
    work_pages: tuple[tuple[str, int], ...]
    origins: tuple[str, ...]
    reused_complete_record_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AcquisitionResult:
    work_id: str
    state: str
    request_attempted: bool
    page_count: int
    error_class: str | None = None
    attempt_count: int = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def rotation_confirmed(env: Mapping[str, str] | None = None) -> bool:
    values = env if env is not None else os.environ
    return str(values.get(ROTATION_CONFIRMATION_ENV, "")).strip().casefold() == "true"


def require_rotation_confirmation(env: Mapping[str, str] | None = None) -> None:
    if not rotation_confirmed(env):
        raise PixivMetadataGateError("blocked_credential_rotation_confirmation_required")


def _queue_key(media_id: int, work_id: str | None, page_index: int | None) -> str:
    suffix = "not-applicable" if work_id is None else f"{work_id}:p{int(page_index or 0)}"
    return f"pixiv-ingestion:{media_id}:{suffix}"


def _approved_media_fields(media: Media | Mapping[str, Any]) -> tuple[tuple[str, str | None], ...]:
    def value(name: str) -> str | None:
        if isinstance(media, Mapping):
            raw = media.get(name)
        else:
            raw = getattr(media, name, None)
        return str(raw) if raw not in (None, "") else None

    # The mandatory future-ingestion denominator is filename/path anchored.
    # Source and thumbnail values remain strengthening evidence in audits, not
    # silent members of this runtime gate denominator.
    return (("filename", value("filename")), ("stored_path", value("path")))


def _media_id(media: Media | Mapping[str, Any]) -> int:
    raw = media.get("id") if isinstance(media, Mapping) else getattr(media, "id")
    return int(raw)


def _compatible_complete_records(
    session: Session,
    work_id: str,
    page_index: int,
) -> list[SourceMetadataRecord]:
    return (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.source_work_id == work_id,
            SourceMetadataRecord.source_page_index == page_index,
            SourceMetadataRecord.metadata_kind.in_(tuple(COMPLETE_METADATA_KINDS)),
            SourceMetadataRecord.status.in_(tuple(COMPLETE_RECORD_STATUSES)),
        )
        .order_by(SourceMetadataRecord.id.asc())
        .all()
    )


def _upsert_queue_record(
    session: Session,
    *,
    media_id: int,
    work_id: str | None,
    page_index: int | None,
    state: str,
    priors: Sequence[PixivFilenamePrior],
    reused_record_ids: Sequence[int] = (),
) -> SourceMetadataRecord:
    key = _queue_key(media_id, work_id, page_index)
    record = (
        session.query(SourceMetadataRecord)
        .filter(SourceMetadataRecord.provider == "pixiv", SourceMetadataRecord.provider_record_key == key)
        .one_or_none()
    )
    private_parser_evidence = [
        {"work_id": item.work_id, "page_index": item.page_index, "source_field": item.source_field, "token": item.token}
        for item in priors
    ]
    raw = {
        "pixiv_ingestion_state": state,
        "parser_version": PARSER_VERSION,
        "parser_evidence": private_parser_evidence,
        "reused_complete_record_ids": [int(value) for value in reused_record_ids],
    }
    provenance = {
        "source": "canonical_pixiv_filename_path_prior",
        "parser_version": PARSER_VERSION,
        "stable_identity_key": {"provider": "pixiv", "work_id": work_id, "page_index": page_index},
        "updated_at": utc_now().isoformat(),
    }
    if record is None:
        record = SourceMetadataRecord(
            provider="pixiv",
            provider_record_key=key,
            media_id=media_id,
            source_work_id=work_id,
            source_page_index=page_index,
            metadata_kind=QUEUE_METADATA_KIND,
            data_type_label="local_runtime_source_prior",
        )
        session.add(record)
    record.status = state
    record.raw_metadata_json = raw
    record.provenance = provenance
    return record


def queue_media_for_pixiv_metadata(session: Session, media: Media | Mapping[str, Any]) -> QueueDecision:
    """Persist the canonical Pixiv decision for one newly imported media row."""

    media_id = _media_id(media)
    priors = parse_approved_fields(_approved_media_fields(media))
    work_pages = distinct_work_pages(priors)
    origins = tuple(sorted({item.source_field for item in priors}))
    if not work_pages:
        _upsert_queue_record(
            session,
            media_id=media_id,
            work_id=None,
            page_index=None,
            state=PixivMetadataState.NOT_APPLICABLE.value,
            priors=(),
        )
        return QueueDecision(media_id, PixivMetadataState.NOT_APPLICABLE.value, PARSER_VERSION, (), origins)

    if len(work_pages) != 1:
        for work_id, page_index in work_pages:
            matching_priors = tuple(item for item in priors if item.work_id == work_id and item.page_index == page_index)
            _upsert_queue_record(
                session,
                media_id=media_id,
                work_id=work_id,
                page_index=page_index,
                state=PixivMetadataState.CONFLICT.value,
                priors=matching_priors,
            )
        return QueueDecision(media_id, PixivMetadataState.CONFLICT.value, PARSER_VERSION, work_pages, origins)

    work_id, page_index = work_pages[0]
    compatible = _compatible_complete_records(session, work_id, page_index)
    state = PixivMetadataState.COMPLETE.value if compatible else PixivMetadataState.PENDING.value
    _upsert_queue_record(
        session,
        media_id=media_id,
        work_id=work_id,
        page_index=page_index,
        state=state,
        priors=priors,
        reused_record_ids=[record.id for record in compatible],
    )
    return QueueDecision(media_id, state, PARSER_VERSION, work_pages, origins, tuple(record.id for record in compatible))


def summarize_batch_closure(session: Session, media_ids: Iterable[int]) -> dict[str, Any]:
    ids = sorted({int(value) for value in media_ids})
    if not ids:
        return {
            "media_count": 0,
            "pixiv_candidate_count": 0,
            "metadata_complete_count": 0,
            "terminal_remote_unavailable_count": 0,
            "open_candidate_count": 0,
            "closed": True,
        }
    records = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.media_id.in_(ids),
        )
        .all()
    )
    states_by_media: dict[int, set[str]] = defaultdict(set)
    for record in records:
        states_by_media[int(record.media_id)].add(str(record.status))
    candidate_states = [
        state
        for media_states in states_by_media.values()
        for state in media_states
        if state in CANDIDATE_STATES
    ]
    counts = Counter(candidate_states)
    complete = counts[PixivMetadataState.COMPLETE.value]
    terminal = counts[PixivMetadataState.TERMINAL.value]
    candidate_count = len(candidate_states)
    missing_queue_media_count = len(set(ids) - set(states_by_media))
    open_count = candidate_count - complete - terminal
    closed = missing_queue_media_count == 0 and candidate_count == complete + terminal and open_count == 0
    return {
        "media_count": len(ids),
        "pixiv_candidate_count": candidate_count,
        "metadata_complete_count": complete,
        "terminal_remote_unavailable_count": terminal,
        "open_candidate_count": open_count,
        "missing_queue_media_count": missing_queue_media_count,
        "state_counts": dict(sorted(counts.items())),
        "closed": closed,
    }


def pending_distinct_work_ids(session: Session) -> tuple[str, ...]:
    rows = (
        session.query(SourceMetadataRecord.source_work_id)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.status.in_((PixivMetadataState.PENDING.value, PixivMetadataState.RETRYABLE.value)),
            SourceMetadataRecord.source_work_id.isnot(None),
        )
        .distinct()
        .order_by(SourceMetadataRecord.source_work_id.asc())
        .all()
    )
    return tuple(str(row[0]) for row in rows)


def build_gallery_dl_metadata_command(entrypoint: Sequence[str], work_id: str) -> list[str]:
    if not re.fullmatch(r"[1-9]\d{5,11}", str(work_id)):
        raise PixivMetadataGateError("invalid_canonical_pixiv_work_id")
    return [
        *entrypoint,
        "--dump-json",
        "--no-download",
        f"https://www.pixiv.net/artworks/{work_id}",
    ]


def _extract_payload_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        if any(key in value for key in ("id", "illust_id", "work_id", "num", "page", "page_index")):
            records.append(dict(value))
        for nested in value.values():
            if isinstance(nested, (Mapping, list, tuple)):
                records.extend(_extract_payload_records(nested))
    elif isinstance(value, (list, tuple)):
        # gallery-dl event rows commonly use [event_type, url, metadata].
        if len(value) >= 3 and isinstance(value[-1], Mapping):
            records.append(dict(value[-1]))
        else:
            for nested in value:
                records.extend(_extract_payload_records(nested))
    return records


def parse_gallery_dl_stdout(stdout: str, expected_work_id: str) -> list[dict[str, Any]]:
    payloads: list[Any] = []
    stripped = stdout.strip()
    if not stripped:
        raise PixivMetadataGateError("metadata_normalization_failed_empty_output")
    try:
        payloads.append(json.loads(stripped))
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            if line.strip():
                payloads.append(json.loads(line))
    records: list[dict[str, Any]] = []
    for payload in payloads:
        records.extend(_extract_payload_records(payload))
    normalized: list[dict[str, Any]] = []
    seen_pages: set[int] = set()
    for raw in records:
        work_id = raw.get("id") or raw.get("illust_id") or raw.get("work_id") or raw.get("pid")
        if str(work_id or "") != str(expected_work_id):
            continue
        page_index = raw.get("num")
        if page_index is None:
            page_index = raw.get("page_index", raw.get("page", 0))
        page_index = int(page_index or 0)
        if page_index in seen_pages:
            continue
        seen_pages.add(page_index)
        user = raw.get("user") if isinstance(raw.get("user"), Mapping) else {}
        creator_id = raw.get("user_id") or raw.get("artist_id") or user.get("id")
        creator_name = raw.get("user_name") or raw.get("artist_name") or raw.get("artist") or user.get("name")
        creator_account = raw.get("user_account") or raw.get("artist_account") or user.get("account")
        profile_identity = raw.get("user_url") or raw.get("artist_profile_url")
        if not profile_identity and creator_id not in (None, ""):
            profile_identity = f"https://www.pixiv.net/users/{creator_id}"
        tags_raw = raw.get("tags") or raw.get("tag") or []
        if isinstance(tags_raw, Mapping):
            tags_raw = list(tags_raw)
        if isinstance(tags_raw, str):
            tags_raw = [tags_raw]
        tags = []
        for item in tags_raw if isinstance(tags_raw, Sequence) else []:
            tag = item.get("name") if isinstance(item, Mapping) else item
            if normalize_source_text(tag):
                tags.append(normalize_source_text(tag))
        normalized.append(
            {
                "work_id": str(expected_work_id),
                "page_index": page_index,
                "title": normalize_source_text(raw.get("title")) or None,
                "creator_id": str(creator_id) if creator_id not in (None, "") else None,
                "creator_name": normalize_source_text(creator_name) or None,
                "creator_account": normalize_source_text(creator_account) or None,
                "creator_profile_identity": str(profile_identity) if profile_identity else None,
                "tags": tuple(dict.fromkeys(tags)),
                "raw": raw,
            }
        )
    if not normalized:
        raise PixivMetadataGateError("metadata_identity_mismatch_or_unsupported_shape")
    return sorted(normalized, key=lambda item: int(item["page_index"]))


def _observation_key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def _upsert_name_observation(
    session: Session,
    record: SourceMetadataRecord,
    *,
    raw_name: str | None,
    role: str,
    source_field: str,
) -> None:
    if not raw_name:
        return
    normalized = normalize_source_text(raw_name)
    key = _observation_key(record.provider_record_key, source_field, normalized)
    existing = (
        session.query(SourceNameObservation)
        .filter(SourceNameObservation.source_metadata_record_id == record.id, SourceNameObservation.observation_key == key)
        .one_or_none()
    )
    if existing is None:
        existing = SourceNameObservation(
            source_metadata_record_id=record.id,
            provider="pixiv",
            observation_key=key,
            media_id=record.media_id,
            source_work_id=record.source_work_id,
            source_page_index=record.source_page_index,
            raw_name=raw_name,
            normalized_name=normalized,
            canonical_name_key=canonical_source_key(normalized),
            name_role=role,
            source_field=source_field,
            provenance={"parser_version": PARSER_VERSION, "source": "gallery_dl_authenticated_metadata"},
            requires_review=True,
            status="observed",
        )
        session.add(existing)


def _upsert_tag_observation(session: Session, record: SourceMetadataRecord, raw_tag: str, order_index: int) -> None:
    normalized = normalize_source_text(raw_tag)
    if not normalized:
        return
    key = _observation_key(record.provider_record_key, "pixiv_tag", normalized)
    existing = (
        session.query(SourceTagObservation)
        .filter(SourceTagObservation.source_metadata_record_id == record.id, SourceTagObservation.observation_key == key)
        .one_or_none()
    )
    if existing is None:
        session.add(
            SourceTagObservation(
                source_metadata_record_id=record.id,
                provider="pixiv",
                observation_key=key,
                raw_tag=raw_tag,
                normalized_tag=normalized,
                canonical_tag_key=canonical_source_key(normalized),
                source_tag_kind="provider_tag",
                order_index=order_index,
                status="observed",
            )
        )


def persist_complete_work(session: Session, work_id: str, pages: Sequence[Mapping[str, Any]]) -> int:
    """Persist normalized metadata and link every queued local page idempotently."""

    queued = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.source_work_id == str(work_id),
        )
        .all()
    )
    pages_by_index = {int(item["page_index"]): item for item in pages}
    linked = 0
    for record in queued:
        page = pages_by_index.get(int(record.source_page_index or 0))
        if page is None:
            record.status = PixivMetadataState.NORMALIZATION_FAILED.value
            raw = dict(record.raw_metadata_json or {})
            raw["failure_reason"] = "provider_metadata_missing_local_page"
            record.raw_metadata_json = raw
            continue
        raw = dict(page["raw"])
        raw["creator_account"] = page.get("creator_account")
        raw["creator_profile_identity"] = page.get("creator_profile_identity")
        record.data_type_label = "authenticated_provider_metadata"
        record.title = page.get("title")
        record.artist_id = page.get("creator_id")
        record.artist_name = page.get("creator_name")
        record.raw_metadata_json = raw
        record.provenance = {
            "source": "gallery_dl_authenticated_metadata",
            "parser_version": PARSER_VERSION,
            "stable_identity_key": {"provider": "pixiv", "work_id": str(work_id), "page_index": int(record.source_page_index or 0)},
        }
        record.status = PixivMetadataState.COMPLETE.value
        record.retrieved_at = utc_now()
        session.flush()
        _upsert_name_observation(session, record, raw_name=page.get("creator_name"), role="artist", source_field="pixiv_user_metadata")
        _upsert_name_observation(session, record, raw_name=page.get("creator_account"), role="artist", source_field="pixiv_user_account")
        _upsert_name_observation(session, record, raw_name=page.get("title"), role="work_title", source_field="pixiv_title")
        for index, tag in enumerate(page.get("tags") or ()):
            _upsert_tag_observation(session, record, str(tag), index)
        linked += 1
    if linked != len(queued):
        raise PixivMetadataGateError("metadata_normalization_failed_missing_local_pages")
    return linked


def mark_work_state(session: Session, work_id: str, state: str, *, reason: str) -> int:
    records = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.source_work_id == str(work_id),
        )
        .all()
    )
    for record in records:
        record.status = state
        raw = dict(record.raw_metadata_json or {})
        raw["failure_reason"] = reason
        raw["last_attempt_at"] = utc_now().isoformat()
        record.raw_metadata_json = raw
    return len(records)


def classify_gallery_dl_failure(stderr: str, *, authentication_passed: bool) -> tuple[str, str]:
    value = str(stderr or "")
    if re.search(r"(?i)(401|403|auth|oauth|login|refresh.?token|cookie)", value):
        return PixivMetadataState.RETRYABLE.value, "retryable_authentication"
    if re.search(r"(?i)(429|rate.?limit|too many requests)", value):
        return PixivMetadataState.RETRYABLE.value, "retryable_rate_limit"
    if re.search(r"(?i)(timeout|timed out|connection|network|dns|temporar)", value):
        return PixivMetadataState.RETRYABLE.value, "retryable_network_transport"
    if authentication_passed and re.search(r"(?i)(404|deleted|private|not found|unavailable)", value):
        return PixivMetadataState.TERMINAL.value, "authenticated_remote_deleted_private_unavailable"
    return PixivMetadataState.RETRYABLE.value, "retryable_provider_failure"


def run_bounded_acquisition(
    session: Session,
    work_ids: Sequence[str],
    *,
    entrypoint: Sequence[str],
    authentication_passed: bool,
    env: Mapping[str, str] | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    timeout_seconds: int = 120,
    min_spacing_seconds: float = MIN_REQUEST_SPACING_SECONDS,
    max_attempts_per_work: int = 3,
) -> list[AcquisitionResult]:
    """Execute a finite distinct-work manifest with per-work DB checkpoints."""

    require_rotation_confirmation(env)
    if not authentication_passed:
        raise PixivMetadataGateError("blocked_gallery_dl_redacted_authentication_preflight_failed")
    if min_spacing_seconds < MIN_REQUEST_SPACING_SECONDS:
        raise PixivMetadataGateError("blocked_pixiv_request_spacing_below_two_seconds")
    if max_attempts_per_work < 1 or max_attempts_per_work > 3:
        raise PixivMetadataGateError("blocked_pixiv_retry_budget_invalid")
    manifest = tuple(dict.fromkeys(str(value) for value in work_ids))
    results: list[AcquisitionResult] = []
    command_count = 0
    stop_remaining_manifest = False
    for work_id in manifest:
        command = build_gallery_dl_metadata_command(entrypoint, work_id)
        for attempt in range(1, max_attempts_per_work + 1):
            if command_count:
                sleeper(max(min_spacing_seconds, min_spacing_seconds * attempt))
            command_count += 1
            try:
                completed = command_runner(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    shell=False,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                state, reason = PixivMetadataState.RETRYABLE.value, "retryable_network_transport"
                mark_work_state(session, work_id, state, reason=reason)
                session.commit()
                if attempt < max_attempts_per_work:
                    continue
                results.append(AcquisitionResult(work_id, state, True, 0, exc.__class__.__name__, attempt))
                stop_remaining_manifest = True
                break
            if completed.returncode != 0:
                state, reason = classify_gallery_dl_failure(completed.stderr or "", authentication_passed=authentication_passed)
                mark_work_state(session, work_id, state, reason=reason)
                session.commit()
                retryable = state == PixivMetadataState.RETRYABLE.value and reason in {
                    "retryable_rate_limit",
                    "retryable_network_transport",
                    "retryable_provider_failure",
                }
                if retryable and attempt < max_attempts_per_work:
                    continue
                results.append(AcquisitionResult(work_id, state, True, 0, reason, attempt))
                if state == PixivMetadataState.RETRYABLE.value:
                    stop_remaining_manifest = True
                break
            try:
                pages = parse_gallery_dl_stdout(completed.stdout or "", work_id)
                linked = persist_complete_work(session, work_id, pages)
                if linked == 0:
                    raise PixivMetadataGateError("metadata_normalization_failed_no_local_page_link")
            except (PixivMetadataGateError, ValueError, TypeError, json.JSONDecodeError) as exc:
                mark_work_state(session, work_id, PixivMetadataState.NORMALIZATION_FAILED.value, reason=exc.__class__.__name__)
                session.commit()
                results.append(AcquisitionResult(work_id, PixivMetadataState.NORMALIZATION_FAILED.value, True, 0, exc.__class__.__name__, attempt))
                stop_remaining_manifest = True
                break
            session.commit()
            results.append(AcquisitionResult(work_id, PixivMetadataState.COMPLETE.value, True, len(pages), attempt_count=attempt))
            break
        if stop_remaining_manifest:
            break
    return results


def promotion_manifest() -> dict[str, Any]:
    return {
        "manifest_version": "pixiv_source_evidence_promotion_v1",
        "reusable_artifacts": [
            "raw_provider_metadata",
            "normalized_provider_metadata",
            "provider_work_id",
            "page_index",
            "creator_id_name_account_profile_identity",
            "work_title",
            "source_tags_and_names",
            "filename_parser_result_and_version",
            "provenance",
            "acquisition_or_terminal_status",
            "immutable_content_fingerprints",
        ],
        "stable_identity_keys": ["provider", "source_work_id", "source_page_index", "evidence_fingerprint"],
        "reusable_llm_judgment_requirements": [
            "stable_pair_identity",
            "compatible_evidence_fingerprint",
            "prompt_schema_model_provider_policy_versions",
            "successful_checkpoint",
            "no_source_contradiction",
        ],
        "required_recomputation_targets": [
            "SourceConcept_component_membership",
            "SourceConcept_ids",
            "union_find_and_cluster_outputs",
            "candidate_blocks_and_partitions",
            "signal_links",
            "materialized_concepts_and_aliases",
            "fallback_and_search_indexes",
            "graph_route_confidence_and_benchmark_metrics",
        ],
        "copy_development_database_row_ids": False,
        "production_execution_authorized": False,
    }


def llm_budget_policy(projected_cost_usd: float, *, finite_manifest: bool, primary_provider: bool, cache_first: bool, fallback_provider: bool, production_or_truth_write: bool) -> dict[str, Any]:
    preauthorized = (
        finite_manifest
        and primary_provider
        and cache_first
        and not fallback_provider
        and not production_or_truth_write
        and 0 <= float(projected_cost_usd) <= 10.0
    )
    return {
        "policy_version": "bounded_phase_primary_llm_usd10_v1",
        "projected_cost_usd": round(float(projected_cost_usd), 6),
        "preauthorized": preauthorized,
        "aggregate_execution_limit_usd": 10.0,
        "retries_count_toward_limit": True,
        "split_run_budget_evasion_forbidden": True,
        "approval_required": not preauthorized,
    }
