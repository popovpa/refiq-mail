import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app import metrics
from app.events.crypto import MailEventCipher
from app.persistence.models import MailLog, MailMessage
from app.persistence.repository import claim_statement, mail_repository, next_delay_seconds
from app.provider.dto import ProviderSendResult
from app.provider.errors import PermanentProviderError, RetryableProviderError
from app.workers.delivery import MailWorker
from tests.conftest import TestingSessionLocal

KEY = b"0123456789abcdef0123456789abcdef"
EMAIL = "secret.user@hidden.test"
CONFIRM_URL = "https://app.example/confirm-account?token=plaintext-token"


class FakeProvider:
    def __init__(self, *, result: ProviderSendResult | None = None, error: Exception | None = None):
        self.calls = []
        self.result = result or ProviderSendResult(message_id="postbox-message-1")
        self.error = error

    async def send(self, message):
        self.calls.append(message)
        if self.error:
            raise self.error
        return self.result


def _cipher() -> MailEventCipher:
    return MailEventCipher(KEY, "v1")


async def _queue(
    *,
    event_id: str = "evt-1",
    idempotency_key: str = "account-confirmation:1",
    status: str = "QUEUED",
    attempt_count: int = 0,
    next_attempt_at: datetime | None = None,
    lease_until: datetime | None = None,
    worker_id: str | None = None,
) -> int:
    blob = _cipher().encrypt(
        {"recipient": {"email": EMAIL}, "variables": {"confirmUrl": CONFIRM_URL, "ttlHours": 24}},
        event_id=event_id,
    )
    now = datetime.now(timezone.utc)
    async with TestingSessionLocal() as session:
        row = MailMessage(
            event_id=event_id,
            idempotency_key=idempotency_key,
            template_code="ACCOUNT_CONFIRMATION",
            template_version=1,
            source_type="USER",
            source_id="42",
            request_id="req-1",
            correlation_id="corr-1",
            encrypted_payload=blob.ciphertext,
            encryption_algorithm=blob.algorithm,
            encryption_key_version=blob.key_version,
            encryption_nonce=blob.nonce,
            status=status,
            attempt_count=attempt_count,
            next_attempt_at=next_attempt_at,
            lease_until=lease_until,
            worker_id=worker_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _row(message_id: int) -> MailMessage:
    async with TestingSessionLocal() as session:
        return (await session.get(MailMessage, message_id))


async def _log_types(message_id: int) -> list[str]:
    async with TestingSessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(MailLog.event_type).where(MailLog.mail_message_id == message_id).order_by(MailLog.id)
                )
            ).scalars().all()
        )


def _worker(provider: FakeProvider, worker_id: str = "worker-a") -> MailWorker:
    return MailWorker(
        TestingSessionLocal,
        provider,
        _cipher(),
        worker_id=worker_id,
        lease_seconds=60,
        max_attempts=5,
        delays=(60, 300, 1800, 7200),
        send_timeout=2,
    )


@pytest.fixture(autouse=True)
def _metrics():
    metrics.reset()
    yield
    metrics.reset()


def test_retry_schedule_defaults():
    delays = (60, 300, 1800, 7200)
    assert next_delay_seconds(1, delays, 5) == 60
    assert next_delay_seconds(2, delays, 5) == 300
    assert next_delay_seconds(3, delays, 5) == 1800
    assert next_delay_seconds(4, delays, 5) == 7200
    assert next_delay_seconds(5, delays, 5) is None


def test_claim_uses_skip_locked():
    sql = str(claim_statement(datetime.now(timezone.utc), 10, lock=True).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
    assert "mail_message" in sql


@pytest.mark.asyncio
async def test_queued_message_is_sent_and_sensitive_payload_is_removed():
    from structlog.testing import capture_logs

    message_id = await _queue()
    provider = FakeProvider()
    with capture_logs() as captured:
        await _worker(provider).run_once()
    row = await _row(message_id)
    assert row.status == "SENT"
    assert row.provider == "YANDEX_POSTBOX"
    assert row.provider_message_id == "postbox-message-1"
    assert row.sent_at is not None
    assert row.attempt_count == 1
    assert row.encrypted_payload is None
    assert row.encryption_nonce is None
    assert row.lease_until is None
    assert await _log_types(message_id) == ["MAIL_PROCESSING_STARTED", "SEND_ATTEMPT", "SEND_SUCCESS"]
    assert provider.calls[0].to == EMAIL
    assert CONFIRM_URL in provider.calls[0].html
    blob = json.dumps(captured, default=str)
    assert EMAIL not in blob
    assert "plaintext-token" not in blob
    assert CONFIRM_URL not in blob
    assert "hidden.test" in blob
    async with TestingSessionLocal() as session:
        logs = list((await session.execute(select(MailLog))).scalars().all())
    stored = json.dumps(
        [
            {
                "event_type": item.event_type,
                "error_message": item.error_message,
                "metadata": item.metadata_json,
                "provider_message_id": item.provider_message_id,
            }
            for item in logs
        ]
    )
    assert EMAIL not in stored
    assert CONFIRM_URL not in stored
    assert "plaintext-token" not in stored


@pytest.mark.asyncio
async def test_retryable_failure_schedules_backoff():
    message_id = await _queue()
    error = RetryableProviderError(
        "Throttling",
        f"slow down for {EMAIL} at {CONFIRM_URL}",
    )
    await _worker(FakeProvider(error=error)).run_once()
    row = await _row(message_id)
    assert row.status == "RETRY"
    assert row.attempt_count == 1
    assert row.last_error_code == "Throttling"
    assert row.next_attempt_at is not None
    delta = row.next_attempt_at
    if delta.tzinfo is None:
        delta = delta.replace(tzinfo=timezone.utc)
    remaining = delta - datetime.now(timezone.utc)
    assert timedelta(seconds=50) <= remaining <= timedelta(seconds=70)
    assert EMAIL not in (row.last_error_message or "")
    assert "plaintext-token" not in (row.last_error_message or "")
    assert "[redacted-email]" in (row.last_error_message or "")
    assert "[redacted-url]" in (row.last_error_message or "")
    assert await _log_types(message_id) == [
        "MAIL_PROCESSING_STARTED",
        "SEND_ATTEMPT",
        "SEND_FAILED",
        "RETRY_SCHEDULED",
    ]
    assert metrics.snapshot()["mail.retries"] == 1


@pytest.mark.asyncio
async def test_permanent_failure_does_not_retry():
    message_id = await _queue()
    await _worker(FakeProvider(error=PermanentProviderError("MessageRejected", "rejected"))).run_once()
    row = await _row(message_id)
    assert row.status == "FAILED"
    assert row.attempt_count == 1
    assert row.next_attempt_at is None
    assert row.lease_until is None
    assert await _log_types(message_id) == [
        "MAIL_PROCESSING_STARTED",
        "SEND_ATTEMPT",
        "SEND_FAILED",
        "MAIL_FAILED",
    ]
    assert metrics.snapshot()["mail.failed"] == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_fails_the_message():
    message_id = await _queue(
        status="RETRY",
        attempt_count=4,
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    await _worker(FakeProvider(error=RetryableProviderError("TIMEOUT", "timed out"))).run_once()
    row = await _row(message_id)
    assert row.status == "FAILED"
    assert row.attempt_count == 5
    assert row.next_attempt_at is None
    assert "MAIL_FAILED" in await _log_types(message_id)


@pytest.mark.asyncio
async def test_expired_processing_lease_can_be_reclaimed():
    message_id = await _queue(
        status="PROCESSING",
        attempt_count=1,
        lease_until=datetime.now(timezone.utc) - timedelta(seconds=5),
        worker_id="stuck-worker",
    )
    now = datetime.now(timezone.utc)
    async with TestingSessionLocal() as session:
        claimed = await mail_repository.claim(
            session,
            now=now,
            batch_size=10,
            lease_seconds=60,
            worker_id="worker-b",
        )
        await session.commit()
        assert [row.id for row in claimed] == [message_id]
        assert claimed[0].attempt_count == 2
        assert claimed[0].worker_id == "worker-b"
        assert claimed[0].status == "PROCESSING"


@pytest.mark.asyncio
async def test_second_worker_does_not_claim_a_leased_job():
    message_id = await _queue()
    now = datetime.now(timezone.utc)
    async with TestingSessionLocal() as first:
        claimed = await mail_repository.claim(
            first,
            now=now,
            batch_size=10,
            lease_seconds=60,
            worker_id="worker-a",
        )
        await first.commit()
    async with TestingSessionLocal() as second:
        again = await mail_repository.claim(
            second,
            now=now,
            batch_size=10,
            lease_seconds=60,
            worker_id="worker-b",
        )
        await second.commit()
    assert [row.id for row in claimed] == [message_id]
    assert again == []
