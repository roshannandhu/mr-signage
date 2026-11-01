"""add offline-play tracking: play_logs.played_offline, rollups.offline_plays

Revision ID: f8a2d6c91b73
Revises: e7f3b9a4c651
Create Date: 2026-11-01

Phase 3.6: ads are counted online and offline. play_logs.played_offline records what the
TV itself observed (null for an app build that predates this, or a row queued before the
device's own update); play_log_hourly_rollups.offline_plays is the aggregated count
worker.py's aggregate_play_logs_sync derives from it, estimating the null case from how long
a play sat on the device before it was finally uploaded.

Additive only. offline_plays defaults to 0 so every existing rollup row reads as entirely
online until the next aggregation pass revisits it -- there is nothing to backfill: the
rollups already discarded which underlying play_logs rows they summed.
"""

from alembic import op
import sqlalchemy as sa


revision = "f8a2d6c91b73"
down_revision = "e7f3b9a4c651"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("play_logs") as batch_op:
        batch_op.add_column(sa.Column("played_offline", sa.Boolean(), nullable=True))
    with op.batch_alter_table("play_log_hourly_rollups") as batch_op:
        batch_op.add_column(
            sa.Column("offline_plays", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("play_log_hourly_rollups") as batch_op:
        batch_op.drop_column("offline_plays")
    with op.batch_alter_table("play_logs") as batch_op:
        batch_op.drop_column("played_offline")
