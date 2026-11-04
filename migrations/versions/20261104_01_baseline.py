"""Reviewed PostgreSQL baseline, frozen at the SQLite-to-PostgreSQL transition.

Do not import live ORM metadata here: later model edits must not change this revision.
"""
from pathlib import Path

from alembic import op, context
from sqlalchemy import inspect

revision = '20261104_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        existing = set(inspect(op.get_bind()).get_table_names()) - {'alembic_version'}
        if existing:
            raise RuntimeError('Baseline requires an empty database; existing tables will not be overwritten')
    ddl = Path(__file__).with_name('baseline_20261104.sql').read_text(encoding='utf-8')
    for statement in ddl.split('-- statement-break'):
        if statement.strip():
            op.execute(statement.strip())


def downgrade():
    raise RuntimeError('Baseline downgrade is intentionally disabled; restore a verified backup instead')
