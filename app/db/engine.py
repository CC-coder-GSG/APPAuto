"""Dialect-specific connection setup, shared by the app and migration tooling."""
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url


def build_engine(url, *, pool_size=5, max_overflow=5, pool_timeout=10):
    dialect = make_url(url).get_backend_name()
    if dialect not in {'sqlite', 'postgresql'}:
        raise ValueError('Only SQLite and PostgreSQL are supported')
    options = {'hide_parameters': True}
    if dialect == 'sqlite':
        options['connect_args'] = {'check_same_thread': False}
    else:
        options.update(pool_size=pool_size, max_overflow=max_overflow,
                       pool_timeout=pool_timeout, pool_pre_ping=True)
    engine = create_engine(url, **options)
    if dialect == 'sqlite':
        @event.listens_for(engine, 'connect')
        def sqlite_pragmas(connection, _):
            cursor = connection.cursor()
            try:
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA synchronous=NORMAL')
            finally:
                cursor.close()
    return engine
