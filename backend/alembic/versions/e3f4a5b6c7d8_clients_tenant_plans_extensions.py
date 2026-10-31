"""Clients, tenant-sold plans, and paid extensions of a booking

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-01

A booking recorded its buyer as a free-text `advertiser` string. That was enough to label a
row and nothing else: the same customer spelled two ways became two customers, there was
nowhere to keep the address a report has to be emailed to, and "everything we ran for this
client" could not be asked at all.

`tenant_plans` is deliberately NOT the existing `plans` table. That one is what MR Signage bills
the tenant; this is what the tenant sells on to an advertiser. One table for both would let
a tenant edit the plan they are billed on, and would show MR Signage's pricing to their clients.

`ad_placement_extensions` is a table rather than an `extended_to` column because a campaign
that does well is extended more than once, and each sale has to survive on the invoice. A
single column would overwrite the record of the first extension every time a second was
made.

`advertiser` is kept, not dropped. It is NOT NULL on every existing row, and the report
still has to name somebody for a booking whose client was later removed. The backfill below
gives every existing booking a real client row, after which the string is a denormalised
label written from the client on save.
"""

from alembic import op
import sqlalchemy as sa


revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("client_code", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Per tenant, not global: two tenants may both sell to "BrightMart" and neither
        # should be blocked by, or able to see, the other's record.
        sa.UniqueConstraint("organization_id", "client_code", name="uq_clients_org_code"),
    )
    op.create_index("ix_clients_organization_id", "clients", ["organization_id"])

    op.create_table(
        "tenant_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_locations", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ad_slots", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("support_tier", sa.String(length=40), nullable=False, server_default="Basic Support"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenant_plans_organization_id", "tenant_plans", ["organization_id"])

    op.create_table(
        "ad_placement_extensions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("placement_id", sa.Integer(), nullable=False),
        sa.Column("extended_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extended_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("additional_price_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["placement_id"], ["ad_placements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("extended_to > extended_from", name="ck_extension_window_forward"),
    )
    op.create_index("ix_ad_placement_extensions_placement_id", "ad_placement_extensions", ["placement_id"])

    op.add_column("ad_placements", sa.Column("client_id", sa.Integer(), nullable=True))
    op.add_column("ad_placements", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_index("ix_ad_placements_client_id", "ad_placements", ["client_id"])
    op.create_index("ix_ad_placements_plan_id", "ad_placements", ["plan_id"])
    # SET NULL, not CASCADE: removing a client or retiring a plan must never delete the
    # bookings sold under them. The commercial terms were copied onto the booking, and
    # `advertiser` still names the buyer.
    op.create_foreign_key(
        "fk_ad_placements_client", "ad_placements", "clients", ["client_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_ad_placements_plan", "ad_placements", "tenant_plans", ["plan_id"], ["id"], ondelete="SET NULL"
    )

    # Backfill: one client per distinct (organisation, advertiser), then point the bookings
    # at it. Done in SQL rather than Python so it holds however many rows exist.
    #
    # row_number() supplies the code, scoped per organisation to match the unique
    # constraint. Blank advertisers are skipped rather than given a placeholder client --
    # inventing "CLT00001 = (blank)" would be worse than leaving client_id NULL, which the
    # report already handles by falling back to the string.
    op.execute(
        """
        INSERT INTO clients (organization_id, name, client_code, created_at, updated_at)
        SELECT organization_id,
               advertiser,
               'CLT' || LPAD(ROW_NUMBER() OVER (
                   PARTITION BY organization_id ORDER BY MIN(id)
               )::text, 5, '0'),
               now(),
               now()
        FROM ad_placements
        WHERE advertiser IS NOT NULL AND btrim(advertiser) <> ''
        GROUP BY organization_id, advertiser
        """
    )
    op.execute(
        """
        UPDATE ad_placements p
        SET client_id = c.id
        FROM clients c
        WHERE c.organization_id = p.organization_id
          AND c.name = p.advertiser
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_ad_placements_plan", "ad_placements", type_="foreignkey")
    op.drop_constraint("fk_ad_placements_client", "ad_placements", type_="foreignkey")
    op.drop_index("ix_ad_placements_plan_id", table_name="ad_placements")
    op.drop_index("ix_ad_placements_client_id", table_name="ad_placements")
    op.drop_column("ad_placements", "plan_id")
    op.drop_column("ad_placements", "client_id")
    op.drop_index("ix_ad_placement_extensions_placement_id", table_name="ad_placement_extensions")
    op.drop_table("ad_placement_extensions")
    op.drop_index("ix_tenant_plans_organization_id", table_name="tenant_plans")
    op.drop_table("tenant_plans")
    op.drop_index("ix_clients_organization_id", table_name="clients")
    op.drop_table("clients")
