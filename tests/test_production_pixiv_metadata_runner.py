import json
import subprocess

import pytest

from scripts.run_production_pixiv_a2_metadata import recover_raw_payloads, publish_raw_response


def payload(work=123456789):
    return json.dumps([[3, 'url', {'id': work, 'num': 0, 'title': 'Work',
                                'user': {'id': 42, 'name': 'Display'}}]])


@pytest.mark.parametrize('content,returncode,expected', [
    ('[', 0, False), (payload(987654321), 0, False),
    (payload(), 1, False), (payload(), 0, True), ('', 0, False),
])
def test_only_valid_success_is_published_for_replay(tmp_path, content, returncode, expected):
    raw = tmp_path / 'metadata-raw'; raw.mkdir()
    path, valid = publish_raw_response(raw, '123456789', 1,
        subprocess.CompletedProcess([], returncode, stdout=content))
    assert valid is expected
    assert path.read_text() == content
    assert (path.suffix == '.json') is expected
    dispatch = {'event': 'dispatch', 'work_id': '123456789', 'attempt': 1, 'scope': 'scope'}
    attempts, cache = recover_raw_payloads(tmp_path, [dispatch], 'scope')
    assert attempts['123456789'] == 1
    assert bool(cache) is expected  # crash before returned journal line


def test_failed_or_truncated_old_raw_cannot_override_valid_earlier_payload(tmp_path):
    raw = tmp_path / 'metadata-raw'; raw.mkdir()
    events = []
    for attempt, content in [(1, payload()), (2, payload()), (3, '[truncated')]:
        path = raw / f'123456789-attempt-{attempt}.json'; path.write_text(content)
        events.append({'event': 'dispatch', 'work_id': '123456789', 'attempt': attempt, 'scope': 'scope'})
        if attempt == 2:
            events.append({'event': 'returned', 'work_id': '123456789', 'attempt': attempt,
                           'returncode': 1, 'stdout': str(path.relative_to(tmp_path))})
    attempts, cache = recover_raw_payloads(tmp_path, events, 'scope')
    assert attempts['123456789'] == 3
    assert cache['123456789'].name == '123456789-attempt-1.json'


@pytest.mark.parametrize('module,action,extra', [
    ('scripts.run_production_pixiv_a2_metadata', 'acquire',
     ['--database', 'unused', '--expected-system-id', 'unused']),
    ('scripts.run_production_pixiv_a2_concepts', 'roles',
     ['--aggregates', 'unused', '--vocabulary', 'unused', '--label', 'unused']),
])
def test_negative_limit_fails_before_preflight_or_file_io(monkeypatch, module, action, extra):
    import importlib
    monkeypatch.setattr('sys.argv', [module, action, '--artifacts', 'missing', '--profile', 'missing',
        '--expected-python', 'missing', '--limit', '-1', *extra])
    with pytest.raises(SystemExit) as exc:
        importlib.import_module(module).main()
    assert exc.value.code == 2
