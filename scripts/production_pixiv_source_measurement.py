"""Candidate/database-bound direct source-search measurements and result replay."""
import os
import sys
import time
from pathlib import Path


def source_ids(session, terms):
    from app.services.source_concept_search_service import source_layer_search_path_media_ids
    sets=[set(source_layer_search_path_media_ids(session,term,include_needs_review=False,
        include_evidence_fallback=True,include_production_alias_evidence=True)['combined']) for term in terms]
    return sorted(set.intersection(*sets) if sets else set())


def measure_source_case(session,case,repeat,*,candidate,database,system_identifier):
    import subprocess
    from sqlalchemy import text
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    root=Path(__file__).resolve().parents[1]
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    actual=tuple(session.execute(text('select current_database(),system_identifier::text from pg_control_system()')).one())
    if head!=candidate or actual!=(database,system_identifier):
        raise ValueError('a2_source_sampler_runtime_identity')
    start=time.perf_counter_ns();ids=source_ids(session,case['terms']);end=time.perf_counter_ns()
    return {'case_id':case['case_id'],'repeat':repeat,'terms':case['terms'],
        'include_needs_review':False,'include_evidence_fallback':True,'include_production_alias_evidence':True,
        'ms':(end-start)/1000000,'started_perf_ns':start,'finished_perf_ns':end,
        'ids':ids,'result_fingerprint':canonical_fingerprint(ids),
        'execution':{'candidate_head':head,'database':actual[0],'system_identifier':actual[1],
            'code_root':str(root),'python_executable':sys.executable,'pid':os.getpid()}}


def replay_source_results(session,cases,*,database,system_identifier):
    from sqlalchemy import text
    if tuple(session.execute(text('select current_database(),system_identifier::text from pg_control_system()')).one())!=(database,system_identifier):
        raise ValueError('a2_source_replay_database_identity')
    return {case['case_id']:source_ids(session,case['terms']) for case in cases}
