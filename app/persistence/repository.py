from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import MailLog, MailMessage

QUEUED = "QUEUED"
PROCESSING = "PROCESSING"
RETRY = "RETRY"
SENT = "SENT"
FAILED = "FAILED"

PROVIDER_NAME = "YANDEX_POSTBOX"


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def claim_statement(now: datetime, batch_size: int, *, lock: bool = True):
    due = or_(
        MailMessage.status == QUEUED,
        and_(MailMessage.status == RETRY, MailMessage.next_attempt_at <= now),
        and_(MailMessage.status == PROCESSING, MailMessage.lease_until < now),
    )
    stmt = (
        select(MailMessage)
        .where(due)
        .order_by(MailMessage.created_at.asc(), MailMessage.id.asc())
        .limit(batch_size)
    )
    if lock:
        stmt = stmt.with_for_update(skip_locked=True)
    return stmt


def next_delay_seconds(attempt_count: int, delays: tuple[int, ...], max_attempts: int) -> int | None:
    """Seconds until the next try. None means the attempt that just failed is final."""
    if attempt_count >= max_attempts:
        return None
    index = max(attempt_count - 1, 0)
    if index >= len(delays):
        return delays[-1]
    return delays[index]


class MailRepository:
    async def claim(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        batch_size: int,
        lease_seconds: int,
        worker_id: str,
    ) -> list[MailMessage]:
        dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
        stmt = claim_statement(now, batch_size, lock=dialect == "postgresql")
        rows = list((await session.execute(stmt)).scalars().all())
        lease_until = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = PROCESSING
            row.processing_started_at = now
            row.lease_until = lease_until
            row.worker_id = worker_id
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.updated_at = now
            session.add(
                MailLog(
                    mail_message_id=row.id,
                    event_type="MAIL_PROCESSING_STARTED",
                    attempt_number=row.attempt_count,
                    created_at=now,
                    metadata_json={"worker_id": worker_id},
                )
            )
        await session.flush()
        return rows

    async def find_by_identity(
        self,
        session: AsyncSession,
        *,
        event_id: str,
        idempotency_key: str,
    ) -> MailMessage | None:
        return (
            await session.execute(
                select(MailMessage).where(
                    or_(
                        MailMessage.event_id == event_id,
                        MailMessage.idempotency_key == idempotency_key,
                    )
                )
            )
        ).scalars().first()

    async def queue_stats(self, session: AsyncSession) -> tuple[int, datetime | None]:
        pending = (QUEUED, RETRY, PROCESSING)
        count = await session.scalar(
            select(func.count()).select_from(MailMessage).where(MailMessage.status.in_(pending))
        )
        oldest = await session.scalar(
            select(func.min(MailMessage.created_at)).where(MailMessage.status.in_(pending))
        )
        return int(count or 0), oldest


mail_repository = MailRepository()
