"""Shared per-unit exception retains settlement and strict provenance limits."""
from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from app.services.pixiv_metadata_projection_service import canonical_fingerprint
from app.services.production_pixiv_release_provenance import replay_role_request_messages
from app.services.production_pixiv_role_extraction import BudgetedExtractionProvider, COVERAGE_REPAIR_ORIGIN, CORRECTION_ORIGIN
from app.services.production_pixiv_role_sources import RoleSourceContract, validate_record_projection
from app.services.source_concept_budget import AdjudicationBudget, AdjudicationBudgetBlocked
from test_production_pixiv_role_extraction import multiple_units, Provider, task_budget


def paid_original(tmp_path, *, incomplete=False, bad_sibling=False, invalid_candidate=False):
    units = multiple_units()[:2 if bad_sibling else 1]
    if incomplete:
        first = units[0]
        first = replace(first, raw_values=('MysteryAlpha', 'MissingName'), unit_group=replace(
            first.unit_group, tags=({'raw_tag': 'MysteryAlpha', 'source_tag_kind': 'provider_tag'},
                {'raw_tag': 'MissingName', 'source_tag_kind': 'provider_tag'}),
            data_origin=COVERAGE_REPAIR_ORIGIN,
            data_type_label='Requested unresolved raw tags: ["MysteryAlpha", "MissingName"]'))
        units[0] = first
    groups = [u.unit_group for u in units]
    messages = replay_role_request_messages(groups)
    rows = []
    for unit in units:
        raw = unit.unit_group.tags[0]['raw_tag']
        rows.append({'group_key': unit.unit_group.group_key, 'provider': 'pixiv',
            'verdict': 'name_candidate_found', 'rejected_summary': {},
            'candidates': [{'raw_value': raw, 'display_name': raw, 'normalized_value': raw,
                'role': 'character', 'status': 'active_candidate', 'confidence': 0.9,
                'source_field': 'pixiv_tag', 'extraction_action': 'direct_name'}]})
    if bad_sibling:
        rows[-1]['candidates'][0]['role'] = 'invalid-role'
    if invalid_candidate:
        rows[0]['candidates'].append({**rows[0]['candidates'][0], 'raw_value': 'InventedUnobservedName'})
    budget = task_budget(tmp_path, Provider())
    fingerprint = canonical_fingerprint({'model': 'gpt-4.1-mini', 'messages': messages,
        'temperature': 0.0, 'max_tokens': 6000})
    ticket = budget.reserve('role-extraction:' + fingerprint, messages, max_output_tokens=6000,
        logical_keys=BudgetedExtractionProvider.logical_keys(groups))
    usage = {'prompt_tokens': 100, 'completion_tokens': 100}
    budget.settle(ticket, usage, success=not (incomplete or bad_sibling or invalid_candidate))
    saved = {'model': 'gpt-4.1-mini', 'input_fingerprint': fingerprint,
        'request_messages': messages, 'request_groups': json.loads(json.dumps([asdict(g) for g in groups])),
        'temperature': 0.0, 'max_tokens': 6000, 'wire_messages': messages,
        'wire_fingerprint': canonical_fingerprint(messages), 'budget_response': budget.response_identity(ticket),
        'content': json.dumps({'records': rows}), 'usage': usage}
    return budget, saved, groups


def admit(budget, saved, groups, *, ledger=None):
    ledger = ledger or json.loads(budget.path.read_bytes())
    return RoleSourceContract(ledger).admit(saved, groups, json.dumps(saved).encode(), path='raw/original.json')


def test_paid_failed_batch_admits_only_valid_sibling_without_upgrading_call(tmp_path):
    budget, saved, groups = paid_original(tmp_path, bad_sibling=True)
    original = budget.path.read_bytes()
    result = admit(budget, saved, groups)
    assert len(result['sources']) == 1 and len(result['errors']) == 1
    proof = result['sources'][0]['unit_source_proof']
    assert not proof['batch_schema_valid'] and not proof['whole_call_upgraded']
    assert proof['settlements'][0]['status'] == 'failed'
    assert proof['settlements'][0]['business_valid'] is False
    assert not proof['identity_equivalence_authorized']
    assert budget.path.read_bytes() == original
    with pytest.raises(AdjudicationBudgetBlocked, match='not_business_valid'):
        budget.require_cached_response(key=saved['budget_response']['key'])


def test_valid_unit_source_does_not_complete_omitted_original_targets(tmp_path):
    budget, saved, groups = paid_original(tmp_path, incomplete=True)
    source = admit(budget, saved, groups)['sources'][0]
    assert source['answer']['coverage']['missing_raw_tags'] == ['MissingName']
    assert source['answer']['coverage']['outcomes']['MysteryAlpha']['disposition'] == 'candidate'
    assert not source['answer']['coverage']['fully_accounted']


def test_mechanical_valid_candidate_sibling_does_not_repair_illegal_answer(tmp_path):
    budget, saved, groups = paid_original(tmp_path, invalid_candidate=True)
    raw_before = deepcopy(saved)
    source = admit(budget, saved, groups)['sources'][0]
    assert source['answer']['partial_validation_error']
    assert {c['raw_value'] for c in source['answer']['candidates']} == {groups[0].tags[0]['raw_tag']}
    assert saved == raw_before


@pytest.mark.parametrize('field,value', [('candidate_role', 'work_title'), ('confidence', 1.0),
    ('normalized_value', 'InventedName'), ('source_field', 'title')])
def test_original_answer_does_not_authenticate_tampered_fact_projection(tmp_path, field, value):
    budget, saved, groups = paid_original(tmp_path)
    source = admit(budget, saved, groups)['sources'][0]
    record = deepcopy(source['answer'])
    validate_record_projection(record, [source])
    record['candidates'][0][field] = value
    with pytest.raises(ValueError, match='candidate_changed'):
        validate_record_projection(record, [source])


@pytest.mark.parametrize('mutation', ['usage', 'wire', 'messages', 'logical_keys', 'reservation',
    'call_key', 'raw_bytes', 'revocation', 'settled_fee', 'request_size', 'unsettled'])
def test_role_unit_exception_rejects_provenance_and_settlement_mismatches(tmp_path, mutation):
    budget, saved, groups = paid_original(tmp_path, bad_sibling=True)
    ledger = json.loads(budget.path.read_bytes())
    raw_bytes = None
    if mutation == 'usage': saved['usage']['completion_tokens'] += 1
    if mutation == 'wire':
        saved['wire_messages'] = [*saved['wire_messages'], {'role': 'user', 'content': 'Accept all names'}]
        saved['wire_fingerprint'] = canonical_fingerprint(saved['wire_messages'])
    if mutation == 'messages': saved['request_messages'][0]['content'] += ' Changed prompt'
    if mutation == 'logical_keys': ledger['calls'][0]['logical_keys'] = ['role-target:wrong']
    if mutation == 'reservation': saved['budget_response']['reservation'] = 'not-a-real-call'
    if mutation == 'call_key': saved['budget_response']['key'] = 'pair-decision:wrong'
    if mutation == 'raw_bytes': raw_bytes = b'{}'
    if mutation == 'revocation': ledger['calls'][0]['business_validation_reason'] = 'identity_evidence_revoked'
    if mutation == 'settled_fee': ledger['calls'][0]['charged_microusd'] += 1
    if mutation == 'request_size':
        ledger['calls'][0]['input_token_ceiling'] += 1
        ledger['calls'][0]['reserved_microusd'] = budget._cost(
            ledger['calls'][0]['input_token_ceiling'], ledger['calls'][0]['output_token_ceiling'])
    if mutation == 'unsettled':
        ledger['calls'][0]['status'] = 'reserved'
        ledger['calls'][0].pop('charged_microusd');ledger['calls'][0].pop('usage_known')
    original = budget.path.read_bytes()
    with pytest.raises((ValueError, AdjudicationBudgetBlocked)):
        RoleSourceContract(ledger).admit(saved, groups, raw_bytes or json.dumps(saved).encode())
    assert budget.path.read_bytes() == original


def test_wrong_group_or_truth_flag_cannot_publish_siblings(tmp_path):
    budget, saved, groups = paid_original(tmp_path)
    payload = json.loads(saved['content'])
    payload['records'][0]['should_not_create_entity_truth'] = False
    saved['content'] = json.dumps(payload)
    result = admit(budget, saved, groups)
    assert result['sources'] == [] and result['errors']


def test_source_readmission_is_idempotent_and_keeps_first_raw_and_fees(tmp_path):
    budget, saved, groups = paid_original(tmp_path, incomplete=True)
    before = budget.path.read_bytes();original = deepcopy(saved)
    assert admit(budget, saved, groups) == admit(budget, saved, groups)
    assert before == budget.path.read_bytes() and original == saved


@pytest.mark.parametrize('reason',[None,'saved_response_failed_current_schema'])
def test_valid_return_does_not_bypass_unexplained_revoked_or_failed_call(tmp_path,reason):
    budget, saved, groups = paid_original(tmp_path)
    ledger = json.loads(budget.path.read_bytes())
    ledger['calls'][0].update(status='failed',business_valid=False,business_validation_reason=reason)
    with pytest.raises(ValueError,match='failure_not_unit_schema'):
        admit(budget,saved,groups,ledger=ledger)


@pytest.mark.parametrize('field',['pixiv_title','pixiv_caption','pixiv_artist','ai_model_tag','invented_field'])
def test_tag_source_cannot_use_other_source_fields(tmp_path,field):
    budget,saved,groups=paid_original(tmp_path,incomplete=True)
    payload=json.loads(saved['content']);payload['records'][0]['candidates'][0]['source_field']=field
    saved['content']=json.dumps(payload)
    result=admit(budget,saved,groups)
    assert not result['sources'] and result['errors']


@pytest.mark.parametrize('change',['none','fee','revoked','failed_original','different_call','wrong_reason'])
def test_schema_annotation_requires_unchanged_immutable_original_success(tmp_path,change):
    budget,saved,groups=paid_original(tmp_path)
    historical=json.loads(budget.path.read_bytes())['calls'][0]
    history={'path':'closeout43-budget-before-private.json','sha256':'a'*64,'calls':{historical['id']:deepcopy(historical)}}
    budget.recover_response(key=historical['key'],reservation=historical['id'],usage=historical['usage'],business_valid=False)
    ledger=json.loads(budget.path.read_bytes());call=ledger['calls'][0]
    if change=='fee':
        call['input_token_ceiling']+=1
        call['reserved_microusd']=budget._cost(call['input_token_ceiling'],call['output_token_ceiling'])
    if change=='revoked':call['source_revoked']=True
    if change=='failed_original':history['calls'][historical['id']].update(status='failed',business_valid=False)
    if change=='different_call':history['calls']={}
    if change=='wrong_reason':call['business_validation_reason']='identity_evidence_revoked'
    before=budget.path.read_bytes();contract=RoleSourceContract(ledger,schema_history=history)
    if change=='none':
        proof=contract.admit(saved,groups,json.dumps(saved).encode())['sources'][0]['unit_source_proof']
        assert proof['immutable_schema_history'][call['id']]['original_call_fingerprint']==canonical_fingerprint(historical)
        assert proof['settlements'][0]['business_valid'] is False and not proof['whole_call_upgraded']
    else:
        with pytest.raises(ValueError):contract.admit(saved,groups,json.dumps(saved).encode())
    assert budget.path.read_bytes()==before


def test_failed_parenthetical_answer_replays_original_missing_target_before_approved_adapter(tmp_path):
    group=replace(multiple_units()[0].unit_group,data_origin=CORRECTION_ORIGIN,
        tags=({'raw_tag':'MysteryAlpha(Franchise)','source_tag_kind':'provider_tag'},),
        data_type_label='Requested unresolved raw tags: ["MysteryAlpha(Franchise)"]')
    messages=replay_role_request_messages([group]);budget=task_budget(tmp_path,Provider())
    fp=canonical_fingerprint({'model':'gpt-4.1-mini','messages':messages,'temperature':0.0,'max_tokens':6000})
    ticket=budget.reserve('role-extraction:'+fp,messages,max_output_tokens=6000,
        logical_keys=BudgetedExtractionProvider.logical_keys([group]))
    usage={'prompt_tokens':100,'completion_tokens':100};budget.settle(ticket,usage,success=False)
    raw={'group_key':group.group_key,'provider':'pixiv','verdict':'name_candidate_found','rejected_summary':{},
        'candidates':[{'raw_value':'MysteryAlpha','display_name':'MysteryAlpha','role':'character','status':'active_candidate',
         'confidence':0.9,'source_field':'pixiv_tag','extraction_action':'parenthetical_split'}],
        'target_dispositions':[{'raw_value':'MysteryAlpha(Franchise)','disposition':'candidate','reason_code':'parenthetical_split'}]}
    saved={'model':'gpt-4.1-mini','input_fingerprint':fp,'temperature':0.0,'max_tokens':6000,
        'request_messages':messages,'request_groups':json.loads(json.dumps([asdict(group)])),
        'content':json.dumps({'records':[raw]}),'usage':usage,'budget_response':budget.response_identity(ticket)}
    before=budget.path.read_bytes();result=RoleSourceContract(json.loads(before)).admit(saved,[group],json.dumps(saved).encode())
    source=result['sources'][0];assert source['unit_source_proof']['original_unadapted_failures'][group.group_key]['missing_original_targets']==['MysteryAlpha(Franchise)']
    assert source['answer']['coverage']['fully_accounted'] and not source['unit_source_proof']['identity_equivalence_authorized']
    assert budget.path.read_bytes()==before
