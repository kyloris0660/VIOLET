"""Recompute A2 evidence outcomes without trusting summary booleans."""
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

PRESERVED_TABLES=frozenset('blombooru_'+name for name in (
    'media','albums','album_hierarchy','album_media','entities','entity_aliases','entity_evidence',
    'entity_external_identities','entity_translations','media_entity_candidates','media_entity_assignments',
    'media_tags','source_concept_signals','source_concepts','source_concept_aliases',
    'source_concept_evidence','source_concept_signal_links'))
PRESERVED_NONEMPTY=frozenset('blombooru_'+name for name in (
    'media','source_concept_signals','source_concepts','source_concept_aliases',
    'source_concept_evidence','source_concept_signal_links'))


def verify_preserved_tables(snapshot):
    import hashlib
    tables=snapshot.get('tables',{})
    if set(tables)!=PRESERVED_TABLES:raise ValueError('a2_preservation_table_inventory_changed')
    empty=hashlib.sha256(b'').hexdigest()
    for name,row in tables.items():
        count=row.get('rows');digest=row.get('sha256')
        if (type(count) is not int or count<0 or not isinstance(digest,str)
            or not re.fullmatch('[a-f0-9]{64}',digest)
            or (count==0)!=(digest==empty)
            or (name in PRESERVED_NONEMPTY and count==0)):
            raise ValueError('a2_preservation_row_digest_invalid:'+name)
    return tables


def verify_preservation_snapshots(snapshots,*,candidate,database,system_identifier,operation):
    from datetime import datetime
    if not operation or len(snapshots) not in (3,4):raise ValueError('a2_preservation_checkpoints_missing')
    checkpoints=['before','after-rollback','after-reapply']+(['after-source'] if len(snapshots)==4 else [])
    baseline=None;previous=None
    for snapshot,checkpoint in zip(snapshots,checkpoints):
        if (snapshot.get('candidate_head')!=candidate or snapshot.get('database')!=database
            or snapshot.get('system_identifier')!=system_identifier
            or snapshot.get('operation_id')!=operation or snapshot.get('checkpoint')!=checkpoint):
            raise ValueError('a2_preservation_operation_binding_changed')
        started=datetime.fromisoformat(snapshot['started_at']);finished=datetime.fromisoformat(snapshot['finished_at'])
        if (not started.tzinfo or not finished.tzinfo or finished<started
            or (previous and started<=previous)):
            raise ValueError('a2_preservation_checkpoint_order_changed')
        previous=finished;tables=verify_preserved_tables(snapshot)
        if baseline is None:baseline=tables
        elif tables!=baseline:raise ValueError('a2_raw_independent_preservation')
    return {'table_count':len(baseline),'checkpoint_count':len(snapshots),'media_count':baseline['blombooru_media']['rows']}


import hashlib, unicodedata
from urllib.parse import parse_qs, urlparse

def verify_source_chip_concept_binding(browser):
    chip=browser.get('source_chip',{});detail=chip.get('detail',{});attempt=browser.get('attempt_id')
    mid=chip.get('media_id');page=urlparse(detail.get('url',''));request=urlparse(detail.get('request_url',''))
    if (type(mid) is not int or mid<=0 or detail.get('media_id')!=mid or detail.get('api_media_id')!=mid
        or chip.get('attempt_id')!=attempt or detail.get('attempt_id')!=attempt or detail.get('status_code')!=200
        or page.path!=f'/media/{mid}' or request.path!=f'/api/source-assertions/media/{mid}'
        or not page.netloc or (page.scheme,page.netloc)!=(request.scheme,request.netloc)
        or not any(a.get('action')=='thumbnail_to_detail' and a.get('media_id')==mid and a.get('attempt_id')==attempt for a in browser.get('actions',[]))):
        raise ValueError('a2_browser_source_chip_detail_binding_changed')
    try:
        api_body=json.loads(detail['body_text'])
        if (hashlib.sha256(detail['body_text'].encode('utf-8')).hexdigest()!=detail.get('body_sha256')
            or api_body.get('media_id')!=mid or api_body.get('source_concepts')!=detail.get('source_concepts')):
            raise ValueError('a2_browser_source_chip_api_body_changed')
    except (KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('a2_browser_source_chip_api_body_missing') from exc
    label=chip.get('display_name');name=chip.get('name_text');concepts=detail.get('source_concepts')
    if not isinstance(label,str) or not label.strip() or label!=name or not isinstance(concepts,list) or not concepts:
        raise ValueError('a2_browser_source_chip_concept_binding_changed')
    key=lambda text:unicodedata.normalize('NFKC',str(text)).strip().lower()
    def concept_label(c):return c.get('display_name') or c.get('primary_display_name') or c.get('search_value') or 'SourceConcept '+str(c.get('concept_id'))
    group=[c for c in concepts if key(concept_label(c))==key(label)]
    declared=chip.get('conceptIds','')
    if not isinstance(declared,str) or not re.fullmatch(r'[1-9][0-9]*(?:,[1-9][0-9]*)*',declared):
        raise ValueError('a2_browser_source_chip_concept_binding_changed')
    ids=[int(value) for value in declared.split(',')]
    expected=[c.get('concept_id') or c.get('id') for c in group]
    if (not group or label!=concept_label(group[0]) or len(set(ids))!=len(ids) or any(type(i) is not int or i<=0 for i in expected)
        or set(ids)!=set(expected) or any(not c.get('evidence_items') or not any(
            e.get('media_scope')=='current_media' and type(e.get('id')) is int and e['id']>0 for e in c['evidence_items'])
            or not any(type(s.get('source_metadata_record_id')) is int and s['source_metadata_record_id']>0
                       and s.get('media_id')==mid for s in c.get('local_media_support',[])) for c in group)):
        raise ValueError('a2_browser_source_chip_media_support_changed')
    value=group[0].get('search_value')
    if value is None:raise ValueError('a2_browser_source_chip_not_searchable')
    value=str(value or concept_label(group[0]))
    token=value.strip()
    query='"'+token.replace('"','')+'"' if re.search(r'^-|[\s:"*?\[\]() ]',token) else token
    href=urlparse(chip.get('href',''));search=chip.get('search',{})
    if chip.get('value')!=value or parse_qs(href.query).get('q')!=[query] or search.get('query')!=query:
        raise ValueError('a2_browser_source_chip_concept_query_changed')
    expansions=search.get('source_concept_expansions',[])
    if not isinstance(expansions,list) or not set(ids)&{r.get('concept_id') for r in expansions}:
        raise ValueError('a2_browser_source_chip_search_concept_changed')
    pages=search.get('pages');total=search.get('total');all_ids=search.get('all_api_ids')
    if not isinstance(pages,list) or not pages or type(total) is not int or total<=0 or not isinstance(all_ids,list):
        raise ValueError('a2_browser_source_chip_complete_results_missing')
    observed=[];limit=None
    for number,row in enumerate(pages,1):
        url=urlparse(row.get('request_url',''));parameters=parse_qs(url.query)
        if (row.get('page')!=number or row.get('status_code')!=200 or row.get('total')!=total
            or url.path!='/api/search' or (url.scheme,url.netloc)!=(href.scheme,href.netloc)
            or parameters.get('q')!=[query] or parameters.get('page')!=[str(number)]
            or type(row.get('limit')) is not int or row['limit']<=0 or parameters.get('limit')!=[str(row['limit'])]):
            raise ValueError('a2_browser_source_chip_result_page_changed')
        if limit is None:limit=row['limit']
        if row['limit']!=limit:raise ValueError('a2_browser_source_chip_result_page_changed')
        values=row.get('ids')
        if not isinstance(values,list) or any(type(i) is not int or i<=0 for i in values):
            raise ValueError('a2_browser_source_chip_result_page_changed')
        observed.extend(values)
    if (len(pages)!=(total+limit-1)//limit or len(observed)!=len(set(observed)) or len(observed)!=total
        or sorted(observed)!=all_ids or mid not in observed or set(search.get('api_ids',[]))!=set(pages[0]['ids'])):
        raise ValueError('a2_browser_source_chip_media_result_changed')
    return {'media_id':mid,'concept_ids':ids,'display_name':label,'query':query,'complete_result_count':total,'page_count':len(pages)}

def verify_browser_actions(browser, *, launch=None, suggestion_oracle=None):
    if launch is not None:
        from scripts.production_pixiv_a2_service_evidence import verify_browser_service
        verify_browser_service(browser,launch)
    from urllib.parse import urlparse,parse_qs
    actions=browser.get('actions',[])
    attempt=browser.get('attempt_id')
    if not isinstance(attempt,str) or not attempt.strip():raise ValueError('a2_browser_attempt_missing')
    navigation=('thumbnail_to_detail','open_fullscreen','close_fullscreen','return_gallery')
    flows={}
    for row in actions:
        if row.get('action') not in navigation:continue
        flow=row.get('flow_id')
        if row.get('attempt_id')!=attempt or not isinstance(flow,str) or not flow.strip():
            raise ValueError('a2_browser_attempt_changed')
        flows.setdefault((row['media_id'],flow),[]).append(row['action'])
    if not flows or any(tuple(steps)!=navigation for steps in flows.values()):
        raise ValueError('a2_media_navigation_order_changed')
    opened={r['media_id']:r for r in actions if r['action']=='open_fullscreen'}
    if len(opened)<3:raise ValueError('a2_fullscreen_samples_missing')
    for mid,row in opened.items():
        image=row['image']
        if (not row.get('overlay_active') or image.get('width',0)<=0 or image.get('height',0)<=0
            or urlparse(image['src']).path!=f'/api/media/{mid}/file'):
            raise ValueError('a2_fullscreen_original_not_loaded')
    search=browser['search'];old=browser['old_tag'];chip=browser['source_chip']
    old_page=urlparse(old.get('url',''));old_request=urlparse(old.get('request_url',''))
    if (old_page.path!='/' or not old_page.netloc or old_request.path!='/api/search'
        or (old_page.scheme,old_page.netloc)!=(old_request.scheme,old_request.netloc)
        or not old.get('query') or parse_qs(old_page.query).get('q')!=[old['query']]
        or parse_qs(old_request.query).get('q')!=[old['query']]
        or old.get('attempt_id')!=attempt):
        raise ValueError('a2_browser_old_tag_navigation_missing')
    suggestion=browser.get('suggestion_display',{})
    mid=suggestion.get('media_id');shown=suggestion.get('observed_items');api=suggestion.get('api_items')
    page=urlparse(suggestion.get('url',''));request=urlparse(suggestion.get('request_url',''))
    if (type(mid) is not int or mid<=0 or suggestion.get('attempt_id')!=attempt
        or page.path!=f'/media/{mid}' or request.path!=f'/api/media/{mid}'
        or not page.netloc or (page.scheme,page.netloc)!=(request.scheme,request.netloc)
        or suggestion.get('status_code')!=200 or suggestion.get('api_media_id')!=mid
        or suggestion.get('mutation_performed') is not False or not isinstance(shown,list) or not shown
        or not isinstance(api,list) or not api):
        raise ValueError('a2_browser_suggestion_not_observed')
    if suggestion_oracle is not None and not any(s.get('media_id')==mid and s.get('suggested_tag')==suggestion.get('tag')
            for s in suggestion_oracle.get('samples',[])):
        raise ValueError('a2_browser_suggestion_frozen_sample_changed')
    for item in shown:
        source=next((s for s in api if s.get('id')==item.get('id')),None)
        if (type(item.get('id')) is not int or item['id']<=0 or item.get('media_id')!=mid
            or not item.get('text') or item.get('visible') is not True
            or item.get('tag_name')!='SPAN' or item.get('href') is not None or 'border-dashed' not in item.get('classes','')
            or not source or source.get('is_suggestion') is not True or not source.get('name')
            or source['name']!=suggestion.get('tag')
            or not (item['text']==source['name'] or item['text'].startswith(source['name']+' (')
                or item.get('title','').startswith(source['name']+', '))
            or 'suggestion' not in item.get('title','')):
            raise ValueError('a2_browser_suggestion_content_changed')
    if set(search['ids'])!=set(search['api_ids']) or set(old['dom_ids'])!=set(old['api_ids']) or not old['api_ids']:
        raise ValueError('a2_browser_dom_api_sets_differ')
    href=urlparse(chip.get('href',''));navigated=urlparse(chip.get('navigated_url',''))
    query=parse_qs(href.query,keep_blank_values=True).get('q',[])
    observed=chip.get('search',{});request=urlparse(observed.get('request_url',''))
    if (chip['kind']!='source_concept' or chip['param']!='q' or not chip.get('conceptIds')
        or href.path!='/' or navigated.path!='/' or not href.netloc or href.scheme not in {'http','https'}
        or (href.scheme,href.netloc)!=(navigated.scheme,navigated.netloc)
        or len(query)!=1 or not query[0].strip()
        or parse_qs(href.query,keep_blank_values=True)!=parse_qs(navigated.query,keep_blank_values=True)
        or observed.get('query')!=query[0] or request.path!='/api/search'
        or (request.scheme,request.netloc)!=(href.scheme,href.netloc)
        or parse_qs(request.query,keep_blank_values=True).get('q')!=query
        or observed.get('status_code')!=200 or not observed.get('api_ids')
        or set(observed.get('dom_ids',[]))!=set(observed['api_ids'])):
        raise ValueError('a2_browser_source_chip_navigation_changed')
    recovery=browser['recovery_page']
    if (recovery['status']!=200 or recovery.get('method')!='GET'
        or recovery.get('mutation_performed') is not False or not recovery.get('text')
        or urlparse(recovery['request_url']).path!='/api/admin/dynamic-library-sync/recovery-items'):
        raise ValueError('a2_browser_recovery_page_not_observed')
    return {'fullscreen_samples':len(opened),'search_dom_api_equal':True,'old_tag_dom_api_equal':True,'recovery_read_observed':True,
            'source_chip_concept_binding':verify_source_chip_concept_binding(browser)}


def recorded_code_root_matches(value,repo):
    from pathlib import Path
    return isinstance(value,str) and bool(value.strip()) and Path(value).is_absolute() and Path(value).resolve()==Path(repo).resolve()


def launcher_runtime_context(launch,repo,candidate):
    """Keep a business candidate distinct from its proved fixed deployment."""
    if 'fixed_runtime_binding' in launch:
        from scripts.production_pixiv_runtime_snapshot import verify_deployment_runtime_binding
        return verify_deployment_runtime_binding(Path(repo),candidate,launch['fixed_runtime_binding'])
    return {'business_source_head':candidate,'runtime_head':candidate,'runtime_root':str(Path(repo).resolve())}


def verify_launcher_action(launch,repo,candidate):
    from pathlib import Path
    entry=launch.get('normal_entry_invocation',{});process=launch.get('server_process_at_action',{})
    profile=launch.get('profile_at_action',{})
    context=launcher_runtime_context(launch,repo,candidate)
    runtime_root=Path(context['runtime_root'])
    if (Path(entry.get('executable','')).name!='V.I.O.L.E.T. Production Launcher.exe'
        or entry.get('arguments')!=[] or entry.get('action') not in {'Start','Restart'}
        or not re.fullmatch('[a-f0-9]{64}',entry.get('sha256',''))):
        raise ValueError('a2_normal_launcher_action_missing')
    if (process.get('ProcessId')!=launch['after_pid'] or not process.get('ParentProcessId')
        or not process.get('CreationDate') or not process.get('CommandLine')
        or not re.search(r'run\.py|uvicorn',process['CommandLine'])):
        raise ValueError('a2_launcher_process_observation_missing')
    if (profile.get('candidate_head')!=context['runtime_head'] or profile.get('pixiv_product_enabled') is not True
        or profile.get('pixiv_product_apply_enabled') is not False
        or profile.get('database')!=launch['database']
        or not recorded_code_root_matches(profile.get('code_root'),runtime_root)
        or not re.fullmatch('[a-f0-9]{64}',profile.get('sha256',''))):
        raise ValueError('a2_launcher_profile_observation_changed')
    verify_normal_entry_provenance(launch,runtime_root)
    return context


def configured_launcher_root(repo):
    """Use this repository's shared Git root, not a supplied receipt path."""
    from pathlib import Path
    from scripts.trusted_git import resolve_trusted_git_executable,run_trusted_git_bytes
    root=Path(repo).resolve(strict=True)
    git=resolve_trusted_git_executable(repo_root=root)
    def common_dir(worktree):
        observed=run_trusted_git_bytes(worktree,['rev-parse','--path-format=absolute','--git-common-dir'],git=git)
        if observed.returncode!=0:raise ValueError('a2_launcher_git_common_dir_invalid')
        common=Path(observed.stdout.decode('utf-8').strip())
        if not common.is_absolute() or not common.is_dir():raise ValueError('a2_launcher_git_common_dir_invalid')
        return common.resolve(strict=True)
    common=common_dir(root);canonical=common.parent
    if common_dir(canonical)!=common:raise ValueError('a2_launcher_git_common_dir_changed')
    return canonical


def verify_normal_entry_provenance(launch,repo):
    import hashlib,json
    from pathlib import Path
    entry=launch.get('normal_entry_invocation',{});evidence=launch.get('normal_entry_provenance',{})
    if not evidence:raise ValueError('a2_normal_entry_provenance_missing')
    canonical=configured_launcher_root(repo)
    executable=canonical/'V.I.O.L.E.T. Production Launcher.exe'
    runtime=canonical/'.local_manifests/production_launcher/launcher-runtime.json'
    def exact_path(value,path):return isinstance(value,str) and Path(value).is_absolute() and Path(value).resolve()==path.resolve()
    def observed_file(row,path):
        return (isinstance(row,dict) and exact_path(row.get('path'),path) and path.is_file()
            and row.get('sha256')==hashlib.sha256(path.read_bytes()).hexdigest())
    if (not exact_path(entry.get('executable'),executable)
        or not observed_file(evidence.get('executable'),executable)
        or entry.get('sha256')!=evidence['executable']['sha256']
        or not observed_file(evidence.get('runtime'),runtime)):
        raise ValueError('a2_normal_entry_configured_file_changed')
    config=json.loads(runtime.read_text(encoding='utf-8'))
    controller=Path(repo)/'scripts/violet_production_control.py'
    profile=Path(repo)/'.local_manifests/production_launcher/production-profile.json'
    if (not exact_path(config.get('repo_root'),Path(repo)) or not exact_path(config.get('controller'),controller)
        or not observed_file(evidence.get('controller'),controller)
        or not observed_file(evidence.get('profile'),profile)
        or launch['profile_at_action']['sha256']!=evidence['profile']['sha256']):
        raise ValueError('a2_normal_entry_runtime_binding_changed')
    actual_profile=json.loads(profile.read_text(encoding='utf-8'))
    observed=launch['profile_at_action']
    if (actual_profile.get('candidate_head')!=observed['candidate_head']
        or actual_profile.get('db',{}).get('name')!=launch['database']
        or actual_profile.get('pixiv_product_enabled') is not True
        or actual_profile.get('pixiv_product_apply_enabled') is not False
        or not exact_path(actual_profile.get('repo_root'),Path(repo))):
        raise ValueError('a2_normal_entry_profile_content_changed')
    chain=evidence.get('process_chain_at_action',[])
    by_pid={row.get('ProcessId'):row for row in chain}
    if (len(by_pid)!=len(chain) or any(type(pid) is not int or pid<=0 for pid in by_pid)
        or type(entry.get('pid')) is not int or entry['pid'] not in by_pid
        or by_pid.get(launch['after_pid'])!=launch['server_process_at_action']):
        raise ValueError('a2_normal_entry_process_chain_missing')
    current=launch['after_pid'];visited=[]
    while current not in visited:
        visited.append(current);row=by_pid.get(current,{})
        if not row.get('CreationDate') or not row.get('CommandLine') or not row.get('ExecutablePath'):
            raise ValueError('a2_normal_entry_process_observation_missing')
        if current==entry['pid']:break
        current=row.get('ParentProcessId')
    import shlex
    def controller_action(row):
        try:tokens=[t.strip('"') for t in shlex.split(row['CommandLine'],posix=False)]
        except ValueError:return False
        for index,token in enumerate(tokens[:-1]):
            if exact_path(token,controller) and tokens[index+1]==entry['action'].lower():
                return any(t=='--profile' and tokens[i+1]==config.get('profile','production-default')
                    for i,t in enumerate(tokens[:-1]))
        return False
    if (current!=entry['pid'] or not exact_path(by_pid[current].get('ExecutablePath'),executable)
        or not any(controller_action(by_pid[pid]) for pid in visited)):
        raise ValueError('a2_normal_entry_start_relationship_changed')
    # Records are captured at the operation. Historical PIDs need not live now;
    # portable unpacked Electron and controller intermediates remain in chain.
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


def frozen_identity_recall(pair,decision,baseline,*,recall_baseline=None):
    """Preserve prior expected supports, not incidental mixed-search extras."""
    from app.services.source_metadata_registry_service import canonical_source_key as key
    pair=tuple(sorted(map(key,pair)))
    def previous(source):
        return next((row for row in (source or {}).get('cases',[])
            if 'expected' in row and tuple(sorted(map(key,row['names'])))==pair),None)
    original=previous(baseline)
    frozen_source=recall_baseline if recall_baseline is not None else baseline
    frozen_case=previous(frozen_source)
    if original is None and frozen_case is None:return [set(),set()]
    if frozen_case is None or frozen_case['expected']!=decision or original is not None and original['expected']!=decision:
        raise ValueError('a2_frozen_identity_baseline_case_changed')
    rows=frozen_source.get('projection_rows')
    if not isinstance(rows,list) or not rows:raise ValueError('a2_frozen_identity_projection_required')
    sides=[{row[4] for row in rows if key(row[0])==name} for name in pair]
    if decision=='must_link':
        missing={mid for row in (original or {},frozen_case)
            for values in row.get('missing_recall_media_ids',[]) for mid in values}
        both=set.union(*sides)|missing
        return [both,both]
    return sides


def collect_creator_projection(cursor):
    """Read actual stable-account support, never account summaries or names."""
    cursor.execute('''select distinct r.provider,r.artist_id,e.concept_id,b.media_id
        from blombooru_source_concept_product_media_bindings b
        join blombooru_source_concept_product_runs p on p.id=b.product_run_id
        join blombooru_source_concept_evidence e on e.id=b.evidence_id
        join blombooru_source_concept_signals s on s.id=e.signal_id
        join blombooru_source_metadata_records r on r.id=b.source_metadata_record_id
        where p.source_mode='production_scope' and p.status='active'
          and b.source_revision=r.binding_revision and r.provider='pixiv'
          and s.role_hint='artist'
          and s.origin_type in ('pixiv_creator_identity_anchor','pixiv_creator_observation')
        order by r.provider,r.artist_id,e.concept_id,b.media_id''')
    return [dict(zip(('provider','provider_creator_id','concept_id','media_id'),row))
            for row in cursor.fetchall()]


import hashlib, json

def projection_fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()

def collect_owned_business_projection(cursor, metadata):
    """Read all owned core and product rows using the caller's one snapshot."""
    resolver=metadata['resolver_run_id']; product_id=metadata['id']
    def rows(table,condition,parameters):
        cursor.execute('select to_jsonb(t) from blombooru_'+table+' t where '+condition+' order by t.id',parameters)
        return [row[0] for row in cursor.fetchall()]
    core={}
    core['resolution_runs']=rows('source_concept_resolution_runs','t.run_id=%s',(resolver,))
    core['signals']=rows('source_concept_signals','t.created_by_run_id=%s',(resolver,))
    core['concepts']=rows('source_concepts','t.created_by_run_id=%s',(resolver,))
    own_concepts='select id from blombooru_source_concepts where created_by_run_id=%s'
    own_signals='select id from blombooru_source_concept_signals where created_by_run_id=%s'
    for name,condition,parameters in (
        ('aliases','t.created_by_run_id=%s or t.concept_id in ('+own_concepts+') or t.source_signal_id in ('+own_signals+')',(resolver,resolver,resolver)),
        ('evidence','t.run_id=%s or t.concept_id in ('+own_concepts+') or t.signal_id in ('+own_signals+')',(resolver,resolver,resolver)),
        ('signal_links','t.run_id=%s or t.concept_id in ('+own_concepts+') or t.signal_id in ('+own_signals+')',(resolver,resolver,resolver)),
        ('search_index','t.run_id=%s or t.concept_id in ('+own_concepts+')',(resolver,resolver)),
    ):
        core[name]=rows('source_concept_'+name,condition,parameters)
    signals={r['id']:r['signal_key'] for r in core['signals']}
    concepts={r['id']:r['concept_key'] for r in core['concepts']}
    runs={r['id']:r['run_id'] for r in core['resolution_runs']}
    def ref(mapping,value):
        if value is None:return None
        return mapping.get(value,{'external_local_id':value})
    floats={'signals':('confidence',),'concepts':('confidence_score','evidence_score'),
            'aliases':('confidence',),'signal_links':('confidence',),'search_index':('weight',)}
    full={}
    for table,items in core.items():
        normalized=[]
        for original in items:
            row={k:v for k,v in original.items() if k not in {'id','created_at','updated_at','started_at','finished_at','runtime_seconds'}}
            # Operational before/after table counts are protected by separate
            # transaction proofs. They are not database-neutral business data.
            if table=='resolution_runs':row.pop('no_truth_write_proof_json',None)
            for field in floats.get(table,()):
                if row.get(field) is not None:row[field]=float(row[field])
            for field,mapping in (('concept_id',concepts),('superseded_by_concept_id',concepts),('signal_id',signals),('source_signal_id',signals),('resolution_run_id',runs)):
                if field in row:row[field]=ref(mapping,row[field])
            normalized.append(row)
        full[table]=sorted(normalized,key=projection_fingerprint)
    full['counts']={key:len(values) for key,values in full.items()}
    full['canonical_fingerprint']=projection_fingerprint(full)
    # Reconstruct the existing authoritative core business schema as an
    # additional cross-check against the actual apply/reapply receipt.
    def select(row,fields):return {destination:row[source] for destination,source in fields}
    core_business={
        'signals':[select(r,[(k,k) for k in ('signal_key','provider','display_value','normalized_key','canonical_key','role_hint','work_context_key','source_kind','trust_tier','status','evidence_payload','source_run_id','created_by_run_id')]) for r in full['signals']],
        'concepts':[select(r,[(k,k) for k in ('concept_key','primary_display_name','concept_type_hint','status','confidence_score','evidence_score','media_count','source_count','created_by_run_id')]+[('evidence_summary','evidence_summary_json'),('lifecycle','lifecycle_payload')]) for r in full['concepts']],
        'aliases':[select(r,[('concept_key','concept_id'),('signal_key','source_signal_id')]+[(k,k) for k in ('alias_key','display_name','alias_role','status','confidence','evidence_payload','created_by_run_id')]) for r in full['aliases']],
        'evidence':[select(r,[('concept_key','concept_id'),('signal_key','signal_id')]+[(k,k) for k in ('provider','evidence_type','evidence_strength','payload','run_id','status')]) for r in full['evidence']],
        'links':[select(r,[('concept_key','concept_id'),('signal_key','signal_id')]+[(k,k) for k in ('link_status','confidence')]+[('reason_code','resolution_reason_code'),('negative_reason','negative_reason_code')]+[(k,k) for k in ('resolver_version','run_id','evidence_payload')]) for r in full['signal_links']],
        'search_index':[select(r,[('concept_key','concept_id')]+[(k,k) for k in ('search_key','display_name','alias_role','weight','status')]+[('evidence_refs','evidence_refs_json'),('run_id','run_id')]) for r in full['search_index']],
    }
    keys={'signals':('signal_key',),'concepts':('concept_key',),'aliases':('concept_key','alias_key','alias_role'),
          'evidence':('concept_key','signal_key','evidence_type'),'links':('signal_key','concept_key','run_id'),'search_index':('concept_key','search_key','alias_role')}
    for table,key in keys.items():core_business[table].sort(key=lambda r:tuple(str(r[k]) for k in key))
    core_business['counts']={key:len(values) for key,values in core_business.items()}
    core_business['canonical_fingerprint']=projection_fingerprint(core_business)
    product_rows={name:rows('source_concept_'+name,'t.product_run_id=%s',(product_id,))
                  for name in ('product_clusters','candidate_dispositions','ambiguity_records')}
    clusters=[select(r,[(k,k) for k in ('cluster_key','primary_display_name','concept_type_hint','status')]+[('member_signal_keys','member_signal_keys_json'),('stable_identity_anchors','stable_identity_anchors_json'),('aliases','aliases_json'),('evidence_summary','evidence_json'),('provenance','provenance_json')])|
              {'work_references':r['work_page_references_json'].get('work',[]),'page_references':r['work_page_references_json'].get('page',[])} for r in product_rows['product_clusters']]
    candidates=[select(r,[(k,k) for k in ('pair_key','left_signal_key','right_signal_key','disposition','reason_code','negative_reason','union_decision','same_resolved_component')]+[('evidence_refs','evidence_refs_json')]) for r in product_rows['candidate_dispositions']]
    ambiguities=[select(r,[(k,k) for k in ('record_key','record_kind','status','reason_code')]+[('signal_keys','signal_keys_json'),('evidence_refs','evidence_refs_json'),('summary','summary_json')]) for r in product_rows['ambiguity_records']]
    child_fingerprints={}
    for table,payloads,key in (('product_clusters',clusters,'cluster_key'),('candidate_dispositions',candidates,'pair_key'),('ambiguity_records',ambiguities,'record_key')):
        by_key={r[key]:r['canonical_fingerprint'] for r in product_rows[table]}
        child_fingerprints[table]=[[r[key],by_key[r[key]],projection_fingerprint(r)] for r in sorted(payloads,key=lambda r:r[key])]
        payloads.sort(key=lambda r:r[key])
    cursor.execute('select summary_json from blombooru_source_concept_product_runs where id=%s',(product_id,))
    summary=cursor.fetchone()[0]
    versions=(summary or {}).get('policy_versions') or {}
    product={
        'scope_key':metadata['scope_key'],'source_mode':metadata['source_mode'],
        'px1_input_fingerprint':metadata['input_fingerprint'],'px2_business_projection_fingerprint':metadata['business_fingerprint'],
        'resolver_version':metadata['resolver_version'],'context_policy_version':versions.get('context_policy_version'),
        'candidate_policy_version':versions.get('candidate_policy_version'),'product_policy_version':metadata['policy_version'],
        'clusters':clusters,'candidate_dispositions':candidates,'ambiguity_records':ambiguities}
    if (summary or {}).get('input_selection') is not None:product['input_selection']=summary['input_selection']
    return {'run_key':metadata['run_key'],'product':product,'product_fingerprint':projection_fingerprint(product),
            'child_fingerprints':child_fingerprints,'core_business':core_business,'full_core':full,
            'full_core_fingerprint':full['canonical_fingerprint'],'core_business_fingerprint':core_business['canonical_fingerprint']}

def verify_owned_business_projection(projection, metadata, *, approved=None):
    if projection.get('run_key')!=metadata['run_key']:raise ValueError('a2_owned_business_projection_run_changed')
    product=projection.get('product',{})
    product_fingerprint=projection_fingerprint(product)
    if (projection.get('product_fingerprint')!=product_fingerprint or product_fingerprint!=metadata['result_fingerprint']
        or any(stored!=actual for values in projection.get('child_fingerprints',{}).values() for _,stored,actual in values)):
        raise ValueError('a2_owned_product_projection_changed')
    expected_children={'product_clusters','candidate_dispositions','ambiguity_records'}
    if set(projection.get('child_fingerprints',{}))!=expected_children:raise ValueError('a2_owned_product_projection_missing')
    for table,field,key in (('product_clusters','clusters','cluster_key'),('candidate_dispositions','candidate_dispositions','pair_key'),('ambiguity_records','ambiguity_records','record_key')):
        declared=projection['child_fingerprints'][table]
        expected=[[r[key],projection_fingerprint(r),projection_fingerprint(r)] for r in sorted(product[field],key=lambda r:r[key])]
        if declared!=expected:raise ValueError('a2_owned_product_projection_child_changed')
    for name in ('core_business','full_core'):
        payload=dict(projection.get(name,{}));fingerprint=payload.pop('canonical_fingerprint',None)
        expected={'signals','concepts','aliases','evidence','links','search_index'} if name=='core_business' else {'resolution_runs','signals','concepts','aliases','evidence','signal_links','search_index'}
        if (set(payload)!=expected|{'counts'} or payload['counts']!={k:len(payload[k]) for k in expected}
            or fingerprint!=projection_fingerprint(payload)
            or projection.get('core_business_fingerprint' if name=='core_business' else 'full_core_fingerprint')!=fingerprint):
            raise ValueError('a2_owned_core_projection_invalid')
    if approved is not None:
        required={'run_key','product_fingerprint','core_business_fingerprint','full_core_fingerprint','core_counts','product_counts'}
        if (set(approved)!=required or any(projection.get(k)!=approved[k] for k in ('run_key','product_fingerprint','core_business_fingerprint','full_core_fingerprint'))
            or projection['full_core']['counts']!=approved['core_counts']
            or {k:len(product[k]) for k in ('clusters','candidate_dispositions','ambiguity_records')}!=approved['product_counts']):
            raise ValueError('a2_owned_business_projection_not_approved_candidate')
    return {'run_key':metadata['run_key'],'product_fingerprint':product_fingerprint,
            'core_business_fingerprint':projection['core_business_fingerprint'],'full_core_fingerprint':projection['full_core_fingerprint'],
            'core_counts':projection['full_core']['counts'],'product_counts':{k:len(product[k]) for k in ('clusters','candidate_dispositions','ambiguity_records')}}


RUN_IDENTITY_FIELDS=('id','run_key','scope_key','source_mode','policy_version','result_fingerprint',
    'input_fingerprint','business_fingerprint','resolver_run_id','resolver_version')


def collect_final_projection(cursor):
    """Collect valid support and every owned business row from the caller snapshot."""
    cursor.execute("""select id,run_key,scope_key,source_mode,policy_version,result_fingerprint,
        input_fingerprint,business_fingerprint,resolver_run_id,resolver_version from blombooru_source_concept_product_runs
        where source_mode in ('existing_source_metadata','production_scope') and status='active' order by id""")
    runs=cursor.fetchall()
    cursor.execute("""select b.id,b.product_run_id,b.evidence_id,b.source_metadata_record_id,b.media_id,b.source_revision
        from blombooru_source_concept_product_media_bindings b join blombooru_source_concept_product_runs p on p.id=b.product_run_id
        join blombooru_source_metadata_records r on r.id=b.source_metadata_record_id
        where p.source_mode in ('existing_source_metadata','production_scope') and p.status='active'
          and b.source_revision=r.binding_revision order by b.id""")
    rows=[list(r) for r in cursor.fetchall()]
    result={'active_runs':len(runs),'run_keys':[r[1] for r in runs],
        'run_metadata':[dict(zip(RUN_IDENTITY_FIELDS,row)) for row in runs],'binding_rows':rows,'bindings':len(rows),
        'bound_media_ids':sorted({r[4] for r in rows}),'source_record_ids':sorted({r[3] for r in rows}),
        'duplicate_support_count':len(rows)-len({(r[2],r[3],r[4]) for r in rows})}

    result['owned_business_projection']=[collect_owned_business_projection(cursor,m) for m in result['run_metadata']]
    return result

def verify_final_projection(recorded,actual,*,approved_run=None,approved_projection=None):
    fields=('active_runs','run_keys','run_metadata','bindings','binding_rows','bound_media_ids','source_record_ids','duplicate_support_count')
    if (actual.get('active_runs')!=1 or actual.get('duplicate_support_count')!=0
        or any(k not in recorded or recorded[k]!=actual.get(k) for k in fields)):
        raise ValueError('a2_live_final_projection_changed')
    metadata=actual.get('run_metadata')
    if (not isinstance(metadata,list) or len(metadata)!=1
        or set(metadata[0])!=set(RUN_IDENTITY_FIELDS)
        or any(value is None or value=='' for value in metadata[0].values())
        or actual['run_keys']!=[metadata[0]['run_key']]
        or any(row[1]!=metadata[0]['id'] for row in actual['binding_rows'])):
        raise ValueError('a2_live_final_projection_changed')
    if approved_run is not None:
        expected_fields=set(RUN_IDENTITY_FIELDS)-{'id'}
        if (set(approved_run)!=expected_fields
            or any(metadata[0][key]!=approved_run[key] for key in expected_fields)):
            raise ValueError('a2_live_run_not_approved_candidate')
    owned=actual.get('owned_business_projection')
    if owned is None:
        if approved_projection is not None:raise ValueError('a2_owned_business_projection_missing')
    else:
        if not isinstance(owned,list) or len(owned)!=len(metadata):raise ValueError('a2_owned_business_projection_missing')
        if 'owned_business_projection' in recorded and recorded['owned_business_projection']!=owned:
            raise ValueError('a2_live_owned_business_projection_changed')
        for projection,run in zip(owned,metadata):
            verify_owned_business_projection(projection,run,approved=approved_projection)
    # Historical apply receipts keep their actual eight-field schema. Current
    # native gates explicitly require independent, Git-protected approval.
    return actual if 'owned_business_projection' in recorded else {k:v for k,v in actual.items() if k!='owned_business_projection'}


def collect_identity_projection(cursor):
    """Read name/role/concept/Media support from the same active database."""
    cursor.execute('''select s.raw_value,s.role_hint,s.work_context_key,e.concept_id,b.media_id,r.source_work_id
        from blombooru_source_concept_product_media_bindings b
        join blombooru_source_concept_product_runs p on p.id=b.product_run_id
        join blombooru_source_concept_evidence e on e.id=b.evidence_id
        join blombooru_source_concept_signals s on s.id=e.signal_id
        join blombooru_source_metadata_records r on r.id=b.source_metadata_record_id
        where p.source_mode='production_scope' and p.status='active'
          and b.source_revision=r.binding_revision''')
    return [list(row) for row in cursor.fetchall()]


def verify_identity_projection(quality, actual):
    from collections import Counter
    reported=quality.get('projection_rows')
    if (not isinstance(actual,list) or not actual or not isinstance(reported,list)
        or any(not isinstance(r,(list,tuple)) or len(r)!=6 for r in [*reported,*actual])
        or Counter(map(tuple,reported))!=Counter(map(tuple,actual))):
        raise ValueError('a2_live_identity_projection_changed')
    return actual


def recompute_quality(quality, oracle, *, suggestion_oracle=None, creator_oracle=None, baseline=None,recall_baseline=None,launch=None,creator_projection=None,identity_projection=None):
    if launch is not None:
        from scripts.production_pixiv_a2_service_evidence import verify_quality_service
        verify_quality_service(quality,launch)
    from app.services.source_metadata_registry_service import canonical_source_key as key
    projection=quality.get('projection_rows')
    if identity_projection is not None:
        projection=verify_identity_projection(quality,identity_projection)
    if not isinstance(projection,list) or not projection:
        raise ValueError('a2_raw_quality_projection_required')
    names={}
    for row in projection:
        raw,role,context,concept,media,work=row
        entry=names.setdefault(key(raw),{'concepts':set(),'media':set()})
        entry['concepts'].add(concept);entry['media'].add(media)
    expected_pairs={tuple(sorted(key(n) for n in row['names'])):row['expected'] for row in oracle['identity_pairs']}
    independent={tuple(sorted(key(n) for n in row['names'])):row
        for row in oracle.get('separation_controls',[])}
    precision={tuple(sorted(key(n) for n in row['names'])):row
        for row in oracle.get('identity_precision_controls',[])}
    for pair,control in precision.items():
        forbidden=control.get('forbidden_media_ids')
        if (expected_pairs.get(pair)!='must_link' or not control.get('source_evidence')
            or not isinstance(forbidden,list) or not forbidden or len(set(forbidden))!=len(forbidden)
            or any(type(mid) is not int or mid<=0 for mid in forbidden)):
            raise ValueError('a2_independent_identity_precision_invalid')
    if launch is not None and any(v=='must_link' and pair not in precision for pair,v in expected_pairs.items()):
        raise ValueError('a2_independent_identity_precision_required')
    if any(v=='cannot_link' for v in expected_pairs.values()) and not independent:
        raise ValueError('a2_independent_separation_controls_required')
    for pair,control in independent.items():
        if (expected_pairs.get(pair)!='cannot_link' or not control.get('source_evidence')
            or set(control.get('exclusive_media',{}))!=set(pair)
            or not all(control['exclusive_media'].values())
            or any(type(mid) is not int or mid<=0 for values in control['exclusive_media'].values() for mid in values)
            or set(control['exclusive_media'][pair[0]]) & set(control['exclusive_media'][pair[1]])):
            raise ValueError('a2_independent_separation_control_invalid')
    def case_identity(case):
        if 'expected' in case and 'names' in case:
            return ('identity',*sorted(key(n) for n in case['names']))
        category=case['category']
        if category=='accepted_search_equivalence_only':return ('search_family',case['accepted_family_id'])
        if category in {'media_set_AND','media_set_negative'}:return (category,*map(key,case['names']))
        if category.startswith('suggestion_'):return ('suggestion',case['media_id'],case['kind'])
        if 'expected_account_union_media_ids' in case:return ('creator',case['query'])
        raise ValueError('a2_quality_case_recomputation_unavailable')
    actual_case_ids=[case_identity(c) for c in quality['cases']]
    if len(set(actual_case_ids))!=len(actual_case_ids):raise ValueError('a2_quality_case_duplicate')
    required_cases={case_identity(c) for c in (baseline or {}).get('cases',[])}
    required_cases.update(('search_family',r['family_id']) for r in oracle.get('search_only_families',[]))
    required_cases.update(('creator',r['query']) for r in (creator_oracle or {}).get('selected_families',[]))
    required_cases.update(('suggestion',r['media_id'],kind) for r in (suggestion_oracle or {}).get('samples',[])
        for kind in ('suggested_positive','suggested_negative','accepted_positive_control'))
    if not required_cases<=set(actual_case_ids):raise ValueError('a2_quality_case_missing')
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
            frozen=frozen_identity_recall(pair,decision,baseline,recall_baseline=recall_baseline)
            desired=[want|old for want,old in zip(desired,frozen)]
            passed=all(s['media'] for s in sides) and all(want<=got for want,got in zip(desired,actual))
            if decision=='must_link':
                passed=passed and bool(shared)
                if pair in precision:
                    forbidden=set(precision[pair]['forbidden_media_ids'])
                    passed=passed and all(not got&forbidden for got in actual)
            elif decision=='cannot_link':
                left,right=map(quote,pair)
                # Distinct identities can co-occur or match old tags. Check the
                # actual query exclusion/intersection behavior without assuming
                # their ordinary mixed-search Media sets must be disjoint.
                compositions=((left+' -'+right,actual[0]-actual[1]),
                    (right+' -'+left,actual[1]-actual[0]),(left+' '+right,actual[0]&actual[1]))
                passed=passed and not shared and all(ids(q)==want for q,want in compositions)
                if pair in independent:
                    exclusive=independent[pair]['exclusive_media']
                    # The negative expectation is frozen from source evidence,
                    # never derived from the very A/B queries being tested.
                    passed=passed and all(set(exclusive[pair[i]])<=actual[i]
                        and not set(exclusive[pair[i]])&actual[1-i] for i in (0,1))
        elif category in {'media_set_AND','media_set_negative'}:
            left,right=map(quote,case['names']);a,b=ids(left),ids(right)
            desired=a&b if category=='media_set_AND' else a-b
            actual=ids(left+' '+('' if category=='media_set_AND' else '-')+right)
            passed=desired==actual
        elif category=='accepted_search_equivalence_only':
            family=next(r for r in oracle['search_only_families'] if r['family_id']==case['accepted_family_id'])
            previous=next((r for r in (baseline or {}).get('cases',[]) if case_identity(r)==case_identity(case)),None)
            sample_ids=[r['media_id'] for r in case['samples']]
            if (len(set(sample_ids))!=len(sample_ids) or previous is not None
                and set(sample_ids)!={r['media_id'] for r in previous['samples']}):
                raise ValueError('a2_frozen_search_samples_changed')
            checks=[[ids(f'id:{s["media_id"]} '+quote(n)) for n in family['names']] for s in case['samples']]
            revisions=family.get('sample_expectation_revisions',[])
            revised={r['media_id']:r for r in revisions}
            if len(revised)!=len(revisions) or any(
                not r.get('approval') or not r.get('source_evidence') or r.get('previous_expected_ids')!=[r['media_id']]
                or r.get('expected_ids')!=[] or r['media_id'] not in sample_ids for r in revisions):
                raise ValueError('a2_search_sample_revision_invalid')
            passed=bool(checks) and all(all(x==set(revised.get(sample['media_id'],{}).get('expected_ids',[sample['media_id']])) for x in row)
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
            accounts=case['accounts']
            account_key=lambda r:(r.get('provider','pixiv'),r['provider_creator_id'])
            source={account_key(r):set(r['expected_media_ids']) for r in family['creators']}
            if (len(source)!=len(family['creators']) or len(accounts)!=len(source)
                or {account_key(r) for r in accounts}!=set(source)):
                raise ValueError('a2_creator_account_missing_or_duplicate')
            raw=creator_projection if creator_projection is not None else quality.get('creator_projection_rows')
            if not isinstance(raw,list) or not raw:raise ValueError('a2_creator_raw_projection_required')
            actual_accounts={identity:{'concepts':set(),'media':set()} for identity in source}
            artist_support={(row[3],row[4]) for row in projection if row[1]=='artist'}
            for row in raw:
                identity=account_key(row)
                if identity not in source:continue
                if (row['concept_id'],row['media_id']) not in artist_support:
                    raise ValueError('a2_creator_projection_support_changed')
                actual_accounts[identity]['concepts'].add(row['concept_id'])
                actual_accounts[identity]['media'].add(row['media_id'])
            concepts=[actual_accounts[account_key(a)]['concepts'] for a in accounts]
            for account in accounts:
                identity=account_key(account);actual_account=actual_accounts[identity]
                missing=source[identity]-actual_account['media']
                if (set(account['concept_ids'])!=actual_account['concepts']
                    or set(account['bound_media_ids'])!=actual_account['media']
                    or missing!=set(account['missing_bound_media_ids'])):
                    raise ValueError('a2_creator_support_summary_changed')
            actual=ids(quote(case['query']));expected=set(case['expected_account_union_media_ids'])
            passed=(expected==actual and queries[quote(case['query'])].get('total')==len(expected)
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
    if not set(independent)<=seen:raise ValueError('a2_independent_separation_case_missing')
    return {'case_count':len(results),'failed_cases':sum(not r for r in results),
            'independent_identity_precision_control_count':len(precision),
            'independent_separation_control_count':len(independent),
            'categories':dict(Counter(c['category'] for c in quality['cases']))}


def recompute_workload(workload, baseline, frozen_cases, *, launch, system_identifier=None, source_results=None):
    """Bind the accepted HTTP and three source-layer passes before statistics."""
    import json
    from urllib.parse import urlsplit,parse_qs
    from scripts.production_pixiv_a2_service_evidence import service_origin,verify_service_observation
    def origin(value):
        try:return service_origin(value)
        except ValueError as error:raise ValueError('a2_workload_service_origin') from error
    try:expected_origin=verify_service_observation(workload,launch)
    except ValueError as error:raise ValueError('a2_workload_'+str(error).removeprefix('a2_')) from error
    expected={row['case_id']:row for row in frozen_cases}
    if not expected or len(expected)!=len(frozen_cases):raise ValueError('a2_frozen_workload_duplicate')
    def indexed(rows):
        result={row['case_id']:row for row in rows}
        if len(result)!=len(rows) or set(result)!=set(expected):raise ValueError('a2_workload_case_coverage')
        for identity,row in result.items():
            case=expected[identity]
            if row['terms']!=case['terms'] or row['category']!=case['category']:
                raise ValueError('a2_workload_case_identity')
            query=' '.join(json.dumps(term,ensure_ascii=False) for term in case['terms'])
            if row['query']!=query or row['status_code']!=200:raise ValueError('a2_workload_query_identity')
        return result
    indexed(baseline['queries'])
    actual=indexed(workload['queries'])
    for row in actual.values():
        request=urlsplit(row['request_url'])
        if origin(row['request_url'])!=expected_origin or request.path!='/api/search' or parse_qs(request.query)!= {'q':[row['query']],'limit':['64']}:
            raise ValueError('a2_workload_request_parameters')
    # The observed service gate above natively proves a fixed runtime's exact
    # business checkout, complete application bytes and sealed profile. Source
    # timings retain that checkout's actual business HEAD and execution root.
    source_root=Path(launch['fixed_runtime_binding']['business_root']
        if 'fixed_runtime_binding' in launch else launch['code_root'])
    source=workload['source_layer_measurements'];seen=set()
    from app.services.pixiv_metadata_projection_service import canonical_fingerprint
    import math
    for row in source:
        identity=(row['case_id'],row['repeat'])
        if identity in seen or row['case_id'] not in expected:raise ValueError('a2_workload_source_coverage')
        seen.add(identity)
        if (row['terms']!=expected[row['case_id']]['terms'] or row['include_needs_review'] is not False
            or row['include_evidence_fallback'] is not True
            or row.get('include_production_alias_evidence') is not True):raise ValueError('a2_workload_source_parameters')
        execution=row.get('execution',{})
        if (execution.get('candidate_head')!=workload['candidate_head'] or execution.get('database')!=workload['database']
            or not execution.get('system_identifier') or (system_identifier is not None and execution['system_identifier']!=system_identifier)
            or not recorded_code_root_matches(execution.get('code_root'),source_root)
            or not execution.get('python_executable') or type(execution.get('pid')) is not int or execution['pid']<=0):
            raise ValueError('a2_workload_source_execution_identity')
        start=row.get('started_perf_ns');end=row.get('finished_perf_ns');ms=row.get('ms')
        if (type(start) is not int or type(end) is not int or not 0<=start<end
            or not isinstance(ms,(int,float)) or not math.isfinite(ms)
            or abs(ms-(end-start)/1000000)>1e-9):
            raise ValueError('a2_workload_source_clock')
        ids=row.get('ids')
        if (not isinstance(ids,list) or any(type(mid) is not int or mid<=0 for mid in ids)
            or ids!=sorted(set(ids)) or row.get('result_fingerprint')!=canonical_fingerprint(ids)
            or (source_results is not None and ids!=source_results.get(row['case_id']))):
            raise ValueError('a2_workload_source_result')
    if seen!={(case_id,repeat) for case_id in expected for repeat in range(3)}:
        raise ValueError('a2_workload_source_coverage')
    return latency_statistics(source),latency_statistics(workload['queries'])


def verify_forward_metadata_spacing(journal_bytes, historical_timing):
    """The accepted journal digest marks the immutable historical prefix."""
    import hashlib,json
    from datetime import datetime
    lines=journal_bytes.splitlines(keepends=True);digest=hashlib.sha256();boundary=None
    for index,line in enumerate(lines):
        digest.update(line)
        if digest.hexdigest()==historical_timing['journal_sha256']:
            boundary=index+1;break
    if boundary is None:raise ValueError('a2_historical_metadata_journal_changed')
    historical=[json.loads(line) for line in lines[:boundary]]
    dispatch=[r for r in historical if r['event']=='dispatch']
    if len(dispatch)!=historical_timing['total_acquisition_commands']:
        raise ValueError('a2_historical_metadata_dispatch_count')
    previous=datetime.fromisoformat(dispatch[-1]['at']) if dispatch else None
    if previous is not None and previous.tzinfo is None:raise ValueError('a2_metadata_dispatch_timezone')
    gaps=[]
    for line in lines[boundary:]:
        row=json.loads(line)
        if row['event']!='dispatch':continue
        current=datetime.fromisoformat(row['at'])
        if current.tzinfo is None:raise ValueError('a2_metadata_dispatch_timezone')
        if previous is not None:
            gap=(current-previous).total_seconds()
            if gap<2:raise ValueError('a2_forward_metadata_dispatch_spacing')
            gaps.append(gap)
        previous=current
    return {'historical_dispatch_count':len(dispatch),'forward_dispatch_count':sum(
        json.loads(line)['event']=='dispatch' for line in lines[boundary:]),
        'minimum_forward_dispatch_gap_seconds':min(gaps) if gaps else None,
        'observed_boundary':'metadata_command_dispatch','provider_internal_http_intervals_claimed':False}
