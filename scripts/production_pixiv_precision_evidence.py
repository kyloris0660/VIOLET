"""Source-frozen identity precision, separate from the retained 80-case score."""
import json
from pathlib import Path


def load_precision_controls():
    return json.loads((Path(__file__).resolve().parents[1]/
        'docs/state/production-pixiv-a2-precision-controls.json').read_text(encoding='utf-8'))


def verify_precision_sources(controls, private_root, *, database, system_identifier):
    import hashlib
    root=Path(private_root).resolve(strict=True)
    try:
        path=(root/controls['independent_source']).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('a2_precision_source_outside_root')
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        source=json.loads(raw)
    except (OSError,KeyError,TypeError,ValueError) as exc:
        raise ValueError('a2_precision_source_unavailable') from exc
    cases=[*controls['identity_separations'],*controls.get('cooccurrence_controls',[])]
    if (digest!=controls.get('source_evidence_sha256')
        or any(c.get('source_evidence_sha256')!=digest for c in cases)
        or source.get('identity')!={'current_database':database,'system_identifier':system_identifier}):
        raise ValueError('a2_precision_source_digest_or_identity_changed')
    def complete_original(row):
        raw=row.get('raw_metadata_json')
        # Historical local Pixiv observations can retain the complete original
        # response before A2 normalization. Verify that response's own work/page
        # identity; do not confuse its DB workflow status with source content.
        return (row.get('provider')=='pixiv' and isinstance(raw,dict)
            and str(raw.get('id'))==row.get('source_work_id')
            and type(raw.get('page_count')) is int and raw['page_count']>0
            and type(row.get('source_page_index')) is int
            and 0<=row['source_page_index']<raw['page_count']
            and isinstance(raw.get('tags'),list) and bool(raw['tags'])
            and isinstance(raw.get('user'),dict) and bool(raw['user'].get('id'))
            and isinstance(raw.get('title'),str) and bool(raw['title']))
    observed={r['media_id'] for r in source.get('metadata',[]) if complete_original(r)}
    required={mid for c in controls['identity_separations'] for s in c['sides'] for mid in s['exclusive_media_ids']}
    required.update(mid for c in controls.get('cooccurrence_controls',[]) for mid in c['media_ids'])
    if not required or not required<=observed:
        raise ValueError('a2_precision_source_media_missing')
    from app.services.source_metadata_registry_service import canonical_source_key
    tags_by_media={}
    for row in source.get('metadata',[]):
        if not complete_original(row):continue
        names=set()
        for tag in row['raw_metadata_json']['tags']:
            if isinstance(tag,str):names.add(canonical_source_key(tag))
            elif isinstance(tag,dict):
                for field in ('name','translated_name'):
                    if isinstance(tag.get(field),str):names.add(canonical_source_key(tag[field]))
        tags_by_media.setdefault(row['media_id'],set()).update(names)
    for case in controls['identity_separations']:
        sides=case['sides']
        for index,side in enumerate(sides):
            names={canonical_source_key(n) for n in side['names']}
            other={canonical_source_key(n) for n in sides[1-index]['names']}
            for mid in side['exclusive_media_ids']:
                tags=tags_by_media.get(mid,set())
                if not names&tags or other&tags:
                    raise ValueError('a2_precision_source_exclusive_name_relation')
    for case in controls.get('cooccurrence_controls',[]):
        for mid in case['media_ids']:
            if not {canonical_source_key(n) for n in case['names']}<=tags_by_media.get(mid,set()):
                raise ValueError('a2_precision_source_cooccurrence_name_relation')
    return {'source_sha256':digest,'source_media_count':len(observed),'required_media_ids':sorted(required)}


def recompute_precision(quality, controls, *, private_root=None, database=None, system_identifier=None):
    from app.services.source_metadata_registry_service import canonical_source_key
    if controls.get('schema_version')!='violet.production-pixiv-precision-controls.v1':
        raise ValueError('a2_precision_controls_invalid')
    if private_root is not None:
        verify_precision_sources(controls,private_root,database=database,system_identifier=system_identifier)
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
        concepts=[{r[3] for r in projection if canonical_source_key(r[0])==canonical_source_key(name)
            and r[1] in {'character','person'}} for name in (left,right)]
        if not all(concepts) or concepts[0]&concepts[1]:
            raise ValueError('a2_precision_cooccurrence_identities_merged_or_missing')
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
