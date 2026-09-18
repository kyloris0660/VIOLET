"""Resume fixed-scope metadata through the existing Pixiv queue.

All paths are explicit, private outputs stay in the worktree's ignored area.
No credential value or media path is printed. A task-wide dispatch journal is
flushed before each external invocation; unknown interrupted calls consume an
attempt. Raw returned JSON survives database/normalization failures for replay.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.flush();os.fsync(stream.fileno())
    # Windows readers may briefly deny replacement even when the writer has
    # normal directory access. Retry this same atomic publication only; a
    # persistent denial still fails and leaves the previous checkpoint intact.
    for attempt in range(8):
        try:
            os.replace(temporary,path)
            break
        except PermissionError:
            if attempt==7:raise
            time.sleep(0.05*(attempt+1))


def append(path,value):
    with path.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),**value},ensure_ascii=False)+'\n')
        stream.flush();os.fsync(stream.fileno())


def complete_remote_page_domain(observed, declared):
    """Check the received domain without expanding a provider-declared count."""
    if not observed or len(declared)!=1:
        return False
    count=next(iter(declared))
    return (type(count) is int and count>0 and len(observed)==count
            and min(observed)==0 and max(observed)==count-1)


def valid_raw_payload(path, work_id, required_pages=()):
    """A nonempty or orphan file is not proof of a reusable provider result."""
    from app.services.pixiv_metadata_ingestion_service import parse_gallery_dl_stdout, PixivMetadataGateError
    try:
        pages = parse_gallery_dl_stdout(path.read_text(encoding='utf-8'), work_id)
        observed = {page['page_index'] for page in pages}
        declared = {page.get('page_count') for page in pages}
        if not observed or len(declared) != 1:
            return False
        # Complete JSON can still end at a page boundary. A full remote domain
        # also proves a genuinely nonexistent local page; otherwise all fixed
        # targets must be present before this response can suppress acquisition.
        complete_remote = complete_remote_page_domain(observed, declared)
        required = set(required_pages)
        return complete_remote or bool(required) and required <= observed
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, PixivMetadataGateError):
        return False


def recover_raw_payloads(out, events, scope_fingerprint, required_pages_by_work=None):
    attempts = Counter()
    candidates = {}
    failed_paths = set()
    for event in events:
        if event['event'] == 'returned' and event['returncode'] != 0:
            failed_paths.add((out / event['stdout']).resolve())
    for event in events:
        work = event['work_id']
        if event['event'] == 'dispatch':
            if event.get('scope') != scope_fingerprint:
                raise RuntimeError('dispatch_scope_mismatch')
            attempts[work] += 1
            path = out / 'metadata-raw' / f"{work}-attempt-{event['attempt']}.json"
        elif event['event'] == 'returned' and event['returncode'] == 0:
            path = out / event['stdout']
        else:
            continue
        path = path.resolve()
        if not path.is_relative_to(out.resolve()):
            raise RuntimeError('raw_payload_outside_artifacts')
        required = (required_pages_by_work or {}).get(work, ())
        if path not in failed_paths and valid_raw_payload(path, work, required):
            candidates[work] = path
    return attempts, candidates


def publish_raw_response(raw_dir, work, attempt, result, required_pages=()):
    """Only validated success is atomically admitted as a replayable payload."""
    temporary = raw_dir / f'{work}-attempt-{attempt}.pending'
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(result.stdout or '')
        stream.flush(); os.fsync(stream.fileno())
    valid = result.returncode == 0 and valid_raw_payload(temporary, work, required_pages)
    suffix = 'json' if valid else 'diagnostic'
    destination = raw_dir / f'{work}-attempt-{attempt}.{suffix}'
    if destination.exists():
        raise RuntimeError('immutable_raw_response_already_exists')
    temporary.rename(destination)
    return destination, valid


def record_command_response(out,raw_dir,journal,work,attempt,result,required_pages=()):
    """Keep post-command evidence handling outside provider retry decisions."""
    try:
        stdout,replayable=publish_raw_response(raw_dir,work,attempt,result,required_pages)
        stderr=raw_dir/f'{work}-attempt-{attempt}.stderr'
        with stderr.open('x',encoding='utf-8') as stream:
            stream.write(result.stderr or '');stream.flush();os.fsync(stream.fileno())
        append(journal,{'event':'returned','work_id':work,'attempt':attempt,'returncode':result.returncode,
            'stdout':str(stdout.relative_to(out)),'replayable':replayable})
    except OSError as exc:
        # The subprocess already returned. Local disk failure is not a new
        # transport attempt; stop and retain the published/orphan evidence.
        raise RuntimeError('metadata_command_evidence_persistence_failed') from exc
    return result


@contextmanager
def exclusive(path):
    with path.open('a+b') as stream:
        if stream.tell()==0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name=='nt':
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream,fcntl.LOCK_UN)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','acquire','status','close-page-mismatches'])
    parser.add_argument('--artifacts',required=True,type=Path)
    parser.add_argument('--profile',required=True,type=Path)
    parser.add_argument('--database',required=True)
    parser.add_argument('--expected-system-id',required=True)
    parser.add_argument('--expected-python',required=True)
    parser.add_argument('--limit',type=int,default=0,help='0 consumes the finite remaining manifest')
    parser.add_argument('--replay-only',action='store_true')
    args=parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend'))
    from scripts.check_python_env import run_checks
    if not run_checks(args.expected_python,str(ROOT))['pass']:
        raise RuntimeError('python_identity_preflight_failed')
    out=args.artifacts.resolve(strict=True)
    if not out.is_relative_to((ROOT/'.local_manifests').resolve()):
        raise RuntimeError('private_artifacts_must_be_repo_local')
    profile=read(args.profile);cfg=profile['db'];backup=read(out/'backup-private.json')
    restore=read(out/'restore-private.json')
    if cfg['name']!=backup['database'] or args.expected_system_id!=backup['system_identifier']:
        raise RuntimeError('profile_backup_identity_mismatch')
    if args.database not in (cfg['name'],restore['target']) or not restore['restore_passed']:
        raise RuntimeError('target_outside_verified_backup_restore')
    if cfg['host'] not in ('localhost','127.0.0.1'):
        raise RuntimeError('target_not_local')
    os.environ.update(VIOLET_SKIP_DOTENV='1',VIOLET_ENV='test',POSTGRES_DB=restore['target'],
                      VIOLET_STORAGE_ROOT=str(out/'offline-storage'))
    from sqlalchemy import create_engine,text
    from sqlalchemy.engine import URL
    from sqlalchemy.orm import Session
    from app.models import Media,SourceMetadataRecord
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    from app.services.pixiv_filename_prior_service import distinct_work_pages,parse_approved_fields
    from app.services.pixiv_metadata_ingestion_service import (
        queue_media_for_pixiv_metadata,run_bounded_acquisition,QUEUE_METADATA_KIND,
        PersistentRequestSpacing,PixivMetadataState,
        parse_gallery_dl_stdout,defer_proven_source_page_mismatch,
    )
    scope=read(out/'fixed-scope-private.json')
    if canonical_fingerprint({k:v for k,v in scope.items() if k!='canonical_fingerprint'})!=scope['canonical_fingerprint']:
        raise RuntimeError('fixed_scope_fingerprint_invalid')
    url=URL.create('postgresql+psycopg2',username=cfg['user'],password=cfg['password'],host=cfg['host'],port=cfg['port'],database=args.database)
    engine=create_engine(url,connect_args={'options':'-c statement_timeout=30000 -c lock_timeout=3000'})
    target='production' if args.database==cfg['name'] else 'restored'
    journal=out/'metadata-dispatch-private.jsonl'
    with exclusive(out/'metadata-runner.lock'),Session(engine) as session:
        identity=session.execute(text('select current_database(),system_identifier::text from pg_control_system()')).one()
        if tuple(identity)!=(args.database,args.expected_system_id):
            raise RuntimeError('live_database_identity_mismatch')
        session.rollback()
        mappings={int(row['media_id']):row for row in scope['mappings'] if row['work_id']}
        required_pages_by_work=defaultdict(set)
        for mapped in mappings.values():
            required_pages_by_work[mapped['work_id']].add(mapped['page_index'])
        if args.action=='prepare':
            counts=Counter();changed=[]
            media=session.query(Media).filter(Media.id.in_(sorted(mappings))).order_by(Media.id).all()
            for index,item in enumerate(media,1):
                mapped=mappings[item.id]
                if mapped['disposition']=='trusted_source_mapping':
                    counts['preserved_trusted_source_mapping']+=1;continue
                current=distinct_work_pages(parse_approved_fields((('filename',item.filename),('stored_path',item.path))))
                if current!=((mapped['work_id'],mapped['page_index']),):
                    counts['changed_prior_excluded']+=1;changed.append(item.id);continue
                decision=queue_media_for_pixiv_metadata(session,item)
                counts[decision.state]+=1
                if index%100==0:
                    session.commit()
                    print(json.dumps({'stage':'prepare','target':target,'processed':index,'counts':counts}),flush=True)
            session.commit()
            receipt={'target':args.database,'scope_fingerprint':scope['canonical_fingerprint'],'counts':dict(counts),
                     'changed_prior_media_ids':changed,'missing_media_ids':sorted(set(mappings)-{item.id for item in media})}
            write(out/f'metadata-prepare-{target}-private.json',receipt)
            print(json.dumps(receipt),flush=True)
            return
        records=session.query(SourceMetadataRecord).filter(SourceMetadataRecord.provider=='pixiv',
            SourceMetadataRecord.metadata_kind==QUEUE_METADATA_KIND,SourceMetadataRecord.media_id.in_(sorted(mappings))).all()
        selected=defaultdict(list);states=Counter()
        for row in records:
            mapped=mappings[row.media_id]
            if (row.source_work_id,row.source_page_index)!=(mapped['work_id'],mapped['page_index']):
                continue
            states[row.status]+=1
            if row.status in (PixivMetadataState.PENDING.value,PixivMetadataState.RETRYABLE.value,PixivMetadataState.NORMALIZATION_FAILED.value) or (
                args.replay_only and row.status==PixivMetadataState.PROVIDER_IDENTITY_MISMATCH.value):
                selected[row.source_work_id].append(row.id)
        print(json.dumps({'stage':'queue_status','target':target,'states':states,'remaining_works':len(selected)}),flush=True)
        session.rollback()
        if args.action=='status':return
        authorization=read(out/'credential-authorization-private.json')
        if not (authorization.get('phase')=='PRODUCTION-PIXIV-A2' and authorization.get('credential_rotated') is False
                and authorization.get('project_owner_authorized_existing_token') is True
                and authorization.get('historical_ml1_exception_reused') is False
                and authorization.get('rotation_environment_set') is False):
            raise RuntimeError('current_a2_credential_authorization_required')
        auth=read(out/'auth-preflight-3/auth-canary-private.json')
        if not auth.get('route_viable'):raise RuntimeError('normal_authentication_preflight_required')
        # Import existing real canary invocations once; a local missing module
        # failure was not a provider dispatch and is intentionally not imported.
        prior_events=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()] if journal.exists() else []
        for stage in ('auth-preflight-2','auth-preflight-3'):
            previous=read(out/stage/'auth-canary-private.json')
            for call in previous['calls']:
                work=call['work_id'];raw=out/stage/f'canary-{work}-stdout-private.json'
                if not any(row.get('event')=='dispatch' and row.get('origin')==stage and row['work_id']==work for row in prior_events):
                    append(journal,{'event':'dispatch','work_id':work,'attempt':1,'origin':stage,'scope':scope['canonical_fingerprint']})
                if not any(row.get('event')=='returned' and row.get('stdout')==str(raw.relative_to(out)) for row in prior_events):
                    append(journal,{'event':'returned','work_id':work,'attempt':1,'returncode':call['returncode'],'stdout':str(raw.relative_to(out))})
        events=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        attempts,cached=recover_raw_payloads(out,events,scope['canonical_fingerprint'],required_pages_by_work)
        if args.action=='close-page-mismatches':
            outcomes=[]
            for work in sorted(selected,key=int):
                missing=[row for row in records if row.source_work_id==work
                    and row.id in selected[work] and row.status==PixivMetadataState.NORMALIZATION_FAILED.value]
                if not missing:continue
                if work not in cached:
                    outcomes.append({'work_id':work,'closed':False,'reason':'saved_provider_payload_missing'});continue
                raw=cached[work].read_text(encoding='utf-8')
                try:pages=parse_gallery_dl_stdout(raw,work)
                except ValueError as exc:
                    outcomes.append({'work_id':work,'closed':False,'reason':str(exc).split(':',1)[0]});continue
                observed=sorted({page['page_index'] for page in pages})
                declared={page.get('page_count') for page in pages}
                complete_remote_page_set=complete_remote_page_domain(observed,declared)
                if not complete_remote_page_set:
                    outcomes.append({'work_id':work,'closed':False,'reason':'complete_provider_page_domain_unproven'});continue
                evidence={'work_id':work,'source_record_ids':sorted(row.id for row in missing),
                    'observed_pages':observed,'declared_page_count':next(iter(declared)),
                    'payload_fingerprint':canonical_fingerprint(raw),
                    'route_closure_reason':'complete_metadata_only_page_domain_has_no_requested_local_page'}
                counts=defer_proven_source_page_mismatch(session,work,attempted_record_ids=evidence['source_record_ids'],
                    observed_page_indexes=observed,original_final_outcome='normalization_failed',manifest_kind='main',
                    evidence_fingerprint=canonical_fingerprint(evidence),deferred_at=datetime.now(timezone.utc).isoformat(),
                    governed_route_exhausted=True)
                session.commit()
                outcomes.append({**evidence,'closed':True,'counts':counts})
                write(out/f'page-mismatch-closure-{target}-private.json',outcomes)
            write(out/f'page-mismatch-closure-{target}-private.json',outcomes)
            print(json.dumps({'stage':'page_mismatch_closure','works':len(outcomes),
                'closed':sum(row['closed'] for row in outcomes),'new_provider_calls':0}),flush=True)
            return
        works=sorted(selected,key=int)
        if args.replay_only:works=[work for work in works if work in cached]
        if args.limit:works=works[:args.limit]
        if target=='restored' and any(work not in cached for work in works):
            raise RuntimeError('restored_rehearsal_must_reuse_payload_no_duplicate_requests')
        raw_dir=out/'metadata-raw';raw_dir.mkdir(exist_ok=True)
        started=time.monotonic();results_count=Counter();new_calls=0
        def capture(command,**kwargs):
            nonlocal new_calls
            work=command[-1].rsplit('/',1)[-1]
            attempt=attempts[work]+1
            if attempt>3:raise RuntimeError('durable_attempt_admission_exhausted')
            append(journal,{'event':'dispatch','work_id':work,'attempt':attempt,'scope':scope['canonical_fingerprint']})
            attempts[work]=attempt;new_calls+=1
            try:
                result=subprocess.run(command,**kwargs)
            except (subprocess.TimeoutExpired,OSError) as exc:
                append(journal,{'event':'transport_failure','work_id':work,'attempt':attempt,'exception':type(exc).__name__})
                raise
            return record_command_response(out,raw_dir,journal,work,attempt,result,required_pages_by_work[work])
        def checkpoint(result):
            value=asdict(result);results_count[result.state]+=1
            append(out/f'metadata-outcomes-{target}-private.jsonl',{'scope':scope['canonical_fingerprint'],**value})
            progress={'target':target,'processed_this_run':sum(results_count.values()),'selected_this_run':len(works),
                'new_provider_invocations':new_calls,'cumulative_provider_invocations':sum(attempts.values()),
                'counts':dict(results_count),'elapsed_seconds':round(time.monotonic()-started,2),
                'last_work':result.work_id,'last_state':result.state,'systemic_stop':result.systemic_stop}
            write(out/f'metadata-progress-{target}-private.json',progress)
            if sum(results_count.values())%10==0 or result.systemic_stop:print(json.dumps(progress),flush=True)
        replay={work:cached[work].read_text(encoding='utf-8') for work in works if work in cached}
        # Disable the CLI's default four hidden retries; the durable task
        # journal owns the three-attempt cap, including across continuations.
        entrypoint=[*auth['entrypoint']['command'],'--retries','0','--sleep-request','2']
        result=run_bounded_acquisition(session,works,entrypoint=entrypoint,authentication_passed=True,
            accept_local_credential_risk=True,env=dict(os.environ),command_runner=capture,timeout_seconds=90,
            max_attempts_per_work=3,prior_attempt_counts=dict(attempts),result_callback=checkpoint,
            attempted_record_ids_by_work=selected,metadata_replay_outputs=replay,allow_normalization_replay=True,
            persistent_spacing=PersistentRequestSpacing(out/'request-spacing.json',phase_manifest_fingerprint=scope['canonical_fingerprint']))
        print(json.dumps({'stage':'finished','selected':len(works),'processed':len(result),'counts':results_count,
                          'systemic_stop':any(item.systemic_stop for item in result)}),flush=True)
    engine.dispose()


if __name__=='__main__':
    main()
