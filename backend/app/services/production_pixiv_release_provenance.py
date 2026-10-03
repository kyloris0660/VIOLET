"""Offline release replay over real questions and retained local answers.

This is local operator provenance, not a signature or hostile-host attestation.
No provider is constructed and no cache or budget is mutated by this module.
"""
import json
from pathlib import Path
from dataclasses import asdict

from .pixiv_metadata_projection_service import canonical_fingerprint
from . import source_concept_resolver_service as resolver
from .production_pixiv_role_extraction import checked_cache_path
from .source_concept_budget import AdjudicationBudget, AdjudicationBudgetBlocked


def replay_role_request_messages(groups):
    """Rebuild the literal pre-schema-fix v1 request, never dispatch it."""
    from . import production_pixiv_role_extraction as roles
    from .source_name_candidate_extraction_service import extraction_messages
    messages=extraction_messages(groups)
    legacy='production_pixiv_role_coverage_repair_v1'
    if not any(g.data_origin==legacy for g in groups):return roles._production_messages(messages)
    if any(g.data_origin!=legacy for g in groups):raise ValueError('mixed_historical_role_prompt_versions')
    # 4eb1833 precedes the 45f5943 schema fix. Its original request did not
    # override F7a output fields. Retain that discrepancy as history only.
    prompt=roles.COVERAGE_REPAIR_PROMPT.split(' The record schema is extended for this task:',1)[0]
    payload=json.loads(messages[1]['content'])
    for row in payload['records']:row.pop('deterministic_hints',None)
    payload['production_prompt_adapter']=legacy
    return [{**messages[0],'content':messages[0]['content']+'\n'+prompt},
        {**messages[1],'content':json.dumps(payload,ensure_ascii=False,sort_keys=True)}]


def _anchored_role_calls(cache_dir,*,authority=None):
    """Read the immutable pre-amendment ledger; never repair the live ledger."""
    import hashlib
    private=Path(cache_dir).resolve().parent
    path=checked_cache_path(private,private/'closeout43-budget-before-private.json')
    if not path.is_file():return {'calls':{}}
    authority_path=Path(__file__).resolve().parents[3]/'docs/state/production-pixiv-a2-budget-authority.json'
    authority=authority or json.loads(authority_path.read_text(encoding='utf-8'))
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=authority['ledger_before_sha256']:
        raise ValueError('semantic_legacy_role_ledger_anchor_changed')
    old=json.loads(raw)
    if len(old['calls'])!=authority['call_count_before']:
        raise ValueError('semantic_legacy_role_ledger_count_changed')
    return {'path':path.name,'sha256':hashlib.sha256(raw).hexdigest(),
        'calls':{row['id']:row for row in old['calls'] if row['key'].startswith('role-extraction:')}}


def _anchored_legacy_role_calls(cache_dir,*,authority=None):
    return {key:row for key,row in _anchored_role_calls(cache_dir,authority=authority)['calls'].items()
        if 'logical_keys' not in row}


def verify_role_response_sources(consumer,vocabulary,facts,cache_dir,ledger,*,diagnostic=False):
    """Reparse saved original questions and answers, including partial batches."""
    from collections import defaultdict
    from types import SimpleNamespace
    from dataclasses import replace
    from . import production_pixiv_role_extraction as roles
    from .source_name_candidate_extraction_service import (
        SourceCandidateInputGroup,group_prompt_payload,extraction_messages,validate_extraction_record,
        deterministic_bundle_for_unit,SourceNameCandidateExtractionError,build_extraction_units)
    from .source_metadata_registry_service import canonical_source_key
    from .production_pixiv_role_sources import RoleSourceContract,validate_record_projection,original_target_accounting
    from .production_pixiv_role_recovery import historical_role_facts,current_role_facts
    full_facts=facts
    if facts.get('source_recovery'):current_role_facts(facts)  # validate the complete view before replay
    facts=historical_role_facts(facts)
    cache_dir=Path(cache_dir).resolve();calls={r['id']:r for r in ledger['calls']}
    legacy_calls=None;legacy_logical_replay={}
    calls_by_key=defaultdict(list)
    for call in calls.values():calls_by_key[call['key']].append(call)
    required_questions={r.get('input_fingerprint') for kind in
        ('records','context_records','completion_records','coverage_repair_records','correction_records')
        for r in facts.get(kind,{}).values()}
    units,_=roles.plan_role_extraction(consumer,vocabulary)
    # Retained older context-free answers can now be shadowed by accepted
    # vocabulary. Reconstruct their real original spelling, not a new call.
    historical,_=build_extraction_units([SourceCandidateInputGroup(group_key='source-replay:'+key,provider='pixiv',
        tags=({'raw_tag':r['raw_value'],'source_tag_kind':'provider_tag'},),data_origin='production_metadata_text_only')
        for key,r in facts.get('records',{}).items()])
    historical=[replace(u,unit_group=replace(u.unit_group,group_key='a2-role:'+canonical_fingerprint(u.extraction_key)[:24])) for u in historical]
    initial={**facts,'context_records':{},'context_by_aggregate':{},'completion_records':{},'completion_by_aggregate':{},
        'coverage_repair_records':{},'coverage_repair_by_aggregate':{},'semantic_corrections':[]}
    initial.pop('identity_qualification',None)
    contextual,_,_=roles.plan_contextual_role_extraction(consumer,vocabulary,initial)
    original,_=roles._original_completion_questions(consumer,vocabulary,facts)
    unit_by_key={u.extraction_key:u for u in [*units,*historical,*contextual,*original.values()]}
    by_group={u.unit_group.group_key:u.unit_group for u in unit_by_key.values()}
    # An old contextual unit remains historical evidence even when newer
    # global answers make that aggregate no longer need a supplementary call.
    # Rebuild its exact original tag question without queuing it for execution.
    from .production_pixiv_semantics import adapt_production_semantics
    historical_contexts=defaultdict(list)
    for signal in adapt_production_semantics(consumer,vocabulary,initial).signals:
        if (signal.origin_type=='pixiv_tag_observation'
            and signal.evidence_payload.get('production_non_identity_reason')!='accepted_general_search_term'
            and signal.evidence_payload.get('source_field')!='popularity_tag'):
            historical_contexts[signal.evidence_payload['aggregate_fingerprint']].append(signal.raw_value)
    for values in historical_contexts.values():
        tags=tuple({'raw_tag':raw,'source_tag_kind':'provider_tag'}
            for raw in sorted(set(values),key=lambda raw:(canonical_source_key(raw),raw)))
        signature=canonical_fingerprint({'schema':'production_pixiv_contextual_roles_v1','tags':tags})
        group=SourceCandidateInputGroup(group_key='a2-context:'+signature[:24],provider='pixiv',tags=tags,
            data_origin='production_metadata_contextual_supplement',source_work_id_present=True)
        by_group[group.group_key]=group
    source_by_question=defaultdict(list);source_count=0;raw_errors=[];original_questions={};original_requests={}
    for group in by_group.values():original_questions[canonical_fingerprint(group_prompt_payload(group))]=group
    try:contract=RoleSourceContract(ledger,legacy_calls=lambda:_anchored_legacy_role_calls(cache_dir),
        schema_history=lambda:_anchored_role_calls(cache_dir))
    except (ValueError,AdjudicationBudgetBlocked) as exc:
        raise ValueError('semantic_role_source_ledger_invalid:'+str(exc)) from exc
    paths=sorted(checked_cache_path(cache_dir,p) for p in [*(cache_dir/'raw').glob('*.json'),*(cache_dir/'raw'/'attempts').glob('*.json'),
                  *(cache_dir/'response-recovery').glob('*.json')])
    # Later attempts retain full request envelopes. They can reconstruct an
    # earlier partial batch's identical group, including answered siblings
    # preserved by the normal resumable unit merge.
    for path in paths:
        saved=json.loads(path.read_text(encoding='utf-8'))
        if not saved.get('request_groups') or not saved.get('request_messages'):continue
        groups=[SourceCandidateInputGroup(**g) for g in saved['request_groups']]
        messages=replay_role_request_messages(groups)
        fingerprint=canonical_fingerprint({'model':saved['model'],'messages':messages,
            'temperature':saved.get('temperature',0.0),'max_tokens':saved.get('max_tokens',6000)})
        if fingerprint!=saved.get('input_fingerprint') or messages!=saved['request_messages']:continue
        for group in groups:
            previous=by_group.get(group.group_key)
            if previous and group_prompt_payload(previous)!=group_prompt_payload(group):
                raise ValueError('semantic_role_group_question_conflict')
            by_group[group.group_key]=group
    for path in paths:
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved.get('model')!='gpt-4.1-mini':continue
        reconstruction=checked_cache_path(cache_dir,cache_dir/'question-reconstruction'/f"{saved.get('input_fingerprint','')}.json")
        try:
            if saved.get('request_groups'):
                groups=[SourceCandidateInputGroup(**g) for g in saved['request_groups']]
            elif reconstruction.is_file():
                import hashlib
                proof=json.loads(reconstruction.read_text(encoding='utf-8'))
                if proof['source_raw_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():
                    raise ValueError('semantic_legacy_raw_changed')
                groups=[SourceCandidateInputGroup(**g) for g in proof['request_groups']]
            else:
                rows=json.loads(saved['content'])['records']
                groups=[by_group[r['group_key']] for r in rows]
        except (KeyError,ValueError,TypeError) as exc:
            raw_errors.append({'path':str(path.relative_to(cache_dir)),'error':'original_request_unreconstructed:'+str(exc)})
            continue  # Invalid raw is history, not answer evidence.
        if not any(canonical_fingerprint(group_prompt_payload(g)) in required_questions for g in groups):continue
        messages=replay_role_request_messages(groups)
        fingerprint=canonical_fingerprint({'model':saved['model'],'messages':messages,
            'temperature':saved.get('temperature',0.0),'max_tokens':saved.get('max_tokens',6000)})
        if fingerprint!=saved.get('input_fingerprint') and not saved.get('request_messages') and len(groups)<=6:
            # Provider rows need not preserve input order. Only an exact match
            # to the retained original request hash establishes that order.
            from itertools import permutations
            for ordering in permutations(groups):
                proposed=replay_role_request_messages(ordering)
                candidate=canonical_fingerprint({'model':saved['model'],'messages':proposed,
                    'temperature':saved.get('temperature',0.0),'max_tokens':saved.get('max_tokens',6000)})
                if candidate==saved.get('input_fingerprint'):
                    groups=list(ordering);messages=proposed;fingerprint=candidate;break
        if fingerprint!=saved.get('input_fingerprint') or (saved.get('request_messages') is not None and saved['request_messages']!=messages):
            raw_errors.append({'path':str(path.relative_to(cache_dir)),'error':'original_request_fingerprint_unreconstructed'})
            continue
        for group in groups:original_questions[canonical_fingerprint(group_prompt_payload(group))]=group
        request=original_requests.setdefault(fingerprint,{'request_groups':[asdict(g) for g in groups],
            'logical_targets':roles.BudgetedExtractionProvider.logical_keys(groups),
            'attempt_ids':[c['id'] for c in calls_by_key['role-extraction:'+fingerprint]],'raw_paths':[]})
        request['raw_paths'].append(str(path.relative_to(cache_dir)))
        try:
            admitted=contract.admit(saved,groups,path.read_bytes(),path=str(path.relative_to(cache_dir)))
        except ValueError as exc:
            raw_errors.append({'path':str(path.relative_to(cache_dir)),'error':str(exc)})
            # Reject this attempted source without poisoning an independent
            # valid answer. Its original failure remains in the inventory.
            continue
        legacy_logical_replay.update(admitted['anchored_legacy_logical_replay'])
        raw_errors.extend({'path':str(path.relative_to(cache_dir)),'group_key':k,'error':v}
            for k,v in admitted['errors'].items())
        for source in admitted['sources']:
            source_by_question[canonical_fingerprint(group_prompt_payload(source['group']))].append(source)
        source_count+=1
    aggregate_tags=defaultdict(set)
    for signal in consumer.signals:
        if signal.origin_type=='pixiv_tag_observation':aggregate_tags[signal.evidence_payload['aggregate_fingerprint']].add(signal.raw_value)
    records_by_key={k:r for kind in ('records','context_records','completion_records','coverage_repair_records','correction_records')
        for k,r in facts.get(kind,{}).items()}
    use=full_facts.get('source_recovery',{}).get('records',{})
    archive={k for k,e in use.items() if e['disposition']=='archive_only_unavailable'}
    projections=full_facts.get('current_record_projections',{})
    records_by_key={k:projections.get(k,r) for k,r in records_by_key.items()}
    missing_direct=[k for k,r in records_by_key.items() if r.get('origin')!='existing_f7a_deterministic'
        and not source_by_question.get(r.get('input_fingerprint')) and k not in archive]
    if diagnostic:
        # Complete native inventory, not release authority or a paid plan.
        # Every original row is retained, including unavailable history.
        inventory={}
        for kind in ('records','context_records','completion_records','coverage_repair_records','correction_records'):
            for key,record in facts.get(kind,{}).items():
                original_record=record;record=projections.get(key,record)
                direct=source_by_question.get(record.get('input_fingerprint'),[])
                error=None
                if direct:
                    try:validate_record_projection(record,direct)
                    except ValueError as exc:error=str(exc)
                inventory[key]={'kind':kind,'question':record.get('input_fingerprint'),
                    'original_record_fingerprint':canonical_fingerprint(original_record),
                    'original_group':asdict(original_questions[record['input_fingerprint']])
                        if record.get('input_fingerprint') in original_questions else None,
                    'deterministic':record.get('origin')=='existing_f7a_deterministic',
                    'direct_sources':[{**s,'group':asdict(s['group'])} for s in direct],
                    'projection_error':error,'inherited_valid_response_keys':record.get('inherited_valid_response_keys',[])}
        return {'record_count':len(inventory),'validated_raw_count':source_count,'records':inventory,
            'missing_direct':missing_direct,'raw_errors':raw_errors,'new_provider_calls':0,
            'original_requests':original_requests,
            'anchored_legacy_logical_replay':legacy_logical_replay}
    if missing_direct:raise ValueError('semantic_role_original_response_missing:'+json.dumps(missing_direct))
    def record_sources(record,visited=()):
        key=record['extraction_key']
        if key in visited:raise ValueError('semantic_role_inheritance_cycle')
        direct=list(source_by_question.get(record.get('input_fingerprint'),[]));result=list(direct)
        for parent in record.get('inherited_valid_response_keys',[]):
            prior=records_by_key.get(parent)
            if not prior or prior.get('parent_extraction_key')!=record.get('parent_extraction_key'):
                raise ValueError('semantic_role_inherited_question_changed')
            inherited=record_sources(prior,(*visited,key))
            if direct and any(s['group'].tags!=direct[0]['group'].tags for s in inherited):
                raise ValueError('semantic_role_inherited_context_changed')
            result.extend(inherited)
        return result
    def replay_coverage(record):
        return original_target_accounting(record,record_sources(record))
    proofs=[];missing_sources=[]
    for kind in ('records','context_records','completion_records','coverage_repair_records','correction_records'):
        for key,record in facts.get(kind,{}).items():
            if key in archive:
                proofs.append({'key':key,'source_use':'archive_only_unavailable','current_semantic_use':False,
                    'original_record_fingerprint':canonical_fingerprint(record),'question':record.get('input_fingerprint')})
                continue
            record=projections.get(key,record)
            sources=record_sources(record)
            if record.get('origin')=='existing_f7a_deterministic':
                unit=unit_by_key.get(key)
                if not unit or unit.llm_required:raise ValueError('semantic_role_deterministic_source_missing')
                bundle=deterministic_bundle_for_unit(unit,run_id='production-pixiv-roles',run_label='production-pixiv-roles')
                expected=roles._record(unit,'gpt-4.1-mini',bundle.record_verdicts[0],bundle.candidates,origin='existing_f7a_deterministic')
                if expected!=record:raise ValueError('semantic_role_deterministic_answer_changed')
                proofs.append({'key':key,'deterministic':True});continue
            if not sources:
                missing_sources.append(key);continue
            unit=unit_by_key.get(key)
            if unit and roles._identity(unit,'gpt-4.1-mini')['input_fingerprint']!=record['input_fingerprint']:
                # Existing case-variant compatibility still requires the actual
                # original representative and reconstructed question identity.
                prior=replace(unit,normalized_value=record['raw_value'],unit_group=replace(unit.unit_group,
                    tags=tuple({**t,'raw_tag':record['raw_value']} for t in unit.unit_group.tags)))
                if roles._identity(prior,'gpt-4.1-mini')['input_fingerprint']!=record['input_fingerprint']:
                    raise ValueError('semantic_role_current_question_changed')
            try:validate_record_projection(record,sources)
            except ValueError as exc:raise ValueError('semantic_role_answer_not_in_original_response:'+key+':'+str(exc)) from exc
            if record.get('target_coverage') and replay_coverage(record)!=record['target_coverage']:
                raise ValueError('semantic_role_target_coverage_changed:'+key)
            proofs.append({'key':key,'question':record['input_fingerprint'],
                'sources':[{'path':s['path'],'fingerprint':s['fingerprint'],'attempts':s['attempts'],
                    'unit_source_proof':s['unit_source_proof']} for s in sources]})
    if missing_sources:raise ValueError('semantic_role_original_response_missing:'+','.join(missing_sources))
    for mapping,kind in [('context_by_aggregate','context_records'),('completion_by_aggregate','completion_records'),
                         ('coverage_repair_by_aggregate','coverage_repair_records')]:
        mappings={**facts.get(mapping,{}),**full_facts.get('current_record_mappings',{}).get(mapping,{})}
        for aggregate,key in mappings.items():
            if key in archive:continue
            record=facts[kind][key];sources=source_by_question.get(record['input_fingerprint'],[])
            if not sources:raise ValueError('semantic_role_mapped_question_missing')
            for source in sources:
                tags={t['raw_tag'] for t in source['group'].tags}
                if not tags<=aggregate_tags[aggregate]:raise ValueError('semantic_role_source_context_changed')
                if mapping!='context_by_aggregate' and tags!=aggregate_tags[aggregate]:
                    raise ValueError('semantic_role_complete_context_changed')
    if facts.get('semantic_corrections'):
        # Rebuild scoped supersession and equivalent-question reuse from the
        # actual source tags, then validate all correction answers again.
        adapt_production_semantics(consumer,vocabulary,full_facts)
    return {'record_count':len(proofs),'validated_raw_count':source_count,'records':proofs,'new_provider_calls':0,
        'anchored_legacy_logical_replay':legacy_logical_replay}


def verify_selected_judgment_sources(edges,signals,judgments,config,ledger):
    from collections import defaultdict
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
    calls_by_key=defaultdict(list)
    for call in calls.values():calls_by_key[call['key']].append(call)
    roots=[Path(config.durable_cache_dir),*(Path(p) for p in config.semantic_cache_dirs)]
    records={};evidence=[]
    def load(key):
        if key not in records:
            found=[]
            for root in roots:
                path=checked_cache_path(root,root/'records'/f'{key}.json')
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
        source_metadata=resolver.llm_cache_metadata({**block,**source['input_signal_summary']},config=config)
        if source.get('cache_key')!=chain[-1]:
            raise ValueError('semantic_judgment_original_source_identity_changed')
        decision_key='decision-input:'+resolver._decision_input_key(source['input_signal_summary'])
        expected_keys={decision_key}
        if source_metadata['cache_key']==chain[-1]:expected_keys.add(source_metadata['cache_key'])
        if config.prompt_version==resolver.PRODUCTION_PAIR_PROMPT_VERSION:
            expected_keys={key+':prompt:'+config.prompt_version for key in expected_keys}
        response=source.get('budget_response')
        if response:
            call=calls.get(response['reservation'])
            if (not call or not AdjudicationBudget._eligible_cached_response(call) or call['key']!=response['key']
                or call['key'] not in expected_keys):
                raise ValueError('semantic_judgment_attempt_not_settled')
            if call.get('usage_known') and call['usage']!={k:response['usage'][k] for k in ('prompt_tokens','completion_tokens')}:
                raise ValueError('semantic_judgment_usage_changed')
        else:
            # Legacy sources can lack the reservation envelope. Only one
            # successful call for this exact original input establishes a
            # reusable source; unrelated or ambiguous tickets cannot fill it.
            matches=[call for key in expected_keys for call in calls_by_key[key]
                     if AdjudicationBudget._eligible_cached_response(call)]
            if len(matches)!=1:
                raise ValueError('semantic_legacy_judgment_source_attempt_missing_or_ambiguous')
            call=matches[0]
        evidence.append({'cache_key':metadata['cache_key'],'source_chain':chain,
                         'source_fingerprint':canonical_fingerprint(source),
                         'source_attempt_id':call['id']})
    return {'selected_pair_count':len(selected),'judgment_count':len(judgments),'sources':evidence,'new_provider_calls':0}


def _replay_source_selection(aggregates,vocabulary,facts,judgments,manifest,private_root,*,semantic_cache_dirs=(),history=None,historical_predecessor=False):
    from .production_pixiv_service import production_consumer,build_production_clustering
    from .source_concept_budget import AdjudicationBudget
    private_root=Path(private_root).resolve(strict=True)
    from .production_pixiv_pair_correction import verify_correction_admission
    def read(name):
        path=(private_root/name).resolve(strict=True)
        if not path.is_relative_to(private_root):raise ValueError('semantic_source_receipt_outside_task')
        return json.loads(path.read_text(encoding='utf-8'))
    receipt=read(manifest['adjudication_receipt'])
    ledger=read('llm-budget-private.json');AdjudicationBudget._charged(ledger)
    from .production_pixiv_service import PRODUCTION_POLICY,HISTORICAL_AMBIGUITY_POLICY
    recorded_policy=manifest['input_identity']['versions']['production_policy']
    historical_policy=(HISTORICAL_AMBIGUITY_POLICY if historical_predecessor and recorded_policy==HISTORICAL_AMBIGUITY_POLICY else None)
    if recorded_policy!=(historical_policy or PRODUCTION_POLICY):
        raise ValueError('semantic_production_policy_replay_mismatch')
    consumer=production_consumer(aggregates,_historical_policy=historical_policy)
    roles=verify_role_response_sources(consumer,vocabulary,facts,private_root/'role-cache',ledger)
    config=resolver.LLMAdjudicationConfig(enabled=True,max_calls=1000000,max_budget_usd=30,
        model_label='gpt-4.1-mini',selection_policy='all_eligible',prompt_version=resolver.PRODUCTION_PAIR_PROMPT_VERSION,
        durable_cache_dir=str(private_root/'llm-cache'),semantic_cache_dirs=tuple(semantic_cache_dirs),semantic_cache_reuse=True)
    initial=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts,_historical_policy=historical_policy)
    by_key={s.signal_key:s for s in initial.resolution.signals}
    def work_pair(edge):return by_key[edge.left_signal_key].role_hint==by_key[edge.right_signal_key].role_hint=='work'
    work_keys={s.signal_key for s in initial.resolution.signals if s.role_hint=='work'}
    work_judgments=[j for j in judgments if j['left_signal_key'] in work_keys and j['right_signal_key'] in work_keys]
    other_judgments=[j for j in judgments if not (j['left_signal_key'] in work_keys and j['right_signal_key'] in work_keys)]
    work_edges=resolver.select_llm_adjudication_edges([e for e in initial.resolution.edge_candidates if work_pair(e)],
        signals=initial.resolution.signals,config=config)
    work=verify_selected_judgment_sources(work_edges,initial.resolution.signals,work_judgments,config,ledger)
    corrections=[]
    if history:corrections.append(verify_correction_admission('work',work_edges,initial.resolution.signals,
        config,ledger,history,private_root))
    del initial
    contextual=build_production_clustering(consumer,vocabulary=vocabulary,role_facts=facts,judgments=work_judgments,_historical_policy=historical_policy)
    others=resolver.select_llm_adjudication_edges([e for e in contextual.resolution.edge_candidates if not work_pair(e)],
        signals=contextual.resolution.signals,config=config)
    remaining=verify_selected_judgment_sources(others,contextual.resolution.signals,other_judgments,config,ledger)
    if history:corrections.append(verify_correction_admission('remaining',others,contextual.resolution.signals,
        config,ledger,history,private_root))
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
    return {'role_sources':roles,'work_sources':work,'remaining_sources':remaining,
            'correction_admissions':corrections,'new_provider_calls':0}


def verify_release_sources(aggregates,vocabulary,facts,judgments,manifest,private_root,*,semantic_cache_dirs=()):
    history=manifest.get('correction_history');binding=None
    if facts.get('semantic_corrections'):
        if not history:raise ValueError('correction_admission_required')
        from .production_pixiv_pair_correction import bind_correction_prior
        binding=bind_correction_prior(aggregates,vocabulary,facts,history['prior'],private_root,
            semantic_cache_dirs=semantic_cache_dirs)
        if binding!=history.get('prior_binding'):raise ValueError('correction_prior_binding_changed')
    result=_replay_source_selection(aggregates,vocabulary,facts,judgments,manifest,private_root,
        semantic_cache_dirs=semantic_cache_dirs,history=history)
    result['correction_prior_binding']=binding
    return result
