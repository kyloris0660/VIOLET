from dataclasses import replace
from types import SimpleNamespace

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
