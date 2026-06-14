"""Types for V.I.O.L.E.T. executable phase contracts.

This module is intentionally stdlib-only. Contract checks must be runnable
before a phase imports application runtime code, opens a DB connection, starts a
server, or initializes provider/LLM clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PhaseContract:
    """Machine-readable declaration of phase requirements."""

    contract_id: str
    contract_version: str
    phase_kind: str
    required_inputs: tuple[str, ...] = ()
    required_stages: tuple[str, ...] = ()
    forbidden_stages: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    required_summary_fields: tuple[str, ...] = ()
    required_public_report_sections: tuple[str, ...] = ()
    required_private_artifacts: tuple[str, ...] = ()
    required_validation_commands: tuple[str, ...] = ()
    db_write_policy: str = "not_applicable"
    provider_policy: str = "not_applicable"
    llm_policy: str = "not_applicable"
    mutation_policy: str = "not_applicable"
    redaction_policy: str = "not_applicable"
    review_pack_policy: str = "not_applicable"
    artifact_lifecycle_policy: str = "not_applicable"
    route_decision_policy: str = "not_applicable"
    failure_behavior: str = "fail_closed"
    custom_checks: tuple[str, ...] = ()
    description: str = ""

    def explain(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "phase_kind": self.phase_kind,
            "description": self.description,
            "required_inputs": list(self.required_inputs),
            "required_stages": list(self.required_stages),
            "forbidden_stages": list(self.forbidden_stages),
            "required_artifacts": list(self.required_artifacts),
            "required_summary_fields": list(self.required_summary_fields),
            "required_public_report_sections": list(self.required_public_report_sections),
            "required_private_artifacts": list(self.required_private_artifacts),
            "required_validation_commands": list(self.required_validation_commands),
            "db_write_policy": self.db_write_policy,
            "provider_policy": self.provider_policy,
            "llm_policy": self.llm_policy,
            "mutation_policy": self.mutation_policy,
            "redaction_policy": self.redaction_policy,
            "review_pack_policy": self.review_pack_policy,
            "artifact_lifecycle_policy": self.artifact_lifecycle_policy,
            "route_decision_policy": self.route_decision_policy,
            "failure_behavior": self.failure_behavior,
            "custom_checks": list(self.custom_checks),
        }


@dataclass(frozen=True)
class ContractFinding:
    """Single contract check finding."""

    code: str
    message: str
    path: str = "$"
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }
        if self.expected is not None:
            payload["expected"] = self.expected
        if self.actual is not None:
            payload["actual"] = self.actual
        return payload


@dataclass
class ContractCheckResult:
    """Result returned by the command-line checker."""

    contract_id: str
    passed: bool = True
    status: str | None = None
    route_approved: bool = False
    target_met_claimed: bool = False
    full_chain_complete_claimed: bool = False
    safe_to_merge_claimed: bool = False
    errors: list[ContractFinding] = field(default_factory=list)
    warnings: list[ContractFinding] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def fail(self, code: str, message: str, *, path: str = "$", expected: Any = None, actual: Any = None) -> None:
        self.errors.append(ContractFinding(code, message, path=path, expected=expected, actual=actual))
        self.passed = False

    def warn(self, code: str, message: str, *, path: str = "$", expected: Any = None, actual: Any = None) -> None:
        self.warnings.append(ContractFinding(code, message, path=path, expected=expected, actual=actual))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "passed": self.passed,
            "status": self.status,
            "route_approved": self.route_approved,
            "target_met_claimed": self.target_met_claimed,
            "full_chain_complete_claimed": self.full_chain_complete_claimed,
            "safe_to_merge_claimed": self.safe_to_merge_claimed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [finding.to_dict() for finding in self.errors],
            "warnings": [finding.to_dict() for finding in self.warnings],
            "details": self.details,
        }
