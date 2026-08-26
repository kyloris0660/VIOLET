"""Canonical source-safety observations used by ingestion policy and FL1 tooling.

This module contains values and observations only. It deliberately does not
decide whether an operation may execute; :class:`SourceIngestionGate` is the
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

    @classmethod
    def from_private_dict(cls, payload: Mapping[str, Any]) -> "FileObjectIdentity":
        if not isinstance(payload, Mapping) or set(payload) != {"platform", "volume_id", "file_id"}:
            raise ValueError("file_object_identity_schema_invalid")
        if any(type(payload[key]) is not str or not payload[key] for key in payload):
            raise ValueError("file_object_identity_schema_invalid")
        return cls(payload["platform"], payload["volume_id"], payload["file_id"])


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

    @classmethod
    def from_private_dict(cls, payload: Mapping[str, Any]) -> "FileChangeIdentity":
        expected = {"change_time_ns", "write_time_ns", "size", "allocation_size"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("file_change_identity_schema_invalid")
        if any(type(payload[key]) is not int for key in expected):
            raise ValueError("file_change_identity_schema_invalid")
        return cls(**{key: payload[key] for key in expected})


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

    @classmethod
    def from_private_dict(cls, payload: Mapping[str, Any]) -> "HandleObservation":
        expected = {
            "object_identity",
            "change_identity",
            "cloud_availability",
            "attributes_known",
            "is_directory",
            "reparse_point",
            "reparse_tag",
            "link_count",
            "no_follow",
            "identity_bound",
            "no_recall_open_only",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("handle_observation_schema_invalid")
        boolean_keys = {
            "attributes_known",
            "is_directory",
            "reparse_point",
            "no_follow",
            "identity_bound",
            "no_recall_open_only",
        }
        if any(type(payload[key]) is not bool for key in boolean_keys):
            raise ValueError("handle_observation_schema_invalid")
        if type(payload["reparse_tag"]) is not int or type(payload["link_count"]) is not int:
            raise ValueError("handle_observation_schema_invalid")
        if payload["reparse_tag"] < 0 or payload["link_count"] <= 0:
            raise ValueError("handle_observation_schema_invalid")
        try:
            availability = CloudAvailability(payload["cloud_availability"])
        except (TypeError, ValueError) as exc:
            raise ValueError("handle_observation_schema_invalid") from exc
        return cls(
            object_identity=FileObjectIdentity.from_private_dict(payload["object_identity"]),
            change_identity=FileChangeIdentity.from_private_dict(payload["change_identity"]),
            cloud_availability=availability,
            attributes_known=payload["attributes_known"],
            is_directory=payload["is_directory"],
            reparse_point=payload["reparse_point"],
            reparse_tag=payload["reparse_tag"],
            link_count=payload["link_count"],
            no_follow=payload["no_follow"],
            identity_bound=payload["identity_bound"],
            no_recall_open_only=payload["no_recall_open_only"],
        )


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
