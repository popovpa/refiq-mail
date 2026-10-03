from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base

EntityId = BigInteger().with_variant(Integer, "sqlite")
JsonValue = JSON().with_variant(JSONB(), "postgresql")


class MailMessage(Base):
    __tablename__ = "mail_message"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_mail_message_event_id"),
        UniqueConstraint("idempotency_key", name="uq_mail_message_idempotency_key"),
        Index("ix_mail_message_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_mail_message_lease_until", "lease_until"),
        Index("ix_mail_message_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(EntityId, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    template_code: Mapped[str] = mapped_column(String(80), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(50))
    source_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    encryption_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    encryption_nonce: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailLog(Base):
    __tablename__ = "mail_log"
    __table_args__ = (
        Index("ix_mail_log_message_created", "mail_message_id", "created_at"),
        Index("ix_mail_log_event_type", "event_type"),
        Index("ix_mail_log_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(EntityId, primary_key=True, autoincrement=True)
    mail_message_id: Mapped[int] = mapped_column(
        EntityId, ForeignKey("mail_message.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JsonValue)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
