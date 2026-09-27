"""Counterexamples from the four actual post-push review threads."""
import copy,hashlib,json
import pytest


def test_live_identity_projection_rejects_attachment_invented_shared_concept():
    from scripts.production_pixiv_a2_evidence import recompute_quality
    from test_production_pixiv_a2_evidence import quality_fixture
    quality=quality_fixture()[0];actual=copy.deepcopy(quality['projection_rows']);actual[1][3]=2
    with pytest.raises(ValueError,match='identity_projection'):
        recompute_quality(quality,{'identity_pairs':[{'names':['a','b'],'expected':'must_link'}]},
            identity_projection=actual)


@pytest.mark.parametrize('defect',['missing','changed','case_digest','identity','outside','missing_media'])
def test_precision_source_requires_actual_bounded_file_digest_and_identity(tmp_path,defect):
    from scripts.production_pixiv_precision_evidence import verify_precision_sources
    from test_production_pixiv_precision_evidence import fixture
    controls=fixture()[1]
    source={'identity':{'current_database':'prod','system_identifier':'system'},
        'metadata':[{'media_id':i,'provider':'pixiv','status':'metadata_complete'} for i in (1,2,3)]}
    path=tmp_path/'source.json';path.write_text(json.dumps(source),encoding='utf-8')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    controls.update(independent_source='source.json',source_evidence_sha256=digest)
    for case in controls['identity_separations']+controls['cooccurrence_controls']:case['source_evidence_sha256']=digest
    assert verify_precision_sources(controls,tmp_path,database='prod',system_identifier='system')['source_media_count']==3
    if defect=='missing':path.unlink()
    elif defect=='changed':path.write_text('{}')
    elif defect=='case_digest':controls['identity_separations'][0]['source_evidence_sha256']='fake'
    elif defect=='outside':controls['independent_source']='../outside.json'
    else:
        if defect=='identity':source['identity']['current_database']='other'
        else:source['metadata']=source['metadata'][:1]
        path.write_text(json.dumps(source),encoding='utf-8')
        digest=hashlib.sha256(path.read_bytes()).hexdigest();controls['source_evidence_sha256']=digest
        for case in controls['identity_separations']+controls['cooccurrence_controls']:case['source_evidence_sha256']=digest
    with pytest.raises(ValueError,match='precision_source'):
        verify_precision_sources(controls,tmp_path,database='prod',system_identifier='system')


@pytest.mark.parametrize('fallback,production,expected',[(True,False,{7}),(False,True,{9}),(True,True,{7,9})])
def test_production_alias_and_overlay_flags_are_independent(monkeypatch,fallback,production,expected):
    from app.services import source_concept_search_service as search
    monkeypatch.setattr(search,'_search_keys_for_term',lambda term:{'name'})
    monkeypatch.setattr(search,'_query_search_index_concept_ids',lambda *a,**k:{1})
    monkeypatch.setattr(search,'_source_concept_media_condition',lambda *a,**k:None)
    monkeypatch.setattr(search,'_overlay_fallback_media_ids',lambda *a:{7})
    monkeypatch.setattr(search,'_production_alias_direct_evidence_media_ids',lambda *a:{9})
    result=search.source_concept_media_condition_for_term(None,'Name',include_evidence_fallback=fallback,
        include_production_alias_evidence=production)
    assert set(result.right.value)==expected
