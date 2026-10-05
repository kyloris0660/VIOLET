"""Record actual collection and per-node outcomes for the authorized full run."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

_nodes=[]
_reports=[]
_collection=[]
_started=None
_path=None
_journal=None


def _snapshot(status,exit_code=None):
    data={'schema_version':'violet.a2.pytest-node-inventory.v1','status':status,'started_at':_started,
        'finished_at':datetime.now(timezone.utc).isoformat() if status=='finished' else None,
        'exit_code':exit_code,'collected_nodeids':_nodes,'reports':_reports,'collection_outcomes':_collection,
        'collected_sha256':hashlib.sha256(json.dumps(_nodes,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'report_journal':_journal.name,'report_journal_sha256':hashlib.sha256(_journal.read_bytes()).hexdigest()}
    temporary=_path.with_suffix(_path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,_path)


def pytest_sessionstart(session):
    global _started,_path,_journal
    _started=datetime.now(timezone.utc).isoformat()
    root=Path(__file__).resolve().parents[1]
    _path=Path(os.environ['VIOLET_A2_TEST_INVENTORY']).resolve()
    if not _path.is_relative_to((root/'.local_manifests/pixiv-a2').resolve()):
        raise RuntimeError('a2_pytest_inventory_outside_private_root')
    _journal=_path.with_suffix(_path.suffix+'.reports.jsonl')
    with _path.open('x',encoding='utf-8') as f:f.write('{}')
    with _journal.open('x',encoding='utf-8'):pass
    _snapshot('running')


def pytest_collection_finish(session):
    _nodes.extend(item.nodeid for item in session.items)
    for item in session.items:
        item.user_properties.append(('a2_nodeid',item.nodeid))
    _snapshot('collected')


def pytest_collectreport(report):
    if report.outcome!='passed':
        _collection.append({'nodeid':report.nodeid,'outcome':report.outcome,'longrepr':str(report.longrepr)})


def pytest_runtest_logreport(report):
    row={'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,
        'wasxfail':getattr(report,'wasxfail',None)}
    _reports.append(row)
    with _journal.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')


def pytest_sessionfinish(session,exitstatus):
    _snapshot('finished',int(exitstatus))
