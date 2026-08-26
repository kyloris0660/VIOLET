"""Privacy-safe CLI projection helpers for the FL1-I2 runner."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PublicCLIError:
    schema_version: str
    status: str
    code: str
    correlation_token: str | None
    paths_redacted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "error": {"code": self.code, "correlation_token": self.correlation_token},
            "paths_redacted": self.paths_redacted,
        }


PUBLIC_ERROR_CODES = frozenset(
    {
        "budget_exhausted",
        "manifest_drift",
        "policy_rejected",
        "source_deferred",
        "worker_interrupted",
        "worker_termination_unconfirmed",
        "validation_failed",
    }
)


def public_error_envelope(error: BaseException) -> dict[str, Any]:
    raw_code = getattr(error, "public_code", None)
    if raw_code in PUBLIC_ERROR_CODES:
        code = str(raw_code)
        correlation = None
    else:
        code = "internal_error"
        correlation = hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:24]
    return PublicCLIError("violet.scv2-fl1-i2-cli-error.v1", "failed", code, correlation).to_dict()


def public_success_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"status", "run_token", "item_counts", "operation_counts", "gate_counts"}
    if set(payload) - allowed:
        raise ValueError("public_success_payload_field_forbidden")
    return {
        "schema_version": "violet.scv2-fl1-i2-cli-result.v1",
        **dict(payload),
        "paths_redacted": True,
    }


def render_public_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
