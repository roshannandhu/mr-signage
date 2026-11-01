"""add kiosk lock PIN: organizations.lock_pin, screens.uses_own_pin

Revision ID: a6c0e9d3f582
Revises: f8a2d6c91b73
Create Date: 2026-11-01

Phase 3.10: TV lock becomes a plan feature (kiosk_lock, already defined since 3.1 but never
gated or sent to the TV) with one workspace-wide PIN an owner can set, and a per-screen
opt-out for a panel that needs to keep its own. Both columns are additive and nullable/
defaulted, so every existing screen reads exactly as it does today (own PIN, no override)
until an owner explicitly sets a workspace PIN.
"""

from alembic import op
import sqlalchemy as sa


revision = "a6c0e9d3f582"
down_revision = "f8a2d6c91b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.add_column(sa.Column("lock_pin", sa.String(length=4), nullable=True))
    with op.batch_alter_table("screens") as batch_op:
        batch_op.add_column(
            sa.Column("uses_own_pin", sa.Boolean(), nullable=False, server_default="false")
        )


def downgrade() -> None:
    with op.batch_alter_table("screens") as batch_op:
        batch_op.drop_column("uses_own_pin")
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.drop_column("lock_pin")
