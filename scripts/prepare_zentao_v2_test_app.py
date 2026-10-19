"""Prepare a separate local app copy with a fresh DB, uploads and settings.

Preparation never starts a service or reads the production .env/database.
Use --run <printed directory> to start this copy on localhost:8091 later.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from uuid import uuid4

MARKER = 'zentao-v2-test-app.json'


def prepare(source: Path, output_parent: Path) -> Path:
    target = output_parent / ('zentao-v2-app-' + uuid4().hex[:12])
    target.mkdir(parents=True, exist_ok=False)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.env', '.env.*', '*.db',
                                   '*.db-*', '*.sqlite*', '*.log', 'node_modules', '.tmp*', 'ui-demo')
    for name in ('app', 'frontend'):
        shutil.copytree(source / name, target / name, ignore=ignore)
    shutil.copy2(source / 'requirements.txt', target / 'requirements.txt')
    (target / 'uploads').mkdir()
    (target / MARKER).write_text(json.dumps({'kind': 'zentao-v2-isolated-app',
                                           'source': str(source), 'fresh_database': True}), encoding='utf-8')
    return target


def isolated_env(target: Path, inherited: dict) -> dict:
    # Remove both Settings' APP_* keys and its legacy environment aliases.
    tree = ast.parse((target / 'app/core/config.py').read_text(encoding='utf-8-sig'))
    aliases = {node.args[0].value for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == 'getenv' and node.args
               and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
    env = {key: value for key, value in inherited.items() if key not in aliases and not key.startswith('APP_')}
    env.update({
        'APP_ENV': 'dev',
        'APP_DATABASE_URL': 'sqlite:///' + (target / 'test.sqlite').as_posix(),
        'APP_SECRET_KEY': secrets.token_urlsafe(32),
        'APP_ZENTAO_BINDING_SECRET': '',
        'APP_ZENTAO_BACKGROUND_SYNC_ENABLED': 'false',
        'APP_ZENTAO_BUILD_SYSTEM_MAPPING_JSON': inherited.get('ZENTAO_TEST_BUILD_SYSTEM_MAPPING_JSON', ''),
        'APP_WECOM_WEBHOOK_URL': '', 'APP_N8N_ZENTAO_AI_WEBHOOK_URL': '',
        'APP_JENKINS_DEFAULT_BASE_URL': 'http://offline.invalid',
        'APP_TERMINAL_CONTROL_ENABLED': 'false',
        'PYTHONPATH': str(target),
    })
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, help='Start a previously prepared isolated directory')
    parser.add_argument('--port', type=int, default=8091)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    if args.run is None:
        target = prepare(source, source / '.codex-tmp')
        print(target)
        print('Fresh app copy prepared; production bindings, database, uploads and .env were not copied.')
        print('Bind only an isolated IPD 5.6 instance when using this copy.')
        return 0
    target = args.run.resolve()
    marker = json.loads((target / MARKER).read_text(encoding='utf-8'))
    if marker.get('kind') != 'zentao-v2-isolated-app':
        parser.error('Not a prepared Zentao test app directory')
    print('Local test app:', target, flush=True)
    return subprocess.call([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                            '--port', str(args.port)], cwd=target, env=isolated_env(target, dict(os.environ)))


if __name__ == '__main__':
    raise SystemExit(main())
