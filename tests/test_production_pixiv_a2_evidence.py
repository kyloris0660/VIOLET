import copy
import pytest
from scripts.production_pixiv_a2_evidence import pytest_outcome,latency_statistics,recompute_quality


@pytest.mark.parametrize('log,code', [
    ('ERROR tests/test_a.py::test_a\n1 passed, 1 error',1),
    ('1 passed',2),('INTERNALERROR interrupted\n1 passed',3),
    ('1 passed, 1 failed',1),
])
def test_pytest_errors_nonzero_exit_and_unaccounted_nodes_fail(log,code):
    with pytest.raises(ValueError):pytest_outcome({'status':'finished','exit_code':code},log)


def test_named_historical_failure_is_accounted_without_demanding_exit_zero():
    counts,nodes=pytest_outcome({'status':'finished','exit_code':1},'FAILED tests/a.py::test_old\n4 passed, 1 failed, 2 skipped')
    assert nodes=={'tests/a.py::test_old'} and counts=={'passed':4,'failed':1,'skipped':2,'errors':0}


def test_xml_teardown_error_cannot_hide_behind_success_summary(tmp_path):
    path=tmp_path/'tests.xml'
    path.write_text('<testsuite><testcase name="test"><error message="teardown"/></testcase></testsuite>')
    with pytest.raises(ValueError,match='xml_log_count_mismatch'):
        pytest_outcome({'status':'finished','exit_code':0},'1 passed',path)


def test_latency_recomputed_from_each_measurement_and_rejects_nonfinite():
    stats=latency_statistics([{'ms':v} for v in range(100)])
    assert stats=={'p50_ms':49.5,'p95_ms':95,'max_ms':99}
    with pytest.raises(ValueError):latency_statistics([{'ms':float('nan')}])


def quality_fixture():
    return {'projection_rows':[['A','character',None,1,10,'work'],['B','character',None,1,11,'work']],
        'queries':{'"a"':{'ids':[10,11],'status_code':200},'"b"':{'ids':[10,11],'status_code':200}},
        'cases':[{'names':['a','b'],'expected':'must_link','category':'supported_multilingual_identity','passed':True}]},


def test_frozen_quality_is_recomputed_even_if_boolean_stays_true():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    assert recompute_quality(value,oracle)['failed_cases']==0
    for mutate in (lambda v:v['queries']['"b"'].update(ids=[11]),
                   lambda v:v['projection_rows'][1].__setitem__(3,2),
                   lambda v:v['cases'][0].update(expected='cannot_link')):
        changed=copy.deepcopy(value);mutate(changed)
        with pytest.raises(ValueError):recompute_quality(changed,oracle)


def test_suggestion_expected_set_cannot_be_rewritten_with_the_observed_result():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    sample={'media_id':20,'suggested_tag':'draft','accepted_control_tag':'kept'}
    case={'category':'suggestion_suggested_positive','kind':'suggested_positive','media_id':20,
        'query':'id:20 "draft"','expected_ids':[],'actual_ids':[],'total':0,'passed':True}
    value['cases'].append(case)
    assert recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})['failed_cases']==0
    case.update(expected_ids=[20],actual_ids=[20],total=1)
    with pytest.raises(ValueError,match='frozen_suggestion_expectation_changed'):
        recompute_quality(value,oracle,suggestion_oracle={'samples':[sample]})


def test_absent_former_identity_case_cannot_disappear_from_denominator():
    value=quality_fixture()[0];oracle={'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]}
    baseline=copy.deepcopy(value)
    value['cases']=[];value['projection_rows']=value['projection_rows'][:1]
    with pytest.raises(ValueError,match='quality_case_missing'):
        recompute_quality(value,oracle,baseline=baseline)
