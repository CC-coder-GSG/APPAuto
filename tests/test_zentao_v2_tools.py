from pathlib import Path

from scripts.prepare_zentao_v2_test_app import isolated_env, prepare
from scripts.probe_zentao_v2 import probes


def test_test_app_copy_excludes_bindings_secrets_database_and_uploads(tmp_path):
    source = tmp_path / 'source'
    (source / 'app/core').mkdir(parents=True)
    (source / 'frontend').mkdir()
    (source / 'uploads').mkdir()
    (source / 'app/core/config.py').write_text("import os\nx = os.getenv('LEGACY_SECRET', '')", encoding='utf-8-sig')
    (source / 'requirements.txt').write_text('fastapi', encoding='utf-8')
    (source / '.env').write_text('production secret', encoding='utf-8')
    (source / 'app_auto.db').write_bytes(b'production bindings')
    (source / 'uploads/production.png').write_bytes(b'production image')
    target = prepare(source, tmp_path / 'output')
    assert not (target / '.env').exists()
    assert not (target / 'app_auto.db').exists()
    assert list((target / 'uploads').iterdir()) == []
    env = isolated_env(target, {'APP_DATABASE_URL': 'production', 'LEGACY_SECRET': 'production', 'PATH': 'keep'})
    assert 'LEGACY_SECRET' not in env
    assert env['APP_DATABASE_URL'] == 'sqlite:///' + (target / 'test.sqlite').as_posix()
    assert env['APP_ZENTAO_BACKGROUND_SYNC_ENABLED'] == 'false'
    assert env['APP_WECOM_WEBHOOK_URL'] == ''
    assert env['PATH'] == 'keep'


def test_live_probe_marks_missing_objects_skipped_and_detects_baseline_mismatch():
    class Client:
        def list_projects(self):
            return [{'id': 1}, {'id': 2}]
        def __getattr__(self, name):
            return lambda *args, **kwargs: None
    report = probes(Client(), {'expected_ids': {'projects': [1, 3]}})
    assert report['checks'][0]['status'] == 'failed'
    assert report['summary']['passed'] == 0
    assert report['summary']['skipped'] > 0
