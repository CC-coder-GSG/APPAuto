from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.final_test_upgrade import ensure_final_test_schema
from app.models import FinalTestRecord, Requirement, User, Version, VersionType
from app.services.final_test_service import FinalTestService, write_final_state
from app.services.workbench_service import WorkbenchService


def test_shared_presence_and_completion_precedence(db_session):
    db = db_session
    major = Version(version_no='presence', version_type=VersionType.MAJOR, final_test_enabled=True)
    alice = User(username='alice-presence', password_hash='x', display_name='Alice')
    bob = User(username='bob-presence', password_hash='x', display_name='Bob')
    db.add_all([major, alice, bob]); db.flush()
    req = Requirement(title='Shared requirement', zentao_req_id='r#presence', major_version_id=major.id)
    db.add(req); db.commit()
    svc = FinalTestService(db)
    svc.upsert_record(req.id, alice, testing=True)
    svc.upsert_record(req.id, bob, testing=True)
    svc.upsert_record(req.id, alice, test_completed=True)
    late = svc.upsert_record(req.id, alice, testing=True)
    assert late['test_completed'] and not late['testing']
    rows = WorkbenchService(db).get_my_workbench(current_user=bob, major_version_id=major.id)
    row = next(item for item in rows if item['id'] == req.id)
    assert row['testing'] is True
    assert {person['user_name']:person['status'] for person in row['final_test_people']} == {
        'Alice':'completed', 'Bob':'testing'}
    undone = svc.upsert_record(req.id, alice, test_completed=False)
    assert not undone['testing'] and not undone['test_completed']
    svc.upsert_record(req.id, bob, testing=False)
    assert svc.upsert_record(req.id, alice, testing=True)['testing']


def test_atomic_parallel_user_updates_do_not_duplicate_or_lose_fields(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "presence.db"}', connect_args={'timeout':5})
    Base.metadata.create_all(engine, tables=[FinalTestRecord.__table__])
    try:
        def update(user, **fields):
            with Session(engine) as db:
                write_final_state(db, 1, user, **fields)
                db.commit()
        with ThreadPoolExecutor(4) as executor:
            jobs = [executor.submit(update, user, **fields) for user in (1,2,3)
                    for fields in ({'case_completed':True}, {'testing':True}, {'test_completed':True}, {'testing':True})]
            for job in jobs: job.result()
        with Session(engine) as db:
            records = db.query(FinalTestRecord).all()
            assert len(records) == 3
            assert all(row.case_completed and row.test_completed and not row.testing for row in records)
    finally:
        engine.dispose()


def test_upgrade_preserves_existing_completed_records_and_is_repeatable(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "old.db"}')
    with Session(engine) as db:
        db.execute(text('CREATE TABLE final_test_records (id INTEGER PRIMARY KEY, test_completed BOOLEAN NOT NULL)'))
        db.execute(text('INSERT INTO final_test_records VALUES (1,1)')); db.commit()
        ensure_final_test_schema(db)
        ensure_final_test_schema(db)
        assert db.execute(text('SELECT test_completed, testing FROM final_test_records')).one() == (1,0)
    engine.dispose()
