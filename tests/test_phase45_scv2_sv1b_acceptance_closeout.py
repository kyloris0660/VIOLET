from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import run_phase45_scv2_sv1b_acceptance_closeout as closeout


def _composite_payload() -> dict[str, object]:
    rows = []
    for case_id in closeout.EXPECTED_CASE_IDS:
        waived = case_id in closeout.WAIVED_CASE_IDS
        rows.append(
            {
                "case_id": case_id,
                "final_disposition": (
                    "owner_waived_nonblocking_known_limitation" if waived else "pass"
                ),
                "underlying_case_mismatch_preserved": waived,
            }
        )
    payload: dict[str, object] = {
        "schema_version": "sv1b_final_composite_owner_acceptance_v1",
        "accepted_implementation_head": closeout.ACCEPTED_IMPLEMENTATION_HEAD,
        "binding_fingerprint": closeout.BINDING_FINGERPRINT,
        "case_manifest_sha256": closeout.MANIFEST_SHA256,
        "owner_waiver": {
            "identity": closeout.OWNER_WAIVER_IDENTITY,
            "does_not_convert_underlying_mismatch_to_pass": True,
        },
        "summary": {
            "pass_count": 37,
            "owner_waived_nonblocking_known_limitation_count": 3,
            "pending_count": 0,
            "unwaived_fail_count": 0,
        },
        "cases": rows,
    }
    payload["composite_fingerprint"] = closeout.payload_fingerprint(payload)
    return payload


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def test_composite_keeps_owner_waivers_distinct_from_pass(tmp_path: Path) -> None:
    path = tmp_path / closeout.COMPOSITE_NAME
    _write(path, _composite_payload())

    result = closeout.validate_composite(path)

    assert result["passed"] is True
    assert result["summary"]["pass_count"] == 37
    assert result["summary"]["owner_waived_nonblocking_known_limitation_count"] == 3


@pytest.mark.parametrize("case_id", closeout.WAIVED_CASE_IDS)
def test_composite_rejects_waiver_forced_into_pass(
    tmp_path: Path, case_id: str
) -> None:
    payload = _composite_payload()
    row = next(item for item in payload["cases"] if item["case_id"] == case_id)
    row["final_disposition"] = "pass"
    payload["composite_fingerprint"] = closeout.payload_fingerprint(
        {key: value for key, value in payload.items() if key != "composite_fingerprint"}
    )
    path = tmp_path / closeout.COMPOSITE_NAME
    _write(path, payload)

    with pytest.raises(closeout.CloseoutError, match="pass_membership"):
        closeout.validate_composite(path)


def test_composite_rejects_self_fingerprint_drift(tmp_path: Path) -> None:
    payload = _composite_payload()
    payload["summary"]["pending_count"] = 1
    path = tmp_path / closeout.COMPOSITE_NAME
    _write(path, payload)

    with pytest.raises(closeout.CloseoutError, match="self_fingerprint"):
        closeout.validate_composite(path)


def test_delta_validation_requires_exact_case_membership() -> None:
    rows = [{"case_id": case_id} for case_id in closeout.EXPECTED_CASE_IDS]
    payload: dict[str, object] = {"cases": rows}
    payload["audit_payload_fingerprint"] = closeout.payload_fingerprint(payload)
    assert len(closeout._validate_delta(payload)) == 40

    bad = copy.deepcopy(payload)
    bad["cases"][-1]["case_id"] = "A01"
    body = {key: value for key, value in bad.items() if key != "audit_payload_fingerprint"}
    bad["audit_payload_fingerprint"] = closeout.payload_fingerprint(body)
    with pytest.raises(closeout.CloseoutError, match="duplicate"):
        closeout._validate_delta(bad)


def test_write_is_exclusive_and_does_not_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "proof.json"
    closeout._write_exclusive_atomic(path, {"one": 1})
    before = path.read_bytes()

    with pytest.raises(closeout.CloseoutError, match="immutable_output_exists"):
        closeout._write_exclusive_atomic(path, {"two": 2})

    assert path.read_bytes() == before


def test_carry_forward_rejects_runtime_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    composite = tmp_path / closeout.COMPOSITE_NAME
    _write(composite, _composite_payload())

    responses = iter(
        [
            "f" * 40,
            "backend/app/models.py\n",
        ]
    )
    monkeypatch.setattr(closeout, "_git", lambda *_args: next(responses))

    with pytest.raises(closeout.CloseoutError, match="runtime_or_data_path_changed"):
        closeout.create_carry_forward(
            repo_root=tmp_path, composite_path=composite, output_root=tmp_path / "out"
        )


def test_carry_forward_rejects_same_implementation_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    composite = tmp_path / closeout.COMPOSITE_NAME
    _write(composite, _composite_payload())
    monkeypatch.setattr(
        closeout, "_git", lambda *_args: closeout.ACCEPTED_IMPLEMENTATION_HEAD
    )

    with pytest.raises(closeout.CloseoutError, match="not_advanced"):
        closeout.create_carry_forward(
            repo_root=tmp_path, composite_path=composite, output_root=tmp_path / "out"
        )
