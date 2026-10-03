"""Mail service tables. Separate Alembic history from the API.

Revision ID: 001
Revises:
Create Date: 2026-10-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mail_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("template_code", sa.String(length=80), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
        sa.Column("encryption_algorithm", sa.String(length=32), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=32), nullable=False),
        sa.Column("encryption_nonce", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_mail_message_event_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_mail_message_idempotency_key"),
    )
    op.create_index(
        "ix_mail_message_status_next_attempt",
        "mail_message",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_mail_message_lease_until", "mail_message", ["lease_until"])
    op.create_index("ix_mail_message_created_at", "mail_message", ["created_at"])
    op.create_table(
        "mail_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mail_message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mail_message_id"], ["mail_message.id"], name="fk_mail_log_mail_message_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mail_log_message_created", "mail_log", ["mail_message_id", "created_at"])
    op.create_index("ix_mail_log_event_type", "mail_log", ["event_type"])
    op.create_index("ix_mail_log_created_at", "mail_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_log_created_at", table_name="mail_log")
    op.drop_index("ix_mail_log_event_type", table_name="mail_log")
    op.drop_index("ix_mail_log_message_created", table_name="mail_log")
    op.drop_table("mail_log")
    op.drop_index("ix_mail_message_created_at", table_name="mail_message")
    op.drop_index("ix_mail_message_lease_until", table_name="mail_message")
    op.drop_index("ix_mail_message_status_next_attempt", table_name="mail_message")
    op.drop_table("mail_message")
