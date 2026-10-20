#!/usr/bin/env python3
"""Robot 7 integration for Jenkins. Parsing does not execute tests or libraries.

Credentials are read only from OMNIQA_KEY, never written to plan/result files.
Commands: catalog, prepare, report, resend. Run in the checked-out test repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def dump(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def git_output(root, *arguments):
    executable = os.environ.get('OMNIQA_GIT_EXECUTABLE', '').strip() or 'git'
    try:
        return subprocess.check_output([executable, '-C', str(root), *arguments], text=True, encoding='utf-8')
    except FileNotFoundError as exc:
        raise RuntimeError('Git executable was not found. Set OMNIQA_GIT_EXECUTABLE to the Jenkins Git tool path '
                           'or add Git to the Jenkins agent PATH.') from exc


def revision(root):
    value = git_output(root, 'rev-parse', '--verify', 'HEAD').strip()
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value):
        raise ValueError('Cannot determine Git revision')
    # Uncommitted scripts must not be reported as the committed revision.
    dirty = git_output(root, 'status', '--porcelain', '--untracked-files=all', '--', 'tests', 'resources')
    if dirty.strip():
        # Jenkins generates this device config before Robot. It is deliberately
        # ignored in the test repository and therefore absent from this output.
        raise ValueError('Tracked tests/resources differ from the Git commit')
    return value


def tests_in(suite):
    yield from suite.tests
    for child in suite.suites:
        yield from tests_in(child)


def case_tag(tags):
    values = [str(t) for t in tags if str(t).lower().startswith('zentao:')]
    if len(values) > 1 or any(not re.fullmatch(r'zentao:[1-9][0-9]*', t, re.I) for t in values):
        raise ValueError('Each test needs at most one exact zentao:<case ID> tag')
    return int(values[0].split(':')[1]) if values else None


def catalog(root, branch, *, verify_git=True):
    from robot.running.builder import TestSuiteBuilder
    root = Path(root).resolve()
    suite = TestSuiteBuilder().build(str(root / 'tests'))
    rows, seen, auto_seen = [], set(), set()
    for test in tests_in(suite):
        source = Path(test.source).resolve()
        relative = source.relative_to(root).as_posix()
        if any(x.startswith(('_', '.')) for x in Path(relative).parts):
            continue
        tags = [str(x) for x in test.tags]
        cid = case_tag(tags)
        identities = [x.split(':', 1)[1] for x in tags if x.lower().startswith('autoid:')]
        if len(identities) > 1:
            raise ValueError('Duplicate autoid tags')
        aid = identities[0] if identities else 'robot:' + hashlib.sha256((relative + '::' + test.name).encode()).hexdigest()
        if not aid or aid in auto_seen or (cid and cid in seen):
            raise ValueError('Duplicate automation identity or Zentao case ID')
        auto_seen.add(aid)
        if cid:
            seen.add(cid)
        rows.append({'case_id': cid, 'automation_id': aid, 'name': test.name, 'path': relative, 'tags': tags})
    return {'revision': revision(root) if verify_git else '0' * 40, 'branch': branch, 'cases': rows}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_api(method, path, data=None):
    base = os.environ.get('OMNIQA_URL', '').rstrip('/')
    key = os.environ.get('OMNIQA_KEY', '')
    parts = urlsplit(base)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or not key:
        raise ValueError('Set OMNIQA_URL and Jenkins Credentials OMNIQA_KEY')
    if not path.startswith('/automation/'):
        raise ValueError('Invalid integration path')
    # OMNIQA_URL is the site root, with any reverse-proxy context prefix.
    req = urllib.request.Request(base + path, method=method,
           data=json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if data is not None else None,
           headers={'X-Automation-Key': key, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Platform integration returned HTTP {exc.code}; inspect the archived payload and platform run') from None


def repository_id():
    value = os.environ.get('OMNIQA_REPOSITORY_ID', '')
    if not value.isdigit() or int(value) < 1:
        raise ValueError('Set OMNIQA_REPOSITORY_ID')
    return int(value)


def build_identity(rev, device):
    number = os.environ.get('BUILD_NUMBER', '')
    job = os.environ.get('OMNIQA_JENKINS_JOB') or os.environ.get('JOB_NAME', '')
    if not number.isdigit() or not job:
        raise ValueError('This command needs a Jenkins JOB_NAME and BUILD_NUMBER')
    return {'job_name': job, 'build_number': int(number), 'revision': rev, 'device_serial': device}


def make_arguments(plan, snapshot, root, output_dir):
    if snapshot['revision'] != plan['revision']:
        raise ValueError('Checked-out Git revision differs from the frozen run plan')
    indexed = {x['case_id']: x for x in snapshot['cases'] if x['case_id']}
    selected = plan['plan']
    if not selected:
        raise ValueError('No cases selected; refusing to run the whole tests directory')
    lines = ['--outputdir', str(Path(output_dir).resolve())]
    if plan.get('dry_run'):
        lines.append('--dryrun')
    for case in selected:
        actual = indexed.get(case['case_id'])
        if not actual or any(actual[k] != case[k] for k in ('automation_id', 'path', 'name')):
            raise ValueError('Robot catalogue differs from the frozen case mapping')
        lines += ['--include', 'zentao:' + str(case['case_id'])]
    lines.append(str(Path(root).resolve() / 'tests'))
    return '\n'.join(lines) + '\n'


def prepare(args):
    rid = repository_id()
    root = Path(args.root).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot = catalog(root, args.branch)
    prefix = f'/automation/integrations/{rid}'
    run_id = args.run_id
    device = args.device
    identity = build_identity(snapshot['revision'], device)
    if not run_id:
        case_ids = [int(x) for x in args.cases.split(',') if x.strip()]
        if not case_ids or not args.testtask:
            raise ValueError('External runs require ZENTAO_CASE_IDS and ZENTAO_TESTTASK_ID')
        request_api('POST', prefix + '/catalog', snapshot)
        plan = request_api('POST', prefix + '/runs/register', identity | {
            'case_ids': case_ids, 'testtask_id': args.testtask, 'device_serial': device,
            'apk_reference': args.apk, 'idempotency_key': 'jenkins_' + hashlib.sha256((identity['job_name'] + ':' + str(identity['build_number'])).encode()).hexdigest(),
            'parameters': {}, 'source': args.source, 'dry_run': os.environ.get('OMNIQA_DRY_RUN', '').lower() == 'true'})
        run_id = plan['id']
    else:
        plan = request_api('GET', prefix + f'/runs/{run_id}/plan')
    if plan['device_serial'] != device:
        raise ValueError('Device differs from the run plan')
    lines = make_arguments(plan, snapshot, root, output)
    dump(output / 'automation-plan.json', {'repository_id': rid, 'run': plan, 'build': identity})
    (output / 'robot_args.txt').write_text(lines, encoding='utf-8')
    (output / 'selected_test_files.txt').write_text('\n'.join(sorted({x['path'] for x in plan['plan']})) + '\n', encoding='utf-8')
    request_api('POST', prefix + f'/runs/{run_id}/started', identity)
    print('Automation plan ready: ' + run_id)


def keyword_steps(test, cid):
    found = {}

    def walk(items):
        for item in items:
            for tag in (getattr(item, 'tags', ()) if getattr(item, 'type', '') == 'KEYWORD' else ()):
                match = re.fullmatch(r'zentao-step:([1-9][0-9]*):([1-9][0-9]*)', str(tag), re.I)
                if match and int(match[1]) == cid:
                    sid = int(match[2])
                    if sid in found:
                        raise ValueError('A Zentao step was executed more than once; an explicit retry policy is required')
                    status = str(getattr(item, 'status', 'NOT RUN'))
                    found[sid] = {'step_id': sid, 'status': status if status in {'PASS', 'FAIL', 'SKIP'} else 'SKIP',
                                  'message': str(getattr(item, 'message', ''))[:4000]}
            walk(getattr(item, 'body', ()))
    walk(test.body)
    return list(found.values())


def parse_results(xml_path, plan):
    from robot.api import ExecutionResult
    result = ExecutionResult(str(xml_path))
    expected = {x['case_id'] for x in plan['plan']}
    rows, seen = [], set()
    for test in tests_in(result.suite):
        cid = case_tag(test.tags)
        if cid not in expected or cid in seen:
            raise ValueError('Robot output contains unselected, unmapped or duplicate cases')
        seen.add(cid)
        status = test.status
        if status not in {'PASS', 'FAIL', 'SKIP'}:
            status = 'ERROR'
        node = test
        while node is not None:
            if any(getattr(node, phase, None) and getattr(node, phase).status == 'FAIL' for phase in ('setup', 'teardown')):
                status = 'ERROR'
            node = getattr(node, 'parent', None)
        rows.append({'case_id': cid, 'status': status, 'duration_seconds': test.elapsed_time.total_seconds(),
                     'message': test.message[:30000], 'started_at': test.start_time.isoformat() if test.start_time else None,
                     'finished_at': test.end_time.isoformat() if test.end_time else None, 'steps': keyword_steps(test, cid)})
    if seen != expected:
        raise ValueError('Robot report is missing selected cases')
    return rows


def report(args):
    output = Path(args.output_dir).resolve()
    saved = json.loads((output / 'automation-plan.json').read_text(encoding='utf-8'))
    plan, rid = saved['run'], saved['repository_id']
    xml = output / 'output.xml'
    payload = saved['build'] | {'outcome': 'completed' if xml.exists() else 'environment_error', 'dry_run': plan.get('dry_run', False),
              'message': '' if xml.exists() else 'Robot output.xml was not generated', 'results': []}
    if xml.exists():
        try:
            payload['results'] = parse_results(xml, plan)
        except Exception as exc:
            payload.update(outcome='environment_error', results=[],
                           message=f'Robot report could not be reconciled with the run plan ({type(exc).__name__})')
    apk = Path(args.apk) if args.apk else None
    if not apk and os.environ.get('WORKSPACE'):
        candidates = list((Path(os.environ['WORKSPACE']) / os.environ.get('APK_DIR', 'apk_in')).glob('*.apk'))
        if len(candidates) == 1:
            apk = candidates[0]
    if apk and apk.is_file():
        hasher = hashlib.sha256()
        with apk.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(chunk)
        payload['apk_sha256'] = hasher.hexdigest()
    archived = {'repository_id': rid, 'run_id': plan['id'], 'payload': payload}
    dump(output / 'automation-result.json', archived)
    request_api('POST', f'/automation/integrations/{rid}/runs/{plan["id"]}/results', payload)
    print('Automation results received by platform: ' + plan['id'])


def device_action(args):
    saved = json.loads((Path(args.output_dir) / 'automation-plan.json').read_text(encoding='utf-8'))
    rid, run = saved['repository_id'], saved['run']
    request_api('POST', f'/automation/integrations/{rid}/runs/{run["id"]}/device/{args.action}', {})
    print('Device ' + args.action + ' completed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    cat = commands.add_parser('catalog')
    cat.add_argument('--root', default='.')
    cat.add_argument('--branch', default=os.environ.get('TEST_BRANCH', 'main'))
    cat.add_argument('--output', default='automation-catalog.json')
    cat.add_argument('--publish', action='store_true')
    prep = commands.add_parser('prepare')
    prep.add_argument('--root', default='.')
    prep.add_argument('--output-dir', required=True)
    prep.add_argument('--branch', default=os.environ.get('TEST_BRANCH', 'main'))
    prep.add_argument('--run-id', default=os.environ.get('AUTOMATION_RUN_ID', ''))
    prep.add_argument('--cases', default=os.environ.get('ZENTAO_CASE_IDS', ''))
    prep.add_argument('--testtask', type=int, default=int(os.environ.get('ZENTAO_TESTTASK_ID') or 0))
    prep.add_argument('--device', default=os.environ.get('DEVICE_ID', ''))
    prep.add_argument('--apk', default=os.environ.get('APK_PATH', ''))
    prep.add_argument('--source', choices=['jenkins', 'zentao'], default=os.environ.get('AUTOMATION_SOURCE', 'jenkins'))
    rep = commands.add_parser('report')
    rep.add_argument('--output-dir', required=True)
    rep.add_argument('--apk', default='')
    resend = commands.add_parser('resend')
    resend.add_argument('result_file')
    device = commands.add_parser('device')
    device.add_argument('action', choices=['lock', 'unlock', 'heartbeat'])
    device.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    if args.command == 'catalog':
        value = catalog(args.root, args.branch)
        dump(args.output, value)
        if args.publish:
            request_api('POST', f'/automation/integrations/{repository_id()}/catalog', value)
        print(f'Catalogue: {len(value["cases"])} tests; revision {value["revision"]}')
    elif args.command == 'prepare':
        prepare(args)
    elif args.command == 'report':
        report(args)
    elif args.command == 'device':
        device_action(args)
    else:
        value = json.loads(Path(args.result_file).read_text(encoding='utf-8'))
        if value['repository_id'] != repository_id():
            raise ValueError('Result file belongs to another project')
        request_api('POST', f'/automation/integrations/{value["repository_id"]}/runs/{value["run_id"]}/results', value['payload'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
