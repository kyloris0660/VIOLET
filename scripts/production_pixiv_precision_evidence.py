"""Source-frozen identity precision, separate from the retained 80-case score."""
import json
from pathlib import Path


def load_precision_controls():
    return json.loads((Path(__file__).resolve().parents[1]/
        'docs/state/production-pixiv-a2-precision-controls.json').read_text(encoding='utf-8'))


def recompute_precision(quality, controls):
    from app.services.source_metadata_registry_service import canonical_source_key
    if controls.get('schema_version')!='violet.production-pixiv-precision-controls.v1':
        raise ValueError('a2_precision_controls_invalid')
    queries=quality['queries'];projection=quality['projection_rows']
    def ids(name):
        query=json.dumps(name,ensure_ascii=False)
        row=queries.get(query)
        if not row or row.get('status_code')!=200 or row.get('total')!=len(set(row['ids'])):
            raise ValueError('a2_precision_complete_query_required:'+query)
        return set(row['ids'])
    checked=[]
    for case in controls['identity_separations']:
        if not case.get('source_evidence_sha256'):
            raise ValueError('a2_precision_source_evidence_required')
        sides=case['sides']
        if len(sides)!=2 or any(not s['names'] or not s['exclusive_media_ids'] for s in sides):
            raise ValueError('a2_precision_exclusive_control_required')
        concepts=[]
        for index,side in enumerate(sides):
            opposite=set(sides[1-index]['exclusive_media_ids'])
            names={canonical_source_key(n) for n in side['names']}
            # Co-occurring different roles and literal ordinary tags remain
            # valid; only character/person identity support defines this set.
            current={r[3] for r in projection if canonical_source_key(r[0]) in names and r[1] in {'character','person'}}
            if not current:raise ValueError('a2_precision_identity_support_missing')
            concepts.append(current)
            for name in side['names']:
                actual=ids(name)
                if actual & opposite:raise ValueError('a2_precision_exclusive_false_hit:'+case['id']+':'+name)
                if not set(side['exclusive_media_ids'])<=actual:
                    raise ValueError('a2_precision_exclusive_recall_missing:'+case['id']+':'+name)
        if concepts[0]&concepts[1]:raise ValueError('a2_precision_distinct_identities_merged:'+case['id'])
        checked.append(case['id'])
    for case in controls.get('cooccurrence_controls',[]):
        left,right=case['names'];a,b=ids(left),ids(right);shared=set(case['media_ids'])
        if not case.get('source_evidence_sha256') or not shared<=a&b:
            raise ValueError('a2_precision_legitimate_cooccurrence_lost')
        qa,qb=(json.dumps(n,ensure_ascii=False) for n in (left,right))
        for query,expected in ((qa+' '+qb,a&b),(qa+' -'+qb,a-b),(qb+' -'+qa,b-a)):
            row=queries.get(query)
            if (not row or row.get('status_code')!=200 or set(row['ids'])!=expected
                or row.get('total')!=len(expected)):
                raise ValueError('a2_precision_media_set_semantics_changed')
        checked.append(case['id'])
    if not checked:raise ValueError('a2_precision_controls_empty')
    return {'case_count':len(checked),'case_ids':checked,'failed_cases':0,
        'retained_quality_denominator_changed':False}
