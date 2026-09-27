import copy
import hashlib
import json
import pytest


@pytest.mark.parametrize('change',['split','missing','multiple'])
def test_each_alias_requires_the_same_unique_identity(change):
    from test_production_pixiv_precision_evidence import fixture
    from scripts.production_pixiv_precision_evidence import recompute_precision
    value,controls=fixture()
    assert recompute_precision(value,controls)['failed_cases']==0
    if change=='split':value['projection_rows'][1][3]=11
    elif change=='missing':value['projection_rows'].pop(1)
    else:
        row=copy.deepcopy(value['projection_rows'][1]);row[3]=11;value['projection_rows'].append(row)
    # Every query still has exactly the same correct Media result set.
    with pytest.raises(ValueError,match='a2_precision_'):
        recompute_precision(value,controls)


@pytest.mark.parametrize('elapsed',[751,3001])
def test_editable_low_timings_cannot_replace_live_performance(monkeypatch,elapsed):
    from scripts import production_pixiv_source_measurement as sampler
    from scripts.production_pixiv_a2_evidence import recompute_workload
    from test_production_pixiv_a2_evidence import workload_fixture,workload_launch_fixture
    value,baseline,cases=workload_fixture()
    for row in value['source_layer_measurements']:
        row.update(started_perf_ns=10,finished_perf_ns=11,ms=0.000001)
    assert recompute_workload(value,baseline,cases,launch=workload_launch_fixture())[0]['p95_ms']<1
    calls=[]
    def measure(session,case,repeat,**kw):
        calls.append((case['case_id'],repeat))
        return {'case_id':case['case_id'],'repeat':repeat,'ids':[],'ms':elapsed}
    monkeypatch.setattr(sampler,'measure_source_case',measure)
    with pytest.raises(ValueError,match='live_source_performance_failed'):
        sampler.verify_live_source_performance(None,cases,candidate='head',database='db',system_identifier='sys')
    assert len(calls)==3*len(cases)


def test_private_metadata_identity_is_hash_bound(tmp_path,monkeypatch):
    from scripts import production_pixiv_metadata_entrypoint as gate
    anchor=tmp_path/'docs/state';anchor.mkdir(parents=True)
    private=tmp_path/'.local_manifests/pixiv-a2';private.mkdir(parents=True)
    raw=b'{}';(private/'identity.json').write_bytes(raw)
    (anchor/'production-pixiv-a2-metadata-entrypoint.json').write_text(json.dumps({
        'private_identity_file':'identity.json','private_identity_sha256':hashlib.sha256(raw).hexdigest()}))
    monkeypatch.setattr(gate,'ROOT',tmp_path)
    (private/'identity.json').write_text('{"entrypoint":{"command":["other.exe"]}}')
    with pytest.raises(ValueError,match='private_identity_changed'):
        gate.verify_metadata_entrypoint({})


@pytest.mark.parametrize('path',['C:\\Users\\fixture\\python.exe','C:/Users/fixture/python.exe'])
def test_documentation_check_rejects_paths_in_other_a2_anchors(tmp_path,path):
    from scripts.check_documentation_state import check_documentation_state,DocumentationStateError
    directory=tmp_path/'docs/state';directory.mkdir(parents=True)
    (directory/'current-phase.json').write_text(json.dumps({'phase_id':'PRODUCTION-PIXIV-A2'}))
    (directory/'production-pixiv-a2-synthetic.json').write_text(json.dumps({'path':path}))
    with pytest.raises(DocumentationStateError,match='public_a2_anchor_redaction_failure'):
        check_documentation_state(root=tmp_path)
