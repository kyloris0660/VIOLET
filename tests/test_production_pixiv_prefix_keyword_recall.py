"""Native F7a observations retain keyword support without acquiring identity."""
import copy
from dataclasses import asdict,make_dataclass,replace
import pytest
from app.services.production_pixiv_semantics import adapt_production_semantics,build_semantic_vocabulary
from app.services.source_name_candidate_extraction_service import SourceCandidateInputGroup,validate_extraction_record
from app.services.source_concept_resolver_service import SourceConceptSignalInput,build_source_concept_signal_drafts,resolve_source_concepts

Consumer=make_dataclass('Consumer',['signals','input_fingerprint'],frozen=True)

def setup_case(*,known_work=True):
    group=SourceCandidateInputGroup(group_key='fixture-context',provider='pixiv',
        tags=({'raw_tag':'FixtureWork','source_tag_kind':'provider_tag'},
              {'raw_tag':'FixtureWork1000users入り','source_tag_kind':'provider_tag'},
              {'raw_tag':'FixtureHero','source_tag_kind':'provider_tag'}),
        data_origin='production_metadata_contextual_supplement',source_work_id_present=True)
    answer={'group_key':group.group_key,'provider':'pixiv','verdict':'name_candidate_found',
        'rejected_summary':{},'candidates':[{'raw_value':'FixtureHero','display_name':'FixtureHero',
        'normalized_value':'FixtureHero','role':'character','status':'active_candidate',
        'confidence':0.9,'source_field':'pixiv_tag','extraction_action':'direct_name'}]}
    verdict,rows,*_=validate_extraction_record(answer,group)
    candidates=[asdict(row) for row in rows]
    assert any(row['candidate_role']=='unknown_name_like' and row['extraction_action']=='popularity_suffix_stripped' for row in candidates)
    def signal(raw,work,aggregate,role='unknown',status='needs_review',trust='weak'):
        return SourceConceptSignalInput(origin_type='pixiv_tag_observation',origin_key=work+':'+raw,provider='pixiv',
            raw_value=raw,display_value=raw,canonical_value=raw,confidence=None,role_hint=role,
            source_kind='provider_tag',work_context_key='pixiv:'+work+':0',trust_tier=trust,status=status,
            evidence_payload={'work_id':work,'page_index':0,'aggregate_fingerprint':aggregate})
    inputs=[signal(raw,'910000001','actual-group') for raw in ('FixtureWork','FixtureWork1000users入り','FixtureHero')]
    if known_work:inputs.append(signal('FixtureWork','910000002','other-group','work','active','medium'))
    consumer=Consumer(build_source_concept_signal_drafts(inputs),'original-input')
    first={'extraction_key':'original-first','raw_value':'FixtureWork','verdict':'no_explicit_name','candidates':[]}
    context={'extraction_key':'original-context','raw_value':'context','verdict':verdict.extraction_verdict,'candidates':candidates}
    facts={'schema_version':'violet.production-pixiv-role-result.v1','records':{'first':first},
        'context_records':{'context':context},'context_by_aggregate':{'actual-group':'context'}}
    return consumer,build_semantic_vocabulary([]),facts

def observation(consumer):return next(s for s in consumer.signals if s.raw_value=='FixtureWork' and s.evidence_payload['work_id']=='910000001')

def test_native_prefix_preserves_first_non_name_and_stays_guarded_unknown():
    consumer,vocab,facts=setup_case();saved=copy.deepcopy(facts)
    current=adapt_production_semantics(consumer,vocab,facts);unknown=observation(current)
    assert (unknown.role_hint,unknown.trust_tier,unknown.status)==('unknown','weak','needs_review')
    assert unknown.work_context_key is None
    assert unknown.evidence_payload['production_role_extraction']['verdict']=='no_explicit_name'
    assert unknown.evidence_payload['production_contextual_unknown_name_observation']['identity_equivalence_authorized'] is False
    typed=next(s for s in current.signals if s.raw_value=='FixtureWork' and s.role_hint=='work')
    resolved=resolve_source_concepts(current.signals,run_id='prefix-unknown-identity-guard',llm_judgments=[{
        'left_signal_key':unknown.signal_key,'right_signal_key':typed.signal_key,'decision':'must_link','confidence':0.99}])
    assert not any({unknown.signal_key,typed.signal_key}<={s.signal_key for s in concept.signals} for concept in resolved.concepts)
    assert facts==saved

@pytest.mark.parametrize('change',['no_known_work','missing_observed_tag','wrong_aggregate','ordinary_unknown','invented_prefix','nondeterministic','full_tag_alias','rejected_candidate'])
def test_invalid_or_generic_unknown_observations_do_not_revive_non_names(change):
    consumer,vocab,facts=setup_case(known_work=change!='no_known_work')
    if change=='missing_observed_tag':consumer=replace(consumer,signals=tuple(s for s in consumer.signals if s.raw_value!='FixtureWork1000users入り'))
    if change=='wrong_aggregate':consumer=replace(consumer,signals=tuple(replace(s,evidence_payload={**s.evidence_payload,'aggregate_fingerprint':'wrong-group'}) if s.raw_value=='FixtureWork1000users入り' else s for s in consumer.signals))
    candidate=next(row for row in facts['context_records']['context']['candidates'] if row['extraction_action']=='popularity_suffix_stripped')
    if change=='ordinary_unknown':candidate['extraction_action']='normal_tag_candidate'
    elif change=='invented_prefix':candidate['evidence_payload']['extracted_prefix']='UnobservedName'
    elif change=='nondeterministic':candidate['evidence_payload']['deterministic_hint']=False
    elif change=='full_tag_alias':candidate['evidence_payload']['full_popularity_tag_is_alias']=True
    elif change=='rejected_candidate':candidate['candidate_status']='rejected'
    current=adapt_production_semantics(consumer,vocab,facts);result=observation(current)
    assert (result.role_hint,result.status,result.trust_tier)==('unknown','rejected','rejected')
    assert 'production_contextual_unknown_name_observation' not in result.evidence_payload

def test_accepted_general_vocabulary_keeps_its_original_rejection():
    consumer,_,facts=setup_case();vocab=build_semantic_vocabulary([{
        'canonical_name':'FixtureWork','display_name':'FixtureWork','category':'general','source':'static'}])
    current=adapt_production_semantics(consumer,vocab,facts);result=observation(current)
    assert result.status==result.trust_tier=='rejected'
    assert 'production_contextual_unknown_name_observation' not in result.evidence_payload
