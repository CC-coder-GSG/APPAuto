"""PostgreSQL starts only on an explicitly migrated database."""
from sqlalchemy import inspect, text

SCHEMA_REVISION = '20261104_01'


def require_schema(engine):
    with engine.connect() as conn:
        if not inspect(conn).has_table('alembic_version'):
            raise RuntimeError('PostgreSQL schema is not initialized; run alembic upgrade head before starting the service')
        versions = set(conn.execute(text('SELECT version_num FROM alembic_version')).scalars())
        if versions != {SCHEMA_REVISION}:
            raise RuntimeError('Database schema version does not match this application; run the reviewed migrations')
