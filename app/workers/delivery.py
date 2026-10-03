from __future__ import annotations

import asyncio
import os
import socket
import time
from datetime import datetime, timedelta, timezone

import structlog

from app import metrics
from app.core.sanitize import email_domain, sanitize_error
from app.events.crypto import MailCryptoError, MailEventCipher
from app.persistence.models import MailLog, MailMessage
from app.persistence.repository import (
    FAILED,
    PROCESSING,
    PROVIDER_NAME,
    RETRY,
    SENT,
    as_utc,
    mail_repository,
    next_delay_seconds,
)
from app.provider.dto import EmailMessage
from app.provider.errors import PermanentProviderError, ProviderError, RetryableProviderError
from app.provider.protocol import MailProvider
from app.templates.registry import TemplateContractError, get_template

logger = structlog.get_logger()

# Provider delivery is not exactly-once. A timeout or crash after the provider
# accepts the message, but before this process stores the result, can send the
# same mail again when the processing lease expires. mail_message ingestion is
# idempotent; the external send is at-least-once in that failure window.


class MailWorker:
    def __init__(
        self,
        session_factory,
        provider: MailProvider,
        cipher: MailEventCipher,
        *,
        worker_id: str | None = None,
        batch_size: int = 10,
        lease_seconds: int = 60,
        max_attempts: int = 5,
        delays: tuple[int, ...] = (60, 300, 1800, 7200),
        send_timeout: float = 8.0,
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.cipher = cipher
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.delays = delays
        self.send_timeout = send_timeout
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> int:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            rows = await mail_repository.claim(
                session,
                now=now,
                batch_size=self.batch_size,
                lease_seconds=self.lease_seconds,
                worker_id=self.worker_id,
            )
            claimed = [(row.id, row.attempt_count) for row in rows]
            await session.commit()
        for message_id, attempt in claimed:
            await self._deliver(message_id, attempt)
        await self._refresh_gauges()
        return len(claimed)

    async def _deliver(self, message_id: int, attempt: int) -> None:
        prepared = await self._prepare(message_id, attempt)
        if prepared is None:
            return
        message, context = prepared
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(self.provider.send(message), timeout=self.send_timeout)
        except TimeoutError as exc:
            await self._fail(
                message_id,
                attempt,
                context,
                RetryableProviderError("TIMEOUT", "provider call timed out"),
                duration_ms=_duration_ms(started),
                cause=exc,
            )
            return
        except ProviderError as exc:
            await self._fail(message_id, attempt, context, exc, duration_ms=_duration_ms(started))
            return
        except Exception as exc:
            from app.provider.classify import classify_provider_error

            await self._fail(
                message_id,
                attempt,
                context,
                classify_provider_error(exc),
                duration_ms=_duration_ms(started),
                cause=exc,
            )
            return
        await self._succeed(
            message_id,
            attempt,
            context,
            result.message_id,
            duration_ms=_duration_ms(started),
        )

    async def _prepare(self, message_id: int, attempt: int):
        async with self.session_factory() as session:
            row = await session.get(MailMessage, message_id)
            if row is None or not _owns(row, self.worker_id, attempt):
                return None
            if row.status != PROCESSING:
                return None
            try:
                message = _render(row, self.cipher)
            except (MailCryptoError, TemplateContractError, ValueError, KeyError) as exc:
                await _mark_terminal(
                    session,
                    row,
                    status=FAILED,
                    code="INVALID_PAYLOAD",
                    message=sanitize_error(type(exc).__name__),
                    attempt=attempt,
                    events=("SEND_FAILED", "MAIL_FAILED"),
                )
                await session.commit()
                metrics.inc(metrics.FAILED)
                logger.error(
                    "mail_render_failed",
                    event_id=row.event_id,
                    template_code=row.template_code,
                    mail_message_id=row.id,
                    attempt_number=attempt,
                    status=FAILED,
                    error_code="INVALID_PAYLOAD",
                )
                return None
            domain = email_domain(message.to)
            now = datetime.now(timezone.utc)
            session.add(
                MailLog(
                    mail_message_id=row.id,
                    event_type="SEND_ATTEMPT",
                    attempt_number=attempt,
                    provider=PROVIDER_NAME,
                    created_at=now,
                )
            )
            await session.commit()
            context = {
                "event_id": row.event_id,
                "template_code": row.template_code,
                "request_id": row.request_id,
                "correlation_id": row.correlation_id,
                "email_domain": domain,
            }
            logger.info(
                "mail_send_started",
                event_id=row.event_id,
                template_code=row.template_code,
                mail_message_id=row.id,
                attempt_number=attempt,
                provider=PROVIDER_NAME,
                request_id=row.request_id,
                correlation_id=row.correlation_id,
                status=PROCESSING,
                email_domain=domain,
            )
            return message, context

    async def _succeed(
        self,
        message_id: int,
        attempt: int,
        context: dict,
        provider_message_id: str | None,
        *,
        duration_ms: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            row = await session.get(MailMessage, message_id)
            if row is None or not _owns(row, self.worker_id, attempt):
                logger.warning("mail_send_result_orphaned", event_id=context["event_id"], attempt_number=attempt)
                return
            row.status = SENT
            row.provider = PROVIDER_NAME
            row.provider_message_id = provider_message_id
            row.sent_at = now
            row.last_error_code = None
            row.last_error_message = None
            row.lease_until = None
            row.processing_started_at = None
            row.worker_id = None
            row.next_attempt_at = None
            row.encrypted_payload = None
            row.encryption_nonce = None
            row.updated_at = now
            session.add(
                MailLog(
                    mail_message_id=row.id,
                    event_type="SEND_SUCCESS",
                    attempt_number=attempt,
                    provider=PROVIDER_NAME,
                    provider_message_id=provider_message_id,
                    duration_ms=duration_ms,
                    created_at=now,
                )
            )
            await session.commit()
        metrics.inc(metrics.SENT)
        metrics.set_gauge(metrics.LATENCY, duration_ms)
        logger.info(
            "mail_send_succeeded",
            event_id=context["event_id"],
            template_code=context["template_code"],
            mail_message_id=message_id,
            attempt_number=attempt,
            provider=PROVIDER_NAME,
            provider_message_id=provider_message_id,
            request_id=context["request_id"],
            correlation_id=context["correlation_id"],
            status=SENT,
            duration_ms=duration_ms,
            email_domain=context["email_domain"],
        )

    async def _fail(
        self,
        message_id: int,
        attempt: int,
        context: dict,
        error: ProviderError,
        *,
        duration_ms: int,
        cause: BaseException | None = None,
    ) -> None:
        permanent = isinstance(error, PermanentProviderError)
        delay = None if permanent else next_delay_seconds(attempt, self.delays, self.max_attempts)
        final = permanent or delay is None
        now = datetime.now(timezone.utc)
        code = (error.code or "PROVIDER_ERROR")[:100]
        message = sanitize_error(error.message)
        async with self.session_factory() as session:
            row = await session.get(MailMessage, message_id)
            if row is None or not _owns(row, self.worker_id, attempt):
                logger.warning("mail_send_result_orphaned", event_id=context["event_id"], attempt_number=attempt)
                return
            row.last_error_code = code
            row.last_error_message = message
            row.lease_until = None
            row.processing_started_at = None
            row.worker_id = None
            row.updated_at = now
            row.provider = PROVIDER_NAME
            events = ["SEND_FAILED"]
            if final:
                row.status = FAILED
                row.next_attempt_at = None
                events.append("MAIL_FAILED")
            else:
                row.status = RETRY
                row.next_attempt_at = now + timedelta(seconds=delay or 0)
                events.append("RETRY_SCHEDULED")
            for event_type in events:
                session.add(
                    MailLog(
                        mail_message_id=row.id,
                        event_type=event_type,
                        attempt_number=attempt,
                        provider=PROVIDER_NAME,
                        error_code=code,
                        error_message=message,
                        duration_ms=duration_ms,
                        created_at=now,
                        metadata_json=None if event_type == "SEND_FAILED" else {"delay_seconds": delay},
                    )
                )
            await session.commit()
            status = row.status
        if final:
            metrics.inc(metrics.FAILED)
        else:
            metrics.inc(metrics.RETRIES)
        logger.warning(
            "mail_send_failed",
            event_id=context["event_id"],
            template_code=context["template_code"],
            mail_message_id=message_id,
            attempt_number=attempt,
            provider=PROVIDER_NAME,
            request_id=context["request_id"],
            correlation_id=context["correlation_id"],
            status=status,
            duration_ms=duration_ms,
            error_code=code,
            error_message=message,
            email_domain=context["email_domain"],
            error_type=type(cause).__name__ if cause else type(error).__name__,
        )

    async def _refresh_gauges(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            pending, oldest = await mail_repository.queue_stats(session)
        metrics.set_gauge(metrics.PENDING, pending)
        if oldest is None:
            metrics.set_gauge(metrics.OLDEST_AGE, 0.0)
        else:
            age = (now - as_utc(oldest)).total_seconds()
            metrics.set_gauge(metrics.OLDEST_AGE, max(age, 0.0))


def _owns(row: MailMessage, worker_id: str, attempt: int) -> bool:
    return row.worker_id == worker_id and int(row.attempt_count or 0) == attempt


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _render(row: MailMessage, cipher: MailEventCipher) -> EmailMessage:
    if not row.encrypted_payload or not row.encryption_nonce:
        raise MailCryptoError("encrypted payload is missing")
    envelope_nonce = row.encryption_nonce
    payload = cipher.decrypt(
        event_id=row.event_id,
        nonce=envelope_nonce,
        ciphertext=row.encrypted_payload,
        key_version=row.encryption_key_version,
    )
    recipient = payload.get("recipient") if isinstance(payload, dict) else None
    variables = payload.get("variables") if isinstance(payload, dict) else None
    if not isinstance(recipient, dict) or not isinstance(variables, dict):
        raise ValueError("mail payload is incomplete")
    email = recipient.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise ValueError("mail recipient is invalid")
    spec = get_template(row.template_code, row.template_version)
    subject, text, html = spec.render(variables)
    return EmailMessage(to=email, subject=subject, text=text, html=html)


async def _mark_terminal(
    session,
    row: MailMessage,
    *,
    status: str,
    code: str,
    message: str,
    attempt: int,
    events: tuple[str, ...],
) -> None:
    now = datetime.now(timezone.utc)
    row.status = status
    row.last_error_code = code
    row.last_error_message = message
    row.next_attempt_at = None
    row.lease_until = None
    row.processing_started_at = None
    row.worker_id = None
    row.updated_at = now
    for event_type in events:
        session.add(
            MailLog(
                mail_message_id=row.id,
                event_type=event_type,
                attempt_number=attempt,
                error_code=code,
                error_message=message,
                created_at=now,
            )
        )
