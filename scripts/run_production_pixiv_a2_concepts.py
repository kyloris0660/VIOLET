"""Production Pixiv roles, cached adjudication and deterministic clustering.

Metadata snapshots are explicit, immutable inputs. Every label shares the
same task budget and durable answer caches. This command performs no database
or media writes; the existing product transaction applies the resulting run.
"""
import argparse
from dataclasses import asdict
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def peak_memory_bytes():
    if os.name!='nt':
        import resource
        value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform=='darwin' else value*1024)
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[
            (name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    handle=ctypes.windll.kernel32.GetCurrentProcess
    handle.restype=wintypes.HANDLE
    query=ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    counters=Counters();counters.cb=ctypes.sizeof(counters)
    if not query(handle(),ctypes.byref(counters),counters.cb):
        raise OSError('process_memory_query_failed')
    return int(counters.PeakWorkingSetSize)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('roles','contextual-roles','complete-contextual-roles','repair-role-coverage','cluster','adjudicate'))
    parser.add_argument('--artifacts',required=True,type=Path)
    parser.add_argument('--profile',required=True,type=Path)
    parser.add_argument('--aggregates',required=True,type=Path)
    parser.add_argument('--vocabulary',required=True,type=Path)
    parser.add_argument('--role-facts',type=Path)
    parser.add_argument('--diagnostic-names',type=Path,help='private names to trace after graph construction; never model input')
    parser.add_argument('--label',required=True)
    parser.add_argument('--expected-python',required=True)
    parser.add_argument('--limit',type=int,default=0,help='bounded role/completion batch; 0 processes all remaining units')
    parser.add_argument('--context-batch-size',type=int,choices=range(1,11),default=5,
                        help='context groups per request; shared token and USD limits remain unchanged')
    parser.add_argument('--text-workers',type=int,choices=(1,2),default=1,
                        help='bounded residual-role workers with independent clients and one shared budget/pause')
    args=parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend'))
    from scripts.check_python_env import run_checks
    from scripts.run_production_pixiv_a2_metadata import read,write,exclusive
    if not run_checks(args.expected_python,str(ROOT))['pass']:raise RuntimeError('python_identity_preflight_failed')
    out=args.artifacts.resolve(strict=True)
    if not out.is_relative_to((ROOT/'.local_manifests').resolve()) or not re.fullmatch('[a-z0-9-]+',args.label):
        raise RuntimeError('private_concept_artifact_location_invalid')
    for path in (args.aggregates,args.vocabulary,args.role_facts,args.diagnostic_names):
        if path and not path.resolve(strict=True).is_relative_to(out):raise RuntimeError('concept_input_outside_task')
    llm=read(args.profile)['tag_translation_llm']
    if llm['model']!='gpt-4.1-mini' or llm['provider']!='openai_compatible' or llm['base_url'].rstrip('/')!='https://api.openai.com/v1':
        raise RuntimeError('approved_model_or_price_identity_changed')
    os.environ.update(VIOLET_SKIP_DOTENV='1',VIOLET_ENV='test',POSTGRES_DB='blombooru_test',VIOLET_STORAGE_ROOT=str(out/'offline-storage'),
        TAG_TRANSLATION_LLM_ENABLED='true',TAG_TRANSLATION_LLM_PROVIDER=llm['provider'],TAG_TRANSLATION_LLM_MODEL=llm['model'],
        TAG_TRANSLATION_LLM_API_KEY=llm['api_key'],TAG_TRANSLATION_LLM_BASE_URL=llm['base_url'],TAG_TRANSLATION_LLM_FALLBACK_ENABLED='false')
    from app.services.production_pixiv_service import production_consumer,build_production_clustering
    from app.services.production_pixiv_role_extraction import plan_role_extraction,extract_production_roles
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    from app.services.source_concept_budget import AdjudicationBudget
    from app.services.source_concept_resolver_service import (
        LLMAdjudicationConfig,run_bounded_llm_adjudication,primary_openai_provider_from_settings,
        select_llm_adjudication_edges,PRODUCTION_PAIR_PROMPT_VERSION,
    )
    aggregates=read(args.aggregates);vocabulary=read(args.vocabulary);facts=read(args.role_facts) if args.role_facts else None
    consumer=production_consumer(aggregates)
    prefix=out/args.label
    identity={'aggregates':canonical_fingerprint(aggregates),'vocabulary':canonical_fingerprint(vocabulary),
              'role_facts':canonical_fingerprint(facts)}
    identity_path=out/f'{args.label}-input-identity-private.json'
    if identity_path.exists() and read(identity_path)!=identity:raise RuntimeError('label_frozen_input_changed')
    write(identity_path,identity)
    # The explicit atomic amendment is performed separately on the existing
    # ledger. This runner never resets spending or silently increases a cap.
    ledger=read(out/'llm-budget-private.json')
    cap=ledger['cap_microusd']/1000000
    if cap not in (10,30) or (cap==30 and not any(
        row['previous_cap_microusd']==10000000 and row['cap_microusd']==30000000
        for row in ledger.get('cap_amendments',[]))):
        raise RuntimeError('authorized_task_budget_amendment_required')
    budget=AdjudicationBudget(out/'llm-budget-private.json',model=llm['model'],cap_usd=cap,input_per_million=0.4,output_per_million=1.6)
    started=time.monotonic()
    with (exclusive(out/'llm-task.lock') if args.action!='cluster' else nullcontext()):
        if args.action in {'contextual-roles','complete-contextual-roles','repair-role-coverage'}:
            from app.services.production_pixiv_role_extraction import extract_contextual_production_roles,plan_contextual_role_extraction
            if args.action=='complete-contextual-roles':
                from app.services.production_pixiv_role_extraction import (
                    complete_contextual_production_roles as extract_contextual_production_roles,
                    plan_contextual_role_completion as plan_contextual_role_extraction,
                )
            if args.action=='repair-role-coverage':
                from app.services.production_pixiv_role_extraction import (
                    repair_missing_role_coverage as extract_contextual_production_roles,
                    plan_role_coverage_repair as plan_contextual_role_extraction,
                )
            if not facts:raise RuntimeError('existing_role_facts_required_for_contextual_supplement')
            provider,summary=primary_openai_provider_from_settings()
            if provider is None:raise RuntimeError('approved_primary_model_unavailable')
            units,mapping,plan=plan_contextual_role_extraction(consumer,vocabulary,facts)
            write(out/f'{args.label}-context-plan-private.json',plan)
            print(json.dumps({key:value for key,value in plan.items() if key!='parent_extraction_keys'}),flush=True)
            def context_progress(value):
                write(out/f'{args.label}-context-progress-private.json',value)
                print(json.dumps(value),flush=True)
            result=extract_contextual_production_roles(consumer,vocabulary,facts,provider=provider,budget=budget,
                cache_dir=out/'role-cache',progress=context_progress,batch_size=args.context_batch_size,unit_limit=args.limit,
                **({'workers':args.text_workers,
                    'provider_factory':lambda:primary_openai_provider_from_settings()[0]}
                    if args.action in {'complete-contextual-roles','repair-role-coverage'} else {}))
            write(out/f'{args.label}-roles-private.json',result)
            summary_key={'complete-contextual-roles':'completion_summary','repair-role-coverage':'coverage_repair_summary'}.get(args.action,'context_summary')
            print(json.dumps(result[summary_key]),flush=True)
            if args.action=='repair-role-coverage':
                print(json.dumps({key:value for key,value in result['role_response_coverage'].items() if key!='unaccounted_targets'}),flush=True)
            return
        if args.action=='roles':
            units,plan=plan_role_extraction(consumer,vocabulary)
            units.sort(key=lambda unit:(-len(unit.occurrences),unit.extraction_key))
            write(out/f'{args.label}-role-plan-private.json',plan)
            selected=units[:args.limit] if args.limit else units
            provider,summary=primary_openai_provider_from_settings()
            if provider is None:raise RuntimeError('approved_primary_model_unavailable')
            def progress(value):
                write(out/f'{args.label}-role-progress-private.json',value)
                print(json.dumps(value,ensure_ascii=False),flush=True)
            print(json.dumps({'stage':'role_plan','total_units':len(units),'selected_units':len(selected),
                'raw_occurrences':plan['raw_string_occurrences_total']}),flush=True)
            result=extract_production_roles(selected,provider=provider,budget=budget,cache_dir=out/'role-cache',progress=progress)
            result['summary']['fixed_input_total_units']=len(units)
            result['summary']['not_selected_in_this_invocation']=len(units)-len(selected)
            write(out/f'{args.label}-roles-private.json',result)
            print(json.dumps(result['summary'],ensure_ascii=False),flush=True)
            return
        def trace(stage,run):
            if not args.diagnostic_names:return
            from app.services.source_metadata_registry_service import canonical_source_key
            names={canonical_source_key(n) for n in read(args.diagnostic_names)}
            selected={s.signal_key for s in run.resolution.signals if canonical_source_key(s.raw_value) in names}
            concepts={link.concept_key for link in run.resolution.links if link.signal_key in selected}
            component={link.signal_key for link in run.resolution.links if link.concept_key in concepts}|selected
            write(out/f'{args.label}-{stage}-causal-trace-private.json',{
                'run_id':run.resolution.run_id,'business_fingerprint':run.business_projection_fingerprint,
                'diagnostic_names':sorted(names),'signals':[asdict(s) for s in run.resolution.signals if s.signal_key in component],
                'edges':[asdict(e) for e in run.resolution.edge_candidates if e.left_signal_key in component or e.right_signal_key in component],
                'links':[asdict(r) for r in run.resolution.links if r.signal_key in component],
                'aliases':[asdict(r) for r in run.resolution.aliases if r.concept_key in concepts],
                'judgments':[r for r in run.resolution.llm_judgments if r.get('left_signal_key') in component or r.get('right_signal_key') in component],
                'diagnostic_selection_did_not_affect_graph_or_model_inputs':True})
        run=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts)
        trace('initial',run)
        judgments=[];receipt=None
        if args.action=='adjudicate':
            from app.services.production_pixiv_release_inputs import verify_role_completion
            role_coverage=verify_role_completion(aggregates,vocabulary,facts,read(out/'llm-budget-private.json'))
            write(out/f'{args.label}-role-completion-admission-private.json',role_coverage)
            config=LLMAdjudicationConfig(enabled=True,max_calls=1000000,max_budget_usd=cap,selection_policy='all_eligible',
                prompt_version=PRODUCTION_PAIR_PROMPT_VERSION,
                model_label=llm['model'],durable_cache_dir=str(out/'llm-cache'),semantic_cache_reuse=True,
                semantic_cache_dirs=(str(Path(read(args.profile)['storage_root'])/'.local_manifests/source_concept_llm_adjudication_cache'),),
                task_budget_path=str(out/'llm-budget-private.json'),input_price_per_million=0.4,output_price_per_million=1.6,
                run_id=run.resolution.run_id,provider_pause_state={})
            planned={'stage':'adjudication_plan','selected_pairs':len(select_llm_adjudication_edges(
                run.resolution.edge_candidates,signals=run.resolution.signals,config=config)),
                'candidate_edges':len(run.resolution.edge_candidates),'signals':len(run.resolution.signals)}
            write(out/f'{args.label}-adjudication-plan-private.json',planned)
            print(json.dumps(planned),flush=True)
            by_key={signal.signal_key:signal for signal in run.resolution.signals}
            def work_pair(edge):
                return by_key[edge.left_signal_key].role_hint==by_key[edge.right_signal_key].role_hint=='work'
            work_edges=[edge for edge in run.resolution.edge_candidates if work_pair(edge)]
            write(out/f'{args.label}-work-selected-pairs-private.json',[asdict(edge) for edge in
                select_llm_adjudication_edges(work_edges,signals=run.resolution.signals,config=config)])
            work_judgments,work_receipt=run_bounded_llm_adjudication(work_edges,signals=run.resolution.signals,config=config)
            write(out/f'{args.label}-work-judgments-private.json',work_judgments)
            write(out/f'{args.label}-work-adjudication-private.json',work_receipt)
            run=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts,judgments=work_judgments)
            trace('work-context',run)
            remaining=[edge for edge in run.resolution.edge_candidates if not work_pair(edge)]
            write(out/f'{args.label}-remaining-selected-pairs-private.json',[asdict(edge) for edge in
                select_llm_adjudication_edges(remaining,signals=run.resolution.signals,config=config)])
            other_judgments,other_receipt=run_bounded_llm_adjudication(remaining,signals=run.resolution.signals,config=config)
            judgments=[*work_judgments,*other_judgments]
            receipt={'work_stage':work_receipt,'remaining_stage':other_receipt,'task_budget':budget.summary(),
                **{key:work_receipt[key]+other_receipt[key] for key in ('selected_pair_count','judgment_count',
                    'error_count','cache_hits','new_provider_call_count','remaining_missing_pair_count')}}
            write(out/f'{args.label}-judgments-private.json',judgments)
            write(out/f'{args.label}-adjudication-private.json',receipt)
            run=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts,judgments=judgments)
            trace('final',run)
            from app.services.production_pixiv_release_inputs import semantic_input_identity
            import subprocess
            write(out/f'{args.label}-semantic-manifest-private.json',{
                'input_identity':semantic_input_identity(aggregates,vocabulary,facts,judgments),
                'candidate_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                'processing':{key:receipt[key] for key in ('selected_pair_count','judgment_count','error_count','remaining_missing_pair_count')},
                'adjudication_receipt':f'{args.label}-adjudication-private.json',
                'work_selection':f'{args.label}-work-selected-pairs-private.json',
                'remaining_selection':f'{args.label}-remaining-selected-pairs-private.json'})
        result={'input_identity':identity,'run_id':run.resolution.run_id,'seconds':time.monotonic()-started,
                'peak_memory_bytes':peak_memory_bytes(),
                'signal_count':len(run.resolution.signals),'edge_count':len(run.resolution.edge_candidates),
                'concept_count':len(run.resolution.concepts),'invariants':run.invariants,'diagnostics':run.diagnostics,
                'resolver_summary':run.resolution.summary,'business_fingerprint':run.business_projection_fingerprint,
                'task_budget':budget.summary()}
        write(out/f'{args.label}-cluster-private.json',result)
        print(json.dumps({key:value for key,value in result.items() if key!='resolver_summary'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
