"""Snapshot, plan, apply or withdraw one fixed production Pixiv projection.

Use the existing profile, verified backup/restore identities, source facts and
owned product transactions. All evidence is private and repo local. A plan is
generated on the selected database and must still match when applied.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def compact_result(value):
    return {key:item for key,item in value.items()
            if key not in {'clusters','candidate_dispositions','ambiguity_records'}}


def database_state(session,run_key=None):
    from app.models import SourceConceptProductRun,SourceConceptProductMediaBinding
    rows=session.query(SourceConceptProductRun).filter(
        SourceConceptProductRun.source_mode.in_(['existing_source_metadata','production_scope']),
        SourceConceptProductRun.status=='active').all()
    bindings=session.query(SourceConceptProductMediaBinding).filter(
        SourceConceptProductMediaBinding.product_run_id.in_([row.id for row in rows])).all()
    return {'active_runs':len(rows),'run_keys':[row.run_key for row in rows],
        'bindings':len(bindings),'bound_media_ids':sorted({row.media_id for row in bindings}),
        'source_record_ids':sorted({row.source_metadata_record_id for row in bindings}),
        'duplicate_support_count':len(bindings)-len({(row.evidence_id,row.source_metadata_record_id,row.media_id) for row in bindings})}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('snapshot','plan','apply','rollback'))
    parser.add_argument('--artifacts',required=True,type=Path)
    parser.add_argument('--profile',required=True,type=Path)
    parser.add_argument('--database',required=True)
    parser.add_argument('--expected-system-id',required=True)
    parser.add_argument('--expected-python',required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--aggregates',type=Path)
    parser.add_argument('--vocabulary',type=Path)
    parser.add_argument('--role-facts',type=Path)
    parser.add_argument('--judgments',type=Path)
    parser.add_argument('--semantic-manifest',type=Path)
    parser.add_argument('--allow-partial-copy',action='store_true')
    parser.add_argument('--accepted-plan',type=Path)
    parser.add_argument('--run-key')
    args=parser.parse_args()
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend'))
    from scripts.check_python_env import run_checks
    from scripts.run_production_pixiv_a2_metadata import read,write,exclusive
    from scripts.violet_production_control import _profile_to_env
    if not run_checks(args.expected_python,str(ROOT))['pass']:
        raise RuntimeError('python_identity_preflight_failed')
    out=args.artifacts.resolve(strict=True)
    if not out.is_relative_to((ROOT/'.local_manifests').resolve()) or not re.fullmatch('[a-z0-9-]+',args.label):
        raise RuntimeError('private_product_artifact_location_invalid')
    for path in (args.aggregates,args.vocabulary,args.role_facts,args.judgments,args.accepted_plan,args.semantic_manifest):
        if path and not path.resolve(strict=True).is_relative_to(out):
            raise RuntimeError('product_input_outside_task')
    profile=read(args.profile);cfg=profile['db']
    backup=read(out/'backup-private.json');restore=read(out/'restore-private.json')
    if (cfg['name']!=backup['database'] or args.expected_system_id!=backup['system_identifier']
        or args.database not in {cfg['name'],restore['target']} or not restore['restore_passed']
        or cfg['host'] not in {'localhost','127.0.0.1'}):
        raise RuntimeError('product_target_outside_verified_backup_restore')
    production=args.database==cfg['name']
    if production and args.allow_partial_copy:
        raise RuntimeError('partial_experiments_require_isolated_database')
    os.environ.update(_profile_to_env(profile,repo_root=ROOT))
    os.environ.update(POSTGRES_DB=args.database,TAG_TRANSLATION_LLM_ENABLED='false',
        TAG_TRANSLATION_LLM_FALLBACK_ENABLED='false')
    if not production:
        os.environ.update(VIOLET_ENV='test',VIOLET_STORAGE_ROOT=str(out/'offline-storage'))
    from sqlalchemy import create_engine,text
    from sqlalchemy.engine import URL
    from sqlalchemy.orm import Session
    from app.services.production_pixiv_service import (
        build_production_inputs,production_consumer,build_production_clustering,replace_production_projection,PRODUCTION_POLICY)
    from app.services.pixiv_product_integration_service import rollback_pixiv_product_run
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    scope=read(out/'fixed-scope-private.json')
    from app.services.production_pixiv_release_inputs import verify_t0_scope,verify_full_input,verify_semantic_manifest
    t0_identity=verify_t0_scope(scope,read(out/'t0-inventory-private.json'),cfg['name'],args.expected_system_id)
    source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    from scripts.trusted_git import candidate_behavior_carry_forward
    if not candidate_behavior_carry_forward(ROOT,source_head):
        raise RuntimeError('candidate_has_uncommitted_behavior_changes')
    if production and args.action in {'plan','apply'} and not all((args.role_facts,args.judgments,args.semantic_manifest)):
        raise RuntimeError('production_complete_semantic_artifacts_required')
    url=URL.create('postgresql+psycopg2',username=cfg['user'],password=cfg['password'],
        host=cfg['host'],port=cfg['port'],database=args.database)
    readonly=args.action in {'snapshot','plan'}
    options='-c statement_timeout=120000 -c lock_timeout=5000'
    if readonly:options+=' -c default_transaction_read_only=on'
    engine=create_engine(url,connect_args={'options':options})
    receipt={'schema_version':'violet.production-pixiv-execution.v1','action':args.action,
        'database':args.database,'system_identifier':args.expected_system_id,'production':production,
        'scope_fingerprint':scope['canonical_fingerprint'],'source_head':source_head,'policy_version':PRODUCTION_POLICY,
        'started_at':datetime.now(timezone.utc).isoformat(),'t0_identity':t0_identity}
    started=time.monotonic()
    with exclusive(out/'product-runner.lock'),Session(engine) as session:
        if readonly:session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        identity=session.execute(text('select current_database(),system_identifier::text from pg_control_system()')).one()
        if tuple(identity)!=(args.database,args.expected_system_id):
            raise RuntimeError('product_live_database_identity_mismatch')
        receipt['before']=database_state(session)
        if args.action=='snapshot':
            aggregates,coverage=build_production_inputs(session,scope)
            path=out/f'{args.label}-aggregates-private.json'
            if path.exists() and read(path)!=list(aggregates):
                raise RuntimeError('immutable_snapshot_label_already_used')
            write(path,aggregates);write(out/f'{args.label}-coverage-private.json',coverage)
            receipt['coverage']={key:value for key,value in coverage.items() if key not in {'items','works','tail_media_ids'}}
        elif args.action=='rollback':
            if not args.run_key:raise RuntimeError('exact_product_run_key_required')
            from app.models import SourceConceptProductRun
            owned=session.query(SourceConceptProductRun).filter_by(run_key=args.run_key).one_or_none()
            if (owned is None or owned.source_mode!='production_scope'
                or owned.scope_key!='pixiv:production:'+scope['canonical_fingerprint'][:32]):
                raise RuntimeError('rollback_outside_current_production_scope')
            # The repository guard proves all rows and rejects new independent
            # references. A broad run or source-table delete is never used.
            receipt['result']=rollback_pixiv_product_run(session,args.run_key)
        else:
            if not args.aggregates or not args.vocabulary:
                raise RuntimeError('frozen_aggregates_and_vocabulary_required')
            aggregates=read(args.aggregates)
            live,coverage=build_production_inputs(session,scope,
                work_ids={row['work_id'] for row in aggregates} if args.allow_partial_copy else None)
            if not args.allow_partial_copy:verify_full_input(aggregates,live,coverage)
            elif canonical_fingerprint(live)!=canonical_fingerprint(aggregates):
                raise RuntimeError('selected_source_snapshot_changed_refresh_required')
            receipt['tail_media_ids']=coverage['tail_media_ids']
            if args.semantic_manifest:
                receipt['semantic_input_identity']=verify_semantic_manifest(read(args.semantic_manifest),
                    aggregates,read(args.vocabulary),read(args.role_facts),read(args.judgments),source_head)
            run=build_production_clustering(production_consumer(aggregates),
                vocabulary=read(args.vocabulary),role_facts=read(args.role_facts) if args.role_facts else None,
                judgments=read(args.judgments) if args.judgments else ())
            receipt['input_fingerprint']=canonical_fingerprint(aggregates)
            receipt['run_id']=run.resolution.run_id
            if args.action=='plan':
                receipt['result']=compact_result(replace_production_projection(session,run,scope=scope))
            else:
                if not args.accepted_plan:raise RuntimeError('actual_target_plan_required')
                accepted=read(args.accepted_plan)
                if (accepted['database']!=args.database or accepted['system_identifier']!=args.expected_system_id
                    or accepted['scope_fingerprint']!=scope['canonical_fingerprint'] or accepted['action']!='plan'
                    or accepted['source_head']!=source_head
                    or accepted.get('semantic_input_identity')!=receipt.get('semantic_input_identity')):
                    raise RuntimeError('accepted_plan_target_mismatch')
                receipt['result']=compact_result(replace_production_projection(session,run,scope=scope,apply=True,
                    accepted_plan=accepted['result']))
        receipt['after']=database_state(session)
        receipt['seconds']=time.monotonic()-started
        receipt['finished_at']=datetime.now(timezone.utc).isoformat()
        write(out/f'{args.label}-{args.action}-private.json',receipt)
    engine.dispose()
    print(json.dumps({key:value for key,value in receipt.items() if key not in {'result','before','after'}},ensure_ascii=False))
    print(json.dumps({'active_runs':receipt['after']['active_runs'],'bindings':receipt['after']['bindings'],
        'bound_media':len(receipt['after']['bound_media_ids'])},ensure_ascii=False))


if __name__=='__main__':main()
