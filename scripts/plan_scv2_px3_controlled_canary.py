#!/usr/bin/env python3
"""Emit exact PX3 owner-gated canary entrypoints; never execute them implicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


GATES = {
    "provider-smoke": "PX3_CONTROLLED_PROVIDER_SMOKE_GATE",
    "existing-db-canary": "PX3_EXISTING_DATABASE_CANARY_GATE",
    "backup-restore": "PX3_BACKUP_RESTORE_GATE",
    "import-canary": "PX3_1_TO_5_PERCENT_IMPORT_CANARY_GATE",
}


def accepted_apply_request(dry_run: dict, *, canary_percent: int) -> dict[str, object]:
    """Carry an actual server dry-run; the apply endpoint rederives its contents."""
    selection = dry_run.get('input_selection') or {}
    unsigned = {key: value for key, value in dry_run.items() if key != 'canonical_fingerprint'}
    fingerprint = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(',', ':'),
                                           ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    if (dry_run.get('status') != 'planned' or dry_run.get('applied') is not False
        or dry_run.get('source_mode') != 'existing_source_metadata'
        or selection.get('percentage') != canary_percent
        or dry_run.get('canonical_fingerprint') != fingerprint
        or dry_run.get('selection_fingerprint') != selection.get('canonical_fingerprint')):
        raise ValueError('px3_actual_dry_run_required')
    fields = {
        'accepted_selection_fingerprint': dry_run.get('selection_fingerprint'),
        'accepted_product_fingerprint': dry_run.get('product_result_fingerprint'),
        'accepted_binding_fingerprint': dry_run.get('media_binding', {}).get('local_binding_fingerprint'),
    }
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) or value == '0'*64
           for value in fields.values()):
        raise ValueError('px3_actual_dry_run_fingerprints_required')
    return dict(mode='apply',canary_percent=canary_percent,confirm=True,
                confirm_phrase='APPLY_PIXIV_SOURCE_CONCEPTS',**fields)


def build_plan(*, gate: str, canary_percent: int, work_limit: int, dry_run: dict | None = None) -> dict[str, object]:
    if gate not in GATES:
        raise ValueError("px3_canary_gate_invalid")
    if (
        isinstance(canary_percent, bool)
        or not isinstance(canary_percent, int)
        or not 1 <= canary_percent <= 5
    ):
        raise ValueError("px3_canary_percentage_invalid")
    if (
        isinstance(work_limit, bool)
        or not isinstance(work_limit, int)
        or not 1 <= work_limit <= 5
    ):
        raise ValueError("px3_provider_work_limit_invalid")
    common_stops = [
        "repository_or_expected_head_mismatch",
        "backup_or_restore_rehearsal_missing",
        "unexpected_existing_scope_drift",
        "private_value_or_credential_exposure",
        "identity_union_or_candidate_accounting_regression",
        "non_sourceconcept_table_write",
        "rollback_guard_failure",
    ]
    plans: dict[str, dict[str, object]] = {
        "provider-smoke": {
            "required_future_authorities": [
                "real_pixiv_network_execution_authorized=true",
                "provider_credentials_authorized=true",
            ],
            "entrypoint": "BLOCKED: historical ingestion CLI does not enforce this work manifest limit",
            "executable_entrypoint_available": False,
            "bounds": {"work_limit": work_limit, "media_download": False},
            "preconditions": [
                "owner-approved exact work manifest containing 1-5 Pixiv works",
                "isolated test database identity and non-production profile",
                "redacted provider profile readiness and spacing/retry controls",
            ],
            "success": [
                "provider request attempts equal the bounded manifest accounting",
                "normalized SourceMetadata records pass canonical Pixiv identity checks",
                "no media or thumbnail download and no raw credential output",
            ],
        },
        "existing-db-canary": {
            "required_future_authorities": [
                "original_database_readonly_query_authorized=true"
            ],
            "entrypoint": (
                "POST /api/admin/pixiv-product-integration/source-metadata/run "
                f'{{"mode":"dry_run","canary_percent":{canary_percent}}}'
            ),
            "apply_entrypoint": None,
            "bounds": {"work_percentage": canary_percent, "full_scan": False},
            "preconditions": [
                "PX3_BACKUP_RESTORE_GATE passed for the exact database identity",
                "dry-run product fingerprint and counts owner accepted",
                "SCV2_PX3_PRODUCT_INTEGRATION_ENABLED and APPLY flags explicitly enabled",
            ],
            "success": [
                "stable hash selection contains exactly ceil(eligible*percent/100) works",
                "apply then replay produces zero row delta",
                "all product and SourceConcept business fingerprints match dry-run",
            ],
        },
        "backup-restore": {
            "required_future_authorities": [
                "original_database_readonly_backup_authorized=true",
                "task_owned_database_create_restore_authorized=true",
            ],
            "entrypoint": "pg_dump --format=custom --file=<exact-backup-artifact> <exact-database>",
            "restore_entrypoint": "pg_restore --exit-on-error --single-transaction --no-owner --no-acl --dbname=<new-task-owned-db> <exact-backup-artifact>",
            "bounds": {"restore_target": "isolated_nonproduction_database_only"},
            "preconditions": [
                "exact database identity recorded before backup",
                "nonzero backup artifact hash recorded",
                "isolated restore target proven not to be the existing application database",
            ],
            "success": [
                "restored schema and bounded SourceConcept counts match backup snapshot",
                "application database remains untouched during restore rehearsal",
                "rollback SQL and product run key are recorded before canary apply",
            ],
        },
        "import-canary": {
            "required_future_authorities": [
                "exact_target_sourceconcept_materialization_authorized=true",
            ],
            "entrypoint": "Capture the actual dry-run JSON, then pass --dry-run-result to generate its exact apply request",
            "apply_entrypoint": None,
            "operation_kind": "sourceconcept_materialization",
            "media_import": False,
            "rollback_entrypoint": (
                "POST /api/admin/pixiv-product-integration/runs/<exact-run-key>/rollback "
                '{"confirm":true,"confirm_phrase":"ROLLBACK_PIXIV_PRODUCT:<exact-run-key>"}'
            ),
            "bounds": {"work_percentage": canary_percent, "allowed_range": "1-5"},
            "preconditions": [
                "controlled provider smoke passed or existing complete metadata selected",
                "existing database dry-run and backup/restore gates passed",
                "selected work IDs and selection fingerprint owner accepted",
            ],
            "success": [
                "all selected bundles and candidate pairs accounted",
                "no cannot-link, deferred, cross-role, or multi-creator union violation",
                "replay is idempotent and exact-run rollback remains available",
            ],
        },
    }
    selected = plans[gate]
    if gate in {'existing-db-canary', 'import-canary'}:
        request = accepted_apply_request(dry_run, canary_percent=canary_percent) if dry_run is not None else None
        selected['apply_request'] = request
        selected['apply_request_ready'] = request is not None
        if request is not None:
            selected['apply_entrypoint'] = 'POST /api/admin/pixiv-product-integration/source-metadata/run ' + json.dumps(request,sort_keys=True)
    plan = {
        "schema_version": "violet.scv2-px3-controlled-canary-plan.v1",
        "gate": GATES[gate],
        "gate_kind": gate,
        "status": "blocked_current_authority_false",
        "current_execution_authorized": False,
        **selected,
        "stop_conditions": common_stops,
        "production_authorized": False,
        "full_library_import_authorized": False,
    }
    plan["canonical_fingerprint"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", choices=tuple(GATES), required=True)
    parser.add_argument("--canary-percent", type=int, default=1)
    parser.add_argument("--work-limit", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument('--dry-run-result', type=Path)
    args = parser.parse_args(argv)
    try:
        plan = build_plan(
            gate=args.gate,
            canary_percent=args.canary_percent,
            work_limit=args.work_limit,
            dry_run=json.loads(args.dry_run_result.read_text(encoding='utf-8')) if args.dry_run_result else None,
        )
    except ValueError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    if args.execute:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
