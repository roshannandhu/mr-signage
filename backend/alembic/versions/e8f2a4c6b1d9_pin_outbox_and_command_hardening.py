"""pin outbox and command hardening

Revision ID: e8f2a4c6b1d9
Revises: d5e8f1a2c3b4
Create Date: 2026-11-03
"""
import os
import re
from alembic import op
import sqlalchemy as sa
from cryptography.fernet import Fernet

revision = "e8f2a4c6b1d9"
down_revision = "d5e8f1a2c3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Widen lock_pin to store Fernet ciphertext and add credentials_version to organizations
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.alter_column(
            "lock_pin",
            existing_type=sa.String(length=4),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
        batch_op.add_column(
            sa.Column("credentials_version", sa.Integer(), server_default="0", nullable=False)
        )

    # 2. Add synced_credentials_version and ensure maintenance_pin fits ciphertext in screens
    with op.batch_alter_table("screens") as batch_op:
        batch_op.alter_column(
            "maintenance_pin",
            existing_type=sa.String(),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column("synced_credentials_version", sa.Integer(), server_default="0", nullable=False)
        )

    # 3. Create outbox_events table for transactional, reliable event delivery
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=100), nullable=False, index=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False, index=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, index=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 4. Create device_command_logs table for physical command execution and ACK tracking
    op.create_table(
        "device_command_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("device_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("screen_id", sa.Integer(), sa.ForeignKey("screens.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("command", sa.String(length=50), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False, index=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_username", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    # 5. Create device_challenges table for dynamic challenge-response emergency recovery
    op.create_table(
        "device_challenges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(length=100), nullable=False, index=True),
        sa.Column("nonce", sa.String(length=32), nullable=False, index=True),
        sa.Column("response_code", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("used", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    # 6. Verified In-Place Data Migration
    pin_key = os.getenv("PIN_ENCRYPTION_KEY")
    if not pin_key or not pin_key.strip():
        raise RuntimeError("FATAL: PIN_ENCRYPTION_KEY missing during migration upgrade. Halted.")

    cipher = Fernet(pin_key.strip().encode("utf-8"))
    conn = op.get_bind()

    # Migrate organization lock_pin
    org_rows = conn.execute(sa.text("SELECT id, lock_pin FROM organizations WHERE lock_pin IS NOT NULL")).fetchall()
    for row in org_rows:
        pin_val = str(row[1]).strip() if row[1] is not None else ""
        if pin_val and re.match(r"^\d{4}$", pin_val):
            encrypted = cipher.encrypt(pin_val.encode("utf-8")).decode("utf-8")
            assert cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8") == pin_val, "Verification failed for organization lock_pin"
            conn.execute(
                sa.text("UPDATE organizations SET lock_pin = :enc, credentials_version = 1 WHERE id = :id"),
                {"enc": encrypted, "id": row[0]},
            )

    # Migrate screen maintenance_pin
    screen_rows = conn.execute(sa.text("SELECT id, maintenance_pin FROM screens WHERE maintenance_pin IS NOT NULL")).fetchall()
    for row in screen_rows:
        pin_val = str(row[1]).strip() if row[1] is not None else ""
        if pin_val and re.match(r"^\d{4}$", pin_val):
            encrypted = cipher.encrypt(pin_val.encode("utf-8")).decode("utf-8")
            assert cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8") == pin_val, "Verification failed for screen maintenance_pin"
            conn.execute(
                sa.text("UPDATE screens SET maintenance_pin = :enc WHERE id = :id"),
                {"enc": encrypted, "id": row[0]},
            )

    # Migrate system_settings universal_maintenance_pin if set
    sys_row = conn.execute(sa.text("SELECT id, value FROM system_settings WHERE key = 'universal_maintenance_pin'")).fetchone()
    if sys_row and sys_row[1]:
        pin_val = str(sys_row[1]).strip()
        if re.match(r"^\d{4}$", pin_val):
            encrypted = cipher.encrypt(pin_val.encode("utf-8")).decode("utf-8")
            assert cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8") == pin_val, "Verification failed for universal_maintenance_pin"
            conn.execute(
                sa.text("UPDATE system_settings SET value = :enc WHERE id = :id"),
                {"enc": encrypted, "id": sys_row[0]},
            )


def downgrade() -> None:
    # Safe Rollback: decrypt tokens back to plaintext before reverting column constraints
    pin_key = os.getenv("PIN_ENCRYPTION_KEY")
    if pin_key and pin_key.strip():
        cipher = Fernet(pin_key.strip().encode("utf-8"))
        conn = op.get_bind()

        org_rows = conn.execute(sa.text("SELECT id, lock_pin FROM organizations WHERE lock_pin IS NOT NULL")).fetchall()
        for row in org_rows:
            enc_val = str(row[1]).strip() if row[1] is not None else ""
            if enc_val.startswith("gAAAAA"):
                try:
                    decrypted = cipher.decrypt(enc_val.encode("utf-8")).decode("utf-8")
                    conn.execute(sa.text("UPDATE organizations SET lock_pin = :dec WHERE id = :id"), {"dec": decrypted[:4], "id": row[0]})
                except Exception:
                    conn.execute(sa.text("UPDATE organizations SET lock_pin = NULL WHERE id = :id"), {"id": row[0]})

        screen_rows = conn.execute(sa.text("SELECT id, maintenance_pin FROM screens WHERE maintenance_pin IS NOT NULL")).fetchall()
        for row in screen_rows:
            enc_val = str(row[1]).strip() if row[1] is not None else ""
            if enc_val.startswith("gAAAAA"):
                try:
                    decrypted = cipher.decrypt(enc_val.encode("utf-8")).decode("utf-8")
                    conn.execute(sa.text("UPDATE screens SET maintenance_pin = :dec WHERE id = :id"), {"dec": decrypted[:4], "id": row[0]})
                except Exception:
                    pass

        sys_row = conn.execute(sa.text("SELECT id, value FROM system_settings WHERE key = 'universal_maintenance_pin'")).fetchone()
        if sys_row and sys_row[1] and str(sys_row[1]).startswith("gAAAAA"):
            try:
                decrypted = cipher.decrypt(str(sys_row[1]).encode("utf-8")).decode("utf-8")
                conn.execute(sa.text("UPDATE system_settings SET value = :dec WHERE id = :id"), {"dec": decrypted[:4], "id": sys_row[0]})
            except Exception:
                pass

    op.drop_table("device_challenges")
    op.drop_table("device_command_logs")
    op.drop_table("outbox_events")

    with op.batch_alter_table("screens") as batch_op:
        batch_op.drop_column("synced_credentials_version")

    with op.batch_alter_table("organizations") as batch_op:
        batch_op.drop_column("credentials_version")
        batch_op.alter_column(
            "lock_pin",
            existing_type=sa.String(length=255),
            type_=sa.String(length=4),
            existing_nullable=True,
        )
