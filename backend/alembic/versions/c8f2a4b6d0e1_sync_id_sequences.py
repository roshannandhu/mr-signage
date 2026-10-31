"""Bring every id sequence up to the rows already in its table

Revision ID: c8f2a4b6d0e1
Revises: b7d1e3f5a921
Create Date: 2026-11-01

Two earlier migrations seeded rows with explicit ids -- the default organisation (id 1) in
d5a7b9c2e4f1 and the three packages (ids 1-3) in e6b8c0d3f5a2 -- on tables whose id is a
Postgres serial. Writing an explicit id does not move the sequence, so on a database built
through the migration chain the sequence still stood at 1: the first signup or the first new
package inserted a row without an id, drew 1 from the sequence, and failed with a duplicate key.

Every table with a serial/identity `id` is set to MAX(id), or left to start at 1 when empty,
rather than just the two known culprits, so a third seed added later cannot do the same.
Idempotent, and it only ever moves a sequence forward to where the data already is. Postgres
only; the other dialects have no sequences to correct.

No downgrade: there is nothing sensible to put a sequence back to.
"""

from alembic import op
import sqlalchemy as sa


revision = "c8f2a4b6d0e1"
down_revision = "b7d1e3f5a921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    for table in inspector.get_table_names():
        if "id" not in {column["name"] for column in inspector.get_columns(table)}:
            continue
        sequence = bind.execute(
            sa.text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": f'"{table}"'}
        ).scalar()
        if not sequence:
            continue
        # With rows: the next id drawn is MAX(id) + 1. Empty: start from 1, as a new table does.
        bind.execute(
            sa.text(
                f'SELECT setval(:sequence, COALESCE((SELECT MAX(id) FROM "{table}"), 1), '
                f'(SELECT MAX(id) IS NOT NULL FROM "{table}"))'
            ),
            {"sequence": sequence},
        )


def downgrade() -> None:
    pass
