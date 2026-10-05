from copy import deepcopy
from dataclasses import asdict

import pytest

from app.services.pixiv_metadata_projection_service import canonical_fingerprint
from app.services.production_pixiv_role_recovery import (
    derive_current_role_view, current_role_facts, historical_role_facts, adopt_role_coverage_answers,
)
from test_production_pixiv_role_sources import paid_original, admit


def inventory_for(facts, sources):
    return {'records': {key: {'original_record_fingerprint': canonical_fingerprint(row),
        'direct_sources': [{**s, 'group': asdict(s['group'])} for s in sources.get(key, [])]}
        for key, row in facts['records'].items()}}


def test_archive_view_preserves_denominator_and_blocks_unavailable_role_inputs(tmp_path):
    budget, saved, groups = paid_original(tmp_path)
    source = admit(budget, saved, groups)['sources'][0]
    good = {**source['answer'], 'extraction_key': 'good', 'raw_value': groups[0].tags[0]['raw_tag'],
        'input_fingerprint': source['unit_source_proof']['question_fingerprint']}
    unavailable = {**deepcopy(good), 'extraction_key': 'unavailable'}
    facts = {'schema_version': 'violet.production-pixiv-role-result.v1', 'records': {'good': good, 'unavailable': unavailable}}
    original = deepcopy(facts)
    recovered, report = derive_current_role_view(facts, inventory_for(facts, {'good': [source]}))
    assert facts == original and recovered['records'] == original['records']
    assert report['counts'] == {'current_adopted': 1, 'superseded_by_valid_answer': 0, 'archive_only_unavailable': 1}
    assert current_role_facts(recovered)['records'] == {'good': good}
    assert recovered['source_recovery']['fixed_record_denominator'] == 2
    assert recovered['source_recovery']['records']['unavailable']['currently_needed_original_input']
    assert not report['new_provider_calls']


@pytest.mark.parametrize('mutation', ['historical_row', 'current_projection', 'drop_record', 'invent_projection'])
def test_role_current_view_rejects_changed_history_or_projection(tmp_path, mutation):
    budget, saved, groups = paid_original(tmp_path)
    source = admit(budget, saved, groups)['sources'][0]
    row = {**source['answer'], 'extraction_key': 'good', 'raw_value': groups[0].tags[0]['raw_tag'],
        'input_fingerprint': source['unit_source_proof']['question_fingerprint']}
    facts = {'records': {'good': row}}
    recovered, _ = derive_current_role_view(facts, inventory_for(facts, {'good': [source]}))
    if mutation == 'historical_row': recovered['records']['good']['verdict'] = 'no_explicit_name'
    if mutation == 'current_projection': recovered['current_record_projections']['good'] = {**row, 'verdict': 'no_explicit_name'}
    if mutation == 'drop_record': recovered['source_recovery']['records'].pop('good')
    if mutation == 'invent_projection': recovered['current_record_projections']['invented'] = row
    with pytest.raises(ValueError, match='role_current_source_view_'):
        current_role_facts(recovered)


def test_missing_source_cannot_complete_original_targets_or_hide_them_from_repair_plan(tmp_path):
    from test_production_pixiv_role_coverage import partial_facts
    from app.services.production_pixiv_role_extraction import plan_role_coverage_repair,summarize_role_response_coverage
    consumer,vocab,facts,provider,budget=partial_facts(tmp_path)
    kinds=('records','context_records','completion_records','coverage_repair_records','correction_records')
    inventory={'records':{key:{'original_record_fingerprint':canonical_fingerprint(row),'direct_sources':[]}
        for kind in kinds for key,row in facts.get(kind,{}).items()}}
    recovered,_=derive_current_role_view(facts,inventory)
    before=budget.path.read_bytes();units,mapping,_=plan_role_coverage_repair(consumer,vocab,recovered)
    assert {raw for unit in units for raw in unit.raw_values}=={'MysteryKnown','MysteryMissing','MysteryUnknown'}
    coverage=summarize_role_response_coverage(consumer,vocab,recovered,require_complete=True)
    assert coverage['original_response_aggregate_count']==len(facts['completion_by_aggregate'])
    assert coverage['counts']['unaccounted']==3 and not coverage['fully_accounted_aggregates']
    assert budget.path.read_bytes()==before and len(provider.calls)==1


def test_archived_global_answer_cannot_supply_reused_target_completeness(tmp_path):
    budget,saved,groups=paid_original(tmp_path)
    source=admit(budget,saved,groups)['sources'][0]
    row={**source['answer'],'extraction_key':'old','raw_value':groups[0].tags[0]['raw_tag'],
        'input_fingerprint':source['unit_source_proof']['question_fingerprint']}
    facts={'records':{'old':row},'role_reused_target_answers':{'aggregate':{'name':{'extraction_key':'old'}}}}
    recovered,_=derive_current_role_view(facts,inventory_for(facts,{}))
    assert current_role_facts(recovered)['role_reused_target_answers']=={'aggregate':{}}
    assert recovered['role_reused_target_answers']==facts['role_reused_target_answers']


@pytest.mark.parametrize('change',['none','changed_question','missing_attempt'])
def test_lifetime_reconstruction_counts_original_call_without_rewriting_legacy_logical_keys(tmp_path,change):
    from app.services.production_pixiv_role_recovery import original_role_attempt_index
    from app.services.production_pixiv_role_sources import role_logical_keys
    from copy import deepcopy
    budget,saved,groups=paid_original(tmp_path)
    before=budget.path.read_bytes();ledger=__import__('json').loads(before)
    ticket=ledger['calls'][0]['id'];ledger['calls'][0].pop('logical_keys')
    question={'request_groups':[asdict(g) for g in groups],'attempt_ids':[ticket]}
    if change=='changed_question':question['request_groups'][0]['tags'][0]['raw_tag']='DifferentName'
    if change=='missing_attempt':question['attempt_ids']=[]
    requests={saved['input_fingerprint']:question}
    if change=='none':
        index=original_role_attempt_index(ledger,requests)
        assert index[role_logical_keys(groups)[0]][0]['id']==ticket
        assert 'logical_keys' not in ledger['calls'][0]
    else:
        with pytest.raises(ValueError,match='role_attempt_original_'):original_role_attempt_index(ledger,requests)
    assert budget.path.read_bytes()==before


def supplemental_fixture(tmp_path):
    budget, saved, groups = paid_original(tmp_path)
    source = admit(budget, saved, groups)['sources'][0]
    original = {**source['answer'], 'extraction_key': 'original',
        'raw_value': groups[0].tags[0]['raw_tag'],
        'input_fingerprint': source['unit_source_proof']['question_fingerprint']}
    facts = {'records': {'original': original}, 'coverage_repair_by_aggregate': {}}
    recovered, _ = derive_current_role_view(facts, inventory_for(facts, {'original': [source]}))
    answer = {**deepcopy(original), 'extraction_key': 'new-answer',
        'unit_source_proofs': [source['unit_source_proof']]}
    return recovered, answer, source


def test_new_paid_answer_appends_without_changing_original_rows_mappings_or_denominator(tmp_path):
    facts, answer, source = supplemental_fixture(tmp_path)
    before = deepcopy(facts)
    result = adopt_role_coverage_answers(facts, {'new-answer': answer}, {'aggregate': 'new-answer'})
    assert facts == before and result['records'] == before['records']
    assert result['coverage_repair_by_aggregate'] == before['coverage_repair_by_aggregate']
    assert result['source_recovery']['fixed_record_denominator'] == 1
    assert historical_role_facts(result)['coverage_repair_records']['new-answer'] == answer
    assert current_role_facts(result)['coverage_repair_by_aggregate'] == {'aggregate': 'new-answer'}
    # Repeating an actual saved answer does not invent a second historical row.
    assert adopt_role_coverage_answers(result, {'new-answer': answer}, {'aggregate': 'new-answer'}) == result


@pytest.mark.parametrize('change', ['no_proof', 'other_kind_collision', 'denominator', 'unadmitted_mapping'])
def test_supplemental_view_rejects_unproved_answers_or_original_accounting_changes(tmp_path, change):
    facts, answer, source = supplemental_fixture(tmp_path)
    if change == 'no_proof':
        answer.pop('unit_source_proofs')
        with pytest.raises(ValueError, match='source_proof_missing'):
            adopt_role_coverage_answers(facts, {'new-answer': answer}, {'aggregate': 'new-answer'})
    elif change == 'other_kind_collision':
        answer['extraction_key'] = 'original'
        with pytest.raises(ValueError, match='record_collision'):
            adopt_role_coverage_answers(facts, {'original': answer}, {'aggregate': 'original'})
    else:
        result = adopt_role_coverage_answers(facts, {'new-answer': answer}, {'aggregate': 'new-answer'})
        if change == 'denominator': result['source_recovery']['fixed_record_denominator'] += 1
        else: result['current_record_mappings']['coverage_repair_by_aggregate']['aggregate'] = 'unadmitted'
        with pytest.raises(ValueError, match='role_current_'):
            current_role_facts(result)
