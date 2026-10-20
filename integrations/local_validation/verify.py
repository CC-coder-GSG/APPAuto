"""Validate the isolated platform over HTTP and a real headless Chrome browser."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import time

import httpx
import websockets

from local_platform import ROOT, WORK
from app.services.zentao_auth_service import decrypt_password

BASE = 'http://127.0.0.1:8001'
REPO = ROOT / '.codex-tmp/surveymaster-integration-20261019'
CONFIG = json.loads((WORK / 'runtime.json').read_text(encoding='utf-8'))
RID = CONFIG['repository_id']
KEY = decrypt_password(CONFIG['integration_key_encrypted'])
EVIDENCE = {}


def prepare_catalog():
    env = os.environ | {'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'safe.directory',
        'GIT_CONFIG_VALUE_0': ROOT.as_posix(), 'GIT_CONFIG_KEY_1': 'safe.directory', 'GIT_CONFIG_VALUE_1': REPO.as_posix()}
    subprocess.run([r'D:\SurveyMasterAutoTesting\.venv\Scripts\python.exe', 'ci/omniqa_robot.py',
        'catalog', '--branch', 'feat/zentao-automation-integration', '--output', str(WORK / 'catalog.json')],
        cwd=REPO, env=env, check=True)
    response = httpx.post(f'{BASE}/automation/integrations/{RID}/catalog',
        headers={'X-Automation-Key': KEY}, json=json.loads((WORK / 'catalog.json').read_text(encoding='utf-8')), timeout=60)
    response.raise_for_status()
    EVIDENCE['catalog_cases'] = len(response.json()['cases'])
    EVIDENCE['revision'] = response.json()['catalog_revision']


async def browser(token):
    chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    browser_log = (WORK / 'browser-stderr.log').open('w', encoding='utf-8')
    process = subprocess.Popen([chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--disable-crash-reporter',
        '--disable-background-networking', '--disable-default-apps', '--no-default-browser-check',
        '--remote-debugging-port=18767', '--remote-debugging-address=127.0.0.1',
        '--user-data-dir=' + str(WORK / 'chrome-profile'), '--window-size=1600,1100', 'about:blank'],
        stdout=browser_log, stderr=browser_log, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Headless Chrome exited; inspect browser-stderr.log')
            try:
                tabs = httpx.get('http://127.0.0.1:18767/json', timeout=1).json()
                target = next(x for x in tabs if x['type'] == 'page')
                break
            except Exception:
                await asyncio.sleep(.25)
        else:
            raise RuntimeError('Headless Chrome did not start')
        async with websockets.connect(target['webSocketDebuggerUrl'], max_size=16 * 1024 * 1024) as ws:
            sequence = 0
            async def cdp(method, params=None):
                nonlocal sequence
                sequence += 1
                await ws.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get('id') == sequence:
                        if 'error' in message:
                            raise RuntimeError(message['error'])
                        return message.get('result', {})
            async def evaluate(expression):
                result = await cdp('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
                if result.get('exceptionDetails'):
                    raise RuntimeError(result['exceptionDetails'])
                return result.get('result', {}).get('value')
            async def until(expression, timeout=120):
                for _ in range(timeout * 4):
                    if await evaluate(expression):
                        return
                    await asyncio.sleep(.25)
                raise RuntimeError('Browser condition timed out: ' + expression)
            await cdp('Page.enable')
            await cdp('Page.addScriptToEvaluateOnNewDocument', {'source': 'if(location.origin === ' + json.dumps(BASE) + ') localStorage.setItem("token",' + json.dumps(token) + ');'})
            await cdp('Page.navigate', {'url': BASE + '/?automation_repository=' + str(RID)})
            await until("!!document.querySelector('input[name=automationCase][value=\"23466\"]')")
            EVIDENCE['browser_panel_loaded'] = True
            await evaluate("""document.querySelector('input[name=automationCase][value="23466"]').checked=true; document.getElementById('automationDevice').value='LOCAL-DRYRUN'; document.getElementById('automationApk').value='local-script-validation-no-apk'; document.getElementById('automationDryRun').checked=true; document.getElementById('automationRunForm').requestSubmit(); true""")
            await until('!!document.getElementById("automationRunDetail").textContent.includes("脚本演练")')
            await until('!document.getElementById("automationStart").disabled')
            # Refresh actual UI while the independent adapter runs Robot and posts results.
            for _ in range(120):
                await evaluate('document.getElementById("automationRunRefresh").click(); true')
                await asyncio.sleep(1)
                if await evaluate('document.getElementById("automationRunDetail").textContent.includes("未回写")'):
                    break
            else:
                raise RuntimeError('Robot result did not appear in browser')
            EVIDENCE['browser_result_visible'] = True
            EVIDENCE['browser_detail_text'] = await evaluate('document.getElementById("automationRunDetail").innerText')
            EVIDENCE['browser_report_links'] = await evaluate('[...document.querySelectorAll("#automationRunDetail a")].map(a=>({text:a.textContent,url:a.href}))')
            await evaluate('document.getElementById("automationDetails").scrollIntoView(); true')
            shot = await cdp('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
            (WORK / 'local-browser.png').write_bytes(base64.b64decode(shot['data']))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        browser_log.close()


def verify_http(token):
    client = httpx.Client(base_url=BASE, headers={'Authorization': 'Bearer ' + token}, timeout=60)
    response = client.get(f'/automation/repositories/{RID}/runs')
    response.raise_for_status()
    run = response.json()['items'][0]
    run = client.get('/automation/runs/' + run['id']).json()
    assert run['dry_run'] and run['status'] == 'completed', run
    assert run['summary']['PASS'] == 1 and len(run['results']) == 1, run
    assert run['results'][0]['sync_status'] == 'skipped', run
    EVIDENCE['run'] = run
    archive = json.loads((WORK / 'builds' / str(run['build_number']) / 'results/automation-result.json').read_text(encoding='utf-8'))
    path = f'/automation/integrations/{RID}/runs/{run["id"]}/results'
    headers = {'X-Automation-Key': KEY}
    duplicate = httpx.post(BASE + path, json=archive['payload'], headers=headers, timeout=60)
    assert duplicate.status_code == 200 and len(duplicate.json()['results']) == 1
    changed = json.loads(json.dumps(archive['payload']))
    changed['results'][0]['status'] = 'FAIL'
    conflict = httpx.post(BASE + path, json=changed, headers=headers, timeout=60)
    assert conflict.status_code == 409, conflict.text
    unauthorized = httpx.post(BASE + path, json=archive['payload'], headers={'X-Automation-Key': 'wrong'}, timeout=60)
    assert unauthorized.status_code == 401
    EVIDENCE['duplicate_callback_no_duplicate_rows'] = True
    EVIDENCE['changed_callback_rejected'] = conflict.status_code
    EVIDENCE['invalid_key_rejected'] = unauthorized.status_code
    for name in ('report.html', 'log.html'):
        response = httpx.get(run['build_url'] + 'artifact/results/' + name, timeout=10)
        assert response.status_code == 200 and '<html' in response.text.lower()
    EVIDENCE['report_and_log_accessible'] = True
    repo = client.get('/automation/repositories').json()['items'][0]
    assert not repo['writeback_enabled']
    EVIDENCE['zentao_writeback_disabled'] = True


if __name__ == '__main__':
    prepare_catalog()
    response = httpx.post(BASE + '/auth/token', data={'username': 'admin', 'password': 'admin'}, timeout=15)
    response.raise_for_status()
    token = response.json()['access_token']
    asyncio.run(browser(token))
    verify_http(token)
    (WORK / 'verification.json').write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Local browser and HTTP validation passed. Evidence: ' + str(WORK / 'verification.json'))
