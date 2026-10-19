"""Read-only integration probes using explicit ZENTAO_TEST_* environment values.

Requires ZENTAO_TEST_BASE_URL, ZENTAO_TEST_ACCOUNT, ZENTAO_TEST_PASSWORD.
Optional --manifest supplies object IDs from an isolated IPD 5.6 instance.
This script does not import app settings, load .env or use existing bindings.
Login/logout are the only session writes; no business object is changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from app.services.zentao_client_service import ZentaoAPIError, ZentaoClient
from app.services.zentao_v2_protocol import decode_v2, normalize_base_url
from app.services.zentao_web_session import ZentaoWebLogin, list_task_efforts_via_web


class ProbeClient(ZentaoClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = []

    def _request(self, method, path, **kwargs):
        self.trace.append({'transport': 'v2', 'method': method.upper(), 'path': path})
        return super()._request(method, path, **kwargs)

    def get_page(self, path, params=None):
        self.trace.append({'transport': 'retained_page', 'method': 'GET', 'path': path})
        return super().get_page(path, params)

    def get_file(self, file_id):
        self.trace.append({'transport': 'retained_file', 'method': 'GET', 'file_id': file_id})
        return super().get_file(file_id)


def probes(client, manifest: dict) -> dict:
    """Return evidence per capability; skipped/missing inputs never count as passed."""
    checks = []
    expected = manifest.get('expected_ids') or {}

    def check(name, operation, object_id=None, required=False):
        if not required and not object_id:
            checks.append({'name': name, 'status': 'skipped', 'reason': 'No test object ID supplied'})
            return
        trace = getattr(client, 'trace', [])
        trace = trace if isinstance(trace, list) else []
        start = len(trace)
        try:
            result = operation()
            evidence = {}
            if isinstance(result, list):
                ids = sorted(str(row['id']) for row in result)
                evidence = {'count': len(ids), 'ids': ids}
                if name in expected and ids != sorted(str(i) for i in expected[name]):
                    raise ValueError('Object IDs differ from the supplied baseline')
            elif isinstance(result, dict):
                evidence = {'keys': sorted(result)}
                if object_id and 'id' in result and str(result['id']) != str(object_id):
                    raise ValueError('Resource ID mismatch')
            else:
                raise ValueError('Missing or unexpected response')
            checks.append({'name': name, 'status': 'passed', **evidence})
        except Exception as exc:
            reason = exc.message if isinstance(exc, ZentaoAPIError) else type(exc).__name__
            checks.append({'name': name, 'status': 'failed', 'reason': reason,
                           'http_status': getattr(exc, 'status_code', None)})
        checks[-1]['requests'] = trace[start:]

    check('projects', client.list_projects, required=True)
    project, execution, product = (manifest.get(key) for key in ('project_id', 'execution_id', 'product_id'))
    check('project_executions', lambda: client.list_project_executions(int(project)), project)
    check('project_builds', lambda: client.list_project_builds(int(project)), project)
    check('execution_stories', lambda: client.list_execution_stories(int(execution)), execution)
    check('execution_tasks', lambda: client.list_execution_tasks(int(execution)), execution)
    check('execution_builds', lambda: client.list_execution_builds(int(execution)), execution)
    check('execution', lambda: client.get_execution(int(execution)), execution)
    check('product_testcases', lambda: client._list_all(f'products/{int(product)}/testcases', 'testcases', 100), product)
    check('product_bugs', lambda: client._list_all(f'products/{int(product)}/bugs', 'bugs', 100), product)
    check('product_systems', lambda: client.get(f'products/{int(product)}/systems'), product)
    for name, method in [('bug', client.get_bug_with_fallback), ('story', client.get_story),
                         ('testcase', client.get_testcase), ('task', client.get_task), ('build', client.get_build)]:
        object_id = manifest.get('case_id' if name == 'testcase' else name + '_id')
        check(name, lambda method=method, oid=object_id: method(int(oid)), object_id)
    task = manifest.get('task_id')
    check('task_efforts', lambda: list_task_efforts_via_web(client.web_login, int(task)), task)
    check('assignable_users', lambda: client.list_assignable_users(int(execution)), execution)
    check('bug_create_metadata', lambda: client.get_create_bug_meta(int(product), int(execution or 0)), product)
    check('testcase_create_metadata', lambda: client.get_create_testcase_meta(int(product)), product)
    file_id = manifest.get('file_id')
    def file_evidence():
        response = client.get_file(int(file_id))
        return {'byte_count': len(response.content), 'sha256': hashlib.sha256(response.content).hexdigest()}
    check('file_download', file_evidence, file_id)
    return {'checks': checks, 'summary': {status: sum(c['status'] == status for c in checks)
                                        for status in ('passed', 'failed', 'skipped')}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    values = {key: os.environ.get('ZENTAO_TEST_' + key, '') for key in ('BASE_URL', 'ACCOUNT', 'PASSWORD')}
    values['BASE_URL'], values['ACCOUNT'] = values['BASE_URL'].strip(), values['ACCOUNT'].strip()
    if not all(values.values()):
        parser.error('Set all three ZENTAO_TEST_BASE_URL, ZENTAO_TEST_ACCOUNT, ZENTAO_TEST_PASSWORD explicitly')
    base = normalize_base_url(values['BASE_URL'])
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig')) if args.manifest else {}
    report = {'target': base, 'declared_target_version': 'IPD 5.6 (not automatically verified)',
              'started_at': datetime.now(timezone.utc).isoformat(), 'business_writes': False}
    try:
        login = httpx.post(f'{base}/api.php/v2/users/login',
                           json={'account': values['ACCOUNT'], 'password': values['PASSWORD']},
                           timeout=15, follow_redirects=False)
        token = decode_v2(login).get('token')
        if not isinstance(token, str) or not token.strip():
            raise ZentaoAPIError(502, 'v2 login did not return a token')
        client = ProbeClient(base, token, account=values['ACCOUNT'],
                              web_login=ZentaoWebLogin(base, values['ACCOUNT'], values['PASSWORD']))
        report.update(probes(client, manifest))
        report['login'] = 'passed'
    except Exception as exc:
        report.update(login='failed', reason=exc.message if isinstance(exc, ZentaoAPIError) else type(exc).__name__)
    output = args.output or ROOT / '.codex-tmp' / ('zentao-v2-live-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Read-only report:', output)
    print(json.dumps(report.get('summary', {'login': report['login']})))
    return 1 if report['login'] != 'passed' or report['summary']['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
