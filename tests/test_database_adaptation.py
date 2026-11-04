from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, create_mock_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.db.engine import build_engine
from app.db.migrate import convert, preflight, snapshot
from app.core.database_errors import is_database_busy


def test_backup_keeps_source_bytes_and_includes_wal(tmp_path):
    source = tmp_path / 'source.db'
    connection = sqlite3.connect(source)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY)')
    connection.execute('INSERT INTO sample VALUES (42)')
    connection.commit()
    original = {p.name: p.read_bytes() for p in tmp_path.glob('source.db*')}
    result = snapshot(source, tmp_path / 'snapshot.db')
    assert result['snapshot_sha256']
    assert all((tmp_path / name).read_bytes() == value for name, value in original.items())
    with sqlite3.connect(tmp_path / 'snapshot.db') as db:
        assert db.execute('SELECT id FROM sample').fetchone() == (42,)
    with pytest.raises(ValueError):
        snapshot(source, tmp_path / 'snapshot.db')
    connection.close()


def test_target_types_preserve_milliseconds_and_reject_lossy_conversion():
    assert convert(Column('stamp', BigInteger), 1774494236309) == 1774494236309
    with pytest.raises(ValueError):
        convert(Column('stamp', Integer), 1774494236309)
    with pytest.raises(ValueError):
        convert(Column('title', String(3)), 'long text')
    with pytest.raises(ValueError):
        convert(Column('flag', Boolean), 'false')


def test_postgresql_lock_index_is_partial():
    from app.models.terminal_device_lock import TerminalDeviceLock
    index = next(i for i in TerminalDeviceLock.__table__.indexes if i.name == 'uq_terminal_active_lock')
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert 'WHERE released_at IS NULL' in sql


def test_postgres_connect_options_never_include_sqlite_pragmas():
    engine = build_engine('postgresql+psycopg://unused:unused@127.0.0.1/unused')
    assert engine.pool._pre_ping
    assert 'check_same_thread' not in engine.dialect.create_connect_args(engine.url)[1]
    engine.dispose()


@pytest.mark.parametrize('code', ['40001', '40P01', '55P03', '53300', '08006'])
def test_postgresql_transient_errors_are_classified_without_retry(code):
    original = Exception('redacted')
    original.sqlstate = code
    from sqlalchemy.exc import OperationalError
    assert is_database_busy(OperationalError(None, None, original))


def test_postgresql_startup_only_checks_schema(monkeypatch):
    import app.db.init_db as module
    import app.db.schema_version as version
    engine = create_mock_engine('postgresql+psycopg://', lambda *a, **k: pytest.fail('startup must not execute DDL'))
    monkeypatch.setattr(module, 'engine', engine)
    checked = []
    monkeypatch.setattr(version, 'require_schema', lambda value: checked.append(value))
    module.init_db()
    assert checked == [engine]


def test_rehearsal_startup_can_disable_all_background_jobs(monkeypatch):
    import app.main as module
    initialized = []
    monkeypatch.setattr(module, 'init_db', lambda: initialized.append(True))
    monkeypatch.setattr(module.settings, 'background_jobs_enabled', False)
    monkeypatch.setattr(module, '_ensure_database_compare_worker', lambda: pytest.fail('worker must stay disabled'))
    monkeypatch.setattr(module.scheduler, 'add_job', lambda *a, **k: pytest.fail('scheduler must stay disabled'))
    module.on_startup()
    assert initialized == [True]


def test_preflight_blocks_foreign_keys_without_leaking_field_values(tmp_path):
    path = tmp_path / 'bad.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER, parent_id INTEGER REFERENCES parent(id), secret TEXT); INSERT INTO child VALUES(1,2,\'private-value\');')
    report = preflight(path)
    assert not report['ok']
    assert any(i['kind'] == 'foreign_key' for i in report['issues'])
    assert 'private-value' not in str(report)


def test_postgresql_baseline_matches_models(db_session):
    if db_session.bind.dialect.name != 'postgresql':
        pytest.skip('Requires APP_TEST_DATABASE_URL')
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.db.model_registry import metadata
    assert compare_metadata(MigrationContext.configure(db_session.connection()), metadata()) == []


def test_product_name_lookup_converts_text_ids_for_numeric_mirror(db_session):
    from app.models import ZentaoTestCaseMirror
    from app.services.workbench_link_service import WorkbenchLinkService
    db_session.add(ZentaoTestCaseMirror(zentao_case_id='u#123', zentao_case_numeric_id=123,
                                      zentao_product_id=321, zentao_product_name='Product name'))
    db_session.commit()
    service = WorkbenchLinkService(db_session)
    service._product_ids = {'321', 'not-numeric'}
    assert service._product_name_map()['321'] == 'Product name'


def test_postgresql_concurrent_final_states_keep_all_users_and_fields():
    import os
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.orm import Session
    from sqlalchemy import delete
    from sqlalchemy.engine import make_url
    from app.models import User, Version, VersionType, Requirement, FinalTestRecord
    from app.services.final_test_service import write_final_state
    url = os.environ.get('APP_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires APP_TEST_DATABASE_URL')
    assert make_url(url).database.endswith('_test')
    engine = build_engine(url)
    marker = uuid4().hex[:12]
    with Session(engine) as db:
        users = [User(username=f'parallel-{marker}-{n}', password_hash='unused') for n in range(3)]
        major = Version(version_no=marker, version_type=VersionType.MAJOR, final_test_enabled=True)
        db.add_all([major, *users]); db.flush()
        req = Requirement(zentao_req_id=marker, title='Parallel test', major_version_id=major.id)
        db.add(req); db.commit()
        req_id, major_id, ids = req.id, major.id, [u.id for u in users]
    try:
        def update(user_id, fields):
            with Session(engine) as db:
                write_final_state(db, req_id, user_id, **fields)
                db.commit()
        with ThreadPoolExecutor(6) as workers:
            jobs = [workers.submit(update, user, fields) for user in ids for fields in (
                {'testing': True}, {'case_completed': True}, {'test_completed': True}, {'testing': True})]
            for job in jobs:
                job.result()
        with Session(engine) as db:
            records = db.query(FinalTestRecord).filter_by(requirement_id=req_id).all()
            assert len(records) == 3
            assert all(r.case_completed and r.test_completed and not r.testing for r in records)
    finally:
        with engine.begin() as conn:
            conn.execute(delete(FinalTestRecord).where(FinalTestRecord.requirement_id == req_id))
            conn.execute(delete(Requirement).where(Requirement.id == req_id))
            conn.execute(delete(Version).where(Version.id == major_id))
            conn.execute(delete(User).where(User.id.in_(ids)))
        engine.dispose()
