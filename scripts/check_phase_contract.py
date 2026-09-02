#!/usr/bin/env python3
"""Check a V.I.O.L.E.T. phase summary against an executable contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_contracts import (  # noqa: E402
    ContractRepositoryContext,
    check_phase_contract,
    get_contract,
    list_contracts,
    load_summary_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a phase summary JSON against an executable contract.")
    parser.add_argument("--contract", help="Contract id to validate, for example source_concept_full_chain_contract_v1.")
    parser.add_argument("--summary", help="Path to summary JSON.")
    parser.add_argument("--phase-kind", help="Optional expected phase kind.")
    parser.add_argument(
        "--repo-root",
        help="Trusted Git repository root for repository-bound contract evidence.",
    )
    parser.add_argument(
        "--expected-python",
        help="Approved interpreter path; actual identity is derived from sys.executable.",
    )
    parser.add_argument(
        "--runtime-ledger",
        help="Private RunLedger JSON used to verify public operation attribution.",
    )
    parser.add_argument(
        "--failure-budget-scenarios",
        help="Private failure-budget before/after RunLedger bundle.",
    )
    parser.add_argument(
        "--reconciliation-scenarios",
        help="Private interrupted/restart/reconciliation RunLedger bundle.",
    )
    parser.add_argument(
        "--fl1-i1-evidence",
        help="Private JSON context containing paths to the complete FL1-I1 evidence bundle.",
    )
    parser.add_argument(
        "--fl1-i2-evidence",
        help="Confined directory containing the complete fixed-name FL1-I2 evidence bundle.",
    )
    parser.add_argument(
        "--px1-evidence",
        help="Confined task-owned temporary directory containing the fixed-name SCV2-PX1 evidence bundle.",
    )
    parser.add_argument(
        "--px2-evidence",
        help="Confined task-owned temporary directory containing the fixed-name SCV2-PX2 evidence bundle.",
    )
    parser.add_argument("--list-contracts", action="store_true", help="List registered contracts as JSON and exit.")
    parser.add_argument("--explain", action="store_true", help="Include contract metadata in output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_contracts:
        payload = {
            "contracts": [
                {
                    "contract_id": contract.contract_id,
                    "contract_version": contract.contract_version,
                    "phase_kind": contract.phase_kind,
                    "description": contract.description,
                }
                for contract in list_contracts()
            ]
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if not args.contract or not args.summary:
        parser.error("--contract and --summary are required unless --list-contracts is used.")

    try:
        contract = get_contract(args.contract)
    except KeyError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stdout)
        return 2

    if args.phase_kind and contract.phase_kind != args.phase_kind:
        print(
            json.dumps(
                {
                    "contract_id": contract.contract_id,
                    "passed": False,
                    "errors": [
                        {
                            "code": "phase_kind_mismatch",
                            "expected": contract.phase_kind,
                            "actual": args.phase_kind,
                        }
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    try:
        summary = load_summary_file(args.summary)
    except Exception as exc:
        print(json.dumps({"contract_id": args.contract, "passed": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    if args.contract == "public_redaction_contract_v1":
        summary_path = Path(args.summary)
        markdown_path = summary_path.with_name(summary_path.name.removesuffix("-summary.json") + ".md")
        if markdown_path.exists():
            summary = {**summary, "public_markdown_text": markdown_path.read_text(encoding="utf-8")}

    repository_context = None
    if (
        args.repo_root
        or args.expected_python
        or args.runtime_ledger
        or args.failure_budget_scenarios
        or args.reconciliation_scenarios
        or args.fl1_i1_evidence
        or args.fl1_i2_evidence
        or args.px1_evidence
        or args.px2_evidence
    ):
        if not args.repo_root:
            parser.error("private evidence options require --repo-root")

        def load_private_json(path: str | None, label: str) -> object | None:
            if not path:
                return None
            try:
                return json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "contract_id": args.contract,
                            "passed": False,
                            "error": f"{label}_unreadable:{type(exc).__name__}",
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                raise SystemExit(2) from exc

        runtime_ledger = load_private_json(args.runtime_ledger, "runtime_ledger")
        failure_budget_scenarios = load_private_json(
            args.failure_budget_scenarios,
            "failure_budget_scenarios",
        )
        reconciliation_scenarios = load_private_json(
            args.reconciliation_scenarios,
            "reconciliation_scenarios",
        )
        fl1_i1_evidence = load_private_json(
            args.fl1_i1_evidence,
            "fl1_i1_evidence",
        )
        fl1_i2_evidence = None
        if args.fl1_i2_evidence:
            from scripts.phase_contracts.fl1_i2_contract import FL1I2EvidencePaths

            # The I2 contract must perform lexical local-temp confinement before
            # any caller-provided evidence path is resolved, stated, or opened.
            fl1_i2_evidence = FL1I2EvidencePaths(Path(args.fl1_i2_evidence))
        scv2_px1_evidence = None
        if args.px1_evidence:
            from scripts.phase_contracts.scv2_px1_contract import Scv2Px1EvidencePaths

            # The PX1 contract performs lexical local-temp confinement before
            # resolving, stating, or opening the caller-provided path.
            scv2_px1_evidence = Scv2Px1EvidencePaths(Path(args.px1_evidence))
        scv2_px2_evidence = None
        if args.px2_evidence:
            from scripts.phase_contracts.scv2_px2_contract import Scv2Px2EvidencePaths

            scv2_px2_evidence = Scv2Px2EvidencePaths(Path(args.px2_evidence))
        repository_context = ContractRepositoryContext(
            repo_root=Path(args.repo_root).resolve(),
            expected_python=(
                Path(args.expected_python) if args.expected_python else None
            ),
            runtime_ledger=runtime_ledger,
            failure_budget_scenario_bundle=failure_budget_scenarios,
            reconciliation_scenario_bundle=reconciliation_scenarios,
            fl1_i1_evidence=fl1_i1_evidence,
            fl1_i2_evidence=fl1_i2_evidence,
            scv2_px1_evidence=scv2_px1_evidence,
            scv2_px2_evidence=scv2_px2_evidence,
        )

    result = check_phase_contract(
        args.contract,
        summary,
        repository_context=repository_context,
    )
    payload = result.to_dict()
    if args.explain:
        payload["contract_explain"] = contract.explain()
    else:
        payload["details"].pop("contract", None)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
