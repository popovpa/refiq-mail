from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.events.contract import (
    MailEnvelope,
    PermanentIngestError,
    TemporaryIngestError,
    decrypt_variables,
    parse_envelope,
    utc_timestamp,
)
from app.events.crypto import MailEventCipher
from app.persistence.models import MailLog, MailMessage
from app.persistence.repository import QUEUED, mail_repository

logger = structlog.get_logger()

# Transport into mail_message is idempotent. Provider delivery is not exactly-once:
# a timeout or crash around the external send can still deliver twice.


@dataclass(frozen=True)
class IncomingRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes


class OffsetCommitter:
    async def commit(self) -> None: ...


class DlqPublisher:
    async def publish(self, *, key: bytes | None, value: bytes) -> None: ...


def dlq_body(record: IncomingRecord, *, code: str, message: str) -> bytes:
    original = None
    record_base64 = None
    try:
        parsed = json.loads(record.value.decode("utf-8"))
        if isinstance(parsed, dict):
            original = parsed
        else:
            record_base64 = _b64(record.value)
    except Exception:
        record_base64 = _b64(record.value)
    body: dict = {
        "sourceTopic": record.topic,
        "partition": record.partition,
        "offset": record.offset,
        "errorCode": code,
        "error": message[:500],
        "failedAt": utc_timestamp(datetime.now(timezone.utc)),
    }
    if original is not None:
        body["record"] = original
    else:
        body["recordBase64"] = record_base64
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode("ascii")


async def ingest(session: AsyncSession, raw: bytes, cipher: MailEventCipher) -> str:
    envelope = parse_envelope(raw)
    decrypt_variables(envelope, cipher)
    try:
        async with session.begin_nested():
            row = _message_from_envelope(envelope)
            session.add(row)
            await session.flush()
            session.add(
                MailLog(
                    mail_message_id=row.id,
                    event_type="MAIL_RECEIVED",
                    created_at=row.created_at,
                    metadata_json={"template_code": envelope.templateCode},
                )
            )
            await session.flush()
    except IntegrityError:
        existing = await mail_repository.find_by_identity(
            session,
            event_id=envelope.eventId,
            idempotency_key=envelope.idempotencyKey,
        )
        if existing is None:
            raise TemporaryIngestError("mail identity conflict could not be resolved")
        session.add(
            MailLog(
                mail_message_id=existing.id,
                event_type="MAIL_DUPLICATE",
                created_at=datetime.now(timezone.utc),
                metadata_json={"event_id": envelope.eventId},
            )
        )
        metrics.inc(metrics.DUPLICATES)
        return "duplicate"
    metrics.inc(metrics.RECEIVED)
    return "created"


def _message_from_envelope(envelope: MailEnvelope) -> MailMessage:
    now = datetime.now(timezone.utc)
    return MailMessage(
        event_id=envelope.eventId,
        idempotency_key=envelope.idempotencyKey,
        template_code=envelope.templateCode,
        template_version=envelope.templateVersion,
        source_type=envelope.source.type,
        source_id=envelope.source.id,
        request_id=envelope.requestId,
        correlation_id=envelope.correlationId,
        encrypted_payload=envelope.encryptedPayload,
        encryption_algorithm=envelope.encryption.algorithm,
        encryption_key_version=envelope.encryption.keyVersion,
        encryption_nonce=envelope.encryption.nonce,
        status=QUEUED,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )


async def handle_kafka_record(
    session_factory,
    record: IncomingRecord,
    *,
    cipher: MailEventCipher,
    offsets: OffsetCommitter,
    dlq: DlqPublisher,
) -> str:
    """Commit the Kafka offset only after the database commit, or after a confirmed DLQ write."""
    async with session_factory() as session:
        try:
            outcome = await ingest(session, record.value, cipher)
            await session.commit()
        except PermanentIngestError as exc:
            await session.rollback()
            await dlq.publish(
                key=record.key,
                value=dlq_body(record, code=exc.code, message=exc.message),
            )
            metrics.inc(metrics.DLQ)
            await offsets.commit()
            return "dlq"
        except Exception as exc:
            await session.rollback()
            logger.warning("mail_ingest_temporary_failure", error_type=type(exc).__name__)
            return "retry"
    await offsets.commit()
    return outcome
