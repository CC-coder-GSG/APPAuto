from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.db_lifecycle import bounded_sync, release_read_transaction
from app.models import SoftwareProduct, User, ZentaoTestCaseMirror
from app.services.zentao_testcase_service import ZentaoTestCaseService


def test_clean_read_returns_connection_without_expiring_values(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "pool.db"}', pool_size=1, max_overflow=0)
    try:
        with Session(engine) as db:
            db.execute(text('select 1'))
            assert engine.pool.checkedout() == 1
            assert release_read_transaction(db)
            assert engine.pool.checkedout() == 0
            with engine.connect() as connection:
                assert connection.scalar(text('select 2')) == 2
    finally:
        engine.dispose()


def test_read_boundary_does_not_commit_pending_writes(db_session):
    user = User(username='not_committed', password_hash='x')
    db_session.add(user)
    assert release_read_transaction(db_session) is False
    assert user in db_session.new
    db_session.rollback()
    assert db_session.query(User).filter_by(username='not_committed').first() is None


def test_bulk_sync_rejects_overlap_and_releases_slot_on_error():
    entered, finish = Event(), Event()
    class Worker:
        db = SimpleNamespace(new=[], dirty=[], deleted=[], in_transaction=lambda: False, rollback=lambda: None)
        @bounded_sync
        def inner(self):
            return 'nested'
        @bounded_sync
        def run(self, wait=False):
            assert self.inner() == 'nested'
            if wait:
                entered.set()
                assert finish.wait(5)
                raise ValueError('remote failure')
            return 'ok'
    worker = Worker()
    with ThreadPoolExecutor(1) as executor:
        job = executor.submit(worker.run, True)
        assert entered.wait(5)
        try:
            with pytest.raises(HTTPException) as error:
                worker.run()
            assert error.value.status_code == 409
        finally:
            finish.set()
        with pytest.raises(ValueError):
            job.result()
    assert worker.run() == 'ok'


@pytest.mark.parametrize('recent', [False, True])
def test_case_details_fetched_before_writes_and_connection_released(db_session, monkeypatch, recent):
    db = db_session
    software = SoftwareProduct(name='lifecycle-test')
    user = User(username='lifecycle-reader', password_hash='x')
    db.add_all([software, user]); db.commit()
    software_id = software.id
    service = ZentaoTestCaseService(db)
    calls = []
    class Client:
        def get_testcase(self, case_id):
            # The old implementation had already flushed the first mirror row.
            assert not db.in_transaction()
            assert not db.new and not db.dirty
            calls.append(case_id)
            return {'id':case_id, 'title':'details', 'steps':[], 'precondition':'ready'}
    monkeypatch.setattr(service, '_get_client_ctx', lambda *args: (Client(), 'https://zentao.invalid'))
    monkeypatch.setattr(service, '_collect_zentao_product_ids_for_software', lambda *args: [1])
    rows = [{'id':991, 'title':'first'}, {'id':992, 'title':'second'}]
    monkeypatch.setattr(service, '_fetch_product_testcases', lambda *args: rows)
    monkeypatch.setattr(service, '_fetch_recent_product_testcases', lambda *args, **kwargs: rows)
    method = service.sync_recent_software_testcases if recent else service.sync_software_testcases
    result = method(software_id=software_id, current_user=user, **({} if recent else {'force':True}))
    assert calls == [991,992]
    assert result['created'] == 2
    assert db.query(ZentaoTestCaseMirror).filter(ZentaoTestCaseMirror.zentao_case_numeric_id.in_(calls)).count() == 2
