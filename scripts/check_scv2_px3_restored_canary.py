"""Private PostgreSQL rehearsal gate within the registered SCV2-PX3 contract.

Read fixed local evidence only. Never connect to a database, follow metadata
artifact pointers, or grant original-database apply authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.plan_scv2_px3_controlled_canary import accepted_apply_request
from scripts.phase_contracts import get_contract

CONTRACT_ID = 'scv2_px3_pixiv_product_integration_contract_v1'
FILES = (
    'original-backup-private.json', 'copy-restore-private.json',
    'copy-migration-private.json', 'copy-dry-run-private.json',
    'copy-accepted-request-private.json', 'copy-apply-private.json',
    'copy-replay-private.json', 'copy-baseline-probes-private.json',
    'copy-after-probes-private.json', 'copy-other-selection-rejected-private.json',
    'copy-rollback-private.json', 'copy-repeated-rollback-private.json',
    'copy-rollback-baseline-private.json', 'copy-browser-private.json',
    'copy-final-database-private.json', 'copy-runtime-isolation-private.json',
)


def require(condition, code):
    if not condition:
        raise ValueError(code)


def validate_observations(data):
    """Cross-check actual plan, committed rows, recall and rollback evidence."""
    plan = data['copy-dry-run-private.json']
    request = accepted_apply_request(plan, canary_percent=1)
    require(request == data['copy-accepted-request-private.json'], 'accepted_request_mismatch')
    selection = plan['input_selection']
    selected = selection['selected_work_count']
    require(selected == (selection['eligible_work_count'] + 99) // 100 and selected > 0, 'work_percentage_invalid')
    apply = data['copy-apply-private.json']
    fresh = data['copy-final-database-private.json']
    count = plan['media_binding']['planned_signal_source_media_binding_count']
    require(count > 0 and apply['media_binding']['binding_write_count'] == count
            == fresh['binding_rows'] == fresh['persisted_binding_write_count'], 'fresh_session_binding_count_mismatch')
    require(apply['product_result_fingerprint'] == request['accepted_product_fingerprint']
            == fresh['result_fingerprint'], 'committed_product_mismatch')
    require(apply['persistence']['rollback_available'] is True, 'rollback_unavailable')
    replay = data['copy-replay-private.json']
    require(replay['idempotent_replay'] is True and replay['media_binding']['binding_write_count'] == 0, 'replay_wrote_bindings')
    before = data['copy-baseline-probes-private.json']
    after = data['copy-after-probes-private.json']
    require(len(before) == len(after) == plan['media_binding']['planned_media_binding_count'], 'media_probe_count_mismatch')
    before_ids = [p['media_id'] for p in before]
    after_ids = [p['media_id'] for p in after]
    require(len(set(before_ids)) == len(before_ids) and len(set(after_ids)) == len(after_ids)
            and set(before_ids) == set(after_ids) == set(fresh['bound_media_ids']), 'media_probe_set_mismatch')
    prior_by_media = {p['media_id']: p for p in before}
    delta = 0
    for current in after:
        media = current['media_id']
        prior = prior_by_media[media]
        creator_media = {p['media_id'] for p in after if p['creator_id'] == current['creator_id']}
        require(media in current['search'] and media in current['identity'], 'sourceconcept_recall_missing')
        expected = set(prior['search']) | creator_media
        require(set(current['search']) == expected, 'sourceconcept_search_false_positive')
        require(set(current['identity']) == set(prior['identity']) | creator_media, 'identity_search_false_positive')
        and_media = {p['media_id'] for p in after if p['and_query'] == current['and_query']}
        require(set(current['and_search']) == set(prior['and_search']) | and_media, 'and_search_false_positive')
        require(media in current['and_search'] and any(c.get('local_media_support') for c in current['detail']), 'and_search_or_detail_missing')
        delta += media not in prior['search']
    require(delta > 0, 'no_sourceconcept_specific_recall_delta')
    rolled = data['copy-rollback-baseline-private.json']
    migration = data['copy-migration-private.json']
    require(rolled['snapshots'] == migration['base_snapshot'] and rolled['search_detail_equals_baseline'] is True
            and rolled['binding_rows'] == rolled['active_runs'] == 0, 'rollback_baseline_mismatch')
    rollback = data['copy-rollback-private.json']
    require(rollback['rolled_back'] is True and rollback['status'] == 'rolled_back'
            and rollback['run_key'] == apply['run_key'] == plan['run_key']
            and rollback['deleted_core_rows']['media_bindings'] == count
            and rollback['deleted_core_rows']['resolution_runs'] == 1
            and rollback['product_audit_rows_retained'] is True
            and rollback['forbidden_truth_table_write_count'] == 0, 'rollback_response_invalid')
    require(data['copy-repeated-rollback-private.json']['idempotent_replay'] is True, 'repeated_rollback_failed')
    require(data['copy-other-selection-rejected-private.json']['reason'] == 'px3_other_active_selection_requires_rollback'
            and fresh['active_runs'] == 1, 'selection_accumulation')
    browser = data['copy-browser-private.json']
    require(browser['passed'] is True and browser['reapply_verified'] is True
            and browser['rollback_baseline_verified'] is True and browser['initial_full_detail_requests'] == 0
            and browser['original_media_network_requests'] == 0 and not browser['page_errors'], 'browser_gate_failed')
    require(fresh['frozen_metadata_and_truth_unchanged'] is True and fresh['runtime_metadata_writes_denied'] is True,
            'frozen_input_boundary_failed')
    isolation = data['copy-runtime-isolation-private.json']
    require(isolation['copy_oid'] == fresh['database_oid'] == data['copy-restore-private.json']['database_oid']
            and isolation['copy_oid'] != data['original-backup-private.json']['source_identity']['database_oid']
            and all(isolation[key] is True for key in ('source_metadata_refresh_denied', 'truth_write_denied',
                'single_process', 'original_database_connection_config_absent'))
            and all(isolation[key] is False for key in ('redis_enabled', 'background_jobs', 'original_media_access')),
            'runtime_isolation_invalid')
    storage = Path(isolation['runtime_storage_root'])
    task = Path(isolation['task_directory'])
    require(storage.is_absolute() and task.is_absolute() and storage != task and storage.is_relative_to(task),
            'runtime_storage_boundary_invalid')
    allowed = {'blombooru_source_concepts'} | {'blombooru_source_concept_' + suffix for suffix in (
        'aliases', 'signals', 'evidence', 'search_index', 'signal_links', 'resolution_runs',
        'product_runs', 'product_clusters', 'candidate_dispositions', 'ambiguity_records', 'product_media_bindings')}
    require(set(isolation['writable_tables']) <= allowed and isolation['writable_tables'], 'runtime_write_grants_invalid')
    return {'passed': True, 'contract_id': CONTRACT_ID, 'scope': 'restored_postgresql_metadata_canary',
            'eligible_work_count': selection['eligible_work_count'], 'selected_work_count': selected,
            'media_count': len(after), 'binding_count': count, 'original_database_apply_authorized': False}


def verify(root, expected_receipt_sha256):
    root = root.resolve(strict=True)
    receipt_path = root / 'restored-canary-receipt-private.json'
    raw = receipt_path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_receipt_sha256, 'receipt_digest_mismatch')
    receipt = json.loads(raw)
    require(set(receipt['artifacts']) == set(FILES), 'evidence_member_set_mismatch')
    data = {}
    for name in FILES:
        raw = (root / name).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == receipt['artifacts'][name], 'evidence_digest_mismatch:' + name)
        data[name] = json.loads(raw)
    backup = data['original-backup-private.json']
    restore = data['copy-restore-private.json']
    require(restore['backup_sha256'] == backup['backup_sha256'] and restore['source_and_copy_distinct'] is True
            and restore['foreign_keys_validated'] is True and restore['restored_counts'] == backup['source_table_counts'], 'independent_restore_mismatch')
    artifact = Path(backup['backup_file']).resolve(strict=True)
    require(artifact.parent == root, 'backup_not_in_task_directory')
    with artifact.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == backup['backup_sha256'], 'backup_content_drift')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if head != receipt['implementation_head']:
        from scripts.scv2_px3_validation_receipt import validate_px3_evidence_carry_forward
        validate_px3_evidence_carry_forward(ROOT, evidence_head=receipt['implementation_head'],
                                           evidence_tree=receipt['implementation_tree'])
    for relative, digest in receipt['runtime_source_sha256'].items():
        require(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, 'runtime_source_drift:' + relative)
    require(get_contract(CONTRACT_ID).phase_kind == 'scv2_px3_pixiv_product_integration', 'unregistered_phase_contract')
    return validate_observations(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--expected-receipt-sha256', required=True)
    args = parser.parse_args()
    try:
        result = verify(args.evidence_dir, args.expected_receipt_sha256)
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({'passed': False, 'error': type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
