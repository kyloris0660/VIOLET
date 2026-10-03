from dataclasses import replace
from types import SimpleNamespace
import pytest

from app.services.production_pixiv_semantics import build_semantic_vocabulary,adapt_production_semantics
from app.services.source_concept_resolver_service import (
    SourceConceptSignalInput,build_source_concept_signal_drafts,resolve_source_concepts,
    LLMAdjudicationConfig,select_llm_adjudication_edges,
)


def source(name,work):
    return SourceConceptSignalInput(origin_type='pixiv_tag_observation',origin_key=work+name,provider='pixiv',
        raw_value=name,display_value=name,canonical_value=name,confidence=None,
        role_hint='unknown',source_kind='provider_tag',work_context_key='pixiv:'+work+':0',
        trust_tier='weak',status='needs_review',evidence_payload={'work_id':work,'page_index':0})


def vocabulary():
    return build_semantic_vocabulary([
        {'canonical_name':'alice_(garden)','display_name':'爱丽丝','aliases_json':'["アリス"]','category':'character','source':'static'},
        {'canonical_name':'dress','display_name':'裙子','category':'general','source':'static'},
        {'canonical_name':'artist','display_name':'画师','category':'artist','source':'static'},
    ])


def test_artwork_context_does_not_fragment_a_character_and_alias_needs_judgment():
    signals=build_source_concept_signal_drafts([source('爱丽丝','12345678'),source('爱丽丝','23456789'),
        source('アリス','12345678'),source('裙子','12345678'),source('画师','12345678')])
    consumer=SimpleNamespace(signals=signals,input_fingerprint='input')
    # Use the real frozen consumer dataclass replacement protocol.
    from dataclasses import make_dataclass
    Consumer=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)
    adapted=adapt_production_semantics(Consumer(signals,'input'),vocabulary())
    by_name={s.raw_value:s for s in adapted.signals}
    assert by_name['爱丽丝'].role_hint=='character' and by_name['爱丽丝'].work_context_key=='garden'
    assert by_name['アリス'].canonical_key!=by_name['爱丽丝'].canonical_key
    assert by_name['裙子'].status=='rejected'
    assert by_name['画师'].role_hint=='unknown'
    assert all(s.evidence_payload['production_original_context'] for s in adapted.signals)
    result=resolve_source_concepts(adapted.signals,run_id='test-production-semantics')
    chinese=[s for s in adapted.signals if s.raw_value=='爱丽丝']
    assert any({s.signal_key for s in chinese}<={s.signal_key for s in concept.signals} for concept in result.concepts)
    pairs=select_llm_adjudication_edges(result.edge_candidates,signals=result.signals,
        config=LLMAdjudicationConfig(enabled=True,max_calls=100,selection_policy='all_eligible'))
    assert any({edge.left_signal_key,edge.right_signal_key}=={by_name['アリス'].signal_key,signal.signal_key}
        for edge in pairs for signal in chinese)


@pytest.mark.parametrize('decision,merged',[('must_link',True),('cannot_link',False)])
def test_production_work_aliases_are_not_each_others_enclosing_context(decision,merged):
    from app.services.source_concept_resolver_service import _context_candidates_by_scope,signal_context_key
    inputs=[replace(source(name,'12345678'),role_hint='work',work_context_key=None,trust_tier='medium',
        status='active',evidence_payload={'production_candidate_scope':'pixiv:work:12345678'})
        for name in ('ExampleAdventure','冒险')]
    signals=build_source_concept_signal_drafts(inputs)
    contexts=_context_candidates_by_scope(signals)
    assert all(signal_context_key(signal,contexts)==(None,None) for signal in signals)
    judgments=[{'left_signal_key':signals[0].signal_key,'right_signal_key':signals[1].signal_key,
        'decision':decision,'confidence':0.9}]
    result=resolve_source_concepts(signals,run_id='production-work-context-test',llm_judgments=judgments)
    assert (len(result.concepts)==1) is merged


def test_explicit_character_and_parenthetical_work_context_guards_still_apply():
    from app.services.source_concept_resolver_service import signal_context_key
    inputs=[replace(source('Aster','12345678'),role_hint='character',work_context_key=context,
        trust_tier='medium',status='active',origin_key='aster-'+context,
        evidence_payload={'production_candidate_scope':'pixiv:work:12345678'}) for context in ('garden','ocean')]
    signals=build_source_concept_signal_drafts(inputs)
    result=resolve_source_concepts(signals,run_id='production-character-guard-test',llm_judgments=[
        {'left_signal_key':signals[0].signal_key,'right_signal_key':signals[1].signal_key,
         'decision':'must_link','confidence':0.9}])
    assert len(result.concepts)==2
    work=replace(signals[0],role_hint='work',work_context_key=None,raw_value='Episode (Series)',parenthetical_context='Series')
    assert signal_context_key(work,{})==('series','parenthetical_context')


@pytest.mark.parametrize('work_decision,shared_context',[('must_link',True),('cannot_link',False)])
def test_only_materialized_work_aliases_supply_character_context(work_decision,shared_context):
    from app.services.production_pixiv_service import _with_adjudicated_work_context
    from app.services.source_concept_resolver_service import _context_candidates_by_scope,signal_context_key
    inputs=[]
    for work_id,work_name,character in [('12345678','ExampleAdventure','Alice'),('23456789','冒险','アリス')]:
        for name,role in [(work_name,'work'),(character,'character')]:
            inputs.append(replace(source(name,work_id),role_hint=role,work_context_key=None,
                trust_tier='medium',status='active',evidence_payload={'production_candidate_scope':'pixiv:work:'+work_id}))
    signals=build_source_concept_signal_drafts(inputs)
    work_keys=[signal.signal_key for signal in signals if signal.role_hint=='work']
    judgments=[{'left_signal_key':work_keys[0],'right_signal_key':work_keys[1],
        'decision':work_decision,'confidence':0.9}]
    adapted=_with_adjudicated_work_context(signals,judgments,'test-work-bootstrap')
    contexts=_context_candidates_by_scope(adapted)
    characters=[signal for signal in adapted if signal.role_hint=='character']
    values=[signal_context_key(signal,contexts) for signal in characters]
    assert (values[0][0]==values[1][0]) is shared_context
    assert all(value[1]=='unique_source_or_media_work_context' for value in values)
    assert all(signal.work_context_key is None for signal in characters)
    # Work identity supplies context only. Character aliases need their own
    # accepted judgment, even after their franchises have been resolved.
    result=resolve_source_concepts(adapted,run_id='test-work-no-character-truth',llm_judgments=judgments)
    assert not any({signal.signal_key for signal in characters}<={signal.signal_key for signal in concept.signals}
        for concept in result.concepts)


@pytest.mark.parametrize('origin',['records','context_records','completion_records'])
@pytest.mark.parametrize('context,retained',[('Uniform',False),('ObservedGame',True),('OtherGame',False)])
def test_model_context_requires_typed_work_in_same_artwork(origin,context,retained):
    from dataclasses import make_dataclass
    from copy import deepcopy
    inputs=[replace(source('LongCharacterName','12345678'),evidence_payload={
        'work_id':'12345678','page_index':0,'aggregate_fingerprint':'exact-group'}),
        replace(source('Uniform','12345678'),role_hint='general'),
        replace(source('ObservedGame','12345678'),role_hint='work',status='active'),
        replace(source('OtherGame','87654321'),role_hint='work',status='active')]
    signals=build_source_concept_signal_drafts(inputs)
    consumer=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'input')
    candidate={'raw_value':'LongCharacterName','candidate_role':'character','confidence':0.9,
        'origin_type':'source_tag_observation','extraction_action':'normal_tag_candidate','candidate_status':'active_candidate',
        'evidence_payload':{},'work_context_key':context}
    fact={'raw_value':'LongCharacterName','verdict':'explicit_name_found','candidates':[candidate]}
    facts={'schema_version':'violet.production-pixiv-role-result.v1','records':{}}
    facts[origin]={'answer':fact}
    if origin!='records':facts[origin.replace('_records','_by_aggregate')]={'exact-group':'answer'}
    saved=deepcopy(facts)
    adapted=adapt_production_semantics(consumer,build_semantic_vocabulary([]),facts)
    character=next(s for s in adapted.signals if s.raw_value=='LongCharacterName')
    assert character.role_hint=='character'
    assert character.work_context_key==(context.casefold() if retained else None)
    assert ('production_rejected_model_context' in character.evidence_payload) is not retained
    assert facts==saved


def test_source_parenthetical_context_is_not_rejected_as_a_model_guess():
    from dataclasses import make_dataclass
    signals=build_source_concept_signal_drafts([source('Aster (UnlistedSeries)','12345678')])
    consumer=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)(signals,'input')
    adapted=adapt_production_semantics(consumer,build_semantic_vocabulary([]))
    assert adapted.signals[0].work_context_key=='UnlistedSeries'
    assert 'production_rejected_model_context' not in adapted.signals[0].evidence_payload
