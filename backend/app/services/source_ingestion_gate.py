"""Unified source ingestion gate for path-based local source workflows.

The gate is intentionally metadata-only for local source paths.  It never opens
or reads file contents, so it does not trigger Cloud Files hydration by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..utils.cloud_files import CloudFileState, classify_cloud_file_state


class SourceKind(str, Enum):
    """Source categories used by ingestion and storage workflows."""

    PATH_SOURCE = "path_source"
    UPLOAD_BYTES = "upload_bytes"
    STAGING_FILE = "staging_file"
    APP_MANAGED_FILE = "app_managed_file"


CLOUD_POLICY_REQUIRED = "controlled_hydration_or_read_probe_or_backfill"
STAGING_AUDIT_POLICY_REQUIRED = "passed_staging_audit_artifact"


def cloud_block_reasons(state: CloudFileState | None) -> tuple[str, ...]:
    """Return structured cloud reasons for a Cloud Files metadata state."""

    if state is None:
        return ()
    reasons: list[str] = []
    if state.offline:
        reasons.append("cloud_offline")
    if state.recall_on_open:
        reasons.append("cloud_recall_on_open")
    if state.recall_on_data_access:
        reasons.append("cloud_recall_on_data_access")
    if state.reparse_point:
        reasons.append("cloud_reparse_point")
    if state.sparse_file:
        reasons.append("cloud_sparse_file")
    if state.likely_cloud_placeholder and not reasons:
        reasons.append("cloud_hydration_failed")
    return tuple(reasons)


def primary_cloud_block_reason(state: CloudFileState | None) -> str:
    """Pick the reason callers should use as the single failure code."""

    if state is None:
        return "generic_copy_failed"
    if not state.exists:
        return "source_missing"
    if not state.is_file:
        return "source_not_file"
    for reason in (
        "cloud_offline",
        "cloud_recall_on_open",
        "cloud_recall_on_data_access",
        "cloud_reparse_point",
        "cloud_sparse_file",
        "cloud_hydration_failed",
    ):
        if reason in cloud_block_reasons(state):
            return reason
    return "path_source_available"


@dataclass(frozen=True)
class SourceIngestionGateResult:
    allowed: bool
    blocked: bool
    source_kind: str
    reason: str
    required_policy: str | None = None
    cloud_state: CloudFileState | None = None
    safe_label: str | None = None

    @property
    def likely_cloud_placeholder(self) -> bool:
        return bool(self.cloud_state and self.cloud_state.likely_cloud_placeholder)

    def to_public_dict(self) -> dict[str, Any]:
        cloud_state = self.cloud_state.to_dict(include_path=False) if self.cloud_state else None
        return {
            "allowed": self.allowed,
            "blocked": self.blocked,
            "source_kind": self.source_kind,
            "reason": self.reason,
            "required_policy": self.required_policy,
            "safe_label": self.safe_label,
            "cloud_state": cloud_state,
            "paths_redacted": True,
        }


class SourceIngestionGate:
    """Central policy gate for source availability before ingestion reads."""

    @staticmethod
    def evaluate_path_source(
        path: str | Path,
        *,
        safe_label: str | None = None,
        hydration_policy_enabled: bool = False,
    ) -> SourceIngestionGateResult:
        state = classify_cloud_file_state(path)
        if not state.exists:
            return SourceIngestionGateResult(
                allowed=False,
                blocked=True,
                source_kind=SourceKind.PATH_SOURCE.value,
                reason="source_missing",
                required_policy="valid_existing_source_file",
                cloud_state=state,
                safe_label=safe_label,
            )
        if not state.is_file:
            return SourceIngestionGateResult(
                allowed=False,
                blocked=True,
                source_kind=SourceKind.PATH_SOURCE.value,
                reason="source_not_file",
                required_policy="regular_source_file",
                cloud_state=state,
                safe_label=safe_label,
            )
        if state.likely_cloud_placeholder and not hydration_policy_enabled:
            return SourceIngestionGateResult(
                allowed=False,
                blocked=True,
                source_kind=SourceKind.PATH_SOURCE.value,
                reason=primary_cloud_block_reason(state),
                required_policy=CLOUD_POLICY_REQUIRED,
                cloud_state=state,
                safe_label=safe_label,
            )
        return SourceIngestionGateResult(
            allowed=True,
            blocked=False,
            source_kind=SourceKind.PATH_SOURCE.value,
            reason="path_source_available",
            required_policy=None,
            cloud_state=state,
            safe_label=safe_label,
        )

    @staticmethod
    def allow_upload_bytes(*, safe_label: str | None = None) -> SourceIngestionGateResult:
        return SourceIngestionGateResult(
            allowed=True,
            blocked=False,
            source_kind=SourceKind.UPLOAD_BYTES.value,
            reason="upload_bytes_already_supplied",
            required_policy=None,
            cloud_state=None,
            safe_label=safe_label,
        )

    @staticmethod
    def evaluate_staging_file(
        *,
        staging_audit_passed: bool,
        safe_label: str | None = None,
    ) -> SourceIngestionGateResult:
        if not staging_audit_passed:
            return SourceIngestionGateResult(
                allowed=False,
                blocked=True,
                source_kind=SourceKind.STAGING_FILE.value,
                reason="staging_audit_required",
                required_policy=STAGING_AUDIT_POLICY_REQUIRED,
                cloud_state=None,
                safe_label=safe_label,
            )
        return SourceIngestionGateResult(
            allowed=True,
            blocked=False,
            source_kind=SourceKind.STAGING_FILE.value,
            reason="staging_audit_passed",
            required_policy=None,
            cloud_state=None,
            safe_label=safe_label,
        )

    @staticmethod
    def allow_app_managed_file(*, safe_label: str | None = None) -> SourceIngestionGateResult:
        return SourceIngestionGateResult(
            allowed=True,
            blocked=False,
            source_kind=SourceKind.APP_MANAGED_FILE.value,
            reason="app_managed_storage_consistency_applies",
            required_policy=None,
            cloud_state=None,
            safe_label=safe_label,
        )
