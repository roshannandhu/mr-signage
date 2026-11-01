"""admin audit trail, plus indexes the admin console filters on

Revision ID: c3e8a1f4d7b9
Revises: b7d4f1a9e6c2
Create Date: 2026-11-01

Nothing recorded what a platform operator did -- suspend, plan change, role change and
permanent delete reached only stdout. admin_audit_events is the append-only record, with
no foreign keys so it survives the tenant or user it describes being deleted.

The two indexes back the console's date-range filters: payments by paid_at, and the fleet's
"offline for longer than" filter on screens.last_seen.

Additive only: one new table and two indexes.
"""

from alembic import op
import sqlalchemy as sa


revision = "c3e8a1f4d7b9"
down_revision = "b7d4f1a9e6c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True, index=True),
        sa.Column("actor_username", sa.String(length=150), nullable=True),
        sa.Column("acting_org_id", sa.Integer(), nullable=True),
        sa.Column("target_org_id", sa.Integer(), nullable=True, index=True),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False, index=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=True, index=True),
    )
    op.create_index("ix_platform_payments_paid_at", "platform_payments", ["paid_at"])
    op.create_index("ix_screens_last_seen", "screens", ["last_seen"])


def downgrade() -> None:
    op.drop_index("ix_screens_last_seen", table_name="screens")
    op.drop_index("ix_platform_payments_paid_at", table_name="platform_payments")
    op.drop_table("admin_audit_events")
