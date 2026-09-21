"""Read-only planning for bounded re-adjudication after semantic corrections."""
from dataclasses import asdict,replace
from collections import defaultdict
import json
from pathlib import Path
import hashlib

from . import source_concept_resolver_service as resolver
from .source_concept_budget import AdjudicationBudget


def read_correction_prior(private_root,filename):
    """Bind the complete predecessor list to its retained processing/selection."""
    from .pixiv_metadata_projection_service import canonical_fingerprint
    root=Path(private_root).resolve(strict=True)
    def read(name):
        path=(root/name).resolve(strict=True)
        if not path.is_relative_to(root):raise ValueError('correction_prior_outside_task')
        return json.loads(path.read_text(encoding='utf-8')),path
    rows,path=read(filename)
    suffix='-judgments-private.json'
    if not path.name.endswith(suffix) or not isinstance(rows,list) or not rows:
        raise ValueError('correction_prior_judgments_required')
    manifest,manifest_path=read(path.name[:-len(suffix)]+'-semantic-manifest-private.json')
    if manifest['input_identity']['judgments']!=canonical_fingerprint(rows):
        raise ValueError('correction_prior_judgments_changed')
    processing,_=read(manifest['adjudication_receipt'])
    selected=[]
    for stage in ('work','remaining'):
        part,_=read(manifest[stage+'_selection'])
        if len(part)!=processing[stage+'_stage']['selected_pair_count']:
            raise ValueError('correction_prior_selection_incomplete')
        selected.extend(part)
    pair=lambda row:tuple(sorted((row['left_signal_key'],row['right_signal_key'])))
    if (len(rows)!=len(selected) or len({pair(r) for r in rows})!=len(rows)
        or len({pair(r) for r in selected})!=len(selected)
        or {pair(r) for r in rows}!={pair(r) for r in selected}
        or processing['judgment_count']!=len(rows)
        or processing['selected_pair_count']!=len(rows)
        or any(manifest['processing'][k]!=len(rows) for k in ('selected_pair_count','judgment_count'))
        or any(value.get(k)!=0 for value in (processing,manifest['processing'])
               for k in ('error_count','remaining_missing_pair_count'))):
        raise ValueError('correction_prior_selection_incomplete')
    return rows,{'judgments':str(path.relative_to(root)),'judgments_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'manifest':str(manifest_path.relative_to(root)),'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'selected_pair_count':len(rows)}


def verify_correction_prior_sources(rows,config,ledger):
    if not isinstance(rows,list) or not rows:raise ValueError('correction_prior_judgments_required')
    roots=[resolver._cache_root(config),*(Path(p) for p in config.semantic_cache_dirs)]
    calls={row['id']:row for row in ledger['calls']};seen=set()
    def load(key):
        for root in roots:
            path=resolver.checked_cache_path(root,root/'records'/(key+'.json'))
            if path.is_file():return json.loads(path.read_text(encoding='utf-8'))
        raise ValueError('correction_prior_cache_missing')
    for row in rows:
        pair=(row['left_signal_key'],row['right_signal_key'])
        if pair in seen:raise ValueError('correction_prior_duplicate')
        seen.add(pair);record=load(row['cache_key'])
        if not resolver._cache_record_is_exact_compatible(record,metadata=row,config=config):
            raise ValueError('correction_prior_cache_identity_changed')
        if row['input_signal_summary']!=record['input_signal_summary'] or record.get('provider_model')!=config.model_label:
            raise ValueError('correction_prior_input_changed')
        derived=resolver._judgment_from_cache_record(record,block_payload=record['input_signal_summary'],
            selected_pair_id='',cache_status='hit',reuse_level='verified_prior')
        if any(row.get(k)!=derived.get(k) for k in ('judgment_id','cache_key','pair_identity','pair_payload_hash',
            'left_signal_key','right_signal_key','decision','confidence','reason_code','error_state')):
            raise ValueError('correction_prior_answer_changed')
        source=record;chain={record['cache_key']}
        while source.get('reused_from_cache_key'):
            key=source['reused_from_cache_key']
            if key in chain:raise ValueError('correction_prior_reuse_cycle')
            chain.add(key);source=load(key)
            if (resolver._decision_input_key(source['input_signal_summary'])!=resolver._decision_input_key(record['input_signal_summary'])
                or resolver.llm_public_decision(source['decision'])!=resolver.llm_public_decision(record['decision'])
                or source['confidence']!=record['confidence']
                or source.get('provider_model')!=config.model_label
                or source.get('prompt_template_version')!=record.get('prompt_template_version')):
                raise ValueError('correction_prior_reuse_changed')
        response=source.get('budget_response')
        if response:
            call=calls.get(response['reservation'])
            if not call or call['status']=='reserved' or call['key']!=response['key']:
                raise ValueError('correction_prior_attempt_unverified')
            if call.get('usage_known') and call['usage']!={k:response['usage'][k] for k in ('prompt_tokens','completion_tokens')}:
                raise ValueError('correction_prior_usage_changed')
    return len(rows)


def bind_correction_prior(aggregates,vocabulary,facts,prior,private_root,*,semantic_cache_dirs=()):
    """Bind and replay the actual input predecessor of this correction batch."""
    from .pixiv_metadata_projection_service import canonical_fingerprint
    from .production_pixiv_corrections import correction_units
    from .production_pixiv_semantics import adapt_production_semantics
    from .production_pixiv_service import production_consumer
    from .production_pixiv_release_provenance import _replay_source_selection
    root=Path(private_root).resolve(strict=True)
    def read(name):
        path=(root/name).resolve(strict=True)
        if not path.is_relative_to(root):raise ValueError('correction_prior_outside_task')
        return json.loads(path.read_text(encoding='utf-8')),path
    manifest,_=read(prior['manifest'])
    current_identity={name:canonical_fingerprint(value) for name,value in (
        ('aggregates',aggregates),('vocabulary',vocabulary),('role_facts',facts))}
    from .production_pixiv_service import HISTORICAL_AMBIGUITY_POLICY
    if all(manifest['input_identity'].get(name)==digest for name,digest in current_identity.items()):
        if manifest['input_identity'].get('versions',{}).get('production_policy')!=HISTORICAL_AMBIGUITY_POLICY:
            raise ValueError('correction_prior_policy_transition_invalid')
        rows,actual_prior=read_correction_prior(root,prior['judgments'])
        if actual_prior!=prior:raise ValueError('correction_prior_provenance_changed')
        replay=_replay_source_selection(aggregates,vocabulary,facts,rows,manifest,root,
            semantic_cache_dirs=semantic_cache_dirs,historical_predecessor=True)
        return {'prior':prior,'input_identity':current_identity,'kind':'ambiguity_policy_only',
            'new_correction_aggregate_count':0,'selected_pair_count':len(rows),
            'source_replay_fingerprint':canonical_fingerprint(replay)}
    provenance=facts.get('incremental_correction_provenance') or facts.get('correction_provenance')
    if not isinstance(provenance,dict):raise ValueError('correction_prior_input_provenance_required')
    previous,previous_path=read(provenance['prior_role_facts'])
    plan,plan_path=read(provenance['plan'])
    previous_digest=hashlib.sha256(previous_path.read_bytes()).hexdigest()
    if (previous_digest!=provenance.get('prior_sha256')
        or plan.get('source_role_facts')!=provenance['prior_role_facts']
        or plan.get('source_role_facts_sha256')!=previous_digest):
        raise ValueError('correction_prior_role_facts_changed')
    manifest,_=read(prior['manifest']);rows,actual_prior=read_correction_prior(root,prior['judgments'])
    if actual_prior!=prior:raise ValueError('correction_prior_provenance_changed')
    expected={name:canonical_fingerprint(value) for name,value in (
        ('aggregates',aggregates),('vocabulary',vocabulary),('role_facts',previous))}
    if any(manifest['input_identity'].get(name)!=digest for name,digest in expected.items()):
        raise ValueError('correction_prior_semantic_input_changed')
    mutable={'semantic_corrections','correction_records','correction_equivalent_sources','correction_provenance',
        'incremental_correction_provenance','correction_execution','previous_execution_receipts'}
    if any(previous.get(key)!=facts.get(key) for key in previous.keys()|facts.keys() if key not in mutable):
        raise ValueError('correction_prior_unrelated_role_fact_changed')
    before_requests={r['aggregate_fingerprint']:r for r in previous.get('semantic_corrections',[])}
    current_requests={r['aggregate_fingerprint']:r for r in facts.get('semantic_corrections',[])}
    if (len(before_requests)!=len(previous.get('semantic_corrections',[]))
        or len(current_requests)!=len(facts.get('semantic_corrections',[]))
        or any(current_requests.get(key)!=row for key,row in before_requests.items())):
        raise ValueError('correction_prior_supersession_history_changed')
    for field in ('correction_records','correction_equivalent_sources'):
        if any(facts.get(field,{}).get(key)!=row for key,row in previous.get(field,{}).items()):
            raise ValueError('correction_prior_answer_history_changed')
    added=[current_requests[key] for key in sorted(current_requests.keys()-before_requests.keys())]
    planned=plan.get('requests',[])
    if (not added or sorted(added,key=lambda r:r['aggregate_fingerprint'])!=sorted(planned,key=lambda r:r['aggregate_fingerprint'])
        or {r['aggregate_fingerprint'] for r in added}!=set(plan.get('scope_aggregates',[]))):
        raise ValueError('correction_prior_plan_scope_changed')
    old_consumer=adapt_production_semantics(production_consumer(aggregates),vocabulary,previous)
    correction_units(old_consumer,previous,added)
    # A retained legacy execution need not claim today's admission gate. Its
    # actual answers and full selection are still rebuilt, never self-attested.
    replay=_replay_source_selection(aggregates,vocabulary,previous,rows,manifest,root,
        semantic_cache_dirs=semantic_cache_dirs,historical_predecessor=True)
    return {'prior':prior,'input_identity':expected,'role_facts':str(previous_path.relative_to(root)),
        'role_facts_sha256':previous_digest,'plan':str(plan_path.relative_to(root)),
        'plan_sha256':hashlib.sha256(plan_path.read_bytes()).hexdigest(),'new_correction_aggregate_count':len(added),
        'source_replay_fingerprint':canonical_fingerprint(replay),
        'selected_pair_count':replay['work_sources']['selected_pair_count']+replay['remaining_sources']['selected_pair_count'],
        'original_invocation_not_relabelled':True,'new_provider_calls':0}


def plan_corrected_pairs(edges,signals,prior_judgments,config,ledger):
    verified_prior_count=verify_correction_prior_sources(prior_judgments,config,ledger)
    # Every recorded key association is part of the same lifetime. Keeping
    # only the immediate predecessor loses attempts after another correction.
    parent={}
    def find(key):
        parent.setdefault(key,key)
        while parent[key]!=key:
            parent[key]=parent[parent[key]];key=parent[key]
        return key
    for call in ledger['calls']:
        keys=sorted({key.split(':prompt:',1)[0] for key in (call['key'],*call.get('logical_keys',[]))
                     if key.startswith('decision-input:')})
        for key in keys[1:]:parent[find(key)]=find(keys[0])
    families=defaultdict(set)
    for key in list(parent):families[find(key)].add(key)
    by_key={s.signal_key:s for s in signals}
    aliases=resolver._context_equivalence_lookup(signals)
    contexts=resolver._context_candidates_by_scope(signals,context_alias_by_key=aliases)
    def payload(signal):
        return resolver._llm_signal_payload(signal,context_by_scope=contexts,context_alias_by_key=aliases)
    # Re-evaluate the *same old source occurrences* under current semantics,
    # then join by actual decision input so a new representative cannot reset
    # the old target's lifetime. Unchanged unrelated contexts stay independent.
    predecessors=defaultdict(set);changes=[]
    for row in prior_judgments:
        left=by_key.get(row['left_signal_key']);right=by_key.get(row['right_signal_key'])
        if left is None or right is None:raise ValueError('correction_prior_source_occurrence_missing')
        current={'left':payload(left),'right':payload(right)}
        old=row['input_signal_summary']
        old_key=resolver._decision_input_key(old);new_key=resolver._decision_input_key(current)
        if old_key==new_key:continue
        previous='decision-input:'+old_key
        predecessors['decision-input:'+new_key].update({previous,*families.get(find(previous),set())})
        changes.append({'old_judgment_id':row['judgment_id'],'old_cache_key':row['cache_key'],
            'old_decision':row['decision'],'old_input':old,'new_input':current,
            'old_logical_key':'decision-input:'+old_key,'new_logical_key':'decision-input:'+new_key,
            'reason':'actual_source_role_context_or_evidence_changed','old_answer_preserved':True})
    configured=replace(config,logical_predecessors={k:tuple(sorted(v)) for k,v in predecessors.items()})
    compatible=resolver._compatible_decision_cache(configured)
    selected=resolver.select_llm_adjudication_edges(edges,signals=signals,config=configured)
    root=resolver._cache_root(configured);missing=[];reused=0;seen=set();cost=0
    attempts=defaultdict(list)
    for call in ledger['calls']:
        for key in {call['key'],*call.get('logical_keys',[])}:attempts[key].append(call)
    for edge in selected:
        block={'edge':asdict(edge),'left':payload(by_key[edge.left_signal_key]),'right':payload(by_key[edge.right_signal_key])}
        metadata=resolver.llm_cache_metadata(block,config=configured)
        key=resolver._decision_input_key(block)
        if resolver._load_exact_cache_record(root,metadata=metadata,config=configured) or compatible.get(key) or key in seen:
            reused+=1;continue
        seen.add(key)
        admission,logical=resolver._pair_budget_identity(configured,metadata,key)
        messages=resolver.pair_request_messages(block,configured)
        ceiling=len(json.dumps(messages,ensure_ascii=False).encode('utf-8'))+512
        # Same Decimal, upward rounding and price identity as real admission.
        from decimal import Decimal,ROUND_CEILING
        reserve=int((Decimal(ceiling)*Decimal(ledger['input_per_million'])+
            Decimal(600)*Decimal(ledger['output_per_million'])).to_integral_value(rounding=ROUND_CEILING))
        prior_calls={c['id']:c for k in (admission,*logical) for c in attempts[k]}
        blocked=len(prior_calls)>=3 or any(c['status']=='reserved' for c in prior_calls.values())
        missing.append({'cache_key':metadata['cache_key'],'input':block,'messages':messages,
            'logical_keys':list(logical),'prior_attempt_ids':sorted({c['id'] for k in (admission,*logical) for c in attempts[k]}),
            'blocked_by_existing_attempts':blocked,'reserve_microusd':reserve})
        if not blocked:cost+=reserve
    charged=AdjudicationBudget._charged(ledger)
    result={'verified_prior_judgment_count':verified_prior_count,
        'logical_predecessors':{k:list(v) for k,v in configured.logical_predecessors.items()},
        'selected_pair_count':len(selected),'compatible_or_same_input_reuse_count':reused,
        'missing_distinct_inputs':len(missing),'dispatchable_call_ceiling':sum(not r['blocked_by_existing_attempts'] for r in missing),
        'reserve_ceiling_microusd':cost,'charged_microusd':charged,'cap_microusd':ledger['cap_microusd'],
        'budget_headroom_sufficient':charged+cost<=ledger['cap_microusd'],
        'changed_previous_inputs':changes,'missing':missing,'new_provider_calls':0}
    return configured,result


def verify_correction_admission(stage,edges,signals,config,ledger,history,private_root):
    """Replay lineage against its retained pre-dispatch ledger and actual answers."""
    from .pixiv_metadata_projection_service import canonical_fingerprint
    root=Path(private_root).resolve(strict=True)
    def read(name):
        path=(root/name).resolve(strict=True)
        if not path.is_relative_to(root):raise ValueError('correction_admission_outside_task')
        return json.loads(path.read_text(encoding='utf-8'))
    if not history or stage not in history.get('stages',{}):
        raise ValueError('correction_admission_required')
    rows,prior=read_correction_prior(root,history['prior']['judgments'])
    if prior!=history['prior']:raise ValueError('correction_prior_provenance_changed')
    entry=history['stages'][stage];before=read(entry['ledger']);saved=read(entry['admission'])
    if canonical_fingerprint(before)!=entry['ledger_fingerprint']:
        raise ValueError('correction_admission_ledger_changed')
    AdjudicationBudget._charged(before)
    if before['calls']!=ledger['calls'][:len(before['calls'])]:
        raise ValueError('correction_admission_attempt_history_changed')
    configured,actual=plan_corrected_pairs(edges,signals,rows,config,before)
    # Current cache includes subsequent answers. Recompute lineage/selection,
    # not the old cache-miss count or old spending from a now-populated cache.
    for key in ('verified_prior_judgment_count','logical_predecessors','selected_pair_count','changed_previous_inputs'):
        if actual[key]!=saved.get(key):raise ValueError('correction_admission_lineage_changed:'+key)
    prior_ids={c['id'] for c in before['calls']}
    for logical,predecessors in configured.logical_predecessors.items():
        keys={logical,*predecessors}
        linked=[c for c in ledger['calls'] if keys & {k.split(':prompt:',1)[0] for k in (c['key'],*c.get('logical_keys',[]))}]
        new=[c for c in linked if c['id'] not in prior_ids and c['key'].split(':prompt:',1)[0]==logical]
        if new and (len(linked)>3 or any(not keys<=set(c.get('logical_keys',[])) for c in new)):
            raise ValueError('correction_admission_lifetime_reset')
    return {'stage':stage,'prior':prior,'verified_prior_judgment_count':len(rows),'new_provider_calls':0}
