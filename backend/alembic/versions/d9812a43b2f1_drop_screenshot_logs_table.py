"""drop screenshot_logs table

Revision ID: d9812a43b2f1
Revises: e8f2a4c6b1d9
Create Date: 2026-11-03 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9812a43b2f1'
down_revision = 'e8f2a4c6b1d9'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.drop_index(op.f('ix_screenshot_logs_screen_id'), table_name='screenshot_logs')
    op.drop_index(op.f('ix_screenshot_logs_organization_id'), table_name='screenshot_logs')
    op.drop_index(op.f('ix_screenshot_logs_id'), table_name='screenshot_logs')
    op.drop_table('screenshot_logs')

def downgrade() -> None:
    op.create_table(
        'screenshot_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('screen_id', sa.Integer(), nullable=False),
        sa.Column('file_url', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['screen_id'], ['screens.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_screenshot_logs_id'), 'screenshot_logs', ['id'], unique=False)
    op.create_index(op.f('ix_screenshot_logs_organization_id'), 'screenshot_logs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_screenshot_logs_screen_id'), 'screenshot_logs', ['screen_id'], unique=False)
