"""Local Jenkins protocol adapter. Executes Robot --dryrun only, never a device.

This is a local acceptance fixture, not a Jenkins installation or deployment.
"""
from __future__ import annotations

import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from local_platform import ROOT, WORK  # sibling launcher sets the isolated environment

from app.services.zentao_auth_service import decrypt_password

CONFIG = json.loads((WORK / 'runtime.json').read_text(encoding='utf-8'))
KEY = decrypt_password(CONFIG['integration_key_encrypted'])
TOKEN = decrypt_password(CONFIG['broker_token_encrypted'])
REPO = ROOT / '.codex-tmp/surveymaster-integration-20261019'
PYTHON = Path(os.environ.get('LOCAL_ROBOT_PYTHON', r'D:\SurveyMasterAutoTesting\.venv\Scripts\python.exe'))
JOB = '/job/SurveyMaster-RF-Test/'
BASE = 'http://127.0.0.1:18766'
LOCK = threading.Lock()
BUILDS = {}
for previous in (WORK / 'builds').glob('*/build.json'):
    row = json.loads(previous.read_text(encoding='utf-8'))
    if row['building']:
        row.update(building=False, result='ABORTED')
    BUILDS[int(previous.parent.name)] = row


def run_build(number):
    row = BUILDS[number]
    output = WORK / 'builds' / str(number) / 'results'
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ | row['params'] | {
        'OMNIQA_URL': 'http://127.0.0.1:8001', 'OMNIQA_KEY': KEY,
        'OMNIQA_REPOSITORY_ID': str(CONFIG['repository_id']),
        'OMNIQA_JENKINS_JOB': 'SurveyMaster-RF-Test', 'BUILD_NUMBER': str(number),
        'PYTHONIOENCODING': 'utf-8',
        'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': ROOT.as_posix(),
        'GIT_CONFIG_KEY_1': 'safe.directory', 'GIT_CONFIG_VALUE_1': REPO.as_posix(),
    }
    sdk = [str(PYTHON), 'ci/omniqa_robot.py']
    with (output.parent / 'console.txt').open('w', encoding='utf-8') as log:
        try:
            subprocess.run(sdk + ['prepare', '--output-dir', str(output)], cwd=REPO, env=env,
                           stdout=log, stderr=log, check=True, timeout=120)
            tested = subprocess.run([str(PYTHON), '-m', 'robot', '-A', str(output / 'robot_args.txt')],
                                    cwd=REPO, env=env, stdout=log, stderr=log, timeout=180)
            posted = subprocess.run(sdk + ['report', '--output-dir', str(output)], cwd=REPO, env=env,
                                    stdout=log, stderr=log, timeout=120)
            row['result'] = 'SUCCESS' if tested.returncode == posted.returncode == 0 else 'FAILURE'
        except Exception as exc:
            log.write(f'Local validation runner failed: {type(exc).__name__}\n')
            row['result'] = 'FAILURE'
        finally:
            row['building'] = False
            (output.parent / 'build.json').write_text(json.dumps(row), encoding='utf-8')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data, headers=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        expected = 'Basic ' + base64.b64encode(('local-validation:' + TOKEN).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization', ''), expected):
            return True
        self.reply(401, {'message': 'Local validation authentication required'})
        return False

    def do_GET(self):
        path = urlsplit(self.path).path
        # Report files are available only on loopback; all API calls require auth.
        if path.startswith(JOB) and ('/artifact/' in path or path.endswith('/consoleText')):
            relative = path[len(JOB):].split('/')
            if not relative[0].isdigit() or int(relative[0]) not in BUILDS:
                return self.reply(404, {})
            folder = WORK / 'builds' / relative[0]
            target = folder / ('console.txt' if path.endswith('/consoleText') else '/'.join(relative[2:]))
            if not target.resolve().is_relative_to(folder.resolve()) or not target.is_file():
                return self.reply(404, {})
            raw = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if target.suffix == '.html' else 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if not self.authenticated():
            return
        if path == '/crumbIssuer/api/json':
            return self.reply(404, {})
        if path == '/me/api/json':
            return self.reply(200, {'id': 'local-validation', 'fullName': '本地执行适配器（非服务器 Jenkins）'})
        if path == JOB + 'api/json':
            names = ['AUTOMATION_RUN_ID', 'ZENTAO_CASE_IDS', 'ZENTAO_TESTTASK_ID', 'TEST_REVISION',
                     'TEST_BRANCH', 'DEVICE_ID', 'APK_PATH', 'OMNIQA_ENABLED', 'OMNIQA_DRY_RUN', 'PACKAGE']
            return self.reply(200, {'name': 'SurveyMaster-RF-Test', 'buildable': True,
                'property': [{'parameterDefinitions': [{'name': k} for k in names]}]})
        if path.startswith('/queue/item/'):
            number = path.split('/')[3]
            if number.isdigit() and int(number) in BUILDS:
                return self.reply(200, {'cancelled': False, 'executable': {'number': int(number), 'url': BASE + JOB + number + '/'}})
        if path.startswith(JOB):
            number = path[len(JOB):].split('/')[0]
            if number.isdigit() and int(number) in BUILDS:
                row = BUILDS[int(number)]
                return self.reply(200, {'number': int(number), 'building': row['building'], 'result': row['result'],
                    'url': BASE + JOB + number + '/',
                    'actions': [{'parameters': [{'name': k, 'value': v} for k, v in row['params'].items()]}]})
        return self.reply(404, {})

    def do_POST(self):
        if not self.authenticated():
            return
        parsed = urlsplit(self.path)
        if parsed.path != JOB + 'buildWithParameters':
            return self.reply(404, {})
        params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        if params.get('OMNIQA_DRY_RUN') != 'true' or params.get('OMNIQA_ENABLED') != 'true':
            return self.reply(422, {'message': 'Local adapter permits script dry runs only'})
        with LOCK:
            number = max([int(p.name) for p in (WORK / 'builds').glob('*') if p.name.isdigit()] + list(BUILDS) + [0]) + 1
            (WORK / 'builds' / str(number)).mkdir(parents=True, exist_ok=True)
            BUILDS[number] = {'params': params, 'building': True, 'result': None}
            (WORK / 'builds' / str(number) / 'build.json').write_text(json.dumps(BUILDS[number]), encoding='utf-8')
        self.reply(201, {}, {'Location': BASE + '/queue/item/' + str(number) + '/'})
        threading.Thread(target=run_build, args=(number,), daemon=True).start()


if __name__ == '__main__':
    print('Local dry-run adapter: ' + BASE, flush=True)
    ThreadingHTTPServer(('127.0.0.1', 18766), Handler).serve_forever()
