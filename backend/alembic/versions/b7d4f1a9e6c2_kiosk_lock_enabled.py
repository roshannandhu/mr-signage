"""tenant-controlled kiosk lock: organizations.lock_enabled, kiosk_unlock_events

Revision ID: b7d4f1a9e6c2
Revises: a6c0e9d3f582
Create Date: 2026-11-01

A plan including the kiosk_lock feature used to lock every paired screen immediately --
there was no tenant-facing on/off switch, only the plan entitlement itself. lock_enabled
is that switch, defaulted off so flipping a plan's feature flag never retroactively traps
an already-paired fleet; a tenant turns it on once they're ready.

kiosk_unlock_events is an audit trail for both ways a TV gets unlocked (a PIN entered
locally, or a remote unlock queued from the admin console), so a super admin has visibility
without reintroducing the master-PIN-opens-everything shortcut Phase 1.7 removed.

Additive only: a new nullable-free boolean with a default, and a new table.
"""

from alembic import op
import sqlalchemy as sa


revision = "b7d4f1a9e6c2"
down_revision = "a6c0e9d3f582"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.add_column(
            sa.Column("lock_enabled", sa.Boolean(), nullable=False, server_default="false")
        )
    op.create_table(
        "kiosk_unlock_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id", sa.Integer(),
            sa.ForeignKey("organizations.id"), nullable=False, index=True,
        ),
        sa.Column("screen_id", sa.Integer(), nullable=True, index=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column(
            "actor_user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("kiosk_unlock_events")
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.drop_column("lock_enabled")
