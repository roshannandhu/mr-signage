"""add contact fields to custom_plan_requests

Revision ID: e7f3b9a4c651
Revises: d4a7c1e9b358
Create Date: 2026-11-01

Phase 3.4: a custom-plan request had no company or contact details at all -- an operator
pricing one only ever saw the organisation name already on the tenant's account, never who
to actually reach, and the tenant's submit had nowhere to put a different contact than
whoever happened to be signed in. Four nullable columns; all additive.

No downgrade data loss: dropping the columns only discards contact details an operator
already has another way to reach (the organisation's own owner).
"""

from alembic import op
import sqlalchemy as sa


revision = "e7f3b9a4c651"
down_revision = "d4a7c1e9b358"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("custom_plan_requests") as batch_op:
        batch_op.add_column(sa.Column("company_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contact_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contact_email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contact_phone", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_plan_requests") as batch_op:
        batch_op.drop_column("contact_phone")
        batch_op.drop_column("contact_email")
        batch_op.drop_column("contact_name")
        batch_op.drop_column("company_name")
