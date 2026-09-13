"""Recompute A2 evidence outcomes without trusting summary booleans."""
import math
import re
import statistics
from collections import Counter
from xml.etree import ElementTree


def verify_browser_actions(browser):
    from urllib.parse import urlparse,parse_qs
    actions=browser.get('actions',[])
    opened={r['media_id']:r for r in actions if r['action']=='open_fullscreen'}
    if len(opened)<3:raise ValueError('a2_fullscreen_samples_missing')
    for mid,row in opened.items():
        image=row['image']
        if (not row.get('overlay_active') or image.get('width',0)<=0 or image.get('height',0)<=0
            or urlparse(image['src']).path!=f'/api/media/{mid}/file'):
            raise ValueError('a2_fullscreen_original_not_loaded')
        kinds={r['action'] for r in actions if r.get('media_id')==mid}
        if not {'thumbnail_to_detail','close_fullscreen','return_gallery'}<=kinds:
            raise ValueError('a2_media_navigation_missing')
    search=browser['search'];old=browser['old_tag'];chip=browser['source_chip']
    if set(search['ids'])!=set(search['api_ids']) or set(old['dom_ids'])!=set(old['api_ids']) or not old['api_ids']:
        raise ValueError('a2_browser_dom_api_sets_differ')
    if (chip['kind']!='source_concept' or chip['param']!='q' or not chip.get('conceptIds')
        or parse_qs(urlparse(chip['href']).query)!=parse_qs(urlparse(chip['navigated_url']).query)):
        raise ValueError('a2_browser_source_chip_navigation_changed')
    recovery=browser['recovery_page']
    if (recovery['status']!=200 or recovery.get('method')!='GET'
        or recovery.get('mutation_performed') is not False or not recovery.get('text')
        or urlparse(recovery['request_url']).path!='/api/admin/dynamic-library-sync/recovery-items'):
        raise ValueError('a2_browser_recovery_page_not_observed')
    return {'fullscreen_samples':len(opened),'search_dom_api_equal':True,'old_tag_dom_api_equal':True,'recovery_read_observed':True}


def verify_launcher_action(launch,repo,candidate):
    from pathlib import Path
    entry=launch.get('normal_entry_invocation',{});process=launch.get('server_process_at_action',{})
    profile=launch.get('profile_at_action',{})
    if (Path(entry.get('executable','')).name!='V.I.O.L.E.T. Production Launcher.exe'
        or entry.get('arguments')!=[] or entry.get('action')!='Restart'
        or not re.fullmatch('[a-f0-9]{64}',entry.get('sha256',''))):
        raise ValueError('a2_normal_launcher_action_missing')
    if (process.get('ProcessId')!=launch['after_pid'] or not process.get('ParentProcessId')
        or not process.get('CreationDate') or not process.get('CommandLine')
        or not re.search(r'run\.py|uvicorn',process['CommandLine'])):
        raise ValueError('a2_launcher_process_observation_missing')
    if (profile.get('candidate_head')!=candidate or profile.get('pixiv_product_enabled') is not True
        or profile.get('pixiv_product_apply_enabled') is not False
        or profile.get('database')!=launch['database']
        or Path(profile.get('code_root','')).resolve()!=Path(repo).resolve()
        or not re.fullmatch('[a-f0-9]{64}',profile.get('sha256',''))):
        raise ValueError('a2_launcher_profile_observation_changed')
    return True


def pytest_outcome(command, log, xml_path=None):
    # Parametrized node IDs and captured application logs can themselves say
    # "1 error". Only pytest's final summary is an outcome count.
    kinds=r'passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected'
    summaries=[line.strip('= \r') for line in log.splitlines() if re.fullmatch(
        rf'\d+ (?:{kinds})(?:, \d+ (?:{kinds}))*(?: in .+)?',line.strip('= \r'))]
    if not summaries:raise ValueError('a2_pytest_summary_missing')
    summary=summaries[-1]
    counts={key:int((re.findall(r'(\d+) '+key+r'\b',summary) or ['0'])[-1])
            for key in ('passed','failed','skipped')}
    counts['errors']=int((re.findall(r'(\d+) errors?\b',summary) or ['0'])[-1])
    failures=set(re.findall(r'^FAILED (\S+)',log,re.MULTILINE))
    errors=set(re.findall(r'^ERROR (\S+)',log,re.MULTILINE))
    if xml_path:
        root=ElementTree.parse(xml_path).getroot()
        cases=list(root.iter('testcase'))
        actual={'passed':0,'failed':0,'errors':0,'skipped':0}
        for case in cases:
            kind=('errors' if case.find('error') is not None else 'failed' if case.find('failure') is not None
                  else 'skipped' if case.find('skipped') is not None else 'passed')
            actual[kind]+=1
        if any(counts[k]!=actual[k] for k in counts):
            raise ValueError('a2_pytest_xml_log_count_mismatch')
    expected_exit=1 if counts['failed'] or counts['errors'] else 0
    if command.get('status')!='finished' or command.get('exit_code')!=expected_exit:
        raise ValueError('a2_pytest_exit_or_completion_invalid')
    if counts['errors'] or errors or re.search(r'^(?:INTERNALERROR|ERRORS? collecting)',log,re.MULTILINE):
        raise ValueError('a2_pytest_unresolved_error')
    if len(failures)!=counts['failed']:
        raise ValueError('a2_pytest_failure_nodes_unaccounted')
    return counts,failures


def latency_statistics(rows):
    values=sorted(float(row['ms']) for row in rows)
    if not values or any(not math.isfinite(v) or v<0 for v in values):
        raise ValueError('a2_query_latency_invalid')
    return {'p50_ms':round(statistics.median(values),3),
            'p95_ms':round(values[math.ceil((len(values)-1)*.95)],3),'max_ms':round(max(values),3)}


def recompute_quality(quality, oracle, *, suggestion_oracle=None, creator_oracle=None, baseline=None):
    from app.services.source_metadata_registry_service import canonical_source_key as key
    projection=quality.get('projection_rows')
    if not isinstance(projection,list) or not projection:
        raise ValueError('a2_raw_quality_projection_required')
    names={}
    for row in projection:
        raw,role,context,concept,media,work=row
        entry=names.setdefault(key(raw),{'concepts':set(),'media':set()})
        entry['concepts'].add(concept);entry['media'].add(media)
    expected_pairs={tuple(sorted(key(n) for n in row['names'])):row['expected'] for row in oracle['identity_pairs']}
    queries=quality['queries'];results=[];seen=set()
    import json
    quote=lambda n:json.dumps(n,ensure_ascii=False)
    def ids(query):
        if query not in queries:raise ValueError('a2_quality_query_missing')
        row=queries[query]
        if row['status_code']!=200:raise ValueError('a2_quality_query_failed')
        return set(row['ids'])
    for case in quality['cases']:
        category=case['category']
        if 'expected' in case and 'names' in case:
            pair=tuple(sorted(key(n) for n in case['names']))
            if expected_pairs.get(pair)!=case['expected']:raise ValueError('a2_frozen_quality_expectation_changed')
            seen.add(pair);sides=[names.get(n,{'concepts':set(),'media':set()}) for n in pair]
            shared=sides[0]['concepts']&sides[1]['concepts'];actual=[ids(quote(n)) for n in pair]
            decision=expected_pairs[pair]
            desired=[sides[0]['media']|sides[1]['media']]*2 if decision=='must_link' else [s['media'] for s in sides]
            passed=all(s['media'] for s in sides) and all(want<=got for want,got in zip(desired,actual))
            if decision=='must_link':passed=passed and bool(shared)
            elif decision=='cannot_link':
                left,right=map(quote,pair)
                # Distinct identities can co-occur or match old tags. Check the
                # actual query exclusion/intersection behavior without assuming
                # their ordinary mixed-search Media sets must be disjoint.
                compositions=((left+' -'+right,actual[0]-actual[1]),
                    (right+' -'+left,actual[1]-actual[0]),(left+' '+right,actual[0]&actual[1]))
                passed=passed and not shared and all(ids(q)==want for q,want in compositions)
        elif category in {'media_set_AND','media_set_negative'}:
            left,right=map(quote,case['names']);a,b=ids(left),ids(right)
            desired=a&b if category=='media_set_AND' else a-b
            actual=ids(left+' '+('' if category=='media_set_AND' else '-')+right)
            passed=desired==actual
        elif category=='accepted_search_equivalence_only':
            family=next(r for r in oracle['search_only_families'] if r['family_id']==case['accepted_family_id'])
            checks=[[ids(f'id:{s["media_id"]} '+quote(n)) for n in family['names']] for s in case['samples']]
            passed=bool(checks) and all(all(x=={sample['media_id']} for x in row)
                for sample,row in zip(case['samples'],checks))
        elif 'expected_ids' in case and 'actual_ids' in case:
            samples=(suggestion_oracle or {}).get('samples',[])
            sample=next((r for r in samples if r['media_id']==case['media_id']),None)
            if not sample:raise ValueError('a2_frozen_suggestion_sample_missing')
            kind=case['kind'];mid=sample['media_id']
            expected=[] if kind=='suggested_positive' else [mid]
            tag=sample['accepted_control_tag'] if kind=='accepted_positive_control' else sample['suggested_tag']
            query=f'id:{mid} '+('-' if kind=='suggested_negative' else '')+json.dumps(tag)
            if (kind not in {'suggested_positive','suggested_negative','accepted_positive_control'}
                or case['query']!=query or case['expected_ids']!=expected):
                raise ValueError('a2_frozen_suggestion_expectation_changed')
            passed=set(expected)==ids(query) and queries[query].get('total')==len(expected)
        elif 'expected_account_union_media_ids' in case:
            family=next((r for r in (creator_oracle or {}).get('selected_families',[]) if r['query']==case['query']),None)
            if not family or set(case['expected_account_union_media_ids'])!=set(family['expected_union_media_ids']):
                raise ValueError('a2_frozen_creator_expectation_changed')
            accounts=case['accounts'];concepts=[set(a['concept_ids']) for a in accounts]
            source={r['provider_creator_id']:set(r['expected_media_ids']) for r in family['creators']}
            if {r['provider_creator_id'] for r in accounts}!=set(source):raise ValueError('a2_creator_account_missing')
            for account in accounts:
                missing=source[account['provider_creator_id']]-set(account['bound_media_ids'])
                if missing!=set(account['missing_bound_media_ids']):raise ValueError('a2_creator_support_summary_changed')
            passed=(set(case['expected_account_union_media_ids'])<=ids(quote(case['query']))
                and all(len(c)==1 for c in concepts) and len(set.union(*concepts))==len(concepts)
                and not any(a['missing_bound_media_ids'] for a in accounts))
        else:raise ValueError('a2_quality_case_recomputation_unavailable')
        results.append(bool(passed))
        if bool(case.get('passed'))!=bool(passed):raise ValueError('a2_quality_summary_disagrees_with_raw')
    # Keep the original independent denominator; absence cannot silently remove
    # a formerly present case from release admission.
    required={pair for pair in expected_pairs if all(n in names for n in pair)}
    if baseline:
        required|={tuple(sorted(key(n) for n in c['names'])) for c in baseline['cases'] if 'expected' in c and 'names' in c}
    if not required<=seen:raise ValueError('a2_quality_case_missing')
    return {'case_count':len(results),'failed_cases':sum(not r for r in results),
            'categories':dict(Counter(c['category'] for c in quality['cases']))}
