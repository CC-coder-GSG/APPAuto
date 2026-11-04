from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.model_registry import metadata

target_metadata = metadata()
if make_url(settings.database_url).get_backend_name() != 'postgresql':
    raise RuntimeError('This migration chain targets PostgreSQL; SQLite uses its existing compatibility upgrades')

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url, poolclass=NullPool, hide_parameters=True)
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('This migration chain targets PostgreSQL; SQLite uses its existing compatibility upgrades')
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
