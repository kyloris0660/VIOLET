import copy
import json
import pytest
from scripts.production_pixiv_precision_evidence import recompute_precision


def fixture():
    controls={'schema_version':'violet.production-pixiv-precision-controls.v1',
        'identity_separations':[{'id':'same-world-distinct-characters','source_evidence_sha256':'source-sha',
            'sides':[{'names':['Alpha','AlphaAlias'],'exclusive_media_ids':[1]},
                {'names':['Beta'],'exclusive_media_ids':[2]}]}],
        'cooccurrence_controls':[{'id':'two-characters','names':['Alpha','Beta'],
            'media_ids':[3],'source_evidence_sha256':'source-sha'}]}
    queries={}
    for q,ids in [('\"Alpha\"',[1,3]),('\"AlphaAlias\"',[1,3]),('\"Beta\"',[2,3]),
        ('\"Alpha\" \"Beta\"',[3]),('\"Alpha\" -\"Beta\"',[1]),('\"Beta\" -\"Alpha\"',[2])]:
        queries[q]={'status_code':200,'total':len(ids),'ids':ids}
    rows=[['Alpha','character','World',10,1,'w1'],['AlphaAlias','character','World',10,3,'w3'],
        ['Beta','character','World',20,2,'w2'],['Beta','character','World',20,3,'w3']]
    return {'queries':queries,'projection_rows':rows},controls


def test_legitimate_aliases_and_multicharacter_cooccurrence_are_retained():
    quality,controls=fixture()
    assert recompute_precision(quality,controls)['case_count']==2


@pytest.mark.parametrize('defect',['exclusive_false_hit','merged','lost_recall','lost_cooccurrence','negative','missing_page'])
def test_precision_failures_cannot_hide_behind_correct_finite_score(defect):
    quality,controls=fixture();quality['failed_cases']=0
    if defect=='exclusive_false_hit':quality['queries']['"Alpha"'].update(ids=[1,2,3],total=3)
    elif defect=='merged':
        for row in quality['projection_rows']:row[3]=10
    elif defect=='lost_recall':quality['queries']['"AlphaAlias"'].update(ids=[3],total=1)
    elif defect=='lost_cooccurrence':quality['queries']['"Beta"'].update(ids=[2],total=1)
    elif defect=='negative':quality['queries']['"Alpha" -"Beta"'].update(ids=[1,3],total=2)
    else:quality['queries']['"Alpha"']['total']=3
    with pytest.raises(ValueError,match='a2_precision_'):
        recompute_precision(quality,controls)
