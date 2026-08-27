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

from ..models import (
    Media,
    SourceMetadataEvidence,
    SourceMetadataRecord,
    SourceNameObservation,
    SourceTagObservation,
)
from ..utils.cache import invalidate_source_metadata_search_cache
from .pixiv_filename_prior_service import PARSER_VERSION, PixivFilenamePrior, distinct_work_pages, parse_approved_fields
from .pixiv_identity_policy import (
    canonical_pixiv_creator_id,
    canonical_pixiv_work_id,
    is_allowlisted_pixiv_provider_marker,
)
from .source_metadata_registry_service import canonical_source_key, normalize_source_text


ROTATION_CONFIRMATION_ENV = "VIOLET_CREDENTIAL_ROTATION_CONFIRMED"
MIN_REQUEST_SPACING_SECONDS = 2.0
PERSISTENT_SPACING_STATE_VERSION = "pixiv_persistent_request_spacing_v1"
MANIFEST_SCOPED_OUTCOME_KEY_VERSION = "pixiv_manifest_scoped_outcome_key_v1"
PIXIV_METADATA_NORMALIZER_VERSION = "pixiv_gallery_dl_metadata_normalizer_v2"
PIXIV_LEGACY_NORMALIZER_VERSION = "legacy_unknown"
QUEUE_METADATA_KIND = "pixiv_ingestion_gate"
COMPLETE_METADATA_KINDS = frozenset({
    "provider_metadata",
    "pixiv_metadata_acquisition",
    "gallery_dl_real_pixiv_metadata",
    QUEUE_METADATA_KIND,
})
CANONICAL_COMPLETE_STATUSES = frozenset({"observed", "active", "accepted", "metadata_complete"})
DEFERRED_PAGE_MISMATCH_POLICY_VERSION = "source_page_mismatch_deferred_nonblocking_v1"
DEFERRED_PAGE_MISMATCH_REASON = "provider_metadata_missing_attempted_local_page"
PAGE_OBSERVED_COMPLETION_EVIDENCE_KIND = "provider_page_observed_complete"
SV1B_TRUST_RECLASSIFICATION_POLICY_VERSION = "sv1b_trusted_complete_reclassification_v1"
SV1B_PHASE_DELTA_ENVELOPE_VERSION = "sv1b_primary_phase_delta_envelope_v1"
QUERY_VISIBLE_OBSERVATION_STATUSES = frozenset({"observed", "active", "accepted"})


class PixivMetadataState(str, Enum):
    NOT_APPLICABLE = "not_applicable_non_pixiv"
    CANDIDATE_DETECTED = "pixiv_candidate_detected"
    PENDING = "metadata_pending"
    RETRYABLE = "metadata_retryable"
    COMPLETE = "metadata_complete"
    TERMINAL = "terminal_remote_unavailable"
    CONFLICT = "filename_identity_conflict"
    NORMALIZATION_FAILED = "normalization_failed"
    PROVIDER_IDENTITY_MISMATCH = "provider_identity_mismatch"
    DEFERRED_PAGE_MISMATCH = "deferred_nonblocking_source_page_mismatch"


CLOSED_STATES = frozenset({
    PixivMetadataState.COMPLETE.value,
    PixivMetadataState.TERMINAL.value,
    PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
})
OPEN_ACQUISITION_STATES = frozenset({PixivMetadataState.PENDING.value, PixivMetadataState.RETRYABLE.value})
CANONICAL_PENDING_STATUSES = frozenset({PixivMetadataState.CANDIDATE_DETECTED.value, PixivMetadataState.PENDING.value})
CANONICAL_RETRYABLE_STATUSES = frozenset({PixivMetadataState.RETRYABLE.value})
CANDIDATE_STATES = frozenset(state.value for state in PixivMetadataState if state is not PixivMetadataState.NOT_APPLICABLE)


def _record_value(record: SourceMetadataRecord | Mapping[str, Any], field: str) -> Any:
    return record.get(field) if isinstance(record, Mapping) else getattr(record, field, None)


def stable_pixiv_source_record_fingerprint(
    record: SourceMetadataRecord | Mapping[str, Any],
) -> str:
    """Return a cross-database reference fingerprint without a numeric row ID."""

    payload = {
        "provider": _record_value(record, "provider"),
        "source_work_id": _record_value(record, "source_work_id"),
        "source_page_index": _record_value(record, "source_page_index"),
        "metadata_kind": _record_value(record, "metadata_kind"),
        "data_type_label": _record_value(record, "data_type_label"),
        "status": _record_value(record, "status"),
        "title": _record_value(record, "title"),
        "artist_id": _record_value(record, "artist_id"),
        "artist_name": _record_value(record, "artist_name"),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def is_trusted_complete_pixiv_metadata_record(
    record: SourceMetadataRecord | Mapping[str, Any],
) -> bool:
    """Return whether one record may support Pixiv identity/search evidence.

    This is intentionally Pixiv/ML1-specific. Queue rows are trusted only after
    exact completion with provider payload or separately governed exact-page
    presence evidence; creator-like fields on an open/deferred queue row are
    never sufficient by themselves.
    """

    provider = str(_record_value(record, "provider") or "").strip().casefold()
    metadata_kind = str(_record_value(record, "metadata_kind") or "").strip()
    data_type = str(_record_value(record, "data_type_label") or "").strip()
    status = str(_record_value(record, "status") or "").strip()
    raw_work_id = _record_value(record, "source_work_id")
    work_id = canonical_pixiv_work_id(raw_work_id)
    page_index = _record_value(record, "source_page_index")
    raw = _record_value(record, "raw_metadata_json")
    provenance = _record_value(record, "provenance")
    if (
        provider != "pixiv"
        or metadata_kind not in COMPLETE_METADATA_KINDS
        or status not in CANONICAL_COMPLETE_STATUSES
        or work_id is None
        or page_index is None
        or isinstance(page_index, bool)
        or not data_type
        or not isinstance(raw, Mapping)
        or not raw
        or not isinstance(provenance, Mapping)
        or not provenance
    ):
        return False
    try:
        canonical_page_index = int(page_index)
    except (TypeError, ValueError):
        return False
    if canonical_page_index < 0 or str(canonical_page_index) != str(page_index):
        return False
    if metadata_kind != QUEUE_METADATA_KIND:
        return True
    stable = provenance.get("stable_identity_key")
    stable = stable if isinstance(stable, Mapping) else {}
    try:
        stable_page_index = int(stable.get("page_index"))
    except (TypeError, ValueError):
        stable_page_index = -1
    stable_matches = bool(
        str(stable.get("provider") or "").casefold() == "pixiv"
        and str(stable.get("work_id") or "") == work_id
        and stable.get("page_index") is not None
        and not isinstance(stable.get("page_index"), bool)
        and stable_page_index == canonical_page_index
    )
    source = str(provenance.get("source") or "")
    if status != PixivMetadataState.COMPLETE.value or not stable_matches:
        return False
    if source == "compatible_complete_record_reuse":
        reuse = raw.get("_pixiv_ingestion_reuse")
        reuse = reuse if isinstance(reuse, Mapping) else {}
        stable_key = str(provenance.get("source_provider_record_key") or "")
        reuse_stable_key = str(reuse.get("source_provider_record_key") or "")
        stable_fingerprint = str(provenance.get("source_record_fingerprint") or "")
        reuse_fingerprint = str(reuse.get("source_record_fingerprint") or "")
        if stable_key or reuse_stable_key or stable_fingerprint or reuse_fingerprint:
            return bool(
                stable_key
                and stable_key == reuse_stable_key
                and stable_fingerprint
                and stable_fingerprint == reuse_fingerprint
            )
        # Accepted pre-v2 Primary rows remain readable for immutable evidence
        # comparison. New writes and v2 replay packages never emit this form.
        return bool(
            provenance.get("source_metadata_record_id") is not None
            and reuse.get("source_metadata_record_id") == provenance.get("source_metadata_record_id")
        )
    return bool(
        source == "gallery_dl_authenticated_metadata"
        and data_type in {
            "authenticated_provider_metadata",
            "authenticated_provider_page_presence_evidence",
        }
    )


def is_pixiv_creator_observation_compatible_with_parent(
    observation: SourceNameObservation | Mapping[str, Any],
    parent: SourceMetadataRecord | Mapping[str, Any],
) -> bool:
    """Require one query-visible creator observation to match its trusted parent."""

    if not is_trusted_complete_pixiv_metadata_record(parent):
        return False
    if str(_record_value(observation, "provider") or "").casefold() != "pixiv":
        return False
    if str(_record_value(observation, "source_field") or "") not in {
        "pixiv_user_metadata",
        "pixiv_user_account",
    }:
        return False
    if str(_record_value(observation, "status") or "") not in QUERY_VISIBLE_OBSERVATION_STATUSES:
        return False
    observation_work = str(_record_value(observation, "source_work_id") or "").strip()
    parent_work = str(_record_value(parent, "source_work_id") or "").strip()
    if observation_work and observation_work != parent_work:
        return False
    observation_page = _record_value(observation, "source_page_index")
    parent_page = _record_value(parent, "source_page_index")
    if observation_page is not None and int(observation_page or 0) != int(parent_page or 0):
        return False
    observation_media = _record_value(observation, "media_id")
    parent_media = _record_value(parent, "media_id")
    return not (
        observation_media is not None
        and parent_media is not None
        and int(observation_media) != int(parent_media)
    )


class PixivMetadataGateError(RuntimeError):
    pass


def manifest_scoped_outcome_key(
    phase_manifest_fingerprint: str,
    provider: str,
    work_id: str,
    requested_page: int,
) -> str:
    """Return the stable acquisition key required for one requested page."""

    fingerprint = str(phase_manifest_fingerprint or "").strip().casefold()
    provider_value = str(provider or "").strip().casefold()
    work_value = canonical_pixiv_work_id(work_id)
    try:
        page_value = int(requested_page)
    except (TypeError, ValueError) as exc:
        raise PixivMetadataGateError("manifest_outcome_page_invalid") from exc
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise PixivMetadataGateError("manifest_outcome_fingerprint_invalid")
    if provider_value != "pixiv":
        raise PixivMetadataGateError("manifest_outcome_provider_invalid")
    if work_value is None:
        raise PixivMetadataGateError("manifest_outcome_work_id_invalid")
    if page_value < 0:
        raise PixivMetadataGateError("manifest_outcome_page_invalid")
    payload = {
        "version": MANIFEST_SCOPED_OUTCOME_KEY_VERSION,
        "phase_manifest_fingerprint": fingerprint,
        "provider": provider_value,
        "work_id": work_value,
        "requested_page": page_value,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class PersistentRequestSpacing:
    """Serialize provider calls through an atomic, restart-safe spacing clock.

    The state file contains no credentials, URLs, local paths, or raw provider
    values. One atomic lock protects readers and writers across processes. A
    stale or malformed lock/state fails closed instead of resetting the clock.
    """

    def __init__(
        self,
        state_path: Path,
        *,
        phase_manifest_fingerprint: str,
        provider: str = "pixiv",
        min_spacing_seconds: float = MIN_REQUEST_SPACING_SECONDS,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        if min_spacing_seconds < MIN_REQUEST_SPACING_SECONDS:
            raise PixivMetadataGateError("blocked_pixiv_request_spacing_below_two_seconds")
        fingerprint = str(phase_manifest_fingerprint or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise PixivMetadataGateError("persistent_spacing_manifest_fingerprint_invalid")
        provider_value = str(provider or "").strip().casefold()
        if provider_value != "pixiv":
            raise PixivMetadataGateError("persistent_spacing_provider_invalid")
        self.state_path = Path(state_path)
        self.lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        self.phase_manifest_fingerprint = fingerprint
        self.provider = provider_value
        self.min_spacing_seconds = float(min_spacing_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.wait_count = 0
        self.total_sleep_seconds = 0.0
        self.last_observed_delay_seconds = 0.0

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PixivMetadataGateError("persistent_spacing_state_invalid") from exc
        if not isinstance(value, Mapping):
            raise PixivMetadataGateError("persistent_spacing_state_invalid")
        if value.get("version") != PERSISTENT_SPACING_STATE_VERSION:
            raise PixivMetadataGateError("persistent_spacing_state_version_mismatch")
        if str(value.get("provider") or "").casefold() != self.provider:
            raise PixivMetadataGateError("persistent_spacing_state_provider_mismatch")
        last_epoch = value.get("last_request_epoch")
        if last_epoch is not None:
            try:
                float(last_epoch)
            except (TypeError, ValueError) as exc:
                raise PixivMetadataGateError("persistent_spacing_last_request_invalid") from exc
        return dict(value)

    def _write_state(self, *, request_epoch: float) -> None:
        seen = set()
        if self.state_path.exists():
            seen.update(self._read_state().get("manifest_fingerprints_seen") or ())
        seen.add(self.phase_manifest_fingerprint)
        payload = {
            "version": PERSISTENT_SPACING_STATE_VERSION,
            "provider": self.provider,
            "last_request_epoch": float(request_epoch),
            "minimum_spacing_seconds": self.min_spacing_seconds,
            "manifest_fingerprints_seen": sorted(seen),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f"{self.state_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)

    def wait_before_request(self, work_id: str) -> None:
        if canonical_pixiv_work_id(work_id) is None:
            raise PixivMetadataGateError("persistent_spacing_work_id_invalid")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.lock_timeout_seconds
        lock_fd: int | None = None
        while lock_fd is None:
            try:
                lock_fd = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(lock_fd, str(os.getpid()).encode("ascii"))
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise PixivMetadataGateError("persistent_spacing_lock_timeout")
                time.sleep(0.05)
        try:
            state = self._read_state()
            now = float(self.clock())
            last_epoch = float(state.get("last_request_epoch") or 0.0)
            elapsed = max(0.0, now - last_epoch) if last_epoch else self.min_spacing_seconds
            delay = max(0.0, self.min_spacing_seconds - elapsed)
            if delay:
                self.sleeper(delay)
            request_epoch = max(float(self.clock()), last_epoch + self.min_spacing_seconds)
            self._write_state(request_epoch=request_epoch)
            self.wait_count += 1
            self.total_sleep_seconds += delay
            self.last_observed_delay_seconds = delay
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def public_evidence(self) -> dict[str, Any]:
        state = self._read_state()
        return {
            "version": PERSISTENT_SPACING_STATE_VERSION,
            "provider": self.provider,
            "minimum_spacing_seconds": self.min_spacing_seconds,
            "persistent_state_present": bool(state),
            "manifest_scope_count": len(state.get("manifest_fingerprints_seen") or ()),
            "wait_count": self.wait_count,
            "total_sleep_seconds": round(self.total_sleep_seconds, 6),
            "last_observed_delay_seconds": round(self.last_observed_delay_seconds, 6),
            "state_path_redacted": True,
        }


class GalleryDlReportedFailure(PixivMetadataGateError):
    def __init__(self, state: str, reason: str):
        super().__init__(reason)
        self.state = state
        self.reason = reason


def classify_pixiv_metadata_lifecycle(status: Any) -> str:
    """Map persisted record status to one canonical metadata lifecycle class."""

    value = str(status or "").strip()
    if value in CANONICAL_COMPLETE_STATUSES:
        return "complete"
    if value in CANONICAL_PENDING_STATUSES:
        return "pending"
    if value in CANONICAL_RETRYABLE_STATUSES or value.startswith("metadata_retryable"):
        return "retryable"
    if value == PixivMetadataState.TERMINAL.value:
        return "terminal"
    if value == PixivMetadataState.NORMALIZATION_FAILED.value:
        return "normalization_failed"
    if value == PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value:
        return "provider_identity_mismatch"
    if value == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value:
        return "deferred_nonblocking_source_page_mismatch"
    if value == PixivMetadataState.CONFLICT.value:
        return "conflict"
    if value == PixivMetadataState.NOT_APPLICABLE.value:
        return "not_applicable"
    return "unknown"


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
    systemic_stop: bool = False


class PixivRouteViabilityClass(str, Enum):
    ROUTE_VIABLE = "route_viable"
    EXPLICIT_AUTHENTICATION_REJECTION = "explicit_authentication_rejection"
    RESOURCE_UNAVAILABLE_INCONCLUSIVE = "resource_unavailable_inconclusive"
    TRANSPORT_OR_PROVIDER_SYSTEMIC_FAILURE = "transport_or_provider_systemic_failure"
    IDENTITY_OR_PAYLOAD_FAILURE = "identity_or_payload_failure"


@dataclass(frozen=True)
class RouteViabilityAttempt:
    work_id: str
    result_class: str
    route_viable: bool
    returned_work_consistent: bool
    returned_page_count: int
    safe_reason_code: str
    elapsed_seconds: float
    attempt_count: int = 1

    @property
    def private_stable_work_reference(self) -> str:
        return hashlib.sha256(self.work_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PageLocalDispositionResult:
    linked_record_ids: tuple[int, ...]
    missing_record_ids: tuple[int, ...]

    @property
    def linked_count(self) -> int:
        return len(self.linked_record_ids)


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
    records = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.source_work_id == work_id,
            SourceMetadataRecord.source_page_index == page_index,
            SourceMetadataRecord.metadata_kind.in_(tuple(COMPLETE_METADATA_KINDS)),
            SourceMetadataRecord.status.in_(tuple(CANONICAL_COMPLETE_STATUSES)),
        )
        .order_by(SourceMetadataRecord.id.asc())
        .all()
    )
    return [record for record in records if is_trusted_complete_pixiv_metadata_record(record)]


def _has_mismatched_pixiv_identity(
    session: Session, media_id: int, work_id: str, page_index: int
) -> bool:
    records = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.media_id == int(media_id),
            SourceMetadataRecord.metadata_kind != QUEUE_METADATA_KIND,
        )
        .all()
    )
    return any(
        str(record.source_work_id or "") != str(work_id)
        or int(record.source_page_index or 0) != int(page_index)
        for record in records
        if record.source_work_id is not None
        and is_trusted_complete_pixiv_metadata_record(record)
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
    phase_delta_reason: str | None = None
    prior_raw: dict[str, Any] = {}
    prior_provenance: dict[str, Any] = {}
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
    elif (
        str(record.status) == PixivMetadataState.COMPLETE.value
        and not is_trusted_complete_pixiv_metadata_record(record)
    ):
        # SV1B tightened the canonical completeness contract. Preserve the raw
        # historical payload/provenance, but reopen an untrusted positive queue
        # row so it cannot silently close an exact page.
        prior_raw = dict(record.raw_metadata_json or {})
        prior_provenance = dict(record.provenance or {})
        phase_delta_reason = "reopened_untrusted_complete"
        raw = {
            **prior_raw,
            "pixiv_ingestion_state": state,
            "parser_version": PARSER_VERSION,
            "parser_evidence": private_parser_evidence,
            "reused_complete_record_ids": [int(value) for value in reused_record_ids],
            "_sv1b_trust_reclassification": {
                "policy_version": SV1B_TRUST_RECLASSIFICATION_POLICY_VERSION,
                "prior_status": PixivMetadataState.COMPLETE.value,
                "raw_metadata_preserved": True,
            },
        }
        provenance = {
            **prior_provenance,
            "source": "canonical_pixiv_filename_path_prior",
            "parser_version": PARSER_VERSION,
            "stable_identity_key": {"provider": "pixiv", "work_id": work_id, "page_index": page_index},
            "trust_reclassification_policy_version": SV1B_TRUST_RECLASSIFICATION_POLICY_VERSION,
        }
    elif str(record.status) in CLOSED_STATES | {
        PixivMetadataState.RETRYABLE.value,
        PixivMetadataState.CONFLICT.value,
        PixivMetadataState.NORMALIZATION_FAILED.value,
        PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value,
    }:
        # Generic import/resume discovery cannot reopen durable closure or
        # silently select a winner for an unresolved identity conflict.
        return record
    elif record is not None:
        prior_raw = dict(record.raw_metadata_json or {})
        prior_provenance = dict(record.provenance or {})
        phase_delta_reason = (
            "refreshed_not_applicable_queue_record"
            if str(record.status) == PixivMetadataState.NOT_APPLICABLE.value
            else "refreshed_open_queue_record"
        )

    if record is not None and phase_delta_reason is not None:
        existing_envelope = prior_raw.get("_sv1b_phase_delta")
        existing_envelope = existing_envelope if isinstance(existing_envelope, Mapping) else {}
        original_raw = existing_envelope.get("original_raw_metadata_json")
        original_raw = dict(original_raw) if isinstance(original_raw, Mapping) else prior_raw
        original_provenance = existing_envelope.get("original_provenance")
        original_provenance = (
            dict(original_provenance)
            if isinstance(original_provenance, Mapping)
            else prior_provenance
        )

        def fingerprint(value: Mapping[str, Any]) -> str:
            return hashlib.sha256(
                json.dumps(
                    dict(value), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8")
            ).hexdigest()

        envelope = {
            "envelope_version": SV1B_PHASE_DELTA_ENVELOPE_VERSION,
            "reclassification_policy_version": SV1B_TRUST_RECLASSIFICATION_POLICY_VERSION,
            "reason_code": phase_delta_reason,
            "original_status": str(existing_envelope.get("original_status") or record.status),
            "original_raw_metadata_fingerprint": fingerprint(original_raw),
            "original_provenance_fingerprint": fingerprint(original_provenance),
            "original_raw_metadata_json": original_raw,
            "original_provenance": original_provenance,
            "original_values_recoverable": True,
            "accepted_stable_identity_unchanged": True,
        }
        raw = {**raw, "_sv1b_phase_delta": envelope}
        provenance = {
            **provenance,
            "sv1b_phase_delta_envelope_version": SV1B_PHASE_DELTA_ENVELOPE_VERSION,
            "sv1b_phase_delta_reason_code": phase_delta_reason,
            "original_raw_metadata_fingerprint": envelope["original_raw_metadata_fingerprint"],
            "original_provenance_fingerprint": envelope["original_provenance_fingerprint"],
        }
    record.status = state
    record.raw_metadata_json = raw
    record.provenance = provenance
    return record


def _materialize_reused_complete_evidence(
    session: Session,
    queue_record: SourceMetadataRecord,
    source_record: SourceMetadataRecord,
) -> None:
    """Copy compatible provider evidence onto the newly linked media row."""

    if not is_trusted_complete_pixiv_metadata_record(source_record):
        raise PixivMetadataGateError("untrusted_complete_record_reuse_rejected")

    source_provider_record_key = str(source_record.provider_record_key or "")
    source_record_fingerprint = stable_pixiv_source_record_fingerprint(source_record)
    if not source_provider_record_key:
        raise PixivMetadataGateError("stable_source_record_key_missing")
    source_raw = dict(source_record.raw_metadata_json or {})
    source_raw["_pixiv_ingestion_reuse"] = {
        "source_provider_record_key": source_provider_record_key,
        "source_record_fingerprint": source_record_fingerprint,
        "stable_identity_key": {
            "provider": "pixiv",
            "work_id": str(queue_record.source_work_id),
            "page_index": int(queue_record.source_page_index or 0),
        },
    }
    queue_record.data_type_label = source_record.data_type_label
    queue_record.title = source_record.title
    queue_record.artist_id = source_record.artist_id
    queue_record.artist_name = source_record.artist_name
    queue_record.raw_metadata_json = source_raw
    queue_record.provenance = {
        "source": "compatible_complete_record_reuse",
        "source_provider_record_key": source_provider_record_key,
        "source_record_fingerprint": source_record_fingerprint,
        "parser_version": PARSER_VERSION,
        "stable_identity_key": {
            "provider": "pixiv",
            "work_id": str(queue_record.source_work_id),
            "page_index": int(queue_record.source_page_index or 0),
        },
    }
    queue_record.retrieved_at = source_record.retrieved_at
    session.flush()

    name_rows = (
        session.query(SourceNameObservation)
        .filter(
            SourceNameObservation.source_metadata_record_id == int(source_record.id),
            SourceNameObservation.status.in_(("observed", "active", "accepted")),
        )
        .order_by(SourceNameObservation.id.asc())
        .all()
    )
    for row in name_rows:
        if row.source_field in {"pixiv_user_metadata", "pixiv_user_account"} and not is_pixiv_creator_observation_compatible_with_parent(row, source_record):
            continue
        existing = (
            session.query(SourceNameObservation.id)
            .filter(
                SourceNameObservation.source_metadata_record_id == int(queue_record.id),
                SourceNameObservation.observation_key == row.observation_key,
            )
            .one_or_none()
        )
        if existing is None:
            session.add(
                SourceNameObservation(
                    source_metadata_record_id=int(queue_record.id),
                    provider=row.provider,
                    observation_key=row.observation_key,
                    media_id=int(queue_record.media_id),
                    source_work_id=queue_record.source_work_id,
                    source_page_index=queue_record.source_page_index,
                    raw_name=row.raw_name,
                    normalized_name=row.normalized_name,
                    canonical_name_key=row.canonical_name_key,
                    name_role=row.name_role,
                    source_field=row.source_field,
                    language_hint=row.language_hint,
                    script_hint=row.script_hint,
                    confidence=row.confidence,
                    provenance={
                        **dict(row.provenance or {}),
                        "reused_from_provider_record_key": source_provider_record_key,
                        "reused_from_source_record_fingerprint": source_record_fingerprint,
                    },
                    requires_review=bool(row.requires_review),
                    status=row.status,
                )
            )

    tag_rows = (
        session.query(SourceTagObservation)
        .filter(
            SourceTagObservation.source_metadata_record_id == int(source_record.id),
            SourceTagObservation.status.in_(("observed", "active", "accepted")),
        )
        .order_by(SourceTagObservation.id.asc())
        .all()
    )
    for row in tag_rows:
        existing = (
            session.query(SourceTagObservation.id)
            .filter(
                SourceTagObservation.source_metadata_record_id == int(queue_record.id),
                SourceTagObservation.observation_key == row.observation_key,
            )
            .one_or_none()
        )
        if existing is None:
            session.add(
                SourceTagObservation(
                    source_metadata_record_id=int(queue_record.id),
                    provider=row.provider,
                    observation_key=row.observation_key,
                    raw_tag=row.raw_tag,
                    normalized_tag=row.normalized_tag,
                    canonical_tag_key=row.canonical_tag_key,
                    source_tag_kind=row.source_tag_kind,
                    source_category_raw=row.source_category_raw,
                    language_hint=row.language_hint,
                    confidence=row.confidence,
                    order_index=row.order_index,
                    taxonomy_kb_id=row.taxonomy_kb_id,
                    status=row.status,
                )
            )


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
    # A mismatch already attached to this media is stronger evidence than a
    # compatible record attached elsewhere.  Never hide that conflict through
    # cross-media complete-record reuse.
    has_mismatched_identity = _has_mismatched_pixiv_identity(
        session, media_id, work_id, page_index
    )
    compatible = [] if has_mismatched_identity else [
        record
        for record in _compatible_complete_records(session, work_id, page_index)
        if not (
            int(record.media_id or 0) == media_id
            and record.metadata_kind == QUEUE_METADATA_KIND
            and record.provider_record_key == _queue_key(media_id, work_id, page_index)
        )
    ]
    if has_mismatched_identity:
        state = PixivMetadataState.CONFLICT.value
    elif compatible:
        state = PixivMetadataState.COMPLETE.value
    else:
        state = PixivMetadataState.PENDING.value
    queue_record = _upsert_queue_record(
        session,
        media_id=media_id,
        work_id=work_id,
        page_index=page_index,
        state=state,
        priors=priors,
        reused_record_ids=[record.id for record in compatible],
    )
    if state == PixivMetadataState.COMPLETE.value and compatible:
        _materialize_reused_complete_evidence(session, queue_record, compatible[0])
    return QueueDecision(media_id, str(queue_record.status), PARSER_VERSION, work_pages, origins, tuple(record.id for record in compatible))


def summarize_batch_closure(session: Session, media_ids: Iterable[int]) -> dict[str, Any]:
    ids = sorted({int(value) for value in media_ids})
    if not ids:
        return {
            "media_count": 0,
            "pixiv_candidate_count": 0,
            "metadata_complete_count": 0,
            "terminal_remote_unavailable_count": 0,
            "deferred_nonblocking_source_page_mismatch_count": 0,
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
        if classify_pixiv_metadata_lifecycle(state) not in {"unknown", "not_applicable"}
    ]
    counts = Counter(candidate_states)
    lifecycle_counts = Counter(classify_pixiv_metadata_lifecycle(state) for state in candidate_states)
    complete = lifecycle_counts["complete"]
    terminal = lifecycle_counts["terminal"]
    deferred = lifecycle_counts["deferred_nonblocking_source_page_mismatch"]
    candidate_count = len(candidate_states)
    missing_queue_media_count = len(set(ids) - set(states_by_media))
    open_count = candidate_count - complete - terminal - deferred
    closed = (
        missing_queue_media_count == 0
        and candidate_count == complete + terminal + deferred
        and open_count == 0
    )
    return {
        "media_count": len(ids),
        "pixiv_candidate_count": candidate_count,
        "metadata_complete_count": complete,
        "terminal_remote_unavailable_count": terminal,
        "deferred_nonblocking_source_page_mismatch_count": deferred,
        "open_candidate_count": open_count,
        "missing_queue_media_count": missing_queue_media_count,
        "state_counts": dict(sorted(counts.items())),
        "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
        "closed": closed,
    }


def pending_distinct_work_ids(session: Session) -> tuple[str, ...]:
    conflicted_work_ids = (
        session.query(SourceMetadataRecord.source_work_id)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.status == PixivMetadataState.CONFLICT.value,
            SourceMetadataRecord.source_work_id.isnot(None),
        )
        .distinct()
    )
    rows = (
        session.query(SourceMetadataRecord.source_work_id)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.status.in_((PixivMetadataState.PENDING.value, PixivMetadataState.RETRYABLE.value)),
            SourceMetadataRecord.source_work_id.isnot(None),
            ~SourceMetadataRecord.source_work_id.in_(conflicted_work_ids),
        )
        .distinct()
        .order_by(SourceMetadataRecord.source_work_id.asc())
        .all()
    )
    return tuple(str(row[0]) for row in rows)


def conflicted_distinct_work_ids(session: Session) -> tuple[str, ...]:
    rows = (
        session.query(SourceMetadataRecord.source_work_id)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.status == PixivMetadataState.CONFLICT.value,
            SourceMetadataRecord.source_work_id.isnot(None),
        )
        .distinct()
        .order_by(SourceMetadataRecord.source_work_id.asc())
        .all()
    )
    return tuple(str(row[0]) for row in rows)


def acquisition_work_lifecycle_counts(session: Session) -> dict[str, int]:
    rows = (
        session.query(SourceMetadataRecord.source_work_id, SourceMetadataRecord.status)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.source_work_id.isnot(None),
        )
        .all()
    )
    by_work: dict[str, set[str]] = defaultdict(set)
    for work_id, status in rows:
        by_work[str(work_id)].add(classify_pixiv_metadata_lifecycle(status))
    counts = Counter()
    priority = (
        "conflict",
        "provider_identity_mismatch",
        "normalization_failed",
        "retryable",
        "pending",
        "deferred_nonblocking_source_page_mismatch",
        "terminal",
        "complete",
    )
    for states in by_work.values():
        selected = next((state for state in priority if state in states), "unknown")
        counts[selected] += 1
    return dict(sorted(counts.items()))


def build_gallery_dl_metadata_command(entrypoint: Sequence[str], work_id: str) -> list[str]:
    canonical_work_id = canonical_pixiv_work_id(work_id)
    if canonical_work_id is None:
        raise PixivMetadataGateError("invalid_canonical_pixiv_work_id")
    return [
        *entrypoint,
        "--dump-json",
        "--no-download",
        f"https://www.pixiv.net/artworks/{canonical_work_id}",
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
        if (
            len(value) >= 3
            and not isinstance(value[0], (Mapping, list, tuple))
            and isinstance(value[-1], Mapping)
        ):
            records.append(dict(value[-1]))
        else:
            for nested in value:
                records.extend(_extract_payload_records(nested))
    return records


def _gallery_dl_provider_marker(raw: Mapping[str, Any]) -> str | None:
    """Return an explicit provider marker without guessing from display data."""

    for key in ("provider", "extractor", "extractor_key", "category"):
        marker = normalize_source_text(raw.get(key)).casefold()
        if marker:
            return marker
    return None


def _first_present_value(*values: Any) -> tuple[Any, bool]:
    for value in values:
        if value not in (None, ""):
            return value, True
    return None, False


def _looks_like_gallery_dl_work_record(raw: Mapping[str, Any]) -> bool:
    if any(key in raw for key in ("illust_id", "work_id", "pid")):
        return True
    return "id" in raw and any(
        key in raw
        for key in (
            "num",
            "page_index",
            "page",
            "page_count",
            "title",
            "tags",
            "tag",
            "user",
            "user_id",
            "artist_id",
        )
    )


def _normalized_gallery_dl_page(
    raw: Mapping[str, Any],
    *,
    expected_work_id: str,
    route_provider_marker: str,
) -> dict[str, Any]:
    explicit_marker = _gallery_dl_provider_marker(raw)
    marker = explicit_marker or route_provider_marker
    if not is_allowlisted_pixiv_provider_marker(marker):
        raise PixivMetadataGateError("metadata_normalization_failed_unknown_provider")

    page_index_raw = raw.get("num")
    if page_index_raw is None:
        page_index_raw = raw.get("page_index", raw.get("page", 0))
    try:
        page_index = int(page_index_raw or 0)
    except (TypeError, ValueError) as exc:
        raise PixivMetadataGateError(
            "metadata_normalization_failed_page_index_invalid"
        ) from exc
    if page_index < 0:
        raise PixivMetadataGateError("metadata_normalization_failed_page_index_invalid")

    page_count_raw = raw.get("page_count", raw.get("count", raw.get("num_pages")))
    page_count: int | None = None
    if page_count_raw not in (None, ""):
        try:
            page_count = int(page_count_raw)
        except (TypeError, ValueError) as exc:
            raise PixivMetadataGateError(
                "metadata_normalization_failed_page_count_invalid"
            ) from exc
        if page_count <= 0 or page_index >= page_count:
            raise PixivMetadataGateError(
                "metadata_normalization_failed_page_count_mismatch"
            )

    user = raw.get("user") if isinstance(raw.get("user"), Mapping) else {}
    creator_id_value, creator_id_present = _first_present_value(
        raw.get("user_id"), raw.get("artist_id"), user.get("id")
    )
    creator_id = canonical_pixiv_creator_id(creator_id_value)
    if creator_id_present and creator_id is None:
        raise PixivMetadataGateError(
            "metadata_normalization_failed_creator_id_invalid"
        )
    creator_name = (
        raw.get("user_name")
        or raw.get("artist_name")
        or raw.get("artist")
        or user.get("name")
    )
    creator_account = (
        raw.get("user_account") or raw.get("artist_account") or user.get("account")
    )
    profile_identity = raw.get("user_url") or raw.get("artist_profile_url")
    profile_identity_source = "raw_provider_identity" if profile_identity else None
    if not profile_identity and creator_id is not None:
        profile_identity = f"https://www.pixiv.net/users/{creator_id}"
        profile_identity_source = "derived_from_stable_creator_id"

    tags_raw = raw.get("tags") or raw.get("tag") or []
    if isinstance(tags_raw, Mapping):
        tags_raw = list(tags_raw)
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]
    tags: set[str] = set()
    for item in tags_raw if isinstance(tags_raw, Sequence) else []:
        tag = item.get("name") if isinstance(item, Mapping) else item
        normalized_tag = normalize_source_text(tag)
        if normalized_tag:
            tags.add(normalized_tag)

    return {
        "normalizer_version": PIXIV_METADATA_NORMALIZER_VERSION,
        "work_id": str(expected_work_id),
        "page_index": page_index,
        "page_count": page_count,
        "title": normalize_source_text(raw.get("title")) or None,
        "creator_id": creator_id,
        "creator_name": normalize_source_text(creator_name) or None,
        "creator_account": normalize_source_text(creator_account) or None,
        "creator_profile_identity": str(profile_identity) if profile_identity else None,
        "creator_profile_identity_source": profile_identity_source,
        "tags": tuple(sorted(tags, key=lambda value: (canonical_source_key(value), value))),
        "raw": dict(raw),
    }


def _normalized_page_conflict_projection(page: Mapping[str, Any]) -> dict[str, Any]:
    """Project only normalized business fields when detecting duplicate conflicts."""

    return {
        key: page.get(key)
        for key in (
            "normalizer_version",
            "work_id",
            "page_index",
            "page_count",
            "title",
            "creator_id",
            "creator_name",
            "creator_account",
            "creator_profile_identity",
            "creator_profile_identity_source",
            "tags",
        )
    }


def normalize_gallery_dl_records(
    records: Sequence[Mapping[str, Any]],
    expected_work_id: str,
    *,
    provider_marker: str = "pixiv",
) -> list[dict[str, Any]]:
    """Normalize already-decoded gallery-dl records through one authority."""

    canonical_work_id = canonical_pixiv_work_id(expected_work_id)
    if canonical_work_id is None:
        raise PixivMetadataGateError("invalid_canonical_pixiv_work_id")
    if not is_allowlisted_pixiv_provider_marker(provider_marker):
        raise PixivMetadataGateError("metadata_normalization_failed_unknown_provider")
    decoded_records = [dict(record) for record in records]
    returned_work_ids = {
        str(raw.get("id") or raw.get("illust_id") or raw.get("work_id") or raw.get("pid"))
        for raw in decoded_records
        if _looks_like_gallery_dl_work_record(raw)
        and (raw.get("id") or raw.get("illust_id") or raw.get("work_id") or raw.get("pid"))
    }
    if returned_work_ids and returned_work_ids != {canonical_work_id}:
        raise PixivMetadataGateError("provider_identity_mismatch")

    pages_by_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in decoded_records:
        if not _looks_like_gallery_dl_work_record(raw):
            continue
        work_id = raw.get("id") or raw.get("illust_id") or raw.get("work_id") or raw.get("pid")
        if str(work_id or "") != canonical_work_id:
            continue
        page = _normalized_gallery_dl_page(
            raw,
            expected_work_id=canonical_work_id,
            route_provider_marker=provider_marker,
        )
        pages_by_index[int(page["page_index"])].append(page)

    normalized: list[dict[str, Any]] = []
    for page_index, candidates in sorted(pages_by_index.items()):
        projections = {
            json.dumps(
                _normalized_page_conflict_projection(candidate),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for candidate in candidates
        }
        if len(projections) != 1:
            raise PixivMetadataGateError(
                "metadata_normalization_failed_conflicting_duplicate_page"
            )
        # Raw payload retention remains private. Choosing by canonical JSON makes
        # duplicate replay independent from provider event ordering.
        selected = min(
            candidates,
            key=lambda value: json.dumps(
                value["raw"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        normalized.append(selected)
    if not normalized:
        if returned_work_ids and canonical_work_id not in returned_work_ids:
            raise PixivMetadataGateError("provider_identity_mismatch")
        raise PixivMetadataGateError("metadata_normalization_failed_unsupported_shape")
    return normalized


def parse_gallery_dl_stdout(
    stdout: str,
    expected_work_id: str,
    *,
    provider_marker: str = "pixiv",
) -> list[dict[str, Any]]:
    payloads: list[Any] = []
    stripped = stdout.strip()
    if not stripped:
        raise PixivMetadataGateError("metadata_normalization_failed_empty_output")
    try:
        payloads.append(json.loads(stripped))
    except json.JSONDecodeError:
        try:
            for line in stdout.splitlines():
                if line.strip():
                    payloads.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise PixivMetadataGateError(
                "metadata_normalization_failed_malformed_json"
            ) from exc
    records: list[dict[str, Any]] = []
    for payload in payloads:
        records.extend(_extract_payload_records(payload))
    error_messages: list[str] = []
    for payload in payloads:
        for value in _walk_payload_mappings(payload):
            if "error" in value or "message" in value:
                error_messages.extend(
                    str(value.get(key) or "")
                    for key in ("error", "message")
                    if value.get(key)
                )
    if error_messages and not records:
        state, reason = classify_gallery_dl_failure(
            " ".join(error_messages), authentication_passed=True
        )
        raise GalleryDlReportedFailure(state, reason)
    return normalize_gallery_dl_records(
        records,
        expected_work_id,
        provider_marker=provider_marker,
    )


def classify_gallery_dl_route_viability(
    *, stdout: str, stderr: str, returncode: int, expected_work_id: str,
) -> RouteViabilityAttempt:
    """Classify route evidence without changing ordinary acquisition state."""

    started = time.monotonic()
    canonical_work_id = canonical_pixiv_work_id(expected_work_id)
    if canonical_work_id is None:
        raise PixivMetadataGateError("invalid_canonical_pixiv_work_id")
    payloads: list[Any] = []
    parse_failed = False
    stripped = str(stdout or "").strip()
    if stripped:
        try:
            payloads.append(json.loads(stripped))
        except json.JSONDecodeError:
            try:
                payloads.extend(json.loads(line) for line in stripped.splitlines() if line.strip())
            except json.JSONDecodeError:
                parse_failed = True
    records: list[dict[str, Any]] = []
    if not parse_failed:
        for payload in payloads:
            records.extend(_extract_payload_records(payload))
    route_markers = {
        marker
        for payload in payloads
        for value in _walk_payload_mappings(payload)
        if (marker := _gallery_dl_provider_marker(value)) is not None
    }
    route_marker_is_pixiv = any(
        is_allowlisted_pixiv_provider_marker(marker) for marker in route_markers
    )
    matching_records: list[dict[str, Any]] = []
    mismatched_identity = False
    provider_is_pixiv = False
    for record in records:
        raw_work_id = record.get("id") or record.get("illust_id") or record.get("work_id") or record.get("pid")
        explicit_marker = _gallery_dl_provider_marker(record)
        record_is_pixiv = (
            is_allowlisted_pixiv_provider_marker(explicit_marker)
            if explicit_marker is not None
            else route_marker_is_pixiv
        )
        if str(raw_work_id or "") == canonical_work_id:
            matching_records.append(record)
            provider_is_pixiv = provider_is_pixiv or record_is_pixiv
        elif raw_work_id not in (None, ""):
            mismatched_identity = True
    if matching_records and provider_is_pixiv:
        page_indexes: set[int] = set()
        for record in matching_records:
            raw_page = record.get("num")
            if raw_page is None:
                raw_page = record.get("page_index", record.get("page", 0))
            try:
                page_indexes.add(int(raw_page or 0))
            except (TypeError, ValueError):
                pass
        return RouteViabilityAttempt(
            canonical_work_id, PixivRouteViabilityClass.ROUTE_VIABLE.value, True, True,
            len(page_indexes), "pixiv_matching_work_metadata_returned",
            round(time.monotonic() - started, 6),
        )

    diagnostic = " ".join((str(stderr or ""), str(stdout or "")))
    if re.search(
        r"(?i)(\b401\b|authentication(?:\s+required|\s+failed|\s+rejected)|login\s+required|"
        r"invalid(?:\s+or\s+expired)?\s+refresh.?token|expired\s+refresh.?token|"
        r"oauth(?:\s+failure|\s+failed|\s+error)|credential(?:s)?\s+rejected|"
        r"\b403\b[^\r\n]*(?:auth|login|token|oauth|credential))", diagnostic,
    ):
        result_class = PixivRouteViabilityClass.EXPLICIT_AUTHENTICATION_REJECTION.value
        reason = "explicit_provider_authentication_rejection"
    elif re.search(r"(?i)(\b404\b|deleted|private|not\s*found|unavailable|does\s+not\s+exist|removed)", diagnostic):
        result_class = PixivRouteViabilityClass.RESOURCE_UNAVAILABLE_INCONCLUSIVE.value
        reason = "resource_unavailable_route_unverified"
    elif re.search(
        r"(?i)(\b429\b|rate.?limit|too many requests|timeout|timed out|connection|network|dns|"
        r"temporary failure|provider process|process failure)", diagnostic,
    ) or (int(returncode) != 0 and not records):
        result_class = PixivRouteViabilityClass.TRANSPORT_OR_PROVIDER_SYSTEMIC_FAILURE.value
        reason = "provider_transport_or_systemic_failure"
    else:
        result_class = PixivRouteViabilityClass.IDENTITY_OR_PAYLOAD_FAILURE.value
        if parse_failed:
            reason = "malformed_provider_payload"
        elif mismatched_identity or (records and not matching_records):
            reason = "returned_work_identity_mismatch"
        elif matching_records and not provider_is_pixiv:
            reason = "returned_provider_not_pixiv"
        else:
            reason = "empty_or_unusable_provider_payload"
    return RouteViabilityAttempt(
        canonical_work_id, result_class, False,
        bool(matching_records) and not mismatched_identity, 0, reason,
        round(time.monotonic() - started, 6),
    )


def run_bounded_route_viability_canary(
    work_ids: Sequence[str], *, entrypoint: Sequence[str],
    env: Mapping[str, str] | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    timeout_seconds: int = 120,
    min_spacing_seconds: float = MIN_REQUEST_SPACING_SECONDS,
    persistent_spacing: PersistentRequestSpacing | None = None,
    max_works: int = 5,
) -> tuple[list[RouteViabilityAttempt], dict[str, Any]]:
    """Probe at most five distinct works without persisting queue outcomes."""

    if max_works < 1 or max_works > 5:
        raise PixivMetadataGateError("blocked_auth_canary_work_limit_invalid")
    if min_spacing_seconds < MIN_REQUEST_SPACING_SECONDS:
        raise PixivMetadataGateError("blocked_pixiv_request_spacing_below_two_seconds")
    selected = tuple(dict.fromkeys(str(value) for value in work_ids))[:max_works]
    attempts: list[RouteViabilityAttempt] = []
    started = time.monotonic()
    for index, work_id in enumerate(selected):
        if persistent_spacing is not None:
            persistent_spacing.wait_before_request(work_id)
        elif index:
            sleeper(min_spacing_seconds)
        command = build_gallery_dl_metadata_command(entrypoint, work_id)
        attempt_started = time.monotonic()
        try:
            completed = command_runner(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_seconds, shell=False, env=env,
            )
            attempt = classify_gallery_dl_route_viability(
                stdout=completed.stdout or "", stderr=completed.stderr or "",
                returncode=int(completed.returncode), expected_work_id=work_id,
            )
        except (subprocess.TimeoutExpired, OSError):
            attempt = RouteViabilityAttempt(
                work_id, PixivRouteViabilityClass.TRANSPORT_OR_PROVIDER_SYSTEMIC_FAILURE.value,
                False, False, 0, "provider_transport_or_systemic_exception",
                round(time.monotonic() - attempt_started, 6),
            )
        attempts.append(attempt)
        if attempt.result_class != PixivRouteViabilityClass.RESOURCE_UNAVAILABLE_INCONCLUSIVE.value:
            break
    classes = Counter(item.result_class for item in attempts)
    route_viable = any(item.route_viable for item in attempts)
    if route_viable:
        status = "route_viable"
    elif classes[PixivRouteViabilityClass.EXPLICIT_AUTHENTICATION_REJECTION.value]:
        status = "blocked_sv1b_provider_authentication"
    elif classes[PixivRouteViabilityClass.TRANSPORT_OR_PROVIDER_SYSTEMIC_FAILURE.value]:
        status = "blocked_sv1b_provider_transport"
    else:
        status = "blocked_sv1b_authentication_canary_inconclusive"
    proof = {
        "canary_version": "sv1b_pixiv_route_viability_canary_v1",
        "status": status,
        "route_viable": route_viable,
        "selected_work_count": len(selected),
        "attempted_work_count": len(attempts),
        "maximum_provider_requests": 5,
        "one_attempt_per_selected_work": len({item.work_id for item in attempts}) == len(attempts),
        "result_class_counts": dict(sorted(classes.items())),
        "attempts": [{
            "private_stable_work_reference": item.private_stable_work_reference,
            "attempt_count": item.attempt_count,
            "result_class": item.result_class,
            "route_viability": item.route_viable,
            "returned_work_consistency": item.returned_work_consistent,
            "returned_page_count": item.returned_page_count,
            "elapsed_seconds": item.elapsed_seconds,
            "safe_reason_code": item.safe_reason_code,
            "raw_output_redacted": True,
        } for item in attempts],
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "raw_stdout_published": False,
        "raw_stderr_published": False,
    }
    return attempts, proof


def _walk_payload_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_payload_mappings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_payload_mappings(nested)


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


def _selected_work_records(
    session: Session,
    work_id: str,
    *,
    attempted_record_ids: Sequence[int] | None = None,
    attempted_page_indexes: Sequence[int] | None = None,
) -> tuple[list[SourceMetadataRecord], int]:
    record_ids = {int(value) for value in attempted_record_ids or ()}
    page_indexes = {int(value) for value in attempted_page_indexes or ()}
    if not record_ids and not page_indexes:
        raise PixivMetadataGateError("exact_attempted_queue_scope_required")
    records = (
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.source_work_id == str(work_id),
        )
        .order_by(SourceMetadataRecord.id.asc())
        .all()
    )
    selected = [
        record
        for record in records
        if (record_ids and int(record.id) in record_ids)
        or (page_indexes and int(record.source_page_index or 0) in page_indexes)
    ]
    requested_keys = len(record_ids) if record_ids else len(page_indexes)
    matched_keys = len({int(record.id) for record in selected}) if record_ids else len(
        {int(record.source_page_index or 0) for record in selected}
    )
    return selected, max(0, requested_keys - matched_keys)


def open_work_records(
    session: Session,
    work_id: str,
    *,
    allow_conflict_resolution: bool = False,
    allow_normalization_replay: bool = False,
) -> tuple[SourceMetadataRecord, ...]:
    eligible_states = set(OPEN_ACQUISITION_STATES)
    if allow_conflict_resolution:
        eligible_states.add(PixivMetadataState.CONFLICT.value)
    if allow_normalization_replay:
        eligible_states.add(PixivMetadataState.NORMALIZATION_FAILED.value)
    return tuple(
        session.query(SourceMetadataRecord)
        .filter(
            SourceMetadataRecord.provider == "pixiv",
            SourceMetadataRecord.metadata_kind == QUEUE_METADATA_KIND,
            SourceMetadataRecord.source_work_id == str(work_id),
            SourceMetadataRecord.status.in_(tuple(eligible_states)),
        )
        .order_by(SourceMetadataRecord.id.asc())
        .all()
    )


def _persist_complete_queue_record(
    session: Session,
    record: SourceMetadataRecord,
    work_id: str,
    page: Mapping[str, Any],
) -> None:
    canonical_work_id = canonical_pixiv_work_id(work_id)
    if canonical_work_id is None or canonical_work_id != str(record.source_work_id):
        raise PixivMetadataGateError("provider_identity_mismatch")
    raw_creator_id = page.get("creator_id")
    creator_id = canonical_pixiv_creator_id(raw_creator_id)
    if raw_creator_id not in (None, "") and creator_id is None:
        raise PixivMetadataGateError("metadata_normalization_failed_creator_id_invalid")
    normalizer_version = (
        normalize_source_text(page.get("normalizer_version"))
        or PIXIV_LEGACY_NORMALIZER_VERSION
    )
    raw = dict(page["raw"])
    raw["creator_account"] = page.get("creator_account")
    raw["creator_profile_identity"] = page.get("creator_profile_identity")
    raw["creator_profile_identity_source"] = page.get("creator_profile_identity_source")
    if normalizer_version != PIXIV_LEGACY_NORMALIZER_VERSION:
        raw["_pixiv_metadata_normalizer_version"] = normalizer_version
    else:
        raw.pop("_pixiv_metadata_normalizer_version", None)
    record.data_type_label = "authenticated_provider_metadata"
    record.title = page.get("title")
    record.artist_id = creator_id
    record.artist_name = page.get("creator_name")
    record.raw_metadata_json = raw
    record.provenance = {
        "source": "gallery_dl_authenticated_metadata",
        "parser_version": PARSER_VERSION,
        "metadata_normalizer_version": normalizer_version,
        "stable_identity_key": {
            "provider": "pixiv",
            "work_id": canonical_work_id,
            "page_index": int(record.source_page_index or 0),
        },
    }
    record.status = PixivMetadataState.COMPLETE.value
    record.retrieved_at = utc_now()
    session.flush()
    _upsert_name_observation(
        session,
        record,
        raw_name=page.get("creator_name"),
        role="artist",
        source_field="pixiv_user_metadata",
    )
    _upsert_name_observation(
        session,
        record,
        raw_name=page.get("creator_account"),
        role="artist",
        source_field="pixiv_user_account",
    )
    _upsert_name_observation(
        session,
        record,
        raw_name=page.get("title"),
        role="work_title",
        source_field="pixiv_title",
    )
    for index, tag in enumerate(page.get("tags") or ()):
        _upsert_tag_observation(session, record, str(tag), index)


def persist_page_local_work_disposition(
    session: Session,
    work_id: str,
    pages: Sequence[Mapping[str, Any]],
    *,
    attempted_record_ids: Sequence[int],
    allow_conflict_resolution: bool = False,
    allow_normalization_replay: bool = False,
    allow_deferred_reopen: bool = False,
) -> PageLocalDispositionResult:
    """Persist returned pages and identify missing rows independently."""

    queued, not_found = _selected_work_records(
        session, work_id, attempted_record_ids=attempted_record_ids
    )
    if not_found:
        raise PixivMetadataGateError("attempted_queue_record_not_found")
    pages_by_index = {int(item["page_index"]): item for item in pages}
    eligible_states = set(OPEN_ACQUISITION_STATES)
    if allow_conflict_resolution:
        eligible_states.add(PixivMetadataState.CONFLICT.value)
    if allow_normalization_replay:
        eligible_states.add(PixivMetadataState.NORMALIZATION_FAILED.value)
    if allow_deferred_reopen:
        eligible_states.add(PixivMetadataState.DEFERRED_PAGE_MISMATCH.value)
    for record in queued:
        if str(record.status) not in eligible_states:
            raise PixivMetadataGateError(f"attempted_closed_queue_transition_rejected:{record.status}")
    linked_record_ids: list[int] = []
    missing_record_ids: list[int] = []
    for record in queued:
        page = pages_by_index.get(int(record.source_page_index or 0))
        if page is None:
            missing_record_ids.append(int(record.id))
            continue
        _persist_complete_queue_record(session, record, work_id, page)
        linked_record_ids.append(int(record.id))
    return PageLocalDispositionResult(
        linked_record_ids=tuple(linked_record_ids),
        missing_record_ids=tuple(missing_record_ids),
    )


def persist_complete_work(
    session: Session,
    work_id: str,
    pages: Sequence[Mapping[str, Any]],
    *,
    attempted_record_ids: Sequence[int],
    allow_conflict_resolution: bool = False,
    allow_normalization_replay: bool = False,
    allow_deferred_reopen: bool = False,
) -> int:
    """Compatibility wrapper requiring every attempted local page to be present."""

    result = persist_page_local_work_disposition(
        session,
        work_id,
        pages,
        attempted_record_ids=attempted_record_ids,
        allow_conflict_resolution=allow_conflict_resolution,
        allow_normalization_replay=allow_normalization_replay,
        allow_deferred_reopen=allow_deferred_reopen,
    )
    if result.missing_record_ids:
        raise PixivMetadataGateError("metadata_normalization_failed_missing_local_pages")
    return result.linked_count


def backfill_creator_source_observations(session: Session) -> dict[str, int]:
    """Deterministically preserve existing Pixiv creator fields in source layer only."""

    records = (
        session.query(SourceMetadataRecord)
        .filter(SourceMetadataRecord.provider == "pixiv")
        .order_by(SourceMetadataRecord.id.asc())
        .all()
    )
    counts = Counter()
    for record in records:
        if not is_trusted_complete_pixiv_metadata_record(record):
            counts["skipped_untrusted_parent_record_count"] += 1
            continue
        raw = dict(record.raw_metadata_json or {})
        user = raw.get("user") if isinstance(raw.get("user"), Mapping) else {}
        creator_id_value, creator_id_present = _first_present_value(
            record.artist_id, raw.get("user_id"), raw.get("artist_id"), user.get("id")
        )
        creator_id = canonical_pixiv_creator_id(creator_id_value)
        creator_name = record.artist_name or raw.get("user_name") or raw.get("artist_name") or user.get("name")
        creator_account = raw.get("creator_account") or raw.get("user_account") or raw.get("artist_account") or user.get("account")
        raw_profile = raw.get("user_url") or raw.get("artist_profile_url") or user.get("profile_url")
        profile_identity = raw_profile or raw.get("creator_profile_identity")
        profile_source = "raw_provider_identity" if raw_profile else raw.get("creator_profile_identity_source")
        if not profile_identity and creator_id is not None:
            profile_identity = f"https://www.pixiv.net/users/{creator_id}"
            profile_source = "derived_from_stable_creator_id"
        if creator_id is not None:
            counts["available_creator_id_count"] += 1
            record.artist_id = creator_id
            counts["normalized_creator_id_count"] += 1
        elif creator_id_present:
            counts["invalid_creator_id_count"] += 1
        if normalize_source_text(creator_name):
            counts["available_creator_name_count"] += 1
            record.artist_name = normalize_source_text(creator_name)
            _upsert_name_observation(session, record, raw_name=str(creator_name), role="artist", source_field="pixiv_user_metadata")
            counts["normalized_creator_name_count"] += 1
            counts["query_visible_creator_name_count"] += 1
        if normalize_source_text(creator_account):
            counts["available_creator_account_count"] += 1
            raw["creator_account"] = str(creator_account)
            _upsert_name_observation(session, record, raw_name=str(creator_account), role="artist", source_field="pixiv_user_account")
            counts["normalized_creator_account_count"] += 1
            counts["query_visible_creator_account_count"] += 1
        if profile_identity:
            counts["available_creator_profile_identity_count"] += 1
            raw["creator_profile_identity"] = str(profile_identity)
            raw["creator_profile_identity_source"] = profile_source or "retained_existing_source_identity"
            counts["retained_creator_profile_identity_count"] += 1
        record.raw_metadata_json = raw
    session.flush()
    counts["explicit_creator_role_misclassification_count"] = 0
    counts["silently_dropped_available_creator_field_count"] = 0
    return dict(sorted(counts.items()))


def _created_by_pr136_pixiv_observation_path(observation: SourceNameObservation) -> bool:
    provenance = observation.provenance or {}
    provenance = provenance if isinstance(provenance, Mapping) else {}
    return (
        str(provenance.get("source") or "") == "gallery_dl_authenticated_metadata"
        or provenance.get("reused_from_provider_record_key") is not None
        or provenance.get("reused_from_source_metadata_record_id") is not None
    )


def supersede_untrusted_pixiv_creator_observations(session: Session) -> dict[str, int]:
    """Hide only PR #136 creator observations whose sole parent is untrusted."""

    rows = (
        session.query(SourceNameObservation, SourceMetadataRecord)
        .join(
            SourceMetadataRecord,
            SourceNameObservation.source_metadata_record_id == SourceMetadataRecord.id,
        )
        .filter(
            SourceNameObservation.provider == "pixiv",
            SourceNameObservation.name_role == "artist",
            SourceNameObservation.source_field.in_(
                ("pixiv_user_metadata", "pixiv_user_account")
            ),
            SourceNameObservation.status.in_(tuple(QUERY_VISIBLE_OBSERVATION_STATUSES)),
        )
        .order_by(SourceNameObservation.id.asc())
        .all()
    )
    counts = Counter()
    for observation, parent in rows:
        if is_pixiv_creator_observation_compatible_with_parent(observation, parent):
            counts["trusted_parent_query_visible_creator_observation_count"] += 1
            continue
        if not _created_by_pr136_pixiv_observation_path(observation):
            counts["preserved_out_of_scope_historical_or_manual_static_count"] += 1
            continue
        counts["untrusted_parent_query_visible_creator_observation_count"] += 1
        counts[
            "untrusted_creator_account_count"
            if observation.source_field == "pixiv_user_account"
            else "untrusted_creator_name_count"
        ] += 1
        provenance = dict(observation.provenance or {})
        provenance["lineage_disposition"] = "superseded_untrusted_pixiv_parent_v1"
        observation.provenance = provenance
        observation.status = "superseded"
        counts["superseded_observation_count"] += 1
    session.flush()
    return dict(sorted(counts.items()))


def mark_work_state(
    session: Session,
    work_id: str,
    state: str,
    *,
    reason: str,
    attempted_record_ids: Sequence[int] | None = None,
    attempted_page_indexes: Sequence[int] | None = None,
    structural_diagnostics: Mapping[str, Any] | None = None,
    allow_conflict_resolution: bool = False,
    allow_normalization_replay: bool = False,
) -> dict[str, int]:
    records, not_found = _selected_work_records(
        session,
        work_id,
        attempted_record_ids=attempted_record_ids,
        attempted_page_indexes=attempted_page_indexes,
    )
    counts = {
        "attempted": len(records),
        "updated": 0,
        "preserved_complete": 0,
        "preserved_terminal": 0,
        "preserved_conflict": 0,
        "preserved_deferred": 0,
        "not_found": not_found,
    }
    for record in records:
        current = str(record.status)
        if current == PixivMetadataState.COMPLETE.value:
            counts["preserved_complete"] += 1
            continue
        if current == PixivMetadataState.TERMINAL.value:
            counts["preserved_terminal"] += 1
            continue
        if current == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value:
            counts["preserved_deferred"] += 1
            continue
        if current == PixivMetadataState.CONFLICT.value:
            if allow_conflict_resolution and state in {
                PixivMetadataState.COMPLETE.value,
                PixivMetadataState.TERMINAL.value,
                PixivMetadataState.NORMALIZATION_FAILED.value,
            }:
                pass
            else:
                counts["preserved_conflict"] += 1
                continue
        if current not in OPEN_ACQUISITION_STATES and not (
            allow_conflict_resolution and current == PixivMetadataState.CONFLICT.value
        ) and not (
            allow_normalization_replay and current == PixivMetadataState.NORMALIZATION_FAILED.value
        ):
            counts["not_found"] += 1
            continue
        record.status = state
        raw = dict(record.raw_metadata_json or {})
        raw["failure_reason"] = reason
        raw["last_attempt_at"] = utc_now().isoformat()
        raw["attempted_queue_record_id"] = int(record.id)
        raw["attempted_page_index"] = int(record.source_page_index or 0)
        if structural_diagnostics:
            raw["structural_diagnostics"] = dict(structural_diagnostics)
        record.raw_metadata_json = raw
        counts["updated"] += 1
    return counts


def defer_proven_source_page_mismatch(
    session: Session,
    work_id: str,
    *,
    attempted_record_ids: Sequence[int],
    observed_page_indexes: Sequence[int],
    original_final_outcome: str,
    manifest_kind: str,
    evidence_fingerprint: str,
    deferred_at: str,
    governed_route_exhausted: bool,
) -> dict[str, int]:
    """Close one exactly proven page mismatch without inventing a page link.

    This transition is deliberately narrower than generic normalization or
    conflict handling. It requires the durable error emitted only after
    authenticated metadata for the exact work parsed successfully but did not
    contain every attempted local page. The original queue payload and
    provenance remain untouched; a separate idempotent evidence row records
    the project-lead governance disposition.
    """

    if manifest_kind not in {"main", "conflict"}:
        raise PixivMetadataGateError("deferred_page_mismatch_manifest_kind_invalid")
    expected_outcome = (
        "normalization_failed" if manifest_kind == "main" else "conflict_normalization_failed"
    )
    if original_final_outcome != expected_outcome:
        raise PixivMetadataGateError("deferred_page_mismatch_final_outcome_invalid")
    if not governed_route_exhausted:
        raise PixivMetadataGateError("deferred_page_mismatch_route_not_exhausted")
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence_fingerprint or "")):
        raise PixivMetadataGateError("deferred_page_mismatch_evidence_fingerprint_invalid")
    if not str(deferred_at or "").strip():
        raise PixivMetadataGateError("deferred_page_mismatch_timestamp_required")

    observed_pages = tuple(sorted({int(value) for value in observed_page_indexes}))
    if not observed_pages:
        raise PixivMetadataGateError("deferred_page_mismatch_observed_pages_required")
    records, not_found = _selected_work_records(
        session,
        work_id,
        attempted_record_ids=attempted_record_ids,
    )
    if not_found or not records:
        raise PixivMetadataGateError("deferred_page_mismatch_attempted_queue_scope_invalid")
    requested_pages = {int(record.source_page_index or 0) for record in records}
    absent_pages = requested_pages - set(observed_pages)
    if not absent_pages:
        raise PixivMetadataGateError("deferred_page_mismatch_requested_page_was_present")

    counts = {
        "attempted": len(records),
        "updated": 0,
        "preserved_deferred": 0,
        "completed_returned": 0,
        "preserved_complete": 0,
        "superseded_deferred_evidence": 0,
        "evidence_created": 0,
    }
    for record in records:
        current = str(record.status)
        evidence_key = hashlib.sha256(
            "|".join(
                (
                    DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
                    str(record.id),
                    evidence_fingerprint,
                )
            ).encode("utf-8")
        ).hexdigest()
        existing = (
            session.query(SourceMetadataEvidence)
            .filter(
                SourceMetadataEvidence.source_metadata_record_id == int(record.id),
                SourceMetadataEvidence.evidence_key == evidence_key,
            )
            .one_or_none()
        )
        requested_page = int(record.source_page_index or 0)
        if requested_page in set(observed_pages):
            completion_key = hashlib.sha256(
                "|".join(
                    (
                        PAGE_OBSERVED_COMPLETION_EVIDENCE_KIND,
                        str(record.id),
                        evidence_fingerprint,
                    )
                ).encode("utf-8")
            ).hexdigest()
            completion_evidence = (
                session.query(SourceMetadataEvidence)
                .filter(
                    SourceMetadataEvidence.source_metadata_record_id == int(record.id),
                    SourceMetadataEvidence.evidence_key == completion_key,
                )
                .one_or_none()
            )
            if current == PixivMetadataState.COMPLETE.value:
                if completion_evidence is None and not is_trusted_complete_pixiv_metadata_record(record):
                    raise PixivMetadataGateError(
                        "page_local_complete_state_missing_observed_page_evidence"
                    )
                counts["preserved_complete"] += 1
                continue
            if current not in {
                PixivMetadataState.NORMALIZATION_FAILED.value,
                PixivMetadataState.CONFLICT.value,
                PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
            }:
                raise PixivMetadataGateError(
                    f"page_local_returned_page_ineligible_current_state:{current}"
                )
            if current == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value and existing is None:
                raise PixivMetadataGateError(
                    "deferred_page_mismatch_existing_state_missing_evidence"
                )
            if existing is not None and existing.status == "active":
                existing.status = "superseded_by_page_local_completion"
                counts["superseded_deferred_evidence"] += 1
            if completion_evidence is None:
                session.add(
                    SourceMetadataEvidence(
                        source_metadata_record_id=int(record.id),
                        evidence_key=completion_key,
                        observation_type="provider_page_presence",
                        evidence_kind=PAGE_OBSERVED_COMPLETION_EVIDENCE_KIND,
                        evidence_strength="authenticated_normalized_exact_page",
                        provenance={
                            "provider": "pixiv",
                            "source_work_id": str(work_id),
                            "source_page_index": requested_page,
                            "provider_observed_page_indexes": list(observed_pages),
                            "provider_response_evidence_fingerprint": evidence_fingerprint,
                            "governance_policy_version": DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
                            "raw_provider_payload_retained": False,
                            "creator_title_tag_observations_materialized": False,
                            "unsupported_page_link_created": False,
                        },
                        status="active",
                    )
                )
                counts["evidence_created"] += 1
            record.data_type_label = "authenticated_provider_page_presence_evidence"
            record.status = PixivMetadataState.COMPLETE.value
            counts["updated"] += 1
            counts["completed_returned"] += 1
            continue
        if current == PixivMetadataState.DEFERRED_PAGE_MISMATCH.value:
            if existing is None:
                raise PixivMetadataGateError("deferred_page_mismatch_existing_state_missing_evidence")
            counts["preserved_deferred"] += 1
            continue
        if current not in {
            PixivMetadataState.NORMALIZATION_FAILED.value,
            PixivMetadataState.CONFLICT.value,
        }:
            raise PixivMetadataGateError(
                f"deferred_page_mismatch_ineligible_current_state:{current}"
            )
        raw = dict(record.raw_metadata_json or {})
        diagnostics = dict(raw.get("structural_diagnostics") or {})
        if diagnostics.get("failure_code") != DEFERRED_PAGE_MISMATCH_REASON:
            raise PixivMetadataGateError("deferred_page_mismatch_reason_not_proven")
        if diagnostics.get("provider_output_returned") is not True:
            raise PixivMetadataGateError("deferred_page_mismatch_provider_output_not_proven")
        if str(diagnostics.get("work_id") or "") != str(work_id):
            raise PixivMetadataGateError("deferred_page_mismatch_provider_work_identity_not_proven")
        if str(diagnostics.get("normalizer_version") or "") != "gallery_dl_pixiv_normalizer_v1":
            raise PixivMetadataGateError("deferred_page_mismatch_normalizer_not_supported")

        if existing is None:
            session.add(
                SourceMetadataEvidence(
                    source_metadata_record_id=int(record.id),
                    evidence_key=evidence_key,
                    observation_type="governance_disposition",
                    evidence_kind=PixivMetadataState.DEFERRED_PAGE_MISMATCH.value,
                    evidence_strength="project_lead_governed_exact",
                    provenance={
                        "provider": "pixiv",
                        "source_work_id": str(work_id),
                        "requested_local_page_index": int(record.source_page_index or 0),
                        "provider_observed_page_indexes": list(observed_pages),
                        "provider_response_evidence_fingerprint": evidence_fingerprint,
                        "parser_version": diagnostics.get("parser_version") or PARSER_VERSION,
                        "normalizer_version": diagnostics.get("normalizer_version"),
                        "original_final_outcome": original_final_outcome,
                        "manifest_kind": manifest_kind,
                        "historical_conflict_evidence_preserved": manifest_kind == "conflict",
                        "reason_code": DEFERRED_PAGE_MISMATCH_REASON,
                        "governance_policy_version": DEFERRED_PAGE_MISMATCH_POLICY_VERSION,
                        "deferred_at": deferred_at,
                        "governed_route_exhausted": True,
                        "unsupported_page_link_created": False,
                        "conflict_winner_selected": False,
                        "p0_to_requested_page_substitution_authorized": False,
                    },
                    status="active",
                )
            )
            counts["evidence_created"] += 1
        record.status = PixivMetadataState.DEFERRED_PAGE_MISMATCH.value
        counts["updated"] += 1
    session.flush()
    return counts


def classify_gallery_dl_failure(stderr: str, *, authentication_passed: bool) -> tuple[str, str]:
    value = str(stderr or "")
    # Authenticated, explicit deleted/private/permanent evidence is terminal
    # even when the same diagnostic also contains 403/auth wording. A bare 403
    # remains retryable authentication evidence.
    if authentication_passed and re.search(
        r"(?i)(404|deleted|private|not\s*found|unavailable|does\s+not\s+exist|removed|permanent)",
        value,
    ):
        return PixivMetadataState.TERMINAL.value, "authenticated_remote_deleted_private_unavailable"
    if re.search(r"(?i)(401|403|auth|oauth|login|refresh.?token|cookie)", value):
        return PixivMetadataState.RETRYABLE.value, "retryable_authentication"
    if re.search(r"(?i)(429|rate.?limit|too many requests)", value):
        return PixivMetadataState.RETRYABLE.value, "retryable_rate_limit"
    if re.search(r"(?i)(timeout|timed out|connection|network|dns|temporar)", value):
        return PixivMetadataState.RETRYABLE.value, "retryable_network_transport"
    return PixivMetadataState.RETRYABLE.value, "retryable_provider_failure"


def correct_inconclusive_canary_terminal_record(
    session: Session,
    record_id: int,
    *,
    historical_attempt_count: int = 1,
) -> dict[str, Any]:
    """Reopen exactly one untrusted canary terminal while retaining its evidence."""

    if historical_attempt_count != 1:
        raise PixivMetadataGateError("blocked_prior_canary_attempt_count_invalid")
    record = session.query(SourceMetadataRecord).filter(SourceMetadataRecord.id == int(record_id)).one_or_none()
    if record is None:
        raise PixivMetadataGateError("blocked_prior_canary_record_missing")
    if record.provider != "pixiv" or record.metadata_kind != QUEUE_METADATA_KIND:
        raise PixivMetadataGateError("blocked_prior_canary_record_not_phase_owned_queue")
    if record.status != PixivMetadataState.TERMINAL.value:
        raise PixivMetadataGateError("blocked_prior_canary_record_not_terminal")
    raw_before = dict(record.raw_metadata_json or {})
    provenance_before = dict(record.provenance or {})
    raw_fingerprint = hashlib.sha256(
        json.dumps(raw_before, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    provenance_fingerprint = hashlib.sha256(
        json.dumps(provenance_before, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    stable_identity = "|".join((
        str(record.provider_record_key or ""), str(record.media_id or ""),
        str(record.source_work_id or ""), str(record.source_page_index or 0),
    ))
    before_fingerprint = hashlib.sha256(
        json.dumps({
            "identity": stable_identity, "status": record.status,
            "raw": raw_before, "provenance": provenance_before,
        }, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    envelope = {
        "version": "sv1b_auth_canary_terminal_correction_v1",
        "reason_code": "sv1b_auth_canary_terminal_untrusted_before_route_viability_v1",
        "historical_classifier_status": PixivMetadataState.TERMINAL.value,
        "historical_attempt_count": 1,
        "original_raw_payload_fingerprint": raw_fingerprint,
        "original_provenance_fingerprint": provenance_fingerprint,
        "ordinary_terminal_closure_accepted": False,
    }
    provenance_after = dict(provenance_before)
    existing = provenance_after.get("sv1b_canary_corrections")
    corrections = list(existing) if isinstance(existing, list) else []
    corrections.append(envelope)
    provenance_after["sv1b_canary_corrections"] = corrections
    record.status = PixivMetadataState.RETRYABLE.value
    record.provenance = provenance_after
    session.flush()
    after_fingerprint = hashlib.sha256(
        json.dumps({
            "identity": stable_identity, "status": record.status,
            "raw": record.raw_metadata_json or {}, "provenance": record.provenance or {},
        }, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return {
        "correction_version": envelope["version"],
        "private_stable_record_reference": hashlib.sha256(stable_identity.encode("utf-8")).hexdigest(),
        "before_fingerprint": before_fingerprint,
        "after_fingerprint": after_fingerprint,
        "stable_identity_unchanged": True,
        "original_raw_payload_preserved": dict(record.raw_metadata_json or {}) == raw_before,
        "original_raw_payload_fingerprint": raw_fingerprint,
        "original_provenance_fields_preserved": all(
            provenance_after.get(key) == value for key, value in provenance_before.items()
        ),
        "original_provenance_fingerprint": provenance_fingerprint,
        "historical_attempt_count": 1,
        "historical_terminal_evidence_retained": True,
        "accepted_provider_metadata_fact_changed": False,
        "ordinary_terminal_closure_accepted": False,
        "new_state": PixivMetadataState.RETRYABLE.value,
        "safe_reason_code": envelope["reason_code"],
    }


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
    allow_conflict_resolution: bool = False,
    accept_local_credential_risk: bool = False,
    result_callback: Callable[[AcquisitionResult], None] | None = None,
    allow_normalization_replay: bool = False,
    persistent_spacing: PersistentRequestSpacing | None = None,
    prior_attempt_counts: Mapping[str, int] | None = None,
) -> list[AcquisitionResult]:
    """Execute a finite distinct-work manifest with per-work DB checkpoints."""

    if not accept_local_credential_risk:
        require_rotation_confirmation(env)
    if not authentication_passed:
        raise PixivMetadataGateError("blocked_gallery_dl_redacted_authentication_preflight_failed")
    if min_spacing_seconds < MIN_REQUEST_SPACING_SECONDS:
        raise PixivMetadataGateError("blocked_pixiv_request_spacing_below_two_seconds")
    if max_attempts_per_work < 1 or max_attempts_per_work > 3:
        raise PixivMetadataGateError("blocked_pixiv_retry_budget_invalid")
    manifest = tuple(dict.fromkeys(str(value) for value in work_ids))
    prior_counts = {str(key): int(value) for key, value in (prior_attempt_counts or {}).items()}
    if any(value < 0 or value > max_attempts_per_work for value in prior_counts.values()):
        raise PixivMetadataGateError("blocked_pixiv_prior_attempt_count_invalid")
    results: list[AcquisitionResult] = []
    command_count = 0
    stop_remaining_manifest = False
    for work_id in manifest:
        prior_attempt_count = prior_counts.get(work_id, 0)
        remaining_attempts = max_attempts_per_work - prior_attempt_count
        if remaining_attempts <= 0:
            results.append(AcquisitionResult(
                work_id,
                PixivMetadataState.RETRYABLE.value,
                False,
                0,
                "retry_budget_exhausted",
                prior_attempt_count,
                True,
            ))
            break
        attempted_records = open_work_records(
            session,
            work_id,
            allow_conflict_resolution=allow_conflict_resolution,
            allow_normalization_replay=allow_normalization_replay,
        )
        if not attempted_records:
            result = AcquisitionResult(work_id, "skipped_complete_or_closed", False, 0, attempt_count=0)
            results.append(result)
            if result_callback:
                result_callback(result)
            continue
        attempted_record_ids = tuple(int(record.id) for record in attempted_records)
        command = build_gallery_dl_metadata_command(entrypoint, work_id)
        for attempt_in_run in range(1, remaining_attempts + 1):
            cumulative_attempt = prior_attempt_count + attempt_in_run
            if persistent_spacing is not None:
                persistent_spacing.wait_before_request(work_id)
            elif command_count:
                sleeper(max(min_spacing_seconds, min_spacing_seconds * cumulative_attempt))
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
                mark_work_state(session, work_id, state, reason=reason, attempted_record_ids=attempted_record_ids, allow_conflict_resolution=allow_conflict_resolution, allow_normalization_replay=allow_normalization_replay)
                session.commit()
                if attempt_in_run < remaining_attempts:
                    continue
                result = AcquisitionResult(work_id, state, True, 0, exc.__class__.__name__, cumulative_attempt, True)
                results.append(result)
                if result_callback:
                    result_callback(result)
                stop_remaining_manifest = True
                break
            if completed.returncode != 0:
                state, reason = classify_gallery_dl_failure(completed.stderr or "", authentication_passed=authentication_passed)
                mark_work_state(session, work_id, state, reason=reason, attempted_record_ids=attempted_record_ids, allow_conflict_resolution=allow_conflict_resolution, allow_normalization_replay=allow_normalization_replay)
                session.commit()
                retryable = state == PixivMetadataState.RETRYABLE.value and reason in {
                    "retryable_rate_limit",
                    "retryable_network_transport",
                    "retryable_provider_failure",
                }
                if retryable and attempt_in_run < remaining_attempts:
                    continue
                systemic_stop = state == PixivMetadataState.RETRYABLE.value
                result = AcquisitionResult(work_id, state, True, 0, reason, cumulative_attempt, systemic_stop)
                results.append(result)
                if result_callback:
                    result_callback(result)
                if state == PixivMetadataState.RETRYABLE.value:
                    stop_remaining_manifest = True
                break
            try:
                pages = parse_gallery_dl_stdout(completed.stdout or "", work_id)
                page_disposition = persist_page_local_work_disposition(
                    session, work_id, pages, attempted_record_ids=attempted_record_ids,
                    allow_conflict_resolution=allow_conflict_resolution,
                    allow_normalization_replay=allow_normalization_replay,
                )
                if page_disposition.linked_count == 0 and not page_disposition.missing_record_ids:
                    raise PixivMetadataGateError("metadata_normalization_failed_no_local_page_link")
                if page_disposition.missing_record_ids:
                    mark_work_state(
                        session,
                        work_id,
                        PixivMetadataState.NORMALIZATION_FAILED.value,
                        reason=DEFERRED_PAGE_MISMATCH_REASON,
                        attempted_record_ids=page_disposition.missing_record_ids,
                        structural_diagnostics={
                            "work_id": str(work_id),
                            "attempted_page_set": sorted(
                                {int(record.source_page_index or 0) for record in attempted_records}
                            ),
                            "observed_page_set": sorted(
                                {int(page["page_index"]) for page in pages}
                            ),
                            "parser_version": PARSER_VERSION,
                            "normalizer_version": "gallery_dl_pixiv_normalizer_v1",
                            "failure_class": "PageLocalSourceDisposition",
                            "failure_code": DEFERRED_PAGE_MISMATCH_REASON,
                            "provider_output_returned": True,
                            "raw_provider_output_retained_in_diagnostic": False,
                        },
                        allow_conflict_resolution=allow_conflict_resolution,
                        allow_normalization_replay=allow_normalization_replay,
                    )
            except GalleryDlReportedFailure as exc:
                session.rollback()
                mark_work_state(
                    session,
                    work_id,
                    exc.state,
                    reason=exc.reason,
                    attempted_record_ids=attempted_record_ids,
                    allow_conflict_resolution=allow_conflict_resolution,
                    allow_normalization_replay=allow_normalization_replay,
                )
                session.commit()
                systemic_stop = exc.state == PixivMetadataState.RETRYABLE.value
                result = AcquisitionResult(
                    work_id, exc.state, True, 0, exc.reason, cumulative_attempt, systemic_stop
                )
                results.append(result)
                if result_callback:
                    result_callback(result)
                if systemic_stop:
                    stop_remaining_manifest = True
                break
            except (PixivMetadataGateError, ValueError, TypeError, json.JSONDecodeError) as exc:
                session.rollback()
                failure_code = str(exc).split(":", 1)[0] or exc.__class__.__name__
                identity_mismatch = "identity_mismatch" in failure_code
                failure_state = (
                    PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value
                    if identity_mismatch
                    else PixivMetadataState.NORMALIZATION_FAILED.value
                )
                if allow_conflict_resolution and identity_mismatch:
                    result = AcquisitionResult(
                        work_id,
                        failure_state,
                        True,
                        0,
                        exc.__class__.__name__,
                        cumulative_attempt,
                    )
                    results.append(result)
                    if result_callback:
                        result_callback(result)
                    break
                mark_work_state(
                    session,
                    work_id,
                    failure_state,
                    reason=failure_code,
                    attempted_record_ids=attempted_record_ids,
                    structural_diagnostics={
                        "work_id": str(work_id),
                        "attempted_page_set": sorted({int(record.source_page_index or 0) for record in attempted_records}),
                        "parser_version": PARSER_VERSION,
                        "normalizer_version": "gallery_dl_pixiv_normalizer_v1",
                        "failure_class": exc.__class__.__name__,
                        "failure_code": failure_code,
                        "provider_output_returned": bool(completed.stdout),
                        "raw_provider_output_retained_in_diagnostic": False,
                    },
                    allow_conflict_resolution=allow_conflict_resolution,
                    allow_normalization_replay=allow_normalization_replay,
                )
                session.commit()
                result = AcquisitionResult(work_id, failure_state, True, 0, failure_code, cumulative_attempt)
                results.append(result)
                if result_callback:
                    result_callback(result)
                break
            session.commit()
            # SourceNameObservation and SourceTagObservation become visible to
            # endpoint-equivalent search only after this transaction commits.
            invalidate_source_metadata_search_cache()
            final_state = (
                PixivMetadataState.NORMALIZATION_FAILED.value
                if page_disposition.missing_record_ids
                else PixivMetadataState.COMPLETE.value
            )
            result = AcquisitionResult(
                work_id,
                final_state,
                True,
                page_disposition.linked_count,
                DEFERRED_PAGE_MISMATCH_REASON if page_disposition.missing_record_ids else None,
                attempt_count=cumulative_attempt,
            )
            results.append(result)
            if result_callback:
                result_callback(result)
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
