from __future__ import annotations

import dataclasses

import pytest

from backend.app.services.source_ingestion_gate import (
    SourceIngestionGate,
    SourceKind,
    is_canonical_directory_observation,
)
from backend.app.services.source_safety import (
    CloudAvailability,
    FileChangeIdentity,
    FileObjectIdentity,
    HandleObservation,
    SourceSafetyPolicy,
)
from scripts.fl1_i1_operation_gateway import CloudAvailability as I1CloudAvailability


def _config(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "policy_version": "scv2-fl1-i2-source-safety.v1",
        "allowed_source_kinds": [SourceKind.PATH_SOURCE.value],
        "require_known_attributes": True,
        "require_no_follow": True,
        "require_identity_bound": True,
        "reject_reparse_points": True,
        "reject_multiple_links": True,
        "reject_recall_risk": True,
    }
    payload.update(overrides)
    return payload


def _observation(**overrides: object) -> HandleObservation:
    payload: dict[str, object] = {
        "object_identity": FileObjectIdentity("synthetic", "volume", "object"),
        "change_identity": FileChangeIdentity(1, 1, 9, 9),
        "cloud_availability": CloudAvailability.AVAILABLE,
        "attributes_known": True,
        "is_directory": False,
        "reparse_point": False,
        "reparse_tag": 0,
        "link_count": 1,
        "no_follow": True,
        "identity_bound": True,
        "no_recall_open_only": True,
    }
    payload.update(overrides)
    return HandleObservation(**payload)  # type: ignore[arg-type]


def test_source_ingestion_gate_is_single_policy_authority() -> None:
    policy = SourceSafetyPolicy.from_trusted_config(_config())
    result = SourceIngestionGate.decide_observation(
        source_kind=SourceKind.PATH_SOURCE,
        observation=_observation(),
        policy=policy,
    )
    assert result.allowed
    assert result.reason == "source_observation_accepted"
    assert result.to_public_dict()["paths_redacted"] is True
    assert not hasattr(policy, "allowed")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"attributes_known": False}, "source_attributes_unknown"),
        ({"no_follow": False}, "source_no_follow_unproven"),
        ({"identity_bound": False}, "source_identity_unbound"),
        ({"reparse_point": True}, "source_reparse_point_rejected"),
        ({"link_count": 2}, "source_hard_link_rejected"),
        ({"cloud_availability": CloudAvailability.RECALL_RISK}, "source_cloud_availability_rejected"),
    ],
)
def test_policy_fail_closed_reasons(changes: dict[str, object], reason: str) -> None:
    result = SourceIngestionGate.decide_observation(
        source_kind=SourceKind.PATH_SOURCE,
        observation=_observation(**changes),
        policy=SourceSafetyPolicy.from_trusted_config(_config()),
    )
    assert result.blocked
    assert result.reason == reason


def test_policy_is_rederived_from_exact_trusted_shape() -> None:
    with pytest.raises(ValueError, match="source_safety_config_shape_invalid"):
        SourceSafetyPolicy.from_trusted_config({**_config(), "caller_allowed": True})
    with pytest.raises(ValueError, match="source_safety_config_boolean_invalid"):
        SourceSafetyPolicy.from_trusted_config(_config(reject_recall_risk=1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(SourceSafetyPolicy.from_trusted_config(_config()), "reject_recall_risk", False)


def test_i1_compatibility_uses_canonical_cloud_availability_enum() -> None:
    assert I1CloudAvailability is CloudAvailability


@pytest.mark.parametrize(
    "changes",
    [
        {"attributes_known": False},
        {"no_follow": False},
        {"identity_bound": False},
        {"reparse_point": True},
        {"cloud_availability": CloudAvailability.UNKNOWN},
        {"cloud_availability": CloudAvailability.RECALL_RISK},
    ],
)
def test_canonical_directory_predicate_fails_closed(
    changes: dict[str, object],
) -> None:
    assert not is_canonical_directory_observation(
        _observation(is_directory=True, **changes)
    )


def test_worker_and_contract_share_the_canonical_directory_predicate() -> None:
    from scripts import fl1_i2_worker
    from scripts.phase_contracts import fl1_i2_contract

    assert is_canonical_directory_observation(
        _observation(is_directory=True)
    )
    assert (
        fl1_i2_worker.is_canonical_directory_observation
        is is_canonical_directory_observation
    )
    assert (
        fl1_i2_contract.is_canonical_directory_observation
        is is_canonical_directory_observation
    )
