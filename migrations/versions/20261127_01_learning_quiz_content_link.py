"""Let a learning quiz optionally belong to a course or material."""
from alembic import op
import sqlalchemy as sa

revision = '20261127_01'
down_revision = '20261113_01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('learning_quizzes', sa.Column('content_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'learning_quizzes_content_id_fkey', 'learning_quizzes', 'learning_contents',
        ['content_id'], ['id'], ondelete='SET NULL', deferrable=True, initially='IMMEDIATE',
    )
    op.create_index('ix_learning_quizzes_content_id', 'learning_quizzes', ['content_id'])


def downgrade():
    op.drop_index('ix_learning_quizzes_content_id', table_name='learning_quizzes')
    op.drop_constraint('learning_quizzes_content_id_fkey', 'learning_quizzes', type_='foreignkey')
    op.drop_column('learning_quizzes', 'content_id')
