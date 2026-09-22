"""Bind actual A2 observations and query pages to their controlled service."""
import ipaddress
import math
import re
from pathlib import Path
from urllib.parse import parse_qs,urlsplit


def service_origin(value, *, fragment=False):
    try:
        parsed=urlsplit(value)
        loopback=parsed.hostname=='localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
        port=parsed.port or (443 if parsed.scheme=='https' else 80)
    except (TypeError,ValueError):
        raise ValueError('a2_service_origin') from None
    if (not loopback or parsed.scheme not in {'http','https'} or parsed.username is not None
        or parsed.password is not None or (parsed.fragment and not fragment)):
        raise ValueError('a2_service_origin')
    return parsed.scheme,parsed.hostname,port


def verify_service_observation(observation,launch):
    expected=service_origin(launch.get('base_url',''))
    for base in (launch.get('base_url',''),observation.get('base_url','')):
        if service_origin(base)!=expected or urlsplit(base).path not in {'','/'} or urlsplit(base).query:
            raise ValueError('a2_service_origin')
    before=observation.get('server_identity',{});after=observation.get('server_identity_after',{})
    pid=launch.get('after_pid',launch.get('identity_pid'))
    candidate=launch.get('candidate_head','');observed=before.get('git_sha','')
    code_root=launch.get('code_root') or launch.get('server_identity',{}).get('code_root')
    same_root=(isinstance(code_root,str) and Path(code_root).is_absolute()
        and isinstance(before.get('code_root'),str) and Path(before['code_root']).is_absolute()
        and Path(before['code_root']).resolve()==Path(code_root).resolve())
    keys=('pid','port','db_name','git_sha','code_root')
    if (type(pid) is not int or pid<=0 or before.get('pid')!=pid
        or not isinstance(candidate,str) or not re.fullmatch('[0-9a-f]{40}',candidate) or observation.get('candidate_head')!=candidate
        or not isinstance(observed,str) or not re.fullmatch('[0-9a-f]{7,40}',observed) or not candidate.startswith(observed)
        or before.get('port')!=expected[2] or not launch.get('database')
        or observation.get('database')!=launch['database'] or before.get('db_name')!=launch['database']
        or not same_root or any(before.get(k)!=after.get(k) for k in keys)):
        raise ValueError('a2_candidate_service_changed')
    launcher_identity=launch.get('server_identity')
    if launcher_identity and any(before.get(k)!=launcher_identity.get(k) for k in keys):
        raise ValueError('a2_launcher_service_changed')
    return expected


def verify_browser_service(browser,launch):
    expected=verify_service_observation(browser,launch)
    def url(value, *, fragment=False):
        if service_origin(value,fragment=fragment)!=expected:raise ValueError('a2_browser_service_origin')
        return urlsplit(value)
    pages=browser.get('pages',[])
    if not pages:raise ValueError('a2_browser_service_pages_missing')
    for page in pages:
        url(page.get('url',''),fragment=True)
        for item in page.get('images',[]):url(item.get('src',''))
    for action in browser.get('actions',[]):
        if 'url' in action:url(action['url'],fragment=True)
        if 'image' in action:
            image=action['image'];url(image.get('src',''))
            if (any(type(image.get(key)) not in (int,float) or not math.isfinite(image[key]) or image[key]<=0
                    for key in ('width','height','rendered_width','rendered_height'))
                or image.get('decoded') is not True or image.get('intersects_viewport') is not True):
                raise ValueError('a2_browser_original_not_rendered')
    chip=browser['source_chip']
    url(chip['href']);url(chip['navigated_url']);url(chip['search']['request_url'])
    search=browser['search'];old=browser['old_tag']
    for row in (search,old):
        page=url(row.get('url',''))
        query=parse_qs(page.query).get('q')
        request=url(row.get('request_url',''))
        if (page.path!='/' or request.path!='/api/search' or not query
            or parse_qs(request.query).get('q')!=query
            or row is old and (query!=[old.get('query')] or old.get('attempt_id')!=browser.get('attempt_id'))):
            raise ValueError('a2_browser_search_service_query')
    for name in ('suggestion_display','recovery_page'):
        row=browser[name];url(row.get('url',''),fragment=True);url(row.get('request_url',''))
    return {'same_candidate_service':True,'pid':browser['server_identity']['pid']}


def verify_quality_service(quality,launch):
    expected=verify_service_observation(quality,launch)
    collections=quality.get('collections',{})
    if not isinstance(collections,dict) or not collections:raise ValueError('a2_quality_collections_missing')
    for key,observation in collections.items():
        if not isinstance(key,str) or not key or observation.get('collection_id')!=key:
            raise ValueError('a2_quality_collection_identity')
        if verify_service_observation(observation,launch)!=expected:raise ValueError('a2_quality_collection_service')
    seen=set()
    for query,row in quality['queries'].items():
        collection=row.get('collection_id')
        if collection not in collections:raise ValueError('a2_quality_query_collection')
        seen.add(collection)
        pages=row.get('pages');total=row.get('total');observed=[]
        if (row.get('method')!='GET' or row.get('status_code')!=200 or type(total) is not int or total<0
            or not isinstance(pages,list) or len(pages)!=max(1,(total+255)//256)):
            raise ValueError('a2_quality_query_pages')
        for number,page in enumerate(pages,1):
            request=urlsplit(page.get('request_url',''))
            if (service_origin(page.get('request_url',''))!=expected or request.path!='/api/search'
                or parse_qs(request.query)!= {'q':[query],'limit':['256'],'page':[str(number)]}
                or page.get('page')!=number or page.get('status_code')!=200 or page.get('total')!=total
                or not isinstance(page.get('ids'),list) or len(page['ids'])>256
                or any(type(mid) is not int or mid<=0 for mid in page['ids'])):
                raise ValueError('a2_quality_query_service')
            observed.extend(page['ids'])
        if len(observed)!=total or len(set(observed))!=total or sorted(observed)!=row.get('ids'):
            raise ValueError('a2_quality_query_page_set')
    if seen!=set(collections):raise ValueError('a2_quality_unused_collection')
    return {'same_candidate_service':True,'collections':len(collections),'queries':len(quality['queries'])}
