"""Dependency-neutral source-safety observations shared by VIOLET layers.

This package intentionally contains values and observations only. It does not
decide whether an operation may execute; ``SourceIngestionGate`` remains the
single policy authority for those decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class CloudAvailability(str, Enum):
    AVAILABLE = "available"
    RECALL_RISK = "recall_risk"
    REPARSE_POINT = "reparse_point"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FileObjectIdentity:
    platform: str
    volume_id: str
    file_id: str

    def __post_init__(self) -> None:
        if not self.platform or not self.volume_id or not self.file_id:
            raise ValueError("file_object_identity_incomplete")

    def to_private_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FileChangeIdentity:
    change_time_ns: int
    write_time_ns: int
    size: int
    allocation_size: int

    def __post_init__(self) -> None:
        if min(self.change_time_ns, self.write_time_ns, self.size, self.allocation_size) < 0:
            raise ValueError("file_change_identity_invalid")

    def to_private_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class HandleObservation:
    object_identity: FileObjectIdentity
    change_identity: FileChangeIdentity
    cloud_availability: CloudAvailability
    attributes_known: bool
    is_directory: bool
    reparse_point: bool
    reparse_tag: int
    link_count: int
    no_follow: bool
    identity_bound: bool
    no_recall_open_only: bool

    def to_private_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cloud_availability"] = self.cloud_availability.value
        return payload


@dataclass(frozen=True)
class SourceSafetyPolicy:
    policy_version: str
    allowed_source_kinds: tuple[str, ...]
    require_known_attributes: bool = True
    require_no_follow: bool = True
    require_identity_bound: bool = True
    reject_reparse_points: bool = True
    reject_multiple_links: bool = True
    reject_recall_risk: bool = True

    @classmethod
    def from_trusted_config(cls, config: Mapping[str, Any]) -> "SourceSafetyPolicy":
        expected_keys = {
            "policy_version",
            "allowed_source_kinds",
            "require_known_attributes",
            "require_no_follow",
            "require_identity_bound",
            "reject_reparse_points",
            "reject_multiple_links",
            "reject_recall_risk",
        }
        if set(config) != expected_keys:
            raise ValueError("source_safety_config_shape_invalid")
        allowed = config["allowed_source_kinds"]
        if not isinstance(allowed, (list, tuple)) or not allowed:
            raise ValueError("source_safety_allowed_kinds_invalid")
        booleans = {
            key: config[key]
            for key in expected_keys
            if key.startswith("require_") or key.startswith("reject_")
        }
        if any(type(value) is not bool for value in booleans.values()):
            raise ValueError("source_safety_config_boolean_invalid")
        version = config["policy_version"]
        if version != "scv2-fl1-i2-source-safety.v1":
            raise ValueError("source_safety_policy_version_invalid")
        return cls(
            policy_version=version,
            allowed_source_kinds=tuple(str(value) for value in allowed),
            **booleans,
        )

    def to_fingerprint_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceDecision:
    allowed: bool
    reason: str
    source_kind: str
    policy_version: str
    observation: HandleObservation | None

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blocked": self.blocked,
            "reason": self.reason,
            "source_kind": self.source_kind,
            "policy_version": self.policy_version,
            "paths_redacted": True,
        }


__all__ = [
    "CloudAvailability",
    "FileChangeIdentity",
    "FileObjectIdentity",
    "HandleObservation",
    "SourceDecision",
    "SourceSafetyPolicy",
]
