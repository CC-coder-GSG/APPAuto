"""PostgreSQL 类型严格比较回归（2026-11-24 线上 500）。

禅道 v2 列表里的 id 是字符串（"1647"）。SQLAlchemy 把字符串和整型列比较时，
bind 类型推成 String，psycopg3 方言会渲染成 ``integer = $1::VARCHAR``，PostgreSQL
直接报「操作符不存在」；反过来整数比字符串列也一样。SQLite 宽松比较，单测里不会炸。

这里在 SQLite 会话上截获每条 ORM 语句，用真实的 psycopg 方言编译，检查这些 id 列
的绑定类型和列类型一致——与线上 ``$1::VARCHAR`` 的成因完全相同。
"""
from __future__ import annotations

import asyncio

from sqlalchemy import Integer, String, event
from sqlalchemy.dialects.postgresql import psycopg as pg_psycopg

from app.models import Version, VersionType
from app.models.bug import BugTracking, BugSourceType
from app.models.cad_test import CadBoard, CadItem
from app.services.cad_test_service import CadTestService
from app.services.zentao_version_sync_service import ZentaoVersionSyncService

PG = pg_psycopg.dialect()
INT_COLUMNS = ('zentao_execution_id', 'zentao_build_id')


def _capture(db):
    statements = []

    def on_execute(execution):
        statements.append(execution.statement)

    event.listen(db, 'do_orm_execute', on_execute)
    return statements


def _bind_types(statements, prefix):
    seen = []
    for stmt in statements:
        compiled = stmt.compile(dialect=PG)
        for name, bind in compiled.binds.items():
            if name.startswith(prefix):
                seen.append(bind.type)
    return seen


class _StringIdClient:
    """禅道 v2 形状：id 全是字符串。"""

    def list_project_executions(self, project_id, limit=200, status='all'):
        return [{'id': '1647', 'name': 's4031'}, {'id': '1875', 'name': 'Survey Master 5.0/s4050'}]

    def list_execution_builds(self, execution_id, limit=500):
        return [{'id': '5403', 'name': '4.0.5.0.261124_alpha.35(40500036)'}]

    def get(self, path, params=None):
        if path.endswith('/builds'):
            return {'builds': [{'id': '5403', 'name': '4.0.5.0.261124_alpha.35(40500036)'}]}
        raise AssertionError(f'unexpected request {path}')  # 同步版本不应再逐个查执行详情


def _sync(db, **kw):
    svc = ZentaoVersionSyncService(db)
    svc._get_client = lambda user_id: _StringIdClient()
    return asyncio.run(svc.sync_versions(user_id=1, software_id=1, zentao_project_id=134, **kw))


def test_version_sync_binds_zentao_ids_as_integers(db_session):
    statements = _capture(db_session)
    result = _sync(db_session, sync_minor=True)
    assert result.created_major and result.created_minor
    for column in INT_COLUMNS:
        types = _bind_types(statements, column)
        assert types, f'no query on {column}'
        assert all(isinstance(t, Integer) for t in types), (column, types)
    rows = db_session.query(Version).order_by(Version.id).all()
    assert {r.zentao_execution_id for r in rows if r.version_type == VersionType.MAJOR} == {1647, 1875}
    assert [r.zentao_build_id for r in rows if r.version_type == VersionType.MINOR] == [5403]


def test_resync_matches_existing_rows_without_duplicates(db_session):
    _sync(db_session, sync_minor=True)
    second = _sync(db_session, sync_minor=True)
    assert not second.created_major and not second.created_minor
    assert db_session.query(Version).count() == 3


def test_reconcile_minors_binds_build_id_as_integer(db_session):
    major = Version(version_no='V4.0.5', version_type=VersionType.MAJOR, software_id=1, zentao_execution_id=1875)
    db_session.add(major)
    db_session.commit()
    statements = _capture(db_session)
    result = ZentaoVersionSyncService(db_session).reconcile_execution_minors(
        _StringIdClient(), software_id=1, parent_id=major.id, zentao_execution_id=1875)
    assert result.created_minor == ['4.0.5.0.261124_alpha.35(40500036)']
    assert all(isinstance(t, Integer) for t in _bind_types(statements, 'zentao_build_id'))


def test_version_sync_skips_product_probe_unless_requested(db_session):
    # _StringIdClient.get 对执行详情直接 AssertionError：同步版本不该再查
    assert _sync(db_session, sync_minor=False).zentao_products == []


def test_product_probe_is_bounded(db_session):
    calls = []

    class Client(_StringIdClient):
        def list_project_executions(self, *a, **k):
            return [{'id': str(i), 'name': f'exec{i}'} for i in range(1, 138)]

        def get(self, path, params=None):
            calls.append(path)
            return {'id': int(path.split('/')[1])}  # v2 详情不带 products

    svc = ZentaoVersionSyncService(db_session)
    svc._get_client = lambda user_id: Client()
    asyncio.run(svc.sync_versions(user_id=1, software_id=1, zentao_project_id=134,
                                  sync_minor=False, resolve_products=True))
    assert calls == [f'executions/{i}' for i in range(137, 132, -1)]


def test_cad_board_binds_bug_ids_as_strings(db_session):
    board = CadBoard(name='CAD')
    major = Version(version_no='V9.9', version_type=VersionType.MAJOR, software_id=1)
    db_session.add_all([board, major])
    db_session.flush()
    db_session.add(CadItem(board_id=board.id, seq=1, title='图纸', zentao_bug_id=777))
    db_session.add(BugTracking(major_version_id=major.id, bug_id='b#777', zentao_bug_id='777',
                               source_type=BugSourceType.MANUAL))
    db_session.commit()
    statements = _capture(db_session)
    detail = CadTestService(db_session).get_board_detail(board.id)
    types = _bind_types(statements, 'zentao_bug_id')
    assert types and all(isinstance(t, String) for t in types), types
    assert detail


def test_sync_route_returns_readable_error_instead_of_bare_500(db_session, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_db
    from app.api.routes import zentao_version
    from tests.test_zentao_bug_actions import _create_user

    async def boom(self, **kwargs):
        raise RuntimeError('db exploded')

    monkeypatch.setattr(ZentaoVersionSyncService, 'sync_versions', boom)
    user = _create_user(db_session)
    app = FastAPI()
    app.include_router(zentao_version.router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        resp = client.post('/zentao/sync-versions', json={'software_id': 1, 'zentao_project_id': 134})
    assert resp.status_code == 500
    assert 'RuntimeError' in resp.json()['detail']
