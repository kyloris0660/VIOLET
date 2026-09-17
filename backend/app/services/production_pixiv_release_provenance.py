"""Offline release replay over real questions and retained local answers.

This is local operator provenance, not a signature or hostile-host attestation.
No provider is constructed and no cache or budget is mutated by this module.
"""
import json
from pathlib import Path
from dataclasses import asdict

from .pixiv_metadata_projection_service import canonical_fingerprint
from . import source_concept_resolver_service as resolver


def verify_role_response_sources(consumer,vocabulary,facts,cache_dir,ledger):
    """Reparse saved original questions and answers, including partial batches."""
    from collections import defaultdict
    from types import SimpleNamespace
    from dataclasses import replace
    from . import production_pixiv_role_extraction as roles
    from .source_name_candidate_extraction_service import (
        SourceCandidateInputGroup,group_prompt_payload,extraction_messages,validate_extraction_record,
        deterministic_bundle_for_unit,SourceNameCandidateExtractionError,build_extraction_units)
    from .source_metadata_registry_service import canonical_source_key
    cache_dir=Path(cache_dir);calls={r['id']:r for r in ledger['calls']}
    units,_=roles.plan_role_extraction(consumer,vocabulary)
    # Retained older context-free answers can now be shadowed by accepted
    # vocabulary. Reconstruct their real original spelling, not a new call.
    historical,_=build_extraction_units([SourceCandidateInputGroup(group_key='source-replay:'+key,provider='pixiv',
        tags=({'raw_tag':r['raw_value'],'source_tag_kind':'provider_tag'},),data_origin='production_metadata_text_only')
        for key,r in facts.get('records',{}).items()])
    historical=[replace(u,unit_group=replace(u.unit_group,group_key='a2-role:'+canonical_fingerprint(u.extraction_key)[:24])) for u in historical]
    initial={**facts,'context_records':{},'context_by_aggregate':{},'completion_records':{},'completion_by_aggregate':{},
        'coverage_repair_records':{},'coverage_repair_by_aggregate':{},'semantic_corrections':[]}
    contextual,_,_=roles.plan_contextual_role_extraction(consumer,vocabulary,initial)
    original,_=roles._original_completion_questions(consumer,vocabulary,facts)
    unit_by_key={u.extraction_key:u for u in [*units,*historical,*contextual,*original.values()]}
    by_group={u.unit_group.group_key:u.unit_group for u in unit_by_key.values()}
    source_by_question=defaultdict(list);source_count=0
    paths=sorted([*(cache_dir/'raw').glob('*.json'),*(cache_dir/'raw'/'attempts').glob('*.json'),
                  *(cache_dir/'response-recovery').glob('*.json')])
    for path in paths:
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved.get('model')!='gpt-4.1-mini':continue
        try:
            rows=json.loads(saved['content'])['records']
            groups=[SourceCandidateInputGroup(**g) for g in saved['request_groups']] if saved.get('request_groups') else [by_group[r['group_key']] for r in rows]
        except (KeyError,ValueError,TypeError):continue  # Invalid raw is history, not answer evidence.
        messages=roles._production_messages(extraction_messages(groups))
        fingerprint=canonical_fingerprint({'model':saved['model'],'messages':messages,
            'temperature':saved.get('temperature',0.0),'max_tokens':saved.get('max_tokens',6000)})
        if fingerprint!=saved.get('input_fingerprint') or (saved.get('request_messages') is not None and saved['request_messages']!=messages):
            continue
        response=saved.get('budget_response')
        if response:
            call=calls.get(response['reservation'])
            if not call or call['status']=='reserved' or call['key']!='role-extraction:'+fingerprint:
                raise ValueError('semantic_role_source_attempt_not_settled')
            if call.get('usage_known') and call['usage']!={k:saved['usage'][k] for k in ('prompt_tokens','completion_tokens')}:
                raise ValueError('semantic_role_source_usage_changed')
        rows_by_key={r['group_key']:r for r in rows if isinstance(r,dict) and 'group_key' in r}
        for group in groups:
            raw=rows_by_key.get(group.group_key)
            if not raw:continue
            adapted=roles._adapt_response_record(raw,SimpleNamespace(unit_group=group))
            # Preserve valid siblings under the existing F7a validator.
            try:verdict,candidates,*_=validate_extraction_record(adapted,group)
            except (ValueError,TypeError,SourceNameCandidateExtractionError):
                candidates=[]
                for candidate in adapted.get('candidates',[]):
                    try:
                        _,valid,*_=validate_extraction_record({**adapted,'candidates':[candidate]},group)
                        candidates.extend(valid)
                    except (ValueError,TypeError,SourceNameCandidateExtractionError):continue
                verdict=None
            # Historical accepted units may retain the base F7a source-field
            # normalization rather than the later bounded production adapter.
            # Both must be reconstructed from this same immutable raw answer.
            try:
                original_verdict,original_candidates,*_=validate_extraction_record(raw,group)
                candidates=[*candidates,*original_candidates]
            except (ValueError,TypeError,SourceNameCandidateExtractionError):pass
            source_by_question[canonical_fingerprint(group_prompt_payload(group))].append({
                'group':group,'candidates':[asdict(c) for c in candidates],
                'verdict':verdict.extraction_verdict if verdict else None,
                'dispositions':adapted.get('target_dispositions',[]),'path':str(path.relative_to(cache_dir)),
                'fingerprint':canonical_fingerprint(saved)})
        source_count+=1
    aggregate_tags=defaultdict(set)
    for signal in consumer.signals:
        if signal.origin_type=='pixiv_tag_observation':aggregate_tags[signal.evidence_payload['aggregate_fingerprint']].add(signal.raw_value)
    proofs=[]
    for kind in ('records','context_records','completion_records','coverage_repair_records','correction_records'):
        for key,record in facts.get(kind,{}).items():
            sources=source_by_question.get(record.get('input_fingerprint'),[])
            if record.get('origin')=='existing_f7a_deterministic':
                unit=unit_by_key.get(key)
                if not unit or unit.llm_required:raise ValueError('semantic_role_deterministic_source_missing')
                bundle=deterministic_bundle_for_unit(unit,run_id='production-pixiv-roles',run_label='production-pixiv-roles')
                expected=roles._record(unit,'gpt-4.1-mini',bundle.record_verdicts[0],bundle.candidates,origin='existing_f7a_deterministic')
                if expected!=record:raise ValueError('semantic_role_deterministic_answer_changed')
                proofs.append({'key':key,'deterministic':True});continue
            if not sources:raise ValueError('semantic_role_original_response_missing:'+key)
            unit=unit_by_key.get(key)
            if unit and roles._identity(unit,'gpt-4.1-mini')['input_fingerprint']!=record['input_fingerprint']:
                # Existing case-variant compatibility still requires the actual
                # original representative and reconstructed question identity.
                prior=replace(unit,normalized_value=record['raw_value'],unit_group=replace(unit.unit_group,
                    tags=tuple({**t,'raw_tag':record['raw_value']} for t in unit.unit_group.tags)))
                if roles._identity(prior,'gpt-4.1-mini')['input_fingerprint']!=record['input_fingerprint']:
                    raise ValueError('semantic_role_current_question_changed')
            candidates={canonical_fingerprint(c) for s in sources for c in s['candidates']}
            if any(canonical_fingerprint(c) not in candidates for c in record['candidates']):
                raise ValueError('semantic_role_answer_not_in_original_response:'+key)
            dispositions={canonical_fingerprint(d) for s in sources for d in s['dispositions'] if isinstance(d,dict)}
            if any(canonical_fingerprint(d) not in dispositions for d in record.get('validated_response',{}).get('target_dispositions',[])):
                raise ValueError('semantic_role_disposition_not_in_original_response')
            if not record['candidates'] and record['verdict'] not in {s['verdict'] for s in sources}:
                raise ValueError('semantic_role_verdict_not_in_original_response')
            proofs.append({'key':key,'question':record['input_fingerprint'],
                'sources':[{'path':s['path'],'fingerprint':s['fingerprint']} for s in sources]})
    for mapping,kind in [('context_by_aggregate','context_records'),('completion_by_aggregate','completion_records'),
                         ('coverage_repair_by_aggregate','coverage_repair_records')]:
        for aggregate,key in facts.get(mapping,{}).items():
            record=facts[kind][key];sources=source_by_question.get(record['input_fingerprint'],[])
            if not sources:raise ValueError('semantic_role_mapped_question_missing')
            for source in sources:
                tags={t['raw_tag'] for t in source['group'].tags}
                if not tags<=aggregate_tags[aggregate]:raise ValueError('semantic_role_source_context_changed')
                if mapping!='context_by_aggregate' and tags!=aggregate_tags[aggregate]:
                    raise ValueError('semantic_role_complete_context_changed')
    return {'record_count':len(proofs),'validated_raw_count':source_count,'records':proofs,'new_provider_calls':0}


def verify_selected_judgment_sources(edges,signals,judgments,config,ledger):
    selected=resolver.select_llm_adjudication_edges(edges,signals=signals,config=config)
    by_signal={s.signal_key:s for s in signals}
    aliases=resolver._context_equivalence_lookup(signals)
    contexts=resolver._context_candidates_by_scope(signals,context_alias_by_key=aliases)
    by_pair={}
    for judgment in judgments:
        pair=(judgment['left_signal_key'],judgment['right_signal_key'])
        if pair in by_pair:raise ValueError('semantic_duplicate_judgment')
        by_pair[pair]=judgment
    if set(by_pair)!={(e.left_signal_key,e.right_signal_key) for e in selected}:
        raise ValueError('semantic_selected_judgment_set_changed')
    calls={r['id']:r for r in ledger['calls']}
    roots=[Path(config.durable_cache_dir),*(Path(p) for p in config.semantic_cache_dirs)]
    records={};evidence=[]
    def load(key):
        if key not in records:
            found=[]
            for root in roots:
                path=root/'records'/f'{key}.json'
                if path.is_file():found.append(json.loads(path.read_text(encoding='utf-8')))
            if not found:raise ValueError('semantic_judgment_source_cache_missing')
            # Volatile copies may differ in provenance, never in answer/input.
            fields=('decision','confidence','input_signal_summary','model_label','provider_model')
            if any(any(row.get(k)!=found[0].get(k) for k in fields) for row in found[1:]):
                raise ValueError('semantic_judgment_source_cache_conflict')
            records[key]=found[0]
        return records[key]
    for edge in selected:
        block={'edge':asdict(edge),**{side:resolver._llm_signal_payload(by_signal[getattr(edge,side+'_signal_key')],
            context_by_scope=contexts,context_alias_by_key=aliases) for side in ('left','right')}}
        metadata=resolver.llm_cache_metadata(block,config=config)
        row=by_pair[(edge.left_signal_key,edge.right_signal_key)]
        record=load(metadata['cache_key'])
        if not resolver._cache_record_is_exact_compatible(record,metadata=metadata,config=config):
            raise ValueError('semantic_judgment_source_input_changed')
        derived=resolver._judgment_from_cache_record(record,block_payload=block,selected_pair_id='',cache_status='hit',reuse_level='offline_verified')
        for key in ('judgment_id','cache_key','pair_payload_hash','pair_identity','input_signal_summary',
                    'decision','confidence','reason_code','provider_model','model_label','error_state'):
            if row.get(key)!=derived.get(key):raise ValueError('semantic_judgment_response_changed:'+key)
        source=record;chain=[metadata['cache_key']]
        while source.get('reused_from_cache_key'):
            prior=source['reused_from_cache_key']
            if prior in chain:raise ValueError('semantic_judgment_reuse_cycle')
            chain.append(prior);source=load(prior)
            if (resolver._decision_input_key(source['input_signal_summary'])!=resolver._decision_input_key(block)
                or resolver.llm_public_decision(source['decision'])!=resolver.llm_public_decision(record['decision'])
                or source['confidence']!=record['confidence'] or source.get('provider_model')!=config.model_label
                or source.get('prompt_template_version')!=config.prompt_version):
                raise ValueError('semantic_judgment_reused_source_changed')
        response=source.get('budget_response')
        if response:
            call=calls.get(response['reservation'])
            if not call or call['status']=='reserved' or call['key']!=response['key']:
                raise ValueError('semantic_judgment_attempt_not_settled')
            if call.get('usage_known') and call['usage']!={k:response['usage'][k] for k in ('prompt_tokens','completion_tokens')}:
                raise ValueError('semantic_judgment_usage_changed')
        evidence.append({'cache_key':metadata['cache_key'],'source_chain':chain,
                         'source_fingerprint':canonical_fingerprint(source)})
    return {'selected_pair_count':len(selected),'judgment_count':len(judgments),'sources':evidence,'new_provider_calls':0}


def verify_release_sources(aggregates,vocabulary,facts,judgments,manifest,private_root,*,semantic_cache_dirs=()):
    from .production_pixiv_service import production_consumer,build_production_clustering
    from .source_concept_budget import AdjudicationBudget
    private_root=Path(private_root).resolve(strict=True)
    def read(name):
        path=(private_root/name).resolve(strict=True)
        if not path.is_relative_to(private_root):raise ValueError('semantic_source_receipt_outside_task')
        return json.loads(path.read_text(encoding='utf-8'))
    receipt=read(manifest['adjudication_receipt'])
    ledger=read('llm-budget-private.json');AdjudicationBudget._charged(ledger)
    consumer=production_consumer(aggregates)
    roles=verify_role_response_sources(consumer,vocabulary,facts,private_root/'role-cache',ledger)
    config=resolver.LLMAdjudicationConfig(enabled=True,max_calls=1000000,max_budget_usd=30,
        model_label='gpt-4.1-mini',selection_policy='all_eligible',prompt_version=resolver.PRODUCTION_PAIR_PROMPT_VERSION,
        durable_cache_dir=str(private_root/'llm-cache'),semantic_cache_dirs=tuple(semantic_cache_dirs))
    initial=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts)
    by_key={s.signal_key:s for s in initial.resolution.signals}
    def work_pair(edge):return by_key[edge.left_signal_key].role_hint==by_key[edge.right_signal_key].role_hint=='work'
    work_keys={s.signal_key for s in initial.resolution.signals if s.role_hint=='work'}
    work_judgments=[j for j in judgments if j['left_signal_key'] in work_keys and j['right_signal_key'] in work_keys]
    other_judgments=[j for j in judgments if not (j['left_signal_key'] in work_keys and j['right_signal_key'] in work_keys)]
    work_edges=resolver.select_llm_adjudication_edges([e for e in initial.resolution.edge_candidates if work_pair(e)],
        signals=initial.resolution.signals,config=config)
    work=verify_selected_judgment_sources(work_edges,initial.resolution.signals,work_judgments,config,ledger)
    del initial
    contextual=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts,judgments=work_judgments)
    others=resolver.select_llm_adjudication_edges([e for e in contextual.resolution.edge_candidates if not work_pair(e)],
        signals=contextual.resolution.signals,config=config)
    remaining=verify_selected_judgment_sources(others,contextual.resolution.signals,other_judgments,config,ledger)
    # Published selection files are evidence too; they must describe this
    # replay, not merely repeat the manifest's self-reported counters.
    for stage,edges,signals,result in [('work',work_edges,None,work),('remaining',others,contextual.resolution.signals,remaining)]:
        name=manifest.get(stage+'_selection')
        if not name:raise ValueError('semantic_selection_receipt_required')
        saved=read(name)
        # Selection identity is established above over the exact signals;
        # match the edge objects represented by every supplied judgment.
        chosen={(j['left_signal_key'],j['right_signal_key']) for j in (work_judgments if stage=='work' else other_judgments)}
        expected=[asdict(e) for e in edges if (e.left_signal_key,e.right_signal_key) in chosen]
        if sorted(saved,key=lambda r:r['edge_key'])!=sorted(expected,key=lambda r:r['edge_key']):
            raise ValueError('semantic_processing_selection_changed')
        observed=receipt['work_stage' if stage=='work' else 'remaining_stage']
        if any(observed.get(k)!=result[k] for k in ('selected_pair_count','judgment_count')) or observed.get('error_count')!=0:
            raise ValueError('semantic_processing_receipt_changed')
    return {'role_sources':roles,'work_sources':work,'remaining_sources':remaining,'new_provider_calls':0}
