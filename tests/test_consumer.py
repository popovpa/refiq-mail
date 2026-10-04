import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app import metrics
from app.consumer.ingest import IncomingRecord, dlq_body, handle_kafka_record
from app.events.crypto import MailEventCipher
from app.kafka import consumer_kwargs
from app.persistence.models import MailLog, MailMessage
from tests.conftest import TestingSessionLocal

KEY = b"0123456789abcdef0123456789abcdef"
EMAIL = "secret.user@hidden.test"
CONFIRM_URL = "https://app.example/confirm-account?token=plaintext-token"


class RecordingOffsets:
    def __init__(self):
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class RecordingDlq:
    def __init__(self):
        self.messages: list[tuple[bytes | None, bytes]] = []

    async def publish(self, *, key: bytes | None, value: bytes) -> None:
        self.messages.append((key, value))


def cipher() -> MailEventCipher:
    return MailEventCipher(KEY, "v1")


def envelope(
    *,
    event_id: str = "evt-1",
    idempotency_key: str = "account-confirmation:1",
    template_code: str = "ACCOUNT_CONFIRMATION",
    template_version: int = 1,
    variables: dict | None = None,
    schema_version: int = 1,
    ciphertext: str | None = None,
    nonce: str | None = None,
) -> bytes:
    payload = {
        "recipient": {"email": EMAIL},
        "variables": variables
        if variables is not None
        else {"confirmUrl": CONFIRM_URL, "ttlHours": 24},
    }
    blob = cipher().encrypt(payload, event_id=event_id)
    body = {
        "schemaVersion": schema_version,
        "eventId": event_id,
        "eventType": "MAIL_SEND_REQUESTED",
        "occurredAt": "2026-10-04T00:00:00.000Z",
        "templateCode": template_code,
        "templateVersion": template_version,
        "idempotencyKey": idempotency_key,
        "source": {"type": "USER", "id": "42"},
        "requestId": "req-1",
        "correlationId": "corr-1",
        "encryption": {
            "algorithm": "AES-256-GCM",
            "keyVersion": "v1",
            "nonce": nonce or blob.nonce,
        },
        "encryptedPayload": ciphertext if ciphertext is not None else blob.ciphertext,
    }
    return json.dumps(body).encode()


def record(raw: bytes, *, offset: int = 1) -> IncomingRecord:
    return IncomingRecord(topic="mail-events", partition=0, offset=offset, key=b"42", value=raw)


async def _messages() -> list[MailMessage]:
    async with TestingSessionLocal() as session:
        return list((await session.execute(select(MailMessage).order_by(MailMessage.id))).scalars().all())


async def _logs(message_id: int) -> list[str]:
    async with TestingSessionLocal() as session:
        rows = (
            await session.execute(
                select(MailLog.event_type)
                .where(MailLog.mail_message_id == message_id)
                .order_by(MailLog.id)
            )
        ).scalars().all()
        return list(rows)


@pytest.fixture(autouse=True)
def _metrics():
    metrics.reset()
    yield
    metrics.reset()


@pytest.mark.asyncio
async def test_valid_confirmation_and_reset_are_queued():
    from structlog.testing import capture_logs

    offsets = RecordingOffsets()
    dlq = RecordingDlq()
    with capture_logs() as captured:
        await handle_kafka_record(
            TestingSessionLocal, record(envelope()), cipher=cipher(), offsets=offsets, dlq=dlq
        )
    events = [item["event"] for item in captured]
    assert "mail_send_request_received" in events
    logged = json.dumps(captured, default=str)
    assert EMAIL not in logged
    assert "plaintext-token" not in logged
    assert CONFIRM_URL not in logged
    reset = envelope(
        event_id="evt-2",
        idempotency_key="password-reset:9",
        template_code="PASSWORD_RESET",
        variables={"resetUrl": "https://app.example/reset-password?token=reset-token", "ttlMinutes": 30},
    )
    await handle_kafka_record(
        TestingSessionLocal, record(reset, offset=2), cipher=cipher(), offsets=offsets, dlq=dlq
    )
    rows = await _messages()
    assert [row.template_code for row in rows] == ["ACCOUNT_CONFIRMATION", "PASSWORD_RESET"]
    assert [row.status for row in rows] == ["QUEUED", "QUEUED"]
    assert rows[0].event_id == "evt-1"
    assert rows[0].encrypted_payload
    assert EMAIL not in rows[0].encrypted_payload
    assert CONFIRM_URL not in (rows[0].encrypted_payload or "")
    assert await _logs(rows[0].id) == ["MAIL_RECEIVED"]
    assert offsets.commits == 2
    assert dlq.messages == []
    assert metrics.snapshot()["mail.received"] == 2


@pytest.mark.asyncio
async def test_duplicate_event_id_and_idempotency_key_do_not_enqueue_another_send():
    offsets = RecordingOffsets()
    dlq = RecordingDlq()
    raw = envelope()
    await handle_kafka_record(TestingSessionLocal, record(raw), cipher=cipher(), offsets=offsets, dlq=dlq)
    await handle_kafka_record(
        TestingSessionLocal, record(raw, offset=2), cipher=cipher(), offsets=offsets, dlq=dlq
    )
    other_event = envelope(event_id="evt-other", idempotency_key="account-confirmation:1")
    await handle_kafka_record(
        TestingSessionLocal, record(other_event, offset=3), cipher=cipher(), offsets=offsets, dlq=dlq
    )
    rows = await _messages()
    assert len(rows) == 1
    assert rows[0].status == "QUEUED"
    assert await _logs(rows[0].id) == ["MAIL_RECEIVED", "MAIL_DUPLICATE", "MAIL_DUPLICATE"]
    assert offsets.commits == 3
    assert metrics.snapshot()["mail.duplicates"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_factory",
    [
        lambda: b"{",
        lambda: envelope(schema_version=99),
        lambda: envelope(template_code="UNKNOWN"),
        lambda: envelope(ciphertext=base64_garbage()),
        lambda: envelope(variables={"ttlHours": 24}),
    ],
)
async def test_permanent_invalid_events_go_to_dlq_without_plaintext(raw_factory):
    offsets = RecordingOffsets()
    dlq = RecordingDlq()
    raw = raw_factory()
    outcome = await handle_kafka_record(
        TestingSessionLocal, record(raw), cipher=cipher(), offsets=offsets, dlq=dlq
    )
    assert outcome == "dlq"
    assert offsets.commits == 1
    assert len(dlq.messages) == 1
    body = dlq.messages[0][1].decode()
    assert EMAIL not in body
    assert "plaintext-token" not in body
    assert CONFIRM_URL not in body
    assert await _messages() == []
    assert metrics.snapshot()["mail.dlq"] == 1


def base64_garbage() -> str:
    return "AAAA"


@pytest.mark.asyncio
async def test_db_failure_does_not_commit_offset_and_success_commits_after_db():
    order: list[str] = []

    class TrackingSession:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            self.session = await self._inner.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return await self._inner.__aexit__(exc_type, exc, tb)

        def __getattr__(self, name):
            return getattr(self.session, name)

        async def commit(self):
            order.append("db")
            raise OperationalError("insert", {}, Exception("db down"))

    def factory():
        return TrackingSession(TestingSessionLocal())

    offsets = RecordingOffsets()
    outcome = await handle_kafka_record(
        factory, record(envelope()), cipher=cipher(), offsets=offsets, dlq=RecordingDlq()
    )
    assert outcome == "retry"
    assert offsets.commits == 0
    assert order == ["db"]

    class OrderedSession(TrackingSession):
        async def commit(self):
            order.append("db")
            await self.session.commit()

    def ok_factory():
        return OrderedSession(TestingSessionLocal())

    class OrderedOffsets(RecordingOffsets):
        async def commit(self) -> None:
            order.append("kafka")
            await super().commit()

    offsets = OrderedOffsets()
    outcome = await handle_kafka_record(
        ok_factory,
        record(envelope(event_id="evt-ok", idempotency_key="account-confirmation:2")),
        cipher=cipher(),
        offsets=offsets,
        dlq=RecordingDlq(),
    )
    assert outcome == "created"
    assert order == ["db", "db", "kafka"]
    assert offsets.commits == 1


def test_consumer_reads_only_mail_events_with_manual_commits():
    config = consumer_kwargs()
    assert config["topics"] == ("mail-events",)
    assert config["group_id"] == "mail-service"
    assert config["enable_auto_commit"] is False
    assert "audit-events" not in config["topics"]
    assert "clickstream-events" not in config["topics"]
    assert not config["group_id"].startswith("data-ingest")


def test_dlq_wrapper_keeps_original_ciphertext():
    raw = envelope()
    body = json.loads(dlq_body(record(raw), code="DECRYPTION_FAILED", message="mail payload could not be decrypted"))
    assert body["sourceTopic"] == "mail-events"
    assert body["errorCode"] == "DECRYPTION_FAILED"
    assert body["record"]["encryptedPayload"]
    assert EMAIL not in json.dumps(body)
    assert "occurredAt" in body["failedAt"] or body["failedAt"].endswith("Z")


@pytest.mark.asyncio
async def test_queued_row_count_matches_one_message():
    await handle_kafka_record(
        TestingSessionLocal, record(envelope()), cipher=cipher(), offsets=RecordingOffsets(), dlq=RecordingDlq()
    )
    async with TestingSessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(MailMessage))
    assert count == 1
    assert datetime.now(timezone.utc)
