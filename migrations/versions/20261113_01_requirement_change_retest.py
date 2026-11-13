"""Keep change-retest dispatches and previous personal completion records."""
from alembic import op
import sqlalchemy as sa

revision = '20261113_01'
down_revision = '20261104_01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('requirement_change_retests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('requirements.id', ondelete='CASCADE', deferrable=True, initially='IMMEDIATE'), nullable=False),
        sa.Column('source_task_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('story_version', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', deferrable=True, initially='IMMEDIATE'), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', deferrable=True, initially='IMMEDIATE'), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('previous_state', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('requirement_id', 'source_task_id', 'story_version', name='uq_requirement_change_retest_source_version'))
    op.create_index('ix_requirement_change_retests_requirement_id', 'requirement_change_retests', ['requirement_id'])


def downgrade():
    op.drop_table('requirement_change_retests')
