"""Isolated, loopback-only platform for local integration acceptance.

Run with the platform venv: local_platform.py init | serve.
Never reads or writes the normal app_auto.db. All runtime data stays in .codex-tmp.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.codex-tmp/automation-local-validation'
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.update({
    'APP_DATABASE_URL': 'sqlite:///' + (WORK / 'platform.db').as_posix(),
    'APP_ENV': 'dev',
    'APP_ZENTAO_BACKGROUND_SYNC_ENABLED': 'false',
    'APP_WECOM_WEBHOOK_URL': '',
    'APP_N8N_ZENTAO_AI_WEBHOOK_URL': '',
    'APP_TERMINAL_CONTROL_ENABLED': 'false',
    'APP_BOT_API_KEY': '',
    'APP_JENKINS_BASE_URL': 'http://127.0.0.1:18766',
})


def initialize():
    from app.db.init_db import init_db
    from app.db.session import SessionLocal
    from app.models import User, UserZentaoBinding, AutomationRepository
    from app.schemas.automation import RepositoryCreate
    from app.services import automation_service, jenkins_service
    from app.services.zentao_auth_service import encrypt_password

    init_db()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(username='admin').one()
        # This credential applies only to the isolated local database.
        admin.password_hash = User.hash_password('admin')
        admin.is_active = True
        db.commit()
        if db.query(AutomationRepository).count():
            print('Existing isolated environment retained: http://127.0.0.1:8001/')
            return
        cfg_path = Path(os.environ.get('LOCAL_ZTF_CONFIG', r'D:\SurveyMasterAutoTesting\conf\ztf.conf'))
        cfg = dict((k.strip().lower(), v.strip()) for k, v in (
            line.split('=', 1) for line in cfg_path.read_text(encoding='utf-8-sig').splitlines()
            if '=' in line and not line.lstrip().startswith(('#', ';'))))
        db.add(UserZentaoBinding(user_id=admin.id, base_url=cfg['url'].rstrip('/'),
            zentao_account=cfg['username'], zentao_password_ciphertext=encrypt_password(cfg['password'])))
        db.commit()
        broker_token = secrets.token_urlsafe(32)
        jenkins_service.upsert_binding(db, admin.id, base_url='http://127.0.0.1:18766',
                                      account='local-validation', token=broker_token)
        response = automation_service.create_repository(db, admin, RepositoryCreate(
            name='SurveyMaster · 本地脚本演练', git_url='git@github.com:CC-coder-GSG/SurveyMasterAutoTesting.git',
            branch='feat/zentao-automation-integration', jenkins_job='SurveyMaster-RF-Test',
            zentao_base_url=cfg['url'].rstrip('/'), product_id=15, execution_id=1289,
            default_testtask_id=1909, target_build_id=4843))
        (WORK / 'runtime.json').write_text(json.dumps({
            'repository_id': response['repository']['id'],
            'integration_key_encrypted': encrypt_password(response['integration_key']),
            'broker_token_encrypted': encrypt_password(broker_token),
        }, indent=2), encoding='utf-8')
    print('Isolated database initialized. URL: http://127.0.0.1:8001/ ; login: admin')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'init', 'serve'}:
        raise SystemExit('Usage: local_platform.py init|serve')
    if sys.argv[1] == 'init':
        initialize()
    else:
        import uvicorn
        uvicorn.run('app.main:app', host='127.0.0.1', port=8001, access_log=False)
