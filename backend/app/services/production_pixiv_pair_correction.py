"""Read-only planning for bounded re-adjudication after semantic corrections."""
from dataclasses import asdict,replace
from collections import defaultdict
import json

from . import source_concept_resolver_service as resolver
from .source_concept_budget import AdjudicationBudget


def plan_corrected_pairs(edges,signals,prior_judgments,config,ledger):
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
        if left is None or right is None:continue
        current={'left':payload(left),'right':payload(right)}
        old=row['input_signal_summary']
        old_key=resolver._decision_input_key(old);new_key=resolver._decision_input_key(current)
        if old_key==new_key:continue
        predecessors['decision-input:'+new_key].add('decision-input:'+old_key)
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
        blocked=any(len(attempts[k])>=3 or any(c['status']=='reserved' for c in attempts[k]) for k in (admission,*logical))
        missing.append({'cache_key':metadata['cache_key'],'input':block,'messages':messages,
            'logical_keys':list(logical),'prior_attempt_ids':sorted({c['id'] for k in (admission,*logical) for c in attempts[k]}),
            'blocked_by_existing_attempts':blocked,'reserve_microusd':reserve})
        if not blocked:cost+=reserve
    charged=AdjudicationBudget._charged(ledger)
    result={'selected_pair_count':len(selected),'compatible_or_same_input_reuse_count':reused,
        'missing_distinct_inputs':len(missing),'dispatchable_call_ceiling':sum(not r['blocked_by_existing_attempts'] for r in missing),
        'reserve_ceiling_microusd':cost,'charged_microusd':charged,'cap_microusd':ledger['cap_microusd'],
        'budget_headroom_sufficient':charged+cost<=ledger['cap_microusd'],
        'changed_previous_inputs':changes,'missing':missing,'new_provider_calls':0}
    return configured,result
