"""add platform_payments: a receipt for what a tenant paid the platform

Revision ID: d4a7c1e9b358
Revises: c8f2a4b6d0e1
Create Date: 2026-11-01

Phase 3.2: the admin Billing page needs a record of what tenants have actually paid, and
nothing held one -- Subscription and WebhookEvent carry no amounts. Written once, by the
shared activation path every purchase already goes through, from the amount the PAYMENT
PROVIDER reports for that charge. No foreign keys to plans or custom_plan_requests: a
payment is a historical fact and must survive either being edited or deleted later.

Additive only. Downgrade drops the table; no data outside it depends on this one existing.
"""

from alembic import op
import sqlalchemy as sa


revision = "d4a7c1e9b358"
down_revision = "c8f2a4b6d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("custom_request_id", sa.Integer(), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_order_id", sa.String(), nullable=True),
        sa.Column("provider_payment_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="captured"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_platform_payments_organization_id", "platform_payments", ["organization_id"])
    op.create_index("ix_platform_payments_provider_order_id", "platform_payments", ["provider_order_id"])


def downgrade() -> None:
    op.drop_index("ix_platform_payments_provider_order_id", table_name="platform_payments")
    op.drop_index("ix_platform_payments_organization_id", table_name="platform_payments")
    op.drop_table("platform_payments")
