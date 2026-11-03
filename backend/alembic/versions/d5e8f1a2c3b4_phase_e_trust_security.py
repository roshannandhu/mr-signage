"""Phase E trust & security: auth_events, token_version, attention_dismissals, tenant_notes

Revision ID: d5e8f1a2c3b4
Revises: c3e8a1f4d7b9
Create Date: 2026-11-03

B5 Sign-in history & session revocation:
* users.last_login_at
* users.token_version (server_default "0")
* auth_events table (user_id, attempted_username, success, method, ip, user_agent, occurred_at)

Support & notes:
* attention_dismissals table
* tenant_notes table
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e8f1a2c3b4"
down_revision = "c3e8a1f4d7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. users additions
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))

    # 2. auth_events
    op.create_table(
        "auth_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("attempted_username", sa.String(length=150), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, index=True),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )

    # 3. attention_dismissals
    op.create_table(
        "attention_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("rule", sa.String(length=50), nullable=False, index=True),
        sa.Column("organization_id", sa.Integer(), nullable=True, index=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dismissed_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 4. tenant_notes
    op.create_table(
        "tenant_notes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_username", sa.String(length=150), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tenant_notes")
    op.drop_table("attention_dismissals")
    op.drop_table("auth_events")
    op.drop_column("users", "token_version")
    op.drop_column("users", "last_login_at")

